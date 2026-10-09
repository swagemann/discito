from app.core.sectioning import merge, split_at, split_text

PSALM = "1 The LORD is my shepherd; I shall not want. 2 He maketh me to lie down in green pastures:\nhe leadeth me beside the still waters."


def test_verse_split() -> None:
    assert split_text(PSALM, "verse") == [
        "The LORD is my shepherd; I shall not want.",
        "He maketh me to lie down in green pastures: he leadeth me beside the still waters.",
    ]


def test_verse_split_superscripts_and_chapter_refs() -> None:
    assert split_text("23:1 A b. 23:2 C d.", "verse") == ["A b.", "C d."]
    assert split_text("¹ A b. ² C d.", "verse") == ["A b.", "C d."]


def test_verse_falls_back_to_lines() -> None:
    assert split_text("no numbers\nhere", "verse") == ["no numbers", "here"]


def test_line_and_stanza() -> None:
    poem = "Line one\nLine two\n\nLine three\n  Line four  "
    assert split_text(poem, "line") == ["Line one", "Line two", "Line three", "Line four"]
    assert split_text(poem, "stanza") == ["Line one\nLine two", "Line three\nLine four"]


def test_sentence() -> None:
    assert split_text('Stop. "Go!" Why?\nOk', "sentence") == ["Stop.", '"Go!"', "Why?", "Ok"]


def test_merge_and_split() -> None:
    s = ["a", "b", "c"]
    assert merge(s, 0) == ["a\nb", "c"]
    assert merge(s, 2) == s
    assert split_at(s, 1, "b1 | b2") == ["a", "b1", "b2", "c"]
