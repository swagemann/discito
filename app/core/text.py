"""Text normalization shared by grading and display.

Both the expected passage and the Whisper transcript go through `normalize_word`
so that "Don't," and "do not" compare equal. Display text keeps its original
punctuation and casing: `display_tokens` maps every normalized token back to
the display word it came from so misses can be highlighted in place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Contractions expanded before punctuation is stripped. Archaic forms are common
# in KJV passages and poems; Whisper usually spells them out or drops the apostrophe.
CONTRACTIONS: dict[str, str] = {
    "can't": "can not",
    "cannot": "can not",
    "won't": "will not",
    "shan't": "shall not",
    "ain't": "is not",
    "'tis": "it is",
    "'twas": "it was",
    "tis": "it is",
    "twas": "it was",
    "o'er": "over",
    "e'er": "ever",
    "ne'er": "never",
    "e'en": "even",
    "i'm": "i am",
    "let's": "let us",
}
SUFFIXES: tuple[tuple[str, str], ...] = (
    ("n't", " not"),
    ("'re", " are"),
    ("'ve", " have"),
    ("'ll", " will"),
)

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'"})
_NON_WORD = re.compile(r"[^a-z0-9' ]+")


def number_to_words(n: int) -> str:
    """Spell out 0..9999 so '23' from Whisper matches 'twenty-three' in the text."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + ("" if rest == 0 else " " + number_to_words(rest))
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        return (
            number_to_words(thousands)
            + " thousand"
            + ("" if rest == 0 else " " + number_to_words(rest))
        )
    return str(n)


def normalize_word(raw: str) -> list[str]:
    """Normalize one whitespace-delimited word into zero or more comparison tokens."""
    word = raw.lower().translate(_APOSTROPHES)
    # Hyphens and dashes separate words ("twenty-three", "word—word").
    word = re.sub(r"[-–—/]+", " ", word)
    out: list[str] = []
    for part in word.split():
        part = part.strip('.,;:!?"()[]{}*_')
        if not part:
            continue
        if part in CONTRACTIONS:
            out.extend(CONTRACTIONS[part].split())
            continue
        for suffix, expansion in SUFFIXES:
            if part.endswith(suffix) and len(part) > len(suffix):
                stem = part[: -len(suffix)]
                part = stem + expansion
                break
        part = _NON_WORD.sub(" ", part).replace("'", "")
        for token in part.split():
            if token.isdigit():
                out.extend(number_to_words(int(token)).split())
            else:
                out.append(token)
    return out


def normalize_text(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.split():
        tokens.extend(normalize_word(raw))
    return tokens


@dataclass(frozen=True)
class Token:
    """A normalized token with its origin: which section and which display word."""

    norm: str
    section: int  # 0-based section index within the graded scope
    word: int  # 0-based display-word index within that section


def display_words(text: str) -> list[str]:
    return text.split()


def expected_tokens(sections: list[str]) -> list[Token]:
    tokens: list[Token] = []
    for s_idx, text in enumerate(sections):
        for w_idx, raw in enumerate(display_words(text)):
            for norm in normalize_word(raw):
                tokens.append(Token(norm=norm, section=s_idx, word=w_idx))
    return tokens
