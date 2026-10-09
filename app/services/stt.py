"""Client for the faster-whisper sidecar.

Audio bytes are streamed straight from the request to the sidecar and never
touch disk on either side; the sidecar decodes from memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings


class SttError(RuntimeError):
    """The sidecar failed. `status` is its HTTP status when it answered at all:
    4xx means this clip was undecodable, anything else means the service is down."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def bad_clip(self) -> bool:
        return self.status is not None and 400 <= self.status < 500


@dataclass
class Transcript:
    text: str
    duration: float | None
    elapsed_ms: int


def transcribe(audio: bytes, content_type: str, prompt: str) -> Transcript:
    settings = get_settings()
    started = time.monotonic()
    try:
        resp = httpx.post(
            f"{settings.whisper_url.rstrip('/')}/transcribe",
            files={"audio": ("clip.webm", audio, content_type or "audio/webm")},
            data={"prompt": prompt},
            timeout=settings.whisper_timeout_s,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError as exc:
        raise SttError(f"transcription failed: {exc}", exc.response.status_code) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SttError(f"transcription failed: {exc}") from exc
    return Transcript(
        text=str(body.get("text", "")),
        duration=body.get("duration"),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
