"""Google Calendar through a service account: when it is on, and what it sends to Google."""
import datetime as dt
import json
import os

from coach import gcal

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
EMAIL = "coach-bot@coach-1.iam.gserviceaccount.com"
CAL = "owner@example.com"


def _key(tmp_path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    p = tmp_path / "google_service_account.json"
    p.write_text(json.dumps({"type": "service_account", "project_id": "coach-1", "private_key_id": "k1",
                             "private_key": pem, "client_email": EMAIL, "client_id": "1",
                             "token_uri": "https://oauth2.googleapis.com/token"}))
    return str(p)


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


class FakeEvents:
    def __init__(self, items=(), fail=None):
        self.items, self.fail, self.calls = list(items), fail, []

    def list(self, **kw):
        self.calls.append(("list", kw["calendarId"], kw))
        if self.fail:
            raise self.fail
        return _Exec({"items": self.items})

    def patch(self, calendarId, eventId, body):
        self.calls.append(("patch", calendarId, body))
        return _Exec({})

    def insert(self, calendarId, body):
        self.calls.append(("insert", calendarId, body))
        return _Exec({"id": "new1"})


class FakeService:
    def __init__(self, ev):
        self.ev = ev

    def events(self):
        return self.ev


def test_the_key_alone_is_not_enough(tmp_path):
    key = _key(tmp_path)
    for cid in ("", "primary"):
        c = gcal.Calendar(str(tmp_path / "none.json"), cid, key)
        assert not c.uses_service_account and not c.enabled
        assert "GOOGLE_CALENDAR_ID" in c.status()


def test_key_and_calendar_id_turn_it_on(tmp_path):
    c = gcal.Calendar(str(tmp_path / "none.json"), CAL, _key(tmp_path))
    assert c.uses_service_account and c.enabled
    assert not c.tasks_enabled
    assert EMAIL in c.status() and CAL in c.status()
    creds = c._creds()
    assert creds.service_account_email == EMAIL
    assert list(creds.scopes) == gcal.SERVICE_SCOPES


def test_oauth_token_still_works_and_nothing_means_off(tmp_path):
    token = tmp_path / "google_token.json"
    token.write_text("{}")
    c = gcal.Calendar(str(token), "", str(tmp_path / "no_key.json"))
    assert c.enabled and not c.uses_service_account and c.status() == "OAuth token"
    off = gcal.Calendar(str(tmp_path / "none.json"), "", str(tmp_path / "no_key.json"))
    assert not off.enabled and off.status() == "off"


def test_service_account_events_go_to_the_shared_calendar_without_popups(tmp_path):
    c = gcal.Calendar(str(tmp_path / "none.json"), CAL, _key(tmp_path))
    ev = FakeEvents()
    c._svc = FakeService(ev)
    d = dt.date(2026, 11, 8)
    c.move_gym({"id": "g1", "description": ""}, d, 9 * 60, 75, "Day 1 – Push", ["A: chest press"])
    c.create_gym(d, 17 * 60, 75, "Day 1 – Push")
    assert [x[0] for x in ev.calls] == ["patch", "insert"]
    assert all(x[1] == CAL for x in ev.calls)
    assert all("reminders" not in x[2] for x in ev.calls)


def test_oauth_events_keep_the_leave_popup(tmp_path):
    token = tmp_path / "google_token.json"
    token.write_text("{}")
    c = gcal.Calendar(str(token))
    ev = FakeEvents()
    c._svc = FakeService(ev)
    c.move_gym({"id": "g1", "description": ""}, dt.date(2026, 11, 8), 9 * 60, 75, "Day 1", [], leave_minutes=25)
    assert ev.calls[0][1] == "primary"
    assert ev.calls[0][2]["reminders"]["overrides"] == [{"method": "popup", "minutes": 25}]


def test_check_reports_events_or_how_to_fix_the_share(tmp_path):
    c = gcal.Calendar(str(tmp_path / "none.json"), CAL, _key(tmp_path))
    c._svc = FakeService(FakeEvents(items=[{"id": "e1", "summary": "x", "start": {"date": "2026-11-08"}}]))
    assert c.check().startswith("calendar connected") and "1 events today" in c.check()
    c._svc = FakeService(FakeEvents(fail=RuntimeError("404 Not Found")))
    msg = c.check()
    assert msg.startswith("calendar error") and f"Share the calendar {CAL} with {EMAIL}" in msg


def test_the_key_never_reaches_git_or_the_code_folder():
    for name in (".gitignore", "bot/.gitignore"):
        with open(os.path.join(REPO, name)) as f:
            assert "google_service_account.json" in f.read().split()
    with open(os.path.join(REPO, "bot", "deploy", "deploy.sh")) as f:
        deploy = f.read()
    assert "--exclude google_service_account.json" in deploy
    assert "for f in google_token.json google_service_account.json" in deploy
    with open(os.path.join(REPO, "bot", "deploy", "server-setup.sh")) as f:
        assert "^GOOGLE_SERVICE_ACCOUNT=" in f.read()
