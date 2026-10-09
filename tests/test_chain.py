from app.core.chain import (
    ChainState,
    DrillStep,
    apply_attempt,
    finish_learn,
    rebase,
    reset_to_partial,
    scope,
    start,
    status_of,
)


def attempt(
    s: ChainState,
    clean: bool,
    missed: list[int] | None = None,
    flagged: set[int] | None = None,
    chunk: int = 1,
    k: int = 3,
) -> ChainState:
    return apply_attempt(
        s,
        clean=clean,
        missed_sections=missed or [],
        flagged_before=flagged or set(),
        chunk=chunk,
        k_required=k,
    )


def test_unlocks_one_by_one_then_full_then_pass() -> None:
    s = start(3, 1)
    assert s.stage == "learn" and scope(s) == [0]
    for n in (1, 2, 3):
        s = finish_learn(s)
        assert s.stage == "recite" and scope(s) == list(range(n))
        s = attempt(s, clean=True)
    assert s.stage == "full" and scope(s) == [0, 1, 2]
    s = attempt(s, clean=True)
    s = attempt(s, clean=True)
    assert s.stage == "full" and s.clean_full_runs == 2
    s = attempt(s, clean=True)
    assert s.stage == "passed" and status_of(s, True) == "passed"


def test_failed_full_run_resets_count_and_drills() -> None:
    s = ChainState(total=3, stage="full", n=3, clean_full_runs=2)
    s = attempt(s, clean=False, missed=[2])
    assert s.stage == "drill" and s.clean_full_runs == 0 and s.resume == "full"
    assert s.drill_queue == (DrillStep("line", 2), DrillStep("transition", 2))
    assert scope(s) == [2]
    assert status_of(s, True) == "full"
    s = attempt(s, clean=False, missed=[2])
    assert scope(s) == [2]  # retry same step
    s = attempt(s, clean=True)
    assert scope(s) == [1, 2]
    s = attempt(s, clean=True)
    assert s.stage == "full" and s.clean_full_runs == 0


def test_chunk_size() -> None:
    s = start(5, 2)
    assert s.n == 2
    s = attempt(finish_learn(s), clean=True, chunk=2)
    assert s.n == 4 and s.stage == "learn"
    s = attempt(finish_learn(s), clean=True, chunk=2)
    assert s.n == 5
    s = attempt(finish_learn(s), clean=True, chunk=2)
    assert s.stage == "full"


def test_partial_first_miss_retries_repeat_miss_drills() -> None:
    s = ChainState(total=4, stage="recite", n=3)
    s1 = attempt(s, clean=False, missed=[1], flagged=set())
    assert s1 == s
    s2 = attempt(s, clean=False, missed=[0, 1], flagged={1})
    assert s2.stage == "drill" and s2.resume == "recite"
    assert s2.drill_queue == (DrillStep("line", 1), DrillStep("transition", 1))


def test_drill_line_zero_has_no_transition() -> None:
    s = attempt(ChainState(total=2, stage="full", n=2), clean=False, missed=[0])
    assert s.drill_queue == (DrillStep("line", 0),)


def test_reset_and_rebase() -> None:
    passed = ChainState(total=4, stage="passed", n=4, clean_full_runs=3)
    r = reset_to_partial(passed, 1)
    assert r.stage == "learn" and r.n == 4 and r.clean_full_runs == 0
    # Editing section 2 (0-based 1) of a passed passage drops back to it.
    assert rebase(passed, 4, 1, 1) == ChainState(total=4, stage="learn", n=2)
    # Edits beyond the child's frontier keep progress.
    mid = ChainState(total=4, stage="recite", n=2)
    assert rebase(mid, 5, 3, 1) == ChainState(total=5, stage="recite", n=2)


def test_json_roundtrip() -> None:
    s = ChainState(total=3, stage="drill", n=3, drill_queue=(DrillStep("line", 1),), resume="full")
    assert ChainState.from_json(s.to_json()) == s
