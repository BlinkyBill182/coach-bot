"""Reminders the user asks for ("remind me at 18:00 to buy chicken"), kept in the database.

Times are Asia/Jerusalem local. repeat: none, daily, or weekdays (Sunday to Thursday,
the Israeli work week). A reminder missed by under 15 minutes (a restart, a deploy) still
goes out; an older one is skipped, and a repeating one moves on to its next time.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from . import domain
from .store import Store

REPEATS = ("none", "daily", "weekdays")
GRACE_MIN = 15
WORKDAYS = {6, 0, 1, 2, 3}  # Python weekday(): Sunday=6, Monday=0 ... Thursday=3


def parse_local(s: str) -> dt.datetime:
    """'YYYY-MM-DD HH:MM' in Israel time -> aware datetime."""
    try:
        naive = dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("datetime must be 'YYYY-MM-DD HH:MM' (Israel time)")
    return naive.replace(tzinfo=domain.TZ)


def fmt(t: dt.datetime) -> str:
    return t.astimezone(domain.TZ).strftime("%Y-%m-%d %H:%M")


def next_time(t: dt.datetime, repeat: str) -> Optional[dt.datetime]:
    """The next occurrence after t, or None for a one-off reminder.

    Adding days to a ZoneInfo datetime keeps the wall time, so 18:00 stays 18:00 across a clock change.
    """
    t = t.astimezone(domain.TZ)
    if repeat == "daily":
        return t + dt.timedelta(days=1)
    if repeat == "weekdays":
        n = t + dt.timedelta(days=1)
        while n.weekday() not in WORKDAYS:
            n += dt.timedelta(days=1)
        return n
    return None


def first_time(t: dt.datetime, repeat: str, now: dt.datetime) -> dt.datetime:
    """For a repeating reminder set in the past, roll forward to the next time after now."""
    while t <= now and repeat != "none":
        t = next_time(t, repeat)
    return t


def add(store: Store, when: str, text: str, repeat: str = "none", now: Optional[dt.datetime] = None) -> dict:
    now = now or domain.now()
    repeat = repeat or "none"
    if repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {REPEATS}")
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    t = first_time(parse_local(when), repeat, now)
    if t <= now:
        raise ValueError(f"{fmt(t)} is in the past (now {fmt(now)})")
    rid = store.add_alarm(t.timestamp(), text, repeat)
    return {"id": rid, "time": fmt(t), "text": text, "repeat": repeat}


def upcoming(store: Store) -> list[dict]:
    return [{"id": a["id"], "time": fmt(dt.datetime.fromtimestamp(a["fire"], domain.TZ)), "text": a["text"],
             "repeat": a["repeat"]} for a in store.active_alarms()]


def cancel(store: Store, rid: int) -> dict:
    a = store.get_alarm(rid)
    if a is None or not a["active"]:
        raise ValueError(f"no active reminder {rid}")
    store.finish_alarm(rid)
    return {"cancelled": rid, "text": a["text"]}


def take_due(store: Store, now: Optional[dt.datetime] = None) -> list[dict]:
    """Claim the reminders due now and return the ones to send.

    Each one is marked (moved to its next time, or finished) before it is returned, so it can
    never go out twice. Missed by more than GRACE_MIN: not sent.
    """
    now = now or domain.now()
    out = []
    for a in store.due_alarms(now.timestamp()):
        t = dt.datetime.fromtimestamp(a["fire"], domain.TZ)
        late = (now - t).total_seconds() / 60
        nxt = next_time(t, a["repeat"])
        while nxt is not None and nxt <= now:
            nxt = next_time(nxt, a["repeat"])
        if not store.claim_alarm(a["id"], a["fire"], nxt.timestamp() if nxt else None):
            continue  # another tick got it first
        if late <= GRACE_MIN:
            out.append({"id": a["id"], "text": a["text"], "time": fmt(t)})
    return out
