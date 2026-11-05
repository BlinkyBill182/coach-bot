"""Wolt: restaurants that deliver to the user now, and their menus, so the bot can suggest what to order.

Read-only, from Wolt's public web endpoints (the same data wolt.com shows): no login, no ordering.
Unofficial, so it may change; every call fails soft. A few requests when the user asks, cached 15 minutes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import aiohttp

from .personal import FOOD_AVOID_WORDS

log = logging.getLogger(__name__)
VENUES_URL = "https://consumer-api.wolt.com/v1/pages/restaurants"
MENU_URL = "https://consumer-api.wolt.com/consumer-api/consumer-assortment/v1/venues/slug/{slug}/assortment"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (CoachBot personal use)", "Accept-Language": "he"}
CACHE_S = 15 * 60
_cache: dict[str, tuple[float, Any]] = {}

# Dishes to hide (personal.FOOD_AVOID_WORDS). None: nothing is hidden.
NOT_WANTED = re.compile("|".join(map(re.escape, FOOD_AVOID_WORDS)), re.IGNORECASE) if FOOD_AVOID_WORDS else None
SKIP_CATEGORY = re.compile(r"שתי|משקאות|קינוח|אלכוהול|יין|בירה|אלרג|לתשומת|הודעה|רטבים|תוספות למנה|"
                           r"drink|dessert|alcohol|sauce", re.IGNORECASE)


def enabled() -> bool:
    """Off unless WOLT_ENABLED=1: the endpoints are unofficial, so turning this on is a choice."""
    return os.environ.get("WOLT_ENABLED", "") == "1"


async def _get(url: str, params: Optional[dict] = None) -> Any:
    key = url + json.dumps(params or {}, sort_keys=True)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers=HEADERS) as s:
        async with s.get(url, params=params) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            data = await r.json(content_type=None)
    _cache[key] = (time.time(), data)
    return data


async def geocode(place: str) -> Optional[tuple[float, float, str]]:
    """An address or place name in Israel -> (lat, lon, display name), via OpenStreetMap."""
    data = await _get(GEOCODE_URL, {"q": place, "format": "json", "limit": "1", "countrycodes": "il", "accept-language": "he"})
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", place)


def parse_venues(data: dict, query: str = "", max_minutes: int = 0, limit: int = 12) -> list[dict]:
    """Open venues that deliver, best rated first, optionally matching a word (name, tags, description)."""
    city = data.get("city") or ""
    q = (query or "").strip().lower()
    out = []
    for sec in data.get("sections") or []:
        for it in sec.get("items") or []:
            v = it.get("venue")
            if not v or not v.get("online") or not v.get("delivers"):
                continue
            text = " ".join([v.get("name") or "", v.get("short_description") or "", " ".join(v.get("tags") or [])]).lower()
            if q and q not in text:
                continue
            if max_minutes and (v.get("estimate") or 999) > max_minutes:
                continue
            score = (v.get("rating") or {}).get("score")
            out.append({
                "name": v.get("name"), "slug": v.get("slug"), "minutes": v.get("estimate_range") or v.get("estimate"),
                "rating": score, "price_level": v.get("price_range"), "tags": v.get("tags") or [],
                "about": v.get("short_description") or "", "address": v.get("address") or "",
                "link": f"https://wolt.com/he/isr/{city}/restaurant/{v.get('slug')}" if city else f"https://wolt.com/he/search?q={v.get('slug')}",
            })
    seen, uniq = set(), []
    for v in sorted(out, key=lambda x: -(x["rating"] or 0)):
        if v["slug"] not in seen:
            seen.add(v["slug"])
            uniq.append(v)
    return uniq[:limit]


def parse_menu(data: dict, max_items: int = 60) -> dict:
    """Dishes by category, without drinks/desserts and without what the user doesn't eat."""
    items = {i["id"]: i for i in data.get("items") or []}
    cats, dropped, n = [], 0, 0
    for c in data.get("categories") or []:
        if SKIP_CATEGORY.search(c.get("name") or ""):
            continue
        dishes = []
        for iid in c.get("item_ids") or []:
            it = items.get(iid)
            if not it or it.get("disabled_info"):
                continue
            desc = re.sub(r"\s+", " ", it.get("description") or "").strip()
            if NOT_WANTED and NOT_WANTED.search((it.get("name") or "") + " " + desc):
                dropped += 1
                continue
            if n >= max_items:
                break
            dishes.append({"name": it.get("name"), "price": round((it.get("price") or 0) / 100), "about": desc[:160]})
            n += 1
        if dishes:
            cats.append({"category": c.get("name"), "dishes": dishes})
    return {"categories": cats, "dishes": n, "left_out_for_beef_or_flour": dropped}


async def search(lat: float, lon: float, query: str = "", max_minutes: int = 0, limit: int = 12) -> list[dict]:
    return parse_venues(await _get(VENUES_URL, {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}"}), query, max_minutes, limit)


async def menu(slug: str, max_items: int = 60) -> dict:
    if not re.fullmatch(r"[a-z0-9\-]{2,120}", slug or ""):
        raise ValueError("slug must be the Wolt venue slug from wolt_search")
    return parse_menu(await _get(MENU_URL.format(slug=slug), {"language": "he"}), max_items)
