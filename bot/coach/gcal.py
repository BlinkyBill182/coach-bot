"""Google Calendar access: read a day's events and move the gym event.

Two ways to connect; either one turns the calendar on:
- A service account: its key file (google_service_account.json) plus GOOGLE_CALENDAR_ID, the
  calendar the owner shared with the service account's email (usually their Gmail address).
- An OAuth token created once with scripts/auth_google.py (google_token.json).
Without either the calendar is simply off and the bot plans from the template.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from typing import Optional

from . import domain

from .personal import GYM_NAME

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"]
SERVICE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TASK_LIST = "מאמן"
TAG = re.compile(r"\[coach (\d{4}-\d{2}-\d{2}) #(\d+)\]")
GYM_TITLE = "חדר כושר"


class Calendar:
    def __init__(self, token_path: str, calendar_id: str = "primary", service_account_path: str = ""):
        self.token_path = token_path
        self.calendar_id = calendar_id or "primary"
        self.service_account_path = service_account_path
        self._svc = None

    @property
    def uses_service_account(self) -> bool:
        # "primary" would be the service account's own calendar, which is empty: it needs the owner's calendar id.
        return (bool(self.service_account_path) and os.path.exists(self.service_account_path)
                and self.calendar_id != "primary")

    @property
    def enabled(self) -> bool:
        return self.uses_service_account or os.path.exists(self.token_path)

    def service_account_email(self) -> str:
        try:
            with open(self.service_account_path) as f:
                return json.load(f).get("client_email", "")
        except (OSError, ValueError, TypeError):
            return ""

    def status(self) -> str:
        """How the calendar is connected, for the startup log."""
        if self.uses_service_account:
            return f"service account {self.service_account_email()} on calendar {self.calendar_id}"
        if self.service_account_path and os.path.exists(self.service_account_path):
            if os.path.exists(self.token_path):
                return "OAuth token (the service account key is ignored: GOOGLE_CALENDAR_ID is empty)"
            return "off: the service account key is there, but GOOGLE_CALENDAR_ID is empty"
        if os.path.exists(self.token_path):
            return "OAuth token"
        return "off"

    def check(self) -> str:
        """Read today's events once, so a wrong setup shows in the log right after a deploy."""
        try:
            n = len(self.events_on(domain.today()))
            return f"calendar connected ({self.status()}): {n} events today"
        except Exception as e:  # noqa: BLE001 - any failure is reported, the bot keeps running
            hint = ""
            if self.uses_service_account:
                hint = (f" Share the calendar {self.calendar_id} with {self.service_account_email()}"
                        " and pick 'Make changes to events'.")
            return f"calendar error ({self.status()}): {e}.{hint}"

    def _reminders(self, leave_minutes: int) -> dict:
        # Reminders belong to the signed-in user. A service account's popup would never reach the owner,
        # so its events keep the owner's default reminders; the bot's own Telegram reminder still goes out.
        if self.uses_service_account:
            return {}
        return {"reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": leave_minutes}]}}

    def _creds(self):
        if self.uses_service_account:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(self.service_account_path,
                                                                         scopes=SERVICE_SCOPES)
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(self.token_path)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())
        return creds

    def _service(self):
        from googleapiclient.discovery import build
        if self._svc is None:
            self._svc = build("calendar", "v3", credentials=self._creds(), cache_discovery=False)
        return self._svc

    def _tasks(self):
        from googleapiclient.discovery import build
        if getattr(self, "_tsvc", None) is None:
            self._tsvc = build("tasks", "v1", credentials=self._creds(), cache_discovery=False)
        return self._tsvc

    @property
    def tasks_enabled(self) -> bool:
        if not self.enabled or self.uses_service_account:  # Google Tasks needs the owner's own sign-in
            return False
        try:
            with open(self.token_path) as f:
                return "https://www.googleapis.com/auth/tasks" in (json.load(f).get("scopes") or [])
        except (OSError, ValueError):
            return False

    # ---------- Google Tasks ----------

    def _list_id(self) -> str:
        svc = self._tasks()
        for tl in svc.tasklists().list(maxResults=100).execute().get("items", []):
            if tl.get("title") == TASK_LIST:
                return tl["id"]
        return svc.tasklists().insert(body={"title": TASK_LIST}).execute()["id"]

    def _all_tasks(self, lid: str) -> list[dict]:
        out, token = [], None
        while True:
            res = self._tasks().tasks().list(tasklist=lid, showCompleted=True, showHidden=True,
                                             maxResults=100, pageToken=token).execute()
            out += res.get("items", [])
            token = res.get("nextPageToken")
            if not token:
                return out

    def sync_day_tasks(self, ds: str, items: list[dict]) -> int:
        """Replace the open tasks for a day with `items` ({title, notes, idx}); keep checked-off ones.

        Also removes the bot's own unchecked tasks left from earlier days. Returns tasks created.
        """
        lid = self._list_id()
        svc = self._tasks()
        today = domain.today().isoformat()
        done_idx = set()
        for t in self._all_tasks(lid):
            m = TAG.search(t.get("notes") or "")
            if not m:
                continue  # not created by the bot
            tag_date, idx = m.group(1), int(m.group(2))
            completed = t.get("status") == "completed"
            if tag_date == ds and completed:
                done_idx.add(idx)
            elif tag_date == ds or (tag_date < today and not completed):
                svc.tasks().delete(tasklist=lid, task=t["id"]).execute()
        due = f"{ds}T00:00:00.000Z"
        created = 0
        for it in reversed(items):  # new tasks go on top, so insert the last one first
            if it["idx"] in done_idx:
                continue
            body = {"title": it["title"], "notes": f"{it['notes']}\n[coach {ds} #{it['idx']}]".strip(), "due": due}
            svc.tasks().insert(tasklist=lid, body=body).execute()
            created += 1
        return created

    def clear_open_tasks(self) -> int:
        """Delete every unchecked task the bot created (any date). The user's own tasks and checked ones stay."""
        lid = self._list_id()
        n = 0
        for t in self._all_tasks(lid):
            if TAG.search(t.get("notes") or "") and t.get("status") != "completed":
                self._tasks().tasks().delete(tasklist=lid, task=t["id"]).execute()
                n += 1
        return n

    def completed_tasks(self, ds: str) -> list[tuple[str, int]]:
        """(task id, plan index) of the bot's tasks for a day that the user checked off."""
        lid = self._list_id()
        out = []
        for t in self._all_tasks(lid):
            m = TAG.search(t.get("notes") or "")
            if m and m.group(1) == ds and t.get("status") == "completed":
                out.append((t["id"], int(m.group(2))))
        return out

    def events_on(self, d: dt.date) -> list[dict]:
        """Timed and all-day events for a local date: [{id, title, start, end, all_day}]."""
        start = dt.datetime.combine(d, dt.time(0), domain.TZ)
        end = start + dt.timedelta(days=1)
        res = self._service().events().list(
            calendarId=self.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100,
        ).execute()
        out = []
        for e in res.get("items", []):
            if e.get("status") == "cancelled":
                continue
            s, en = e.get("start", {}), e.get("end", {})
            if "dateTime" in s:
                out.append({
                    "id": e["id"], "title": e.get("summary", ""), "all_day": False,
                    "start": dt.datetime.fromisoformat(s["dateTime"]).astimezone(domain.TZ),
                    "end": dt.datetime.fromisoformat(en["dateTime"]).astimezone(domain.TZ),
                    "description": e.get("description", ""),
                    "location": e.get("location", ""),
                    "online": bool(e.get("hangoutLink") or (e.get("conferenceData") or {}).get("entryPoints")),
                })
            else:
                out.append({"id": e["id"], "title": e.get("summary", ""), "all_day": True,
                            "start": None, "end": None, "description": e.get("description", "")})
        return out

    @staticmethod
    def is_gym(e: dict) -> bool:
        return GYM_TITLE in (e.get("title") or "")

    def busy_minutes(self, events: list[dict], d: dt.date) -> list[tuple[int, int]]:
        """Busy intervals in minutes from midnight, excluding the gym event and all-day events."""
        out = []
        for e in events:
            if e["all_day"] or self.is_gym(e):
                continue
            s = e["start"] if e["start"].date() == d else dt.datetime.combine(d, dt.time(0), domain.TZ)
            en = e["end"] if e["end"].date() == d else dt.datetime.combine(d, dt.time(23, 59), domain.TZ)
            out.append((s.hour * 60 + s.minute, en.hour * 60 + en.minute))
        return out

    def move_gym(self, event: dict, d: dt.date, start_min: int, duration: int, title_line: str,
                 workout_lines: list[str], leave_minutes: int = 20) -> None:
        """Move one instance of the recurring gym event and rewrite its description."""
        start = dt.datetime.combine(d, dt.time(start_min // 60, start_min % 60), domain.TZ)
        end = start + dt.timedelta(minutes=duration)
        footer = ""
        desc = event.get("description") or ""
        if "---" in desc:
            footer = "\n\n---" + desc.split("---", 1)[1]
        body = {
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"},
            "description": "🏋️ " + title_line + ("\n" + "\n".join(workout_lines) if workout_lines else "") + footer,
            **self._reminders(leave_minutes),
        }
        self._service().events().patch(calendarId=self.calendar_id, eventId=event["id"], body=body).execute()
        log.info("moved gym event %s to %s", event["id"], start.isoformat())

    def create_gym(self, d: dt.date, start_min: int, duration: int, title_line: str,
                   location: str = GYM_NAME, leave_minutes: int = 20) -> dict:
        """Create a one-off gym event (the user asked for one on a day the series doesn't cover)."""
        start = dt.datetime.combine(d, dt.time(start_min // 60, start_min % 60), domain.TZ)
        end = start + dt.timedelta(minutes=duration)
        body = {
            "summary": f"💪🏾 {GYM_TITLE}", "location": location, "description": "🏋️ " + title_line,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"},
            **self._reminders(leave_minutes),
        }
        ev = self._service().events().insert(calendarId=self.calendar_id, body=body).execute()
        log.info("created gym event %s on %s", ev["id"], start.isoformat())
        return {"id": ev["id"], "title": body["summary"], "all_day": False, "start": start, "end": end,
                "description": body["description"], "location": location, "online": False}

    def delete_event(self, event_id: str) -> None:
        self._service().events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        log.info("deleted event %s", event_id)

    def gym_event(self, events: list[dict]) -> Optional[dict]:
        return next((e for e in events if self.is_gym(e) and not e["all_day"]), None)


def task_items(plan: list[dict], workout_lines: list[str], prep: Optional[str]) -> list[dict]:
    """One task per plan entry, in time order; idx is the entry's position in the plan."""
    out = []
    for i, x in enumerate(plan):
        notes = x.get("d") or ""
        if "p" in x:
            notes = (notes + "\n" if notes else "") + f"{domain.kcal(x):.0f} קק״ל, {x['p']:g} ג׳ חלבון"
        if x.get("buy"):
            notes = (notes + "\n" if notes else "") + x["buy"]
        if x["kind"] == "train" and workout_lines:
            notes = (notes + "\n" if notes else "") + "\n".join(workout_lines)
        out.append({"title": f"{x['t']} {x['n']}", "notes": notes, "idx": i})
    if prep:
        out.append({"title": "יום בישול", "notes": prep, "idx": 99})
    return out
