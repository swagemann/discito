from app.core.text import expected_tokens, normalize_text, number_to_words


def test_normalize_strips_punctuation_and_case() -> None:
    assert normalize_text("The LORD is my shepherd; I shall not want.") == [
        "the",
        "lord",
        "is",
        "my",
        "shepherd",
        "i",
        "shall",
        "not",
        "want",
    ]


def test_contractions_expand() -> None:
    assert normalize_text("Don't 'tis o'er we'll") == [
        "do",
        "not",
        "it",
        "is",
        "over",
        "we",
        "will",
    ]


def test_curly_apostrophes() -> None:
    assert normalize_text("don’t") == ["do", "not"]


def test_numbers_and_hyphens() -> None:
    assert normalize_text("23") == ["twenty", "three"]
    assert normalize_text("twenty-three") == ["twenty", "three"]
    assert number_to_words(1611) == "one thousand six hundred eleven"


def test_dashes_split_words() -> None:
    assert normalize_text("woods—and") == ["woods", "and"]


def test_expected_tokens_track_origin() -> None:
    toks = expected_tokens(["I can't stop", "for Death"])
    assert [(t.norm, t.section, t.word) for t in toks] == [
        ("i", 0, 0),
        ("can", 0, 1),
        ("not", 0, 1),
        ("stop", 0, 2),
        ("for", 1, 0),
        ("death", 1, 1),
    ]


def test_scope_labels() -> None:
    from app.web import scope_label

    assert scope_label("partial:1") == "Line 1"
    assert scope_label("partial:3") == "Lines 1–3"
    assert scope_label("drill:transition:4") == "Drill: lines 3→4"
    assert scope_label("drill:line:2") == "Drill: line 2"
    assert scope_label("full") == "Full run"
