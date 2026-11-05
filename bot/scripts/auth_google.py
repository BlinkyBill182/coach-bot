"""One-time Google Calendar sign-in. Run on your Mac, then copy the token to the server.

1. In Google Cloud Console create an OAuth client of type "Desktop app" and download
   its JSON as credentials.json next to this script's folder (bot/credentials.json).
2. Run:  .venv/bin/python scripts/auth_google.py
3. A browser opens; sign in and allow calendar access.
4. google_token.json is written; deploy.sh copies it to the server.
"""
from __future__ import annotations

import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"]
HERE = os.path.join(os.path.dirname(__file__), "..")

flow = InstalledAppFlow.from_client_secrets_file(os.path.join(HERE, "credentials.json"), SCOPES)
creds = flow.run_local_server(port=0)
with open(os.path.join(HERE, "google_token.json"), "w") as f:
    f.write(creds.to_json())
print("Saved google_token.json")
