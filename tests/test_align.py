from app.core.align import grade, tokens_match


def test_perfect() -> None:
    g = grade(
        ["The LORD is my shepherd;", "I shall not want."],
        "the lord is my shepherd i shall not want",
    )
    assert g.rate == 1.0 and g.clean(1.0) and not g.misses


def test_skipped_word_attributed_to_section() -> None:
    g = grade(
        ["The LORD is my shepherd;", "I shall not want."], "the lord is my shepherd i shall want"
    )
    assert g.matched == g.total - 1
    assert [(m.section, m.word, m.expected, m.heard) for m in g.misses] == [(1, 2, "not", None)]
    assert not g.clean(1.0)
    assert g.missed_sections == {1}


def test_substitution_reports_heard() -> None:
    g = grade(["He maketh me lie down"], "he makes me lie down")
    # 'maketh' (6 letters) vs 'makes' is two edits: a miss.
    assert [(m.expected, m.heard) for m in g.misses] == [("maketh", "makes")]


def test_fuzzy_long_words_only() -> None:
    assert tokens_match("shepherd", "shepherds")
    assert not tokens_match("shepherd", "shepard")  # two edits
    assert not tokens_match("thee", "the")  # 4 letters: exact only
    assert tokens_match("pastures", "pasture")


def test_homophones() -> None:
    assert tokens_match("two", "to")
    assert tokens_match("their", "there")


def test_extra_words_do_not_count() -> None:
    g = grade(["whose woods these are"], "um whose woods uh these are I think")
    assert g.clean(1.0)


def test_threshold() -> None:
    g = grade(["one two three four"], "one two three")
    assert g.rate == 0.75
    assert g.clean(0.75) and not g.clean(0.8)


def test_contraction_vs_expanded() -> None:
    assert grade(["I don't know"], "I do not know").clean(1.0)


def test_empty_transcript() -> None:
    g = grade(["a b"], "")
    assert g.matched == 0 and len(g.misses) == 2
