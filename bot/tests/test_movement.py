import asyncio
import datetime as dt
import importlib
import json
from types import SimpleNamespace

import pytest

from coach import domain, movement as mv
from coach.tools import ToolRunner

HOME = (32.0800, 34.7800)
TZ = domain.TZ
DAY = dt.date(2026, 11, 7)  # a Saturday, a rest day


def ts(h, m=0, d=DAY):
    return dt.datetime.combine(d, dt.time(h, m), TZ).timestamp()


def track(points, until=None):
    """points: (hour, minute, lat offset in metres, accuracy)."""
    s = None
    for h, m, north, acc in points:
        s = mv.add_fix(s, HOME[0] + north / 111320, HOME[1], ts(h, m), acc, until or ts(23, 0))
    return s


def home_day(start=9, end=17, jitter=25, acc=20):
    return track([(h, m, jitter if (h + m) % 2 else -jitter, acc) for h in range(start, end) for m in (0, 30)] + [(end, 0, 0, acc)])


# ---------- what the location says ----------

def test_a_day_at_home_with_gps_jitter_is_still():
    s = home_day()
    assert mv.status(s, ts(17, 0)) == "still"
    assert s["max_m"] < mv.LEAVE_M


def test_leaving_is_moved():
    s = track([(9, 0, 0, 20), (10, 0, 0, 20), (11, 0, 500, 20), (11, 10, 1000, 20), (12, 0, 0, 20)])
    assert mv.status(s, ts(17, 0)) == "moved"


def test_moved_needs_no_full_coverage():
    s = track([(14, 0, 0, 20), (14, 10, 800, 20), (14, 20, 1500, 20)])
    assert mv.status(s, ts(14, 30)) == "moved"


def test_a_walk_inside_the_radius_isnt_seen():  # a known limit, and the offer says so
    s = track([(h, m, 250 if m else 0, 10) for h in range(9, 17) for m in (0, 30)] + [(17, 0, 0, 10)])
    assert s["max_m"] <= mv.LEAVE_M and mv.status(s, ts(17)) == "still"


def test_one_far_point_is_a_glitch():
    s = track([(h, 0, 0, 20) for h in range(9, 17)] + [(17, 0, 900, 20)])
    assert s["far_n"] == 1 and mv.status(s, ts(17, 0)) == "still"


def test_inaccurate_fixes_say_nothing_about_distance():
    s = track([(h, 0, 2000 if h % 2 else 0, 500) for h in range(9, 12)] + [(h, 0, 0, 20) for h in range(12, 18)])
    assert s["max_m"] == 0 and s["far_n"] == 0


@pytest.mark.parametrize("s,now", [
    (None, 17),  # never shared
    (track([(9, 0, 0, 20), (9, 30, 0, 20)]), 17),  # too few points, long silence
    (track([(13, 0, 0, 20), (14, 0, 0, 20), (15, 0, 0, 20), (16, 0, 0, 20)]), 17),  # started too late
    (track([(h, 0, 0, 20) for h in range(9, 14)]), 17),  # silent since 13:00
    (track([(9, 0, 0, 20), (10, 0, 0, 20), (14, 0, 0, 20), (15, 0, 0, 20), (16, 0, 0, 20)]), 17),  # a 4-hour hole
])
def test_without_coverage_it_says_unknown(s, now):
    assert mv.status(s, ts(now)) == "unknown"


def test_a_one_off_location_is_not_live_sharing():
    s = None
    for h in (9, 11, 13, 15, 16):
        s = mv.add_fix(s, HOME[0], HOME[1], ts(h), 20)
    assert mv.status(s, ts(17)) == "unknown"


def test_sharing_that_ended_is_unknown():
    s = track([(h, 0, 0, 20) for h in range(9, 15)], until=ts(23))
    s = mv.add_fix(s, HOME[0], HOME[1], ts(15), 20, None, ended=True)
    assert mv.status(s, ts(17)) == "unknown"


def test_a_later_live_update_cancels_an_ended_mark():
    s = track([(h, 0, 0, 20) for h in range(9, 15)])
    s = mv.add_fix(s, HOME[0], HOME[1], ts(15), 20, None, ended=True)
    s = mv.add_fix(s, HOME[0], HOME[1], ts(16, 30), 20, ts(23))
    assert s["ended"] is None and mv.status(s, ts(17)) == "still"


def test_an_8_hour_share_covers_17_00_but_not_the_evening():
    until = ts(9) + 8 * 3600
    s = track([(h, 0, 0, 20) for h in range(9, 18)], until=until)
    assert mv.status(s, ts(17)) == "still"
    assert mv.status(s, ts(21, 30)) == "unknown"


def test_yesterdays_state_says_nothing_today():
    assert mv.status(home_day(), ts(9, d=DAY + dt.timedelta(days=1))) == "unknown"


# ---------- what is kept ----------

def test_midnight_forgets_the_coordinates_and_keeps_the_summary(store):
    for h in range(9, 18):
        mv.record(store, HOME[0], HOME[1], ts(h), 20, ts(23))
    assert mv.state(store) is not None
    mv.roll_over(store, DAY)
    assert mv.state(store) is not None  # still today
    mv.roll_over(store, DAY + dt.timedelta(days=1))
    assert mv.state(store) is None
    kept = store.get_meta(f"move:{DAY.isoformat()}")
    assert json.loads(kept)["status"] == "still" and json.loads(kept)["first"] == "09:00"
    assert "lat" not in kept and str(round(HOME[0], 2)) not in kept


def test_the_export_carries_no_location_or_queued_photo(store):
    store.set_meta("last_location", json.dumps({"lat": 32.0800, "lon": 34.7800, "ts": 1}))
    mv.record(store, HOME[0], HOME[1], ts(9), 20, ts(23))
    store.add_pending(1, [{"type": "image", "source": {"data": "QUFB"}}], "[תמונה]")
    store.set_meta("usage:2026-11-04", "{}")
    meta = store.export()["meta"]
    assert "kv:usage:2026-11-04" in meta
    assert not {"kv:last_location", "kv:move_state", "kv:pending"} & set(meta)
    assert store.pending() and store.get_meta("last_location")  # still on the server


# ---------- how much ----------

@pytest.mark.parametrize("kg,want", [(70.3, 140), (72, 145), (40, 100), (100, 150), (None, 140)])
def test_still_kcal_is_small_and_capped(kg, want):
    assert mv.still_kcal(kg) == want


def rest_day_store(store, monkeypatch, hour=17):
    now = dt.datetime.combine(DAY, dt.time(hour), TZ)
    monkeypatch.setattr(domain, "now", lambda: now)
    monkeypatch.setattr(domain, "today", lambda: DAY)
    store.save_day({**domain.blank_day(DAY.isoformat()), "weight": 70.3})
    return DAY.isoformat()


def run(r, name, args):
    out, err = asyncio.run(r.run(name, args))
    return json.loads(out), err


def on_plan_so_far(store, ds, until=17 * 60):
    """Log the template's meals up to now, so the day isn't behind and no top-ups blur the numbers."""
    d = store.day(ds)
    plan = domain.day_plan(store.schedule(), d)
    d["items"] = [{"n": x["n"], "p": x["p"], "f": x["f"], "c": x["c"]} for x in plan
                  if x["kind"] == "meal" and "p" in x and domain.minutes(x["t"]) <= until]
    store.save_day(d)
    return plan


def test_still_day_needs_a_preview_and_a_later_yes(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    first = ToolRunner(store, None)
    out, err = run(first, "still_day", {"date": ds, "confirm": True})
    assert err and "preview" in out["error"]
    out, err = run(first, "still_day", {"date": ds, "confirm": False})
    assert not err and out["needs_ben_yes"] and out["preview"]["kcal"] == 140 and out["preview"]["carbs"] == 35
    assert out["preview"]["after"] == 2460
    out, err = run(first, "still_day", {"date": ds, "confirm": True})
    assert err and "hasn't answered" in out["error"]
    assert domain.day_target(store.day(ds))["k"] == 2600  # nothing changed yet
    out, err = run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})
    assert not err and out["applied"]


def test_still_day_moves_only_calories_and_carbs_and_only_today(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})
    g = domain.day_target(store.day(ds))
    assert g == {"k": 2460, "p": 155, "f": 80, "c": 280}
    assert domain.TARGETS["rest"]["k"] == 2600
    assert store.data("config/program") is None  # the lasting targets didn't move
    tomorrow = (DAY + dt.timedelta(days=1)).isoformat()
    assert domain.day_target(store.day(tomorrow))["k"] == domain.TARGETS[store.day(tomorrow)["type"]]["k"]
    assert not any("שינוי קבוע" in n["text"] for n in store.profile_notes())  # one day, not a lasting fact


def test_still_day_comes_off_the_biggest_carb_meal_and_survives_rebalance(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    before = on_plan_so_far(store, ds)
    biggest = max((x for x in before if x["kind"] == "meal" and "p" in x and domain.minutes(x["t"]) > 17 * 60), key=lambda x: x["c"])
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})

    def meal():
        return next(x for x in domain.day_plan(store.schedule(), store.day(ds)) if x["n"] == biggest["n"])
    assert meal()["c"] == biggest["c"] - 35 and "35 ג׳ פחמימות פחות היום" in meal()["d"]
    assert meal()["p"] == biggest["p"] and meal()["f"] == biggest["f"]
    for _ in range(3):
        ToolRunner(store, None).rebalance(ds)
    assert meal()["c"] == biggest["c"] - 35
    assert meal()["d"].count("פחות היום") == 1


def test_still_day_undo_restores_it(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    before = on_plan_so_far(store, ds)
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})
    out, err = run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False, "undo": True})
    assert not err and out["target"]["k"] == 2600
    after = domain.day_plan(store.schedule(), store.day(ds))
    assert sum(x.get("c", 0) for x in after if "p" in x) == sum(x.get("c", 0) for x in before if "p" in x)


def test_still_day_refuses_a_training_day(store, monkeypatch):
    sunday = dt.date(2026, 11, 8)
    now = dt.datetime.combine(sunday, dt.time(17), TZ)
    monkeypatch.setattr(domain, "now", lambda: now)
    out, err = run(ToolRunner(store, None), "still_day", {"date": sunday.isoformat(), "confirm": False})
    assert err and "training day" in out["error"]


def test_the_change_counts_in_remaining_and_in_the_checklist(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    d = store.day(ds)
    d["targetAdj"] = {"k": -140, "c": -35, "why": "x"}
    d["items"] = [{"n": "x", "p": 155, "f": 80, "c": 280}]
    store.save_day(d)
    d = store.day(ds)
    assert domain.remaining(d)["k"] == pytest.approx(2460 - domain.kcal(d["items"][0]))
    food = next(c for c in domain.checklist(d) if c["label"] == "אוכל ביעד")
    assert "2,460" in food["det"]


def test_a_day_with_only_the_change_is_kept(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    d = domain.blank_day(ds)
    d["targetAdj"] = {"k": -100, "c": -25, "why": ""}
    store.save_day(d)
    assert store.day(ds)["targetAdj"]["k"] == -100


# ---------- what they read ----------

def preview(k=140):
    return {"kcal": k, "carbs": 35, "before": 2600, "after": 2460, "meal": "ארוחת ערב"}


def test_proposal_is_honest_and_recommends_a_walk_when_not_gaining():
    text = mv.proposal_text(home_day(), preview(), 0.02, "gain")
    assert "מ-09:00" in text and "הערכה" in text and "לא רואה" in text and "30 דקות הליכה" in text
    assert "ההמלצה שלי: הליכה" in text and "ארוחת ערב" in text and "2,460" in text
    assert "שומן" in text and "שריפ" not in text  # fat doesn't move; nothing about burning it


def test_proposal_leaves_the_choice_when_gaining_on_plan():
    assert "ההמלצה" not in mv.proposal_text(home_day(), preview(), 0.2, "gain")
    assert "ההמלצה" not in mv.proposal_text(home_day(), preview(), 0.0, "lose")


def test_context_line(store):
    assert "unknown" in mv.context_line(store, ts(17))
    for h in range(9, 18):
        mv.record(store, HOME[0], HOME[1], ts(h), 20, ts(23))
    line = mv.context_line(store, ts(17))
    assert "still" in line and "fat" in line


# ---------- in the bot ----------

@pytest.fixture
def bot(store, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("OWNER_ID", "1234567")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("GOOGLE_TOKEN", str(tmp_path / "none.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    b = importlib.import_module("coach.bot")
    monkeypatch.setattr(b, "OWNER_ID", 1234567)
    monkeypatch.setattr(b, "store", store)
    b.sent = []

    async def send(ctx, text, buttons=None, with_app=False):
        b.sent.append((text, buttons or []))
    monkeypatch.setattr(b, "send", send)
    return b


def test_live_location_reaches_the_movement_state(bot, store, monkeypatch):
    async def post(*a, **k):
        return True
    monkeypatch.setattr(bot.eta, "post_location", post)
    sent = dt.datetime(2026, 11, 7, 7, 0, tzinfo=dt.timezone.utc)
    loc = SimpleNamespace(latitude=HOME[0], longitude=HOME[1], horizontal_accuracy=15.0, live_period=8 * 3600)

    async def reply(text):
        pass
    msg = SimpleNamespace(location=loc, date=sent, edit_date=sent + dt.timedelta(minutes=3), reply_text=reply)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=1234567), effective_message=msg, edited_message=msg, message=None)
    asyncio.run(bot.on_location(upd, None))
    s = mv.state(store)
    assert s["n"] == 1 and s["until"] == sent.timestamp() + 8 * 3600 and s["ended"] is None
    loc.live_period = None  # the live location stopped
    asyncio.run(bot.on_location(upd, None))
    assert mv.state(store)["ended"] is not None


def still_today(store):
    for h in range(9, 18):
        mv.record(store, HOME[0], HOME[1], ts(h), 20, ts(23))


def test_the_check_offers_and_changes_nothing_by_itself(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    still_today(store)
    monkeypatch.setattr(bot.time, "time", lambda: ts(17))
    asyncio.run(bot.movement_check(None, ds))
    text, buttons = bot.sent[0]
    assert "לא התרחקת" in text
    assert [b[1] for b in buttons] == [f"still:walk:{ds}", f"still:cut:{ds}", f"still:no:{ds}"]
    assert domain.day_target(store.day(ds))["k"] == 2600
    reply = asyncio.run(bot.still_choice("cut", ds))
    assert "2,460" in reply and "35 ג׳ פחמימות פחות" in reply
    assert domain.day_target(store.day(ds))["k"] == 2460


def test_an_unknown_day_says_nothing(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    monkeypatch.setattr(bot.time, "time", lambda: ts(17))
    asyncio.run(bot.movement_check(None, ds))
    assert bot.sent == []


def test_a_moved_day_says_nothing(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    for h, north in ((9, 0), (10, 800), (11, 1500)):
        mv.record(store, HOME[0] + north / 111320, HOME[1], ts(h), 20, ts(23))
    monkeypatch.setattr(bot.time, "time", lambda: ts(17))
    asyncio.run(bot.movement_check(None, ds))
    assert bot.sent == []


def test_choosing_the_walk_keeps_the_food_and_sets_a_reminder(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    text = asyncio.run(bot.still_choice("walk", ds))
    assert "17:30" in text and "האוכל נשאר" in text
    assert domain.day_target(store.day(ds))["k"] == 2600
    assert any("הליכה" in a["text"] for a in store.active_alarms())
    assert "הליכה" in store.day(ds)["notes"]


def test_declining_changes_nothing(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    assert asyncio.run(bot.still_choice("no", ds)) == "בסדר, בלי שינוי."
    assert domain.day_target(store.day(ds))["k"] == 2600


def test_an_expired_preview_changes_nothing(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    assert "לא שיניתי" in asyncio.run(bot.still_choice("cut", ds))
    assert domain.day_target(store.day(ds))["k"] == 2600


def evening_at(bot, store, monkeypatch, day):
    now = dt.datetime.combine(day, dt.time(21, 30), TZ)
    monkeypatch.setattr(domain, "now", lambda: now)
    monkeypatch.setattr(domain, "today", lambda: day)

    async def plan_day(*a, **k):
        return None
    monkeypatch.setattr(bot.planner, "plan_day", plan_day)
    return now


def test_evening_asks_instead_of_logging_when_he_never_left(bot, store, monkeypatch):
    sunday = dt.date(2026, 11, 8)
    now = evening_at(bot, store, monkeypatch, sunday)
    for h in range(9, 22):
        mv.record(store, HOME[0], HOME[1], ts(h, d=sunday), 20, ts(23, d=sunday))
    asyncio.run(bot.evening(None, now))
    assert store.day(sunday.isoformat())["workout"]["exercises"] == []
    text, buttons = bot.sent[-1]
    assert "לא יצאת" in text
    assert [b[1] for b in buttons] == [f"asplan:{sunday.isoformat()}", f"noworkout:{sunday.isoformat()}"]


def test_evening_still_logs_from_the_plan_without_location(bot, store, monkeypatch):
    sunday = dt.date(2026, 11, 8)
    now = evening_at(bot, store, monkeypatch, sunday)
    asyncio.run(bot.evening(None, now))
    assert store.day(sunday.isoformat())["workout"]["exercises"]
    assert not any("לא יצאת" in t for t, _ in bot.sent)


def test_no_workout_button_makes_it_a_rest_day(bot, store, monkeypatch):
    sunday = dt.date(2026, 11, 8)
    now = dt.datetime.combine(sunday, dt.time(21, 35), TZ)
    monkeypatch.setattr(domain, "now", lambda: now)
    monkeypatch.setattr(domain, "today", lambda: sunday)
    ds = sunday.isoformat()
    calls = []

    async def answer(*a, **k):
        calls.append("answer")

    async def edit(*a, **k):
        calls.append("edit")
    q = SimpleNamespace(data=f"noworkout:{ds}", answer=answer, edit_message_reply_markup=edit)
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1234567))
    assert domain.day_target(store.day(ds))["k"] == 2850
    asyncio.run(bot.on_button(upd, None))
    assert store.day(ds)["type"] == "rest" and domain.day_target(store.day(ds))["k"] == 2600
    assert "יום מנוחה" in bot.sent[-1][0] and "2,600" in bot.sent[-1][0]


# ---------- what the review found ----------

def apply_cut(store, ds):
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    out, err = run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})
    assert not err, out
    return out


def plan_of(store, ds):
    return domain.day_plan(store.schedule(), store.day(ds))


def test_the_trim_never_moves_to_the_next_meal_after_dinner(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    template = on_plan_so_far(store, ds)
    apply_cut(store, ds)
    dinner = next(x for x in plan_of(store, ds) if x["n"] == "ארוחת ערב")
    late = next(x for x in plan_of(store, ds) if x["n"] == "מתוק מתוכנן")
    before_late = late["c"]
    d = store.day(ds)  # they eat dinner as planned, at 20:05
    d["items"].append({"n": dinner["n"], "p": dinner["p"], "f": dinner["f"], "c": dinner["c"]})
    store.save_day(d)
    t = dt.datetime.combine(DAY, dt.time(20, 5), TZ)
    monkeypatch.setattr(domain, "now", lambda: t)
    ToolRunner(store, None).rebalance(ds)
    late = next(x for x in plan_of(store, ds) if x["n"] == "מתוק מתוכנן")
    assert late["c"] == before_late and domain.TRIM_MARK not in late["d"]
    planned_total = sum(domain.kcal(x) for x in plan_of(store, ds) if "p" in x)
    assert abs(planned_total - sum(domain.kcal(x) for x in template if "p" in x) + 140) <= 10  # 140 less, not 280


def test_the_trim_survives_an_outing_that_comes_and_goes_without_doubling(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    on_plan_so_far(store, ds)
    apply_cut(store, ds)
    d = store.day(ds)
    plan = plan_of(store, ds)
    dinner_c = next(x for x in plan if x["n"] == "ארוחת ערב")["c"]
    domain.reconcile_away(plan, [{"s": 19 * 60, "e": 21 * 60, "title": "אירוע", "location": "תל אביב"}], 17 * 60, store.schedule()["rest"])
    assert next(x for x in plan if x["n"] == "ארוחת ערב").get("away")
    domain.reconcile_away(plan, [], 17 * 60, store.schedule()["rest"])
    d["plan"] = plan
    store.save_day(d)
    ToolRunner(store, None).rebalance(ds)
    dinner = next(x for x in plan_of(store, ds) if x["n"] == "ארוחת ערב")
    assert dinner["c"] == dinner_c and dinner["d"].count(domain.TRIM_MARK) == 1


def test_a_cut_is_only_as_big_as_the_meal_that_takes_it(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch, hour=21)  # only the 22:00 meal is left
    on_plan_so_far(store, ds, until=21 * 60)
    late = next(x for x in plan_of(store, ds) if x["n"] == "מתוק מתוכנן")
    out = apply_cut(store, ds)
    assert out["meal"] == "מתוק מתוכנן" and out["carbs"] == int(late["c"]) and out["kcal"] == out["carbs"] * 4
    assert domain.day_target(store.day(ds))["k"] == 2600 - out["kcal"]


def test_nothing_left_to_cut_changes_nothing(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch, hour=23)
    out, err = run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    assert not err and out["nothing_left"] and domain.day_target(store.day(ds))["k"] == 2600


def test_the_reply_names_the_meal_that_was_cut_not_one_with_top_ups(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)  # nothing logged: top-ups pile onto the meals ahead
    ToolRunner(store, None).rebalance(ds)
    assert any(x.get("base") for x in plan_of(store, ds))
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    text = asyncio.run(bot.still_choice("cut", ds))
    cut = next(x for x in plan_of(store, ds) if domain.TRIM_MARK in x.get("d", ""))
    assert cut["n"] in text and cut["n"] == store.day(ds)["targetAdj"]["meal"]


def test_a_preview_for_one_meal_cant_apply_to_another(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False})
    t = dt.datetime.combine(DAY, dt.time(20, 30), TZ)  # dinner is past now
    monkeypatch.setattr(domain, "now", lambda: t)
    out, err = run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": True})
    assert err and domain.day_target(store.day(ds))["k"] == 2600


def test_a_day_turned_into_training_is_back_on_the_training_target(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    apply_cut(store, ds)
    assert domain.day_target(store.day(ds))["k"] == 2460
    d = store.day(ds)
    d["type"], d["cycleDay"] = "train", 5
    store.save_day(d)
    d = store.day(ds)
    assert domain.day_target(d)["k"] == 2850
    plan = plan_of(store, ds)
    assert domain.trim_for_target(d, [dict(x) for x in store.schedule()["train"]], 17 * 60) is None


def test_undo_also_takes_back_the_note(store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    d = store.day(ds)
    d["notes"] = "ישן רע"
    store.save_day(d)
    apply_cut(store, ds)
    assert mv.NOTE in store.day(ds)["notes"]
    run(ToolRunner(store, None), "still_day", {"date": ds, "confirm": False, "undo": True})
    assert store.day(ds)["notes"] == "ישן רע" and store.day(ds)["targetAdj"] is None


def test_fixes_that_say_nothing_about_distance_dont_make_a_still_day():
    s = track([(9, 0, 0, 20)])
    for h in range(10, 17):  # hourly, 5.5 km away, accuracy 500 m
        s = mv.add_fix(s, HOME[0] + 5500 / 111320, HOME[1], ts(h), 500, ts(23))
    assert mv.status(s, ts(17)) == "unknown"
    s = track([(h, 0, 0, 500) for h in range(8, 16)] + [(16, 30, 0, 20)])  # all bad until 16:30
    assert s["first"] == ts(16, 30) and mv.status(s, ts(17)) == "unknown"


def test_a_pin_doesnt_start_the_coverage():
    s = mv.add_fix(None, HOME[0], HOME[1], ts(9), 20)  # a one-off pin at 09:00
    for m in (50, 55):
        s = mv.add_fix(s, HOME[0], HOME[1], ts(11, m), 20, ts(23))
    for h in range(12, 18):
        s = mv.add_fix(s, HOME[0], HOME[1], ts(h), 20, ts(23))
    assert s["first"] == ts(11, 50) and mv.status(s, ts(17)) == "unknown"


def test_a_pin_can_still_show_he_moved():
    s = None
    for north in (0, 900, 1800):
        s = mv.add_fix(s, HOME[0] + north / 111320, HOME[1], ts(12), 20)
    assert s["far_n"] >= 2 and mv.status(s, ts(13)) == "moved"


@pytest.mark.parametrize("sigma,interval,acc", [(20, 1, 25), (20, 5, 25), (20, 10, 25), (35, 1, 40), (35, 5, 40), (60, 1, 65)])
def test_gps_jitter_at_any_update_rate_is_not_moving(sigma, interval, acc):
    import random
    rnd = random.Random(7)
    s = None
    t = ts(9)
    while t <= ts(17):
        s = mv.add_fix(s, HOME[0] + rnd.gauss(0, sigma) / 111320, HOME[1] + rnd.gauss(0, sigma) / 94000, t, acc, ts(23))
        t += interval * 60
    assert mv.status(s, ts(17)) == "still", (s["far_n"], s["max_m"])


def test_a_real_walk_at_one_minute_updates_is_moving():
    s = track([(9, 0, 0, 15), (10, 0, 0, 15), (11, 0, 0, 15)])
    t = ts(16)
    for i in range(30):  # 30 minutes out and back, about 80 m a minute
        north = 1200 - abs(i * 80 - 1200)
        s = mv.add_fix(s, HOME[0] + north / 111320, HOME[1], t + i * 60, 15, ts(23))
    assert mv.status(s, ts(16, 30)) == "moved"


def test_the_questions_reach_the_chat_history(bot, store, monkeypatch):
    ds = rest_day_store(store, monkeypatch)
    still_today(store)
    monkeypatch.setattr(bot.time, "time", lambda: ts(17))
    store.add_chat(bot.OWNER_ID, "user", "שלום")
    asyncio.run(bot.movement_check(None, ds))
    assert "לא התרחקת" in store.recent_chat(bot.OWNER_ID)[-1]["content"]


def test_no_workout_records_the_skip_and_survives_a_calendar_error(bot, store, monkeypatch):
    sunday = dt.date(2026, 11, 8)
    now = dt.datetime.combine(sunday, dt.time(21, 35), TZ)
    monkeypatch.setattr(domain, "now", lambda: now)
    monkeypatch.setattr(domain, "today", lambda: sunday)
    ds = sunday.isoformat()

    async def boom(*a, **k):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(bot.planner, "plan_day", boom)
    monkeypatch.setattr("coach.tools.planner.plan_day", boom)
    text = asyncio.run(bot.no_workout(ds))
    assert "לא הצלחתי" in text and store.day(ds)["workout"]["skipped"]
