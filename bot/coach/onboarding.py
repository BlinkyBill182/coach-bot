"""The opening questionnaire: the base the coach works from.

A page inside Telegram (bot/web/onboarding.html, opened with /onboarding, routes in coach/web.py). No Claude calls
except one look at the optional body photo (Coach.describe_build).

Where things go:
- config/onboarding: the draft while they fill it in (kept on the server, so closing the page loses nothing),
  then the saved answers and the estimate.
- config/profile notes (source "onboarding"): job, foods and preferences, what's at home, so the bot sees them in
  every message. A second run replaces them.
- config/program: the targets, only after their yes on the page (ToolRunner.onboarding_targets, through _gate), and
  only offered when the formula is 5% or more away from the live targets.
- The body photo is never stored: only a short description in words is.
The exercise plan, the week and the meal template don't change here; a different gym count is raised with them.
"""
from __future__ import annotations

from typing import Any, Optional

from . import domain
from .store import Store
from .tools import ToolRunner

DOC = "config/onboarding"
MAX_TEXT, MAX_BUILD = 500, 300

SEX = {"m": "גבר", "f": "אישה"}
SEX_OFFSET = {"m": 5, "f": -161}  # Mifflin-St Jeor
GOALS = {"lose": "ירידה במשקל", "keep": "שמירה על כושר", "gain": "עלייה במסת שריר"}
GOAL_FACTOR = {"lose": 0.80, "keep": 1.0, "gain": 1.10}
ACTIVITY = {"sit": "בעיקר יושב", "mixed": "חצי-חצי", "active": "על הרגליים או עבודה פיזית"}
ACTIVITY_FACTOR = {"sit": 1.4, "mixed": 1.55, "active": 1.75}  # the day without the gym
GYM_KCAL_PER_KG = 5.0  # about 5 METs for 75 minutes, net of rest: (5 - 1) x kg x 1.25 h
PROTEIN_PER_KG = 2.2
FAT_SHARE, FAT_MIN_PER_KG = 0.25, 0.9
KEEP_GAP = 0.05  # closer than this to the live targets: the targets stay
FLOOR_KCAL = {"m": 1500, "f": 1200}

NUMBERS = {"age": ("body", 14, 99, "גיל בין 14 ל-99"), "weight": ("body", 35, 250, "משקל בין 35 ל-250 ק״ג"),
           "height": ("body", 120, 230, "גובה בין 120 ל-230 ס״מ"), "gym": ("gym", 0, 7, "בין 0 ל-7 פעמים בשבוע")}
CHOICES = {"sex": SEX, "goal": GOALS, "activity": ACTIVITY}
TEXTS = ("job", "avoid", "pantry")
REQUIRED = (("age", "body"), ("weight", "body"), ("height", "body"), ("sex", "sex"), ("goal", "goal"),
            ("activity", "activity"), ("gym", "gym"))


class Invalid(ValueError):
    """An answer the page should send them back to fix. step: the question it belongs to."""

    def __init__(self, step: str, message: str):
        super().__init__(message)
        self.step = step


# ---------- the estimate ----------

def estimate(a: dict) -> dict:
    """Daily burn and targets from the answers. An estimate (the BMR formula alone is off by about 10%);
    after 4 weeks the weight trend beats it."""
    kg, cm, age = a["weight"], a["height"], a["age"]
    bmr = 10 * kg + 6.25 * cm - 5 * age + SEX_OFFSET[a["sex"]]
    rest = bmr * ACTIVITY_FACTOR[a["activity"]]
    train = rest + GYM_KCAL_PER_KG * kg
    factor = GOAL_FACTOR[a["goal"]]

    def target(burn: float) -> dict:
        k = burn * factor
        if a["goal"] == "lose":
            k = max(k, bmr * 1.1, FLOOR_KCAL[a["sex"]])
        k = round(k / 10) * 10
        p = round(PROTEIN_PER_KG * kg)
        f = round(max(FAT_MIN_PER_KG * kg, FAT_SHARE * k / 9))
        return {"k": k, "p": p, "f": f, "c": max(0, round((k - p * 4 - f * 9) / 4))}
    return {"bmr": round(bmr), "burn": {"rest": round(rest), "train": round(train)},
            "targets": {"train": target(train), "rest": target(rest)}}


def target_gap(targets: dict) -> float:
    """The largest difference from the live calorie targets, as a share of them."""
    return max(abs(targets[t]["k"] - domain.TARGETS[t]["k"]) / domain.TARGETS[t]["k"] for t in ("train", "rest"))


# ---------- the document ----------

def _doc(store: Store) -> dict:
    return store.data(DOC) or {}


def answers(store: Store) -> dict:
    """The saved answers; empty until the questionnaire was finished once."""
    return _doc(store).get("answers") or {}


def fresh_install(store: Store) -> bool:
    return store.data(DOC) is None and not store.list("log")


def _draft(d: dict) -> dict:
    """What he's filling in: the draft, or on a new run the saved answers to start from."""
    if d.get("draft"):
        return dict(d["draft"])
    return {k: v for k, v in (d.get("answers") or {}).items() if k != "date"}


def clean(raw: dict) -> dict:
    """Only the known answers, checked. An empty value clears the answer (None). The build isn't here:
    it comes only from the photo route."""
    out: dict[str, Any] = {}
    for key, (step, lo, hi, message) in NUMBERS.items():
        if key not in raw:
            continue
        v = raw[key]
        if v in (None, ""):
            out[key] = None
            continue
        if isinstance(v, bool):
            raise Invalid(step, message)
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise Invalid(step, message) from None
        if not lo <= n <= hi or (key in ("age", "gym") and n != int(n)):
            raise Invalid(step, message)
        out[key] = int(n) if key in ("age", "gym") else n
    for key, options in CHOICES.items():
        if key in raw:
            if raw[key] in (None, ""):
                out[key] = None
            elif raw[key] not in options:
                raise Invalid(key, "בחירה לא מוכרת")
            else:
                out[key] = raw[key]
    for key in TEXTS:
        if key in raw:
            text = str(raw[key] or "").strip()[:MAX_TEXT]
            out[key] = text or None
    return out


def _merge(draft: dict, changes: dict) -> dict:
    return {k: v for k, v in {**draft, **changes}.items() if v is not None}


# ---------- what the page calls ----------

def state(store: Store) -> dict:
    """Everything the page needs to open."""
    d = _doc(store)
    last = next((x["weight"] for _, x in sorted(store.days().items(), reverse=True) if x["weight"] is not None), None)
    return {"draft": _draft(d), "saved": bool(d.get("answers")), "lastWeight": last}


def save_draft(store: Store, raw: dict) -> dict:
    d = _doc(store)
    d["draft"] = _merge(_draft(d), clean(raw))
    d["status"] = "draft"
    store.set(DOC, d)
    return d["draft"]


def set_build(store: Store, build: Optional[str]) -> None:
    """From the photo route: the description in words, or None to clear it."""
    d = _doc(store)
    d["draft"] = _merge(_draft(d), {"build": build[:MAX_BUILD] if build else None})
    d["status"] = "draft"
    store.set(DOC, d)


def _complete(draft: dict) -> dict:
    for key, step in REQUIRED:
        if draft.get(key) is None:
            raise Invalid(step, "חסרה תשובה")
    return draft


def review(store: Store) -> dict:
    """The last screen: the estimate against the live targets. From a 5% gap it records the preview that their
    press on the page (a later request) confirms."""
    store.program()
    a = _complete(_draft(_doc(store)))
    est = estimate(a)
    gap = target_gap(est["targets"])
    propose = gap >= KEEP_GAP
    if propose:
        ToolRunner(store, None).onboarding_targets(est["targets"], confirm=False)
    return {"estimate": est, "live": {t: dict(domain.TARGETS[t]) for t in ("train", "rest")}, "gap": round(gap, 4), "propose": propose}


def discard(store: Store) -> None:
    """Leave without saving: the draft goes, the saved answers stay."""
    d = _doc(store)
    d.update(draft={}, status="done" if d.get("answers") else "none")
    store.set(DOC, d)


def _summary(a: dict) -> list[str]:
    lines = [f"גיל {a['age']} · {a['weight']:g} ק״ג · {a['height']:g} ס״מ · {SEX[a['sex']]}",
             f"מטרה: {GOALS[a['goal']]}",
             "עיסוק: " + " · ".join(x for x in (a.get("job"), ACTIVITY[a["activity"]]) if x),
             f"חדר כושר: {a['gym']} בשבוע"]
    if a.get("build"):
        lines.append(f"מבנה: {a['build']}")
    if a.get("avoid"):
        lines.append(f"לא אוכל והעדפות: {a['avoid']}")
    if a.get("pantry"):
        lines.append(f"תמיד בבית ותוספים: {a['pantry']}")
    return lines


def _macro(t: dict) -> str:
    return f"{t['k']:,.0f} קק״ל ({t['p']:g} חלבון, {t['f']:g} שומן, {t['c']:g} פחמימות)"


def _facts(a: dict) -> list[str]:
    return [f"שאלון פתיחה, {label}: {a[key]}" for key, label in
            (("job", "עיסוק"), ("avoid", "מאכלים שלא אוכל והעדפות"), ("pantry", "תמיד בבית ותוספים זמינים")) if a.get(key)]


def finish(store: Store, apply_targets: bool, chat_id: int) -> str:
    """Save the base; targets change only with apply_targets, which goes through the gate (ValueError when the
    preview doesn't match, so the page reviews again). Returns the message for the chat."""
    store.program()
    d = _doc(store)
    a = _complete(_draft(d))
    est = estimate(a)
    propose = target_gap(est["targets"]) >= KEEP_GAP
    lines = ["שמרתי את השאלון. מעכשיו אני עובד מהבסיס הזה."]
    if apply_targets and propose:
        ToolRunner(store, None).onboarding_targets(est["targets"], confirm=True)
        lines.append(f"היעדים עודכנו. אימון: {_macro(est['targets']['train'])}. מנוחה: {_macro(est['targets']['rest'])}.")
    elif propose:
        lines.append("היעדים לא השתנו.")
    planned = sum(1 for v in domain.WEEKDAY_DAY.values() if v not in domain.REST_DAYS)
    if a["gym"] != planned:
        lines.append(f"בתוכנית יש {planned} אימונים בשבוע ואתה אמרת {a['gym']}. אם לשנות את השבוע, תכתוב לי ונעבור על זה.")
    a = {**a, "date": domain.today().isoformat()}
    store.set(DOC, {"status": "done", "draft": {}, "answers": a, "estimate": est})
    notes = [n for n in store.profile_notes() if n.get("source") != "onboarding"]
    notes += [{"text": t, "date": a["date"], "source": "onboarding"} for t in _facts(a)]
    store.set_profile_notes(notes)
    reply = "\n".join(lines)
    store.add_chat(chat_id, "user", "[שאלון פתיחה]\n" + "\n".join(_summary(a)))
    store.add_chat(chat_id, "assistant", reply)
    return reply


def base_line(store: Store) -> Optional[str]:
    """One line for the model and for memory.sh pull."""
    d = _doc(store)
    a = d.get("answers")
    if not a:
        return None
    est = d.get("estimate") or {}
    burn = est.get("burn") or {}
    return (f"Base from the questionnaire ({a.get('date')}; wins over how the skill describes the user): age {a['age']}, {a['height']:g} cm, "
            f"{a['weight']:g} kg then, {'male' if a['sex'] == 'm' else 'female'}, goal {a['goal']}, "
            f"day without gym: {a['activity']}, gym {a['gym']}/week"
            + (f", build (from a photo that was not kept): {a['build']}" if a.get("build") else "")
            + (f". Formula estimate, about 10% error: rest day burn {burn.get('rest')}, training day {burn.get('train')} kcal; the weight trend beats it." if burn else "."))
