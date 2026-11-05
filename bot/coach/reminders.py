"""What to remind the user about, and when. Pure functions; bot.py sends them."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from . import domain

MEAL_LEAD = 20       # minutes before a meal: start preparing
LEAVE_LEAD = 25      # minutes before training: get ready, leave at -20
CHECK_AFTER = 45     # minutes after training ends: ask for the sets
EVENING = 21 * 60 + 30


@dataclass
class Reminder:
    key: str                      # unique per day, used to send once
    at: int                       # minute of the day
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (label, callback data)


def _meal_line(x: dict) -> str:
    return f"{x['n']}: {x['d']}" if x.get("d") else x["n"]


def morning_text(day: dict, plan: list[dict], mission: Optional[dict], streak: Optional[str]) -> str:
    """Wake-up message in the coach voice: the day, the mission, the streak."""
    train = next((x for x in plan if x["kind"] == "train"), None)
    first_meal = next((y for y in plan if y["kind"] == "meal" and "p" in y), None)
    cd = day["cycleDay"] or 1
    if day["type"] == "train" and train:
        lines = [f"בוקר. יום {cd}, {domain.CYCLE[cd]}."]
        pre = next((y for y in plan if y["n"] == "לפני האימון"), None)
        lines.append("שקילה ותשלח לי את המספר" + (f", {pre['d']} ב-{pre['t']}" if pre and pre.get("d") else "")
                     + f", וב-{train['t']} ב{domain.GYM_NAME}.")
        lines.append("")
        if mission:
            lines.append(f"המשימה: {mission['name']} {mission['w']:g} ק״ג × {mission['r']:g}.")
            lines.append(f"פעם קודמת: {mission['prev']}. " + ("מעלים." if mission["up"] else "אותו משקל, עוד חזרה."))
        else:
            lines.append("המשימה: משקל × חזרות לכל סט. היום קובעים את נקודת ההתחלה.")
    else:
        lines = ["בוקר. יום מנוחה.", "שקילה ותשלח לי את המספר. השריר נבנה היום, מהאוכל ומהשינה."]
        if first_meal:
            lines.append(f"ארוחה ראשונה ב-{first_meal['t']}: {first_meal['d'] or first_meal['n']}.")
    if streak:
        lines += ["", streak]
    return "\n".join(lines)


def day_reminders(day: dict, plan: list[dict], workout_lines: list[str], mission: Optional[dict] = None,
                  streak: Optional[str] = None) -> list[Reminder]:
    """Reminders for one day from its plan."""
    ds = day["date"]
    out: list[Reminder] = []
    train = next((x for x in plan if x["kind"] == "train"), None)
    for i, x in enumerate(plan):
        m = domain.minutes(x["t"])
        if m is None:
            continue
        if x["kind"] == "wake":
            out.append(Reminder(f"{ds}:wake", m, morning_text(day, plan, mission, streak)))
        elif x["kind"] == "meal" and "p" in x and x.get("away"):
            text = "\n".join([f"{x['n']}, בדרך: {x['d'].split(': ', 1)[-1]}",
                               f"{domain.kcal(x):.0f} קק״ל, {x['p']:g} ג׳ חלבון.", x.get("buy", "")]).strip()
            out.append(Reminder(f"{ds}:meal:{i}", m, text,
                                [("✓ אכלתי", f"meal:{ds}:{i}"), ("⏰ עוד 15 דק׳", f"snooze:{ds}:meal:{i}"), ("🏠 אני בבית", f"home:{ds}")]))
        elif x["kind"] == "meal" and "p" in x:
            lead = 0 if x["n"] in ("לפני האימון", "מתוק מתוכנן") or x.get("p", 0) < 10 else MEAL_LEAD
            when = "עכשיו" if lead == 0 else f"בעוד {lead} דק׳ ({x['t']})"
            text = f"{when}: {_meal_line(x)}\n{domain.kcal(x):.0f} קק״ל, {x['p']:g} ג׳ חלבון."
            out.append(Reminder(f"{ds}:meal:{i}", m - lead, text,
                                [("✓ אכלתי", f"meal:{ds}:{i}"), ("⏰ עוד 15 דק׳", f"snooze:{ds}:meal:{i}")]))
        elif x["kind"] == "train" and day["type"] == "train":
            text = "\n".join([f"יוצאים ב-{domain.hm(m - 20)}. אימון ב-{x['t']}.", "", *workout_lines])
            out.append(Reminder(f"{ds}:leave", m - LEAVE_LEAD, text, [("⏰ עוד 15 דק׳", f"snooze:{ds}:leave")]))
            end = m + 90
            out.append(Reminder(f"{ds}:sets", end + CHECK_AFTER,
                                "האימון מאחוריך. שלח משקל × חזרות לכל תרגיל, או לחץ אם עשית בדיוק לפי התוכנית.",
                                [("✓ עשיתי לפי התוכנית", f"asplan:{ds}")]))
    # Before leaving for an away event: what to take for the meals that fall outside.
    packs: dict[str, list[dict]] = {}
    for x in plan:
        if x.get("away") and x.get("leave"):
            packs.setdefault(x["leave"], []).append(x)
    for leave, meals in packs.items():
        lm = domain.minutes(leave)
        if lm is None:
            continue
        items = [f"• {y['t']} {y['n']}: {y['d'].split(': ', 1)[-1]}" for y in meals]
        title = meals[0]["away"]
        out.append(Reminder(f"{ds}:pack:{leave}", lm - 15,
                            "\n".join([f"יוצאים ב-{leave} ל{title}. לוקחים לדרך:", *items,
                                        "שקית קטנה ובקבוק מים, וזה מסודר."])))
    return out


def due(reminders: list[Reminder], now_min: int, window: int = 3) -> list[Reminder]:
    """Reminders whose time arrived in the last `window` minutes."""
    return [r for r in reminders if now_min - window < r.at <= now_min]


def evening_text(day: dict, result: Optional[dict], tomorrow: dt.date, logged_plan: bool,
                 week: Optional[dict], extras: Optional[dict] = None) -> str:
    """The 21:30 message: today's result, records and streak, then tomorrow's shape."""
    extras = extras or {}
    lines = []
    for pr in extras.get("prs") or []:
        lines.append(f"🏆 שיא: {pr}")
    missing = [c for c in domain.checklist(day) if not c["ok"]]
    if day["closed"]:
        lines.append("היום סגור ✓")
    elif not missing:
        lines.append("יום מלא ✓ סגור אותו.")
    else:
        lines.append("חסר להיום:")
        lines += [f"• {c['label']}: {c['det']}" for c in missing]
    if extras.get("streak"):
        lines.append(extras["streak"])
    if logged_plan:
        lines.append("רשמתי את האימון לפי התוכנית. תתקן מה שהיה שונה ותסמן שהאימון הושלם.")
    if result:
        cd = result["cycleDay"]
        head = f"מחר, {domain.day_label(tomorrow)}: יום {cd}, {domain.CYCLE[cd]}."
        if result["type"] == "train" and result["start"] is not None:
            head += f" אימון ב-{domain.hm(result['start'])}, לצאת ב-{domain.hm(result['start'] - 20)}."
            if result["conflict"]:
                head += " לא מצאתי חלון פנוי ביומן, השארתי את השעה הרגילה."
            elif result["moved"]:
                head += " הזזתי את האירוע ביומן."
        lines += ["", head]
        wake = next((x for x in result["plan"] if x["kind"] == "wake"), None)
        if wake:
            lines.append(f"קימה ושקילה ב-{wake['t']}.")
        prep = domain.PREP.get(tomorrow.weekday())
        if prep:
            lines.append(f"יום בישול: {prep}")
    if tomorrow.weekday() == domain.SHOP_FOR_WEEKDAY:
        lines += ["", "קניות לשבוע:", *domain.SHOPPING]
    if week and week.get("change") is not None:
        ch = week["change"]
        verdict = "בטווח" if 0.1 <= ch <= 0.4 else ("אין עלייה" if ch < 0.1 else "מהר מדי")
        lines += ["", f"שבוע: ממוצע {week['weight_avg']:.1f} ק״ג, {ch:+.2f} מהשבוע הקודם ({verdict})."]
    if week and extras.get("progress"):
        lines.append("מההתחלה: " + " · ".join(extras["progress"]))
    return "\n".join(lines)


# ---------- follow-ups that depend on what was logged ----------

CHECK_AT = 16 * 60
BEHIND_K, BEHIND_P = 500, 40


def follow_ups(day: dict, plan: list[dict]) -> list[Reminder]:
    """Second reminder: weigh-in an hour after waking, only when still missing. (Creatine is one dose with breakfast.)"""
    ds = day["date"]
    out = []
    wake = next((domain.minutes(x["t"]) for x in plan if x["kind"] == "wake"), None)
    if day["weight"] is None and wake is not None:
        out.append(Reminder(f"{ds}:weigh2", wake + 60,
                            "עוד לא נרשם משקל היום. אם עוד לא אכלת, זה הזמן להישקל ולשלוח לי את המספר. אם כבר אכלת, מדלגים היום."))
    return out


def afternoon_text(gap: dict, changes: list[str], left: dict) -> str:
    """The 16:00 nudge when the day trails the plan."""
    if gap["logged"] == 0:
        return "עוד לא נרשם אוכל היום. מה אכלת עד עכשיו? כתוב לי או שלח תמונה, ואסדר את שאר היום."
    lines = [f"בדיקת 16:00: אתה מאחור בכ-{max(0, gap['k']):,.0f} קק״ל ו-{max(0, gap['p']):.0f} ג׳ חלבון לעומת התוכנית."]
    if changes:
        lines.append("עדכנתי את שאר היום:")
        lines += [f"• {c}" for c in changes]
    lines.append(f"נשאר להיום: {max(0, left['k']):,.0f} קק״ל, {max(0, left['p']):.0f} ג׳ חלבון.")
    return "\n".join(lines)
