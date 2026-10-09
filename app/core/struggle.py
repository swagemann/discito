"""Struggle score: an exponentially weighted miss rate per item.

score' = α·miss + (1−α)·score, so the last few attempts dominate and old misses
fade. An item is flagged once score ≥ FLAG_AT and stays flagged until it has
CLEAR_AFTER consecutive correct attempts (hysteresis, so one lucky run does
not unflag a line that was missed twice in a row).
"""

from __future__ import annotations

from dataclasses import dataclass

ALPHA = 0.5
FLAG_AT = 0.34
CLEAR_AFTER = 3


@dataclass
class StruggleState:
    score: float = 0.0
    consecutive_correct: int = 0
    flagged: bool = False
    miss_count: int = 0
    attempt_count: int = 0


def record(state: StruggleState, correct: bool) -> StruggleState:
    score = ALPHA * (0.0 if correct else 1.0) + (1 - ALPHA) * state.score
    consecutive = state.consecutive_correct + 1 if correct else 0
    flagged = state.flagged or score >= FLAG_AT
    if flagged and consecutive >= CLEAR_AFTER:
        flagged = False
    return StruggleState(
        score=round(score, 6),
        consecutive_correct=consecutive,
        flagged=flagged,
        miss_count=state.miss_count + (0 if correct else 1),
        attempt_count=state.attempt_count + 1,
    )
