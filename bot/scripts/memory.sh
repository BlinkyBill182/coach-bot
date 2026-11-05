#!/usr/bin/env bash
# Runs on your computer, with COACH_HOST=root@<server> in the environment. Reads or changes the bot's shared memory on the server.
#   memory.sh pull [--hours 72]   saved facts and recent Telegram chat
#   memory.sh add "fact"          save a fact the bot will see in every message
#   memory.sh forget <n>          delete fact n
# Used by the Claude Code SessionStart hook; prints a short note and exits 0 when the server can't be reached.
set -uo pipefail
HOST="${COACH_HOST:-}"
if [ -z "$HOST" ]; then
  [ "${1:-pull}" = "pull" ] && exit 0   # not set up: the SessionStart hook stays quiet
  echo "Set COACH_HOST=root@<server> first." >&2; exit 1
fi
args=""
for a in "$@"; do args+=" $(printf '%q' "$a")"; done
out=$(ssh -o BatchMode=yes -o ConnectTimeout=6 "$HOST" \
  "cd /docker/coach && docker compose exec -T coach python scripts/memory.py$args" 2>&1)
code=$?
if [ $code -ne 0 ]; then
  if [ "${1:-pull}" = "pull" ]; then
    echo "# Coach bot memory: could not reach the server ($HOST). Run bot/scripts/memory.sh pull before relying on bot memory."
    exit 0
  fi
  echo "$out" >&2
  exit $code
fi
echo "$out"
