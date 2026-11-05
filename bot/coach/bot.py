"""Telegram bot: chat with the coach, reminders through the day, evening planning."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import datetime as dt
import logging
import os
import time

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update,
                      WebAppInfo)
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          TypeHandler, filters)

from . import alarms, domain, eta, gcal, movement, onboarding, planner, reminders, stt, web
from .agent import BILLING_REPLY, Coach
from .gcal import Calendar
from .store import Store
from .tools import ToolRunner, week_summary

log = logging.getLogger("coach")

TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID") or 0)
DB_PATH = os.environ.get("DB_PATH", "coach.db")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN", "google_token.json")
WEB_URL = os.environ.get("WEB_URL", "")  # https://<host>/  (Mini App)
# Google Tasks sync is off since 2026-10-21: the user works with the bot, and the tasks only duplicated its reminders.
GOOGLE_TASKS = os.environ.get("GOOGLE_TASKS") == "1"
# Planned reminders (wake, meals, leave, sets, packing, follow-ups). Set AUTO_REMINDERS=0 to switch them off;
# reminders the user asks for (alarms), snoozes, the 16:00 check and the 21:30 summary keep working.
AUTO_REMINDERS = os.environ.get("AUTO_REMINDERS", "1") != "0"

store = Store(DB_PATH)
cal = Calendar(GOOGLE_TOKEN)
coach = Coach(store, cal)


def is_owner(update: Update) -> bool:
    return bool(OWNER_ID) and update.effective_user is not None and update.effective_user.id == OWNER_ID


def app_button() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("📊 פתח מעקב", web_app=WebAppInfo(url=WEB_URL))] if WEB_URL else []


def keyboard(buttons: list[tuple[str, str]], with_app: bool = False) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(label, callback_data=data) for label, data in buttons]] if buttons else []
    if with_app and WEB_URL:
        rows.append(app_button())
    return InlineKeyboardMarkup(rows) if rows else None


async def send(ctx: ContextTypes.DEFAULT_TYPE, text: str, buttons=None, with_app=False) -> None:
    for i in range(0, len(text), 4000):
        last = i + 4000 >= len(text)
        await ctx.bot.send_message(OWNER_ID, text[i:i + 4000],
                                   reply_markup=keyboard(buttons or [], with_app) if last else None)


# ---------- commands and messages ----------

async def log_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    msg = update.effective_message
    kind = "callback" if update.callback_query else ("location" if msg and msg.location else
                                                     "command" if update.message and (update.message.text or "").startswith("/") else "message")
    log.info("update %s from user %s (owner=%s)", kind, u.id if u else None, is_owner(update))


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("handler error", exc_info=ctx.error)
    if isinstance(update, Update) and is_owner(update) and update.effective_chat:
        await ctx.bot.send_message(update.effective_chat.id, "משהו נכשל אצלי. נסה שוב, ואם זה חוזר תגיד ל-Claude.")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text(f"מספר המשתמש שלך: {update.effective_user.id}")
        return
    if onboarding.fresh_install(store):
        await send_onboarding(update)
        return
    await update.message.reply_text(
        "אני המאמן שלך. כתוב לי מה אכלת, כמה שקלת ומה הרמת, או שלח תמונה של הצלחת.\n"
        "/today מצב היום · /plan התוכנית למחר · /app המעקב",
        reply_markup=InlineKeyboardMarkup([app_button()]) if WEB_URL else None)


async def send_onboarding(update: Update) -> None:
    """The questionnaire is a page inside Telegram, like the tracker."""
    if not WEB_URL:
        await update.message.reply_text("השאלון הוא דף בתוך טלגרם, וצריך להגדיר WEB_URL כדי לפתוח אותו.")
        return
    url = WEB_URL.rstrip("/") + "/onboarding"
    await update.message.reply_text("שאלון הפתיחה: 9 שאלות קצרות, שמהן אני בונה את הבסיס שלי עליך.",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 פתח את השאלון", web_app=WebAppInfo(url=url))]]))


async def cmd_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await send_onboarding(update)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    d = store.day(domain.today().isoformat())
    t, rem = domain.totals(d), domain.remaining(d)
    lines = [f"{domain.day_label(domain.today())}: {t['k']:,.0f} קק״ל, {t['p']:.0f} ג׳ חלבון.",
             f"נשאר: {max(0, rem['k']):,.0f} קק״ל, {max(0, rem['p']):.0f} ג׳ חלבון, {max(0, rem['c']):.0f} ג׳ פחמימה."]
    lines += [f"{'✓' if c['ok'] else '•'} {c['label']}: {c['det']}" for c in domain.checklist(d)]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([app_button()]) if WEB_URL else None)


async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    tom = domain.today() + dt.timedelta(days=1)
    day = store.day(tom.isoformat())
    plan = domain.day_plan(store.schedule(), day)
    items, plans = store.library()
    cd = day["cycleDay"] or domain.planned_cycle(tom)
    lines = [f"מחר, {domain.day_label(tom)}: יום {cd}, {domain.CYCLE[cd]}."]
    for x in plan:
        lines.append(f"{x['t']} {x['n']}" + (f" · {x['d']}" if x.get("d") else ""))
        if x["kind"] == "train" and day["type"] == "train":
            lines += ["   " + s for s in domain.workout_lines(store.days(), items, plans, cd, tom.isoformat())]
    await update.message.reply_text("\n".join(lines))


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update) and WEB_URL:
        await update.message.reply_text("המעקב:", reply_markup=InlineKeyboardMarkup([app_button()]))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await ctx.bot.send_chat_action(OWNER_ID, ChatAction.TYPING)
    reply = await coach.respond(OWNER_ID, update.message.text, update.message.text)
    await send(ctx, reply)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await ctx.bot.send_chat_action(OWNER_ID, ChatAction.TYPING)
    photo = update.message.photo[-1]
    f = await photo.get_file()
    data = bytes(await f.download_as_bytearray())
    caption = update.message.caption or "מה יש בצלחת? תעריך ותרשום."
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(data).decode()}},
        {"type": "text", "text": caption},
    ]
    reply = await coach.respond(OWNER_ID, content, f"[תמונה] {caption}")
    await send(ctx, reply)


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    if not stt.enabled():
        await update.message.reply_text("הודעות קוליות עוד לא מחוברות. כתוב לי בטקסט בינתיים.")
        return
    await ctx.bot.send_chat_action(OWNER_ID, ChatAction.TYPING)
    media = update.message.voice or update.message.audio
    f = await media.get_file()
    data = bytes(await f.download_as_bytearray())
    try:
        text = await stt.transcribe(data, mime=media.mime_type or "audio/ogg")
    except Exception as e:  # network or API error
        log.error("transcription failed: %s", e)
        await update.message.reply_text("לא הצלחתי לתמלל את ההקלטה. נסה שוב או כתוב בטקסט.")
        return
    if not text:
        await update.message.reply_text("לא שמעתי כלום בהקלטה. נסה שוב.")
        return
    reply = await coach.respond(OWNER_ID, text, f"[קולית] {text}")
    await send(ctx, f"🎤 שמעתי: {text}\n\n{reply}")


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The user's location (live sharing arrives as edited messages) goes to the drive-time service, not to Claude."""
    if not is_owner(update):
        return
    msg = update.effective_message
    loc = msg.location
    when = msg.edit_date or msg.date
    store.set_meta("last_location", json.dumps({"lat": loc.latitude, "lon": loc.longitude, "ts": when.timestamp()}))
    # An active live location carries live_period; an edit without it means the sharing ended.
    movement.record(store, loc.latitude, loc.longitude, when.timestamp(), loc.horizontal_accuracy,
                    msg.date.timestamp() + loc.live_period if loc.live_period else None,
                    ended=bool(update.edited_message and not loc.live_period))
    saved = await eta.post_location(loc.latitude, loc.longitude, msg.edit_date or msg.date,
                                    accuracy=loc.horizontal_accuracy)
    if update.edited_message:
        return  # a live update: no reply
    if not eta.enabled():
        text = "זמני נסיעה מ-Waze עוד לא מחוברים, אז המיקום לא נשמר."
    elif not saved:
        text = "לא הצלחתי לשמור את המיקום. נסה שוב בעוד רגע."
    elif loc.live_period:
        text = ("📍 מיקום חי מחובר. זמני הנסיעה יחושבו ממקום שאתה נמצא בו.\n"
                "כדי שאדע אם זזת היום, השיתוף צריך להתחיל עד 11:00 ולהימשך עד 17:00.")
    else:
        text = "📍 קיבלתי את המיקום."
    await msg.reply_text(text)


# ---------- logging a planned meal (button or checked-off task) ----------

def log_planned_meal(ds: str, i: int) -> dict | None:
    """Log plan entry i of day ds as eaten. Returns what's left, or None if it isn't a meal."""
    day = store.day(ds)
    plan = domain.day_plan(store.schedule(), day)
    if not 0 <= i < len(plan) or "p" not in plan[i]:
        return None
    x = plan[i]
    item = {"n": x["n"] + (f": {x['d']}" if x.get("d") else ""), "p": x["p"], "f": x["f"], "c": x["c"]}
    if "k" in x:
        item["k"] = x["k"]
    day["items"].append(item)
    if "קריאטין" in (x.get("d") or ""):
        day["cr"][0] = True
    store.save_day(day)
    ToolRunner(store, cal).rebalance(ds)
    return domain.remaining(store.day(ds))


# ---------- buttons ----------

def set_at_home_text(ds: str) -> str:
    """The user pressed "I'm home": today's on-the-go meals go back to their home versions."""
    d = store.day(ds)
    d["atHome"] = True
    plan = d.get("plan") or [dict(x) for x in domain.day_plan(store.schedule(), d)]
    domain.reconcile_away(plan, [], 0, store.schedule()[d["type"]])
    d["plan"] = plan
    store.save_day(d)
    ToolRunner(store, cal).rebalance(ds)
    now_min = domain.now().hour * 60 + domain.now().minute
    plan = domain.day_plan(store.schedule(), store.day(ds))
    left = [f"{x['t']} {x['n']}: {x['d']}" for x in plan if x["kind"] == "meal" and "p" in x and (domain.minutes(x["t"]) or 0) > now_min]
    return "\n".join(["הבנתי, אתה בבית היום. התוכנית חזרה לגרסה של הבית:", *left]) if left else "הבנתי, אתה בבית היום."


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_owner(update):
        await q.answer()
        return
    parts = (q.data or "").split(":")
    kind = parts[0]
    if kind == "meal" and len(parts) == 3:
        ds, i = parts[1], int(parts[2])
        name = (domain.day_plan(store.schedule(), store.day(ds))[i:i + 1] or [{"n": ""}])[0]["n"]
        if name in domain.logged_plan_names(store.day(ds)):
            await q.answer("כבר נרשם")
            return
        rem = log_planned_meal(ds, i)
        if rem is None:
            await q.answer("לא מצאתי את הארוחה")
            return
        await q.answer("נרשם ✓")
        await q.edit_message_text(f"{q.message.text}\n\n✓ נרשם. נשאר היום {max(0, rem['k']):,.0f} קק״ל, {max(0, rem['p']):.0f} ג׳ חלבון.")
    elif kind == "still" and len(parts) == 3:
        await q.answer()
        await q.edit_message_reply_markup(None)
        text = await still_choice(parts[1], parts[2])
        store.add_chat(OWNER_ID, "user", f"[כפתור בבדיקת התנועה: {parts[1]}]")
        store.add_chat(OWNER_ID, "assistant", text)
        await send(ctx, text)
    elif kind == "noworkout" and len(parts) == 2:
        await q.answer()
        await q.edit_message_reply_markup(None)
        text = await no_workout(parts[1])
        store.add_chat(OWNER_ID, "user", "[כפתור: לא התאמנתי]")
        store.add_chat(OWNER_ID, "assistant", text)
        await send(ctx, text)
    elif kind == "home" and len(parts) == 2:
        await q.answer("עדכנתי")
        await send(ctx, set_at_home_text(parts[1]))
    elif kind == "cr" and len(parts) == 3:
        ds = parts[1]
        day = store.day(ds)
        day["cr"] = [True, False]
        store.save_day(day)
        await q.answer("נרשם ✓")
        await q.edit_message_text(f"{q.message.text}\n\n✓ קריאטין נרשם (5 ג׳).")
    elif kind == "snooze":
        key = ":".join(parts[1:])
        store.add_snooze(key + f":{time.time():.0f}", time.time() + 15 * 60,
                         {"text": q.message.text, "buttons": [[b.text, b.callback_data] for row in (q.message.reply_markup.inline_keyboard if q.message.reply_markup else []) for b in row if b.callback_data and not b.callback_data.startswith("snooze")]})
        await q.answer("אזכיר בעוד 15 דקות")
        await q.edit_message_reply_markup(None)
    elif kind == "asplan" and len(parts) == 2:
        ds = parts[1]
        runner = ToolRunner(store, cal)
        await runner.run("load_workout_plan", {"date": ds, "with_reps": True})
        d = store.day(ds)
        d["workout"]["fromPlan"] = True  # not counted as a workout until the user confirms it
        store.save_day(d)
        await runner.run("set_workout_done", {"date": ds, "done": True})
        day = store.day(ds)
        n = sum(len(domain.done_sets(e)) for e in day["workout"]["exercises"])
        await q.answer("נרשם ✓")
        await q.edit_message_text(f"{q.message.text}\n\n✓ רשמתי את האימון לפי התוכנית ({n} סטים). אם משהו היה שונה, תכתוב לי.")
    else:
        await q.answer()


# ---------- a day without moving ----------

async def still_choice(choice: str, ds: str) -> str:
    """The user's answer to the movement check. Nothing changed until now."""
    runner = ToolRunner(store, cal)
    if choice == "walk":
        at = runner.plan_walk(ds)
        return f"סגור. {movement.WALK_MIN} דקות הליכה, והאוכל נשאר כמו שהוא." + (f" אזכיר לך ב-{at}." if at else "")
    if choice == "cut":
        out, err = await runner.run("still_day", {"date": ds, "confirm": True})
        body = json.loads(out)
        if err or not body.get("applied"):
            log.warning("still_day not applied: %s", out)
            return "לא שיניתי כלום: ההצעה כבר לא בתוקף. כתוב לי אם עדיין רלוונטי."
        meal = f" ב{body['meal']}" if body.get("meal") else ""
        return (f"עדכנתי ליום אחד: {body['carbs']} ג׳ פחמימות פחות{meal}. היעד היום {body['after']:,.0f} קק״ל, "
                "החלבון והשומן לא זזו. מחר חוזרים ליעד הרגיל.")
    return "בסדר, בלי שינוי."


async def no_workout(ds: str) -> str:
    """He said there was no workout: the same two steps as when they say it in the chat."""
    runner = ToolRunner(store, cal)
    await runner.run("skip_workout", {"date": ds, "reason": "לפי המיקום החי לא יצא, והוא אישר שלא התאמן"})
    try:
        _, err = await runner.run("set_day_program", {"date": ds, "type": "rest"})
    except Exception:  # the calendar failed; the log is already right
        log.exception("set_day_program failed")
        err = True
    if err:
        return "רשמתי שלא היה אימון. לא הצלחתי לעדכן את התוכנית של היום, אז היעד אולי לא התעדכן."
    g = domain.day_target(store.day(ds))
    return f"רשמתי שלא היה אימון, והפכתי את היום ליום מנוחה. היעד: {g['k']:,.0f} קק״ל ו-{g['p']:g} ג׳ חלבון."


async def movement_check(ctx: ContextTypes.DEFAULT_TYPE, ds: str) -> None:
    """Rest days at 17:00: if live location shows they stayed put, offer a walk or fewer carbs. Unknown says nothing."""
    s = movement.state(store)
    st = movement.status(s, time.time())
    log.info("movement check %s: %s", ds, st)
    if st != "still":
        return
    out, err = await ToolRunner(store, cal).run("still_day", {"date": ds, "confirm": False})
    body = json.loads(out)
    if err or "preview" not in body:
        log.warning("still_day preview failed: %s", out)
        return
    text = movement.proposal_text(s, body["preview"], week_summary(store).get("change"), onboarding.answers(store).get("goal"))
    store.add_chat(OWNER_ID, "assistant", text)  # so a typed answer has something to answer
    await send(ctx, text, [("🚶 הליכה", f"still:walk:{ds}"), ("🍚 פחות פחמימות", f"still:cut:{ds}"), ("בלי שינוי", f"still:no:{ds}")])


# ---------- scheduled jobs ----------

async def tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Every minute: send the day's due reminders, snoozes and the evening check-in."""
    if not OWNER_ID:
        return
    now = domain.now()
    store.program()  # live targets and week from config/program
    movement.roll_over(store, now.date())  # yesterday's coordinates go at midnight
    for a in alarms.take_due(store, now):  # reminders the user asked for; each is marked before it is sent
        try:
            await send(ctx, f"⏰ {a['text']}")
            log.info("reminder %s sent (due %s)", a["id"], a["time"])
        except Exception:
            log.exception("reminder %s failed to send", a["id"])
    ds = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    day = store.day(ds)
    if not day["closed"]:
        plan = domain.day_plan(store.schedule(), day)
        items, plans = store.library()
        cd = day["cycleDay"] or domain.planned_cycle(now.date())
        lines = domain.workout_lines(store.days(), items, plans, cd, ds) if day["type"] == "train" else []
        all_days = store.days()
        mission = domain.key_lift(all_days, items, plans, cd, ds) if day["type"] == "train" else None
        streak = domain.streak_line(domain.streaks(all_days, now.date()))
        planned = reminders.day_reminders(day, plan, lines, mission, streak) + reminders.follow_ups(day, plan) if AUTO_REMINDERS else []
        # Nothing goes out on a stale plan: read the calendar first (and every 15 minutes, for new events).
        check_due = reminders.CHECK_AT <= now_min < reminders.CHECK_AT + 30 and not store.was_sent(f"{ds}:check16")
        fresh = {"has_gym": None}
        if AUTO_REMINDERS and (check_due or now.minute % 15 == 0
                               or any(not store.was_sent(r.key) for r in reminders.due(planned, now_min))):
            fresh = await planner.refresh_today(store, cal, now.date(), now_min)
            if fresh.get("gym_moved"):
                await send(ctx, f"ראיתי ביומן שהאימון זז ל-{fresh['gym_moved']}. עדכנתי את התוכנית של היום.")
            if fresh.get("gym_moved") or fresh.get("changes"):
                ToolRunner(store, cal).rebalance(ds)
                log.info("plan refreshed from the calendar: %s", fresh.get("changes") or fresh.get("gym_moved"))
                day = store.day(ds)
                plan = domain.day_plan(store.schedule(), day)
                planned = reminders.day_reminders(day, plan, lines, mission, streak) + reminders.follow_ups(day, plan)
        for r in reminders.due(planned, now_min):
            if r.key.endswith(":leave") and fresh.get("has_gym") is False:
                continue  # the gym event is gone from the calendar
            if r.key.endswith(":sets") and any(domain.done_sets(e) for e in day["workout"]["exercises"]):
                continue
            if r.key.endswith(":wake") and day["weight"] is not None:
                continue
            if ":meal:" in r.key:
                i = int(r.key.rsplit(":", 1)[1])
                if i < len(plan) and not domain.meal_still_needed(day, plan[i]):
                    continue  # the day is already (almost) on target
            if store.mark_sent(r.key):
                await send(ctx, r.text, r.buttons)
        if reminders.CHECK_AT <= now_min < reminders.CHECK_AT + 30 and store.mark_sent(f"{ds}:check16"):
            gap = domain.behind(day, plan, now_min)
            if gap["logged"] == 0 or gap["k"] > reminders.BEHIND_K or gap["p"] > reminders.BEHIND_P:
                changes = ToolRunner(store, cal).rebalance(ds)
                await send(ctx, reminders.afternoon_text(gap, changes, domain.remaining(store.day(ds))), with_app=True)
    if (AUTO_REMINDERS and day["type"] == "rest" and not day["closed"]
            and movement.CHECK_AT <= now_min < movement.CHECK_AT + 30 and store.mark_sent(f"{ds}:move")):
        await movement_check(ctx, ds)
    if AUTO_REMINDERS:
        await traffic_check(ctx, now)
    if 3 * 60 + 30 <= now_min < 4 * 60 and store.mark_sent(f"{ds}:backup"):
        await asyncio.to_thread(daily_backup, now.date())
    if now.minute % 5 == 0:
        await sync_tasks(ctx, now)
        await retry_pending(ctx)
    # Keep the prompt cache warm from the day's first Claude call until midnight (see Coach.keep_warm).
    if coach.last_call and now.hour >= 7 and time.time() - coach.last_call >= KEEP_WARM_S \
            and dt.datetime.fromtimestamp(coach.last_call, domain.TZ).date() == now.date():
        await coach.keep_warm(OWNER_ID)
    for s in store.due_snoozes(time.time()):
        await send(ctx, s["text"], [tuple(b) for b in s.get("buttons") or []])
    if reminders.EVENING <= now_min < reminders.EVENING + 60 and store.mark_sent(f"{ds}:evening"):
        await evening(ctx, now)


async def traffic_check(ctx: ContextTypes.DEFAULT_TYPE, now: dt.datetime) -> None:
    """An hour before leaving for an away event: ask Waze again, and move the leave time if traffic changed."""
    try:
        moved = await planner.traffic_check(store, cal, now.date(), now.hour * 60 + now.minute)
    except Exception:
        log.exception("traffic check failed")
        return
    for old, new in moved:
        head = "🚗 עומסים" if new < old else "🚗 הכביש פנוי יותר"
        await send(ctx, f"{head}: לצאת ב-{new} במקום {old}")


KEEP_WARM_S = 55 * 60  # under the 1-hour cache lifetime
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
BACKUP_KEEP = 14


def daily_backup(d: dt.date) -> None:
    """03:30: a copy of the database next to it (data/backups), the last 14 days kept.
    For a copy off the server use scripts/export.py, and keep it private: it holds your health data."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        store.backup_to(os.path.join(BACKUP_DIR, f"coach-{d.isoformat()}.db"))
        old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("coach-") and f.endswith(".db"))[:-BACKUP_KEEP]
        for f in old:
            os.remove(os.path.join(BACKUP_DIR, f))
        log.info("backup written: coach-%s.db (%d removed)", d.isoformat(), len(old))
    except Exception:
        log.exception("daily backup failed")


async def retry_pending(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Messages saved while the API had no credit: handle them in order once it works again."""
    for _ in range(5):
        q = store.pending()
        if not q:
            return
        item = q[0]
        reply = await coach.respond(item["chat_id"], item["content"], item["history"], queue=False)
        if reply == BILLING_REPLY:
            return  # still no credit; try again in 5 minutes
        store.set_pending(store.pending()[1:])
        await send(ctx, f"על ההודעה ששלחת כשהקרדיט נגמר (\"{item['history'][:60]}\"):\n\n{reply}")


async def sync_tasks(ctx: ContextTypes.DEFAULT_TYPE, now: dt.datetime) -> None:
    """Google Tasks: log checked-off meals, and rewrite a day's tasks when its plan changed."""
    if not GOOGLE_TASKS or not cal.tasks_enabled:
        return
    ds = now.date().isoformat()
    try:
        for task_id, idx in await asyncio.to_thread(cal.completed_tasks, ds):
            if not store.mark_sent(f"{ds}:task:{task_id}"):
                continue
            day = store.day(ds)
            plan = domain.day_plan(store.schedule(), day)
            if idx < len(plan) and "p" in plan[idx] and plan[idx]["n"] not in domain.logged_plan_names(day):
                rem = log_planned_meal(ds, idx)
                if rem is not None:
                    await send(ctx, f"✓ {plan[idx]['n']} נרשם מ-Google Tasks. נשאר היום {max(0, rem['k']):,.0f} קק״ל, {max(0, rem['p']):.0f} ג׳ חלבון.")
        tom = now.date() + dt.timedelta(days=1)
        days = [now.date()] + ([tom] if store.day(tom.isoformat()).get("plan") else [])
        items, plans = store.library()
        for d in days:
            day = store.day(d.isoformat())
            plan = domain.day_plan(store.schedule(), day)
            cd = day["cycleDay"] or domain.planned_cycle(d)
            lines = domain.workout_lines(store.days(), items, plans, cd, d.isoformat()) if day["type"] == "train" else []
            tasks = gcal.task_items(plan, lines, domain.PREP.get(d.weekday()))
            sig = hashlib.sha1(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if store.get_meta(f"tasks:{d.isoformat()}") != sig:
                n = await asyncio.to_thread(cal.sync_day_tasks, d.isoformat(), tasks)
                store.set_meta(f"tasks:{d.isoformat()}", sig)
                log.info("google tasks for %s synced (%d created)", d, n)
    except Exception:
        log.exception("google tasks sync failed")


async def evening(ctx: ContextTypes.DEFAULT_TYPE, now: dt.datetime) -> None:
    ds = now.date().isoformat()
    day = store.day(ds)
    logged_plan = False
    unreported = day["type"] == "train" and not day["workout"]["exercises"] and not day["closed"] and not day["workout"].get("skipped")
    # Live location says they never left: don't log a workout from the plan, ask.
    ask_gym = unreported and movement.status(movement.state(store), now.timestamp()) == "still"
    if unreported and not ask_gym:
        # the user's standing choice: log an unreported workout from the plan, they correct it.
        runner = ToolRunner(store, cal)
        await runner.run("load_workout_plan", {"date": ds, "with_reps": True})
        await runner.run("add_note", {"date": ds, "target": "workout",
                                      "text": "נרשם אוטומטית לפי התוכנית. לתקן מה שהיה שונה ולסמן שהאימון הושלם."})
        day = store.day(ds)
        logged_plan = bool(day["workout"]["exercises"])
    tom = now.date() + dt.timedelta(days=1)
    try:
        result = await planner.plan_day(store, cal, tom)
    except Exception:
        log.exception("planning tomorrow failed")
        result = None
    await sync_tasks(ctx, now)
    week = week_summary(store) if now.weekday() == 6 else None
    all_days = store.days()
    extras = {"prs": domain.prs_on(all_days, ds), "streak": domain.streak_line(domain.streaks(all_days, now.date())),
              "progress": domain.progress_since_start(all_days) if week else []}
    await send(ctx, reminders.evening_text(day, result, tom, logged_plan, week, extras), with_app=True)
    if ask_gym:
        question = "לפי המיקום החי לא יצאת היום, אז לא רשמתי אימון מהתוכנית. היה אימון?"
        store.add_chat(OWNER_ID, "assistant", question)
        await send(ctx, question, [("✓ התאמנתי לפי התוכנית", f"asplan:{ds}"), ("לא התאמנתי", f"noworkout:{ds}")])


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand("today", "מצב היום"), BotCommand("plan", "התוכנית למחר"),
                                   BotCommand("app", "פתח את המעקב"), BotCommand("onboarding", "שאלון פתיחה")])
    if WEB_URL and OWNER_ID:
        await app.bot.set_chat_menu_button(OWNER_ID, MenuButtonWebApp("מעקב", WebAppInfo(url=WEB_URL)))
    await web.start(store, TOKEN, OWNER_ID, describe=coach.describe_build,
                    notify=lambda text: app.bot.send_message(OWNER_ID, text))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    loaded = store.load_starter(os.path.join(os.path.dirname(__file__), "..", "starter"))
    if loaded:
        log.info("new install: loaded the starter plan %s", loaded)
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, log_update), group=-1)
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("app", cmd_app))
    app.add_handler(CommandHandler("onboarding", cmd_onboarding))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.LOCATION & (filters.UpdateType.MESSAGE | filters.UpdateType.EDITED_MESSAGE),
                                   on_location))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(tick, interval=60, first=5)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
