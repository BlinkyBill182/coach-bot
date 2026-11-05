import datetime as dt

from coach import domain


def test_cycle_anchor():
    assert domain.planned_cycle(dt.date(2026, 10, 21)) == 1
    assert domain.planned_cycle(dt.date(2026, 10, 18)) == 4
    assert domain.planned_cycle(dt.date(2026, 10, 19)) == 5
    assert domain.planned_cycle(dt.date(2026, 10, 27)) == 1


def test_checklist_rest_day_complete():
    d = domain.blank_day("2026-10-18")
    d.update(weight=72.3, cr=[True, True], items=[{"n": "x", "p": 160, "f": 80, "c": 300}])
    assert all(c["ok"] for c in domain.checklist(d))


def test_checklist_train_flags_missing_reps():
    d = domain.blank_day("2026-10-19")
    d["workout"]["exercises"] = [{"name": "a", "muscle": "chest", "sets": [{"w": 60, "r": None}]}]
    w = [c for c in domain.checklist(d) if c["label"] == "אימון"][0]
    assert not w["ok"] and "בלי משקל או חזרות" in w["det"]


def test_suggest_progression():
    lib = {"sets": 3, "lo": 6, "hi": 8}
    prev = {"date": "x", "sets": [{"w": 60, "r": 8}] * 3}
    assert domain.suggest("p", "chest", lib, prev) == {"w": 62.5, "r": 6, "up": True, "bw": False}
    prev = {"date": "x", "sets": [{"w": 60, "r": 8}, {"w": 60, "r": 7}, {"w": 60, "r": 6}]}
    assert domain.suggest("p", "chest", lib, prev)["w"] == 60
    legs = {"sets": 3, "lo": 8, "hi": 12}
    assert domain.suggest("lp", "quads", legs, {"date": "x", "sets": [{"w": 100, "r": 12}] * 3})["w"] == 105


def test_find_slot_avoids_meetings():
    d = dt.date(2026, 10, 19)  # Monday
    assert domain.find_slot([], d) == 540  # mornings from 09:00
    # meeting 10:30-12:00 -> every morning start clashes; 12:30 needs 12:10 free -> fits
    assert domain.find_slot([(630, 720)], d) == 750
    # meeting 09:00-10:00 -> first free start is 10:10+ -> 10:30
    assert domain.find_slot([(540, 600)], d) == 630
    # Friday closes at 17:00
    fri = dt.date(2026, 10, 23)
    assert domain.find_slot([(600, 1000)], fri) is None


def test_build_plan_moves_workout_and_meals():
    tpl = [
        {"t": "09:00", "n": "קימה", "d": "", "kind": "wake"},
        {"t": "10:30", "n": "לפני האימון", "d": "בננה", "kind": "meal", "p": 1, "f": 0, "c": 27},
        {"t": "11:00", "n": "אימון", "d": "", "kind": "train"},
        {"t": "12:45", "n": "אחרי האימון", "d": "", "kind": "meal", "p": 13, "f": 2, "c": 54},
        {"t": "14:00", "n": "ארוחת צהריים", "d": "", "kind": "meal", "p": 46, "f": 25, "c": 78},
    ]
    plan = domain.build_plan(tpl, 750, 90, [(840, 900)])  # workout 12:30, meeting 14:00-15:00
    by = {x["n"]: x["t"] for x in plan}
    assert by["אימון"] == "12:30" and by["לפני האימון"] == "12:00" and by["אחרי האימון"] == "14:15"
    assert by["ארוחת צהריים"] == "15:00"  # out of the meeting and 45+ min after the post-workout shake
    assert [x["t"] for x in plan] == sorted(x["t"] for x in plan)


def test_workout_lines_groups_supersets(store):
    items, plans = store.library()
    lines = domain.workout_lines({}, items, plans, 1, "2026-10-21")
    assert lines[0].startswith("A: לחיצת חזה")
    assert lines[1].startswith("B: ")
    assert lines[-1].startswith("• יד אחורית")


def test_build_plan_keeps_lunch_away_from_late_workout(store):
    tpl = store.schedule()["train"]
    plan = domain.build_plan(tpl, 870, 90, [(720, 840)])  # meeting 12:00-14:00, workout 14:30
    t = {x["n"]: domain.minutes(x["t"]) for x in plan}
    lunch = t["ארוחת צהריים"]
    assert not 870 - 90 <= lunch < 870 + 90 + 15 and not 720 <= lunch < 840
    meals = sorted(domain.minutes(x["t"]) for x in plan if x["kind"] == "meal" and x["n"] not in ("לפני האימון", "אחרי האימון"))
    assert all(b - a >= 75 for a, b in zip(meals, meals[1:]))


def test_wake_moves_earlier_for_morning_workout(store):
    plan = domain.build_plan(store.schedule()["train"], 540, 90, [])
    t = {x["n"]: x["t"] for x in plan}
    assert t["אימון"] == "09:00" and t["קימה ושקילה"] == "08:00" and t["לפני האימון"] == "08:30"


def test_meal_before_meeting_after_morning_workout(store):
    plan = domain.build_plan(store.schedule()["train"], 540, 90, [(720, 840)])  # 09:00 workout, meeting 12-14
    t = {x["n"]: x["t"] for x in plan}
    assert t["ארוחת בוקר"] == "11:45" and t["ארוחת צהריים"] == "14:30"


def test_creatine_one_dose_completes_checklist():
    d = domain.blank_day("2026-10-18")
    c = next(x for x in domain.checklist(d) if x["label"] == "קריאטין")
    assert not c["ok"] and c["det"] == "0 / 5 ג׳"
    d["cr"] = [True, False]
    c = next(x for x in domain.checklist(d) if x["label"] == "קריאטין")
    assert c["ok"] and c["det"] == "5 / 5 ג׳"


def test_suggest_uses_the_load_of_most_sets():
    lib = {"lo": 12, "hi": 20, "sets": 3}
    prev = {"sets": [{"w": 50, "r": 6}, {"w": 35, "r": 12}, {"w": 35, "r": 8}]}
    assert domain.suggest("תאומים בישיבה", "calves", lib, prev) == {"w": 35, "r": 9, "up": False, "bw": False}


def test_suggest_keeps_load_at_the_gym_max():
    lib = {"lo": 10, "hi": 15, "sets": 3, "max": 24.0}
    prev = {"sets": [{"w": 24, "r": 20}, {"w": 24, "r": 20}, {"w": 24, "r": 15}]}
    assert domain.suggest("כפיפות צד", "abs", lib, prev)["cap"] is True
    assert domain.suggest("כפיפות צד", "abs", lib, prev)["w"] == 24
    assert domain.suggest("כפיפות צד", "abs", dict(lib, max=None), prev)["w"] == 26.5


def test_norm_library_keeps_max():
    items, _ = domain.norm_library({"items": [{"id": "sb", "name": "כפיפות צד", "muscle": "abs", "max": 24}]})
    assert items[0]["max"] == 24.0


def test_program_follows_the_week_from_2026_10_28():
    import datetime as dt
    days = {dt.date(2026, 10, 25) + dt.timedelta(days=i): None for i in range(14)}
    got = {d.isoformat(): domain.planned_cycle(d) for d in days}
    # before the switch: the old 6-day cycle (21.10 = day 1)
    assert got["2026-10-26"] == 6 and got["2026-10-27"] == 1
    # from Wed 28.10: Sun push 1, Mon pull 2, Tue legs 3, Wed rest 4, Thu upper 5, Fri lower 6, Sat rest 7
    assert [got[f"2026-11-0{i}"] for i in range(1, 8)] == [1, 2, 3, 4, 5, 6, 7]
    assert got["2026-10-28"] == 4 and got["2026-10-31"] == 7
    assert domain.day_type(None, dt.date(2026, 10, 31)) == "rest"
    assert domain.day_type(None, dt.date(2026, 10, 30)) == "train"
    assert domain.CYCLE[6] == "פלג תחתון"


def test_norm_day_accepts_day_7():
    d = domain.norm_day("2026-10-31", {"cycleDay": 7, "type": "rest"})
    assert d["cycleDay"] == 7
