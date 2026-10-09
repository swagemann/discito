"""Grade a transcript against known text by word-level alignment.

This is alignment, not open transcription: we know what the child should say,
so we find the cheapest edit script (Levenshtein over token sequences) that
turns the expected tokens into the transcript and count expected tokens that
landed on a matching transcript token. Extra words the child adds ("um", a
restart) are insertions and cost nothing against the matched-word rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.text import Token, expected_tokens, normalize_text

HOMOPHONES_FILE = Path(__file__).with_name("homophones.txt")
FUZZY_MIN_LEN = 5


@lru_cache
def homophone_groups() -> dict[str, frozenset[str]]:
    """word -> every word it may be heard as. One group per line in the seed file."""
    groups: dict[str, frozenset[str]] = {}
    for line in HOMOPHONES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        words = frozenset(w.strip().lower() for w in line.split(",") if w.strip())
        for w in words:
            groups[w] = groups.get(w, frozenset()) | words
    return groups


def _within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]


def tokens_match(expected: str, heard: str) -> bool:
    if expected == heard:
        return True
    if len(expected) >= FUZZY_MIN_LEN and _within_one_edit(expected, heard):
        return True
    return heard in homophone_groups().get(expected, frozenset())


@dataclass
class Miss:
    section: int  # index within the graded sections
    word: int  # display-word index within that section
    expected: str
    heard: str | None  # substituted transcript token, or None if skipped


@dataclass
class Grade:
    total: int
    matched: int
    misses: list[Miss] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return 1.0 if self.total == 0 else self.matched / self.total

    def clean(self, threshold: float) -> bool:
        # Small epsilon so a 100% threshold is not defeated by float rounding.
        return self.rate + 1e-9 >= threshold

    @property
    def missed_sections(self) -> set[int]:
        return {m.section for m in self.misses}


def align_detail(expected: list[Token], heard: list[str]) -> list[tuple[str | None, bool]]:
    n, m = len(expected), len(heard)
    # cost[i][j]: edits to align expected[:i] with heard[:j].
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        e = expected[i - 1].norm
        row, prev = cost[i], cost[i - 1]
        for j in range(1, m + 1):
            sub = prev[j - 1] + (0 if tokens_match(e, heard[j - 1]) else 1)
            row[j] = min(sub, prev[j] + 1, row[j - 1] + 1)

    result: list[tuple[str | None, bool]] = [(None, False)] * n
    i, j = n, m
    while i > 0 and j > 0:
        e = expected[i - 1].norm
        ok = tokens_match(e, heard[j - 1])
        if cost[i][j] == cost[i - 1][j - 1] + (0 if ok else 1):
            result[i - 1] = (heard[j - 1], ok)
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + 1:
            result[i - 1] = (None, False)
            i -= 1
        else:
            j -= 1
    return result


def grade(sections: list[str], transcript: str) -> Grade:
    expected = expected_tokens(sections)
    heard = normalize_text(transcript)
    detail = align_detail(expected, heard)
    g = Grade(total=len(expected), matched=0)
    seen_words: set[tuple[int, int]] = set()
    for tok, (got, ok) in zip(expected, detail, strict=True):
        if ok:
            g.matched += 1
            continue
        key = (tok.section, tok.word)
        if key in seen_words:
            continue
        seen_words.add(key)
        g.misses.append(Miss(section=tok.section, word=tok.word, expected=tok.norm, heard=got))
    return g


def initial_prompt(sections: list[str], limit: int = 800) -> str:
    """Whisper's initial_prompt is capped (~224 tokens); keep the tail-trimmed text."""
    text = " ".join(" ".join(s.split()) for s in sections)
    return text[:limit]
