"""Parent dashboard numbers: progress, streaks, trouble spots, period summaries, export."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import chain
from app.core.config import get_settings
from app.core.text import normalize_word
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


# --- period summaries ------------------------------------------------------------------


@dataclass
class Summary:
    """Attempt counts for one time window. Rates are None until there is data."""

    label: str
    days: int = 0  # distinct local days with any attempt
    recite_attempts: int = 0
    recite_clean: int = 0  # effective verdict (parent overrides applied)
    words_matched: int = 0
    words_total: int = 0
    helped: int = 0  # attempts after Listen / with the text kept on screen
    spoken_s: float = 0.0
    spell_answers: int = 0
    spell_correct: int = 0
    _days: set[date] = field(default_factory=set, repr=False)

    @property
    def clean_pct(self) -> int | None:
        return _pct(self.recite_clean, self.recite_attempts)

    @property
    def accuracy_pct(self) -> int | None:
        return _pct(self.words_matched, self.words_total)

    @property
    def helped_pct(self) -> int | None:
        return _pct(self.helped, self.recite_attempts)

    @property
    def spell_pct(self) -> int | None:
        return _pct(self.spell_correct, self.spell_answers)

    @property
    def spoken_min(self) -> int:
        return round(self.spoken_s / 60)

    @property
    def empty(self) -> bool:
        return not (self.recite_attempts or self.spell_answers)

    def add_recite(self, a: ReciteAttempt) -> None:
        self._days.add(local_day(a.created_at))
        self.days = len(self._days)
        self.recite_attempts += 1
        self.recite_clean += int(a.effective_verdict)
        self.words_matched += a.matched
        self.words_total += a.total
        self.helped += int(a.peeked)
        self.spoken_s += a.audio_seconds or 0.0

    def add_spelling(self, a: SpellingAttempt) -> None:
        self._days.add(local_day(a.created_at))
        self.days = len(self._days)
        self.spell_answers += 1
        self.spell_correct += int(a.correct)


def _pct(num: int, den: int) -> int | None:
    return None if den == 0 else round(100 * num / den)


def _windows() -> list[tuple[str, date | None, date | None]]:
    """(label, first day inclusive, last day inclusive); None means unbounded."""
    t = today()
    return [
        ("Last 7 days", t - timedelta(days=6), None),
        ("7 days before", t - timedelta(days=13), t - timedelta(days=7)),
        ("All time", None, None),
    ]


def summarize(
    recite: Iterable[ReciteAttempt], spelling: Iterable[SpellingAttempt] = ()
) -> list[Summary]:
    """This week, the week before, and all time, so a parent can see the direction."""
    windows = _windows()
    out = [Summary(label) for label, _, _ in windows]

    def hit(day: date, lo: date | None, hi: date | None) -> bool:
        return (lo is None or day >= lo) and (hi is None or day <= hi)

    for a in recite:
        day = local_day(a.created_at)
        for s, (_, lo, hi) in zip(out, windows, strict=True):
            if hit(day, lo, hi):
                s.add_recite(a)
    for sp in spelling:
        day = local_day(sp.created_at)
        for s, (_, lo, hi) in zip(out, windows, strict=True):
            if hit(day, lo, hi):
                s.add_spelling(sp)
    return out


def child_recite_attempts(db: Session, child_id: int) -> list[ReciteAttempt]:
    return list(
        db.scalars(
            select(ReciteAttempt)
            .join(PassageAssignment, ReciteAttempt.assignment_id == PassageAssignment.id)
            .where(PassageAssignment.child_id == child_id)
            .order_by(ReciteAttempt.id)
        )
    )


def child_spelling_attempts(db: Session, child_id: int) -> list[SpellingAttempt]:
    return list(
        db.scalars(
            select(SpellingAttempt)
            .join(ListAssignment, SpellingAttempt.assignment_id == ListAssignment.id)
            .where(ListAssignment.child_id == child_id)
            .order_by(SpellingAttempt.id)
        )
    )


def child_summary(db: Session, child_id: int) -> list[Summary]:
    return summarize(child_recite_attempts(db, child_id), child_spelling_attempts(db, child_id))


def assignment_summary(db: Session, assignment_id: int) -> list[Summary]:
    rows = db.scalars(
        select(ReciteAttempt)
        .where(ReciteAttempt.assignment_id == assignment_id)
        .order_by(ReciteAttempt.id)
    )
    return summarize(rows)


# --- hard-to-say words ---------------------------------------------------------------------


@dataclass
class HardWord:
    word: str  # as printed in the passage (first spelling seen)
    misses: int
    lines: list[str]  # "<passage title> line <n>" for each line it is missed in


def hard_words(
    db: Session, *, child_id: int | None = None, assignment_id: int | None = None, limit: int = 8
) -> list[HardWord]:
    """Words the grader keeps missing, across lines and passages. For a child with
    speech trouble these are usually the words that are hard to *say*, not to recall."""
    q = select(LineStat).join(PassageAssignment, LineStat.assignment_id == PassageAssignment.id)
    if assignment_id is not None:
        q = q.where(LineStat.assignment_id == assignment_id)
    if child_id is not None:
        q = q.where(PassageAssignment.child_id == child_id)
    misses: dict[str, int] = defaultdict(int)
    shown: dict[str, str] = {}
    where: dict[str, list[str]] = defaultdict(list)
    for r in db.scalars(q):
        if not r.word_misses:
            continue
        words = r.section.text.split()
        title = r.section.passage.title
        for idx, n in r.word_misses.items():
            i = int(idx)
            if i >= len(words):
                continue
            key = " ".join(normalize_word(words[i])) or words[i].lower()
            misses[key] += n
            shown.setdefault(key, words[i].strip('.,;:!?"()[]{}*_'))
            where[key].append(f"{title} line {r.section.ordinal + 1}")
    out = [HardWord(shown[k], n, where[k]) for k, n in misses.items() if n >= 2]
    out.sort(key=lambda h: (-h.misses, h.word))
    return out[:limit]


# --- export ----------------------------------------------------------------------------------


def attempts_csv(recite: Sequence[ReciteAttempt], spelling: Sequence[SpellingAttempt]) -> str:
    """One row per graded attempt or spelling answer, for a teacher or therapist."""
    from app.web import local_time, scope_label

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "when",
            "kind",
            "item",
            "step",
            "result",
            "right",
            "of",
            "accuracy_pct",
            "help",
            "missed",
            "heard_or_typed",
        ]
    )
    rows: list[tuple[datetime, list[object]]] = []
    for a in recite:
        secs = {s.id: s for s in a.assignment.passage.sections}
        missed = " ".join(
            secs[m["section_id"]].text.split()[m["word"]]
            for m in a.missed_words
            if m["section_id"] in secs and m["word"] < len(secs[m["section_id"]].text.split())
        )
        rows.append(
            (
                a.created_at,
                [
                    local_time(a.created_at),
                    "recite",
                    a.assignment.passage.title,
                    scope_label(a.scope),
                    "clean" if a.effective_verdict else "missed",
                    a.matched,
                    a.total,
                    _pct(a.matched, a.total),
                    "yes" if a.peeked else "",
                    missed,
                    a.transcript,
                ],
            )
        )
    for sp in spelling:
        rows.append(
            (
                sp.created_at,
                [
                    local_time(sp.created_at),
                    "spelling",
                    sp.word.spelling_list.title,
                    sp.mode,
                    "correct" if sp.correct else "wrong",
                    int(sp.correct),
                    1,
                    100 if sp.correct else 0,
                    "",
                    "" if sp.correct else sp.word.word,
                    sp.typed,
                ],
            )
        )
    rows.sort(key=lambda r: _aware(r[0]))
    for _, row in rows:
        w.writerow(row)
    return buf.getvalue()


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=ZoneInfo("UTC"))
