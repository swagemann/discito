"""JSON endpoints driving the practice pages (static/js/recite.js, spell.js)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session
from app.models import ListAssignment, PassageAssignment
from app.services import recite as recite_svc
from app.services import spelling as spelling_svc
from app.services.stt import SttError, transcribe
from app.web import require_household

router = APIRouter(prefix="/api", dependencies=[Depends(require_household)])
log = get_logger("app.api")

# ~10 MB of opus is far beyond any recitation; stops runaway uploads.
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def _recite(db: Session, assignment_id: int) -> PassageAssignment:
    a = db.get(PassageAssignment, assignment_id)
    if a is None or a.passage.archived:
        raise HTTPException(404)
    return a


def _list(db: Session, assignment_id: int) -> ListAssignment:
    a = db.get(ListAssignment, assignment_id)
    if a is None or a.spelling_list.archived:
        raise HTTPException(404)
    return a


@router.get("/recite/{assignment_id}")
def recite_state(assignment_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _recite(db, assignment_id)
    recite_svc.ensure_started(db, a)
    return recite_svc.practice_view(db, a, a.child)


@router.post("/recite/{assignment_id}/ready")
def recite_ready(assignment_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _recite(db, assignment_id)
    recite_svc.finish_learn(db, a)
    return recite_svc.practice_view(db, a, a.child)


@router.post("/recite/{assignment_id}/attempt")
def recite_attempt(
    assignment_id: int,
    audio: UploadFile | None = File(None),
    typed: str = Form(""),
    peeked: bool = Form(False),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    a = _recite(db, assignment_id)
    recite_svc.ensure_started(db, a)
    audio_seconds: float | None = None
    stt_ms: int | None = None
    if audio is not None:
        data = audio.file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "recording too long")
        try:
            result = transcribe(data, audio.content_type or "audio/webm", recite_svc.prompt_for(a))
        except SttError as exc:
            log.warning("stt.failed", error=str(exc), assignment_id=a.id)
            raise HTTPException(502, "Couldn't hear that — the listening service is down.") from exc
        finally:
            del data  # audio is never persisted
        transcript = result.text
        audio_seconds, stt_ms = result.duration, result.elapsed_ms
        log.info("stt.done", assignment_id=a.id, audio_s=audio_seconds, stt_ms=stt_ms)
    elif get_settings().stt_typed_fallback:
        transcript = typed
    else:
        raise HTTPException(400, "no audio")
    attempt = recite_svc.submit_attempt(
        db, a, transcript, peeked=peeked, audio_seconds=audio_seconds, stt_ms=stt_ms
    )
    view = recite_svc.practice_view(db, a, a.child)
    view["attempt"] = recite_svc.attempt_view(attempt)
    return view


@router.get("/spell/{assignment_id}")
def spell_state(assignment_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    return spelling_svc.session_view(db, _list(db, assignment_id))


@router.post("/spell/{assignment_id}/start")
def spell_start(
    assignment_id: int, mode: str = Form(...), db: Session = Depends(get_session)
) -> dict[str, Any]:
    a = _list(db, assignment_id)
    spelling_svc.start(db, a, mode)
    return spelling_svc.session_view(db, a)


@router.post("/spell/{assignment_id}/answer")
def spell_answer(
    assignment_id: int, typed: str = Form(""), db: Session = Depends(get_session)
) -> dict[str, Any]:
    a = _list(db, assignment_id)
    feedback = spelling_svc.answer(db, a, typed[:80])
    view = spelling_svc.session_view(db, a)
    view["feedback"] = feedback
    return view
