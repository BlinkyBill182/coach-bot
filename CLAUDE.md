# Telegram fitness coach

A personal coach bot for one owner. Before answering anything about food, weight, workouts or the tracker, load the **muscle-coach** skill (`.claude/skills/muscle-coach/SKILL.md`).

## Files
- `.claude/skills/muscle-coach/`: the bot's system prompt (`SKILL.md`) and evidence references (`exercises.md`, `nutrition.md`). Edit, then deploy.
- `bot/coach/personal.py`: gym, cooking days, shopping list, foods to hide. The one code file meant for personal details.
- `bot/coach/domain.py`: coaching rules. Keep it in sync with `tracker.html`, which holds the same rules for the page.
- `bot/coach/agent.py`: Claude calls and the bot's rules. `bot/coach/tools.py`: the tools Claude can use.
- `bot/coach/onboarding.py` and `bot/web/onboarding.html`: the questionnaire. `tracker.html`: the tracker page.
- `bot/starter/`: the plan a new install starts from. It is loaded once and never overwrites existing documents.
- `bot/deploy/`: Docker, Traefik labels and deploy scripts. `bot/SETUP.md`: installation steps.

## Rules
- Lasting changes to targets, the week, the exercise plan or the meal template go through preview first, then the owner's yes in a later turn (`ToolRunner._gate`).
- Live values are in the server database (`config/program`, `config/exercises`, `config/schedule`) and win over numbers in the skill.
- Secrets live only in `/docker/coach/.env` on the server. Never commit or print `.env`, `credentials.json` or `google_token.json`.
- Health data stays on the server. Never add logs, exports or backups to the repo.
- A push to `main` deploys once the GitHub secrets are set, so confirm with the owner before pushing.
- Add-ons are off until configured: Wolt (`WOLT_ENABLED=1`), drive times (`ETA_KEY`), voice (`ELEVENLABS_API_KEY`), calendar (`google_token.json`).

## Commands
- Tests, from `bot/`: `python -m pytest -q` (needs `pip install -r requirements.txt pytest`).
- Deploy: `bash bot/deploy/deploy.sh root@<server>`.
- Logs: `ssh root@<server> 'cd /docker/coach && docker compose logs --tail 50'`.
- Shared memory: with `COACH_HOST=root@<server>` set, `bot/scripts/memory.sh pull` shows the bot's saved facts and recent chat, and `memory.sh add "..."` saves a fact. Read them before coaching decisions.
