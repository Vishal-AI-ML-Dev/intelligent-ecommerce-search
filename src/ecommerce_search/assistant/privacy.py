"""Basic personal-data screening before any generation request (Milestone 16).

The Gemini API Additional Terms say not to submit sensitive, confidential or personal information
to unpaid services. Messages that look like they contain an email address, a phone number or a
long identity or card-like number are not sent to the provider; the assistant falls back to plain
search results and asks the user to remove it.

This is a basic pattern screen. It does not catch all personal information (names, addresses or
free-text health details pass), and it can also flag harmless text.
"""

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 10 or more digits, optionally separated by spaces, dots or hyphens (phones, Aadhaar, cards).
LONG_NUMBER = re.compile(r"(?<![\d])\+?\d(?:[\s.-]?\d){9,}(?![\d])")


def looks_personal(message: str) -> bool:
    return bool(EMAIL.search(message) or LONG_NUMBER.search(message))
