"""Chain-practice state machine for one child on one passage.

Stages:
  learn  — TTS reads the newly unlocked sections (no grading); child taps "ready".
  recite — child recites sections 1..n; clean unlocks the next chunk.
  drill  — a queue of short recitations (a line alone, then the transition from
           the previous line into it) for lines that keep failing.
  full   — the whole passage, no text, no peek; K consecutive clean runs pass.
  passed — done; can be reopened for review.

Drills: in partial practice a failed run drills the missed lines that were
*already* flagged before the attempt (a repeat offender). A first miss just
replays the failed section for a retry. In full mode every failed run drills
the missed lines before the next full attempt.

All functions are pure: they take a state and return a new one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

Stage = Literal["learn", "recite", "drill", "full", "passed"]


@dataclass(frozen=True)
class DrillStep:
    kind: Literal["line", "transition"]
    section: int  # 0-based section index


@dataclass(frozen=True)
class ChainState:
    total: int
    stage: Stage = "learn"
    n: int = 1  # sections unlocked, 1-based count
    clean_full_runs: int = 0
    drill_queue: tuple[DrillStep, ...] = field(default_factory=tuple)
    resume: Stage = "recite"  # where a finished drill returns to

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["drill_queue"] = [asdict(s) for s in self.drill_queue]
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> ChainState:
        return cls(
            total=int(d["total"]),
            stage=d["stage"],
            n=int(d["n"]),
            clean_full_runs=int(d["clean_full_runs"]),
            drill_queue=tuple(
                DrillStep(kind=s["kind"], section=int(s["section"])) for s in d["drill_queue"]
            ),
            resume=d.get("resume", "recite"),
        )


def start(total: int, chunk: int) -> ChainState:
    if total <= 0:
        return ChainState(total=0, stage="learn", n=0)
    return ChainState(total=total, stage="learn", n=min(max(chunk, 1), total))


def scope(state: ChainState) -> list[int]:
    """0-based section indexes the current step covers (what to recite or hear)."""
    if state.stage == "drill" and state.drill_queue:
        step = state.drill_queue[0]
        if step.kind == "transition" and step.section > 0:
            return [step.section - 1, step.section]
        return [step.section]
    if state.stage in ("full", "passed"):
        return list(range(state.total))
    return list(range(state.n))


def learn_sections(state: ChainState, chunk: int) -> list[int]:
    """Sections introduced in the current learn step (the newest chunk)."""
    first = max(0, state.n - max(chunk, 1))
    return list(range(first, state.n))


def scope_label(state: ChainState) -> str:
    if state.stage == "drill" and state.drill_queue:
        step = state.drill_queue[0]
        return f"drill:{step.kind}:{step.section + 1}"
    if state.stage == "full":
        return "full"
    return f"partial:{state.n}"


def finish_learn(state: ChainState) -> ChainState:
    if state.stage != "learn":
        return state
    return replace(state, stage="recite")


def _drills_for(sections: list[int]) -> tuple[DrillStep, ...]:
    steps: list[DrillStep] = []
    for s in sorted(set(sections)):
        steps.append(DrillStep("line", s))
        if s > 0:
            steps.append(DrillStep("transition", s))
    return tuple(steps)


def apply_attempt(
    state: ChainState,
    *,
    clean: bool,
    missed_sections: list[int],
    flagged_before: set[int],
    chunk: int,
    k_required: int,
) -> ChainState:
    """Advance the chain after a graded attempt on `scope(state)`.

    `missed_sections` are absolute 0-based section indexes with at least one miss;
    `flagged_before` holds the sections that were flagged before this attempt.
    """
    if state.stage == "recite":
        if clean:
            if state.n >= state.total:
                return replace(state, stage="full", n=state.total, clean_full_runs=0)
            return replace(state, stage="learn", n=min(state.n + max(chunk, 1), state.total))
        repeat = [s for s in missed_sections if s in flagged_before]
        if repeat:
            return replace(state, stage="drill", drill_queue=_drills_for(repeat), resume="recite")
        return state

    if state.stage == "drill":
        if not clean or not state.drill_queue:
            return state
        rest = state.drill_queue[1:]
        if rest:
            return replace(state, drill_queue=rest)
        return replace(state, stage=state.resume, drill_queue=())

    if state.stage == "full":
        if clean:
            runs = state.clean_full_runs + 1
            if runs >= k_required:
                return replace(state, stage="passed", clean_full_runs=runs)
            return replace(state, clean_full_runs=runs)
        drills = _drills_for(missed_sections)
        if drills:
            return replace(
                state, stage="drill", clean_full_runs=0, drill_queue=drills, resume="full"
            )
        return replace(state, clean_full_runs=0)

    return state


def status_of(state: ChainState, started: bool) -> str:
    if not started:
        return "not_started"
    if state.stage == "passed":
        return "passed"
    if state.stage == "full" or (state.stage == "drill" and state.resume == "full"):
        return "full"
    return "partial"


def percent_complete(state: ChainState, started: bool, k_required: int) -> int:
    """Partial practice is the first 70%, full runs the last 30%."""
    if not started or state.total == 0:
        return 0
    if state.stage == "passed":
        return 100
    if status_of(state, started) == "full":
        return 70 + int(30 * state.clean_full_runs / max(k_required, 1))
    return int(70 * max(state.n - 1, 0) / state.total)


def reset_to_partial(state: ChainState, chunk: int) -> ChainState:
    """Parent reset: back to learning the last section, keeping earlier progress."""
    if state.total == 0:
        return state
    n = max(1, min(state.n, state.total))
    if state.stage in ("full", "passed") or (state.stage == "drill" and state.resume == "full"):
        n = max(1, state.total - max(chunk, 1) + 1)
    return ChainState(total=state.total, stage="learn", n=n)


def rebase(state: ChainState, total: int, first_changed: int | None, chunk: int) -> ChainState:
    """Passage text was edited: keep progress up to the first changed section."""
    if total == 0:
        return ChainState(total=0, stage="learn", n=0)
    if first_changed is None:
        return replace(state, total=total, n=min(state.n, total))
    if state.stage in ("full", "passed") or state.resume == "full" or first_changed < state.n:
        n = max(1, min(first_changed + 1, total))
        return ChainState(total=total, stage="learn", n=n)
    return replace(state, total=total, n=min(state.n, total))
