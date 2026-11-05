"""SQLite store with the same document model as the tracker artifact's db.

Documents live at slash paths (`log/2026-10-18`, `config/exercises`) so the
tracker page can run unchanged against /api through web/shim.js.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Optional

from . import domain

SEGMENT = re.compile(r"^[A-Za-z0-9_\-.~:@+]{1,200}$")
# Stays on the server: never in scripts/export.py, even if you keep that export somewhere safe.
# pending can hold a photo sent while the API had no credit; the other two are where the user is.
PRIVATE_META = ("kv:pending", "kv:last_location", "kv:move_state")


def check_path(path: str, doc: bool) -> list[str]:
    parts = path.split("/")
    if not all(SEGMENT.match(p) and p not in (".", "..") for p in parts):
        raise ValueError(f"bad path {path!r}")
    if (len(parts) % 2 == 0) != doc:
        raise ValueError(f"{path!r} is not a {'document' if doc else 'collection'} path")
    return parts


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs(
                  path TEXT PRIMARY KEY, collection TEXT NOT NULL, id TEXT NOT NULL,
                  data TEXT NOT NULL, version INTEGER NOT NULL, seq INTEGER NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS docs_collection ON docs(collection);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                  role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sent(key TEXT PRIMARY KEY, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS snooze(key TEXT PRIMARY KEY, fire REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS alarm(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, fire REAL NOT NULL, text TEXT NOT NULL,
                  repeat TEXT NOT NULL DEFAULT 'none', active INTEGER NOT NULL DEFAULT 1,
                  created REAL NOT NULL, last_sent REAL);
                """
            )

    # ---------- documents ----------

    def _next_seq(self) -> int:
        row = self.db.execute("SELECT value FROM meta WHERE key='seq'").fetchone()
        seq = int(row["value"]) + 1 if row else 1
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seq',?)", (str(seq),))
        return seq

    def seq(self, collection: Optional[str] = None) -> int:
        """Change counter; bumps on every write or delete in the collection."""
        key = f"seq:{collection}" if collection else "seq"
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return int(row["value"]) if row else 0

    def _touch(self, collection: str, seq: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (f"seq:{collection}", str(seq)))

    def get(self, path: str) -> Optional[dict]:
        check_path(path, doc=True)
        row = self.db.execute("SELECT data, version FROM docs WHERE path=?", (path,)).fetchone()
        return {"data": json.loads(row["data"]), "version": row["version"]} if row else None

    def data(self, path: str) -> Optional[dict]:
        d = self.get(path)
        return d["data"] if d else None

    def list(self, collection: str) -> list[dict]:
        check_path(collection, doc=False)
        rows = self.db.execute(
            "SELECT id, data, version FROM docs WHERE collection=? ORDER BY id", (collection,)
        ).fetchall()
        return [{"id": r["id"], "data": json.loads(r["data"]), "version": r["version"]} for r in rows]

    def set(self, path: str, data: dict) -> int:
        parts = check_path(path, doc=True)
        if not isinstance(data, dict):
            raise ValueError("document body must be an object")
        body = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if len(body.encode()) > 256 * 1024:
            raise ValueError("document over 256 KiB")
        collection, doc_id = "/".join(parts[:-1]), parts[-1]
        with self.lock, self.db:
            row = self.db.execute("SELECT version FROM docs WHERE path=?", (path,)).fetchone()
            version = (row["version"] + 1) if row else 1
            seq = self._next_seq()
            self.db.execute(
                "INSERT OR REPLACE INTO docs(path,collection,id,data,version,seq,updated) VALUES(?,?,?,?,?,?,?)",
                (path, collection, doc_id, body, version, seq, time.time()),
            )
            self._touch(collection, seq)
        return version

    def update(self, path: str, patch: dict) -> int:
        cur = self.get(path)
        if cur is None:
            raise KeyError(path)
        return self.set(path, deep_merge(cur["data"], patch))

    def delete(self, path: str) -> None:
        parts = check_path(path, doc=True)
        with self.lock, self.db:
            seq = self._next_seq()
            self.db.execute("DELETE FROM docs WHERE path=?", (path,))
            self._touch("/".join(parts[:-1]), seq)

    # ---------- typed helpers ----------

    def day(self, date_str: str) -> dict:
        return domain.norm_day(date_str, self.data(f"log/{date_str}"))

    def days(self) -> dict[str, dict]:
        return {r["id"]: domain.norm_day(r["id"], r["data"]) for r in self.list("log") if domain.DATE_RE.match(r["id"])}

    def save_day(self, d: dict) -> None:
        path = f"log/{d['date']}"
        if domain.is_empty(d):
            self.delete(path)
            return
        body = {k: d[k] for k in ("date", "type", "cycleDay", "weight", "cr", "items", "workout", "notes", "closed", "atHome", "targetAdj", "plan")}
        self.set(path, body)

    def library(self) -> tuple[list[dict], dict]:
        return domain.norm_library(self.data("config/exercises"))

    def schedule(self) -> dict:
        return domain.norm_schedule(self.data("config/schedule"))

    def program(self) -> dict:
        """config/program (targets, week), applied to domain so every rule uses the live values."""
        return domain.apply_program(self.data("config/program"))

    def travel(self) -> dict:
        return domain.norm_travel(self.data("config/travel"))

    def profile_notes(self) -> list[dict]:
        return (self.data("config/profile") or {}).get("notes") or []

    def set_profile_notes(self, notes: list[dict]) -> None:
        self.set("config/profile", {"notes": notes})

    def load_starter(self, folder: str) -> list[str]:
        """A new install gets the starter plan (bot/starter/<collection>/<id>.json); existing documents stay as they are."""
        written = []
        for dirpath, _, files in os.walk(folder):
            for name in sorted(files):
                if name.endswith(".json"):
                    path = os.path.relpath(os.path.join(dirpath, name), folder)[:-5].replace(os.sep, "/")
                    if self.get(path) is None:
                        with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                            self.set(path, json.load(f))
                        written.append(path)
        return written

    # ---------- backups ----------

    def backup_to(self, dest: str) -> None:
        """A consistent copy of the whole database (SQLite online backup)."""
        out = sqlite3.connect(dest)
        try:
            with self.lock:
                self.db.backup(out)
        finally:
            out.close()

    def export(self) -> dict:
        """Everything as plain data: documents by path, chat, reminders, and the bot's small state."""
        docs = {r["path"]: json.loads(r["data"]) for r in self.db.execute("SELECT path, data FROM docs ORDER BY path")}
        chat = [dict(r) for r in self.db.execute("SELECT chat_id, role, content, ts FROM chat ORDER BY id")]
        alarm = [dict(r) for r in self.db.execute("SELECT fire, text, repeat, active, created, last_sent FROM alarm ORDER BY id")]
        meta = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta WHERE key LIKE 'kv:%' ORDER BY key")
                if r["key"] not in PRIVATE_META}
        return {"docs": docs, "chat": chat, "alarm": alarm, "meta": meta}

    # ---------- chat history ----------

    def add_chat(self, chat_id: int, role: str, content: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO chat(chat_id,role,content,ts) VALUES(?,?,?,?)", (chat_id, role, content, time.time()))

    def recent_chat(self, chat_id: int, limit: int = 36, chunk: int = 16, days: int = 1) -> list[dict]:
        """The recent conversation, with a start that moves in jumps, not on every message.

        A stable start keeps the history's prompt cache valid from one message to the next: messages
        from yesterday and today (local days), and when there are more than `limit`, the oldest are
        dropped `chunk` at a time, so the window holds between limit-chunk and limit messages.
        """
        start = dt.datetime.combine(domain.today() - dt.timedelta(days=days), dt.time(0), domain.TZ).timestamp()
        rows = self.db.execute(
            "SELECT role, content FROM chat WHERE chat_id=? AND ts>=? ORDER BY id", (chat_id, start)).fetchall()
        excess = len(rows) - limit
        if excess > 0:  # drop whole chunks from the start
            rows = rows[((excess + chunk - 1) // chunk) * chunk:]
        msgs = [{"role": r["role"], "content": r["content"]} for r in rows]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    # ---------- messages waiting for API credit ----------

    def pending(self) -> list[dict]:
        return json.loads(self.get_meta("pending") or "[]")

    def set_pending(self, q: list[dict]) -> None:
        self.set_meta("pending", json.dumps(q[-20:], ensure_ascii=False))

    def add_pending(self, chat_id: int, content: Any, history_text: str) -> None:
        self.set_pending(self.pending() + [{"chat_id": chat_id, "content": content, "history": history_text, "ts": time.time()}])

    # ---------- small key-value state ----------

    def get_meta(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (f"kv:{key}",)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (f"kv:{key}", value))

    def del_meta(self, key: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM meta WHERE key=?", (f"kv:{key}",))

    # ---------- reminders ----------

    def mark_sent(self, key: str) -> bool:
        """True the first time a key is marked; False if it was already sent."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO sent(key,ts) VALUES(?,?)", (key, time.time()))
            return cur.rowcount == 1

    def was_sent(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone() is not None

    def add_snooze(self, key: str, fire: float, payload: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO snooze(key,fire,payload) VALUES(?,?,?)", (key, fire, json.dumps(payload, ensure_ascii=False)))

    # ---------- reminders the user asked for (coach/alarms.py) ----------

    def add_alarm(self, fire: float, text: str, repeat: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO alarm(fire,text,repeat,active,created) VALUES(?,?,?,1,?)",
                                  (fire, text, repeat, time.time()))
            return int(cur.lastrowid)

    def get_alarm(self, rid: int) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM alarm WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def active_alarms(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM alarm WHERE active=1 ORDER BY fire").fetchall()]

    def due_alarms(self, now_ts: float) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM alarm WHERE active=1 AND fire<=? ORDER BY fire", (now_ts,)).fetchall()]

    def claim_alarm(self, rid: int, fire: float, next_fire: Optional[float]) -> bool:
        """Mark a due reminder as handled: move it to next_fire, or finish it. False if already claimed."""
        with self.lock, self.db:
            if next_fire is None:
                cur = self.db.execute("UPDATE alarm SET active=0, last_sent=? WHERE id=? AND fire=? AND active=1",
                                      (time.time(), rid, fire))
            else:
                cur = self.db.execute("UPDATE alarm SET fire=?, last_sent=? WHERE id=? AND fire=? AND active=1",
                                      (next_fire, time.time(), rid, fire))
            return cur.rowcount == 1

    def finish_alarm(self, rid: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE alarm SET active=0 WHERE id=?", (rid,))

    def due_snoozes(self, now_ts: float) -> list[dict[str, Any]]:
        with self.lock, self.db:
            rows = self.db.execute("SELECT key, payload FROM snooze WHERE fire<=?", (now_ts,)).fetchall()
            self.db.execute("DELETE FROM snooze WHERE fire<=?", (now_ts,))
        return [{"key": r["key"], **json.loads(r["payload"])} for r in rows]
