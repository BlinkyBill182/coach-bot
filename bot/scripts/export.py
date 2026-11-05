"""Export the whole database as readable files, for the off-server backup.

Usage (inside the container): python scripts/export.py > export.tar.gz
Writes a gzipped tar to stdout:
  docs/<collection>/<id>.json   every document (log/2026-10-27.json, config/exercises.json, ...)
  chat.json, alarm.json, meta.json
Restore with scripts/restore.py <folder>.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from coach.store import Store  # noqa: E402


def build(store: Store) -> bytes:
    data = store.export()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, obj) -> None:
            raw = (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(raw), int(time.time())
            tar.addfile(info, io.BytesIO(raw))
        for path, doc in data["docs"].items():
            add(f"docs/{path}.json", doc)
        add("chat.json", data["chat"])
        add("alarm.json", data["alarm"])
        add("meta.json", data["meta"])
    return buf.getvalue()


if __name__ == "__main__":
    sys.stdout.buffer.write(build(Store(os.environ.get("DB_PATH", "coach.db"))))
