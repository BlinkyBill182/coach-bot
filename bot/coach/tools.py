"""Tools Claude uses to read and change the user's log, plan and calendar."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from .personal import HOME_ADDRESS
from . import alarms, domain, eta, movement, planner, wolt
from .gcal import Calendar
from .store import Store

DATE = {"type": "string", "description": "Local date, YYYY-MM-DD."}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: list[dict] = [
    {"name": "get_day", "description": "Read one day's log: weight, creatine, meals with macros, totals, what's left against the target, workout sets, the closing checklist and the day's plan.",
     "input_schema": _obj({"date": DATE}, ["date"])},
    {"name": "add_meals", "description": "Log one or more foods for a day. Estimate grams of protein, fat and carbs; pass kcal only when it comes from a label.",
     "input_schema": _obj({"date": DATE, "items": {"type": "array", "minItems": 1, "items": _obj({
         "name": {"type": "string", "description": "Short Hebrew name with the amount, e.g. 'תבשיל עוף: 100 ג׳ עוף, 500 ג׳ בטטה'."},
         "protein": {"type": "number"}, "fat": {"type": "number"}, "carbs": {"type": "number"},
         "kcal": {"type": "number", "description": "Only from a label."}}, ["name", "protein", "fat", "carbs"])}},
         ["date", "items"])},
    {"name": "remove_meal", "description": "Remove a logged food by its index from get_day.",
     "input_schema": _obj({"date": DATE, "index": {"type": "integer", "minimum": 0}}, ["date", "index"])},
    {"name": "set_weight", "description": "Record the morning weight in kg.",
     "input_schema": _obj({"date": DATE, "kg": {"type": "number", "minimum": 30, "maximum": 200}}, ["date", "kg"])},
    {"name": "set_creatine", "description": "Mark today's single 5 g creatine dose as taken (true) or not (false).",
     "input_schema": _obj({"date": DATE, "taken": {"type": "boolean"}}, ["date", "taken"])},
    {"name": "load_workout_plan", "description": "Fill the day's workout with the planned exercises for its cycle day, in order with superset letters, weights pre-filled from the last session. with_reps=true also fills the suggested reps (use when the user says they did the plan as written).",
     "input_schema": _obj({"date": DATE, "with_reps": {"type": "boolean"}}, ["date", "with_reps"])},
    {"name": "log_exercise", "description": "Record the sets of one exercise (weight in kg x reps). Replaces that exercise's sets; adds the exercise if it is not in the workout yet. For an exercise that appears in several supersets (side bends, lateral raise), pass the superset letter.",
     "input_schema": _obj({"date": DATE, "name": {"type": "string", "description": "Exercise name as in the library."},
                           "superset": {"type": "string", "description": "Superset letter, e.g. A. Omit if none."},
                           "sets": {"type": "array", "minItems": 1, "items": _obj({"w": {"type": "number"}, "r": {"type": "integer"}}, ["w", "r"])}},
                          ["date", "name", "sets"])},
    {"name": "remove_exercise", "description": "Remove one exercise row from a day's workout (e.g. a planned exercise the user skipped). superset: the row's letter, '-' for a row without a letter, or omit to take any matching row (an empty one first).",
     "input_schema": _obj({"date": DATE, "name": {"type": "string"}, "superset": {"type": "string"}}, ["date", "name"])},
    {"name": "set_workout_done", "description": "Mark the day's workout as completed or not. Marking it done removes empty sets and empty exercise rows and keeps supersets together.",
     "input_schema": _obj({"date": DATE, "done": {"type": "boolean"}}, ["date", "done"])},
    {"name": "skip_workout", "description": "Record that the user is not training on a planned training day (their decision: tired, busy, out). Removes planned exercises that have no reported reps, marks the day so the evening job doesn't log the workout from the plan, and the day breaks the training streak. Use set_workout_done instead if they did train.",
     "input_schema": _obj({"date": DATE, "reason": {"type": "string", "description": "Short Hebrew reason, e.g. 'הופעה בערב'."}}, ["date", "reason"])},
    {"name": "add_note", "description": "Append a note to the day (sleep, hunger, pain) or to the workout.",
     "input_schema": _obj({"date": DATE, "text": {"type": "string"}, "target": {"type": "string", "enum": ["day", "workout"]}}, ["date", "text", "target"])},
    {"name": "skip_planned_meal", "description": "Mark a meal in today's plan as skipped or replaced (e.g. The user ate lunch out instead of the planned lunch), so it no longer counts. The rest of the day is then rebalanced.",
     "input_schema": _obj({"date": DATE, "item": {"type": "string", "description": "Plan item name, e.g. 'ארוחת צהריים'."},
                           "reason": {"type": "string", "description": "Short Hebrew note, e.g. 'הוחלף בשווארמה'."}}, ["date", "item", "reason"])},
    {"name": "replace_planned_meal", "description": "Replace a meal in a day's plan with what the user will eat instead (a friend's place, a restaurant), with your estimate of its macros, so the day's plan still adds up. The meal stays in the plan (pinned) and the rest of the day is rebalanced. Use skip_planned_meal only when they won't eat that meal at all.",
     "input_schema": _obj({"date": DATE, "item": {"type": "string", "description": "Plan item name, e.g. 'ארוחת ערב'."},
                           "description": {"type": "string", "description": "Short Hebrew: what and where, e.g. 'דייט ב-my yhai: עוף או סלמון עם אורז (הערכה)'."},
                           "p": {"type": "number"}, "f": {"type": "number"}, "c": {"type": "number"},
                           "time": {"type": "string", "description": "HH:MM, optional new time."}}, ["date", "item", "description", "p", "f", "c"])},
    {"name": "set_day_program", "description": "Change what a day is: a training day of a given program day, or a rest day (the user swaps or moves workouts). Training: sets type and cycleDay, loads that day's exercises with last weights (replacing planned rows without reps), creates a gym event if the calendar has none, moves it to start/end, and rebuilds the day's meal plan around it (pinned items stay). Rest: drops planned exercises without reps and rebuilds the plan; if a gym event remains, it is returned so you can ask the user whether to delete it (delete_gym_event). Program days: 1 push, 2 pull, 3 legs, 5 upper, 6 lower.",
     "input_schema": _obj({"date": DATE, "type": {"type": "string", "enum": ["train", "rest"]},
                           "cycle_day": {"type": "integer", "enum": [1, 2, 3, 5, 6], "description": "Program day for a training day."},
                           "start": {"type": "string", "description": "HH:MM workout start (training days). Omit to use the first free slot from 09:00."},
                           "end": {"type": "string", "description": "HH:MM workout end, optional (default 90 min)."}}, ["date", "type"])},
    {"name": "delete_gym_event", "description": "Delete the gym event on a date from Google Calendar. Only after the user said yes (e.g. a day they turned into a rest day).",
     "input_schema": _obj({"date": DATE}, ["date"])},
    {"name": "set_targets", "description": "Lasting change to the daily targets for training or rest days (kcal, protein, fat, carbs). Two steps: confirm=false returns a preview to show the user; after their yes, call again with the same values and confirm=true.",
     "input_schema": _obj({"day_type": {"type": "string", "enum": ["train", "rest"]},
                           "kcal": {"type": "number"}, "protein": {"type": "number"}, "fat": {"type": "number"}, "carbs": {"type": "number"},
                           "confirm": {"type": "boolean"}}, ["day_type", "confirm"])},
    {"name": "set_week", "description": "Lasting change to which weekday is which program day (1 push, 2 pull, 3 legs, 4 rest, 5 upper, 6 lower, 7 rest). Pass only the weekdays that change. Two steps: preview (confirm=false), then apply after the user's yes (confirm=true, same values).",
     "input_schema": _obj({"days": {"type": "object", "description": "e.g. {\"wed\": 5, \"thu\": 4}; keys sun mon tue wed thu fri sat.",
                                    "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 7}},
                           "confirm": {"type": "boolean"}}, ["days", "confirm"])},
    {"name": "update_exercise_plan", "description": "Lasting change to one program day's exercises: the full ordered list with superset letters. New exercise names are added to the library (give muscle). Two steps: preview (confirm=false) shows the change and the weekly sets per muscle; apply after the user's yes (confirm=true, same values).",
     "input_schema": _obj({"program_day": {"type": "integer", "enum": [1, 2, 3, 5, 6]},
                           "exercises": {"type": "array", "minItems": 1, "items": _obj({
                               "name": {"type": "string"}, "superset": {"type": "string"},
                               "sets": {"type": "integer", "minimum": 1, "maximum": 8},
                               "lo": {"type": "integer", "minimum": 1, "maximum": 50}, "hi": {"type": "integer", "minimum": 1, "maximum": 50},
                               "muscle": {"type": "string", "enum": list(domain.MUSCLES)}}, ["name"])},
                           "confirm": {"type": "boolean"}}, ["program_day", "exercises", "confirm"])},
    {"name": "update_meal_template", "description": "Lasting change to the daily meal template for training or rest days: the full list of entries (time, name, description, kind, macros). Two steps: preview (confirm=false) shows totals against the targets; apply after the user's yes (confirm=true, same values).",
     "input_schema": _obj({"day_type": {"type": "string", "enum": ["train", "rest"]},
                           "entries": {"type": "array", "minItems": 1, "items": _obj({
                               "t": {"type": "string", "description": "HH:MM"}, "n": {"type": "string"}, "d": {"type": "string"},
                               "kind": {"type": "string", "enum": ["wake", "meal", "train", "other"]},
                               "p": {"type": "number"}, "f": {"type": "number"}, "c": {"type": "number"}, "k": {"type": "number"}},
                               ["t", "n", "kind"])},
                           "confirm": {"type": "boolean"}}, ["day_type", "entries", "confirm"])},
    {"name": "wolt_search", "description": "Restaurants on Wolt that deliver now, best rated first. where: an address or place in Israel; omit to use the user's last shared location (or their home if none). query: one word to match (e.g. 'עוף', 'סושי', 'poke', 'salad', 'בריאות'). Returns name, slug, delivery minutes, rating, price level, link.",
     "input_schema": _obj({"where": {"type": "string"}, "query": {"type": "string"},
                           "max_minutes": {"type": "integer", "minimum": 10, "maximum": 120}}, [])},
    {"name": "wolt_menu", "description": "The menu of one Wolt restaurant (slug from wolt_search): dishes by category with price and description. Drinks, desserts and dishes with beef or flour products are already left out.",
     "input_schema": _obj({"slug": {"type": "string"}}, ["slug"])},
    {"name": "set_at_home", "description": "The user stays home on this date, whatever the calendar says (at_home=true): on-the-go meals go back to their home versions and events no longer count as outings that day. at_home=false undoes it.",
     "input_schema": _obj({"date": DATE, "at_home": {"type": "boolean"}}, ["date", "at_home"])},
    {"name": "still_day", "description": "One-day change for a rest day on which the user stayed in one place (they told you, or the movement check found it). Lowers only that day's calories and carbs by a small amount the server computes (100-150 kcal), taken from the biggest carb meal still ahead; protein and fat don't move, and tomorrow is back to normal. Two steps: confirm=false returns a preview to show the user with the alternative of a 30-minute walk; apply only after their yes in a later message (confirm=true). undo=true takes it back. It's an estimate of calories burned: never say anything about burning fat.",
     "input_schema": _obj({"date": DATE, "confirm": {"type": "boolean"}, "undo": {"type": "boolean"}}, ["date", "confirm"])},
    {"name": "close_day", "description": "Close a day after the checklist is complete (or when the user asks to close it anyway).",
     "input_schema": _obj({"date": DATE}, ["date"])},
    {"name": "get_exercise_history", "description": "Last sessions of an exercise with weight x reps, and the suggested next load.",
     "input_schema": _obj({"name": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["name"])},
    {"name": "get_plan", "description": "The schedule for a date: wake, meals with times and macros, workout time with exercises and loads, cooking-day tasks, and calendar events.",
     "input_schema": _obj({"date": DATE}, ["date"])},
    {"name": "move_workout", "description": "Move the day's workout to a new start time (HH:MM): updates the gym event in Google Calendar and re-times the day's plan. Omit start to pick the first free slot around the calendar.",
     "input_schema": _obj({"date": DATE, "start": {"type": "string", "description": "HH:MM, 24h."}}, ["date"])},
    {"name": "set_plan_time", "description": "Change the time of one item in a day's plan (e.g. lunch to 15:00). The item is pinned: later re-planning keeps it.",
     "input_schema": _obj({"date": DATE, "item": {"type": "string", "description": "Plan item name, e.g. 'ארוחת צהריים'."},
                           "time": {"type": "string", "description": "HH:MM"}}, ["date", "item", "time"])},
    {"name": "get_drive_time", "description": "Live drive time from Waze, from the user's current location (their Telegram live location) to a destination: minutes, km, route and arrival time. depart_at: leave later than now (Waze predicts the traffic for that hour). Use for every 'how long to X'.",
     "input_schema": _obj({"destination": {"type": "string", "description": "Address or place name, Hebrew or English, e.g. 'תל אביב', 'רחוב הרצל 1, ראשון לציון'."},
                           "depart_at": {"type": "string", "description": "YYYY-MM-DD HH:MM, Israel time. Omit for now."}}, ["destination"])},
    {"name": "set_travel_time", "description": "Fallback when Waze has no answer or the event has no address: save how many minutes it takes the user to get to a place, used to time 'leave now' and packing reminders for events there. place: a word from the event's location or title (e.g. \"Herzl\", \"Dana\"). Use place \"default\" for everywhere else.",
     "input_schema": _obj({"place": {"type": "string"}, "minutes": {"type": "integer", "minimum": 5, "maximum": 180}}, ["place", "minutes"])},
    {"name": "set_reminder", "description": "Schedule a Telegram message to the user at a time they pick (Israel time). It is sent prefixed with ⏰. repeat: none (default), daily, or weekdays (Sunday to Thursday). Returns its id and time.",
     "input_schema": _obj({"datetime": {"type": "string", "description": "YYYY-MM-DD HH:MM, Israel time."},
                           "text": {"type": "string", "description": "The reminder text in Hebrew, as the user will read it."},
                           "repeat": {"type": "string", "enum": ["none", "daily", "weekdays"]}}, ["datetime", "text"])},
    {"name": "list_reminders", "description": "Upcoming reminders the user set: id, time, text, repeat.",
     "input_schema": _obj({}, [])},
    {"name": "cancel_reminder", "description": "Cancel a reminder by its id (from list_reminders or set_reminder).",
     "input_schema": _obj({"id": {"type": "integer"}}, ["id"])},
    {"name": "remember", "description": "Save a lasting fact about the user's routine, preferences or constraints (e.g. 'Tuesdays they work until 18:00', 'doesn't like salmon'). These notes are shown to you in every conversation.",
     "input_schema": _obj({"fact": {"type": "string"}}, ["fact"])},
    {"name": "forget", "description": "Delete a saved fact by its number in the notes list.",
     "input_schema": _obj({"index": {"type": "integer", "minimum": 0}}, ["index"])},
    {"name": "week_summary", "description": "Last 7 vs previous 7 days: average weight and change, average calories and protein, workouts, sets per muscle over the last 6 days.",
     "input_schema": _obj({}, [])},
]


def enabled_tools() -> list[dict]:
    """Tools of add-ons that are off don't reach Claude, so it doesn't offer what it can't do."""
    off = set()
    if not wolt.enabled():
        off |= {"wolt_search", "wolt_menu"}
    if not eta.enabled():
        off |= {"get_drive_time"}
    return [t for t in TOOLS if t["name"] not in off]


def _day_view(store: Store, ds: str) -> dict:
    d = store.day(ds)
    t, rem = domain.totals(d), domain.remaining(d)
    return {
        "date": ds, "label": domain.day_label(domain.parse_date(ds)), "type": d["type"],
        "cycleDay": d["cycleDay"] or domain.planned_cycle(domain.parse_date(ds)),
        "weight": d["weight"], "creatine_5g_taken": domain.creatine_taken(d),
        "meals": [{"index": i, "name": x["n"], "p": x["p"], "f": x["f"], "c": x["c"], "kcal": domain.kcal(x)} for i, x in enumerate(d["items"])],
        "totals": {k: round(v) for k, v in t.items()}, "target": domain.day_target(d), "target_change_today": d.get("targetAdj"),
        "left": {k: round(v) for k, v in rem.items()},
        "workout": d["workout"], "notes": d["notes"], "closed": d["closed"],
        "checklist": domain.checklist(d),
    }


class ToolRunner:
    def __init__(self, store: Store, cal: Calendar):
        self.store, self.cal = store, cal
        self.handlers: dict[str, Callable[[dict], Awaitable[Any]]] = {
            t["name"]: getattr(self, "t_" + t["name"]) for t in TOOLS
        }
        self.changed = False  # a write happened during this turn
        self.turn = uuid.uuid4().hex  # previews from this turn can't be confirmed in it

    # ---------- lasting changes: preview first, apply after the user's yes ----------

    def _gate(self, name: str, a: dict) -> bool:
        """False: this is a preview (remembered). True: a confirmed apply of an earlier turn's preview."""
        args = {k: v for k, v in a.items() if k != "confirm"}
        h = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        key = f"preview:{name}"
        if not a.get("confirm"):
            self.store.set_meta(key, json.dumps({"h": h, "turn": self.turn, "ts": time.time()}))
            return False
        prev = json.loads(self.store.get_meta(key) or "{}")
        if prev.get("h") != h:
            raise ValueError("no preview of exactly this change: call with confirm=false, show the user, apply after their yes")
        if prev.get("turn") == self.turn:
            raise ValueError("The user hasn't answered the preview yet: send it to them and apply only after their yes in a later message")
        if time.time() - prev.get("ts", 0) > 6 * 3600:
            raise ValueError("the preview is over 6 hours old: show it again")
        self.store.set_meta(key, "{}")
        return True

    def _note(self, text: str) -> None:
        notes = self.store.profile_notes()
        notes.append({"text": f"שינוי קבוע ({domain.today().strftime('%d.%m.%Y')}): {text}", "date": domain.today().isoformat(), "source": "bot"})
        self.store.set_profile_notes(notes)

    def _program_doc(self) -> dict:
        return domain.norm_program(self.store.data("config/program"))

    async def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (json result, is_error)."""
        fn = self.handlers.get(name)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"}), True
        try:
            return json.dumps(await fn(args), ensure_ascii=False, default=str), False
        except (ValueError, KeyError, TypeError) as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False), True

    def _edit(self, ds: str, fn: Callable[[dict], None]) -> dict:
        domain.parse_date(ds)
        d = self.store.day(ds)
        fn(d)
        self.store.save_day(d)
        self.changed = True
        return _day_view(self.store, ds)

    # ---------- handlers ----------

    async def t_get_day(self, a):
        return _day_view(self.store, a["date"])

    async def t_add_meals(self, a):
        def f(d):
            for it in a["items"]:
                o = {"n": it["name"], "p": float(it["protein"]), "f": float(it["fat"]), "c": float(it["carbs"])}
                if it.get("kcal") is not None:
                    o["k"] = float(it["kcal"])
                d["items"].append(o)
        v = self._edit(a["date"], f)
        return {"totals": v["totals"], "left": v["left"], "target": v["target"], "plan_changes": self.rebalance(a["date"])}

    async def t_remove_meal(self, a):
        def f(d):
            if not 0 <= a["index"] < len(d["items"]):
                raise ValueError("no meal at that index")
            d["items"].pop(a["index"])
        v = self._edit(a["date"], f)
        return {"totals": v["totals"], "left": v["left"], "plan_changes": self.rebalance(a["date"])}

    async def t_skip_planned_meal(self, a):
        sched = self.store.schedule()

        def f(d):
            plan = domain.day_plan(sched, d)
            hit = next((x for x in plan if x["n"] == a["item"] and x["kind"] == "meal"), None)
            if hit is None:
                raise ValueError(f"no planned meal named {a['item']!r}; meals: {[x['n'] for x in plan if x['kind'] == 'meal']}")
            domain.mark_skipped(hit, a["reason"])
            d["plan"] = plan
        self._edit(a["date"], f)
        return {"ok": True, "plan_changes": self.rebalance(a["date"])}

    async def t_set_day_program(self, a):
        ds = a["date"]
        d0 = domain.parse_date(ds)
        typ = a["type"]
        start = domain.minutes(a["start"]) if a.get("start") else None
        end = domain.minutes(a["end"]) if a.get("end") else None
        if a.get("start") and start is None or a.get("end") and end is None:
            raise ValueError("start and end must be HH:MM")
        if typ == "train" and a.get("cycle_day") not in (1, 2, 3, 5, 6):
            raise ValueError("a training day needs cycle_day 1, 2, 3, 5 or 6")
        duration = (end - start) if start is not None and end is not None else None
        if duration is not None and duration <= 0:
            raise ValueError("end must be after start")
        items, plans = self.store.library()
        days = self.store.days()

        def f(d):
            w = d["workout"]
            kept = [e for e in w["exercises"] if domain.done_sets(e)]
            if typ == "train":
                d["type"], d["cycleDay"] = "train", int(a["cycle_day"])
                w["skipped"] = w["fromPlan"] = False
                fresh = domain.workout_from_plan(days, items, plans, d["cycleDay"], ds, False)
                w["exercises"] = kept + [e for e in fresh if (e["name"], e.get("ss")) not in {(k["name"], k.get("ss")) for k in kept}]
            else:
                d["type"] = "rest"
                d["cycleDay"] = 7 if domain.planned_cycle(d0) == 7 else 4
                w["exercises"] = kept
        self._edit(ds, f)
        result = await planner.plan_day(self.store, self.cal, d0, start_min=start, duration=duration, create_gym=(typ == "train"))
        out = {"date": ds, "type": result["type"], "cycleDay": result["cycleDay"], "name": domain.CYCLE[result["cycleDay"]],
               "workout_start": domain.hm(result["start"]) if result["start"] is not None else None,
               "gym_event_created": result["created"], "gym_event_moved": result["moved"],
               "plan": [f"{x['t']} {x['n']}" for x in result["plan"]]}
        if typ == "rest" and self.cal and self.cal.enabled:
            ev = await asyncio.to_thread(self.cal.events_on, d0)
            g = self.cal.gym_event(ev)
            if g is not None:
                out["gym_event_left"] = g["start"].strftime("%H:%M") + " (ask the user whether to delete it)"
        return out

    async def t_delete_gym_event(self, a):
        d0 = domain.parse_date(a["date"])
        ev = await asyncio.to_thread(self.cal.events_on, d0)
        g = self.cal.gym_event(ev)
        if g is None:
            raise ValueError("no gym event on that date")
        await asyncio.to_thread(self.cal.delete_event, g["id"])
        return {"deleted": True, "date": a["date"], "was": g["start"].strftime("%H:%M")}

    async def t_wolt_search(self, a):
        where = (a.get("where") or "").strip()
        src = None
        if where:
            g = await wolt.geocode(where)
            if g is None:
                raise ValueError(f"couldn't find {where!r} on the map; ask the user for a street and city")
            lat, lon, src = g
        else:
            last = json.loads(self.store.get_meta("last_location") or "{}")
            if last and time.time() - last.get("ts", 0) < 3 * 3600:
                lat, lon, src = last["lat"], last["lon"], "The user's shared location"
            else:
                g = await wolt.geocode(HOME_ADDRESS) if HOME_ADDRESS else None
                if g is None:
                    raise ValueError("no recent shared location and no home address; ask the user to share a location or name a place")
                lat, lon, _ = g
                src = "home (no recent shared location; ask the user to share their location if they are elsewhere)"
        venues = await wolt.search(lat, lon, a.get("query") or "", int(a.get("max_minutes") or 0))
        return {"from": src, "venues": venues, "note": "Wolt data; only recommend, never order"}

    async def t_wolt_menu(self, a):
        return await wolt.menu(a["slug"].strip())

    async def t_set_at_home(self, a):
        ds, at_home = a["date"], bool(a["at_home"])
        sched = self.store.schedule()

        def f(d):
            d["atHome"] = at_home
            if at_home:
                plan = d.get("plan") or [dict(x) for x in domain.day_plan(sched, d)]
                domain.reconcile_away(plan, [], 0, sched[d["type"]])
                d["plan"] = plan
        self._edit(ds, f)
        changes = self.rebalance(ds)
        if not at_home and self.cal:
            now = domain.now()
            await planner.refresh_today(self.store, self.cal, domain.parse_date(ds), now.hour * 60 + now.minute)
        return {"ok": True, "at_home": at_home, "plan_changes": changes,
                "plan": [f"{x['t']} {x['n']}: {x['d']}" for x in domain.day_plan(self.store.schedule(), self.store.day(ds)) if x["kind"] == "meal"]}

    async def t_replace_planned_meal(self, a):
        sched = self.store.schedule()

        def f(d):
            plan = domain.day_plan(sched, d)
            hit = next((x for x in plan if x["n"] == a["item"]), None)
            if hit is None:
                raise ValueError(f"no plan item named {a['item']!r}; items: {[x['n'] for x in plan]}")
            for k in ("base", "auto", "k"):
                hit.pop(k, None)
            hit.update(kind="meal", d=a["description"].strip(), p=round(float(a["p"]), 1),
                       f=round(float(a["f"]), 1), c=round(float(a["c"]), 1), pinned=True)
            if a.get("time"):
                if domain.minutes(a["time"]) is None:
                    raise ValueError("time must be HH:MM")
                hit["t"] = a["time"]
            plan.sort(key=lambda x: domain.minutes(x["t"]) if domain.minutes(x["t"]) is not None else 9999)
            d["plan"] = plan
        self._edit(a["date"], f)
        return {"ok": True, "plan_changes": self.rebalance(a["date"])}

    def rebalance(self, ds: str) -> list[str]:
        """Re-fit today's remaining planned meals to the targets; returns the changes made."""
        now = domain.now()
        if ds != now.date().isoformat():
            return []
        d = self.store.day(ds)
        before = domain.day_plan(self.store.schedule(), d)
        plan, changes = domain.rebalance(d, before, now.hour * 60 + now.minute)
        if plan != before:  # also when earlier top-ups were only taken back
            d["plan"] = plan
            self.store.save_day(d)
        return changes

    async def t_set_weight(self, a):
        self._edit(a["date"], lambda d: d.__setitem__("weight", round(float(a["kg"]), 1)))
        return {"ok": True, "weight": round(float(a["kg"]), 1)}

    async def t_set_creatine(self, a):
        v = self._edit(a["date"], lambda d: d.__setitem__("cr", [bool(a["taken"]), False]))
        return {"creatine_5g_taken": v["creatine_5g_taken"]}

    async def t_load_workout_plan(self, a):
        ds = a["date"]
        items, plans = self.store.library()
        days = self.store.days()

        def f(d):
            cd = d["cycleDay"] or domain.planned_cycle(domain.parse_date(ds))
            have = {}
            for e in d["workout"]["exercises"]:
                k = (e["name"], e.get("ss"))
                have[k] = have.get(k, 0) + 1
            for e in domain.workout_from_plan(days, items, plans, cd, ds, bool(a.get("with_reps"))):
                k = (e["name"], e.get("ss"))
                if have.get(k):
                    have[k] -= 1
                    continue
                d["workout"]["exercises"].append(e)
        v = self._edit(ds, f)
        return {"workout": v["workout"]}

    async def t_log_exercise(self, a):
        items, _ = self.store.library()
        ss = (a.get("superset") or "").strip().upper() or None
        sets = [{"w": float(s["w"]), "r": int(s["r"])} for s in a["sets"]]

        def f(d):
            d["workout"]["skipped"] = False  # they reported sets, so they trained
            exs = d["workout"]["exercises"]
            same = [e for e in exs if e["name"] == a["name"] and (ss is None or e.get("ss") == ss)]
            target = next((e for e in same if not domain.done_sets(e)), same[0] if same else None)
            if target is None:
                L = domain.lib_for(items, a["name"])
                target = {"name": a["name"], "muscle": L["muscle"] if L else "other", "sets": []}
                if ss:
                    target["ss"] = ss
                exs.append(target)
            target["sets"] = sets
        v = self._edit(a["date"], f)
        days = self.store.days()
        hist = domain.history(days, a["name"], a["date"])
        prev = hist[0] if hist else None
        best_prev = domain.best_set(hist)
        top = max((x for x in sets if x["r"] > 0), key=domain.e1rm, default=None)
        pr = bool(best_prev and top and domain.e1rm(top) > domain.e1rm(best_prev))
        return {"ok": True, "previous": prev, "personal_record": pr,
                "best_set_today": top, "previous_best_set": best_prev,
                "sets_today": sum(len(domain.done_sets(e)) for e in v["workout"]["exercises"])}

    async def t_remove_exercise(self, a):
        ss = (a.get("superset") or "").strip().upper() or None
        removed = {}

        def f(d):
            exs = d["workout"]["exercises"]
            i = domain.find_exercise(exs, a["name"], ss)
            if i is None:
                rows = [f"{e['name']} ({e.get('ss') or '-'})" for e in exs]
                raise ValueError(f"no row {a['name']!r} with superset {ss or 'any'}; rows: {rows}")
            removed.update(exs.pop(i))
        v = self._edit(a["date"], f)
        return {"removed": {"name": removed["name"], "superset": removed.get("ss"), "sets": removed["sets"]},
                "rows_left": len(v["workout"]["exercises"])}

    async def t_set_workout_done(self, a):
        removed = []

        def f(d):
            d["workout"]["done"] = bool(a["done"])
            if a["done"]:  # confirmed by the user
                d["workout"]["skipped"] = d["workout"]["fromPlan"] = False
                removed.append(domain.tidy_done_workout(d["workout"]))
        self._edit(a["date"], f)
        return {"ok": True, "empty_rows_removed": removed[0] if removed else 0}

    async def t_add_note(self, a):
        def f(d):
            if a["target"] == "workout":
                d["workout"]["notes"] = (d["workout"]["notes"] + "\n" if d["workout"]["notes"] else "") + a["text"]
            else:
                d["notes"] = (d["notes"] + "\n" if d["notes"] else "") + a["text"]
        self._edit(a["date"], f)
        return {"ok": True}

    async def t_close_day(self, a):
        v = self._edit(a["date"], lambda d: d.__setitem__("closed", True))
        return {"closed": True, "checklist": v["checklist"]}

    async def t_get_exercise_history(self, a):
        items, _ = self.store.library()
        hist = domain.history(self.store.days(), a["name"], "9999-12-31")[: a.get("limit") or 5]
        L = domain.lib_for(items, a["name"])
        sg = domain.suggest(a["name"], L["muscle"] if L else "other", L, hist[0] if hist else None)
        return {"history": hist, "range": f"{L['sets']}x{L['lo']}-{L['hi']}" if L else None, "next": sg}

    async def t_get_plan(self, a):
        ds = a["date"]
        d = domain.parse_date(ds)
        day = self.store.day(ds)
        cd = domain.cycle_day(self.store.data(f"log/{ds}"), d)
        items, plans = self.store.library()
        events = []
        if self.cal.enabled:
            try:
                import asyncio
                events = [{"title": e["title"], "start": e["start"].strftime("%H:%M") if e["start"] else "כל היום",
                           "end": e["end"].strftime("%H:%M") if e["end"] else ""}
                          for e in await asyncio.to_thread(self.cal.events_on, d)]
            except Exception as e:  # report, don't fail the tool
                events = [{"error": f"calendar: {e}"}]
        return {
            "date": ds, "label": domain.day_label(d), "cycleDay": cd, "name": domain.CYCLE[cd], "type": day["type"],
            "plan": domain.day_plan(self.store.schedule(), day),
            "workout": domain.workout_lines(self.store.days(), items, plans, cd, ds) if day["type"] == "train" else [],
            "cooking": domain.PREP.get(d.weekday()), "calendar": events,
        }

    async def t_move_workout(self, a):
        d = domain.parse_date(a["date"])
        start = domain.minutes(a["start"]) if a.get("start") else None
        if a.get("start") and start is None:
            raise ValueError("start must be HH:MM")
        res = await planner.plan_day(self.store, self.cal, d, start_min=start)
        self.changed = True
        return {"start": domain.hm(res["start"]) if res["start"] is not None else None, "calendar_moved": res["moved"],
                "no_free_slot": res["conflict"], "plan": res["plan"]}

    async def t_set_plan_time(self, a):
        t = domain.minutes(a["time"])
        if t is None:
            raise ValueError("time must be HH:MM")
        sched = self.store.schedule()

        def f(d):
            plan = domain.day_plan(sched, d)
            hit = next((x for x in plan if x["n"] == a["item"]), None)
            if hit is None:
                raise ValueError(f"no plan item named {a['item']!r}; items: {[x['n'] for x in plan]}")
            hit["t"] = domain.hm(t)
            hit["pinned"] = True
            plan.sort(key=lambda x: domain.minutes(x["t"]) or 9999)
            d["plan"] = plan
        v = self._edit(a["date"], f)
        return {"ok": True, "plan": domain.day_plan(sched, self.store.day(v["date"]))}

    async def t_get_drive_time(self, a):
        depart = (a.get("depart_at") or "").strip().replace(" ", "T") or None
        r = await eta.eta(a["destination"].strip(), "me", depart)
        if "error" in r:
            why = {0: "the Waze service is not reachable", 409: "no recent location from the user's phone (they need to share live location)",
                   422: "Waze found no place by that name", 400: "bad depart_at"}.get(r["status"], "Waze failed")
            raise ValueError(f"no drive time: {why} ({r['error']}). Tell the user; don't guess.")
        out = {k: r.get(k) for k in ("minutes", "km", "route", "depart_at", "arrive_at")}
        loc = r.get("from_location") or {}
        out["from_location"] = loc
        if (loc.get("age_min") or 0) > eta.STALE_MIN:
            out["note"] = f"The user's location is {loc['age_min']} min old; say so."
        return out

    async def t_set_travel_time(self, a):
        cur = self.store.travel()
        place, mins = a["place"].strip(), int(a["minutes"])
        if place.lower() == "default":
            cur["default"] = mins
        else:
            cur["places"] = [p for p in cur["places"] if p["match"].lower() != place.lower()] + [{"match": place, "minutes": mins}]
        self.store.set("config/travel", cur)
        self.changed = True
        retimed = {}
        for d in (domain.today(), domain.today() + dt.timedelta(days=1)):
            if self.store.day(d.isoformat()).get("plan"):
                retimed[d.isoformat()] = await planner.retime_away(self.store, self.cal, d)
        return {"saved": True, "travel": cur, "plan_leave_times": retimed}

    async def t_skip_workout(self, a):
        def f(d):
            w = d["workout"]
            w["exercises"] = [e for e in w["exercises"] if domain.done_sets(e) and not w.get("fromPlan")]
            w["skipped"], w["done"], w["fromPlan"] = True, False, False
            line = f"אין אימון היום: {a['reason'].strip()}"
            w["notes"] = (w["notes"] + "\n" if w["notes"] else "") + line
        d = self._edit(a["date"], f)
        return {"ok": True, "skipped": True, "kept_exercises": [e["name"] for e in d["workout"]["exercises"]]}

    async def t_set_reminder(self, a):
        self.changed = True
        return alarms.add(self.store, a["datetime"], a["text"], a.get("repeat") or "none")

    async def t_list_reminders(self, a):
        return {"reminders": alarms.upcoming(self.store)}

    async def t_cancel_reminder(self, a):
        self.changed = True
        return alarms.cancel(self.store, int(a["id"]))

    async def t_set_targets(self, a):
        typ = a["day_type"]
        cur = self._program_doc()
        new = dict(cur["targets"][typ])
        for arg, k in (("kcal", "k"), ("protein", "p"), ("fat", "f"), ("carbs", "c")):
            if a.get(arg) is not None:
                if not 0 < float(a[arg]) < 10000:
                    raise ValueError(f"{arg} out of range")
                new[k] = float(a[arg])
        macro_k = new["p"] * 4 + new["f"] * 9 + new["c"] * 4
        preview = {"day_type": typ, "before": cur["targets"][typ], "after": new,
                   "kcal_from_macros": round(macro_k), "fat_floor_65_ok": new["f"] >= 65}
        if not self._gate("set_targets", a):
            return {"preview": preview, "needs_ben_yes": True}
        cur["targets"][typ] = new
        self.store.set("config/program", cur)
        self.store.program()
        self.changed = True
        self._note(f"יעדי יום {'אימון' if typ == 'train' else 'מנוחה'}: {new['k']:g} קק״ל, {new['p']:g} חלבון, {new['f']:g} שומן, {new['c']:g} פחמימות")
        return {"applied": True, **preview}

    def onboarding_targets(self, targets: dict, confirm: bool) -> dict:
        """Both day types at once, from the questionnaire. The same preview-then-yes as set_targets."""
        if not self._gate("onboarding_targets", {"targets": targets, "confirm": confirm}):
            return {"preview": targets, "needs_ben_yes": True}
        cur = self._program_doc()
        cur["targets"].update({typ: {k: float(v) for k, v in targets[typ].items()} for typ in ("train", "rest")})
        self.store.set("config/program", cur)
        self.store.program()
        self.changed = True
        self._note("יעדים מהשאלון: " + "; ".join(
            f"{'אימון' if typ == 'train' else 'מנוחה'} {t['k']:g} קק״ל, {t['p']:g} חלבון, {t['f']:g} שומן, {t['c']:g} פחמימות"
            for typ, t in targets.items()))
        return {"applied": True}

    async def t_still_day(self, a):
        ds = a["date"]
        now = domain.now()
        if a.get("undo"):
            def clear(d):
                d["targetAdj"] = None
                d["notes"] = "\n".join(n for n in d["notes"].split("\n") if not n.startswith(movement.NOTE))
            self._edit(ds, clear)
            return {"undone": True, "target": domain.day_target(self.store.day(ds)), "plan_changes": self.rebalance(ds)}
        if ds != now.date().isoformat():
            raise ValueError("only for today")
        d = self.store.day(ds)
        if d["type"] != "rest":
            raise ValueError("this is a training day: ask the user whether they trained; if not, make it a rest day with set_day_program first")
        if d["closed"]:
            raise ValueError("the day is closed")
        if d.get("targetAdj"):
            return {"already_applied": d["targetAdj"], "target": domain.day_target(d)}
        k = movement.still_kcal(movement.latest_weight(self.store.days()))
        meal = domain.trim_candidate(d, domain.day_plan(self.store.schedule(), d), now.hour * 60 + now.minute)
        if meal is None:
            return {"nothing_left": True, "note": "no carb meal left in today's plan, so there is nothing to change"}
        c = min(round(k / 4), int(meal["c"]))  # never promise more than that meal has
        k = min(k, c * 4)
        before = domain.TARGETS["rest"]["k"]
        preview = {"kcal": k, "carbs": c, "before": before, "after": before - k, "meal": meal["n"],
                   "protein_and_fat": "unchanged", "alternative": f"{movement.WALK_MIN} minutes of walking, food unchanged"}
        if not self._gate("still_day", {"date": ds, "kcal": k, "meal": meal["n"], "confirm": a.get("confirm")}):
            return {"preview": preview, "needs_ben_yes": True}

        def f(x):
            x["targetAdj"] = {"k": -k, "c": -c, "why": "לא זז מהמקום", "meal": meal["n"]}
            x["notes"] = (x["notes"] + "\n" if x["notes"] else "") + f"{movement.NOTE} היעד ירד היום ב-{k} קק״ל ({c} ג׳ פחמימות)."
        self._edit(ds, f)
        self.rebalance(ds)
        return {"applied": True, **preview, "target": domain.day_target(self.store.day(ds))}

    def plan_walk(self, ds: str) -> Optional[str]:
        """The user chose the walk: a note on the day and a reminder at the next half hour, if there is one today.
        Returns the reminder's time."""
        now = domain.now()
        m = ((now.hour * 60 + now.minute + 15 + 29) // 30) * 30
        at = domain.hm(m) if m < 24 * 60 and ds == now.date().isoformat() else None
        if at:
            alarms.add(self.store, f"{ds} {at}", f"{movement.WALK_MIN} דקות הליכה, בקצב נוח.", "none")

        def f(d):
            d["notes"] = (d["notes"] + "\n" if d["notes"] else "") + f"{movement.NOTE} בחר {movement.WALK_MIN} דקות הליכה, בלי שינוי באוכל."
        self._edit(ds, f)
        return at

    async def t_set_week(self, a):
        cur = self._program_doc()
        days = {k: int(v) for k, v in (a.get("days") or {}).items()}
        bad = [k for k in days if k not in domain.WEEK_KEYS or not 1 <= days[k] <= 7]
        if bad or not days:
            raise ValueError(f"days must map {domain.WEEK_KEYS} to 1-7; bad: {bad}")
        week = {**cur["week"], **days}
        name = lambda w: {k: f"{n} {domain.CYCLE[n]}" for k, n in w.items()}
        # days already saved with the old program keep it until set_day_program changes them
        today = domain.today()
        saved = []
        for i in range(1, 8):
            d = today + dt.timedelta(days=i)
            doc = self.store.data(f"log/{d.isoformat()}")
            key = domain.WEEK_KEYS[(d.weekday() + 1) % 7]
            if doc and doc.get("cycleDay") and doc["cycleDay"] != week[key] and key in days:
                saved.append(f"{d.isoformat()} ({key}) is saved as day {doc['cycleDay']}")
        preview = {"before": name(cur["week"]), "after": name(week), "already_planned_days": saved}
        if not self._gate("set_week", a):
            return {"preview": preview, "needs_ben_yes": True}
        cur["week"] = week
        self.store.set("config/program", cur)
        self.store.program()
        self.changed = True
        self._note("ימי האימון בשבוע: " + ", ".join(f"{k} {domain.CYCLE[n]}" for k, n in week.items()))
        return {"applied": True, **preview,
                "next": "already planned days keep their old program; call set_day_program for them if the user wants"}

    async def t_update_exercise_plan(self, a):
        day = int(a["program_day"])
        raw = self.store.data("config/exercises") or {}
        items, plans = domain.norm_library(raw)
        items = [dict(x) for x in items]
        by_name = {x["name"]: x for x in items}
        new_items, rows = [], []
        for e in a["exercises"]:
            name = e["name"].strip()
            L = by_name.get(name)
            if L is None:
                if not e.get("muscle"):
                    raise ValueError(f"new exercise {name!r} needs a muscle")
                L = {"id": "x" + hashlib.sha1(name.encode()).hexdigest()[:8], "name": name, "muscle": e["muscle"],
                     "days": [], "sets": int(e.get("sets") or 3), "lo": int(e.get("lo") or 8), "hi": int(e.get("hi") or 12)}
                items.append(L)
                by_name[name] = L
                new_items.append(name)
            for k in ("sets", "lo", "hi"):
                if e.get(k):
                    L[k] = int(e[k])
            if L["lo"] > L["hi"]:
                raise ValueError(f"{name}: lo above hi")
            ss = (e.get("superset") or "").strip().upper() or None
            rows.append({"name": name, "ss": ss} if ss else {"name": name})
        before = [(r.get("ss") or "-") + ":" + r["name"] for r in plans.get(day, [])]
        plans = {**plans, day: [{"name": r["name"], "ss": r.get("ss")} for r in rows]}
        for x in items:  # an exercise's days follow the plans
            x["days"] = sorted(n for n, arr in plans.items() if any(r["name"] == x["name"] for r in arr))
        weekly: dict[str, int] = {}
        for n, arr in plans.items():
            if n in domain.WEEKDAY_DAY.values():
                for r in arr:
                    L = by_name.get(r["name"])
                    if L:
                        weekly[L["muscle"]] = weekly.get(L["muscle"], 0) + L["sets"]
        preview = {"program_day": f"{day} {domain.CYCLE[day]}", "before": before,
                   "after": [(r.get("ss") or "-") + ":" + r["name"] for r in rows], "new_library_items": new_items,
                   "weekly_direct_sets_after": weekly}
        if not self._gate("update_exercise_plan", a):
            return {"preview": preview, "needs_ben_yes": True}
        out_plans = {str(n): [{"name": r["name"], **({"ss": r["ss"]} if r.get("ss") else {})} for r in arr] for n, arr in plans.items()}
        self.store.set("config/exercises", {"items": items, "plans": out_plans})
        self.changed = True
        self._note(f"תוכנית יום {day} ({domain.CYCLE[day]}): " + " · ".join(preview["after"]))
        return {"applied": True, **preview}

    async def t_update_meal_template(self, a):
        typ = a["day_type"]
        entries = []
        for e in a["entries"]:
            if domain.minutes(e["t"]) is None:
                raise ValueError(f"bad time {e['t']!r}")
            o = {"t": e["t"], "n": e["n"].strip(), "d": (e.get("d") or "").strip(), "kind": e["kind"]}
            if e["kind"] == "meal":
                if any(e.get(k) is None for k in ("p", "f", "c")):
                    raise ValueError(f"meal {e['n']!r} needs p, f and c")
                o.update(p=float(e["p"]), f=float(e["f"]), c=float(e["c"]))
                if e.get("k") is not None:
                    o["k"] = float(e["k"])
            entries.append(o)
        entries.sort(key=lambda x: domain.minutes(x["t"]))
        meals = [x for x in entries if x["kind"] == "meal"]
        tot = {"k": round(sum(domain.kcal(x) for x in meals)), "p": round(sum(x["p"] for x in meals)),
               "f": round(sum(x["f"] for x in meals)), "c": round(sum(x["c"] for x in meals))}
        cur = self.store.schedule()
        preview = {"day_type": typ, "entries": [f"{x['t']} {x['n']}" + (f" ({domain.kcal(x):.0f} קק״ל, {x['p']:g} ח׳)" if x['kind'] == 'meal' else "") for x in entries],
                   "totals": tot, "targets": domain.TARGETS[typ],
                   "before_totals": {"k": round(sum(domain.kcal(x) for x in cur[typ] if "p" in x))}}
        if not self._gate("update_meal_template", a):
            return {"preview": preview, "needs_ben_yes": True}
        self.store.set("config/schedule", {**cur, typ: entries})
        self.changed = True
        self._note(f"תבנית ארוחות ליום {'אימון' if typ == 'train' else 'מנוחה'}: " + ", ".join(f"{x['t']} {x['n']}" for x in entries)
                   + f" ({tot['k']} קק״ל, {tot['p']} ג׳ חלבון)")
        return {"applied": True, **preview, "note": "days already planned keep their plan; the template applies from the next planning"}

    async def t_remember(self, a):
        notes = self.store.profile_notes()
        notes.append({"text": a["fact"].strip(), "date": domain.today().isoformat(), "source": "bot"})
        self.store.set_profile_notes(notes)
        return {"saved": True, "count": len(notes)}

    async def t_forget(self, a):
        notes = self.store.profile_notes()
        if not 0 <= a["index"] < len(notes):
            raise ValueError("no note at that index")
        removed = notes.pop(a["index"])
        self.store.set_profile_notes(notes)
        return {"removed": removed["text"]}

    async def t_week_summary(self, a):
        return week_summary(self.store)


def week_summary(store: Store) -> dict:
    days = store.days()
    t = domain.today()

    def window(a: int, b: int) -> list[dict]:
        lo, hi = (t - dt.timedelta(days=a)).isoformat(), (t - dt.timedelta(days=b)).isoformat()
        return [d for k, d in days.items() if lo <= k <= hi]

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    this, prev = window(6, 0), window(13, 7)
    w_this = avg([d["weight"] for d in this if d["weight"] is not None])
    w_prev = avg([d["weight"] for d in prev if d["weight"] is not None])
    fed = [d for d in this if d["items"]]
    return {
        "weight_avg": w_this, "weight_avg_prev": w_prev,
        "change": round(w_this - w_prev, 2) if w_this is not None and w_prev is not None else None,
        "weigh_ins": sum(1 for d in this if d["weight"] is not None),
        "kcal_avg": avg([domain.totals(d)["k"] for d in fed]), "protein_avg": avg([domain.totals(d)["p"] for d in fed]),
        "workouts": sum(1 for d in this if domain.has_workout(d)),
        "sets_last_6_days": domain.volume(days, t), "leg_targets": domain.VOL_TARGET,
    }
