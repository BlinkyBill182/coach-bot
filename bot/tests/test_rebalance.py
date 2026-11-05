from coach import domain, reminders


def _day(store, ds="2026-10-19"):
    d = store.day(ds)
    return d, domain.day_plan(store.schedule(), d)


def _left_after_plan(d, plan, now_min):
    fut = domain._future_meals(plan, d, now_min)
    t = domain.totals(d)
    g = domain.TARGETS[d["type"]]
    return g["k"] - t["k"] - sum(domain.kcal(x) for x in fut), g["p"] - t["p"] - sum(x["p"] for x in fut)


def test_on_plan_day_needs_no_changes(store):
    d, plan = _day(store)
    d["items"] = [{"n": "ארוחת בוקר", "p": 28, "f": 29, "c": 49}]
    _, changes = domain.rebalance(d, plan, 10 * 60)
    assert changes == []


def test_small_breakfast_gets_topped_up(store):
    d, plan = _day(store)
    d["items"] = [{"n": "קפה וחטיף", "p": 5, "f": 5, "c": 20}]  # instead of the 12:00 breakfast
    new, changes = domain.rebalance(d, plan, 12 * 60 + 30)
    assert changes
    k_gap, p_gap = _left_after_plan(d, new, 12 * 60 + 30)
    assert abs(k_gap) <= 150 + 100 and p_gap <= 15


def test_big_unplanned_meal_drops_snacks(store):
    d, plan = _day(store)
    d["items"] = [{"n": "ארוחת בוקר", "p": 28, "f": 29, "c": 49}, {"n": "פיצה משפחתית", "p": 45, "f": 60, "c": 160}]
    new, changes = domain.rebalance(d, plan, 12 * 60)
    assert any("מדלגים" in c for c in changes)
    sweet = next(x for x in new if x["n"] == "מתוק מתוכנן")
    assert "p" not in sweet and sweet["kind"] == "other"


def test_skip_marks_meal_and_drops_macros():
    x = {"t": "14:00", "n": "ארוחת צהריים", "d": "עוף ואורז", "kind": "meal", "p": 46, "f": 25, "c": 78}
    domain.mark_skipped(x, "הוחלף בשווארמה")
    assert x == {"t": "14:00", "n": "ארוחת צהריים", "d": "הוחלף בשווארמה", "kind": "other"}


def test_behind_counts_due_meals(store):
    d, plan = _day(store)
    gap = domain.behind(d, plan, 16 * 60)
    assert gap["logged"] == 0 and gap["k"] > 1000


def test_follow_ups(store):
    d, plan = _day(store)
    keys = {r.key: r for r in reminders.follow_ups(d, plan)}
    assert keys["2026-10-19:weigh2"].at == 9 * 60  # an hour after the 08:00 wake
    assert "2026-10-19:creatine2" not in keys  # one dose with breakfast, no evening nudge
    d["weight"] = 72.0
    assert reminders.follow_ups(d, plan) == []


def test_afternoon_text():
    txt = reminders.afternoon_text({"k": 700, "p": 50, "logged": 2}, ["17:00 ביניים: הוספתי 3 תמרים"], {"k": 1500, "p": 90})
    assert "מאחור בכ-700" in txt and "3 תמרים" in txt
    assert "עוד לא נרשם אוכל" in reminders.afternoon_text({"k": 0, "p": 0, "logged": 0}, [], {"k": 0, "p": 0})


def _dinner_day(items):
    from coach import domain
    d = domain.blank_day("2026-10-20")
    d["type"] = "train"
    d["items"] = items
    plan = [{"t": "19:30", "n": "ארוחת ערב", "d": "תבשיל עוף", "kind": "meal", "p": 40, "f": 11, "c": 97}]
    return d, plan


def test_rebalance_does_not_pile_up_topups():
    from coach import domain
    d, plan = _dinner_day([{"n": "בוקר", "p": 60, "f": 30, "c": 150}])
    runs = []
    for _ in range(5):  # the user logs food several times while still behind
        plan, _ = domain.rebalance(d, plan, 12 * 60)
        plan = domain.norm_schedule({"train": plan})["train"]  # saved and loaded like the store does
        runs.append((plan[0]["d"], plan[0]["p"], plan[0].get("k")))
    assert len(set(runs)) == 1  # same day, same answer: nothing stacks up
    assert plan[0]["base"]["p"] == 40


def test_rebalance_skips_a_meal_that_no_longer_fits_and_can_bring_it_back():
    from coach import domain
    full = [{"n": "הכול", "p": 170, "f": 70, "c": 380}]  # about 2,830 kcal of 2,850
    d, plan = _dinner_day(full)
    plan, changes = domain.rebalance(d, plan, 19 * 60)
    assert plan[0]["kind"] == "other" and plan[0]["auto"]
    assert not domain.meal_still_needed(d, {"p": 40, "f": 11, "c": 97})
    d["items"] = [{"n": "בוקר", "p": 60, "f": 30, "c": 150}]  # a wrong log was removed: behind again
    plan, _ = domain.rebalance(d, domain.norm_schedule({"train": plan})["train"], 19 * 60)
    assert plan[0]["kind"] == "meal" and plan[0]["p"] >= 40


def test_skip_by_ben_stays_skipped():
    from coach import domain
    d, plan = _dinner_day([{"n": "בוקר", "p": 60, "f": 30, "c": 150}])
    domain.mark_skipped(plan[0], "אכל בחוץ")
    plan, _ = domain.rebalance(d, plan, 12 * 60)
    assert plan[0]["kind"] == "other" and plan[0]["d"] == "אכל בחוץ"


def test_big_gap_never_stacks_the_same_topup():
    from coach import domain
    d, plan = _dinner_day([])  # nothing eaten, one meal left: about 2,200 kcal missing
    plan, _ = domain.rebalance(d, plan, 9 * 60)
    desc = plan[0]["d"]
    for name, *_ in domain.TOPUPS:
        assert desc.count(name) <= 1
    assert desc.count(domain.SCOOP[0]) <= 1


def test_replace_planned_meal_keeps_macros_and_pins(store):
    import asyncio
    from coach.tools import ToolRunner
    r = ToolRunner(store, None)
    out, err = asyncio.run(r.run("replace_planned_meal", {
        "date": "2026-10-25", "item": "ארוחת ערב", "description": "דייט: עוף עם אורז (הערכה)",
        "p": 40, "f": 25, "c": 80, "time": "20:30"}))
    assert not err, out
    from coach import domain
    d = store.day("2026-10-25")
    x = next(x for x in domain.day_plan(store.schedule(), d) if x["n"] == "ארוחת ערב")
    assert x["kind"] == "meal" and x["p"] == 40 and x["t"] == "20:30" and x["pinned"]


def test_checklist_passes_workout_on_a_skipped_day():
    from coach import domain
    d = domain.blank_day("2026-10-25")
    d["type"] = "train"
    d["workout"]["skipped"] = True
    w = next(c for c in domain.checklist(d) if c["label"] == "אימון")
    assert w["ok"]


def test_taking_back_topups_is_saved(store, monkeypatch):
    import asyncio
    import datetime as dt
    from coach import domain
    from coach.tools import ToolRunner
    monkeypatch.setattr(domain, "now", lambda: dt.datetime(2026, 10, 25, 9, 0, tzinfo=domain.TZ))
    ds = "2026-10-25"
    d = store.day(ds)
    d["type"] = "train"
    d["plan"] = [{"t": "13:00", "n": "צהריים", "d": "עוף", "kind": "meal", "p": 50, "f": 20, "c": 100},
                 {"t": "20:00", "n": "ערב", "d": "דייט", "kind": "other"}]
    store.save_day(d)
    r = ToolRunner(store, None)
    r.rebalance(ds)  # dinner has no macros yet: top-ups land on lunch
    assert "+" in domain.day_plan(store.schedule(), store.day(ds))[0]["d"]
    asyncio.run(r.run("replace_planned_meal", {"date": ds, "item": "ערב", "description": "דייט (הערכה)",
                                               "p": 105, "f": 55, "c": 285}))
    lunch = domain.day_plan(store.schedule(), store.day(ds))[0]
    assert lunch["d"] == "עוף" and lunch["p"] == 50  # top-ups taken back and saved
