"""Parent dashboard numbers: progress, streaks, trouble spots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import chain
from app.core.config import get_settings
from app.models import (
    LineStat,
    ListAssignment,
    PassageAssignment,
    ReciteAttempt,
    SpellingAttempt,
    WordStat,
)
from app.services import spelling as spelling_svc


def local_day(ts: datetime) -> date:
    tz = ZoneInfo(get_settings().timezone)
    if ts.tzinfo is None:  # SQLite returns naive datetimes; they are stored as UTC
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(tz).date()


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def streak(days: Iterable[date]) -> int:
    """Consecutive practice days ending today (or yesterday, so it survives until bedtime)."""
    have = set(days)
    d = today()
    if d not in have:
        d -= timedelta(days=1)
    n = 0
    while d in have:
        n += 1
        d -= timedelta(days=1)
    return n


@dataclass
class Row:
    kind: str  # recite | spelling
    assignment_id: int
    title: str
    status: str
    status_label: str
    percent: int
    streak: int
    due: date | None
    flagged: int


def recite_rows(db: Session, child_id: int) -> list[Row]:
    rows: list[Row] = []
    for a in db.scalars(select(PassageAssignment).where(PassageAssignment.child_id == child_id)):
        if a.passage.archived:
            continue
        st = None if a.state is None else chain.ChainState.from_json(a.state)
        days = [
            local_day(t)
            for t in db.scalars(
                select(ReciteAttempt.created_at).where(ReciteAttempt.assignment_id == a.id)
            )
        ]
        flagged = sum(
            1
            for r in db.scalars(select(LineStat).where(LineStat.assignment_id == a.id))
            if r.flagged
        )
        rows.append(
            Row(
                kind="recite",
                assignment_id=a.id,
                title=a.passage.title,
                status=a.status,
                status_label=recite_label(a),
                percent=0 if st is None else chain.percent_complete(st, True, a.k_required),
                streak=streak(days),
                due=a.passage.due_date,
                flagged=flagged,
            )
        )
    return rows


def recite_label(a: PassageAssignment) -> str:
    total = len(a.passage.sections)
    if a.status == "partial":
        return f"Partial ({a.current_section}/{total})"
    if a.status == "full":
        return f"Full in progress ({a.clean_full_runs}/{a.k_required})"
    return {"not_started": "Not started", "passed": "Passed"}.get(a.status, a.status)


def spelling_label(db: Session, a: ListAssignment) -> str:
    if a.status == "practicing":
        known, total = spelling_svc.known_count(db, a)
        return f"Practicing ({known}/{total} known)"
    return {"not_started": "Not started", "passed": "Test passed"}.get(a.status, a.status)


def spelling_rows(db: Session, child_id: int) -> list[Row]:
    rows: list[Row] = []
    for a in db.scalars(select(ListAssignment).where(ListAssignment.child_id == child_id)):
        if a.spelling_list.archived:
            continue
        known, total = spelling_svc.known_count(db, a)
        days = [
            local_day(t)
            for t in db.scalars(
                select(SpellingAttempt.created_at).where(SpellingAttempt.assignment_id == a.id)
            )
        ]
        flagged = sum(1 for r in spelling_svc.word_stats(db, a).values() if r.flagged)
        rows.append(
            Row(
                kind="spelling",
                assignment_id=a.id,
                title=a.spelling_list.title,
                status=a.status,
                status_label=spelling_label(db, a),
                percent=100 if a.status == "passed" else int(100 * known / total) if total else 0,
                streak=streak(days),
                due=a.spelling_list.due_date,
                flagged=flagged,
            )
        )
    return rows


STATUS_ORDER = {"full": 0, "partial": 1, "practicing": 1, "not_started": 2, "passed": 3}


def sort_rows(rows: list[Row]) -> list[Row]:
    """By due date (undated last), then status; passed items sink to the bottom."""
    return sorted(
        rows,
        key=lambda r: (
            r.status == "passed",
            r.due is None,
            r.due or date.max,
            STATUS_ORDER.get(r.status, 9),
            r.title.lower(),
        ),
    )


@dataclass
class Trouble:
    kind: str  # line | word
    label: str
    context: str
    score: float
    misses: int
    attempts: int
    trend: str  # up (worse) | down (better) | flat
    flagged: bool


def _trend(prev: float, now: float) -> str:
    if now > prev + 0.01:
        return "up"
    if now < prev - 0.01:
        return "down"
    return "flat"


def trouble_spots(db: Session, child_id: int, limit: int = 8) -> list[Trouble]:
    out: list[Trouble] = []
    lines = db.scalars(
        select(LineStat)
        .join(PassageAssignment, LineStat.assignment_id == PassageAssignment.id)
        .where(PassageAssignment.child_id == child_id, LineStat.miss_count > 0)
    )
    for r in lines:
        out.append(
            Trouble(
                kind="line",
                label=r.section.text,
                context=r.section.passage.title,
                score=r.struggle_score,
                misses=r.miss_count,
                attempts=r.attempt_count,
                trend=_trend(r.prev_score, r.struggle_score),
                flagged=r.flagged,
            )
        )
    words = db.scalars(
        select(WordStat)
        .join(ListAssignment, WordStat.assignment_id == ListAssignment.id)
        .where(ListAssignment.child_id == child_id, WordStat.miss_count > 0)
    )
    for w in words:
        out.append(
            Trouble(
                kind="word",
                label=w.word.word,
                context=w.word.spelling_list.title,
                score=w.struggle_score,
                misses=w.miss_count,
                attempts=w.attempt_count,
                trend=_trend(w.prev_score, w.struggle_score),
                flagged=w.flagged,
            )
        )
    out.sort(key=lambda t: (not t.flagged, -t.score, -t.misses))
    return out[:limit]
