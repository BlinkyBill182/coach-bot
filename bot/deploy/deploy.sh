#!/usr/bin/env bash
# Runs on your computer. Copies the code to the VPS and rebuilds the container.
#   deploy.sh root@<server>                    update code, rebuild, restart
#   deploy.sh root@<server> --first <domain>   first time: folders, .env with Traefik settings
#   deploy.sh root@<server> --seed             also load bot/seed/ (documents you exported yourself)
set -euo pipefail
HOST="${1:?usage: deploy.sh root@<server> [--first <domain>] [--seed]}"; shift
FIRST=""; SEED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --first) FIRST="${2:?--first needs a domain}"; shift 2 ;;
    --seed) SEED=1; shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # the repo folder

ssh "$HOST" 'mkdir -p /docker/coach/src/bot /docker/coach/src/.claude/skills/muscle-coach /docker/coach/data'
rsync -az --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude seed --exclude '*.db' \
  --exclude .env --exclude credentials.json --exclude google_token.json --exclude google_service_account.json "$ROOT/bot/" "$HOST:/docker/coach/src/bot/"
rsync -az --delete "$ROOT/.claude/skills/muscle-coach/" "$HOST:/docker/coach/src/.claude/skills/muscle-coach/"
rsync -az "$ROOT/tracker.html" "$HOST:/docker/coach/src/tracker.html"
rsync -az "$ROOT/bot/deploy/compose.yaml" "$HOST:/docker/coach/compose.yaml"
for f in google_token.json google_service_account.json; do   # Google Calendar access, if you set it up
  if [ -f "$ROOT/bot/$f" ]; then
    rsync -az "$ROOT/bot/$f" "$HOST:/docker/coach/data/$f"
    ssh "$HOST" "chown 1000:1000 /docker/coach/data/$f && chmod 600 /docker/coach/data/$f"
  fi
done
if [ -n "$FIRST" ]; then
  ssh "$HOST" "bash /docker/coach/src/bot/deploy/server-setup.sh '$FIRST'"
fi
if ! ssh "$HOST" 'grep -q "^TELEGRAM_TOKEN=." /docker/coach/.env && grep -q "^ANTHROPIC_API_KEY=." /docker/coach/.env'; then
  echo "Secrets missing: fill TELEGRAM_TOKEN and ANTHROPIC_API_KEY in /docker/coach/.env, then run deploy.sh again."
  exit 0
fi
ssh "$HOST" 'cd /docker/coach && docker compose build -q && docker compose up -d'
if [ -n "$SEED" ]; then
  rsync -az "$ROOT/bot/seed/" "$HOST:/docker/coach/data/seed/"
  ssh "$HOST" 'chown -R 1000:1000 /docker/coach/data && cd /docker/coach && docker compose exec -T coach python scripts/seed.py /data/seed'
fi
ssh "$HOST" 'cd /docker/coach && sleep 3 && docker compose ps && docker compose logs --tail 15 coach'
