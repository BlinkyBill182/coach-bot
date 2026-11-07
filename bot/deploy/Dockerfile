# Build context: the repo folder (bot/, .claude/skills/, tracker.html)
FROM python:3.12-slim
WORKDIR /app
COPY bot/requirements.txt bot/requirements.txt
RUN pip install --no-cache-dir -r bot/requirements.txt
COPY bot/ bot/
COPY .claude/skills/muscle-coach/ skill/muscle-coach/
COPY tracker.html tracker.html
WORKDIR /app/bot
ENV PYTHONUNBUFFERED=1 \
    SKILL_PATH=/app/skill/muscle-coach/SKILL.md \
    TRACKER_PATH=/app/tracker.html \
    DB_PATH=/data/coach.db \
    GOOGLE_TOKEN=/data/google_token.json \
    GOOGLE_SERVICE_ACCOUNT=/data/google_service_account.json \
    WEB_HOST=0.0.0.0 \
    PORT=8080
RUN useradd --uid 1000 --no-create-home coach
USER coach
CMD ["python", "-m", "coach.bot"]
