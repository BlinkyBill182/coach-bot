import asyncio
import json

import pytest

from coach import domain
from coach.tools import ToolRunner


def run(r, name, args):
    out, err = asyncio.run(r.run(name, args))
    return json.loads(out), err


def test_set_targets_needs_a_preview_and_a_later_yes(store):
    first = ToolRunner(store, None)
    out, err = run(first, "set_targets", {"day_type": "train", "kcal": 3000, "confirm": True})
    assert err and "preview" in out["error"]
    out, err = run(first, "set_targets", {"day_type": "train", "kcal": 3000, "confirm": False})
    assert not err and out["needs_ben_yes"] and out["preview"]["after"]["k"] == 3000
    out, err = run(first, "set_targets", {"day_type": "train", "kcal": 3000, "confirm": True})
    assert err and "hasn't answered" in out["error"]  # same turn: the user hasn't said yes yet
    assert domain.TARGETS["train"]["k"] == 2850
    later = ToolRunner(store, None)  # their next message
    out, err = run(later, "set_targets", {"day_type": "train", "kcal": 2950, "confirm": True})
    assert err  # different values than the preview they saw
    out, err = run(later, "set_targets", {"day_type": "train", "kcal": 3000, "confirm": True})
    assert not err and out["applied"]
    assert domain.TARGETS["train"]["k"] == 3000
    assert store.data("config/program")["targets"]["train"]["k"] == 3000
    assert any("שינוי קבוע" in n["text"] for n in store.profile_notes())


def test_set_week_moves_a_program_day(store):
    run(ToolRunner(store, None), "set_week", {"days": {"wed": 5, "thu": 4}, "confirm": False})
    out, err = run(ToolRunner(store, None), "set_week", {"days": {"wed": 5, "thu": 4}, "confirm": True})
    assert not err, out
    import datetime as dt
    assert domain.planned_cycle(dt.date(2026, 11, 4)) == 5  # a Wednesday
    assert domain.planned_cycle(dt.date(2026, 11, 5)) == 4  # a Thursday
    assert domain.day_type(None, dt.date(2026, 11, 5)) == "rest"


def test_update_exercise_plan_adds_items_and_keeps_other_days(store):
    store.set("config/exercises", {"items": [
        {"id": "a", "name": "לחיצת חזה", "muscle": "chest", "days": [1], "sets": 3, "lo": 6, "hi": 8},
        {"id": "b", "name": "פולי עליון", "muscle": "back", "days": [2], "sets": 3, "lo": 6, "hi": 8}],
        "plans": {"1": [{"name": "לחיצת חזה", "ss": "A"}], "2": [{"name": "פולי עליון"}]}})
    args = {"program_day": 1, "confirm": False, "exercises": [
        {"name": "לחיצת חזה", "superset": "A"}, {"name": "מקבילים", "superset": "A", "muscle": "triceps", "lo": 8, "hi": 12}]}
    out, err = run(ToolRunner(store, None), "update_exercise_plan", args)
    assert not err and out["preview"]["new_library_items"] == ["מקבילים"]
    out, err = run(ToolRunner(store, None), "update_exercise_plan", {**args, "confirm": True})
    assert not err, out
    items, plans = domain.norm_library(store.data("config/exercises"))
    assert [r["name"] for r in plans[1]] == ["לחיצת חזה", "מקבילים"]
    assert [r["name"] for r in plans[2]] == ["פולי עליון"]
    assert domain.lib_for(items, "מקבילים")["days"] == [1]


def test_update_meal_template_previews_totals(store):
    entries = [{"t": "08:00", "n": "קימה", "kind": "wake"},
               {"t": "09:00", "n": "בוקר", "kind": "meal", "p": 40, "f": 20, "c": 80}]
    out, err = run(ToolRunner(store, None), "update_meal_template", {"day_type": "rest", "entries": entries, "confirm": False})
    assert not err and out["preview"]["totals"]["p"] == 40
    out, err = run(ToolRunner(store, None), "update_meal_template", {"day_type": "rest", "entries": entries, "confirm": True})
    assert not err
    assert [x["n"] for x in store.schedule()["rest"]] == ["קימה", "בוקר"]
