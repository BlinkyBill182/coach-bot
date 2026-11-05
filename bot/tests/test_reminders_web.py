import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from coach import domain, reminders
from coach.web import check_init_data, make_app


def test_day_reminders(store):
    day = store.day("2026-10-19")
    plan = domain.day_plan(store.schedule(), day)
    rs = reminders.day_reminders(day, plan, ["A: ..."])
    keys = {r.key: r for r in rs}
    assert keys["2026-10-19:wake"].at == 8 * 60
    assert keys["2026-10-19:leave"].at == 9 * 60 - 25
    assert keys["2026-10-19:sets"].at == 9 * 60 + 90 + 45
    lunch = next(r for r in rs if "ארוחת צהריים" in r.text)
    assert lunch.at == 14 * 60 + 30 - 20 and lunch.buttons[0][1].startswith("meal:2026-10-19:")
    assert [r.key for r in reminders.due(rs, 8 * 60)] == ["2026-10-19:wake"]


def test_evening_text(store):
    day = store.day("2026-10-18")
    res = {"cycleDay": 5, "type": "train", "start": 750, "plan": domain.day_plan(store.schedule(), store.day("2026-10-19")),
           "moved": True, "conflict": False}
    txt = reminders.evening_text(day, res, domain.parse_date("2026-10-19"), False, None)
    assert "אימון ב-12:30, לצאת ב-12:10" in txt and "הזזתי" in txt


def _init_data(token, user_id, age=0):
    pairs = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": user_id}), "query_id": "q"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_init_data_validation():
    tok = "123:abc"
    assert check_init_data(_init_data(tok, 42), tok, 42)
    assert not check_init_data(_init_data(tok, 43), tok, 42)
    assert not check_init_data(_init_data("999:zzz", 42), tok, 42)
    assert not check_init_data(_init_data(tok, 42, age=8 * 86400), tok, 42)


def test_api_roundtrip(store):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        tok = "123:abc"
        client = TestClient(TestServer(make_app(store, tok, 42)))
        await client.start_server()
        try:
            r = await client.get("/api/c/log")
            assert r.status == 403
            h = {"X-Telegram-Init-Data": _init_data(tok, 42)}
            r = await client.put("/api/d/log/2026-10-20", json={"date": "2026-10-20", "weight": 70}, headers=h)
            assert (await r.json())["version"] == 1
            r = await client.patch("/api/d/log/2026-10-20", json={"weight": 70.5}, headers=h)
            assert r.status == 200
            j = await (await client.get("/api/c/log", headers=h)).json()
            assert j["docs"][0]["data"]["weight"] == 70.5 and j["seq"] > 0
            r = await client.patch("/api/d/log/2026-01-01", json={"a": 1}, headers=h)
            assert (await r.json())["code"] == "invalid_argument"
            r = await client.get("/")
            assert r.status == 200 and "shim.js" in await r.text()
        finally:
            await client.close()

    asyncio.run(go())


def test_evening_text_sends_shopping_list_before_sunday_only(store):
    day = store.day("2026-11-07")
    saturday = reminders.evening_text(day, None, domain.parse_date("2026-11-08"), False, None)
    assert "קניות לשבוע:" in saturday
    for line in domain.SHOPPING:
        assert line in saturday
    for tomorrow in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"):
        assert "קניות לשבוע:" not in reminders.evening_text(day, None, domain.parse_date(tomorrow), False, None)

