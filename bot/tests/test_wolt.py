import json
import os
import re

from coach import wolt

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "wolt")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def test_parse_venues_open_best_rated_with_links():
    out = wolt.parse_venues(load("nearby.json"))
    assert out and all(v["link"].startswith("https://wolt.com/he/isr/example-area/restaurant/") for v in out)
    ratings = [v["rating"] or 0 for v in out]
    assert ratings == sorted(ratings, reverse=True)
    sushi = wolt.parse_venues(load("nearby.json"), query="sushi")
    assert sushi and all("sushi" in (v["name"] + " ".join(v["tags"]) + v["about"]).lower() for v in sushi)


def test_parse_menu_drops_what_the_user_avoids(monkeypatch):
    monkeypatch.setattr(wolt, "NOT_WANTED", re.compile("סנדוויץ|טמפורה"))
    m = wolt.parse_menu(load("menu.json"))
    names = [d["name"] for c in m["categories"] for d in c["dishes"]]
    cats = [c["category"] for c in m["categories"]]
    assert names and not any("אלרג" in c or "לתשומת" in c for c in cats)
    assert not any(wolt.NOT_WANTED.search(n) for n in names)
    assert all(isinstance(d["price"], int) for c in m["categories"] for d in c["dishes"])


def test_nothing_is_hidden_by_default():
    assert wolt.NOT_WANTED is None
    assert wolt.parse_menu(load("menu.json"))["categories"]


def test_menu_rejects_a_bad_slug():
    import asyncio
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(wolt.menu("../etc"))
