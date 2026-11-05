import asyncio
import os
from types import SimpleNamespace as NS

from coach.gcal import Calendar


def test_agent_tool_loop(store, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    from coach import agent

    coach = agent.Coach(store, Calendar(str(tmp_path / "none.json")))
    calls = []

    async def fake_create(**kw):
        calls.append({**kw, "messages": list(kw["messages"])})
        if len(calls) == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="thinking", thinking=""),
                NS(type="tool_use", id="t1", name="set_weight", input={"date": "2026-10-19", "kg": 72.1}),
            ])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="רשמתי 72.1")])

    monkeypatch.setattr(coach.client.beta.messages, "create", fake_create)
    reply = asyncio.run(coach.respond(1, "שקלתי 72.1", "שקלתי 72.1"))
    assert reply == "רשמתי 72.1"
    assert store.day("2026-10-19")["weight"] == 72.1
    first = calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    roles = [m["role"] for m in first["messages"]]
    assert roles == ["user", "system"]
    second = [m["role"] for m in calls[1]["messages"]]
    assert second == ["user", "system", "assistant", "user"]
    assert calls[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"
    hist = store.recent_chat(1)
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_bot_module_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "b.db"))
    import importlib
    import coach.bot as bot
    importlib.reload(bot)
    assert bot.OWNER_ID == 0


def test_load_system_appends_exercise_reference(tmp_path, monkeypatch):
    from coach import agent
    (tmp_path / "SKILL.md").write_text("---\nname: x\n---\nCoach body", encoding="utf-8")
    monkeypatch.setenv("SKILL_PATH", str(tmp_path / "SKILL.md"))
    assert "Exercise reference" not in agent.load_system()
    (tmp_path / "exercises.md").write_text("# Exercise reference\nLeg curl: seated", encoding="utf-8")
    text = agent.load_system()
    assert text.index("Coach body") < text.index("# Exercise reference") < text.index("Running as the user's Telegram bot")
    assert "name: x" not in text


def test_real_skill_folder_has_exercise_reference(monkeypatch):
    from coach import agent
    monkeypatch.delenv("SKILL_PATH", raising=False)
    text = agent.load_system()
    assert "# Muscle coach" in text and "Exercise reference" in text


def test_load_system_appends_nutrition_reference(tmp_path, monkeypatch):
    from coach import agent
    (tmp_path / "SKILL.md").write_text("Coach body", encoding="utf-8")
    (tmp_path / "exercises.md").write_text("# Exercise reference", encoding="utf-8")
    (tmp_path / "nutrition.md").write_text("# Nutrition reference", encoding="utf-8")
    monkeypatch.setenv("SKILL_PATH", str(tmp_path / "SKILL.md"))
    text = agent.load_system()
    assert text.index("# Exercise reference") < text.index("# Nutrition reference") < text.index("Running as the user's Telegram bot")


def test_real_skill_folder_has_nutrition_reference(monkeypatch):
    from coach import agent
    monkeypatch.delenv("SKILL_PATH", raising=False)
    assert "# Nutrition reference" in agent.load_system()


def test_billing_error_queues_the_message(store, monkeypatch):
    import asyncio
    import anthropic
    import httpx
    from coach import agent

    class Broke:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                    raise anthropic.BadRequestError(
                        "Your credit balance is too low to access the Anthropic API.",
                        response=httpx.Response(400, request=req), body=None)

    c = agent.Coach.__new__(agent.Coach)
    c.store, c.cal, c.client, c.system = store, None, Broke(), []
    reply = asyncio.run(c.respond(1, "סט 3: פולי 67.5×10", "סט 3: פולי 67.5×10"))
    assert reply == agent.BILLING_REPLY
    assert [p["history"] for p in store.pending()] == ["סט 3: פולי 67.5×10"]
    assert store.recent_chat(1) == []  # the error is not saved as a conversation turn
    asyncio.run(c.respond(1, "x", "x", queue=False))
    assert len(store.pending()) == 1


def test_failed_turn_is_not_kept_in_history(store):
    import asyncio
    import anthropic
    import httpx
    from coach import agent

    class Down:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                    raise anthropic.InternalServerError("overloaded", response=httpx.Response(500, request=req), body=None)

    c = agent.Coach.__new__(agent.Coach)
    c.store, c.cal, c.client, c.system = store, None, Down(), []
    store.add_chat(1, "user", "אימון קצר היום?")
    store.add_chat(1, "assistant", "כן: מדלגים על הרמה קדמית ויד קדמית.")
    for _ in range(10):
        asyncio.run(c.respond(1, "סט 3", "סט 3"))
    hist = store.recent_chat(1)
    assert [m["content"] for m in hist] == ["אימון קצר היום?", "כן: מדלגים על הרמה קדמית ויד קדמית."]


def test_context_shows_todays_notes(store):
    from coach import agent, domain
    now = domain.now()
    d = store.day(now.date().isoformat())
    d["workout"]["notes"] = "אימון קצר: בלי הרמה קדמית ויד קדמית"
    store.save_day(d)
    assert "אימון קצר: בלי הרמה קדמית ויד קדמית" in agent.context_message(store, now)


def test_spend_limit_counts_as_billing():
    from coach import agent
    e = Exception("Error code: 400 - You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC.")
    assert agent.is_billing_error(e)
    assert not agent.is_billing_error(Exception("invalid tool schema"))


def test_usage_cost_and_daily_totals(store):
    from types import SimpleNamespace as NS
    from coach import agent
    u = NS(input_tokens=1000, output_tokens=500, cache_read_input_tokens=20000, cache_creation_input_tokens=0, cache_creation=None)
    assert round(agent.usage_cost("claude-opus-5-5", u), 4) == round((1000 * 4 + 20000 * 0.2 + 500 * 20) / 1e6, 4)
    assert agent.usage_cost("claude-sonnet-5-5", u) < agent.usage_cost("claude-opus-5-5", u)
    c = agent.Coach.__new__(agent.Coach)
    c.store = store
    c.record_usage("claude-opus-5-5", NS(usage=u))
    c.record_usage("claude-opus-5-5", NS(usage=u))
    import json
    from coach import domain
    day = json.loads(store.get_meta(f"usage:{domain.today().isoformat()}"))
    assert day["claude-opus-5-5"]["calls"] == 2


def test_escalation_switches_to_the_senior_model(store, monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from coach import agent
    monkeypatch.setattr(agent, "FAST_MODEL", "claude-sonnet-5-5")
    seen = []

    class Client:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    seen.append(kw["model"])
                    usage = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None)
                    if len(seen) == 1:
                        block = NS(type="tool_use", id="t1", name="ask_senior_coach", input={"reason": "plan change"})
                        return NS(stop_reason="tool_use", content=[block], usage=usage)
                    return NS(stop_reason="end_turn", content=[NS(type="text", text="תשובה")], usage=usage)

    c = agent.Coach.__new__(agent.Coach)
    c.store, c.cal, c.client, c.system = store, None, Client(), []
    c.tools = agent.TOOLS + [agent.ESCALATE_TOOL]
    assert asyncio.run(c.respond(1, "לשנות את התוכנית?", "לשנות את התוכנית?")) == "תשובה"
    assert seen == ["claude-sonnet-5-5", "claude-opus-5-5"]


def test_history_window_start_moves_in_jumps(store):
    starts = []
    for i in range(60):
        store.add_chat(1, "user" if i % 2 == 0 else "assistant", f"m{i}")
        msgs = store.recent_chat(1)
        starts.append(msgs[0]["content"] if msgs else None)
    changes = sum(1 for a, b in zip(starts, starts[1:]) if a != b)
    assert changes <= 3  # 60 messages, the start jumped only a few times
    assert 20 <= len(store.recent_chat(1)) <= 36


def test_history_gets_a_cache_marker_and_keep_warm_sends_no_output(store):
    import asyncio
    from types import SimpleNamespace as NS
    from coach import agent
    store.add_chat(1, "user", "שלום")
    store.add_chat(1, "assistant", "היי")
    sent = {}

    class Client:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    sent.update(kw)
                    return NS(usage=NS(input_tokens=0, output_tokens=0, cache_read_input_tokens=45000,
                                       cache_creation_input_tokens=0, cache_creation=None))

    c = agent.Coach.__new__(agent.Coach)
    c.store, c.cal, c.client, c.system = store, None, Client(), [{"type": "text", "text": "S"}]
    h = c.history(1)
    assert h[-1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert asyncio.run(c.keep_warm(1))
    assert sent["max_tokens"] == 0 and sent["system"] == c.system and sent["tools"] == c.tools
    assert "cache_control" not in sent  # nothing new is written
    assert c.last_call > 0
