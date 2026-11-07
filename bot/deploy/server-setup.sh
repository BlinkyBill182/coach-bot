#!/usr/bin/env bash
# Runs on the VPS (Hostinger Docker + Traefik template), once, as root.
# Detects Traefik's network, HTTPS entrypoint and certificate resolver, and writes /docker/coach/.env.
set -euo pipefail
DOMAIN="${1:?usage: server-setup.sh <domain>}"
cd /docker/coach
mkdir -p data && chown 1000:1000 data

TC="$(docker ps --format '{{.Names}}' | grep -i traefik | head -1 || true)"
EP="" RES=""
if [ -n "$TC" ]; then
  CFG="$(docker inspect "$TC" --format '{{join .Args " "}} {{join .Config.Cmd " "}} {{range .Config.Labels}}{{.}} {{end}}')"
  CFG="$CFG $(docker exec "$TC" sh -c 'cat /etc/traefik/traefik.y*ml /traefik.y*ml 2>/dev/null' || true)"
  EP="$(echo "$CFG" | grep -oE 'entrypoints\.[A-Za-z0-9_-]+\.address=:443' | head -1 | cut -d. -f2 || true)"
  RES="$(echo "$CFG" | grep -oE 'certificatesresolvers\.[A-Za-z0-9_-]+' | head -1 | cut -d. -f2 || true)"
fi
EP="${EP:-websecure}"; RES="${RES:-letsencrypt}"
echo "traefik container: ${TC:-none}  entrypoint: $EP  resolver: $RES"
if [ -z "$TC" ]; then
  echo "No Traefik container is running. Install the Traefik template in Hostinger's Docker Manager, or the tracker won't open in Telegram."
fi

if [ ! -f .env ]; then
  cp src/bot/.env.example .env
  sed -i '/^DB_PATH=\|^GOOGLE_TOKEN=\|^GOOGLE_SERVICE_ACCOUNT=\|^SKILL_PATH=\|^TRACKER_PATH=\|^PORT=/d' .env
  sed -i "s|^WEB_URL=.*|WEB_URL=https://$DOMAIN/|" .env
  sed -i "s|^WEB_KEY=.*|WEB_KEY=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')|" .env
  printf '\n# Docker / Traefik (detected by server-setup.sh)\nDOMAIN=%s\nTRAEFIK_ENTRYPOINT=%s\nTRAEFIK_CERTRESOLVER=%s\n' "$DOMAIN" "$EP" "$RES" >> .env
fi
chmod 600 .env
echo "Next: fill TELEGRAM_TOKEN and ANTHROPIC_API_KEY in /docker/coach/.env, then: cd /docker/coach && docker compose up -d --build"
