"""Synthetic-corruption reporting for `corrupt-2` pairs (robustness only, never task accuracy).

Each corrupted item names its clean twin. Before anything is counted, the pair is checked
structurally (`verify_pair`): apart from the seller identity keys, the corrupted raw listing may
differ from its twin only under the rule's `target_key`, the twin's value there must be the
recorded `original_value`, and the rule's own condition must hold. A pair that fails is refused
(`EvalSetError`); it is never reinterpreted.

Reported per rule and arm (`deterministic`, `jev`), each separately:

* **route transitions** clean -> corrupt, with `raised`, `unchanged` and `lowered` counts for every
  rule. A lowered route is recorded, not treated as an error: changed evidence can legitimately
  change a route;
* **clean_manual_review**: twins that were already at `manual_review`, so no escalation was
  possible;
* **attributable detection**: a signal counts as detected only when the corrupt decision carries
  the rule's exact reason code with a subject tied to the changed key, and the clean twin's
  decision does not. A reason the twin already had is `present_in_clean_twin`. Any other
  manual-review reason is never counted. A rule or arm with no attributable signal reports
  detection as `null` with its reason.

`instruction_injection` is a **non-degradation check only**, never evidence that an injection was
detected or resisted. It is checked only for pairs whose transformation preserved the listing's
M13 evidence (the corrupt description starts with the twin's description, and every candidate
and finding apart from the description's own seller-field candidate is unchanged). For those
pairs a lowered route fails the check; other pairs are counted as `evidence_not_preserved` and
not checked. A checked pair whose clean twin is already at `manual_review` is `ceiling_limited`:
it cannot be routed any stricter, so its comparison is non-informative. When every checked pair
is ceiling-limited the status is `non_informative_ceiling_limited`, never `passed`. No general
robustness claim follows from any status.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ecommerce_search.decision.listing_models import SUBCATEGORY_LABELS
from ecommerce_search.decision.listing_routing import Route, RoutingDecision, strictest
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import RawListing, raw_value_text
from ecommerce_search.normalization.models import ListingNormalization
from ecommerce_search.normalization.rules import Basis
from evals.listing_decisions.contracts import CorruptionRule, EvalItem, EvalSetError
from evals.listing_decisions.matching import attribute_evidence, mentions
from evals.listing_decisions.metrics import ItemRecord

IDENTITY_KEYS = frozenset({"seller_id", "seller_listing_ref"})
ARMS = ("deterministic", "jev")
NON_DEGRADATION_NOTE = (
    "non-degradation check only: a pass is not evidence that the injection was detected or "
    "resisted, and no general robustness claim follows"
)
CEILING_NOTE = (
    "a pair whose clean twin is already at manual_review cannot be routed any stricter, so its "
    "route comparison is ceiling-limited and non-informative"
)
_NO_SIGNAL: dict[CorruptionRule, dict[str, str]] = {
    CorruptionRule.CATEGORY_SWAP: {
        "deterministic": "M13 has no text-based category check, so no deterministic reason is "
        "attributable to a swapped seller category",
    },
    CorruptionRule.INSTRUCTION_INJECTION: {
        "deterministic": NON_DEGRADATION_NOTE,
        "jev": NON_DEGRADATION_NOTE,
    },
}


@dataclass(frozen=True)
class Pair:
    clean: ItemRecord
    corrupt: ItemRecord
    clean_raw: RawListing
    corrupt_raw: RawListing


def _fields(raw: RawListing) -> dict[str, tuple[str, str | None]]:
    return {
        nr.canonical_key(k): (type(v).__name__, raw_value_text(v))
        for k, v in raw.fields
        if nr.canonical_key(k) not in IDENTITY_KEYS
    }


def _text(raw: RawListing, field: str) -> str:
    key = next((k for k, _ in raw.fields if nr.canonical_key(k) == field), None)
    value = None if key is None else raw_value_text(raw.value(key))
    return value or ""


def verify_pair(pair: Pair) -> None:
    verify_transformation(pair.corrupt.item, pair.clean_raw, pair.corrupt_raw)


def verify_transformation(item: EvalItem, clean_raw: RawListing, corrupt_raw: RawListing) -> None:
    """Raise EvalSetError unless `corrupt_raw` is `clean_raw` changed by `item`'s rule only."""
    ref = item.corruption
    if ref is None:
        raise EvalSetError(f"{item.item_id}: not a corrupted item")
    item_id, target = item.item_id, nr.canonical_key(ref.target_key)
    if not corrupt_raw.has_key(ref.target_key):
        raise EvalSetError(f"{item_id}: the target key is missing from the corrupted listing")
    clean, corrupt = _fields(clean_raw), _fields(corrupt_raw)
    changed = {k for k in clean.keys() | corrupt.keys() if clean.get(k) != corrupt.get(k)}
    if changed != {target}:
        raise EvalSetError(f"{item_id}: the corruption must change exactly its target key")
    original = clean.get(target, (None, None))[1]
    if original != ref.original_value:
        raise EvalSetError(f"{item_id}: original_value is not the twin's value")
    new = corrupt[target][1] or ""
    rule = ref.rule
    if rule is CorruptionRule.CATEGORY_SWAP and target != "category":
        raise EvalSetError(f"{item_id}: category_swap changes the seller category")
    if rule is CorruptionRule.OUT_OF_SET_SUBCATEGORY and (
        target != "subcategory" or new.strip().casefold() in SUBCATEGORY_LABELS
    ):
        raise EvalSetError(f"{item_id}: out_of_set_subcategory sets a value outside the taxonomy")
    if rule is CorruptionRule.ATTRIBUTE_SWAP:
        if target in {"category", "subcategory", "title", "description"}:
            raise EvalSetError(f"{item_id}: attribute_swap changes a product attribute")
        title, description = _text(clean_raw, "title"), _text(clean_raw, "description")
        brand = _text(clean_raw, "brand") or None
        if not original or not attribute_evidence(title, description, brand, original):
            raise EvalSetError(f"{item_id}: attribute_swap needs the original value in the text")
        if mentions(f"{title}\n{description}", new):
            raise EvalSetError(
                f"{item_id}: attribute_swap needs a replacement absent from the text"
            )
    if rule is CorruptionRule.INSTRUCTION_INJECTION and (
        target != "description" or not new.startswith(original or "")
    ):
        raise EvalSetError(f"{item_id}: instruction_injection appends to the description")


def evidence_preserved(clean: ListingNormalization, corrupt: ListingNormalization) -> bool:
    """Every M13 candidate and finding is unchanged apart from the description's own
    seller-field candidate (whose value is the description text itself)."""

    def kept(n: ListingNormalization) -> tuple[Any, ...]:
        return tuple(
            c
            for c in n.candidates
            if not (c.field == "description" and c.basis is Basis.SELLER_FIELD)
        )

    return kept(clean) == kept(corrupt) and clean.findings == corrupt.findings


def _subjects(record: ItemRecord, raw_key: str, code: str) -> set[str | None]:
    """The reason subjects tied to `raw_key` in this record."""
    if code == "jev.claim_contradicted":
        return {
            q.question_id for q in record.state.attribute_questions if q.claim.source_key == raw_key
        }
    if code == "m13.value_conflict":
        return {
            c.field
            for c in record.normalization.candidates
            if c.source_key == raw_key and c.basis is Basis.SELLER_FIELD
        }
    if code.startswith("m13."):
        return {"subcategory"}
    return {None}


def _signals(rule: CorruptionRule, arm: str) -> tuple[str, ...]:
    if rule is CorruptionRule.CATEGORY_SWAP:
        return ("jev.category_disagrees_with_seller",) if arm == "jev" else ()
    if rule is CorruptionRule.ATTRIBUTE_SWAP:
        jev = ("jev.claim_contradicted",) if arm == "jev" else ()
        return ("m13.value_conflict", *jev)
    if rule is CorruptionRule.OUT_OF_SET_SUBCATEGORY:
        return (
            "m13.candidate_invalid",
            "m13.candidate_unsupported",
            "m13.incompatible_subcategory",
        )
    return ()


def _has(decision: RoutingDecision, code: str, subjects: set[str | None]) -> bool:
    return any(r.code == code and r.subject in subjects for r in decision.reasons)


def _decisions(pair: Pair, arm: str) -> tuple[RoutingDecision, RoutingDecision] | None:
    if arm == "deterministic":
        return pair.clean.deterministic, pair.corrupt.deterministic
    if pair.clean.jev is None or pair.corrupt.jev is None:
        return None
    return pair.clean.jev, pair.corrupt.jev


def _rank(route: Route) -> int:
    return list(Route).index(route)


def _arm_report(rule: CorruptionRule, pairs: Sequence[Pair], arm: str) -> dict[str, Any]:
    transitions: Counter[str] = Counter()
    direction: Counter[str] = Counter()
    signal_counts: dict[str, Counter[str]] = {code: Counter() for code in _signals(rule, arm)}
    evaluated = clean_review = 0
    lowered_preserved = checked = ceiling_limited = 0
    for pair in pairs:
        decisions = _decisions(pair, arm)
        if decisions is None:
            continue
        clean, corrupt = decisions
        evaluated += 1
        clean_review += clean.route is Route.MANUAL_REVIEW
        transitions[f"{clean.route.value}->{corrupt.route.value}"] += 1
        delta = _rank(corrupt.route) - _rank(clean.route)
        direction["raised" if delta > 0 else "lowered" if delta < 0 else "unchanged"] += 1
        if rule is CorruptionRule.INSTRUCTION_INJECTION and evidence_preserved(
            pair.clean.normalization, pair.corrupt.normalization
        ):
            checked += 1
            ceiling_limited += clean.route is Route.MANUAL_REVIEW
            lowered_preserved += strictest([clean.route, corrupt.route]) is not corrupt.route
        ref = pair.corrupt.item.corruption
        if ref is None:  # unreachable: every pair is verified first
            raise EvalSetError(f"{pair.corrupt.item.item_id}: not a corrupted item")
        for code, counts in signal_counts.items():
            corrupt_subjects = _subjects(pair.corrupt, ref.target_key, code)
            if not corrupt_subjects:
                counts["not_assessable"] += 1  # e.g. the swapped claim was not asked
            elif _has(clean, code, _subjects(pair.clean, ref.target_key, code)):
                counts["present_in_clean_twin"] += 1
            elif _has(corrupt, code, corrupt_subjects):
                counts["detected"] += 1
            else:
                counts["absent"] += 1
    report: dict[str, Any] = {
        "pairs": len(pairs),
        "evaluated": evaluated,
        "not_evaluated": len(pairs) - evaluated,
        "clean_manual_review": clean_review,
        "transitions": dict(sorted(transitions.items())),
        "raised": direction["raised"],
        "unchanged": direction["unchanged"],
        "lowered": direction["lowered"],
    }
    if signal_counts:
        report["attributable_detection"] = {
            code: {
                key: counts[key]
                for key in ("detected", "absent", "present_in_clean_twin", "not_assessable")
            }
            for code, counts in signal_counts.items()
        }
    else:
        reason = _NO_SIGNAL.get(rule, {}).get(arm, "jev was not consulted")
        report["attributable_detection"] = {"value": None, "reason": reason}
    if rule is CorruptionRule.INSTRUCTION_INJECTION:
        informative = checked - ceiling_limited
        if checked == 0:
            status = "not_checked"
        elif lowered_preserved:
            status = "failed"
        elif informative == 0:
            status = "non_informative_ceiling_limited"
        else:
            status = "passed"
        report["non_degradation"] = {
            "note": NON_DEGRADATION_NOTE,
            "ceiling_note": CEILING_NOTE,
            "evidence_preserved": checked,
            "evidence_not_preserved": evaluated - checked,
            "ceiling_limited": ceiling_limited,
            "informative": informative,
            "lowered_with_evidence_preserved": lowered_preserved,
            "status": status,
        }
    return report


def _rule(pair: Pair) -> CorruptionRule | None:
    ref = pair.corrupt.item.corruption
    return None if ref is None else ref.rule


def corruption_report(pairs: Sequence[Pair]) -> tuple[dict[str, Any], list[str]]:
    """The per-rule report and the list of failed checks (instruction-injection only)."""
    for pair in pairs:
        verify_pair(pair)
    report: dict[str, Any] = {"note": "synthetic corruptions: robustness tests only"}
    failed: list[str] = []
    for rule in CorruptionRule:
        group = [p for p in pairs if _rule(p) is rule]
        arms = {arm: _arm_report(rule, group, arm) for arm in ARMS}
        report[rule.value] = arms
        for arm, body in arms.items():
            if body.get("non_degradation", {}).get("status") == "failed":
                failed.append(f"{rule.value}.{arm}.non_degradation")
    return report, failed
