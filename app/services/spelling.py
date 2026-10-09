"""Spelling flow: sessions, answers, word stats, list status, carry-forward."""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import spelling as sp
from app.core.struggle import StruggleState, record
from app.db.base import utcnow
from app.models import ListAssignment, SpellingAttempt, SpellingList, SpellingWord, WordStat

_rng = random.SystemRandom()


def parse_words(raw: str) -> list[str]:
    """Bulk add: one word per line or comma-separated; duplicates dropped, order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        w = chunk.strip()
        if w and w.casefold() not in seen:
            seen.add(w.casefold())
            out.append(w)
    return out


def words_for(db: Session, a: ListAssignment) -> list[SpellingWord]:
    """List words plus this child's carried-forward review words."""
    return list(
        db.scalars(
            select(SpellingWord)
            .where(
                SpellingWord.list_id == a.list_id,
                or_(SpellingWord.for_child_id.is_(None), SpellingWord.for_child_id == a.child_id),
            )
            .order_by(SpellingWord.ordinal, SpellingWord.id)
        )
    )


def word_stats(db: Session, a: ListAssignment) -> dict[int, WordStat]:
    rows = db.scalars(select(WordStat).where(WordStat.assignment_id == a.id))
    return {r.word_id: r for r in rows}


def get_state(a: ListAssignment) -> dict[str, Any]:
    return {**sp.idle_state(a.round_no), **a.state} if a.state else sp.idle_state(a.round_no)


def _save(a: ListAssignment, state: dict[str, Any]) -> None:
    a.state = state
    a.round_no = int(state["round_no"])


def _sync_status(db: Session, a: ListAssignment) -> None:
    if a.status == "passed":
        return
    if a.state is not None and (
        a.state.get("mode") or a.state.get("round_no") or a.state.get("last_test")
    ):
        a.status = "practicing"


def start(db: Session, a: ListAssignment, mode: str) -> dict[str, Any]:
    words = [w.id for w in words_for(db, a)]
    state = get_state(a)
    if mode == "learn":
        state = sp.start_learn(state, words)
    elif mode == "practice":
        flagged = {wid for wid, r in word_stats(db, a).items() if r.flagged}
        state = sp.start_practice(state, words, flagged, _rng)
    elif mode == "test":
        state = sp.start_test(state, words, _rng)
    else:
        state = sp.pause(state)
    _save(a, state)
    _sync_status(db, a)
    db.commit()
    return state


def _record_stat(db: Session, a: ListAssignment, word_id: int, correct: bool, mode: str) -> None:
    stats = word_stats(db, a)
    row = stats.get(word_id)
    if row is None:
        row = WordStat(assignment_id=a.id, word_id=word_id)
        db.add(row)
        db.flush()
    new = record(
        StruggleState(
            score=row.struggle_score,
            consecutive_correct=row.consecutive_correct,
            flagged=row.flagged,
            miss_count=row.miss_count,
            attempt_count=row.attempt_count,
        ),
        correct,
    )
    row.prev_score = row.struggle_score
    row.struggle_score = new.score
    row.consecutive_correct = new.consecutive_correct
    row.flagged = new.flagged
    row.miss_count = new.miss_count
    row.attempt_count = new.attempt_count
    row.status = sp.next_word_status(row.status, new.consecutive_correct, correct, mode)  # type: ignore[arg-type]


def answer(db: Session, a: ListAssignment, typed: str) -> dict[str, Any]:
    """Grade the current word. Returns feedback for the UI (no reveal in test mode)."""
    state = get_state(a)
    word_id = sp.current_word(state)
    if word_id is None:
        return {"ok": False, "error": "no current word"}
    word = db.get(SpellingWord, word_id)
    if word is None:
        return {"ok": False, "error": "word missing"}
    mode = state["mode"]
    correct = sp.is_correct(word.word, typed)
    db.add(
        SpellingAttempt(
            assignment_id=a.id, word_id=word_id, mode=mode, typed=typed, correct=correct
        )
    )
    feedback: dict[str, Any] = {"ok": True, "mode": mode}

    if mode == "learn":
        state = sp.answer_learn(state, correct)
        feedback.update(correct=correct, word=word.word)
    elif mode == "practice":
        _record_stat(db, a, word_id, correct, "practice")
        db.flush()
        words = [w.id for w in words_for(db, a)]
        flagged = {wid for wid, r in word_stats(db, a).items() if r.flagged}
        state, event = sp.answer_practice(state, word_id, correct, words, flagged, _rng)
        feedback.update(correct=correct, word=word.word, event=event)
    else:
        _record_stat(db, a, word_id, correct, "test")
        state, result = sp.answer_test(
            state, word_id, typed, correct, a.spelling_list.pass_threshold
        )
        if result is not None:
            by_id = {w.id: w.word for w in words_for(db, a)}
            for ans in result["answers"]:
                ans["word"] = by_id.get(ans["word_id"], "?")
            feedback["result"] = result
            if result["passed"] and a.status != "passed":
                a.status = "passed"
                a.passed_at = utcnow()
                db.flush()
                if a.spelling_list.carry_forward:
                    carry_forward(db, a)
    _save(a, state)
    _sync_status(db, a)
    db.commit()
    return feedback


# --- carry-forward -------------------------------------------------------------------


def _next_assignment(db: Session, a: ListAssignment) -> ListAssignment | None:
    rows = db.scalars(
        select(ListAssignment)
        .join(SpellingList)
        .where(
            ListAssignment.child_id == a.child_id,
            ListAssignment.id != a.id,
            ListAssignment.status != "passed",
            SpellingList.archived.is_(False),
        )
        .order_by(SpellingList.due_date.is_(None), SpellingList.due_date, SpellingList.id)
    ).all()
    current_due = a.spelling_list.due_date
    for r in rows:
        due = r.spelling_list.due_date
        if current_due is None or due is None or due >= current_due:
            return r
    return None


def carry_forward(db: Session, a: ListAssignment, target: ListAssignment | None = None) -> int:
    """Copy this child's still-flagged words from `a` into their next list as review words."""
    target = target or _next_assignment(db, a)
    if target is None:
        return 0  # picked up later by pull_carry_forward when a new list is assigned
    existing = {w.word.casefold() for w in words_for(db, target)}
    stats = [r for r in word_stats(db, a).values() if r.flagged and not r.carried]
    next_ord = len(target.spelling_list.words)
    added = 0
    for r in stats:
        r.carried = True
        if r.word.word.casefold() in existing:
            continue
        db.add(
            SpellingWord(
                list_id=target.list_id,
                word=r.word.word,
                sentence=r.word.sentence,
                homophone=r.word.homophone,
                ordinal=next_ord + added,
                for_child_id=a.child_id,
                carried_from_list_id=a.list_id,
            )
        )
        existing.add(r.word.word.casefold())
        added += 1
    db.flush()
    return added


def pull_carry_forward(db: Session, target: ListAssignment) -> int:
    """A new list was assigned: bring in flagged words from finished lists still waiting."""
    done = db.scalars(
        select(ListAssignment)
        .join(SpellingList)
        .where(
            ListAssignment.child_id == target.child_id,
            ListAssignment.id != target.id,
            SpellingList.carry_forward.is_(True),
            or_(ListAssignment.status == "passed", SpellingList.archived.is_(True)),
        )
    ).all()
    return sum(carry_forward(db, a, target) for a in done)


def known_count(db: Session, a: ListAssignment) -> tuple[int, int]:
    words = words_for(db, a)
    stats = word_stats(db, a)
    known = sum(1 for w in words if w.id in stats and stats[w.id].status == "known")
    return known, len(words)


def reset(db: Session, a: ListAssignment) -> None:
    for r in word_stats(db, a).values():
        db.delete(r)
    a.state = None
    a.round_no = 0
    a.status = "not_started"
    a.passed_at = None
    db.commit()


def session_view(db: Session, a: ListAssignment) -> dict[str, Any]:
    state = get_state(a)
    wid = sp.current_word(state)
    word = db.get(SpellingWord, wid) if wid is not None else None
    known, total = known_count(db, a)
    mode = state["mode"]
    return {
        "assignment_id": a.id,
        "title": a.spelling_list.title,
        "status": a.status,
        "mode": mode,
        "round_no": state["round_no"],
        "round_kind": state["round_kind"],
        "remaining": len(state["queue"]),
        "copies": state["copies"],
        "learn_copies": sp.LEARN_COPIES,
        "known": known,
        "total": total,
        "threshold": a.spelling_list.pass_threshold,
        "last_test": state.get("last_test"),
        "current": None
        if word is None
        else {
            "id": word.id,
            # Speak-only: the word text is sent only in learn mode, where it is shown.
            "speak": word.word,
            "show": word.word if mode == "learn" else None,
            "sentence": word.sentence,
            "homophone": word.homophone,
        },
    }
