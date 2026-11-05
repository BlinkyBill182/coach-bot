"""Mini App server: serves tracker.html inside Telegram and a small REST store API.

The page is the same file published as the claude.ai artifact; web/shim.js gives it
the `claude.use("db")` interface backed by this server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

from aiohttp import web

from . import onboarding
from .store import Store

log = logging.getLogger(__name__)
HERE = os.path.dirname(__file__)
TRACKER = os.environ.get("TRACKER_PATH", os.path.join(HERE, "..", "..", "tracker.html"))
SHIM = os.path.join(HERE, "..", "web", "shim.js")
ONBOARDING_PAGE = os.path.join(HERE, "..", "web", "onboarding.html")
MAX_PHOTO = 8 * 1024 * 1024  # the page shrinks it first; this is only a guard
WEB_KEY = os.environ.get("WEB_KEY", "")  # optional: open the page outside Telegram with ?key=
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("WEB_HOST", "127.0.0.1")  # 0.0.0.0 inside Docker

SKELETON = (
    '<!doctype html><html><head><meta charset=utf8>'
    '<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);'
    'padding-bottom:env(safe-area-inset-bottom,0px)}html{scroll-padding-top:env(safe-area-inset-top,0px)}'
    'body{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;background:#fff;color:#000}'
    'img{max-width:100%}[hidden]:not([hidden=until-found i]){display:none!important}</style>'
    '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
    '<script src="shim.js"></script></head><body>'
)


def check_init_data(init_data: str, bot_token: str, owner_id: int, max_age: int = 7 * 86400) -> bool:
    """Validate Telegram WebApp initData and that it belongs to the owner."""
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return False
    received = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return False
    if time.time() - int(pairs.get("auth_date", "0")) > max_age:
        return False
    try:
        return json.loads(pairs.get("user", "{}")).get("id") == owner_id
    except ValueError:
        return False


def make_app(store: Store, bot_token: str, owner_id: int, describe=None, notify=None) -> web.Application:
    """describe: bytes -> description of a body photo (Coach.describe_build). notify: text -> sends it to the chat."""
    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path.startswith("/api/"):
            init = request.headers.get("X-Telegram-Init-Data", "")
            key = request.headers.get("X-Access-Key", "")
            ok = (init and check_init_data(init, bot_token, owner_id)) or (WEB_KEY and hmac.compare_digest(key, WEB_KEY))
            if not ok:
                return web.json_response({"code": "not_granted", "message": "open the tracker from the bot"}, status=403)
        return await handler(request)

    def err(code: str, msg: str, status: int = 400) -> web.Response:
        return web.json_response({"code": code, "message": msg}, status=status)

    async def page(request: web.Request) -> web.Response:
        with open(TRACKER, encoding="utf-8") as f:
            body = f.read()
        return web.Response(text=SKELETON + body + "</body></html>", content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    async def shim(request: web.Request) -> web.Response:
        with open(SHIM, encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="application/javascript", headers={"Cache-Control": "no-store"})

    async def list_collection(request: web.Request) -> web.Response:
        path = request.match_info["path"]
        try:
            docs = store.list(path)
        except ValueError as e:
            return err("invalid_argument", str(e))
        return web.json_response({"seq": store.seq(path), "docs": docs})

    async def get_doc(request: web.Request) -> web.Response:
        path = request.match_info["path"]
        try:
            d = store.get(path)
        except ValueError as e:
            return err("invalid_argument", str(e))
        coll = path.rsplit("/", 1)[0]
        return web.json_response({"seq": store.seq(coll), "exists": d is not None,
                                  "data": d["data"] if d else None, "version": d["version"] if d else None})

    async def write_doc(request: web.Request) -> web.Response:
        path = request.match_info["path"]
        try:
            if request.method == "DELETE":
                store.delete(path)
                return web.json_response({"ok": True})
            body = await request.json()
            if request.method == "PUT":
                v = store.set(path, body)
            else:
                v = store.update(path, body)
            return web.json_response({"ok": True, "version": v})
        except KeyError:
            return err("invalid_argument", "document does not exist")
        except (ValueError, json.JSONDecodeError) as e:
            return err("invalid_argument", str(e))

    # ---------- the questionnaire page ----------

    async def onboarding_page(request: web.Request) -> web.Response:
        with open(ONBOARDING_PAGE, encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html", headers={"Cache-Control": "no-store"})

    def invalid(e: onboarding.Invalid) -> web.Response:
        return web.json_response({"code": "invalid", "message": str(e), "step": e.step}, status=400)

    async def ob_state(request: web.Request) -> web.Response:
        return web.json_response(onboarding.state(store))

    async def ob_draft(request: web.Request) -> web.Response:
        try:
            return web.json_response({"draft": onboarding.save_draft(store, await request.json())})
        except onboarding.Invalid as e:
            return invalid(e)
        except json.JSONDecodeError:
            return err("invalid_argument", "bad JSON")

    async def ob_photo(request: web.Request) -> web.Response:
        """The body photo stays in memory for this one look. Nothing here writes it anywhere."""
        if describe is None:
            return err("unavailable", "photo analysis is off", 503)
        data = bytearray()
        async for chunk in request.content.iter_chunked(64 * 1024):
            data += chunk
            if len(data) > MAX_PHOTO:
                return err("too_large", "the photo is too large", 413)
        if bytes(data[:3]) != b"\xff\xd8\xff":
            return err("not_jpeg", "expected a JPEG", 415)
        build = await describe(bytes(data))
        if build:
            onboarding.set_build(store, build)
        return web.json_response({"build": build[:onboarding.MAX_BUILD] if build else None})

    async def ob_photo_clear(request: web.Request) -> web.Response:
        onboarding.set_build(store, None)
        return web.json_response({"ok": True})

    async def ob_review(request: web.Request) -> web.Response:
        try:
            return web.json_response(onboarding.review(store))
        except onboarding.Invalid as e:
            return invalid(e)

    async def ob_finish(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return err("invalid_argument", "bad JSON")
        try:
            text = onboarding.finish(store, bool(body.get("apply")), owner_id)
        except onboarding.Invalid as e:
            return invalid(e)
        except ValueError as e:  # the targets they saw aren't the ones that would change
            return err("stale_preview", str(e), 409)
        if notify:
            try:
                await notify(text)
            except Exception:  # it's saved; only the chat message is missing
                log.exception("could not send the questionnaire summary")
        return web.json_response({"text": text})

    async def ob_discard(request: web.Request) -> web.Response:
        onboarding.discard(store)
        return web.json_response({"ok": True})

    def static_page(title: str, body: str):
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{title}</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#13232E}}</style>'
                f'</head><body><h1>{title}</h1>{body}</body></html>')

        async def handler(request: web.Request) -> web.Response:
            return web.Response(text=html, content_type="text/html")
        return handler

    about = static_page("Coach", "<p>A private fitness coaching assistant for one person. It plans the day's meals and "
                        "workouts and fits the workout into the owner's Google Calendar. It is not offered to the public.</p>"
                        '<p><a href="/privacy">Privacy policy</a></p>')
    privacy = static_page("Privacy policy", "<p>Coach is a personal tool used only by its owner.</p>"
                          "<p><b>Google Calendar data.</b> Coach reads the owner's calendar events for a given day to find free time, and "
                          "updates only the time, description and reminder of the owner's own recurring gym event. It does not create or "
                          "delete other events, and does not store event contents beyond what is needed to plan that day.</p>"
                          "<p><b>Storage.</b> Meal, weight and workout logs and the Google access token are stored on the owner's private "
                          "server. Nothing is sold or shared with third parties. Messages are processed by Anthropic's Claude API to "
                          "answer the owner.</p><p><b>Removal.</b> The owner can revoke access at any time at "
                          "myaccount.google.com/permissions.</p>"
                          + (f"<p>Contact: {os.environ['CONTACT_EMAIL']}</p>" if os.environ.get("CONTACT_EMAIL") else ""))
    app = web.Application(middlewares=[auth], client_max_size=512 * 1024)
    app.router.add_get("/about", about)
    app.router.add_get("/privacy", privacy)
    app.router.add_get("/", page)
    app.router.add_get("/shim.js", shim)
    app.router.add_get("/onboarding", onboarding_page)
    app.router.add_get("/api/onboarding", ob_state)
    app.router.add_put("/api/onboarding/draft", ob_draft)
    app.router.add_post("/api/onboarding/photo", ob_photo)
    app.router.add_delete("/api/onboarding/photo", ob_photo_clear)
    app.router.add_post("/api/onboarding/review", ob_review)
    app.router.add_post("/api/onboarding/finish", ob_finish)
    app.router.add_post("/api/onboarding/discard", ob_discard)
    app.router.add_get("/api/c/{path:.+}", list_collection)
    app.router.add_get("/api/d/{path:.+}", get_doc)
    app.router.add_route("PUT", "/api/d/{path:.+}", write_doc)
    app.router.add_route("PATCH", "/api/d/{path:.+}", write_doc)
    app.router.add_route("DELETE", "/api/d/{path:.+}", write_doc)
    return app


async def start(store: Store, bot_token: str, owner_id: int, describe=None, notify=None) -> web.AppRunner:
    runner = web.AppRunner(make_app(store, bot_token, owner_id, describe, notify))
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    log.info("tracker web app on %s:%d", HOST, PORT)
    return runner
