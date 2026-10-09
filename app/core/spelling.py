"""Spelling session scheduler for one child on one list.

Practice draws words randomly without replacement until the round is done,
then runs fix-up rounds of the missed words (also without replacement) until a
fix-up round comes back clean. The next full round includes flagged words a
second time, spaced apart. Test is one strict pass with feedback at the end.

The state is a plain dict so it can live in a JSON column; functions return a
new dict and never mutate their input.
"""

from __future__ import annotations

import random
from copy import deepcopy
from typing import Any, Literal

Mode = Literal["learn", "practice", "test"]
LEARN_COPIES = 2
KNOWN_AFTER = 2  # consecutive correct practice answers


def idle_state(round_no: int = 0) -> dict[str, Any]:
    return {
        "mode": None,
        "queue": [],
        "round_kind": "full",
        "round_no": round_no,
        "missed": [],
        "answers": [],
        "copies": 0,
        "last_test": None,
        "paused_practice": None,
    }


def _stash_practice(s: dict[str, Any]) -> None:
    """Leaving a practice round (for learn, test, or the menu) keeps its place."""
    if s["mode"] == "practice" and s["queue"]:
        s["paused_practice"] = {
            "queue": s["queue"],
            "round_kind": s["round_kind"],
            "missed": s["missed"],
        }


def pause(state: dict[str, Any]) -> dict[str, Any]:
    s = deepcopy(state)
    _stash_practice(s)
    if s["mode"] != "test":  # a test in progress is resumed as-is
        s.update(mode=None, queue=[], copies=0)
    else:
        s["mode"] = None
    return s


def spaced_round(word_ids: list[int], doubled: set[int], rng: random.Random) -> list[int]:
    order = list(word_ids)
    rng.shuffle(order)
    gap = max(2, len(order) // 2)
    extras = [w for w in order if w in doubled]
    rng.shuffle(extras)
    for w in extras:
        pos = order.index(w)
        if pos + gap <= len(order):
            order.insert(pos + gap, w)
        elif pos - gap >= 0:
            order.insert(pos - gap, w)
        else:
            order.append(w)
    return order


def start_learn(state: dict[str, Any], word_ids: list[int]) -> dict[str, Any]:
    s = deepcopy(state)
    _stash_practice(s)
    s.update(mode="learn", queue=list(word_ids), copies=0)
    return s


def start_practice(
    state: dict[str, Any], word_ids: list[int], flagged: set[int], rng: random.Random
) -> dict[str, Any]:
    """Resume an unfinished practice round, else begin the next full round."""
    s = deepcopy(state)
    if s["mode"] == "practice" and s["queue"]:
        return s
    paused = s.get("paused_practice")
    if paused and paused["queue"]:
        s.update(mode="practice", paused_practice=None, **paused)
        return s
    s.update(
        mode="practice",
        queue=spaced_round(word_ids, flagged, rng),
        round_kind="full",
        round_no=s["round_no"] + 1,
        missed=[],
    )
    return s


def start_test(state: dict[str, Any], word_ids: list[int], rng: random.Random) -> dict[str, Any]:
    s = deepcopy(state)
    if s["mode"] == "test" and s["queue"]:
        return s
    if s["mode"] is None and s["queue"] and s["answers"]:
        s["mode"] = "test"  # resume a paused test
        return s
    _stash_practice(s)
    order = list(word_ids)
    rng.shuffle(order)
    s.update(mode="test", queue=order, answers=[])
    return s


def current_word(state: dict[str, Any]) -> int | None:
    queue: list[int] = state["queue"]
    return queue[0] if state["mode"] and queue else None


def answer_learn(state: dict[str, Any], correct: bool) -> dict[str, Any]:
    s = deepcopy(state)
    if not correct or not s["queue"]:
        return s
    s["copies"] += 1
    if s["copies"] >= LEARN_COPIES:
        s["queue"].pop(0)
        s["copies"] = 0
        if not s["queue"]:
            s["mode"] = None
    return s


def answer_practice(
    state: dict[str, Any],
    word_id: int,
    correct: bool,
    word_ids: list[int],
    flagged: set[int],
    rng: random.Random,
) -> tuple[dict[str, Any], str | None]:
    """Record an answer; returns (state, event) where event is 'fixup' or 'round_done'."""
    s = deepcopy(state)
    if not s["queue"] or s["queue"][0] != word_id:
        return s, None
    s["queue"].pop(0)
    if not correct and word_id not in s["missed"]:
        s["missed"].append(word_id)
    if s["queue"]:
        return s, None
    if s["missed"]:
        fix = list(s["missed"])
        rng.shuffle(fix)
        s.update(queue=fix, round_kind="fixup", missed=[])
        return s, "fixup"
    # Round fully clean (including fix-ups): queue the next full round.
    s.update(
        queue=spaced_round(word_ids, flagged, rng),
        round_kind="full",
        round_no=s["round_no"] + 1,
        missed=[],
    )
    return s, "round_done"


def answer_test(
    state: dict[str, Any], word_id: int, typed: str, correct: bool, threshold: float
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Record a test answer; returns (state, result) with result set when the test ends."""
    s = deepcopy(state)
    if not s["queue"] or s["queue"][0] != word_id:
        return s, None
    s["queue"].pop(0)
    s["answers"].append({"word_id": word_id, "typed": typed, "correct": correct})
    if s["queue"]:
        return s, None
    total = len(s["answers"])
    right = sum(1 for a in s["answers"] if a["correct"])
    score = right / total if total else 1.0
    result = {
        "score": score,
        "right": right,
        "total": total,
        "passed": score + 1e-9 >= threshold,
        "answers": s["answers"],
    }
    s.update(mode=None, answers=[], last_test=result)
    return s, result


def is_correct(expected: str, typed: str) -> bool:
    """Exact spelling; case and surrounding whitespace are not graded."""
    return expected.strip().casefold() == typed.strip().casefold()


def next_word_status(status: str, consecutive_correct: int, correct: bool, mode: Mode) -> str:
    """New → Learning → Known (2 consecutive correct in practice); a miss drops Known."""
    if not correct:
        return "learning"
    if mode == "practice" and consecutive_correct >= KNOWN_AFTER:
        return "known"
    return "learning" if status == "new" else status
