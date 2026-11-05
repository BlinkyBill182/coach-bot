import datetime as dt

import pytest

from coach import alarms, domain


def at(s):
    return alarms.parse_local(s)


def test_one_off_sent_once(store):
    now = at("2026-10-21 17:00")
    r = alarms.add(store, "2026-10-21 17:02", "לקנות עוף", now=now)
    assert r["time"] == "2026-10-21 17:02"
    assert alarms.take_due(store, at("2026-10-21 17:01")) == []
    sent = alarms.take_due(store, at("2026-10-21 17:02"))
    assert [a["text"] for a in sent] == ["לקנות עוף"]
    assert alarms.take_due(store, at("2026-10-21 17:03")) == []  # never twice
    assert alarms.upcoming(store) == []


def test_missed_by_more_than_15_min_is_skipped(store):
    alarms.add(store, "2026-10-21 17:00", "ישן", now=at("2026-10-21 16:00"))
    alarms.add(store, "2026-10-21 17:10", "חדש", now=at("2026-10-21 16:00"))
    sent = alarms.take_due(store, at("2026-10-21 17:20"))  # back after a restart
    assert [a["text"] for a in sent] == ["חדש"]
    assert alarms.upcoming(store) == []


def test_daily_moves_to_next_day_and_keeps_wall_time(store):
    alarms.add(store, "2026-10-24 18:00", "קריאטין", "daily", now=at("2026-10-24 12:00"))
    assert alarms.take_due(store, at("2026-10-24 18:00"))
    # Israel leaves summer time on Sunday 2026-10-25; the reminder stays at 18:00 local.
    assert alarms.upcoming(store)[0]["time"] == "2026-10-25 18:00"


def test_weekdays_skip_friday_and_saturday(store):
    alarms.add(store, "2026-10-22 08:00", "שקילה", "weekdays", now=at("2026-10-22 07:00"))  # Thursday
    alarms.take_due(store, at("2026-10-22 08:00"))
    assert alarms.upcoming(store)[0]["time"] == "2026-10-25 08:00"  # Sunday


def test_repeat_set_in_the_past_rolls_forward(store):
    r = alarms.add(store, "2026-10-21 08:00", "שקילה", "daily", now=at("2026-10-21 12:00"))
    assert r["time"] == "2026-10-22 08:00"


def test_past_one_off_rejected_and_cancel(store):
    with pytest.raises(ValueError):
        alarms.add(store, "2026-10-21 08:00", "x", now=at("2026-10-21 12:00"))
    r = alarms.add(store, "2026-10-21 20:00", "y", now=at("2026-10-21 12:00"))
    alarms.cancel(store, r["id"])
    assert alarms.take_due(store, at("2026-10-21 20:00")) == []
    with pytest.raises(ValueError):
        alarms.cancel(store, r["id"])


def test_reminders_survive_a_new_store_on_the_same_file(tmp_path):
    from coach.store import Store
    path = str(tmp_path / "c.db")
    alarms.add(Store(path), "2026-10-21 20:00", "שורד ריסטארט", now=at("2026-10-21 12:00"))
    assert alarms.upcoming(Store(path))[0]["text"] == "שורד ריסטארט"


def test_retime_away_uses_new_travel_time():
    plan = [{"t": "20:00", "n": "ארוחת ערב", "kind": "meal", "p": 38, "f": 3, "c": 56,
             "away": "אירוע אשדוד", "leave": "16:40"}]
    ev = [{"title": "אירוע אשדוד", "all_day": False, "location": "אשדוד",
           "start": dt.datetime(2026, 10, 21, 17, 0, tzinfo=domain.TZ),
           "end": dt.datetime(2026, 10, 21, 21, 0, tzinfo=domain.TZ), "online": False}]
    travel = domain.norm_travel({"default": 20, "places": [{"match": "אשדוד", "minutes": 40}]})
    wins = domain.away_windows(ev, dt.date(2026, 10, 21), travel)
    assert domain.retime_away(plan, wins) == {"אירוע אשדוד": "16:20"}
    assert plan[0]["leave"] == "16:20"
