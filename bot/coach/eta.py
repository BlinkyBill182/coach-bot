"""Live drive times from a service of your own (the calls below show the API it must answer).

Every query starts from "me", the user's freshest location: their Telegram live location, which the bot forwards.
Off when ETA_KEY is unset; any failure returns None and the caller falls back to config/travel.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Optional

import aiohttp

from . import domain

log = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=8)
STALE_MIN = 30  # a location older than this is mentioned in the answer


def url() -> str:
    return os.environ.get("ETA_URL", "").rstrip("/")


def enabled() -> bool:
    return bool(os.environ.get("ETA_KEY"))


def _headers() -> dict:
    return {"X-API-Key": os.environ.get("ETA_KEY", "")}


async def eta(dest: str, origin: str = "me", depart_at: Optional[str] = None) -> dict:
    """The service's reply, or {"error", "status"} (status 0: off or no connection)."""
    if not enabled():
        return {"error": "ETA_KEY is not set", "status": 0}
    params = {"from": origin, "to": dest}
    if depart_at:
        params["depart_at"] = depart_at
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.get(f"{url()}/eta", params=params, headers=_headers()) as r:
                body = await r.json(content_type=None)
                if r.status != 200:
                    return {"error": (body or {}).get("error") or f"HTTP {r.status}", "status": r.status}
                return body
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}", "status": 0}


async def drive_minutes(dest: str, origin: str = "me", depart_at: Optional[str] = None) -> Optional[dict]:
    """{minutes, km, route, arrive_at, from_location?, ...}, or None on any error (logged)."""
    out = await eta(dest, origin, depart_at)
    if "error" in out:
        if enabled():
            log.warning("waze eta failed (%s): %s", out["status"], out["error"])
        return None
    return out


async def live_travel(events: list[dict], d: dt.date, travel: Optional[dict] = None) -> dict[str, int]:
    """{location: minutes} from "me" for the away events that start on d and have an address.

    Departs at the event start minus the manual estimate (travel), so Waze predicts traffic for the right hour.
    With no home address, the way back is taken to be as long as the way there.
    """
    if not enabled():
        return {}
    manual = travel or domain.norm_travel(None)
    jobs: dict[str, dt.datetime] = {}
    for e in domain.away_events(events):
        loc = domain.place_of(e)
        if not loc or loc in jobs or e["start"].date() != d:
            continue
        jobs[loc] = max(e["start"] - dt.timedelta(minutes=domain.travel_for(e, manual)), domain.now())
    if not jobs:
        return {}
    res = await asyncio.gather(*(drive_minutes(loc, "me", t.strftime("%Y-%m-%dT%H:%M")) for loc, t in jobs.items()))
    return {loc: int(r["minutes"]) for loc, r in zip(jobs, res) if r and r.get("minutes") is not None}


async def post_location(lat: float, lon: float, at: dt.datetime, accuracy: Optional[float] = None,
                        source: str = "telegram") -> bool:
    """Save where the user is. True when the service accepted it."""
    if not enabled():
        return False
    body = {"lat": lat, "lon": lon, "source": source, "at": at.isoformat()}
    if accuracy is not None:
        body["accuracy"] = accuracy
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.post(f"{url()}/location", json=body, headers=_headers()) as r:
                if r.status != 200:
                    log.warning("location post failed: HTTP %s", r.status)
                    return False
                return True
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("location post failed: %s", e)
        return False
