"""Shared memory between the bot and Claude Code on your computer.

The bot's saved facts (config/profile notes) are the one list both sides read and
write. Run inside the container (bot/scripts/memory.sh does that over ssh):
  python scripts/memory.py pull [--hours 72]   saved facts plus recent Telegram chat
  python scripts/memory.py add "fact"           save a fact (source: claude-code)
  python scripts/memory.py forget <n>           delete fact n
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from coach import domain, onboarding  # noqa: E402
from coach.store import Store  # noqa: E402

MAX_MSG = 280
MAX_MSGS = 40


def notes_text(notes: list[dict]) -> str:
    if not notes:
        return "No saved facts."
    return "\n".join(f"{i}) [{n.get('date', '?')}, {n.get('source', 'bot')}] {n['text']}" for i, n in enumerate(notes))


def program_text(store: Store) -> str:
    """The live program: what the bot and the tracker use now (the bot can change it with the user's yes)."""
    store.program()
    out = ["Live program (config/program, config/exercises, config/schedule; these win over SKILL.md numbers):"]
    out.append("Targets: " + "; ".join(f"{typ} {g['k']:g} kcal P{g['p']:g} F{g['f']:g} C{g['c']:g}" for typ, g in domain.TARGETS.items()))
    out.append("Week: " + ", ".join(f"{k} {domain.WEEKDAY_DAY[(i - 1) % 7]} {domain.CYCLE[domain.WEEKDAY_DAY[(i - 1) % 7]]}"
                                    for i, k in enumerate(domain.WEEK_KEYS)))
    items, plans = store.library()
    for n in sorted(plans):
        out.append(f"Day {n} {domain.CYCLE[n]}: " + " · ".join((e.get("ss") or "-") + ":" + e["name"] for e in plans[n]))
    sched = store.schedule()
    for typ in ("train", "rest"):
        meals = [x for x in sched[typ] if x["kind"] == "meal" and "p" in x]
        out.append(f"Meal template {typ}: " + ", ".join(f"{x['t']} {x['n']}" for x in sched[typ])
                   + f" = {sum(domain.kcal(x) for x in meals):.0f} kcal, P{sum(x['p'] for x in meals):.0f}")
    base = onboarding.base_line(store)
    if base:
        out.append(base)
    return "\n".join(out) + "\n"


def usage_text(store: Store, days: int = 7) -> str:
    """Claude API spend per day as the bot recorded it (estimates from list prices)."""
    out = []
    for i in range(days - 1, -1, -1):
        d = (domain.today() - dt.timedelta(days=i)).isoformat()
        u = json.loads(store.get_meta(f"usage:{d}") or "{}")
        if u:
            out.append(f"{d}: " + ", ".join(f"{m.replace('claude-', '')} {v['calls']} calls ${v['usd']:.2f}" for m, v in u.items())
                       + f" = ${sum(v['usd'] for v in u.values()):.2f}")
    return "API usage (bot estimate):\n" + ("\n".join(out) if out else "nothing recorded yet") + "\n"


def chat_text(store: Store, hours: float) -> str:
    rows = store.db.execute(
        "SELECT role, content, ts FROM chat WHERE ts>? ORDER BY id DESC LIMIT ?",
        (time.time() - hours * 3600, MAX_MSGS),
    ).fetchall()
    out = []
    for r in reversed(rows):
        when = dt.datetime.fromtimestamp(r["ts"], domain.TZ).strftime("%m-%d %H:%M")
        text = " ".join(r["content"].split())
        if len(text) > MAX_MSG:
            text = text[:MAX_MSG] + "…"
        out.append(f"{when} {'the user' if r['role'] == 'user' else 'bot'}: {text}")
    return "\n".join(out) or "No messages."


def main(argv: list[str]) -> None:
    store = Store(os.environ.get("DB_PATH", "coach.db"))
    cmd = argv[0] if argv else "pull"
    notes = store.profile_notes()
    if cmd == "pull":
        hours = float(argv[argv.index("--hours") + 1]) if "--hours" in argv else 72
        print("# Coach bot memory (live from the server, shared with the Telegram bot)")
        print(program_text(store))
        print(usage_text(store))
        print("Saved facts (config/profile notes; the bot sees these in every message):")
        print(notes_text(notes))
        print(f"\nTelegram chat, last {hours:g} h (newest last, up to {MAX_MSGS} messages):")
        print(chat_text(store, hours))
    elif cmd == "add":
        text = " ".join(argv[1:]).strip()
        if not text:
            sys.exit("add needs the fact text")
        notes.append({"text": text, "date": domain.today().isoformat(), "source": "claude-code"})
        store.set_profile_notes(notes)
        print(f"saved as {len(notes) - 1}")
    elif cmd == "forget":
        i = int(argv[1])
        if not 0 <= i < len(notes):
            sys.exit(f"no fact {i}")
        removed = notes.pop(i)
        store.set_profile_notes(notes)
        print(f"removed: {removed['text']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
