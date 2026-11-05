"""Voice messages to text with ElevenLabs Scribe."""
from __future__ import annotations

import logging
import os

import aiohttp

log = logging.getLogger(__name__)
URL = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL = os.environ.get("STT_MODEL", "scribe_v1")


def enabled() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


async def transcribe(audio: bytes, filename: str = "voice.ogg", mime: str = "audio/ogg") -> str:
    """Hebrew-first transcription. Raises RuntimeError with the API's message on failure."""
    form = aiohttp.FormData()
    form.add_field("model_id", MODEL)
    form.add_field("language_code", "heb")
    form.add_field("tag_audio_events", "false")
    form.add_field("file", audio, filename=filename, content_type=mime)
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        async with s.post(URL, data=form, headers=headers) as r:
            body = await r.json(content_type=None)
            if r.status != 200:
                detail = body.get("detail") if isinstance(body, dict) else body
                raise RuntimeError(f"{r.status}: {detail}")
    return (body.get("text") or "").strip()
