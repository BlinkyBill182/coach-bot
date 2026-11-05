#!/usr/bin/env bash
# Run once on your computer: setup-github-deploy.sh root@<server> <github-user>/<your-private-repo>. Lets GitHub Actions deploy the bot on every push to main:
#   1. makes a dedicated SSH key for deploys (~/.ssh/coach_deploy), without a passphrase
#   2. adds its public half to the VPS (root's authorized_keys), labelled github-deploy
#   3. stores the private half, the server's host key and its address as GitHub secrets of your repo
# To revoke: delete the "github-deploy" line in /root/.ssh/authorized_keys on the server and the secrets on GitHub.
set -euo pipefail
HOST="${1:?usage: setup-github-deploy.sh root@<server> <github-user>/<repo>}"
REPO="${2:?usage: setup-github-deploy.sh root@<server> <github-user>/<repo>}"
KEY="$HOME/.ssh/coach_deploy"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -C "github-deploy" -f "$KEY"
PUB="$(cut -d' ' -f2 "$KEY.pub")"
# The file may not end with a newline: add one first, so the key never joins the previous line.
ssh "$HOST" "mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && grep -qF '$PUB' ~/.ssh/authorized_keys || { [ -z \"\$(tail -c1 ~/.ssh/authorized_keys)\" ] || echo >> ~/.ssh/authorized_keys; cat >> ~/.ssh/authorized_keys; }" < "$KEY.pub"
gh secret set VPS_SSH_KEY --repo "$REPO" < "$KEY"
ssh-keyscan -t ed25519,rsa,ecdsa "${HOST#*@}" 2>/dev/null | gh secret set VPS_KNOWN_HOSTS --repo "$REPO"
printf '%s' "$HOST" | gh secret set VPS_HOST --repo "$REPO"
echo "Done. Test it: gh workflow run deploy --repo $REPO, then gh run watch --repo $REPO"
