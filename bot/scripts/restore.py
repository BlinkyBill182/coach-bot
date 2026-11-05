"""Restore a backup made by scripts/export.py (an extracted folder).

Usage: python scripts/restore.py <folder> [--force]
Without --force only missing documents are written and chat/reminders are left alone,
so it can't overwrite newer data by accident. With --force every document in the backup
replaces the current one, and chat and reminders are loaded when their tables are empty.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from coach.store import Store  # noqa: E402


def restore(store: Store, root: str, force: bool) -> dict:
    n = skipped = 0
    docs = os.path.join(root, "docs")
    for dirpath, _, files in os.walk(docs):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = os.path.relpath(os.path.join(dirpath, name), docs)[:-5].replace(os.sep, "/")
            if store.get(path) is not None and not force:
                skipped += 1
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                store.set(path, json.load(f))
            n += 1
    extra = {}
    if force:
        for table, cols in (("chat", ("chat_id", "role", "content", "ts")),
                            ("alarm", ("fire", "text", "repeat", "active", "created", "last_sent"))):
            fn = os.path.join(root, f"{table}.json")
            if not os.path.exists(fn) or store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                continue
            rows = json.load(open(fn, encoding="utf-8"))
            with store.lock, store.db:
                store.db.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                     [tuple(r.get(c, time.time() if c in ('ts', 'created') else None) for c in cols) for r in rows])
            extra[table] = len(rows)
    return {"documents": n, "skipped": skipped, **extra}


if __name__ == "__main__":
    print(restore(Store(os.environ.get("DB_PATH", "coach.db")), sys.argv[1], "--force" in sys.argv))
