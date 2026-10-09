"""Recite flow: grading attempts, line stats, chain state, overrides, re-sectioning."""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import chain
from app.core.align import grade, initial_prompt
from app.core.config import get_settings
from app.core.sectioning import UNITS, Unit, split_text
from app.core.struggle import StruggleState, record
from app.db.base import utcnow
from app.models import Child, LineStat, Passage, PassageAssignment, ReciteAttempt, Section

# --- passage sections -----------------------------------------------------------


def set_sections(db: Session, passage: Passage, texts: list[str]) -> None:
    """Replace a passage's sections, keeping rows (and their stats) whose text is unchanged.

    Assignments keep progress up to the first changed section; stats for changed
    sections are dropped with the rows (cascade).
    """
    old = list(passage.sections)
    old_texts = [s.text for s in old]
    matcher = difflib.SequenceMatcher(a=old_texts, b=texts, autojunk=False)
    keep: dict[int, Section] = {}  # new index -> existing row
    first_changed: int | None = None
    for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                keep[j1 + k] = old[i1 + k]
        elif first_changed is None:
            first_changed = j1

    kept_ids = {row.id for row in keep.values()}
    for row in old:
        if row.id not in kept_ids:
            passage.sections.remove(row)
    db.flush()
    rows: list[Section] = []
    for idx, text in enumerate(texts):
        row = keep.get(idx) or Section(text=text, ordinal=idx)
        row.ordinal = idx
        rows.append(row)
    passage.sections = rows
    db.flush()

    if first_changed is None and len(old) == len(texts):
        return
    for a in passage.assignments:
        if a.state is None:
            continue
        st = chain.rebase(
            chain.ChainState.from_json(a.state), len(texts), first_changed, a.child.chunk_size
        )
        _save_state(a, st)


def resection(db: Session, passage: Passage) -> None:
    unit: Unit = passage.chunk_unit if passage.chunk_unit in UNITS else "line"
    set_sections(db, passage, split_text(passage.text, unit))


# --- state ---------------------------------------------------------------------------


def _save_state(a: PassageAssignment, st: chain.ChainState) -> None:
    a.state = st.to_json()
    a.status = chain.status_of(st, started=True)
    a.current_section = st.n
    a.clean_full_runs = st.clean_full_runs
    if st.stage == "passed" and a.passed_at is None:
        a.passed_at = utcnow()
    elif st.stage != "passed":
        a.passed_at = None


def load_state(a: PassageAssignment) -> chain.ChainState | None:
    return None if a.state is None else chain.ChainState.from_json(a.state)


def ensure_started(db: Session, a: PassageAssignment) -> chain.ChainState:
    st = load_state(a)
    if st is None:
        st = chain.start(len(a.passage.sections), a.child.chunk_size)
        _save_state(a, st)
        db.commit()
    return st


def line_stats(db: Session, assignment_id: int) -> dict[int, LineStat]:
    rows = db.scalars(select(LineStat).where(LineStat.assignment_id == assignment_id))
    return {r.section_id: r for r in rows}


def _stat_snapshot(r: LineStat) -> dict[str, Any]:
    return {
        "struggle_score": r.struggle_score,
        "consecutive_correct": r.consecutive_correct,
        "flagged": r.flagged,
        "miss_count": r.miss_count,
        "attempt_count": r.attempt_count,
        "word_misses": dict(r.word_misses),
        "prev_score": r.prev_score,
    }


# --- attempts ------------------------------------------------------------------------


def scope_sections(a: PassageAssignment, st: chain.ChainState) -> list[Section]:
    sections = a.passage.sections
    return [sections[i] for i in chain.scope(st) if i < len(sections)]


def prompt_for(a: PassageAssignment) -> str:
    st = load_state(a) or chain.start(len(a.passage.sections), a.child.chunk_size)
    return initial_prompt([s.text for s in scope_sections(a, st)])


def submit_attempt(
    db: Session,
    a: PassageAssignment,
    transcript: str,
    *,
    peeked: bool,
    audio_seconds: float | None = None,
    stt_ms: int | None = None,
) -> ReciteAttempt:
    st = ensure_started(db, a)
    review = st.stage == "passed"
    if st.stage == "learn":
        st = chain.finish_learn(st)
        _save_state(a, st)
    if st.stage == "full":
        peeked = False  # no text or Listen in full mode; the UI hides both
    scoped = scope_sections(a, st)
    g = grade([s.text for s in scoped], transcript)
    clean = g.clean(a.pass_threshold)
    if peeked and get_settings().peek_invalidates:
        clean = False
    misses = [
        {
            "section_id": scoped[m.section].id,
            "word": m.word,
            "expected": m.expected,
            "heard": m.heard,
        }
        for m in g.misses
    ]
    attempt = ReciteAttempt(
        assignment_id=a.id,
        scope="review" if review else chain.scope_label(st),
        section_ids=[s.id for s in scoped],
        transcript=transcript,
        missed_words=misses,
        matched=g.matched,
        total=g.total,
        verdict=clean,
        peeked=peeked,
        audio_seconds=audio_seconds,
        stt_ms=stt_ms,
    )
    db.add(attempt)
    if not review:
        stats = line_stats(db, a.id)
        attempt.state_before = {
            "state": a.state,
            "lines": {str(s.id): _stat_snapshot(stats[s.id]) for s in scoped if s.id in stats},
        }
        _apply(db, a, st, scoped, misses, clean, stats)
    db.commit()
    return attempt


def _apply(
    db: Session,
    a: PassageAssignment,
    st: chain.ChainState,
    scoped: list[Section],
    misses: list[dict[str, Any]],
    clean: bool,
    stats: dict[int, LineStat],
) -> None:
    by_section: dict[int, list[int]] = defaultdict(list)
    for m in misses:
        by_section[m["section_id"]].append(int(m["word"]))
    # A line only takes a miss when the attempt failed: an attempt that counts as
    # clean (threshold under 100%, or a parent override) credits every line.
    ordinal_of = {s.id: s.ordinal for s in a.passage.sections}
    flagged_before = {
        ordinal_of[sid] for sid, r in stats.items() if r.flagged and sid in ordinal_of
    }
    missed_ordinals: list[int] = []
    for s in scoped:
        row = stats.get(s.id)
        if row is None:
            row = LineStat(
                assignment_id=a.id,
                section_id=s.id,
                struggle_score=0.0,
                consecutive_correct=0,
                flagged=False,
                miss_count=0,
                attempt_count=0,
                word_misses={},
                prev_score=0.0,
            )
            db.add(row)
            stats[s.id] = row
        missed = bool(by_section.get(s.id)) and not clean
        if by_section.get(s.id):
            missed_ordinals.append(s.ordinal)
        new = record(
            StruggleState(
                score=row.struggle_score,
                consecutive_correct=row.consecutive_correct,
                flagged=row.flagged,
                miss_count=row.miss_count,
                attempt_count=row.attempt_count,
            ),
            correct=not missed,
        )
        row.prev_score = row.struggle_score
        row.struggle_score = new.score
        row.consecutive_correct = new.consecutive_correct
        row.flagged = new.flagged
        row.miss_count = new.miss_count
        row.attempt_count = new.attempt_count
        if by_section.get(s.id):
            wm = dict(row.word_misses or {})
            for w in by_section[s.id]:
                wm[str(w)] = wm.get(str(w), 0) + 1
            row.word_misses = wm
    new_state = chain.apply_attempt(
        st,
        clean=clean,
        missed_sections=missed_ordinals,
        flagged_before=flagged_before,
        chunk=a.child.chunk_size,
        k_required=a.k_required,
    )
    _save_state(a, new_state)


def override_attempt(db: Session, attempt: ReciteAttempt, value: bool | None) -> bool:
    """Record a parent override. If it is the assignment's latest attempt and the
    effective verdict changes, replay it from the saved snapshot so the child gets
    (or loses) the credit. Returns True when progress was replayed."""
    before = attempt.effective_verdict
    attempt.override = value
    replayed = False
    a = attempt.assignment
    latest = db.scalar(
        select(ReciteAttempt.id)
        .where(ReciteAttempt.assignment_id == a.id)
        .order_by(ReciteAttempt.id.desc())
        .limit(1)
    )
    if (
        latest == attempt.id
        and attempt.state_before is not None
        and attempt.effective_verdict != before
        and attempt.state_before.get("state") is not None
    ):
        snap = attempt.state_before
        stats = line_stats(db, a.id)
        for sid, values in snap.get("lines", {}).items():
            row = stats.get(int(sid))
            if row is not None:
                for k, v in values.items():
                    setattr(row, k, v)
        # Lines first touched by this attempt start over from zero.
        for sid in attempt.section_ids:
            if str(sid) not in snap.get("lines", {}) and sid in stats:
                db.delete(stats.pop(sid))
        db.flush()
        a.state = snap["state"]
        st = chain.ChainState.from_json(snap["state"])
        if st.stage == "learn":
            st = chain.finish_learn(st)
        by_id = {s.id: s for s in a.passage.sections}
        scoped = [by_id[sid] for sid in attempt.section_ids if sid in by_id]
        _apply(db, a, st, scoped, attempt.missed_words, attempt.effective_verdict, stats)
        replayed = True
    db.commit()
    return replayed


def finish_learn(db: Session, a: PassageAssignment) -> None:
    st = ensure_started(db, a)
    _save_state(a, chain.finish_learn(st))
    db.commit()


def reset(db: Session, a: PassageAssignment) -> None:
    st = load_state(a)
    if st is None:
        return
    _save_state(a, chain.reset_to_partial(st, a.child.chunk_size))
    db.commit()


# --- views ---------------------------------------------------------------------------


def practice_view(db: Session, a: PassageAssignment, child: Child) -> dict[str, Any]:
    """Everything the practice page needs to render the current step."""
    st = load_state(a) or chain.start(len(a.passage.sections), child.chunk_size)
    stats = line_stats(db, a.id)
    sections = a.passage.sections
    learn = chain.learn_sections(st, child.chunk_size) if st.stage == "learn" else []
    return {
        "assignment_id": a.id,
        "title": a.passage.title,
        "reference": a.passage.reference,
        "status": a.status,
        "state": st.to_json(),
        "stage": st.stage,
        "scope": chain.scope(st),
        "learn": learn,
        "drill": asdict(st.drill_queue[0]) if st.stage == "drill" and st.drill_queue else None,
        "k_required": a.k_required,
        "total": st.total,
        "chunk": child.chunk_size,
        "show_text": child.show_text,
        "tts_rate": child.tts_rate,
        "sections": [
            {
                "id": s.id,
                "ordinal": s.ordinal,
                "text": s.text,
                "flagged": bool(stats.get(s.id) and stats[s.id].flagged),
                # Display-word indexes missed at least twice: underlined in practice.
                "weak_words": sorted(
                    int(w)
                    for w, c in (stats[s.id].word_misses if s.id in stats else {}).items()
                    if c >= 2
                ),
            }
            for s in sections
        ],
    }


def attempt_view(attempt: ReciteAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "scope": attempt.scope,
        "verdict": attempt.effective_verdict,
        "matched": attempt.matched,
        "total": attempt.total,
        "transcript": attempt.transcript,
        "misses": attempt.missed_words,
        "missed_section_ids": sorted({m["section_id"] for m in attempt.missed_words}),
        "stt_ms": attempt.stt_ms,
    }
