import io
import os
import sqlite3
import sys
import tarfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_backup_copy_is_a_full_database(store, tmp_path):
    store.set("log/2026-10-27", {"date": "2026-10-27", "weight": 71.5})
    dest = tmp_path / "b.db"
    store.backup_to(str(dest))
    row = sqlite3.connect(dest).execute("SELECT data FROM docs WHERE path='log/2026-10-27'").fetchone()
    assert "71.5" in row[0]


def test_export_and_restore_round_trip(store, tmp_path):
    import export
    import restore
    from coach.store import Store
    store.set("log/2026-10-27", {"date": "2026-10-27", "weight": 71.5})
    store.add_chat(1, "user", "שקלתי 71.5")
    tar = tarfile.open(fileobj=io.BytesIO(export.build(store)), mode="r:gz")
    tar.extractall(tmp_path / "x", filter="data")
    assert (tmp_path / "x" / "docs" / "log" / "2026-10-27.json").exists()
    fresh = Store(str(tmp_path / "new.db"))
    out = restore.restore(fresh, str(tmp_path / "x"), force=True)
    assert out["documents"] >= 1 and out["chat"] == 1
    assert fresh.data("log/2026-10-27")["weight"] == 71.5
    again = restore.restore(fresh, str(tmp_path / "x"), force=False)  # nothing overwritten without --force
    assert again["documents"] == 0
