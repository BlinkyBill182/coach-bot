"""What a new install gets, and what stays off until the owner turns it on."""
import json
import os

from coach import agent, domain, personal, tools, wolt

STARTER = os.path.join(os.path.dirname(__file__), "..", "starter")


def test_starter_plan_loads_once_and_keeps_changes(tmp_path):
    from coach.store import Store
    s = Store(str(tmp_path / "t.db"))
    assert sorted(s.load_starter(STARTER)) == ["config/exercises", "config/schedule"]
    assert s.schedule()["train"] and s.schedule()["rest"]
    assert domain.norm_library(s.data("config/exercises"))
    s.set("config/schedule", {"train": [], "rest": []})
    assert s.load_starter(STARTER) == []
    assert s.data("config/schedule") == {"train": [], "rest": []}


def test_starter_plan_covers_every_training_day():
    with open(os.path.join(STARTER, "config", "exercises.json"), encoding="utf-8") as f:
        ex = json.load(f)
    names = {i["name"] for i in ex["items"]}
    for day in ("1", "2", "3", "5", "6"):
        assert ex["plans"][day] and all(x["name"] in names for x in ex["plans"][day])


def test_a_fresh_install_still_opens_the_questionnaire(tmp_path):
    from coach import onboarding
    from coach.store import Store
    s = Store(str(tmp_path / "t.db"))
    s.load_starter(STARTER)
    assert onboarding.fresh_install(s)


def test_addons_are_off_by_default(monkeypatch):
    monkeypatch.delenv("WOLT_ENABLED", raising=False)
    monkeypatch.delenv("ETA_KEY", raising=False)
    names = {t["name"] for t in tools.enabled_tools()}
    assert not names & {"wolt_search", "wolt_menu", "get_drive_time"}
    assert "- Wolt:" not in agent.bot_rules() and "Drive times come from Waze" not in agent.bot_rules()


def test_addons_turn_on(monkeypatch):
    monkeypatch.setenv("WOLT_ENABLED", "1")
    monkeypatch.setenv("ETA_KEY", "k")
    names = {t["name"] for t in tools.enabled_tools()}
    assert {"wolt_search", "wolt_menu", "get_drive_time"} <= names
    assert "- Wolt:" in agent.bot_rules()


def test_personal_defaults_name_no_one():
    assert personal.GYM_NAME and personal.HOME_ADDRESS == "" and personal.FOOD_AVOID_WORDS == []
    assert wolt.NOT_WANTED is None
