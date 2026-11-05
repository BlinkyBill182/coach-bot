"""The plan follows the calendar: nothing goes out on a stale plan (the user, 2026-11-04)."""
import asyncio
import datetime as dt

from coach import domain, planner
from coach.tools import ToolRunner

D = dt.date(2026, 11, 4)
DS = D.isoformat()


def at(h, m=0):
    return dt.datetime.combine(D, dt.time(h, m), domain.TZ)


class Cal:
    enabled = True

    def __init__(self, events=()):
        self.events = list(events)

    def events_on(self, d):
        return list(self.events)

    def busy_minutes(self, events, d):
        return []

    def is_gym(self, e):
        return "חדר כושר" in e["title"]

    def gym_event(self, events):
        return next((e for e in events if self.is_gym(e) and not e["all_day"]), None)

    def move_gym(self, *a, **k):
        raise AssertionError("the bot must not move an event the user moved himself")


def event(title, start, end, location=""):
    return {"id": title, "title": title, "all_day": False, "start": start, "end": end, "location": location, "online": False, "description": ""}


GYM = event("חדר כושר", at(18), at(19, 30))
SOHO = event("שיחה עם יוסי", at(9, 30), at(17, 30), "Example Cafe, Yefet St 27, Tel Aviv-Yafo")


def lunch(**kw):
    x = {"t": "14:30", "n": "ארוחת צהריים", "d": "100 ג׳ עוף, 250 ג׳ אורז, סלט", "kind": "meal", "p": 31, "f": 23, "c": 78}
    x.update(kw)
    return x


def save_plan(store, plan, **day):
    d = store.day(DS)
    d.update(type="train", cycleDay=1, plan=plan, **day)
    store.save_day(d)


def refresh(store, cal, now_min=14 * 60):
    return asyncio.run(planner.refresh_today(store, cal, D, now_min))


def plan_of(store):
    return domain.day_plan(store.schedule(), store.day(DS))


def test_meal_returns_home_when_the_event_moved_to_another_day(store):
    x = lunch()
    domain.make_away(x, {"s": 570, "e": 1050, "title": "שיחה עם יוסי", "location": "Example Cafe"})
    assert x["d"].startswith("בדרך")
    save_plan(store, [{"t": "18:00", "n": "אימון", "d": "", "kind": "train"}, x])
    out = refresh(store, Cal([GYM]))  # the event isn't on today's calendar any more
    assert out["changes"]
    meal = next(p for p in plan_of(store) if p["n"] == "ארוחת צהריים")
    assert meal["d"] == "100 ג׳ עוף, 250 ג׳ אורז, סלט" and meal["p"] == 31 and "away" not in meal and "buy" not in meal


def test_new_event_makes_coming_meals_on_the_go_but_not_past_or_pinned(store):
    plan = [lunch(), lunch(t="16:30", n="ביניים"), lunch(t="17:00", n="קבועה", pinned=True)]
    plan[0]["t"] = "12:00"
    save_plan(store, plan)
    refresh(store, Cal([SOHO]), now_min=13 * 60)
    got = {p["n"]: p for p in plan_of(store)}
    assert got["ביניים"].get("away") == "שיחה עם יוסי"
    assert "away" not in got["ארוחת צהריים"]  # already past
    assert "away" not in got["קבועה"]  # the user set it by hand


def test_no_change_means_no_write(store):
    save_plan(store, [lunch()])
    v = store.get(f"log/{DS}")["version"]
    assert refresh(store, Cal([GYM]))["changes"] == []
    assert store.get(f"log/{DS}")["version"] == v


def test_gym_moved_by_hand_rebuilds_the_day(store, monkeypatch):
    async def no_travel(*a, **k):
        return {}
    monkeypatch.setattr(domain, "now", lambda: at(9))
    from coach import eta
    monkeypatch.setattr(eta, "live_travel", no_travel)
    save_plan(store, [{"t": "18:00", "n": "אימון", "d": "", "kind": "train"}, lunch()])
    moved = event("חדר כושר", at(20), at(21, 15))

    class C(Cal):
        def move_gym(self, *a, **k):
            raise AssertionError("must not move it")

    out = refresh(store, C([moved]), now_min=9 * 60)
    assert out["gym_moved"] == "20:00"
    assert next(p for p in plan_of(store) if p["kind"] == "train")["t"] == "20:00"


def test_calendar_failure_leaves_the_plan_alone(store):
    class Broken(Cal):
        def events_on(self, d):
            raise RuntimeError("no network")

    x = lunch()
    domain.make_away(x, {"s": 570, "e": 1050, "title": "שיחה עם יוסי", "location": "Example Cafe"})
    save_plan(store, [x])
    out = refresh(store, Broken())
    assert out["read"] is False and plan_of(store)[0].get("away")


def test_at_home_beats_the_calendar(store):
    x = lunch()
    domain.make_away(x, {"s": 570, "e": 1050, "title": "שיחה עם יוסי", "location": "Example Cafe"})
    save_plan(store, [x])
    r = ToolRunner(store, Cal([SOHO]))
    out = asyncio.run(r.run("set_at_home", {"date": DS, "at_home": True}))
    assert not out[1], out[0]
    assert store.day(DS)["atHome"] and "away" not in plan_of(store)[0]
    refresh(store, Cal([SOHO]), now_min=8 * 60)  # the event is still there
    assert "away" not in plan_of(store)[0]


def test_away_reminder_offers_the_home_button():
    from coach import reminders
    x = lunch()
    domain.make_away(x, {"s": 570, "e": 1050, "title": "שיחה עם יוסי", "location": "Example Cafe"})
    d = domain.blank_day(DS)
    rs = reminders.day_reminders(d, [x], [])
    assert any(("🏠 אני בבית", f"home:{DS}") in (r.buttons or []) for r in rs)
