"""Period summaries over attempt rows (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.services import stats


def recite(days_ago: int, *, clean: bool, matched: int = 8, total: int = 10, helped: bool = False):
    return SimpleNamespace(
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
        effective_verdict=clean,
        matched=matched,
        total=total,
        peeked=helped,
        audio_seconds=30.0,
    )


def spell(days_ago: int, *, correct: bool) -> Any:
    return SimpleNamespace(created_at=datetime.now(UTC) - timedelta(days=days_ago), correct=correct)


def test_summaries_bucket_by_window() -> None:
    rows = stats.summarize(
        [recite(0, clean=True), recite(1, clean=False, helped=True), recite(10, clean=True)],
        [spell(0, correct=True), spell(20, correct=False)],
    )
    week, prev, ever = rows
    assert [r.label for r in rows] == ["Last 7 days", "7 days before", "All time"]

    assert week.recite_attempts == 2 and week.recite_clean == 1 and week.clean_pct == 50
    assert week.accuracy_pct == 80 and week.helped == 1 and week.helped_pct == 50
    assert week.days == 2 and week.spoken_min == 1
    assert week.spell_answers == 1 and week.spell_pct == 100

    assert prev.recite_attempts == 1 and prev.clean_pct == 100 and prev.spell_answers == 0
    assert prev.spell_pct is None

    assert ever.recite_attempts == 3 and ever.clean_pct == 67 and ever.spell_answers == 2
    assert ever.spell_pct == 50 and ever.days == 4


def test_empty_summary_has_no_rates() -> None:
    week, prev, ever = stats.summarize([])
    assert ever.empty and ever.clean_pct is None and ever.accuracy_pct is None
    assert week.spoken_min == 0 and prev.days == 0
