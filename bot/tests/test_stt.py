import asyncio

from aiohttp import web

from coach import stt


def test_transcribe_sends_hebrew_and_returns_text(monkeypatch):
    seen = {}

    async def handler(request):
        seen["key"] = request.headers.get("xi-api-key")
        form = await request.post()
        seen["lang"], seen["model"] = form["language_code"], form["model_id"]
        seen["audio"] = form["file"].file.read()
        return web.json_response({"text": " חתירה 67.5, עשר חזרות "})

    async def run():
        app = web.Application()
        app.router.add_post("/v1/speech-to-text", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(stt, "URL", f"http://127.0.0.1:{port}/v1/speech-to-text")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        try:
            return await stt.transcribe(b"OggS-fake")
        finally:
            await runner.cleanup()

    assert asyncio.run(run()) == "חתירה 67.5, עשר חזרות"
    assert seen == {"key": "test-key", "lang": "heb", "model": stt.MODEL, "audio": b"OggS-fake"}


def test_enabled_needs_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert not stt.enabled()
