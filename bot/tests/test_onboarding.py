import asyncio
import base64
import importlib
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from coach import domain, onboarding as ob, web

CHAT = 7
ANSWERS = dict(age=41, weight=70.3, height=182.0, sex="m", activity="sit", goal="gain", gym=5)
FULL = {**ANSWERS, "job": "בונה אוטומציות מהמחשב", "avoid": "בלי בקר, בלי קמח", "pantry": "אורז, ביצים, קריאטין, חלבון"}
SECRET = b"\xff\xd8\xffBODYPHOTOBYTES"


# ---------- the estimate ----------

def test_estimate_for_ben_lands_near_his_live_targets():
    est = ob.estimate(ANSWERS)
    assert est["bmr"] == 1640  # 703 + 1137.5 - 205 + 5
    assert est["burn"] == {"rest": 2297, "train": 2648}
    assert est["targets"]["train"]["k"] == 2910 and est["targets"]["rest"]["k"] == 2530
    assert est["targets"]["train"]["p"] == 155 and est["targets"]["rest"]["f"] == 70
    assert ob.target_gap(est["targets"]) < ob.KEEP_GAP


def test_estimate_macros_add_up_and_fat_has_a_floor():
    t = ob.estimate(ANSWERS)["targets"]["rest"]
    assert abs(t["p"] * 4 + t["f"] * 9 + t["c"] * 4 - t["k"]) <= 8
    small = ob.estimate({**ANSWERS, "goal": "lose"})["targets"]["rest"]
    assert small["f"] >= 0.9 * 70.3 - 1


def test_a_deficit_has_a_floor():
    t = ob.estimate(dict(age=30, weight=45.0, height=150.0, sex="f", activity="sit", goal="lose"))
    assert t["targets"]["rest"]["k"] >= 1200
    assert t["targets"]["rest"]["k"] >= t["bmr"] * 1.1 - 10


def test_sex_changes_the_formula_by_166():
    assert ob.estimate(ANSWERS)["bmr"] - ob.estimate({**ANSWERS, "sex": "f"})["bmr"] == 166


# ---------- answers ----------

@pytest.mark.parametrize("raw,step", [
    ({"age": 200}, "body"), ({"age": 5}, "body"), ({"age": 41.5}, "body"), ({"age": "abc"}, "body"), ({"age": True}, "body"),
    ({"weight": 20}, "body"), ({"height": 90}, "body"), ({"gym": 9}, "gym"), ({"gym": 2.5}, "gym"),
    ({"sex": "x"}, "sex"), ({"goal": "x"}, "goal"), ({"activity": "x"}, "activity"),
])
def test_bad_answers_are_refused_with_their_question(store, raw, step):
    with pytest.raises(ob.Invalid) as e:
        ob.save_draft(store, raw)
    assert e.value.step == step


def test_numbers_may_come_as_text(store):
    d = ob.save_draft(store, {"age": "41", "weight": "70.3", "height": "182", "gym": "5"})
    assert (d["age"], d["weight"], d["height"], d["gym"]) == (41, 70.3, 182.0, 5)


def test_the_build_cant_be_set_from_the_draft(store):
    assert "build" not in ob.save_draft(store, {"age": 41, "build": "ממציא"})


def test_empty_text_clears_an_answer_and_long_text_is_cut(store):
    ob.save_draft(store, {"job": "משהו"})
    assert "job" not in ob.save_draft(store, {"job": "  ", "age": 41})
    assert len(ob.save_draft(store, {"pantry": "א" * 900})["pantry"]) == ob.MAX_TEXT


def test_review_sends_him_to_what_is_missing(store):
    ob.save_draft(store, {"age": 41, "weight": 70, "height": 182, "sex": "m"})
    with pytest.raises(ob.Invalid) as e:
        ob.review(store)
    assert e.value.step == "goal"


def test_state_offers_the_last_weight(store):
    store.set("log/2026-11-01", {"date": "2026-11-01", "weight": 70.3})
    s = ob.state(store)
    assert s["lastWeight"] == 70.3 and s["draft"] == {} and not s["saved"]


def test_a_new_install_starts_it_and_a_live_one_does_not(store):
    assert ob.fresh_install(store)
    store.set("log/2026-11-01", {"date": "2026-11-01", "weight": 70})
    assert not ob.fresh_install(store)


# ---------- saving the base ----------

def fill(store, **over):
    ob.save_draft(store, {**FULL, **over})


def test_saving_keeps_the_targets_when_close(store):
    fill(store)
    r = ob.review(store)
    assert r["propose"] is False and r["estimate"]["targets"]["train"]["k"] == 2910 and r["live"]["rest"]["k"] == 2600
    assert "שמרתי" in ob.finish(store, False, CHAT)
    assert domain.TARGETS["train"]["k"] == 2850
    assert store.data("config/program") is None  # nothing touched the targets
    doc = store.data(ob.DOC)
    assert doc["status"] == "done" and doc["answers"]["goal"] == "gain" and doc["estimate"]["bmr"] == 1640 and doc["draft"] == {}


def test_answers_reach_the_saved_facts_the_context_and_the_chat(store):
    from coach.agent import context_message
    store.set_profile_notes([{"text": "מכונת חתירה: 75 אחרי 67.5", "date": "2026-10-28", "source": "bot"}])
    fill(store)
    ob.finish(store, False, CHAT)
    texts = [n["text"] for n in store.profile_notes()]
    assert texts[0].startswith("מכונת חתירה")  # their other facts stay
    assert any("בלי בקר" in t for t in texts) and any("חלבון" in t for t in texts) and any("אוטומציות" in t for t in texts)
    ctx = context_message(store)
    assert "Base from the questionnaire" in ctx and "goal gain" in ctx and "בלי בקר" in ctx
    chat = store.recent_chat(CHAT)
    assert chat[0]["content"].startswith("[שאלון פתיחה]") and chat[1]["role"] == "assistant"


def test_a_second_run_starts_from_the_answers_and_replaces_its_facts(store):
    fill(store)
    ob.finish(store, False, CHAT)
    n = len(store.profile_notes())
    assert ob.state(store)["draft"]["age"] == 41  # the page opens with what they said
    ob.save_draft(store, {"gym": 4})
    text = ob.finish(store, False, CHAT)
    assert "בתוכנית יש 5 אימונים בשבוע ואתה אמרת 4" in text
    assert len(store.profile_notes()) == n and store.data(ob.DOC)["answers"]["gym"] == 4


def test_the_questionnaire_never_rewrites_the_program(store):
    exercises, schedule = store.data("config/exercises"), store.data("config/schedule")
    fill(store, gym=3)
    ob.finish(store, False, CHAT)
    assert store.data("config/exercises") == exercises and store.data("config/schedule") == schedule
    assert store.data("config/program") is None


def test_a_big_gap_needs_his_yes_before_targets_change(store):
    fill(store, goal="lose")
    assert ob.review(store)["propose"] is True
    assert domain.TARGETS["rest"]["k"] == 2600
    assert "היעדים לא השתנו" in ob.finish(store, False, CHAT)
    assert domain.TARGETS["rest"]["k"] == 2600 and store.data("config/program") is None


def test_a_big_gap_applies_through_the_gate_once(store):
    fill(store, goal="lose")
    new = ob.review(store)["estimate"]["targets"]
    assert "היעדים עודכנו" in ob.finish(store, True, CHAT)
    assert domain.TARGETS["rest"]["k"] == new["rest"]["k"] and domain.TARGETS["train"]["p"] == new["train"]["p"]
    assert store.data("config/program")["targets"]["rest"]["k"] == new["rest"]["k"]
    assert any("יעדים מהשאלון" in n["text"] for n in store.profile_notes())
    assert json.loads(store.get_meta("preview:onboarding_targets")) == {}  # a yes works once


def test_apply_without_a_preview_is_refused_and_saves_nothing(store):
    fill(store, goal="lose")
    with pytest.raises(ValueError):
        ob.finish(store, True, CHAT)
    assert store.data("config/program") is None and not ob.answers(store)


def test_apply_after_the_answers_changed_is_refused(store):
    fill(store, goal="lose")
    ob.review(store)
    ob.save_draft(store, {"weight": 90})  # not what they saw
    with pytest.raises(ValueError):
        ob.finish(store, True, CHAT)
    assert store.data("config/program") is None


def test_apply_is_ignored_when_the_targets_stay(store):
    fill(store)
    ob.review(store)
    ob.finish(store, True, CHAT)
    assert store.data("config/program") is None


def test_leaving_keeps_the_saved_answers_and_estimate(store):
    fill(store)
    ob.finish(store, False, CHAT)
    saved = ob.base_line(store)
    ob.save_draft(store, {"weight": 120})
    ob.review(store)
    assert ob.base_line(store) == saved  # an unsaved review changes nothing
    ob.discard(store)
    assert ob.base_line(store) == saved and store.data(ob.DOC)["answers"]["weight"] == 70.3
    assert ob.state(store)["draft"]["weight"] == 70.3


def test_an_old_chat_run_left_in_the_doc_does_no_harm(store):
    store.set(ob.DOC, {"status": "active", "step": "body", "touched": 1, "draft": {}})
    assert ob.state(store)["draft"] == {} and ob.base_line(store) is None
    fill(store)
    assert "שמרתי" in ob.finish(store, False, CHAT)


# ---------- the page's routes ----------

async def _with_client(store, fn, describe=None, notify=None, key="k"):
    web.WEB_KEY = key
    client = TestClient(TestServer(web.make_app(store, "1:x", CHAT, describe, notify)))
    await client.start_server()
    try:
        return await fn(client)
    finally:
        await client.close()


HEAD = {"X-Access-Key": "k"}


def everything_stored(store):
    return json.dumps(store.export(), ensure_ascii=False) + "".join(
        str(r[0]) for r in store.db.execute("SELECT value FROM meta")) + "".join(
        str(r[0]) for r in store.db.execute("SELECT content FROM chat")) + "".join(
        str(r[0]) for r in store.db.execute("SELECT data FROM docs"))


def test_the_page_is_served_and_the_api_needs_auth(store):
    async def go(c):
        page = await c.get("/onboarding")
        assert page.status == 200 and "שאלון פתיחה" in await page.text()
        for method, path in (("GET", ""), ("PUT", "/draft"), ("POST", "/photo"), ("DELETE", "/photo"),
                             ("POST", "/review"), ("POST", "/finish"), ("POST", "/discard")):
            r = await c.request(method, "/api/onboarding" + path, json={} if method != "GET" else None)
            assert r.status == 403, path
    asyncio.run(_with_client(store, go))


def test_the_whole_page_flow_over_http(store):
    told = []

    async def notify(text):
        told.append(text)

    async def go(c):
        r = await c.put("/api/onboarding/draft", json=FULL, headers=HEAD)
        assert r.status == 200 and (await r.json())["draft"]["age"] == 41
        r = await c.post("/api/onboarding/review", json={}, headers=HEAD)
        body = await r.json()
        assert r.status == 200 and body["propose"] is False and body["gap"] < 0.05
        r = await c.post("/api/onboarding/finish", json={"apply": False}, headers=HEAD)
        assert r.status == 200 and "שמרתי" in (await r.json())["text"]
        r = await c.get("/api/onboarding", headers=HEAD)
        assert (await r.json())["saved"] is True
    asyncio.run(_with_client(store, go, notify=notify))
    assert len(told) == 1 and "שמרתי" in told[0]


def test_a_bad_answer_comes_back_with_its_question(store):
    async def go(c):
        r = await c.put("/api/onboarding/draft", json={"age": 200}, headers=HEAD)
        body = await r.json()
        assert r.status == 400 and body["step"] == "body" and "גיל" in body["message"]
        r = await c.post("/api/onboarding/review", json={}, headers=HEAD)
        assert r.status == 400 and (await r.json())["step"] == "body"
    asyncio.run(_with_client(store, go))


def test_a_stale_yes_comes_back_as_a_conflict(store):
    async def go(c):
        await c.put("/api/onboarding/draft", json={**FULL, "goal": "lose"}, headers=HEAD)
        r = await c.post("/api/onboarding/finish", json={"apply": True}, headers=HEAD)
        assert r.status == 409
    asyncio.run(_with_client(store, go))
    assert store.data("config/program") is None


def test_a_failed_chat_message_doesnt_lose_the_save(store):
    async def notify(text):
        raise RuntimeError("telegram down")

    async def go(c):
        await c.put("/api/onboarding/draft", json=FULL, headers=HEAD)
        r = await c.post("/api/onboarding/finish", json={}, headers=HEAD)
        assert r.status == 200
    asyncio.run(_with_client(store, go, notify=notify))
    assert ob.answers(store)["age"] == 41


# ---------- the body photo: only words stay ----------

def test_body_photo_leaves_only_a_description(store):
    seen = []

    async def describe(data):
        seen.append(data)
        return "מבנה רזה, רגליים דקות"

    async def go(c):
        r = await c.post("/api/onboarding/photo", data=SECRET, headers=HEAD)
        assert r.status == 200 and (await r.json())["build"] == "מבנה רזה, רגליים דקות"
    asyncio.run(_with_client(store, go, describe))
    assert seen == [SECRET]
    assert store.data(ob.DOC)["draft"]["build"] == "מבנה רזה, רגליים דקות"
    stored = everything_stored(store)
    assert "BODYPHOTOBYTES" not in stored and base64.standard_b64encode(SECRET).decode() not in stored
    assert store.pending() == [] and store.recent_chat(CHAT) == []  # not queued, not in the chat


def test_a_photo_it_couldnt_read_stores_nothing(store):
    async def describe(data):
        return None

    async def go(c):
        r = await c.post("/api/onboarding/photo", data=SECRET, headers=HEAD)
        assert r.status == 200 and (await r.json())["build"] is None
    asyncio.run(_with_client(store, go, describe))
    assert "build" not in (store.data(ob.DOC) or {}).get("draft", {}) and store.pending() == []


def test_the_description_stays_through_saving_and_can_be_cleared(store):
    async def describe(data):
        return "מבנה רזה"

    async def go(c):
        await c.post("/api/onboarding/photo", data=SECRET, headers=HEAD)
        r = await c.delete("/api/onboarding/photo", headers=HEAD)
        assert r.status == 200
        assert "build" not in store.data(ob.DOC)["draft"]
        await c.post("/api/onboarding/photo", data=SECRET, headers=HEAD)
        await c.put("/api/onboarding/draft", json=FULL, headers=HEAD)
        await c.post("/api/onboarding/finish", json={}, headers=HEAD)
    asyncio.run(_with_client(store, go, describe))
    assert ob.answers(store)["build"] == "מבנה רזה"
    assert "photo that was not kept" in ob.base_line(store)


def test_only_a_jpeg_of_a_sane_size_is_looked_at(store):
    called = []

    async def describe(data):
        called.append(1)
        return "x"

    async def go(c):
        r = await c.post("/api/onboarding/photo", data=b"\x89PNGxxxx", headers=HEAD)
        assert r.status == 415
        r = await c.post("/api/onboarding/photo", data=b"\xff\xd8\xff" + b"0" * (web.MAX_PHOTO + 1), headers=HEAD)
        assert r.status == 413
    asyncio.run(_with_client(store, go, describe))
    assert called == []


def test_a_photo_larger_than_the_json_limit_still_goes_through(store):
    async def describe(data):
        return "מבנה"

    async def go(c):
        r = await c.post("/api/onboarding/photo", data=b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024), headers=HEAD)
        assert r.status == 200
    asyncio.run(_with_client(store, go, describe))


def test_photo_analysis_off_is_said(store):
    async def go(c):
        r = await c.post("/api/onboarding/photo", data=SECRET, headers=HEAD)
        assert r.status == 503
    asyncio.run(_with_client(store, go))


def test_describe_build_handles_refusal_and_errors(store, monkeypatch):
    import anthropic
    from coach.agent import Coach
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    coach = Coach(store, None)
    calls = []

    def make(result):
        async def create(**kw):
            calls.append(kw)
            if isinstance(result, Exception):
                raise result
            return result
        return create
    text = SimpleNamespace(type="text", text="מבנה רזה")
    ok = SimpleNamespace(stop_reason="end_turn", content=[text], usage=None)
    monkeypatch.setattr(coach.client.beta.messages, "create", make(ok))
    assert asyncio.run(coach.describe_build(SECRET)) == "מבנה רזה"
    kw = calls[-1]
    assert "tools" not in kw and len(kw["messages"]) == 1 and "cache_control" not in kw  # one separate look
    monkeypatch.setattr(coach.client.beta.messages, "create", make(SimpleNamespace(stop_reason="refusal", content=[], usage=None)))
    assert asyncio.run(coach.describe_build(SECRET)) is None
    none = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="NONE")], usage=None)
    monkeypatch.setattr(coach.client.beta.messages, "create", make(none))
    assert asyncio.run(coach.describe_build(SECRET)) is None
    req = SimpleNamespace(method="POST", url="https://x", headers={})
    monkeypatch.setattr(coach.client.beta.messages, "create", make(anthropic.APIConnectionError(request=req)))
    assert asyncio.run(coach.describe_build(SECRET)) is None
    assert store.pending() == []


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
    return b


def command_update():
    replies = []

    async def reply_text(text, reply_markup=None):
        replies.append((text, reply_markup))
    msg = SimpleNamespace(text="/onboarding", reply_text=reply_text)
    return SimpleNamespace(effective_user=SimpleNamespace(id=1234567), message=msg), replies


def test_the_command_opens_the_page(bot, monkeypatch):
    monkeypatch.setattr(bot, "WEB_URL", "https://coach.example/")
    upd, replies = command_update()
    asyncio.run(bot.cmd_onboarding(upd, None))
    button = replies[0][1].inline_keyboard[0][0]
    assert button.web_app.url == "https://coach.example/onboarding"


def test_without_a_web_url_it_says_so(bot, monkeypatch):
    monkeypatch.setattr(bot, "WEB_URL", "")
    upd, replies = command_update()
    asyncio.run(bot.cmd_onboarding(upd, None))
    assert "WEB_URL" in replies[0][0] and replies[0][1] is None


def test_a_new_install_gets_the_page_from_start(bot, monkeypatch):
    monkeypatch.setattr(bot, "WEB_URL", "https://coach.example/")
    upd, replies = command_update()
    asyncio.run(bot.cmd_start(upd, None))
    assert replies[0][1].inline_keyboard[0][0].web_app.url.endswith("/onboarding")


def test_text_and_photos_stay_with_the_coach_even_with_a_draft_open(bot, store, monkeypatch):
    ob.save_draft(store, {"age": 41})
    responded = []

    async def respond(chat, content, history, **k):
        responded.append(history)
        return "רשמתי"
    monkeypatch.setattr(bot.coach, "respond", respond)

    async def send_message(*a, **k):
        pass

    async def send_chat_action(*a, **k):
        pass
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=send_message, send_chat_action=send_chat_action))
    msg = SimpleNamespace(text="אכלתי עוף")
    asyncio.run(bot.on_text(SimpleNamespace(effective_user=SimpleNamespace(id=1234567), message=msg), ctx))
    assert responded == ["אכלתי עוף"]
