import datetime as dt

from coach import agent, domain, reminders

D = dt.date(2026, 10, 20)


def ev(title, s, e, location=""):
    mk = lambda hm: dt.datetime.combine(D, dt.time(*map(int, hm.split(":"))), domain.TZ)
    return {"title": title, "start": mk(s), "end": mk(e), "all_day": False, "location": location, "description": ""}


def test_is_away():
    assert domain.is_away(ev("עוזר לחבר", "12:00", "14:00", "Herzl Street"))
    assert domain.is_away(ev("הופעה של חבר", "17:00", "23:55", "תואמה פגישת מוזיקה"))  # long
    assert not domain.is_away(ev("אין רכב", "14:30", "16:00"))
    assert not domain.is_away(ev("לבקש מחבר חומוס", "15:30", "16:00"))
    assert not domain.is_away(ev("שיחה", "10:00", "10:30", "https://meet.google.com/x"))


def test_meals_go_portable_when_out(store):
    events = [ev("עוזר לחבר", "12:00", "14:00", "Herzl Street"),
              ev("פגישה עם דנה", "14:30", "18:30", "Example Cafe")]
    away = domain.away_windows(events, D)
    assert len(away) == 1  # 14:00 -> 14:30 is one outing
    assert away[0]["s"] == 11 * 60 + 40 and away[0]["e"] == 18 * 60 + 50
    assert away[0]["title"] == "עוזר לחבר ואז פגישה עם דנה" and away[0]["location"] == "Example Cafe"
    busy = [(720, 840), (870, 1110)]
    plan = domain.build_plan(store.schedule()["train"], 540, 90, busy, away)
    outs = [x for x in plan if x.get("away")]
    assert outs, plan
    for x in outs:
        assert x["d"].startswith("בדרך (") and x["home"] and x["buy"] and "p" in x
    # the reminder day: an on-the-go meal reminder and a packing reminder before leaving
    day = store.day("2026-10-20")
    rs = {r.key: r for r in reminders.day_reminders(day, plan, [])}
    packs = [r for k, r in rs.items() if ":pack:" in k]
    assert packs and "לוקחים לדרך" in packs[0].text
    meal = next(r for r in rs.values() if "בדרך:" in r.text)
    assert "או לקנות" in meal.text and meal.buttons[0][0] == "✓ אכלתי"


def test_buy_text_sizes_and_no_beef():
    big, small = domain.buy_text(700, "Example Cafe"), domain.buy_text(300, "")
    assert "באזור Example Cafe" in big and "שווארמה עוף" in big
    assert "בסופר" in small
    assert "בקר" not in big + small


def test_context_lists_away_meals(store):
    d = store.day("2026-10-20")
    d["plan"] = [{"t": "15:00", "n": "ביניים", "d": "בדרך (פגישה): יוגורט", "kind": "meal", "p": 26, "f": 1.5, "c": 41,
                  "k": 280, "away": "פגישה עם דנה", "where": "Example Cafe", "leave": "14:10", "buy": "x"}]
    store.save_day(d)
    txt = agent.context_message(store, dt.datetime(2026, 10, 20, 9, 0, tzinfo=domain.TZ))
    assert "Away today: 15:00 ביניים during פגישה עם דנה at Example Cafe." in txt


def test_away_fields_survive_saving(store):
    d = store.day("2026-10-20")
    events = [ev("עוזר לחבר", "12:00", "14:00", "Herzl Street"), ev("פגישה עם דנה", "14:30", "18:30", "Example Cafe")]
    d["plan"] = domain.build_plan(store.schedule()["train"], 540, 90, [(720, 840), (870, 1110)], domain.away_windows(events, D))
    store.save_day(d)
    plan = domain.day_plan(store.schedule(), store.day("2026-10-20"))
    outs = [x for x in plan if x.get("away")]
    assert outs and all(x.get("leave") and x.get("buy") for x in outs)
    assert any(":pack:" in r.key for r in reminders.day_reminders(store.day("2026-10-20"), plan, []))


def test_separate_outings_and_short_location():
    events = [ev("עוזר לחבר 😎🏡", "12:00", "14:00", "Herzl Street, Tel Aviv"), ev("רופא 🥰", "16:00", "17:00", "Sample St 9, Tel Aviv")]
    away = domain.away_windows(events, D)
    assert [(a["title"], a["location"]) for a in away] == [("עוזר לחבר", "Herzl Street"), ("רופא", "Sample St 9")]


def test_travel_per_place(store, tmp_path):
    import asyncio, json
    from coach.gcal import Calendar
    from coach.tools import ToolRunner
    r = ToolRunner(store, Calendar(str(tmp_path / "none.json")))
    out, err = asyncio.run(r.run("set_travel_time", {"place": "Herzl", "minutes": 45}))
    assert not err
    travel = store.travel()
    assert travel == {"default": 20, "places": [{"match": "Herzl", "minutes": 45}]}
    events = [ev("עוזר לחבר", "12:00", "14:00", "Herzl Street"), ev("פגישה עם דנה", "14:30", "18:30", "Example Cafe")]
    w = domain.away_windows(events, D, travel)
    assert len(w) == 1 and w[0]["s"] == 12 * 60 - 45 and w[0]["e"] == 18 * 60 + 30 + 20  # back home from Example Cafe: 20
    solo = domain.away_windows([ev("רופא", "16:00", "17:00", "Sample St 9")], D, travel)
    assert solo[0]["s"] == 16 * 60 - 20


def test_pinned_meal_survives_replanning(store):
    tpl = store.schedule()["train"]
    lunch = next(dict(x) for x in tpl if x["n"] == "ארוחת צהריים")
    lunch.update(t="13:00", d="חזה עוף אצל חבר", pinned=True)
    events = [ev("עוזר לחבר", "12:00", "14:00", "Herzl Street")]
    travel = {"default": 20, "places": [{"match": "Herzl", "minutes": 45}]}
    plan = domain.build_plan(tpl, 540, 90, [(720, 840)], domain.away_windows(events, D, travel), [lunch])
    got = next(x for x in plan if x["n"] == "ארוחת צהריים")
    assert got["t"] == "13:00" and got["d"] == "חזה עוף אצל חבר" and not got.get("away")
    meals = sorted(domain.minutes(x["t"]) for x in plan if x["kind"] == "meal" and x["n"] not in ("לפני האימון", "אחרי האימון"))
    assert all(b - a >= 75 for a, b in zip(meals, meals[1:]))
