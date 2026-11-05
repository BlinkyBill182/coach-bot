import asyncio
import json

import pytest

from coach.gcal import Calendar
from coach.tools import ToolRunner


def run(runner, name, args):
    out, err = asyncio.run(runner.run(name, args))
    assert not err, out
    return json.loads(out)


def test_store_versions_and_seq(store):
    s0 = store.seq("log")
    assert store.set("log/2026-10-20", {"date": "2026-10-20"}) == 1
    assert store.update("log/2026-10-20", {"weight": 70}) == 2
    assert store.get("log/2026-10-20")["data"] == {"date": "2026-10-20", "weight": 70}
    store.delete("log/2026-10-20")
    assert store.get("log/2026-10-20") is None and store.seq("log") > s0
    with pytest.raises(ValueError):
        store.set("log", {})
    with pytest.raises(KeyError):
        store.update("log/2026-01-01", {"a": 1})


def test_meals_weight_and_close(store, tmp_path):
    r = ToolRunner(store, Calendar(str(tmp_path / "none.json")))
    out = run(r, "add_meals", {"date": "2026-10-18", "items": [{"name": "ביצים", "protein": 80, "fat": 65, "carbs": 60}]})
    assert out["totals"]["p"] == 80 and out["left"]["p"] == 75
    run(r, "set_weight", {"date": "2026-10-18", "kg": 72.34})
    run(r, "set_creatine", {"date": "2026-10-18", "taken": True})
    day = run(r, "get_day", {"date": "2026-10-18"})
    assert day["weight"] == 72.3 and day["creatine_5g_taken"] is True
    run(r, "close_day", {"date": "2026-10-18"})
    assert store.day("2026-10-18")["closed"]


def test_plan_workout_with_repeated_exercises(store, tmp_path):
    r = ToolRunner(store, Calendar(str(tmp_path / "none.json")))
    out = run(r, "load_workout_plan", {"date": "2026-10-19", "with_reps": False})
    exs = out["workout"]["exercises"]
    assert len(exs) == 14  # day 5: four supersets of three, plus biceps and triceps
    assert sum(1 for e in exs if e["name"] == "כפיפות צד") == 4
    run(r, "load_workout_plan", {"date": "2026-10-19", "with_reps": False})  # no duplicates
    assert len(store.day("2026-10-19")["workout"]["exercises"]) == 14
    run(r, "log_exercise", {"date": "2026-10-19", "name": "כפיפות צד", "superset": "C", "sets": [{"w": 20, "r": 12}] * 3})
    side = [e for e in store.day("2026-10-19")["workout"]["exercises"] if e["name"] == "כפיפות צד"]
    assert [e["ss"] for e in side if e["sets"][0]["r"]] == ["C"]


def test_progression_flows_to_next_session(store, tmp_path):
    r = ToolRunner(store, Calendar(str(tmp_path / "none.json")))
    run(r, "log_exercise", {"date": "2026-10-21", "name": "לחיצת חזה", "superset": "A", "sets": [{"w": 60, "r": 8}] * 3})
    h = run(r, "get_exercise_history", {"name": "לחיצת חזה"})
    assert h["next"]["w"] == 62.5 and h["next"]["up"]
    out = run(r, "log_exercise", {"date": "2026-10-25", "name": "לחיצת חזה", "superset": "A", "sets": [{"w": 62.5, "r": 8}]})
    assert out["personal_record"]


def test_set_plan_time_and_memory(store, tmp_path):
    r = ToolRunner(store, Calendar(str(tmp_path / "none.json")))
    out = run(r, "set_plan_time", {"date": "2026-10-19", "item": "ארוחת צהריים", "time": "15:00"})
    assert any(x["n"] == "ארוחת צהריים" and x["t"] == "15:00" for x in out["plan"])
    run(r, "remember", {"fact": "בימי שלישי עובד עד 18:00"})
    assert store.profile_notes()[0]["text"].startswith("בימי שלישי")
    run(r, "forget", {"index": 0})
    assert store.profile_notes() == []


class _FakeCal:
    enabled = True

    def __init__(self):
        self.events, self.created, self.deleted, self.moved = [], [], [], []

    def events_on(self, d):
        return list(self.events)

    def busy_minutes(self, events, d):
        return []

    def is_gym(self, e):
        return "חדר כושר" in e["title"]

    def gym_event(self, events):
        return next((e for e in events if self.is_gym(e) and not e["all_day"]), None)

    def create_gym(self, d, start, dur, title_line, location="חדר הכושר", leave_minutes=20):
        import datetime as dt
        from coach import domain
        s = dt.datetime.combine(d, dt.time(start // 60, start % 60), domain.TZ)
        e = {"id": "new", "title": "💪🏾 חדר כושר", "all_day": False, "start": s, "end": s + dt.timedelta(minutes=dur),
             "description": title_line, "location": location, "online": False}
        self.created.append(e)
        self.events.append(e)
        return e

    def move_gym(self, event, d, start, dur, title_line, lines, leave_minutes=20):
        self.moved.append((event["id"], start, dur, title_line))

    def delete_event(self, eid):
        self.deleted.append(eid)
        self.events = [e for e in self.events if e["id"] != eid]


def test_set_day_program_makes_a_rest_day_a_training_day(store, monkeypatch):
    import asyncio
    from coach import domain, eta
    from coach.tools import ToolRunner

    async def no_live(*a, **k):
        return {}
    monkeypatch.setattr(eta, "live_travel", no_live)
    cal = _FakeCal()
    r = ToolRunner(store, cal)
    out, err = asyncio.run(r.run("set_day_program", {"date": "2026-10-28", "type": "train", "cycle_day": 5,
                                                     "start": "11:00", "end": "12:15"}))
    assert not err, out
    d = store.day("2026-10-28")
    assert d["type"] == "train" and d["cycleDay"] == 5
    assert d["workout"]["exercises"] and all(not domain.done_sets(e) for e in d["workout"]["exercises"])
    assert cal.created and cal.created[0]["start"].hour == 11
    assert cal.moved and cal.moved[0][1] == 660 and cal.moved[0][2] == 75
    train = next(x for x in d["plan"] if x["kind"] == "train")
    assert train["t"] == "11:00"


def test_set_day_program_rest_reports_the_gym_event_then_delete(store, monkeypatch):
    import asyncio
    import json
    from coach import eta
    from coach.tools import ToolRunner

    async def no_live(*a, **k):
        return {}
    monkeypatch.setattr(eta, "live_travel", no_live)
    cal = _FakeCal()
    cal.create_gym(__import__("datetime").date(2026, 10, 30), 480, 90, "Day 6")
    r = ToolRunner(store, cal)
    out, err = asyncio.run(r.run("set_day_program", {"date": "2026-10-30", "type": "rest"}))
    assert not err, out
    assert "gym_event_left" in json.loads(out)
    assert store.day("2026-10-30")["type"] == "rest"
    out, err = asyncio.run(r.run("delete_gym_event", {"date": "2026-10-30"}))
    assert not err and cal.deleted == ["new"]
