"""`redaction-1`: versioned pattern redaction for return free text (contracts §5.2).

Kinds, applied in this order, each match replaced by a fixed token:

* email: the M16 `assistant/privacy.py` `EMAIL` pattern -> `[EMAIL]`;
* upi: `handle@provider` with no dot after the provider -> `[UPI]`;
* number: the M16 `LONG_NUMBER` pattern (10 or more digits, optional `+` and separators)
  -> `[NUMBER]`;
* pin: a standalone 6-digit run -> `[PIN]`.

The passes repeat until no pattern matches. Tokens contain no `@` and no digit, and every match
consumes at least one of them, so this ends; the result therefore satisfies G1 (no pattern
matches redacted output) and G2 (redaction is idempotent), and G3 holds because only the fixed
tokens are inserted.

This is a bounded pattern screen, not personal-data detection. Names, postal addresses,
numbers in words or split by other characters, spaced PIN codes and anything outside these
patterns pass unchanged; false positives are accepted. The M16 screen is not changed.
"""

import re
from dataclasses import dataclass

from ecommerce_search.assistant.privacy import EMAIL, LONG_NUMBER
from ecommerce_search.returns.contracts import REDACTION_KINDS
from ecommerce_search.returns.versions import REDACTION_VERSION

UPI = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9@_-])(?!\.[A-Za-z0-9])")
PIN = re.compile(r"(?<!\d)\d{6}(?!\d)")
PREVIEW_MAX = 80

PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", EMAIL, "[EMAIL]"),
    ("upi", UPI, "[UPI]"),
    ("number", LONG_NUMBER, "[NUMBER]"),
    ("pin", PIN, "[PIN]"),
)  # order equals `REDACTION_KINDS` (tested)


@dataclass(frozen=True, slots=True)
class Redaction:
    text: str
    counts: dict[str, int]  # replacements per kind, every kind present
    version: str = REDACTION_VERSION


def matches_any(text: str) -> bool:
    """True if any `redaction-1` pattern matches (used to reject IDs and dataset text)."""
    return any(pattern.search(text) for _, pattern, _ in PATTERNS)


def redact(text: str) -> Redaction:
    counts = dict.fromkeys(REDACTION_KINDS, 0)
    while True:
        for kind, pattern, token in PATTERNS:
            text, replaced = pattern.subn(token, text)
            counts[kind] += replaced
        if not matches_any(text):
            return Redaction(text=text, counts=counts)


def safe_preview(value: str) -> str:
    """Redacted, at most `PREVIEW_MAX` code points; redacted again after truncation, because
    cutting text can leave a new standalone 6-digit run."""
    return redact(redact(value).text[:PREVIEW_MAX]).text
