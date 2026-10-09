"""faster-whisper sidecar: POST /transcribe (multipart: audio, prompt) -> text + words.

Audio is decoded from memory (PyAV) and never written to disk. One model
instance, CPU int8; requests are serialized because CTranslate2 already uses
every core it is given and parallel decodes only add latency.
"""

import io
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel
from starlette.concurrency import run_in_threadpool

MODEL_NAME = os.environ.get("WHISPER_MODEL", "small.en")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "0"))  # 0 = all cores
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
MAX_BYTES = 10 * 1024 * 1024

log = logging.getLogger("whisper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_model: WhisperModel | None = None
_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    started = time.monotonic()
    _model = WhisperModel(
        MODEL_NAME, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=CPU_THREADS
    )
    log.info("model %s loaded in %.1fs", MODEL_NAME, time.monotonic() - started)
    yield


app = FastAPI(title="discito-whisper", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok" if _model is not None else "loading", "model": MODEL_NAME}


def _transcribe(data: bytes, prompt: str) -> dict:
    assert _model is not None
    with _lock:
        segments, info = _model.transcribe(
            io.BytesIO(data),
            language="en",
            beam_size=BEAM_SIZE,
            temperature=0.0,
            # The expected text biases decoding toward archaic words (thee, shalt, begat).
            initial_prompt=prompt or None,
            # Recitations are short; carrying context across windows invites hallucinated repeats.
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        segments = list(segments)
    words = [
        {"word": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2), "p": round(w.probability, 3)}
        for s in segments
        for w in (s.words or [])
    ]
    return {
        "text": " ".join(s.text.strip() for s in segments).strip(),
        "words": words,
        "duration": round(info.duration, 2),
    }


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), prompt: str = Form("")) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    started = time.monotonic()
    try:
        result = await run_in_threadpool(_transcribe, data, prompt[:1000])
    except Exception as exc:  # undecodable audio, etc.
        log.warning("transcribe failed: %s", exc)
        raise HTTPException(422, "could not decode audio") from exc
    finally:
        del data
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    log.info("transcribed %.1fs audio in %dms", result["duration"], result["elapsed_ms"])
    return result
