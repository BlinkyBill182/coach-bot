import asyncio
import datetime as dt
import importlib
import json
from types import SimpleNamespace

from coach import domain, eta, planner
from coach.tools import ToolRunner

D = domain.today() + dt.timedelta(days=1)  # live_travel only asks Waze for departures from now on


def ev(title, s, e, location=""):
    mk = lambda hm: dt.datetime.combine(D, dt.time(*map(int, hm.split(":"))), domain.TZ)
    return {"title": title, "start": mk(s), "end": mk(e), "all_day": False, "location": location, "description": ""}


MEETING = ev("פגישה עם דנה", "14:00", "18:00", "Example Cafe, Tel Aviv")
MANUAL = {"default": 20, "places": [{"match": "Example Cafe", "minutes": 45}]}


def stub_waze(monkeypatch, minutes):
    """Waze on, answering `minutes` (None: it failed). Records the calls."""
    calls = []

    async def drive(dest, origin="me", depart_at=None):
        calls.append((dest, origin, depart_at))
        return None if minutes is None else {"minutes": minutes, "km": 11.3}
    monkeypatch.setenv("ETA_KEY", "test")
    monkeypatch.setattr(eta, "drive_minutes", drive)
    return calls


def test_off_without_key():
    assert not eta.enabled()
    assert asyncio.run(eta.live_travel([MEETING], D)) == {}
    assert asyncio.run(eta.drive_minutes("תל אביב")) is None
    assert asyncio.run(eta.eta("תל אביב"))["status"] == 0


def test_live_minutes_beat_manual_match(monkeypatch):
    calls = stub_waze(monkeypatch, 31)
    live = asyncio.run(eta.live_travel([MEETING, ev("חדר כושר", "09:00", "10:30", "Example Gym")], D, MANUAL))
    assert live == {"Example Cafe, Tel Aviv": 31}  # full location; the gym is skipped
    assert calls == [("Example Cafe, Tel Aviv", "me", f"{D.isoformat()}T13:15")]  # start minus the manual 45
    travel = domain.norm_travel({**MANUAL, "live": live})
    assert domain.travel_for(MEETING, travel) == 31
    w = domain.away_windows([MEETING], D, travel)[0]
    assert (w["s"], w["e"]) == (14 * 60 - 31, 18 * 60 + 31)  # the way back takes as long


def test_no_live_answer_falls_back_to_manual_then_default(monkeypatch):
    stub_waze(monkeypatch, None)
    live = asyncio.run(eta.live_travel([MEETING], D, MANUAL))
    assert live == {}
    travel = domain.norm_travel({**MANUAL, "live": live})
    assert "live" not in travel
    assert domain.travel_for(MEETING, travel) == 45
    assert domain.travel_for(ev("סידורים", "10:00", "11:00", "Herzl 1, Tel Aviv"), travel) == 20


def test_online_and_no_address_events_are_not_asked(monkeypatch):
    calls = stub_waze(monkeypatch, 10)
    events = [ev("שיחה", "10:00", "13:30", "https://meet.google.com/x"), ev("הופעה", "20:00", "23:00")]
    assert asyncio.run(eta.live_travel(events, D)) == {} and calls == []


def test_get_drive_time(monkeypatch, store):
    async def fake(dest, origin="me", depart_at=None):
        assert (dest, origin, depart_at) == ("תל אביב", "me", "2026-10-26T08:00")
        return {"minutes": 29, "km": 11.3, "route": "20 איילון צפון", "depart_at": "2026-10-26T08:00:00+02:00",
                "arrive_at": "2026-10-26T08:29:00+02:00", "cached": False,
                "from_location": {"source": "telegram", "at": "x", "age_min": 45}}
    monkeypatch.setattr(eta, "eta", fake)
    out, err = asyncio.run(ToolRunner(store, None).run("get_drive_time", {"destination": "תל אביב", "depart_at": "2026-10-26 08:00"}))
    r = json.loads(out)
    assert not err and r["minutes"] == 29 and r["km"] == 11.3 and r["from_location"]["source"] == "telegram"
    assert "45 min old" in r["note"]


def test_get_drive_time_says_when_there_is_no_location(monkeypatch, store):
    async def fake(dest, origin="me", depart_at=None):
        return {"error": "no recent location", "status": 409}
    monkeypatch.setattr(eta, "eta", fake)
    out, err = asyncio.run(ToolRunner(store, None).run("get_drive_time", {"destination": "תל אביב"}))
    assert err and "share live location" in json.loads(out)["error"]


class FakeCal:
    enabled = True

    def __init__(self, events):
        self.events = events

    def events_on(self, d):
        return self.events


def test_traffic_check_moves_leave_only_past_5_min(monkeypatch, store):
    ds = D.isoformat()
    day = store.day(ds)
    day["plan"] = [{"t": "15:00", "n": "ביניים", "d": "בדרך: x", "kind": "meal", "p": 20, "f": 5, "c": 30,
                    "away": "פגישה עם דנה", "where": "Example Cafe", "leave": "13:30"}]
    store.save_day(day)
    cal = FakeCal([MEETING])
    leave = lambda: store.day(ds)["plan"][0]["leave"]

    stub_waze(monkeypatch, 33)  # 13:27: 3 min off, stays
    assert asyncio.run(planner.traffic_check(store, cal, D, 12 * 60 + 30)) == []
    assert leave() == "13:30"
    assert asyncio.run(planner.traffic_check(store, cal, D, 12 * 60 + 31)) == []  # once per leave time

    day = store.day(ds)
    day["plan"][0]["leave"] = "13:40"
    store.save_day(day)
    calls = stub_waze(monkeypatch, 45)  # 13:15: 25 min earlier
    assert asyncio.run(planner.traffic_check(store, cal, D, 12 * 60 + 10)) == []  # more than an hour ahead
    assert calls == []
    assert asyncio.run(planner.traffic_check(store, cal, D, 12 * 60 + 40)) == [("13:40", "13:15")]
    assert leave() == "13:15"
    assert asyncio.run(planner.traffic_check(store, cal, D, 12 * 60 + 41)) == []  # the new time was just checked


def test_live_location_is_forwarded(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("OWNER_ID", "1234567")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("GOOGLE_TOKEN", str(tmp_path / "none.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    bot = importlib.import_module("coach.bot")
    monkeypatch.setattr(bot, "OWNER_ID", 1234567)
    posted, replies = [], []

    async def post(lat, lon, at, accuracy=None, source="telegram"):
        posted.append({"lat": lat, "lon": lon, "at": at.isoformat(), "accuracy": accuracy, "source": source})
        return True
    monkeypatch.setattr(eta, "post_location", post)
    monkeypatch.setenv("ETA_KEY", "test")

    async def reply(text):
        replies.append(text)
    sent = dt.datetime(2026, 10, 25, 9, 0, tzinfo=dt.timezone.utc)
    edited = sent + dt.timedelta(minutes=3)
    loc = SimpleNamespace(latitude=32.0800, longitude=34.7880, horizontal_accuracy=15.0, live_period=0x7FFFFFFF)
    msg = SimpleNamespace(location=loc, date=sent, edit_date=edited, reply_text=reply)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=1234567), effective_message=msg,
                          edited_message=msg, message=None)
    asyncio.run(bot.on_location(upd, None))
    assert posted == [{"lat": 32.0800, "lon": 34.7880, "at": edited.isoformat(), "accuracy": 15.0, "source": "telegram"}]
    assert replies == []  # live updates get no reply

    msg.edit_date, loc.live_period = None, None  # a one-off location
    asyncio.run(bot.on_location(SimpleNamespace(**{**vars(upd), "edited_message": None, "message": msg}), None))
    assert posted[-1]["at"] == sent.isoformat() and len(replies) == 1

    upd.effective_user.id = 1  # someone else: ignored
    asyncio.run(bot.on_location(upd, None))
    assert len(posted) == 2
