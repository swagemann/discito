import random

from app.core import spelling as sp


def test_spaced_round_duplicates_flagged_apart() -> None:
    rng = random.Random(1)
    for _ in range(50):
        order = sp.spaced_round(list(range(8)), {3, 5}, rng)
        assert sorted(order) == sorted(list(range(8)) + [3, 5])
        for w in (3, 5):
            i, j = [k for k, x in enumerate(order) if x == w]
            assert j - i >= 2


def test_practice_round_then_fixups_until_clean() -> None:
    rng = random.Random(0)
    words = [1, 2, 3, 4]
    s = sp.start_practice(sp.idle_state(), words, set(), rng)
    assert sorted(s["queue"]) == words and s["round_no"] == 1
    seen = []
    event = None
    while s["round_kind"] == "full" and s["queue"]:
        w = sp.current_word(s)
        assert w is not None
        seen.append(w)
        s, event = sp.answer_practice(s, w, w not in (2, 4), words, set(), rng)
    assert sorted(seen) == words  # without replacement
    assert event == "fixup" and sorted(s["queue"]) == [2, 4]
    # Miss 4 again in the fix-up: another fix-up of just 4.
    while s["queue"]:
        w = sp.current_word(s)
        assert w is not None
        s, event = sp.answer_practice(s, w, w != 4, words, set(), rng)
        if event:
            break
    assert event == "fixup" and s["queue"] == [4]
    s, event = sp.answer_practice(s, 4, True, words, {4}, rng)
    assert event == "round_done" and s["round_no"] == 2 and s["round_kind"] == "full"
    assert sorted(s["queue"]) == [1, 2, 3, 4, 4]


def test_practice_resumes_unfinished_round() -> None:
    rng = random.Random(0)
    s = sp.start_practice(sp.idle_state(), [1, 2, 3], set(), rng)
    s, _ = sp.answer_practice(s, s["queue"][0], True, [1, 2, 3], set(), rng)
    again = sp.start_practice(s, [1, 2, 3], set(), rng)
    assert again == s


def test_test_mode_scores_at_end() -> None:
    rng = random.Random(0)
    s = sp.start_test(sp.idle_state(), [1, 2], rng)
    first = s["queue"][0]
    s, result = sp.answer_test(s, first, "x", True, 1.0)
    assert result is None
    s, result = sp.answer_test(s, s["queue"][0], "y", False, 1.0)
    assert result is not None and result["score"] == 0.5 and not result["passed"]
    assert s["mode"] is None and s["last_test"] == result


def test_learn_needs_two_copies() -> None:
    s = sp.start_learn(sp.idle_state(), [7, 8])
    s = sp.answer_learn(s, True)
    assert sp.current_word(s) == 7
    s = sp.answer_learn(s, False)
    s = sp.answer_learn(s, True)
    assert sp.current_word(s) == 8


def test_word_status() -> None:
    assert sp.next_word_status("new", 1, True, "practice") == "learning"
    assert sp.next_word_status("learning", 2, True, "practice") == "known"
    assert sp.next_word_status("learning", 2, True, "test") == "learning"
    assert sp.next_word_status("known", 0, False, "practice") == "learning"
    assert sp.is_correct("Necessary", " necessary ")


def test_switching_modes_keeps_practice_place() -> None:
    rng = random.Random(0)
    words = [1, 2, 3, 4]
    s = sp.start_practice(sp.idle_state(), words, set(), rng)
    s, _ = sp.answer_practice(s, s["queue"][0], False, words, set(), rng)
    queue, missed = list(s["queue"]), list(s["missed"])
    s = sp.start_learn(s, words)
    s = sp.pause(s)
    assert s["mode"] is None
    s = sp.start_practice(s, words, set(), rng)
    assert s["queue"] == queue and s["missed"] == missed and s["round_no"] == 1


def test_paused_test_resumes() -> None:
    rng = random.Random(0)
    s = sp.start_test(sp.idle_state(), [1, 2, 3], rng)
    s, _ = sp.answer_test(s, s["queue"][0], "a", True, 1.0)
    left = list(s["queue"])
    s = sp.pause(s)
    s = sp.start_test(s, [1, 2, 3], rng)
    assert s["mode"] == "test" and s["queue"] == left and len(s["answers"]) == 1
