"""Coaching rules shared by the bot, the reminders and the tracker API.

Mirrors the logic in tracker.html so the page and the bot agree on targets,
cycle days, the day-closing checklist and double progression.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .personal import GYM_HOURS, GYM_NAME, PREP, SHOP_FOR_WEEKDAY, SHOPPING  # noqa: F401

TZ = ZoneInfo("Asia/Jerusalem")

# Defaults; the live values are in config/program and are applied with apply_program().
TARGETS_DEFAULT = {
    "train": {"k": 2850, "p": 155, "f": 75, "c": 385},
    "rest": {"k": 2600, "p": 155, "f": 80, "c": 315},
}
TARGETS = {k: dict(v) for k, v in TARGETS_DEFAULT.items()}
ANCHOR = dt.date(2026, 10, 21)  # an older 6-day cycle, used only for days before WEEK_START
WEEK_START = dt.date(2026, 10, 28)  # from here the program follows the calendar week
CYCLE = {1: "דחיפה", 2: "משיכה", 3: "רגליים", 4: "מנוחה", 5: "פלג עליון", 6: "פלג תחתון", 7: "מנוחה"}
REST_DAYS = (4, 7)
# Python weekday() -> program day: Sun push, Mon pull, Tue legs, Wed rest, Thu upper, Fri lower, Sat rest
WEEKDAY_DEFAULT = {6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7}
WEEKDAY_DAY = dict(WEEKDAY_DEFAULT)
WEEK_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")  # config/program week; Python weekday() = (index - 1) % 7


def norm_program(v: Optional[dict]) -> dict:
    """config/program: {targets: {train|rest: {k,p,f,c}}, week: {sun..sat: program day 1-7}}."""
    v = v or {}
    targets = {}
    for typ, base in (("train", TARGETS_DEFAULT["train"]), ("rest", TARGETS_DEFAULT["rest"])):
        t = (v.get("targets") or {}).get(typ) or {}
        targets[typ] = {k: float(t[k]) if isinstance(t.get(k), (int, float)) and 0 < t[k] < 10000 else base[k]
                        for k in ("k", "p", "f", "c")}
    week = {}
    for i, key in enumerate(WEEK_KEYS):
        n = (v.get("week") or {}).get(key)
        week[key] = int(n) if isinstance(n, (int, float)) and 1 <= n <= 7 else WEEKDAY_DEFAULT[(i - 1) % 7]
    return {"targets": targets, "week": week}


def apply_program(v: Optional[dict]) -> dict:
    """Load config/program into TARGETS and WEEKDAY_DAY (in place, so every module sees it)."""
    p = norm_program(v)
    for typ in ("train", "rest"):
        TARGETS[typ].update({k: (int(x) if float(x).is_integer() else x) for k, x in p["targets"][typ].items()})
    for i, key in enumerate(WEEK_KEYS):
        WEEKDAY_DAY[(i - 1) % 7] = p["week"][key]
    return p
MUSCLES = {
    "chest": "חזה", "shoulders": "כתפיים", "triceps": "יד אחורית", "back": "גב",
    "biceps": "יד קדמית", "abs": "בטן", "quads": "ארבע ראשי", "hamstrings": "המסטרינג",
    "glutes": "ישבן", "calves": "תאומים", "adductors": "מקרבים", "other": "אחר",
}
LEGS = {"quads", "hamstrings", "glutes", "calves", "adductors"}
VOL_TARGET = {"quads": 10, "hamstrings": 9, "calves": 8}
DAYN = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------- dates and times ----------

def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def today() -> dt.date:
    return now().date()


def parse_date(s: str) -> dt.date:
    if not DATE_RE.match(s or ""):
        raise ValueError(f"bad date {s!r}, expected YYYY-MM-DD")
    return dt.date.fromisoformat(s)


def minutes(t: str) -> Optional[int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def hm(mins: int) -> str:
    mins = max(0, min(mins, 23 * 60 + 59))
    return f"{mins // 60:02d}:{mins % 60:02d}"


def day_label(d: dt.date) -> str:
    return f"יום {DAYN[d.weekday()]} {d.day:02d}.{d.month:02d}"


# ---------- cycle ----------

def planned_cycle(d: dt.date) -> int:
    if d >= WEEK_START:
        return WEEKDAY_DAY[d.weekday()]
    return (d - ANCHOR).days % 6 + 1


def cycle_day(day: Optional[dict], d: dt.date) -> int:
    if day and day.get("cycleDay"):
        return day["cycleDay"]
    return planned_cycle(d)


def day_type(day: Optional[dict], d: dt.date) -> str:
    if day and day.get("type") in ("train", "rest"):
        return day["type"]
    return "rest" if planned_cycle(d) in REST_DAYS else "train"


# ---------- day documents ----------

def _num(v: Any) -> Optional[float]:
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def blank_day(date_str: str) -> dict:
    d = parse_date(date_str)
    cd = planned_cycle(d)
    return {
        "date": date_str, "type": "rest" if cd in REST_DAYS else "train", "cycleDay": cd,
        "weight": None, "cr": [False, False], "items": [],
        "workout": {"done": False, "exercises": [], "notes": ""},
        "notes": "", "closed": False, "atHome": False, "targetAdj": None, "plan": None,
    }


def norm_item(i: dict) -> dict:
    o = {"n": str(i.get("n") or ""), "p": float(i.get("p") or 0), "f": float(i.get("f") or 0), "c": float(i.get("c") or 0)}
    if isinstance(i.get("k"), (int, float)):
        o["k"] = i["k"]
    return o


def norm_set(s: dict) -> dict:
    o = {"w": _num(s.get("w")) if s else None, "r": _num(s.get("r")) if s else None}
    if s and isinstance(s.get("rir"), (int, float)):
        o["rir"] = s["rir"]
    return o


def norm_adj(v: Any) -> Optional[dict]:
    """A one-day change to the target: {k, c, why, meal}. Calories and carbs only; protein and fat never move.
    meal: the one planned meal it comes off."""
    if not isinstance(v, dict) or not isinstance(v.get("k"), (int, float)) or not isinstance(v.get("c"), (int, float)):
        return None
    return {"k": v["k"], "c": v["c"], "why": str(v.get("why") or ""), "meal": str(v.get("meal") or "")}


def norm_day(date_str: str, v: Optional[dict]) -> dict:
    """Normalize a stored day document; the same shape tracker.html writes."""
    if not v:
        return blank_day(date_str)
    w = v.get("workout") or {}
    exs = []
    for e in w.get("exercises") or []:
        ex = {"name": str(e.get("name") or ""), "muscle": e.get("muscle") if e.get("muscle") in MUSCLES else "other",
              "sets": [norm_set(s) for s in e.get("sets") or []]}
        if isinstance(e.get("ss"), str) and e["ss"]:
            ex["ss"] = e["ss"]
        exs.append(ex)
    cd = v.get("cycleDay")
    return {
        "date": date_str,
        "type": "rest" if v.get("type") == "rest" else "train",
        "cycleDay": int(cd) if isinstance(cd, (int, float)) and 1 <= cd <= 7 else None,
        "weight": v["weight"] if isinstance(v.get("weight"), (int, float)) else None,
        "cr": [bool((v.get("cr") or [False, False])[0]), bool((v.get("cr") or [False, False])[1])] if isinstance(v.get("cr"), list) and len(v["cr"]) >= 2 else [False, False],
        "items": [norm_item(i) for i in v.get("items") or []],
        "workout": {"done": bool(w.get("done")), "exercises": exs, "notes": w.get("notes") if isinstance(w.get("notes"), str) else "",
                    # skipped: the user decided not to train; fromPlan: logged automatically from the plan, not confirmed yet
                    "skipped": bool(w.get("skipped")), "fromPlan": bool(w.get("fromPlan"))},
        "notes": v.get("notes") if isinstance(v.get("notes"), str) else "",
        "closed": bool(v.get("closed")),
        "atHome": bool(v.get("atHome")),  # the user said they stay home today, whatever the calendar says
        "targetAdj": norm_adj(v.get("targetAdj")),  # one-day target change the user approved (still day)
        "plan": v["plan"] if isinstance(v.get("plan"), list) and v["plan"] else None,
    }


def is_empty(d: dict) -> bool:
    w = d["workout"]
    cd = planned_cycle(parse_date(d["date"]))
    overridden = d.get("cycleDay") not in (None, cd) or d.get("type") != ("rest" if cd in REST_DAYS else "train")
    return (not overridden and not d.get("plan") and d["weight"] is None and not any(d["cr"]) and not d["items"]
            and not d["notes"] and not w["done"] and not w["exercises"] and not w["notes"] and not w.get("skipped")
            and not d.get("targetAdj"))


def creatine_taken(d: dict) -> bool:
    """One 5 g dose a day (since 2026-10-19). cr keeps two slots for old days; either counts."""
    return any(d["cr"])


def kcal(i: dict) -> float:
    return round(i["k"]) if i.get("k") is not None else round(4 * i["p"] + 9 * i["f"] + 4 * i["c"])


def totals(d: dict) -> dict:
    t = {"k": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
    for i in d["items"]:
        t["k"] += kcal(i)
        t["p"] += i["p"]
        t["f"] += i["f"]
        t["c"] += i["c"]
    return t


def day_target(d: dict) -> dict:
    """The day's target: the live one, plus a one-day change the user approved (targetAdj, rest days only:
    a day turned into a training day is back to the training target)."""
    g = dict(TARGETS[d["type"]])
    adj = d.get("targetAdj")
    if adj and d["type"] == "rest":
        g["k"], g["c"] = g["k"] + adj["k"], g["c"] + adj["c"]
    return g


def remaining(d: dict) -> dict:
    t, g = totals(d), day_target(d)
    return {k: g[k] - t[k] for k in g}


def done_sets(ex: dict) -> list:
    return [s for s in ex["sets"] if (s.get("r") or 0) > 0]


def has_workout(d: dict) -> bool:
    """A workout the user actually did: marked done, or sets they reported. Not a skipped day, and not one
    logged automatically from the plan until they confirm it."""
    w = d["workout"]
    if w["done"]:
        return True
    if w.get("skipped"):
        return False
    return not w.get("fromPlan") and any(done_sets(e) for e in w["exercises"])


def checklist(d: dict) -> list[dict]:
    """The same checks the tracker shows before closing a day."""
    t, g = totals(d), day_target(d)
    out = [
        {"ok": d["weight"] is not None, "label": "שקילת בוקר",
         "det": f"{d['weight']:g} ק״ג" if d["weight"] is not None else "לא נרשם משקל"},
        {"ok": creatine_taken(d), "label": "קריאטין", "det": "5 / 5 ג׳" if creatine_taken(d) else "0 / 5 ג׳"},
    ]
    k_ok = abs(t["k"] - g["k"]) <= g["k"] * 0.05
    p_ok = t["p"] >= g["p"] * 0.95
    det = f"{t['k']:,.0f} / {g['k']:,} קק״ל · {t['p']:.0f} / {g['p']} ג׳ חלבון"
    if not k_ok:
        det += f" · חסרות {g['k'] - t['k']:,.0f} קק״ל" if t["k"] < g["k"] else f" · {t['k'] - g['k']:,.0f} קק״ל מעל היעד"
    out.append({"ok": k_ok and p_ok, "label": "אוכל ביעד", "det": det + ("" if p_ok else " · חסר חלבון")})
    if d["type"] == "train" and d["workout"].get("skipped") and not d["workout"]["done"]:
        out.append({"ok": True, "label": "אימון", "det": "דילגת היום (החלטה שלך)"})
    elif d["type"] == "train":
        exs = d["workout"]["exercises"]
        n = sum(len(e["sets"]) for e in exs)
        empty = sum(1 for e in exs for s in e["sets"] if not (s.get("r") or 0) > 0 or s.get("w") is None)
        if not exs:
            det = "לא נרשמו תרגילים"
        elif empty:
            det = f"{empty} סטים בלי משקל או חזרות"
        elif not d["workout"]["done"]:
            det = "לא סומן שהאימון הושלם"
        else:
            det = f"{len(exs)} תרגילים, {n} סטים"
        out.append({"ok": bool(exs) and not empty and d["workout"]["done"], "label": "אימון", "det": det})
    else:
        out.append({"ok": True, "label": "אימון", "det": "יום מנוחה"})
    return out


# ---------- exercises and progression ----------

def norm_library(v: Optional[dict]) -> tuple[list[dict], dict[int, list[dict]]]:
    v = v or {}
    items = []
    for i, x in enumerate(v.get("items") or []):
        if not x.get("name"):
            continue
        items.append({
            "id": str(x.get("id") or f"x{i}"), "name": str(x["name"]),
            "muscle": x.get("muscle") if x.get("muscle") in MUSCLES else "other",
            "days": [n for n in x.get("days") or [] if 1 <= n <= 7],
            "sets": int(x.get("sets") or 3), "lo": int(x.get("lo") or 8), "hi": int(x.get("hi") or 12),
        })
        if x.get("max"):
            items[-1]["max"] = float(x["max"])  # heaviest load the gym has, e.g. the 24 kg kettlebell
    plans: dict[int, list[dict]] = {}
    for k, arr in (v.get("plans") or {}).items():
        try:
            n = int(k)
        except ValueError:
            continue
        if 1 <= n <= 7 and isinstance(arr, list):
            plans[n] = [{"name": str(e.get("name")), "ss": e.get("ss") or None} for e in arr if e.get("name")]
    return items, plans


def lib_for(items: list[dict], name: str) -> Optional[dict]:
    return next((x for x in items if x["name"] == name), None)


def lib_day(items: list[dict], plans: dict[int, list[dict]], cd: int) -> list[dict]:
    """Ordered exercises for a cycle day, with superset letters."""
    if plans:
        out = []
        for p in plans.get(cd, []):
            L = lib_for(items, p["name"]) or {"name": p["name"], "muscle": "other", "sets": 3, "lo": 8, "hi": 12}
            out.append({**L, "ss": p["ss"]})
        return out
    return [{**x, "ss": None} for x in items if cd in x["days"]]


def history(days: dict[str, dict], name: str, before: str) -> list[dict]:
    """Earlier sessions of an exercise, newest first: [{date, sets}]."""
    out = []
    for k in sorted(days, reverse=True):
        if k >= before:
            continue
        for e in days[k]["workout"]["exercises"]:
            if e["name"] != name:
                continue
            s = [x for x in e["sets"] if (x.get("r") or 0) > 0 and x.get("w") is not None]
            if s:
                out.append({"date": k, "sets": s})
    return out


def e1rm(s: dict) -> float:
    return s["w"] * (1 + s["r"] / 30) if s["w"] > 0 else s["r"]


def work_weight(sets: list[dict]) -> float:
    """The load used in most sets (heavier on a tie), so one too-heavy or warm-up set doesn't set the target."""
    ws = [s["w"] for s in sets]
    return max(set(ws), key=lambda w: (ws.count(w), w))


def suggest(name: str, muscle: str, lib_item: Optional[dict], prev: Optional[dict]) -> Optional[dict]:
    """Double progression from the previous session: {w, r, up, bw, cap}."""
    if not prev:
        return None
    lo = lib_item["lo"] if lib_item else 8
    hi = lib_item["hi"] if lib_item else 12
    n = lib_item["sets"] if lib_item else 3
    top = work_weight(prev["sets"])
    at_top = [s for s in prev["sets"] if s["w"] == top]
    min_r = min(s["r"] for s in at_top)
    all_top = len(at_top) >= min(n, len(prev["sets"])) and min_r >= hi
    if top <= 0:
        return {"w": 0, "r": hi + 2 if all_top else min(hi, min_r + 1), "up": False, "bw": True}
    if all_top:
        nxt = top + (5 if muscle in LEGS else 2.5)
        cap = (lib_item or {}).get("max")
        if cap and nxt > cap:  # no heavier load: keep the weight, slow the tempo
            return {"w": top, "r": hi, "up": False, "bw": False, "cap": True}
        return {"w": nxt, "r": lo, "up": True, "bw": False}
    return {"w": top, "r": min(hi, min_r + 1), "up": False, "bw": False}


def workout_from_plan(days: dict[str, dict], items: list[dict], plans: dict, cd: int, date_str: str, with_reps: bool) -> list[dict]:
    """Exercises for a day from the plan, pre-filled with suggested loads."""
    out = []
    for L in lib_day(items, plans, cd):
        prev = (history(days, L["name"], date_str) or [None])[0]
        sg = suggest(L["name"], L["muscle"], lib_for(items, L["name"]), prev)
        ex = {"name": L["name"], "muscle": L["muscle"], "sets": [
            {"w": sg["w"] if sg else None, "r": (sg["r"] if sg and with_reps else None)} for _ in range(L["sets"])
        ]}
        if L.get("ss"):
            ex["ss"] = L["ss"]
        out.append(ex)
    return out


def workout_lines(days: dict[str, dict], items: list[dict], plans: dict, cd: int, date_str: str) -> list[str]:
    """Human lines for a day's workout, grouped by superset, with next loads."""
    xs = lib_day(items, plans, cd)
    lines: list[str] = []
    i = 0
    while i < len(xs):
        j = i
        while xs[i].get("ss") and j + 1 < len(xs) and xs[j + 1].get("ss") == xs[i]["ss"]:
            j += 1
        group = xs[i:j + 1]
        parts = []
        for L in group:
            prev = (history(days, L["name"], date_str) or [None])[0]
            sg = suggest(L["name"], L["muscle"], lib_for(items, L["name"]), prev)
            load = (f"{sg['r']:g} חזרות" if sg["bw"] else f"{sg['w']:g}×{sg['r']:g}" + (", טמפו איטי" if sg.get("cap") else "")) if sg else "בלי משקל קודם"
            parts.append(f"{L['name']} ({load})")
        prefix = f"{group[0]['ss']}: " if j > i else "• "
        lines.append(prefix + " + ".join(parts))
        i = j + 1
    return lines


def volume(days: dict[str, dict], end: dt.date) -> dict[str, int]:
    start = (end - dt.timedelta(days=5)).isoformat()
    v: dict[str, int] = {}
    for k, d in days.items():
        if start <= k <= end.isoformat():
            for e in d["workout"]["exercises"]:
                v[e["muscle"]] = v.get(e["muscle"], 0) + len(done_sets(e))
    return v


# ---------- schedule and plans ----------

def norm_schedule(v: Optional[dict]) -> dict[str, list[dict]]:
    def f(arr):
        out = []
        for x in arr or []:
            if not x.get("n"):
                continue
            o = {"t": str(x.get("t") or ""), "n": str(x["n"]), "d": str(x.get("d") or ""), "kind": str(x.get("kind") or "other")}
            if isinstance(x.get("p"), (int, float)):
                o.update(p=x["p"], f=x.get("f") or 0, c=x.get("c") or 0)
                if isinstance(x.get("k"), (int, float)):
                    o["k"] = x["k"]
            for key in ("away", "where", "leave", "buy", "home"):  # on-the-go meals
                if isinstance(x.get(key), str) and x[key]:
                    o[key] = x[key]
            if isinstance(x.get("home_m"), dict):
                o["home_m"] = x["home_m"]
            if x.get("pinned"):  # time set by the user; the planner keeps it
                o["pinned"] = True
            if isinstance(x.get("base"), dict):  # template version before automatic top-ups or skips
                o["base"] = {k: x["base"][k] for k in BASE_KEYS if k in x["base"]}
            if x.get("auto"):
                o["auto"] = True
            out.append(o)
        return out
    v = v or {}
    return {"train": f(v.get("train")), "rest": f(v.get("rest"))}


def day_plan(schedule: dict, day: dict) -> list[dict]:
    if day.get("plan"):
        return norm_schedule({"train": day["plan"]})["train"]
    return schedule[day["type"]]


MORNING_START = 9 * 60  # the user prefers training in the morning, from 09:00


def find_slot(busy: list[tuple[int, int]], d: dt.date, preferred: int = MORNING_START, duration: int = 90,
              travel: int = 10, earliest: int = MORNING_START, latest: int = 1200) -> Optional[int]:
    """Start minute for a workout that clears every busy interval (with travel) and the gym's hours."""
    open_m, close_m = GYM_HOURS[d.weekday()]

    def fits(s: int) -> bool:
        e = s + duration
        if s < max(open_m, earliest) or e > close_m:
            return False
        return all(e + travel <= b0 or s - travel >= b1 for b0, b1 in busy)

    candidates = [preferred] + list(range(preferred + 30, latest + 1, 30)) + list(range(preferred - 30, earliest - 1, -30))
    return next((s for s in candidates if fits(s)), None)


def build_plan(template: list[dict], train_start: Optional[int], duration: int,
               busy: list[tuple[int, int]], away: Optional[list[dict]] = None,
               pins: Optional[list[dict]] = None) -> list[dict]:
    """Template for the day, with the workout moved and meals kept out of busy blocks.

    pins: plan items the user set by hand; they keep their time and content and the rest fits around them.
    """
    plan = [dict(x) for x in template]
    pinned = {p["n"]: p for p in pins or [] if p.get("pinned")}
    for i, x in enumerate(plan):
        if x["n"] in pinned:
            plan[i] = dict(pinned[x["n"]])
    if train_start is not None:
        end = train_start + duration
        for x in plan:
            if x["kind"] == "train":
                x["t"] = hm(train_start)
                x["d"] = f"{GYM_NAME}, {hm(train_start)}–{hm(end)}"
            elif x["n"] == "לפני האימון":
                x["t"] = hm(train_start - 30)
            elif x["n"] == "אחרי האימון":
                x["t"] = hm(end + 15)
            elif x["kind"] == "wake" and (minutes(x["t"]) or 0) > train_start - 60:
                x["t"] = hm(train_start - 60)
    fixed = {"לפני האימון", "אחרי האימון"}
    # Full meals stay out of meetings and out of the window from 90 min before training
    # to 15 min after it, and keep at least 75 min from other meals (45 min from the
    # light pre/post-workout snacks).
    blocked = list(busy)
    if train_start is not None:
        blocked.append((train_start - 90, train_start + duration + 15))
    wake = next((minutes(x["t"]) for x in plan if x["kind"] == "wake"), 0) or 0
    shakes = [minutes(x["t"]) for x in plan if x["kind"] == "meal" and x["n"] in fixed]
    placed: list[int] = [minutes(x["t"]) for x in plan if x.get("pinned") and x["kind"] == "meal" and minutes(x["t"]) is not None]
    movable = sorted((x for x in plan if x["kind"] == "meal" and x["n"] not in fixed and not x.get("pinned")
                      and minutes(x["t"]) is not None), key=lambda x: minutes(x["t"]))

    def ok(t: int) -> bool:
        return (wake + 15 <= t <= 23 * 60 and not any(b0 <= t < b1 for b0, b1 in blocked)
                and all(abs(t - p) >= 75 for p in placed) and all(abs(t - p) >= 45 for p in shakes))

    away = away or []

    def out(t: int) -> Optional[dict]:
        return next((a for a in away if a["s"] <= t < a["e"]), None)

    for x in movable:
        m = minutes(x["t"])
        win = out(m)
        if win:
            # Out of the house at meal time: eat at home up to 45 min earlier or later, else on the go.
            best = next((t for d in range(0, 46, 15) for t in (m - d, m + d) if ok(t) and not out(t)), None)
            if best is None:
                # Eaten on the go: still keep the spacing from other meals.
                def spaced(t: int) -> bool:
                    return (wake + 15 <= t <= 23 * 60 and all(abs(t - p) >= 75 for p in placed)
                            and all(abs(t - p) >= 45 for p in shakes))
                at = next((t for d in range(0, 121, 15) for t in (m - d, m + d) if spaced(t)), m)
                x["t"] = hm(at)
                make_away(x, out(at) or win)
                placed.append(at)
                continue
        else:
            best = next((t for d in range(0, 241, 15) for t in (m - d, m + d) if ok(t)), m)
        x["t"] = hm(best)
        placed.append(best)
    plan.sort(key=lambda x: minutes(x["t"]) if minutes(x["t"]) is not None else 9999)
    return plan


# ---------- rebalancing the rest of the day ----------

TOPUPS = [  # (name, p, f, c, kcal) — simple carb-heavy additions the user already eats
    ("חצי מנת גיינר", 13.6, 2.2, 54.3, 294),
    ("3 תמרים", 0.6, 0.0, 54.0, 220),
    ("150 ג׳ אורז מבושל", 4.0, 0.5, 42.0, 190),
    ("כף דבש", 0.0, 0.0, 17.0, 64),
]
SCOOP = ("סקופ חלבון", 29.8, 0.5, 1.5, 130)  # a typical 34 g scoop
CUTTABLE = ["מתוק מתוכנן", "ביניים", "שייק"]  # dropped first when the day is already over target


def logged_plan_names(day: dict) -> set[str]:
    return {i["n"].split(":")[0].strip() for i in day["items"]}


def _future_meals(plan: list[dict], day: dict, now_min: int) -> list[dict]:
    done = logged_plan_names(day)
    return [x for x in plan if x["kind"] == "meal" and "p" in x and (minutes(x["t"]) or 0) > now_min
            and x["n"] not in done]


BASE_KEYS = ("d", "p", "f", "c", "k", "kind")


def _keep_base(x: dict) -> None:
    """Remember the template version of a meal before the first automatic change."""
    if "base" not in x:
        x["base"] = {k: x[k] for k in BASE_KEYS if k in x}


def restore(x: dict) -> None:
    """Undo automatic changes (top-ups, auto-skips) so a rebalance starts from the template meal."""
    b = x.pop("base", None)
    if b:
        for k in BASE_KEYS:
            x.pop(k, None)
        x.update(b)
    x.pop("auto", None)


def _add(x: dict, name: str, p: float, f: float, c: float, k: float) -> None:
    _keep_base(x)
    cur = kcal(x)
    x["d"] = (x["d"] + " + " if x.get("d") else "") + name
    x["p"], x["f"], x["c"] = round(x["p"] + p, 1), round(x["f"] + f, 1), round(x["c"] + c, 1)
    x["k"] = round(cur + k)


def mark_skipped(x: dict, why: str, auto: bool = False) -> None:
    """Turn a planned meal into a note line: no macros, so it no longer counts.

    auto=True: the rebalance did it, and a later rebalance may bring the meal back.
    auto=False: the user skipped or replaced it, so it stays skipped.
    """
    if auto:
        _keep_base(x)
        x["auto"] = True
    else:
        x.pop("base", None)
        x.pop("auto", None)
    for key in ("p", "f", "c", "k"):
        x.pop(key, None)
    x["kind"] = "other"
    x["d"] = why


TRIM_MARK = "פחות היום"


def trim_candidate(day: dict, plan: list[dict], now_min: int) -> Optional[dict]:
    """The home meal still ahead with the most carbs, as the template has it (without automatic top-ups)."""
    plan = [dict(x) for x in plan]
    for x in plan:
        if (minutes(x["t"]) or 0) > now_min and (x.get("base") or x.get("auto")):
            restore(x)
    meals = [x for x in _future_meals(plan, day, now_min) if x.get("c", 0) > 0 and not x.get("away")]
    return max(meals, key=lambda y: y["c"]) if meals else None


def trim_for_target(day: dict, plan: list[dict], now_min: int) -> Optional[dict]:
    """A one-day lower target comes off the one meal chosen when the user approved it, once. Runs inside rebalance,
    so restoring the template can't undo it; once that meal is eaten or past, nothing else is cut."""
    adj = day.get("targetAdj")
    if not adj or adj["c"] >= 0 or not adj.get("meal") or day["type"] != "rest":
        return None
    x = next((y for y in _future_meals(plan, day, now_min) if y["n"] == adj["meal"]), None)
    if x is None or x.get("away") or TRIM_MARK in x.get("d", ""):
        return None
    cut = min(-adj["c"], x["c"])
    _keep_base(x)
    k = kcal(x)
    x["c"], x["k"] = round(x["c"] - cut, 1), round(k - cut * 4)
    x["d"] = (x["d"] + " · " if x.get("d") else "") + f"{cut:g} ג׳ פחמימות {TRIM_MARK}"
    return x


def rebalance(day: dict, plan: list[dict], now_min: int, kcal_tol: int = 150, p_tol: int = 15) -> tuple[list[dict], list[str]]:
    """Adjust the meals still ahead so the day lands on target. Returns (plan, changes in Hebrew)."""
    plan = [dict(x) for x in plan]
    for x in plan:
        if (minutes(x["t"]) or 0) > now_min and (x.get("base") or x.get("auto")):
            restore(x)  # start again from the template, so top-ups never pile up
    trim_for_target(day, plan, now_min)
    future = _future_meals(plan, day, now_min)
    if not future:
        return plan, []
    g, eaten = day_target(day), totals(day)
    gap_k = g["k"] - eaten["k"] - sum(kcal(x) for x in future)
    gap_p = g["p"] - eaten["p"] - sum(x["p"] for x in future)
    changes: list[str] = []
    scoop_to = next((x for x in future if "p" in x and SCOOP[0] not in x.get("d", "") and "חלבון" not in x.get("d", "")), None)
    if gap_p > p_tol and scoop_to is not None:
        x = scoop_to
        _add(x, *SCOOP)
        gap_k -= SCOOP[4]
        gap_p -= SCOOP[1]
        changes.append(f"{x['t']} {x['n']}: הוספתי {SCOOP[0]} (חסר חלבון)")
    for _ in range(3):
        if gap_k <= kcal_tol:
            break
        def free(t):  # meals that don't have this top-up yet: never two of the same on one meal
            return [x for x in future if "p" in x and t[0] not in x.get("d", "")]
        fits = [t for t in TOPUPS if t[4] <= gap_k + 100 and free(t)] or [t for t in TOPUPS[::-1] if free(t)][:1]
        if not fits:
            break
        name, p, f, c, k = max(fits, key=lambda t: t[4])
        post = next((x for x in free((name,)) if x["n"] == "אחרי האימון"), None)
        target = post if name == "חצי מנת גיינר" and post else min(free((name,)), key=kcal)
        _add(target, name, p, f, c, k)
        gap_k -= k
        changes.append(f"{target['t']} {target['n']}: הוספתי {name}")
    if gap_k < -kcal_tol:
        for n in CUTTABLE:
            x = next((y for y in future if y["n"] == n and "p" in y), None)
            if x is None or gap_k >= -kcal_tol:
                continue
            if gap_p + x["p"] > p_tol:  # dropping it would leave protein short
                continue
            gap_k += kcal(x)
            gap_p += x["p"]
            mark_skipped(x, "מדלגים היום: כבר עברת את יעד הקלוריות", auto=True)
            changes.append(f"{x['t']} {x['n']}: מדלגים")
    # A main meal that no longer fits: less than half of it is left for the day (they likely already ate it).
    left = g["k"] - eaten["k"]
    for x in [y for y in future if "p" in y]:
        if left < kcal(x) / 2:
            mark_skipped(x, f"מדלגים: נשארו היום רק {max(0, left):.0f} קק״ל", auto=True)
            changes.append(f"{x['t']} {x['n']}: מדלגים (היום כבר כמעט ביעד)")
        else:
            left -= kcal(x)
    return plan, changes


def meal_still_needed(day: dict, x: dict) -> bool:
    """False when the day has less than half of this meal's calories left: no reminder for it."""
    return "p" in x and remaining(day)["k"] >= kcal(x) / 2


def behind(day: dict, plan: list[dict], now_min: int) -> dict:
    """How far the logged food trails what the plan expected by now."""
    due = [x for x in plan if x["kind"] == "meal" and "p" in x and (minutes(x["t"]) or 0) <= now_min]
    t = totals(day)
    return {"k": sum(kcal(x) for x in due) - t["k"], "p": sum(x["p"] for x in due) - t["p"], "logged": len(day["items"])}


# ---------- motivation: streaks, the day's key lift, progress ----------

def protein_hit(d: dict) -> bool:
    return bool(d["items"]) and totals(d)["p"] >= TARGETS[d["type"]]["p"] * 0.95


def streaks(days: dict[str, dict], today_d: dt.date) -> dict[str, int]:
    """Consecutive days ending today (if it already counts) or yesterday.

    closed: days closed from the checklist; train: training days with a logged workout
    (rest days don't break it); protein: days at 95%+ of the protein target.
    """
    def run(ok, skip=lambda d, day: False) -> int:
        n, d = 0, today_d
        today_doc = days.get(d.isoformat())
        if not (today_doc and ok(today_doc)):
            d -= dt.timedelta(days=1)
        for _ in range(366):
            doc = days.get(d.isoformat())
            if doc is not None and ok(doc):
                n += 1
            elif not skip(d, doc):
                break
            d -= dt.timedelta(days=1)
        return n

    return {
        "closed": run(lambda x: x["closed"]),
        "train": run(lambda x: x["type"] == "train" and has_workout(x),
                     skip=lambda d, doc: day_type(doc, d) == "rest"),
        "protein": run(protein_hit),
    }


def streak_line(st: dict[str, int]) -> Optional[str]:
    parts = []
    if st["closed"] >= 2:
        parts.append(f"{st['closed']} ימים סגורים")
    if st["train"] >= 2:
        parts.append(f"{st['train']} אימונים בלי פספוס")
    if st["protein"] >= 3:
        parts.append(f"{st['protein']} ימים ביעד חלבון")
    return "רצף: " + " · ".join(parts) + ". שומרים עליו." if parts else None


def key_lift(days: dict[str, dict], items: list[dict], plans: dict, cd: int, ds: str) -> Optional[dict]:
    """The day's headline lift: the first planned exercise that goes up in weight, else the first with history."""
    first = None
    for L in lib_day(items, plans, cd):
        hist = history(days, L["name"], ds)
        if not hist:
            continue
        sg = suggest(L["name"], L["muscle"], lib_for(items, L["name"]), hist[0])
        if not sg or sg["bw"]:
            continue
        hit = {"name": L["name"], "w": sg["w"], "r": sg["r"], "up": sg["up"],
               "prev": ", ".join(f"{s['w']:g}×{s['r']:g}" for s in hist[0]["sets"])}
        if sg["up"]:
            return hit
        first = first or hit
    return first


def best_set(hist: list[dict]) -> Optional[dict]:
    sets = [s for h in hist for s in h["sets"]]
    return max(sets, key=e1rm) if sets else None


def prs_on(days: dict[str, dict], ds: str) -> list[str]:
    """Personal records set on a day: 'name w×r (קודם w×r)'."""
    out, seen = [], set()
    for e in days.get(ds, {"workout": {"exercises": []}})["workout"]["exercises"]:
        if e["name"] in seen:
            continue
        sets = [s for s in e["sets"] if (s.get("r") or 0) > 0 and s.get("w") is not None]
        prev = best_set(history(days, e["name"], ds))
        if not sets or prev is None:
            continue
        top = max(sets, key=e1rm)
        if e1rm(top) > e1rm(prev) + 1e-9:
            seen.add(e["name"])
            out.append(f"{e['name']} {top['w']:g}×{top['r']:g} (קודם {prev['w']:g}×{prev['r']:g})")
    return out


def progress_since_start(days: dict[str, dict]) -> list[str]:
    """Top working weight: first logged session vs latest, biggest gains first."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for k in sorted(days):
        for e in days[k]["workout"]["exercises"]:
            ws = [s["w"] for s in e["sets"] if (s.get("r") or 0) > 0 and s.get("w")]
            if ws:
                first.setdefault(e["name"], max(ws))
                last[e["name"]] = max(ws)
    gains = sorted(((last[n] - first[n], n) for n in first if last[n] != first[n]), reverse=True)
    return [f"{n} {first[n]:g}→{last[n]:g} ({d:+g})" for d, n in gains if d > 0][:4]


# ---------- tidying a finished workout ----------

def tidy_done_workout(w: dict) -> int:
    """Drop sets with no reps and rows left empty, and keep each superset's rows together.

    Run when a workout is marked done. Returns how many exercise rows were removed.
    """
    for e in w["exercises"]:
        e["sets"] = [s for s in e["sets"] if (s.get("r") or 0) > 0]
    keep = [e for e in w["exercises"] if e["sets"]]
    removed = len(w["exercises"]) - len(keep)
    order: list = []
    groups: dict = {}
    for i, e in enumerate(keep):
        k = e.get("ss") or f"#{i}"
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(e)
    w["exercises"] = [e for k in order for e in groups[k]]
    return removed


def find_exercise(exs: list[dict], name: str, superset: Optional[str]) -> Optional[int]:
    """Index of the row to act on. superset: a letter, '-' for rows without one, or None for any.

    Among matches, an empty row (no logged reps) is preferred.
    """
    def ok(e):
        if e["name"] != name:
            return False
        if superset is None:
            return True
        return (e.get("ss") or "-") == superset

    hits = [i for i, e in enumerate(exs) if ok(e)]
    empty = [i for i in hits if not done_sets(exs[i])]
    return (empty or hits or [None])[0]


# ---------- meals away from home ----------

TRAVEL_MIN = 20  # assumed travel each way to an away event
AWAY_WORDS = ("הופעה", "אירוע", "חתונה", "מסעד", "סידור", "נסיעה", "טיסה", "פגישה")
NOT_A_PLACE = ("http", "zoom", "meet.google", "teams", "פגישת")
# Easy to carry, from food the user has at home: (name, p, f, c, kcal)
PORTABLE = [
    ("שייקר: סקופ חלבון + 75 ג׳ גיינר (אבקה יבשה, מים בדרך)", 38.6, 3.2, 56.3, 411),
    ("יוגורט חלבון (200 ג׳) + בננה", 21.3, 6.4, 39.0, 295),
    ("חטיף חלבון + תפוח", 20.0, 7.0, 42.0, 310),
    ("3 תמרים + 30 ג׳ אגוזים", 5.0, 18.0, 58.0, 410),
    ("2 פריכיות עם שקיק חמאת בוטנים + בננה", 5.0, 8.5, 43.0, 270),
]


def is_away(e: dict) -> bool:
    """An event that takes the user out of the house: a physical address, 3+ hours, or an outing word."""
    if e.get("all_day") or not e.get("start") or not e.get("end"):
        return False
    loc = (e.get("location") or "").strip().lower()
    physical = bool(loc) and not any(w in loc for w in NOT_A_PLACE)
    long = (e["end"] - e["start"]).total_seconds() >= 3 * 3600
    title = (e.get("title") or "").lower()
    return physical or long or any(w in title for w in AWAY_WORDS)


def away_events(events: list[dict]) -> list[dict]:
    """The events that take the user out, by start time. The gym has its own leave reminder."""
    return sorted((e for e in events if is_away(e) and "חדר כושר" not in (e.get("title") or "")),
                  key=lambda e: e["start"])


def place_of(e: dict) -> str:
    """The event's full location when it is a real place, else ""."""
    loc = (e.get("location") or "").strip()
    return "" if any(w in loc.lower() for w in NOT_A_PLACE) else loc


def norm_travel(v: Optional[dict]) -> dict:
    """config/travel: {default: minutes, places: [{match, minutes}]}; match is found in an event's location or title.

    live: {location: minutes} from Waze for this planning run (never saved); it wins over places and default.
    """
    v = v or {}
    places = [{"match": str(p.get("match") or "").strip(), "minutes": int(p.get("minutes") or TRAVEL_MIN)}
              for p in v.get("places") or [] if str(p.get("match") or "").strip()]
    out = {"default": int(v.get("default") or TRAVEL_MIN), "places": places}
    if v.get("live"):
        out["live"] = {str(k): int(m) for k, m in v["live"].items()}
    return out


def travel_for(e: dict, travel: dict) -> int:
    """Minutes to the event: Waze's live answer, else the manual place match, else the default."""
    live = (travel.get("live") or {}).get(place_of(e))
    if live is not None:
        return live
    hay = f"{e.get('location') or ''} {e.get('title') or ''}".lower()
    hit = next((p for p in travel["places"] if p["match"].lower() in hay), None)
    return hit["minutes"] if hit else travel["default"]


def away_windows(events: list[dict], d: dt.date, travel: Optional[dict] = None, join_gap: int = 60) -> list[dict]:
    """Minutes from midnight when the user is out, travel included: [{s, e, title, location}].

    Away events less than an hour apart are one outing (they go from one to the next). Travel time
    comes from travel_for: Waze live minutes, else config/travel per place, else the default (20 min).
    """
    travel = travel or norm_travel(None)
    raw = []
    for e in away_events(events):
        s_ = e["start"].hour * 60 + e["start"].minute if e["start"].date() == d else 0
        en = e["end"].hour * 60 + e["end"].minute if e["end"].date() == d else 24 * 60 - 1
        title = re.sub(r"\s+", " ", re.sub(r"[^\w\s׳״'\-.,:()]", "", e.get("title") or "")).strip()
        loc = place_of(e).split(",")[0].strip()
        mins = travel_for(e, travel)
        if raw and s_ - raw[-1]["end"] < join_gap:
            last = raw[-1]
            last["end"] = max(last["end"], en)
            last["titles"].append(title)
            last["location"] = loc or last["location"]
            last["back"] = mins  # travel home is from the last place
        else:
            raw.append({"start": s_, "end": en, "titles": [title], "location": loc, "to": mins, "back": mins})
    return [{"s": max(0, w["start"] - w["to"]), "e": min(24 * 60 - 1, w["end"] + w["back"]),
             "title": " ואז ".join(w["titles"]), "location": w["location"]} for w in raw]


def pick_portable(p: float, k: float) -> tuple:
    return min(PORTABLE, key=lambda o: abs(o[4] - k) / 100 + abs(o[1] - p) / 8)


def buy_text(kcal_: float, location: str) -> str:
    where = f" באזור {location}" if location else ""
    if kcal_ >= 450:
        return (f"או לקנות{where}: שווארמה עוף בצלחת עם אורז, חזה עוף על הגריל עם אורז או תפוחי אדמה, "
                "או סושי סלמון (12–16 יחידות). מסופר: סלט עוף מוכן, פריכיות ומשקה חלבון צמחי.")
    return f"או לקנות{where} בסופר או בקיוסק: משקה חלבון צמחי ובננה, או חטיף חלבון ותפוח."


def retime_away(plan: list[dict], windows: list[dict], min_shift: int = 0) -> dict[str, str]:
    """Set each away item's 'leave' from the matching window (same outing title, else the window
    that contains the item's time), when it moves by at least min_shift minutes.
    Changes plan in place; returns {title: leave} for what moved."""
    moved = {}
    for x in plan:
        if not x.get("away"):
            continue
        m = minutes(x["t"])
        win = next((w for w in windows if w["title"] == x["away"]), None) or \
            next((w for w in windows if m is not None and w["s"] <= m <= w["e"]), None)
        old = minutes(x.get("leave") or "")
        if win and x.get("leave") != hm(win["s"]) and (old is None or abs(win["s"] - old) >= min_shift):
            x["leave"] = hm(win["s"])
            moved[x["away"]] = x["leave"]
    return moved


AWAY_KEYS = ("away", "where", "leave", "buy", "home", "home_m")


def reconcile_away(plan: list[dict], windows: list[dict], now_min: int, template: Optional[list[dict]] = None) -> list[str]:
    """Bring a saved plan in line with today's calendar, in place. Returns what changed (Hebrew).

    An on-the-go meal whose outing is gone goes back to its home version, even if its time has
    passed and it wasn't eaten yet. A coming meal that now falls inside an outing goes on the road.
    The meals around the workout and anything the user set by hand stay as they are.
    """
    by_name = {t["n"]: t for t in template or []}
    changes: list[str] = []
    for x in plan:
        m = minutes(x["t"])
        win = next((w for w in windows if m is not None and w["s"] <= m <= w["e"]), None)
        if x.get("away"):
            if win is not None:
                if win["title"] != x["away"]:  # another outing at this time
                    x["d"] = (x.get("d") or "").replace(f"({x['away']})", f"({win['title']})")
                    x["away"], x["where"], x["leave"] = win["title"], win["location"], hm(win["s"])
                continue
            if x["kind"] == "meal" and "p" in x:
                t = by_name.get(x["n"]) or {}
                macros = x.get("home_m") or {k: t[k] for k in ("p", "f", "c", "k") if k in t}
                if macros:
                    x.pop("k", None)
                    x.update(macros)
                x["d"] = x.get("home") or t.get("d") or x.get("d", "")
                x.pop("base", None)
                x.pop("auto", None)
                changes.append(f"{x['t']} {x['n']}: חזר לגרסה של הבית")
            for k in AWAY_KEYS:
                x.pop(k, None)
        elif (win is not None and m > now_min and x["kind"] == "meal" and "p" in x
              and not x.get("pinned") and x["n"] not in ("לפני האימון", "אחרי האימון")):
            make_away(x, win)
            changes.append(f"{x['t']} {x['n']}: בדרך ({win['title']})")
    return changes


def make_away(x: dict, win: dict) -> None:
    """Turn a planned meal into its on-the-go version, keeping what it replaced."""
    k0, p0 = kcal(x), x["p"]
    name, p, f, c, k = pick_portable(p0, k0)
    x["home_m"] = {key: x[key] for key in ("p", "f", "c", "k") if key in x}
    x["home"] = x.get("d", "")
    x["d"] = f"בדרך ({win['title']}): {name}"
    x["p"], x["f"], x["c"], x["k"] = p, f, c, k
    x["away"], x["where"], x["leave"] = win["title"], win["location"], hm(win["s"])
    x["buy"] = buy_text(k0, win["location"])
