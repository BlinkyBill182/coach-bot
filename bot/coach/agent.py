"""Claude conversation loop for Telegram messages."""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import time
from typing import Any, Optional

import anthropic

from . import domain, movement, onboarding, planner
from .gcal import Calendar
from .store import Store
from . import eta, wolt
from .tools import TOOLS, ToolRunner, enabled_tools

log = logging.getLogger(__name__)
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5-5")
# Optional cheaper model for routine turns; it hands a turn to MODEL with ask_senior_coach. Off when empty.
FAST_MODEL = os.environ.get("COACH_FAST_MODEL", "")
MAX_STEPS = 12
# $ per million tokens: input, output, cache read. Cache writes: 1.25x input (5 min), 2x (1 hour).
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}
ESCALATE_TOOL = {
    "name": "ask_senior_coach",
    "description": "Hand this turn to the senior coach model. Use it for questions that need judgment rather than logging: "
                   "program or nutrition decisions, research questions, lasting changes (targets, week, exercise plan, meal template), "
                   "anything medical, or when you are unsure. Not for logging food, sets, weight, creatine or reminders.",
    "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
}


def usage_cost(model: str, u: Any) -> float:
    """Dollars for one response's usage (estimate from list prices)."""
    inp, out, read = PRICES.get(model, PRICES["claude-opus-5-5"])
    cc = getattr(u, "cache_creation", None)
    if cc is not None:
        w5 = getattr(cc, "ephemeral_5m_input_tokens", 0) or 0
        w1 = getattr(cc, "ephemeral_1h_input_tokens", 0) or 0
    else:  # no TTL breakdown: count every write at the 5-minute price
        w5, w1 = getattr(u, "cache_creation_input_tokens", 0) or 0, 0
    return ((u.input_tokens or 0) * inp + (getattr(u, "cache_read_input_tokens", 0) or 0) * read
            + w5 * inp * 1.25 + w1 * inp * 2 + (u.output_tokens or 0) * out) / 1e6

BOT_RULES = """
# Running as the user's Telegram bot
You are talking with the user in Telegram, on their phone. The section above is your coaching knowledge; where it mentions Claude Code or files, use the tools you have here instead. Your tools read and write the same log the tracker app shows.

- Reply in Hebrew unless the user writes English. Short lines, no markdown tables, no headers, no bold markers: Telegram shows plain text. A few short lines beat a long message.
- Voice: a direct coach. Confident, short, numbers first. Talk to them as someone who trains, not someone trying to. Build confidence from real results they can't argue with: a personal record against the previous best, a streak, kilos added since the start, the weekly trend. No empty hype or slogans, no exclamation-mark cheerleading, at most one emoji (💪 or 🏆) and only for a real win. A missed meal, workout or weigh-in gets one plain line and the next step, never a lecture or guilt.
- When log_exercise returns personal_record, open the reply with it: "🏆 שיא: <exercise> <w>×<r> (קודם <w>×<r>)". When the context shows a streak, mention it when it adds weight to the next step.
- Logging is the main job. When the user reports food, weight, creatine or sets, call the tools first, then answer with what you logged, what is left today (kcal and protein, carbs if relevant) and the concrete next step (the next meal from the plan, or the next set's load).
- Food photos: estimate the portion and macros, log them, and say the estimate in one line so the user can correct it. Ask for grams only when the estimate would be far off.
- Dates: "today" and times come from the context message. Use YYYY-MM-DD in tools. After midnight and before 04:00, food still belongs to the previous day unless the user says otherwise.
- Workouts: the plan may use supersets, and an exercise may repeat across them, so pass the superset letter. If they say they did the plan as written, use load_workout_plan with with_reps=true, then set_workout_done. Flag personal records.
- After logging food, the tool result may include plan_changes: the remaining meals were adjusted to land on today's target. Tell the user the changes in one or two lines. If the food replaced a planned meal (they ate lunch out, or log a meal within about 90 minutes of a planned meal), call skip_planned_meal for that planned meal first, then add_meals.
- When the user says ahead of time that they will eat somewhere else (a friend's place, a restaurant), call replace_planned_meal with your estimate of that meal's macros and the time; never turn a meal they will eat into a note without macros, or the plan piles the missing calories onto other meals. Never tell the user to eat a planned meal after you told them the day is done.
- Memory is shared with Claude Code on the user's computer: it reads your saved facts and this chat, and its decisions reach you through the skill above or as saved facts. Save with remember every lasting decision: their routine, schedule, food likes or constraints, a label they send, how they report an exercise, their answer to a question you asked, a change to the plan they agree to. One fact per note, in Hebrew, with the numbers. Before saving, check the saved facts; if one is now wrong, forget it and save the new one. Don't save one-off events or what the skill already says.
- Decisions for today live in the log, not only in the chat: the chat you see is only the last messages. The moment you and the user agree on a change for today (a shorter workout, skipping an exercise, a meal swap or a later meal), apply it with the tools in the same reply: remove_exercise for skipped exercises, skip_planned_meal or set_plan_time for meals, and add_note with one short line of what was decided and why. The context message shows today's notes; trust them over what you don't remember.
- Plans follow the calendar: before each reminder and each of your replies the plan is checked against today's calendar, so trust the context message. When the user says they stay home, or isn't really going to an event in the calendar, call set_at_home: the day's on-the-go meals go back to their home versions and the bot stops treating that day's events as outings.
- When the user says they won't train today (or didn't), call skip_workout with their reason; don't leave it only as a note.
- Lasting changes (the user's goals, food they can or can't eat, which days they train, the exercise plan, the meal template, the targets) are made here in Telegram with set_targets, set_week, update_exercise_plan and update_meal_template. Always two steps: call with confirm=false, show the user the preview in short lines (what changes, totals against the targets), and apply with confirm=true and the same values only after their clear yes in their next message. The tool saves the decision as a fact. A one-day change is not a lasting change: use set_day_program, set_plan_time or replace_planned_meal for that.
- When the user swaps or moves a workout to another day ("Wednesday is upper instead of rest", "Friday rest"), call set_day_program for each changed day: it sets the day type and program day, loads the exercises, creates or moves the gym event and rebuilds the meal plan. Then add their special meals with replace_planned_meal. On a day turned into rest, if a gym event is left, ask the user before delete_gym_event. The streak counts only workouts they did.
- Wolt: when the user asks what to order or where to eat nearby, call wolt_search (their location or the place they names, with a query word if they said what they feel like), open the menus of 2-3 promising places with wolt_menu, and suggest 2-3 dishes: restaurant, dish, price, delivery time, your estimate of kcal and protein, and how it fits what is left today. Respect the food the user avoids, from the questionnaire and the saved facts. Give the Wolt link. You cannot order: the user orders themselves. Say the macros are estimates.
- The questionnaire is the base: its answers come in the context message and as saved facts, and win over how the coaching knowledge describes the user. It's a page inside Telegram that /onboarding opens, not a conversation with you; closing it midway loses nothing, and nothing is saved as their base until they press save on its last screen. Its body photo goes straight from the page to one separate look and is not kept anywhere: if asked, say that only a short description in words stayed. The photo gives no body-fat number and doesn't enter the calorie formula.
- Movement: the context message says whether live location shows they moved today. It works only while they share their Telegram live location, it can't see walking inside a home or an office or a treadmill, a phone left at home looks like not moving, and a car ride looks like moving. It's a rough estimate of calories burned and says nothing about burning fat; never claim otherwise. When they say they stayed in today on a rest day, call still_day with confirm=false, offer the preview and the 30-minute walk, and apply with confirm=true only after their yes in a later message. The bot already asks at 17:00 when live location shows they stayed put; don't ask again.
- Never invent numbers you haven't logged or read. If a tool fails, say what failed in one line.
- Away from home: plan items with "בדרך" are meals the user eats out (a meeting, errands). When they say they are going out, or asks what to eat while out, suggest something easy to carry from what they have (protein powder in a shaker, a protein yogurt, a protein bar, dates, nuts, a banana, rice cakes with a peanut-butter sachet), or what to buy near the place: types of places and dishes (grilled chicken with rice or potatoes, salmon sushi; at a supermarket a ready chicken salad, rice cakes, a protein drink), always respecting the food the user avoids. Never name specific businesses or addresses you aren't sure exist. Size it to what is left today.
- Reminders: when the user asks to be reminded of something ("תזכיר לי ב-18:00 לקנות עוף"), call set_reminder with the Israel-time datetime, the text in their words, and repeat if they said every day or every workday (weekdays = Sunday to Thursday). Confirm in one line: the time and the text. list_reminders shows what is set; cancel_reminder removes one.
- Drive times come from Waze, from the user's current location (their Telegram live location, which you receive): answer "how long to X" with get_drive_time (minutes, km, arrival). The plan's leave times use Waze too and are re-checked an hour before leaving. If get_drive_time fails, say why in one line (no recent location: ask them to share live location; Waze failed) and don't guess. set_travel_time is only the fallback, for when Waze has no answer or an event has no address; it re-times today's and tomorrow's departures; tell them the new leave time it returns.
- Calendar: you may move the gym event. Never create or delete other calendar events.
- Exercise technique: the exercise reference above has cues per exercise. When the user is about to start an exercise or reports its first set, give at most one technique cue, the one that matters most for that exercise, in one short line. Not on every set.
- Food questions: the nutrition reference above holds the evidence. Use it for what, how much and when to eat, supplements and health questions. At most one nutrition tip per reply, only when it fits what the user just asked or logged. Never change the targets or the meal template (config/schedule) without the user's yes; when they decide, save it with remember. For blood tests, symptoms or medication, say it is not medical advice and point them to their doctor.
- Fibre: there is no fibre field in the log. When the user closes the day or asks how it went, estimate the day's fibre roughly from what they logged; if it is under 30 g, suggest one easy fix for tomorrow (an apple, oats, vegetables with dinner). One line, and say it's an estimate.
- When an exercise has stalled 3 sessions in a row (same load, no added reps), suggest one swap from the reference and say why in one line. Never change the plan (config/exercises) without the user's yes; when they decide, save it with remember.
"""


BUILD_PROMPT = """You help a fitness coach bot. The person sent one photo of their own body, on purpose, so the coach can see their build.
Describe the build in Hebrew, one or two short plain lines: overall frame, which areas carry more or less muscle, and how lean the midsection looks, in words.
No body-fat percentage, no weight estimate, no medical or health judgement, nothing about the face, tattoos, the room or anyone else in the photo.
If the photo doesn't clearly show a person's body, or shows someone under 18, answer with exactly the word NONE."""


REFERENCES = ("exercises.md", "nutrition.md")  # read from the SKILL.md folder when present


def bot_rules() -> str:
    """The rules, without those of add-ons that are off."""
    drop = []
    if not wolt.enabled():
        drop.append("- Wolt:")
    if not eta.enabled():
        drop.append("- Drive times come from Waze")
    return "\n".join(line for line in BOT_RULES.split("\n") if not any(line.startswith(d) for d in drop))


def load_system() -> str:
    path = os.environ.get("SKILL_PATH", os.path.join(os.path.dirname(__file__), "..", "..", ".claude", "skills", "muscle-coach", "SKILL.md"))
    try:
        with open(path, encoding="utf-8") as f:
            skill = f.read()
        if skill.startswith("---"):
            skill = skill.split("---", 2)[2]
    except OSError:
        log.warning("skill file not found at %s", path)
        skill = "You are the user's fitness coach."
    refs = ""
    for name in REFERENCES:
        try:
            with open(os.path.join(os.path.dirname(path), name), encoding="utf-8") as f:
                refs += "\n\n" + f.read().strip()
        except OSError:
            pass
    return skill.strip() + refs + "\n" + bot_rules()


def context_message(store: Store, now: Optional[dt.datetime] = None) -> str:
    now = now or domain.now()
    t = now.date()
    ds = t.isoformat()
    d = store.day(ds)
    cd = d["cycleDay"] or domain.planned_cycle(t)
    tot, rem = domain.totals(d), domain.remaining(d)
    plan = domain.day_plan(store.schedule(), d)
    cur_m = now.hour * 60 + now.minute
    upcoming = [f"{x['t']} {x['n']}" for x in plan if (domain.minutes(x["t"]) or 0) >= cur_m][:4]
    tom = t + dt.timedelta(days=1)
    tom_cd = domain.cycle_day(store.data(f"log/{tom.isoformat()}"), tom)
    notes = store.profile_notes()
    lines = [
        f"Now: {domain.day_label(t)} {t.year}, {now.strftime('%H:%M')} (Israel). Today = {ds}.",
        f"Today: cycle day {cd} ({domain.CYCLE[cd]}), {'training' if d['type'] == 'train' else 'rest'} day"
        + (", closed" if d["closed"] else "") + ".",
        "Targets (live, config/program): " + "; ".join(
            f"{typ} {g['k']:g} kcal P{g['p']:g} F{g['f']:g} C{g['c']:g}" for typ, g in domain.TARGETS.items())
        + ". Week: " + ", ".join(f"{k} {domain.CYCLE[domain.WEEKDAY_DAY[(i - 1) % 7]]}" for i, k in enumerate(domain.WEEK_KEYS)) + ".",
        f"Logged: {tot['k']:.0f} kcal, P{tot['p']:.0f} F{tot['f']:.0f} C{tot['c']:.0f}; left {rem['k']:.0f} kcal, "
        f"P{rem['p']:.0f}. Weight: {d['weight'] if d['weight'] is not None else 'not logged'}. "
        f"Creatine 5 g: {'taken' if domain.creatine_taken(d) else 'not yet'}. "
        f"Workout sets logged: {sum(len(domain.done_sets(e)) for e in d['workout']['exercises'])}.",
        "Next in plan: " + ("; ".join(upcoming) if upcoming else "nothing left today") + ".",
        "Today's notes (decisions for today): " + (" | ".join(n for n in (d["notes"], d["workout"].get("notes")) if n) or "none") + ".",
        "Streaks (days): closed {closed}, workouts without a miss {train}, protein target {protein}.".format(**domain.streaks(store.days(), t)),
        f"Tomorrow {tom.isoformat()}: cycle day {tom_cd} ({domain.CYCLE[tom_cd]}).",
        movement.context_line(store, now.timestamp()),
    ]
    if d.get("targetAdj"):
        lines.append(f"Today's target changed for one day with the user's yes: {d['targetAdj']['k']:g} kcal, {d['targetAdj']['c']:g} g carbs ({d['targetAdj']['why']}).")
    base = onboarding.base_line(store)
    if base:
        lines.append(base)
    aways = [x for x in plan if x.get("away")]
    if aways:
        lines.append("Away today: " + "; ".join(f"{x['t']} {x['n']} during {x['away']}" + (f" at {x['where']}" if x.get("where") else "") for x in aways) + ".")
    if notes:
        lines.append("Saved facts about the user: " + " | ".join(f"{i}) {n['text']}" for i, n in enumerate(notes)))
    return "\n".join(lines)


BILLING_REPLY = ("חשבון ה-API של Claude חסום כרגע: נגמר הקרדיט, או שהגעת למגבלת השימוש החודשית שהגדרת. "
                 "אז אני לא יכול לענות. שמרתי את ההודעה, ואטפל בה לבד כשהגישה תחזור. "
                 "אפשר להעלות את המגבלה או להוסיף קרדיט ב-console.anthropic.com (Limits, Plans & Billing).")


def is_billing_error(e: Exception) -> bool:
    """Out of credit, or the monthly spend limit the user set in the console: nothing works until they change it."""
    msg = str(getattr(e, "message", "") or e).lower()
    return "credit balance" in msg or "usage limit" in msg


class Coach:
    tools = TOOLS  # plus ESCALATE_TOOL when FAST_MODEL is set (see __init__)
    def __init__(self, store: Store, cal: Calendar):
        self.store, self.cal = store, cal
        self.client = anthropic.AsyncAnthropic()
        # The instructions and tools stay cached for an hour: The user's messages are often 5-60 minutes apart.
        self.system = [{"type": "text", "text": load_system(), "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
        self.tools = enabled_tools() + ([ESCALATE_TOOL] if FAST_MODEL else [])

    last_call = 0.0  # time of the last Claude request (real or keep-warm)

    def history(self, chat_id: int) -> list[dict]:
        """The stored conversation, with a 1-hour cache marker on its last message, so the next
        message reads it from the cache instead of writing it again."""
        msgs = self.store.recent_chat(chat_id)
        if msgs:
            last = msgs[-1]
            msgs[-1] = {"role": last["role"], "content": [
                {"type": "text", "text": last["content"], "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}
        return msgs

    async def keep_warm(self, chat_id: int) -> bool:
        """Refresh the cached instructions, tools and history without generating anything (max_tokens 0).
        Costs one cache read, about a cent, instead of a full rewrite (~$0.35) after an hour of quiet."""
        messages = self.history(chat_id) + [{"role": "user", "content": "keep-warm"}]
        try:
            resp = await self.client.beta.messages.create(
                model=FAST_MODEL or MODEL, max_tokens=0, system=self.system, tools=self.tools, messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APIError as e:
            log.warning("keep-warm failed: %s", getattr(e, "message", e))
            return False
        self.record_usage(FAST_MODEL or MODEL, resp)
        return True

    async def describe_build(self, image: bytes) -> Optional[str]:
        """One look at the questionnaire's body photo. A separate call with no history, tools or cache, and
        nothing is queued on failure, so the photo is never stored. Returns the description, or None."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.standard_b64encode(image).decode()}},
                   {"type": "text", "text": "תאר את המבנה."}]
        try:
            resp = await self.client.beta.messages.create(
                model=MODEL, max_tokens=4000, system=BUILD_PROMPT, messages=[{"role": "user", "content": content}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APIError as e:
            log.warning("body photo look failed: %s", type(e).__name__)
            return None
        self.record_usage(MODEL, resp)
        if resp.stop_reason == "refusal":
            return None
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        return None if not text or text == "NONE" else text

    def record_usage(self, model: str, resp: Any) -> None:
        """Log one call's tokens and add them to today's totals (meta usage:<date>)."""
        self.last_call = time.time()
        u = getattr(resp, "usage", None)
        if u is None:
            return
        cost = usage_cost(model, u)
        log.info("usage %s in=%s read=%s write=%s out=%s $%.4f", model, u.input_tokens,
                 getattr(u, "cache_read_input_tokens", 0), getattr(u, "cache_creation_input_tokens", 0), u.output_tokens, cost)
        key = f"usage:{domain.today().isoformat()}"
        day = json.loads(self.store.get_meta(key) or "{}")
        m = day.setdefault(model, {"calls": 0, "in": 0, "read": 0, "write": 0, "out": 0, "usd": 0.0})
        m["calls"] += 1
        m["in"] += u.input_tokens or 0
        m["read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        m["write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        m["out"] += u.output_tokens or 0
        m["usd"] = round(m["usd"] + cost, 4)
        self.store.set_meta(key, json.dumps(day))

    async def respond(self, chat_id: int, content: Any, history_text: str, queue: bool = True) -> str:
        """content: str or list of content blocks (image + text). Returns the reply text.

        Out of API credit: the message is saved (queue=True) and BILLING_REPLY is returned.
        """
        self.store.program()  # live targets and week from config/program
        now = domain.now()
        try:  # answer from today's calendar as it is now, not as it was when the plan was written
            fresh = await planner.refresh_today(self.store, self.cal, now.date(), now.hour * 60 + now.minute)
            if fresh.get("changes") or fresh.get("gym_moved"):
                ToolRunner(self.store, self.cal).rebalance(now.date().isoformat())
        except Exception:
            log.exception("calendar refresh failed")
        runner = ToolRunner(self.store, self.cal)
        messages: list[dict] = self.history(chat_id)
        messages.append({"role": "user", "content": content})
        messages.append({"role": "system", "content": context_message(self.store)})
        reply = ""
        failed = False  # an API error: the turn is not kept in the chat history
        model = FAST_MODEL or MODEL
        for _ in range(MAX_STEPS):
            try:
                resp = await self.client.beta.messages.create(
                    model=model, max_tokens=16000, system=self.system, tools=self.tools, messages=messages,
                    output_config={"effort": "medium"},
                    cache_control={"type": "ephemeral"},  # the conversation so far, reused by the next step of this turn
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                )
            except anthropic.RateLimitError:
                reply, failed = "יש עומס על השרת של Claude. נסה שוב בעוד דקה.", True
                break
            except anthropic.APIStatusError as e:
                log.error("API error %s: %s", e.status_code, e.message)
                if is_billing_error(e):
                    # Nothing went through: keep the message and handle it once credit is back.
                    if queue:
                        self.store.add_pending(chat_id, content, history_text)
                    return BILLING_REPLY
                reply, failed = f"שגיאה בחיבור ל-Claude ({e.status_code}). נסה שוב.", True
                break
            except anthropic.APIConnectionError:
                reply, failed = "אין חיבור ל-Claude כרגע. נסה שוב בעוד רגע.", True
                break
            self.record_usage(model, resp)
            if resp.stop_reason == "refusal":
                reply = "לא הצלחתי לענות על זה. נסה לנסח אחרת."
                break
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                reply = "\n".join(b.text for b in resp.content if b.type == "text").strip()
                break
            results = []
            for b in calls:
                if b.name == ESCALATE_TOOL["name"]:
                    model = MODEL  # the rest of this turn runs on the senior model
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps({"escalated": True, "model": MODEL}), "is_error": False})
                    continue
                out, err = await runner.run(b.name, b.input if isinstance(b.input, dict) else json.loads(b.input))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        else:
            reply = reply or "עצרתי באמצע. כתוב לי שוב מה חסר."
        reply = reply or "רשמתי."
        if not failed:
            self.store.add_chat(chat_id, "user", history_text)
            self.store.add_chat(chat_id, "assistant", reply)
        return reply
