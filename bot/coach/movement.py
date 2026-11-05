"""Did the user leave their place today? Only from their Telegram live location, and only while they share it.

What this can and can't say:
- It knows the distance from the day's first point, nothing else. It doesn't see walking inside a home or an
  office or around the block within the radius, a treadmill, or a phone left at home; a car ride looks like moving
  and burns almost nothing. (Summing a path was tried and dropped: indoor GPS jitter added up to kilometres.)
- So "still" means a small, one-day estimate of calories burned, never anything about fat, and nothing
  changes without the user's yes (the still_day tool, through ToolRunner._gate).
- No coverage means "unknown", and an unknown day says nothing.

Stored: move_state holds today's coordinates, stays out of the export (store.PRIVATE_META) and is deleted
once the day ends; move:<date> keeps that day's summary with distances only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from typing import Optional

from . import domain

CHECK_AT = 17 * 60          # rest days: one check before dinner
LEAVE_M = 300               # further than this from the day's first point (plus GPS accuracy) means they left
FAR_FIXES = 2               # one far point can be a GPS glitch
MAX_ACCURACY_M = 100        # worse fixes say nothing about distance
FIRST_FIX_BY = 11 * 60      # sharing has to start by then for the day to count
MIN_FIXES = 3
MAX_GAP_MIN = 180           # a longer silence means we don't know what happened in it (to tune with real data)
COVER_GRACE_MIN = 60        # sharing may end this long before the check
STATE = "move_state"
NOTE = "יום בלי תנועה:"      # starts the line on the day's notes

KCAL_PER_KG = 2.0           # 4 km less walking x 0.5 kcal/kg/km net (ACSM walking equation)
KCAL_MIN, KCAL_MAX = 100, 150
WALK_MIN = 30


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def _local(ts: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, domain.TZ)


def _hm(ts: Optional[float]) -> str:
    return _local(ts).strftime("%H:%M") if ts else "?"


def add_fix(state: Optional[dict], lat: float, lon: float, ts: float, accuracy: Optional[float] = None,
            live_until: Optional[float] = None, ended: bool = False) -> dict:
    """One location update on the day's state. Pure; a new day starts a new state.

    first, last, n and max_gap_min are the coverage: only live, accurate updates count. A one-off pin can show
    they moved, but never that they stayed. An inaccurate update says nothing except that sharing is still on."""
    day = _local(ts).date().isoformat()
    if not state or state["date"] != day:
        state = {"date": day, "first": None, "last": None, "n": 0, "far_n": 0, "max_m": 0.0,
                 "max_gap_min": 0.0, "until": None, "ended": None}
    state = dict(state)
    if live_until:
        state["until"], state["ended"] = live_until, None  # a live update means it hasn't ended
    if ended:
        state["ended"] = ts
    if accuracy is not None and accuracy > MAX_ACCURACY_M:
        return state
    if live_until:
        if state["last"] is not None:
            state["max_gap_min"] = max(state["max_gap_min"], round((ts - state["last"]) / 60, 1))
        state["first"] = state["first"] or ts
        state["last"] = max(state["last"] or ts, ts)
        state["n"] += 1
    if "lat0" not in state:
        state.update(lat0=lat, lon0=lon)
        return state
    away = distance_m(state["lat0"], state["lon0"], lat, lon)
    state["max_m"] = round(max(state["max_m"], away))
    if away > LEAVE_M + (accuracy or 0):
        state["far_n"] += 1
    return state


def status(state: Optional[dict], now_ts: float) -> str:
    """moved, still or unknown. Moving is proof enough; "still" needs sharing from the morning until now."""
    if not state or _local(now_ts).date().isoformat() != state["date"]:
        return "unknown"
    if state["far_n"] >= FAR_FIXES:
        return "moved"
    if not state["first"] or state["n"] < MIN_FIXES or not state["until"]:
        return "unknown"
    first = _local(state["first"])
    if first.hour * 60 + first.minute > FIRST_FIX_BY:
        return "unknown"
    gap = (now_ts - state["last"]) / 60
    if max(state["max_gap_min"], gap) > MAX_GAP_MIN:
        return "unknown"  # a long silence: maybe sharing stopped
    covered_until = min(state["until"], state["ended"] or state["until"])
    if now_ts - covered_until > COVER_GRACE_MIN * 60:
        return "unknown"
    return "still"


def state(store) -> Optional[dict]:
    return json.loads(store.get_meta(STATE) or "null")


def record(store, lat: float, lon: float, ts: float, accuracy: Optional[float] = None,
           live_until: Optional[float] = None, ended: bool = False) -> dict:
    cur = state(store)
    if cur and cur["date"] != _local(ts).date().isoformat():
        close(store, cur)
    new = add_fix(cur, lat, lon, ts, accuracy, live_until, ended)
    store.set_meta(STATE, json.dumps(new))
    return new


def summary(s: dict, st: str) -> dict:
    """The day without coordinates: this is what stays and goes to the backup."""
    return {"date": s["date"], "status": st, "first": _hm(s["first"]), "last": _hm(s["last"]),
            "fixes": s["n"], "max_m": s["max_m"], "max_gap_min": s["max_gap_min"]}


def close(store, s: dict) -> None:
    """Keep the day's summary and forget where they were."""
    end = dt.datetime.combine(domain.parse_date(s["date"]), dt.time(23, 59), domain.TZ).timestamp()
    store.set_meta(f"move:{s['date']}", json.dumps(summary(s, status(s, min(end, (s["last"] or end) + 60)))))
    store.del_meta(STATE)


def roll_over(store, today: dt.date) -> None:
    """Every minute: yesterday's coordinates go at midnight even if no new location arrives."""
    s = state(store)
    if s and s["date"] < today.isoformat():
        close(store, s)


def context_line(store, now_ts: float) -> str:
    s = state(store)
    st = status(s, now_ts)
    if st == "unknown":
        return "Movement today: unknown (no full live location). Don't guess; the user can say they stayed in."
    return (f"Movement today (live location, {_hm(s['first'])}-{_hm(s['last'])}): {st}, "
            f"farthest {s['max_m']:.0f} m. It can't see movement inside a home or office, and it says nothing about fat.")


def still_kcal(weight_kg: Optional[float]) -> int:
    """About 4 km less walking than a normal rest day, capped: the formulas' own error is bigger than this."""
    k = KCAL_PER_KG * (weight_kg or 70)
    return int(max(KCAL_MIN, min(KCAL_MAX, round(k / 5) * 5)))


def latest_weight(days: dict[str, dict]) -> Optional[float]:
    return next((d["weight"] for _, d in sorted(days.items(), reverse=True) if d["weight"] is not None), None)


def proposal_text(s: Optional[dict], preview: dict, week_change: Optional[float], goal: Optional[str]) -> str:
    """The 17:00 message. Two options; they pick. Nothing has changed yet."""
    since = f" מ-{_hm(s['first'])} ועד עכשיו" if s and s.get("first") else ""
    k, c = preview["kcal"], preview["carbs"]
    lines = [f"לפי המיקום החי לא התרחקת היום מהמקום שהיית בו{since}.",
             f"זו הערכה: ביום כזה נשרפות בערך {k} קק״ל פחות מיום מנוחה רגיל. הליכה בתוך הבית או במשרד אני לא רואה.",
             "", "יש שתי אפשרויות:",
             f"🚶 {WALK_MIN} דקות הליכה, והאוכל נשאר כמו שהוא.",
             f"🍚 {k} קק״ל פחות היום: {c} ג׳ פחמימות פחות"
             + (f" ב{preview['meal']}" if preview.get("meal") else "")
             + f". היעד יורד מ-{preview['before']:,.0f} ל-{preview['after']:,.0f} קק״ל, החלבון והשומן לא זזים. מחר חוזרים ליעד הרגיל."]
    if goal != "lose" and (week_change is None or week_change < 0.1):
        trend = "המשקל כמעט לא עלה השבוע" if week_change is not None else "אין עדיין מגמת משקל של שבועיים"
        lines += ["", f"ההמלצה שלי: הליכה. {trend}, והורדת אוכל הולכת נגד העלייה."]
    return "\n".join(lines)
