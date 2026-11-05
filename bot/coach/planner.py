"""Day planning: fit the workout around the calendar and write the day's plan."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Optional

from . import domain, eta
from .gcal import Calendar
from .store import Store

log = logging.getLogger(__name__)
WORKOUT_MIN = 90
TRAFFIC_SHIFT = 5  # minutes a re-check must move a departure by


async def plan_day(store: Store, cal: Calendar, d: dt.date, start_min: Optional[int] = None,
                   move_event: bool = True, duration: Optional[int] = None, create_gym: bool = False) -> dict:
    """Plan a date. Returns {cycleDay, type, start, plan, moved, created, conflict, events}.

    start_min forces a workout time; otherwise the first free slot from 09:00 is used.
    duration: workout minutes (default WORKOUT_MIN). create_gym: on a training day without a
    gym event, create one (the user asked for it).
    """
    dur = duration or WORKOUT_MIN
    ds = d.isoformat()
    day = store.day(ds)
    cd = domain.cycle_day(store.data(f"log/{ds}"), d)
    typ = domain.day_type(store.data(f"log/{ds}"), d)
    template = store.schedule()[typ]
    events, busy = [], []
    if cal.enabled:
        try:
            events = await asyncio.to_thread(cal.events_on, d)
            busy = cal.busy_minutes(events, d)
        except Exception:  # calendar trouble must not block planning
            log.exception("calendar read failed")
    preferred = domain.MORNING_START  # the user: always the morning, from 09:00
    conflict = False
    start = None
    if typ == "train":
        if start_min is not None:
            start = start_min
        else:
            start = domain.find_slot(busy, d, preferred=preferred, duration=dur)
            if start is None:
                start, conflict = preferred, True
    pins = [x for x in (day.get("plan") or []) if x.get("pinned")]
    travel = await live_travel(store, events, d)
    away = [] if day.get("atHome") else domain.away_windows(events, d, travel)
    plan = domain.build_plan(template, start, dur, busy, away, pins)
    day["plan"] = plan
    day["cycleDay"] = cd
    day["type"] = typ
    store.save_day(day)

    moved = created = False
    if create_gym and typ == "train" and cal.enabled and cal.gym_event(events) is None:
        try:
            events.append(await asyncio.to_thread(cal.create_gym, d, start, dur, f"Day {cd} – {domain.CYCLE[cd]}"))
            created = True
        except Exception:
            log.exception("creating the gym event failed")
    if move_event and typ == "train" and cal.enabled and events:
        gym = cal.gym_event(events)
        if gym is not None:
            cur = gym["start"].hour * 60 + gym["start"].minute
            items, plans = store.library()
            lines = domain.workout_lines(store.days(), items, plans, cd, ds)
            try:
                await asyncio.to_thread(cal.move_gym, gym, d, start, dur,
                                        f"Day {cd} – {domain.CYCLE[cd]}", lines)
                moved = cur != start
            except Exception:
                log.exception("calendar update failed")
    return {"cycleDay": cd, "type": typ, "start": start, "plan": plan, "moved": moved, "created": created,
            "conflict": conflict, "events": [e for e in events if not cal.is_gym(e)]}


async def live_travel(store: Store, events: list[dict], d: dt.date) -> dict:
    """config/travel with Waze's live minutes from the user's location merged in (they win)."""
    travel = store.travel()
    return {**travel, "live": await eta.live_travel(events, d, travel)}


async def refresh_today(store: Store, cal: Calendar, d: dt.date, now_min: int) -> dict:
    """Check the saved plan against the calendar as it is now, and fix what no longer holds.

    Returns {"read": calendar was read, "has_gym": a gym event exists, "changes": [...], "gym_moved": "HH:MM" or None}.
    Calendar trouble leaves everything as it is.
    """
    out = {"read": False, "has_gym": None, "changes": [], "gym_moved": None}
    if not cal or not cal.enabled:
        return out
    try:
        events = await asyncio.to_thread(cal.events_on, d)
    except Exception:
        log.exception("calendar read failed")
        return out
    out["read"] = True
    ds = d.isoformat()
    day = store.day(ds)
    gym = cal.gym_event(events)
    out["has_gym"] = gym is not None
    if day["closed"]:
        return out
    plan = day.get("plan") or [dict(x) for x in domain.day_plan(store.schedule(), day)]
    train = next((x for x in plan if x["kind"] == "train"), None)
    if gym is not None and train is not None and day["type"] == "train" and not day["workout"]["done"]:
        start = gym["start"].hour * 60 + gym["start"].minute
        cur = domain.minutes(train["t"])
        if cur is not None and abs(start - cur) >= 15 and start > now_min:  # the user moved the event themselves
            dur = max(15, int((gym["end"] - gym["start"]).total_seconds() // 60))
            await plan_day(store, cal, d, start_min=start, move_event=False, duration=dur)
            out["gym_moved"] = domain.hm(start)
            return out
    windows = [] if day.get("atHome") else domain.away_windows(events, d, store.travel())
    before = json.dumps(plan, sort_keys=True)
    out["changes"] = domain.reconcile_away(plan, windows, now_min, store.schedule()[day["type"]])
    if json.dumps(plan, sort_keys=True) != before:
        day["plan"] = plan
        store.save_day(day)
    return out


async def retime_away(store: Store, cal: Calendar, d: dt.date, min_shift: int = 0) -> dict[str, str]:
    """Recompute 'leave' times for the away meals in a saved day plan, after a travel time or the traffic changed.

    Only the departure times move (and the packing reminder with them); meals, skips and pins stay.
    A leave time moves only when it shifts by at least min_shift minutes. Returns {window title: new leave time}.
    """
    ds = d.isoformat()
    day = store.day(ds)
    plan = day.get("plan")
    if not plan or not cal.enabled:
        return {}
    try:
        events = await asyncio.to_thread(cal.events_on, d)
    except Exception:  # calendar trouble: keep the plan as it is
        log.exception("calendar read failed")
        return {}
    travel = await live_travel(store, events, d)
    out = domain.retime_away(plan, domain.away_windows(events, d, travel), min_shift)
    if out:
        store.save_day(day)
    return out


async def traffic_check(store: Store, cal: Calendar, d: dt.date, now_min: int) -> list[tuple[str, str]]:
    """About an hour before each leave time, ask Waze again once from where the user is now.

    Returns [(old leave, new leave)] for departures that moved by TRAFFIC_SHIFT minutes or more.
    """
    if not eta.enabled():
        return []
    ds = d.isoformat()
    plan = store.day(ds).get("plan") or []
    leaves = sorted({x["leave"] for x in plan if x.get("away") and x.get("leave")})
    due = [lv for lv in leaves if (m := domain.minutes(lv)) is not None and m - 60 <= now_min < m - 15]
    if not [lv for lv in due if store.mark_sent(f"{ds}:eta:{lv}")]:
        return []
    old = {x["away"]: x["leave"] for x in plan if x.get("away") and x.get("leave")}
    out = []
    for title, new in (await retime_away(store, cal, d, TRAFFIC_SHIFT)).items():
        if old.get(title) in due:
            store.mark_sent(f"{ds}:eta:{new}")  # checked just now; don't ask again for the new time
        if title in old:
            out.append((old[title], new))
    return sorted(set(out))
