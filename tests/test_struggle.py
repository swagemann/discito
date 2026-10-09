from app.core.struggle import StruggleState, record


def test_single_miss_flags_and_three_correct_clear() -> None:
    s = record(StruggleState(), correct=False)
    assert s.score == 0.5 and s.flagged
    for _ in range(2):
        s = record(s, correct=True)
        assert s.flagged
    s = record(s, correct=True)
    assert (
        not s.flagged and s.consecutive_correct == 3 and s.miss_count == 1 and s.attempt_count == 4
    )


def test_old_misses_fade() -> None:
    s = StruggleState(score=1.0)
    for _ in range(3):
        s = record(s, correct=True)
    assert s.score == 0.125
