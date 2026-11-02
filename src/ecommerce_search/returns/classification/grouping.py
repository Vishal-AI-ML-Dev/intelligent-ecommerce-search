"""`return-grouping-1`: deterministic per-SKU grouping of stored predictions (rule signatures).

A group is the returns of one SKU (`product_id`) whose predictions share a rule signature:
* the prediction's candidate set, in taxonomy order, joined by `|` (an unresolved defect stays
  `product_quality_defect|manufacturing_defect`; nothing is merged or resolved here);
* for a single `sizing_fit` prediction, plus a direction from its supporting rules
  (`too_small`, `too_large`, `mixed` or `unspecified`), because "too small" and "too large"
  returns are different complaints.

The method is lexical through the Part 1 rules only: it compares which rules matched, not free
text or embeddings, and it never downloads or loads a model. Catalog v1 has no variants, so
grouping is by SKU only. A return with several predictions joins one group per prediction;
abstained returns join none.

A group is a grouping of similar complaints, not a root cause, a defect finding or a fault.
Every member is stored, but a group is flagged `is_pattern` only when at least
`min_pattern_size` of its members **qualify**:
* the prediction's tier is `medium` or `high`;
* the prediction has no contradicting evidence;
* the return carries no conflict review reason (`conflicting_statements`,
  `statement_inspection_conflict` or `conflicting_inspections`). This is conservative: a
  conflict anywhere on a return disqualifies all of its predictions.

Low-tier, contradicted and conflicting members never make a group a pattern. The threshold is a
display threshold, not statistical significance.
"""

from collections import defaultdict
from dataclasses import dataclass

GROUPING_VERSION = "return-grouping-1"
METHOD = "rule_signature"
DEFAULT_MIN_PATTERN_SIZE = 3
SIZING = "sizing_fit"
_SMALL = ("code:SIZE_TOO_SMALL", "text:sizing_small:")
_LARGE = ("code:SIZE_TOO_LARGE", "text:sizing_large:")
QUALIFYING_TIERS = frozenset({"medium", "high"})
CONFLICT_REASONS = frozenset(
    {"conflicting_statements", "statement_inspection_conflict", "conflicting_inspections"}
)


@dataclass(frozen=True, slots=True)
class GroupItem:
    classification_id: int
    product_id: str
    prediction_ordinal: int
    candidates: tuple[str, ...]  # taxonomy order, as stored
    supporting_rule_ids: tuple[str, ...]
    tier: str
    contradicted: bool  # the prediction has contradicting evidence
    review_reasons: tuple[str, ...]  # of the whole return

    @property
    def qualifies(self) -> bool:
        """Whether this member may count towards a pattern."""
        return (
            self.tier in QUALIFYING_TIERS
            and not self.contradicted
            and not CONFLICT_REASONS.intersection(self.review_reasons)
        )


@dataclass(frozen=True, slots=True)
class Group:
    product_id: str
    signature: str
    candidates: tuple[str, ...]
    members: tuple[GroupItem, ...]  # sorted by classification ID
    is_pattern: bool

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def qualifying_member_count(self) -> int:
        return sum(m.qualifies for m in self.members)


def _matches(rule_id: str, prefixes: tuple[str, ...]) -> bool:
    return any(rule_id == p or (p.endswith(":") and rule_id.startswith(p)) for p in prefixes)


def signature(item: GroupItem) -> str:
    base = "|".join(item.candidates)
    if item.candidates != (SIZING,):
        return base
    small = any(_matches(r, _SMALL) for r in item.supporting_rule_ids)
    large = any(_matches(r, _LARGE) for r in item.supporting_rule_ids)
    direction = {
        (True, False): "too_small",
        (False, True): "too_large",
        (True, True): "mixed",
    }.get((small, large), "unspecified")
    return f"{base}:{direction}"


def group_items(items: list[GroupItem], min_pattern_size: int) -> list[Group]:
    """Groups ordered by (product_id, signature); the result does not depend on input order."""
    if min_pattern_size < 2:
        raise ValueError("min_pattern_size must be at least 2")
    buckets: dict[tuple[str, str], list[GroupItem]] = defaultdict(list)
    for item in items:
        buckets[(item.product_id, signature(item))].append(item)
    groups = []
    for (product_id, sig), members in sorted(buckets.items()):
        ordered = tuple(sorted(members, key=lambda m: (m.classification_id, m.prediction_ordinal)))
        if len({m.classification_id for m in ordered}) != len(ordered):
            raise ValueError("a return has two predictions with one signature")
        groups.append(
            Group(
                product_id=product_id,
                signature=sig,
                candidates=ordered[0].candidates,
                members=ordered,
                is_pattern=sum(m.qualifies for m in ordered) >= min_pattern_size,
            )
        )
    return groups
