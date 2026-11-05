import datetime as dt

from coach import domain, reminders


def _d(ds, **kw):
    d = domain.blank_day(ds)
    d.update(kw)
    return d


def _train(ds, w=60):
    d = _d(ds)
    d["workout"]["exercises"] = [{"name": "לחיצת חזה", "muscle": "chest", "sets": [{"w": w, "r": 8}] * 3}]
    return d


def test_streaks_skip_rest_days_and_count_closed():
    # 21 D1, 22 D2, 23 D3, 24 D4 rest, 25 D5
    days = {ds: _train(ds) for ds in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-25")}
    days["2026-10-24"] = _d("2026-10-24")  # rest day, nothing logged
    for k in ("2026-10-23", "2026-10-25"):
        days[k]["closed"] = True
    st = domain.streaks(days, dt.date(2026, 10, 25))
    assert st["train"] == 4  # the rest day doesn't break it
    assert st["closed"] == 1  # 24 not closed
    assert domain.streak_line(st) == "רצף: 4 אימונים בלי פספוס. שומרים עליו."


def test_key_lift_and_records(store):
    items, plans = store.library()
    days = {"2026-10-21": _train("2026-10-21", 60)}
    k = domain.key_lift(days, items, plans, 5, "2026-10-25")
    assert k["name"] == "לחיצת חזה" and k["w"] == 62.5 and k["up"] and k["prev"] == "60×8, 60×8, 60×8"
    days["2026-10-25"] = _train("2026-10-25", 62.5)
    assert domain.prs_on(days, "2026-10-25") == ["לחיצת חזה 62.5×8 (קודם 60×8)"]
    assert domain.progress_since_start(days) == ["לחיצת חזה 60→62.5 (+2.5)"]


def test_morning_text_coach_voice(store):
    day = store.day("2026-10-25")
    plan = domain.day_plan(store.schedule(), day)
    txt = reminders.morning_text(day, plan, {"name": "לחיצת חזה", "w": 62.5, "r": 6, "up": True, "prev": "60×8, 60×8, 60×8"},
                                 "רצף: 6 ימים סגורים. שומרים עליו.")
    assert txt.startswith("בוקר. יום 5, פלג עליון.")
    assert "המשימה: לחיצת חזה 62.5 ק״ג × 6." in txt and "מעלים." in txt and txt.endswith("שומרים עליו.")
    first = reminders.morning_text(day, plan, None, None)
    assert "קובעים את נקודת ההתחלה" in first


def test_evening_text_shows_records_first(store):
    day = store.day("2026-10-25")
    txt = reminders.evening_text(day, None, dt.date(2026, 10, 26), False, None,
                                 {"prs": ["לחיצת חזה 62.5×8 (קודם 60×8)"], "streak": "רצף: 3 ימים סגורים. שומרים עליו."})
    assert txt.splitlines()[0] == "🏆 שיא: לחיצת חזה 62.5×8 (קודם 60×8)"
    assert "רצף: 3 ימים סגורים" in txt


def test_tidy_done_workout_drops_empty_rows_and_groups_supersets():
    w = {"done": True, "exercises": [
        {"name": "פולי עליון", "muscle": "back", "ss": "C", "sets": [{"w": 60, "r": 10}]},
        {"name": "כפיפות צד", "muscle": "abs", "ss": "C", "sets": [{"w": None, "r": None}] * 3},
        {"name": "חתירה במכונה", "muscle": "back", "ss": "D", "sets": [{"w": 67.5, "r": 10}, {"w": None, "r": None}]},
        {"name": "יד קדמית", "muscle": "biceps", "sets": [{"w": None, "r": None}]},
        {"name": "הרחקה אחורית בישיבה כפופה", "muscle": "shoulders", "ss": "C", "sets": [{"w": 8, "r": 10}]},
    ]}
    assert domain.tidy_done_workout(w) == 2
    assert [(e["name"], e.get("ss")) for e in w["exercises"]] == [
        ("פולי עליון", "C"), ("הרחקה אחורית בישיבה כפופה", "C"), ("חתירה במכונה", "D")]
    assert len(w["exercises"][2]["sets"]) == 1


def test_find_exercise_letters_and_empty_first():
    exs = [{"name": "יד קדמית", "ss": "E", "sets": [{"w": 30, "r": 8}]},
           {"name": "יד קדמית", "sets": [{"w": None, "r": None}]}]
    assert domain.find_exercise(exs, "יד קדמית", "-") == 1
    assert domain.find_exercise(exs, "יד קדמית", "E") == 0
    assert domain.find_exercise(exs, "יד קדמית", None) == 1  # the empty row first
    assert domain.find_exercise(exs, "יד קדמית", "C") is None


def _train_day(ds, sets=True, **w):
    from coach import domain
    d = domain.blank_day(ds)
    d["type"] = "train"
    if sets:
        d["workout"]["exercises"] = [{"name": "פולי עליון", "muscle": "back", "sets": [{"w": 67.5, "r": 10}]}]
    d["workout"].update(w)
    return d


def test_streak_breaks_on_skipped_or_unconfirmed_plan_workout():
    import datetime as dt
    from coach import domain
    days = {
        "2026-10-20": _train_day("2026-10-20", done=True),
        "2026-10-21": _train_day("2026-10-21", done=True),
        "2026-10-22": _train_day("2026-10-22", done=True),
        "2026-10-23": _train_day("2026-10-23", fromPlan=True),  # logged by the evening job, never confirmed
    }
    rest = domain.blank_day("2026-10-24")
    rest["type"] = "rest"
    days["2026-10-24"] = rest
    assert domain.streaks(days, dt.date(2026, 10, 25))["train"] == 0
    days["2026-10-23"] = _train_day("2026-10-23", sets=False, skipped=True)
    assert domain.streaks(days, dt.date(2026, 10, 25))["train"] == 0
    days["2026-10-23"]["workout"]["done"] = True  # they trained after all and marked it
    assert domain.streaks(days, dt.date(2026, 10, 25))["train"] == 4


def test_skip_workout_tool_keeps_flag_through_save(store):
    import asyncio
    from coach import domain
    from coach.tools import ToolRunner
    r = ToolRunner(store, None)
    out, err = asyncio.run(r.run("skip_workout", {"date": "2026-10-25", "reason": "עייף"}))
    assert not err
    d = store.day("2026-10-25")
    assert d["workout"]["skipped"] and not domain.has_workout(d)
    assert "אין אימון היום: עייף" in d["workout"]["notes"]
