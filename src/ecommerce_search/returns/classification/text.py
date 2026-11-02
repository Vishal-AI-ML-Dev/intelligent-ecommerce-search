"""Matching normalization for customer text. The original text is never changed.

Each token is found in the original text with its code-point offsets, then normalized on its
own copy: NFKC, case-folded, apostrophes removed ("doesn't" -> "doesnt") and any run of three or
more equal characters cut to two ("tooo smalll" -> "too small"). Devanagari vowel signs and
viramas are part of a token, so Devanagari words stay whole.

**Bounded misspelling match:** only lexicon tokens on an explicit allowlist (`rules.py`), each
of at least `FUZZY_MIN_LENGTH` ASCII letters, match an input token that starts with the same
letter and is within one insertion, deletion or substitution ("damagd" -> "damaged", "colur" ->
"colour"). Every other token, including Devanagari, Hinglish spellings and negation words,
matches exactly; Hinglish spelling variants are listed explicitly instead. A fuzzy match lowers
the rule tier.
"""

import re
import unicodedata
from dataclasses import dataclass

FUZZY_MIN_LENGTH = 6
_WORD = r"[\w\u0900-\u097F]+"  # word characters and the Devanagari block
TOKEN = re.compile(rf"{_WORD}(?:['\u2019]{_WORD})*")
_REPEAT = re.compile(r"(.)\1{2,}")


@dataclass(frozen=True, slots=True)
class Token:
    norm: str
    start: int
    end: int


def normalize_token(raw: str) -> str:
    folded = unicodedata.normalize("NFKC", raw).casefold()
    return _REPEAT.sub(r"\1\1", folded.replace("'", "").replace("\u2019", ""))


def tokenize(text: str) -> list[Token]:
    return [Token(normalize_token(m.group()), m.start(), m.end()) for m in TOKEN.finditer(text)]


def within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1 :] == b[i + 1 :]
    return a[i:] == b[i + 1 :]


def token_match(lexicon_token: str, token: str, *, fuzzy: bool) -> str | None:
    """`"exact"`, `"fuzzy"` (only when `fuzzy` allows it for this lexicon token) or `None`."""
    if token == lexicon_token:
        return "exact"
    if (
        fuzzy
        and len(lexicon_token) >= FUZZY_MIN_LENGTH
        and lexicon_token.isascii()
        and lexicon_token.isalpha()
        and token[:1] == lexicon_token[:1]
        and within_one_edit(lexicon_token, token)
    ):
        return "fuzzy"
    return None
