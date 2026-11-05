import datetime as dt

from coach import domain, gcal


class FakeTasks:
    """Minimal stand-in for the Google Tasks v1 service."""

    def __init__(self, items):
        self.items = list(items)  # top of the list first
        self.lists = [{"id": "L", "title": gcal.TASK_LIST}]
        self.n = 0

    def tasklists(self):
        svc = self

        class TL:
            def list(self, **kw):
                return _Exec({"items": svc.lists})

            def insert(self, body):
                return _Exec({"id": "L2"})
        return TL()

    def tasks(self):
        svc = self

        class T:
            def list(self, **kw):
                return _Exec({"items": list(svc.items)})

            def delete(self, tasklist, task):
                svc.items = [t for t in svc.items if t["id"] != task]
                return _Exec({})

            def insert(self, tasklist, body):
                svc.n += 1
                svc.items.insert(0, {"id": f"n{svc.n}", **body, "status": "needsAction"})
                return _Exec({})
        return T()


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


def test_task_items(store):
    plan = domain.day_plan(store.schedule(), store.day("2026-10-19"))
    items = gcal.task_items(plan, ["A: לחיצת חזה (60×8)"], domain.PREP.get(0))
    assert items[0]["title"] == "08:00 קימה ושקילה"
    train = next(i for i in items if i["title"] == "09:00 אימון")
    assert "A: לחיצת חזה" in train["notes"]
    assert any("קק״ל" in i["notes"] for i in items)


def test_sync_keeps_done_replaces_open_and_clears_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(domain, "today", lambda: dt.date(2026, 10, 20))
    fake = FakeTasks([
        {"id": "old-open", "title": "x", "notes": "[coach 2026-10-18 #1]", "status": "needsAction"},
        {"id": "old-done", "title": "x", "notes": "[coach 2026-10-18 #2]", "status": "completed"},
        {"id": "mine-open", "title": "x", "notes": "[coach 2026-10-20 #0]", "status": "needsAction"},
        {"id": "mine-done", "title": "x", "notes": "[coach 2026-10-20 #1]", "status": "completed"},
        {"id": "bens-own", "title": "לקנות חלב", "notes": "", "status": "needsAction"},
    ])
    c = gcal.Calendar(str(tmp_path / "t.json"))
    c._tasks = lambda: fake
    items = [{"title": "08:00 קימה", "notes": "", "idx": 0}, {"title": "08:30 בננה", "notes": "", "idx": 1},
             {"title": "09:00 אימון", "notes": "", "idx": 2}]
    created = c.sync_day_tasks("2026-10-20", items)
    ids = [t["id"] for t in fake.items]
    assert created == 2  # #1 already checked off
    assert "old-open" not in ids and "mine-open" not in ids
    assert {"old-done", "mine-done", "bens-own"} <= set(ids)
    titles = [t["title"] for t in fake.items if t["id"].startswith("n")]
    assert titles == ["08:00 קימה", "09:00 אימון"]  # time order, top to bottom
    assert c.completed_tasks("2026-10-20") == [("mine-done", 1)]


def test_clear_open_tasks_keeps_ben_tasks_and_checked_ones():
    from coach.gcal import Calendar

    deleted = []

    class Req:
        def __init__(self, v=None):
            self.v = v

        def execute(self):
            return self.v

    class Tasks:
        def list(self, **kw):
            return Req({"items": [
                {"id": "a", "notes": "x\n[coach 2026-10-21 #1]", "status": "needsAction"},
                {"id": "b", "notes": "x\n[coach 2026-10-20 #2]", "status": "completed"},
                {"id": "c", "notes": "The user's own", "status": "needsAction"},
            ]})

        def delete(self, tasklist, task):
            deleted.append(task)
            return Req()

    class Svc:
        def tasks(self):
            return Tasks()

    cal = Calendar("/nonexistent")
    cal._tsvc = Svc()
    cal._list_id = lambda: "L"
    assert cal.clear_open_tasks() == 1
    assert deleted == ["a"]
