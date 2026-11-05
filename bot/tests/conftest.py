import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
SEED = os.path.join(os.path.dirname(__file__), "fixtures")  # config the tests were written against (copied from bot/seed)


@pytest.fixture
def store(tmp_path):
    from coach.store import Store
    s = Store(str(tmp_path / "t.db"))
    for coll in ("config",):
        d = os.path.join(SEED, coll)
        if os.path.isdir(d):
            for f in os.listdir(d):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    s.set(f"{coll}/{f[:-5]}", json.load(fh))
    return s


@pytest.fixture(autouse=True)
def no_eta(monkeypatch):
    """Waze stays off unless a test stubs it: no network in tests."""
    monkeypatch.delenv("ETA_KEY", raising=False)


@pytest.fixture(autouse=True)
def _default_program():
    """Tools may change targets and the week in memory; every test starts from the defaults."""
    from coach import domain
    domain.apply_program(None)
    yield
    domain.apply_program(None)
