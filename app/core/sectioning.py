"""Split passage text into practice sections.

Units: `line` (default for poems), `verse` (default for scripture: splits on
inline verse numbers such as "1 The LORD is my shepherd ... 2 He maketh me"),
`sentence`, and `stanza` (blank-line separated). Manual merge/split happens in
the editor on the resulting list.
"""

from __future__ import annotations

import re
from typing import Literal

Unit = Literal["line", "verse", "sentence", "stanza"]
UNITS: tuple[Unit, ...] = ("line", "verse", "sentence", "stanza")

# A verse number: 1–3 digits (optionally "23:1" chapter:verse) at the start of
# the text or after whitespace, followed by whitespace. Superscript digits from
# copy-pasted Bibles are folded to ASCII first.
_VERSE_MARK = re.compile(r"(?:(?<=\s)|^)(?:\d{1,3}:)?\d{1,3}(?=\s)")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’)]))\s+")


def default_unit(passage_type: str) -> Unit:
    return "verse" if passage_type == "verse" else "line"


def _clean(s: str) -> str:
    return " ".join(s.split())


def split_text(text: str, unit: Unit) -> list[str]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if unit == "line":
        parts = text.split("\n")
    elif unit == "stanza":
        parts = re.split(r"\n\s*\n", text)
    elif unit == "sentence":
        parts = _SENTENCE_END.split(" ".join(text.split()))
    else:
        flat = " ".join(text.translate(_SUPERSCRIPTS).split())
        marks = list(_VERSE_MARK.finditer(flat))
        if not marks:
            # No verse numbers: fall back to lines so pasted text still works.
            return split_text(text, "line")
        parts = []
        lead = flat[: marks[0].start()]
        if lead.strip():
            parts.append(lead)
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(flat)
            parts.append(flat[m.end() : end])
    if unit == "stanza":
        # Keep line breaks inside a stanza for display; collapse runs of spaces.
        return [
            "\n".join(_clean(line) for line in p.split("\n") if line.strip())
            for p in parts
            if p.strip()
        ]
    return [_clean(p) for p in parts if p.strip()]


def merge(sections: list[str], index: int) -> list[str]:
    """Merge section `index` with the one after it."""
    if not 0 <= index < len(sections) - 1:
        return sections
    joined = sections[index] + "\n" + sections[index + 1]
    return sections[:index] + [joined] + sections[index + 2 :]


def split_at(sections: list[str], index: int, marked: str) -> list[str]:
    """Split section `index` where the editor inserted `|` markers in its text."""
    if not 0 <= index < len(sections):
        return sections
    pieces = [p.strip() for p in marked.split("|") if p.strip()]
    if not pieces:
        return sections
    return sections[:index] + pieces + sections[index + 1 :]
