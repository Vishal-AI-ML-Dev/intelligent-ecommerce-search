"""Shared lexical matching `lt-1` for the M14 listing set (offline).

One definition is used by the `corrupt-2` generator, the pair checker and both label-word
disclosures, so they never disagree.

* **Tokens:** the case-folded runs of letters and digits (`[^\\W_]+`). Every other character,
  including spaces, hyphens, commas and slashes, is a boundary.
* **`mentions(text, value)`:** the value's token sequence occurs contiguously in the text's
  token sequence. "Red" is not mentioned by "Redmi"; "Navy Blue" is mentioned by "navy-blue
  shoes"; a value with no token is never mentioned.
* **`attribute_evidence(title, description, brand, value)`:** used for `attribute_swap`. The
  value must be mentioned in the title or description by an occurrence that does not overlap any
  occurrence of the seller's brand phrase. So "Red" inside the brand "Red Tape" is not evidence
  of the colour red. When the brand is unknown, a brand-only occurrence cannot be ruled out, so
  the case counts as ambiguous and is excluded.

This is a lexical rule. It does not prove that the text semantically supports a claim.
"""

import re

MATCHING_VERSION = "lt-1"
_TOKEN = re.compile(r"[^\W_]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _spans(haystack: list[str], needle: list[str]) -> list[tuple[int, int]]:
    n = len(needle)
    if n == 0:
        return []
    return [(i, i + n) for i in range(len(haystack) - n + 1) if haystack[i : i + n] == needle]


def mentions(text: str, value: str) -> bool:
    return bool(_spans(tokens(text), tokens(value)))


def attribute_evidence(title: str, description: str, brand: str | None, value: str) -> bool:
    if not brand or not tokens(brand):
        return False  # ambiguous: a brand-only occurrence cannot be ruled out
    for text in (title, description):
        words = tokens(text)
        brand_spans = _spans(words, tokens(brand))
        for start, end in _spans(words, tokens(value)):
            if not any(start < b_end and b_start < end for b_start, b_end in brand_spans):
                return True
    return False
