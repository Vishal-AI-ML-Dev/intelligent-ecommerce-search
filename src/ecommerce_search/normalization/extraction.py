"""Conservative title/description extraction (Milestone 13).

Only the explicit patterns in `rules.TEXT_PATTERNS` are read. Each match becomes unverified
candidates with `basis=title|description`, quoting the exact raw text with Unicode code-point
offsets into the seller's raw string (Python `str` indices). Nothing else in the text is
interpreted: text that looks like an instruction is data, the category is never read from
text, a bare "NVMe" never implies an SSD medium, and a qualified amount ("up to 32GB RAM") is
uncertain rather than normalized. Repeated or differing mentions are all kept; the caller
records conflicts without choosing a winner.

Patterns have bounded quantifiers and no nesting, and the text is bounded by intake, so a
scan is linear in the text length.
"""

import re

from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.fields import make_candidate, measure_outcome
from ecommerce_search.normalization.models import AttributeCandidate, Outcome
from ecommerce_search.normalization.rules import Basis

_RAM = "title.ram_pattern"
_STORAGE = "title.storage_pattern"


def extract_text_candidates(basis: Basis, key: str, text: str) -> list[AttributeCandidate]:
    """Candidates found in one raw title or description string."""
    if basis not in nr.TEXT_BASES:
        raise ValueError(f"{basis} is not a text basis")
    out: list[AttributeCandidate] = []

    def add(field: str, match: re.Match[str], rule_id: str, outcome: Outcome, value=None):
        out.append(
            make_candidate(
                field, key, match[0], rule_id, outcome, value, basis=basis, span=match.span()
            )
        )

    for match in re.finditer(nr.TEXT_PATTERNS[_RAM], text):
        if match["qualifier"] is not None:
            add("ram_gb", match, _RAM, Outcome.UNCERTAIN)
        else:
            add("ram_gb", match, _RAM, *measure_outcome("ram_gb", match["measure"]))

    for match in re.finditer(nr.TEXT_PATTERNS[_STORAGE], text):
        medium, interface = nr.TEXT_STORAGE_TERMS[" ".join(match["term"].split()).casefold()]
        if match["qualifier"] is not None:
            add("storage_gb", match, _STORAGE, Outcome.UNCERTAIN)
            if medium is not None:
                add("storage_type", match, _STORAGE, Outcome.UNCERTAIN)
            if interface is not None:
                add("storage_interface", match, _STORAGE, Outcome.UNCERTAIN)
            continue
        add("storage_gb", match, _STORAGE, *measure_outcome("storage_gb", match["measure"]))
        if medium is not None:
            add("storage_type", match, _STORAGE, Outcome.NORMALIZED, medium.value)
        if interface is not None:
            add("storage_interface", match, _STORAGE, Outcome.NORMALIZED, interface.value)
    return out
