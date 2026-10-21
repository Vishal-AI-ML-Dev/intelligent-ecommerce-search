"""Label-based task metrics and descriptive counts for the M14 listing evaluation (offline).

**Label-based metrics** (`task_metrics`) score Jev's `jl-1` proposals against external source
labels on clean items of the selected split. Every denominator is explicit:

* `eligible`: clean items with a label for the task whose scoring is exact. Items excluded by the
  mapping (`excluded_ambiguous_mapping`) are counted apart and never enter any denominator; the
  exclusion is decided by the label, before and independently of any prediction.
* `attempted`: eligible items with a recorded outcome; `not_attempted` = eligible - attempted.
* `abstentions`: attempted items without a unique top label: `tie` or `fallback` (by reason).
* `predictions`: attempted items with a unique top label, `none` included (a decision).
  `predictions` = `correct` + `wrong` + `predicted_none`, where `predicted_none` is a `none`
  answer for a gold label other than `none`.
* `overall_accuracy` = correct / eligible; `answered_accuracy` = correct / predictions;
  `coverage` = predictions / eligible. A zero denominator gives `null` with a reason.

The deterministic baseline makes no proposal, so it is reported apart (`baseline_task_metrics`):
zero predictions, coverage 0 of eligible, accuracy `null`. It is not a category predictor.

**Descriptive counts** (`descriptive`): route and reason-code counts per arm, Jev tie/`none`
counts, support-label counts and a title-word disclosure. No correctness is implied: no expected
route or ambiguity label exists.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ecommerce_search.decision.listing_models import (
    CATEGORY_LABELS,
    NONE_LABEL,
    SUBCATEGORY_LABELS,
    ListingDecisionOutcome,
    ListingDecisionState,
)
from ecommerce_search.decision.listing_routing import RoutingDecision
from ecommerce_search.normalization.models import ListingNormalization
from evals.listing_decisions.contracts import EvalItem, SubcategoryScoring, Task
from evals.listing_decisions.matching import MATCHING_VERSION, mentions

_PLACES = Decimal("0.0001")
NO_ELIGIBLE = "no eligible labelled items"
NO_PREDICTIONS = "no predictions"
NO_PREDICTIONS_OF_LABEL = "no predictions of this label"
NO_GOLD_OF_LABEL = "no eligible items with this gold label"
F1_UNDEFINED = "precision or recall is null, or both are zero"
POOLED_WARNING = (
    "pooled over sources: each category comes from a different source and format, so a pooled "
    "figure can reflect source format as much as content; read the per-source figures"
)
BASELINE_NOTE = (
    "the deterministic baseline makes no category or subcategory proposal; it is reported for "
    "coverage only and is not an equivalent predictor"
)


@dataclass(frozen=True)
class ItemRecord:
    item: EvalItem
    normalization: ListingNormalization
    state: ListingDecisionState
    deterministic: RoutingDecision
    outcome: ListingDecisionOutcome | None  # the recorded Jev outcome, if any
    jev: RoutingDecision | None


def ratio(numerator: int, denominator: int, reason: str) -> dict[str, Any]:
    if denominator == 0:
        value, why = None, reason
    else:
        quotient = Decimal(numerator) / Decimal(denominator)
        value, why = str(quotient.quantize(_PLACES, ROUND_HALF_EVEN)), None
    return {"numerator": numerator, "denominator": denominator, "value": value, "reason": why}


def _labels(task: Task) -> tuple[str, ...]:
    return CATEGORY_LABELS if task is Task.CATEGORY else SUBCATEGORY_LABELS


def jev_prediction(record: ItemRecord, task: Task) -> tuple[str, str | None]:
    """(`not_attempted` | `fallback` | `tie` | `label`, detail): the fallback reason or label."""
    outcome = record.outcome
    if outcome is None:
        return "not_attempted", None
    if outcome.fallback_reason is not None:
        return "fallback", outcome.fallback_reason
    proposal = outcome.category if task is Task.CATEGORY else outcome.subcategory
    if proposal is None:  # unreachable: a successful outcome carries both proposals
        raise ValueError("a successful outcome without its proposal")
    if len(proposal.top_labels) > 1:
        return "tie", None
    return "label", proposal.top_labels[0]


def _split_eligible(
    records: Sequence[ItemRecord], task: Task
) -> tuple[list[tuple[ItemRecord, str]], int]:
    eligible, excluded = [], 0
    for record in records:
        label = record.item.label(task)
        if label is None:
            continue
        if label.scoring is SubcategoryScoring.EXCLUDED_AMBIGUOUS_MAPPING:
            excluded += 1
            continue
        eligible.append((record, label.value))
    return eligible, excluded


def _score(eligible: list[tuple[ItemRecord, str]], task: Task) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    fallbacks: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = {}
    predicted: Counter[str] = Counter()
    correct_by: Counter[str] = Counter()
    gold_by: Counter[str] = Counter()
    for record, gold in eligible:
        kind, detail = jev_prediction(record, task)
        gold_by[gold] += 1
        column = detail if kind == "label" else kind
        confusion.setdefault(gold, Counter())[column] += 1
        if kind == "not_attempted":
            counts["not_attempted"] += 1
            continue
        counts["attempted"] += 1
        if kind == "fallback":
            counts["fallback"] += 1
            fallbacks[detail] += 1
        elif kind == "tie":
            counts["tie"] += 1
        else:
            counts["predictions"] += 1
            predicted[detail] += 1
            if detail == gold:
                counts["correct"] += 1
                correct_by[gold] += 1
            elif detail == NONE_LABEL:
                counts["predicted_none"] += 1
            else:
                counts["wrong"] += 1
    n, predictions = len(eligible), counts["predictions"]
    per_label = {}
    for label in _labels(task):
        if not gold_by[label] and not predicted[label]:
            continue
        precision = ratio(correct_by[label], predicted[label], NO_PREDICTIONS_OF_LABEL)
        recall = ratio(correct_by[label], gold_by[label], NO_GOLD_OF_LABEL)
        per_label[label] = {
            "gold": gold_by[label],
            "predicted": predicted[label],
            "correct": correct_by[label],
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return {
        "eligible": n,
        "attempted": counts["attempted"],
        "not_attempted": counts["not_attempted"],
        "abstentions": {
            "total": counts["tie"] + counts["fallback"],
            "tie": counts["tie"],
            "fallback": counts["fallback"],
            "fallback_by_reason": dict(sorted(fallbacks.items())),
        },
        "predictions": predictions,
        "correct": counts["correct"],
        "wrong": counts["wrong"],
        "predicted_none": counts["predicted_none"],
        "overall_accuracy": ratio(counts["correct"], n, NO_ELIGIBLE),
        "answered_accuracy": ratio(counts["correct"], predictions, NO_PREDICTIONS),
        "coverage": ratio(predictions, n, NO_ELIGIBLE),
        "per_label": per_label,
        "confusion": {gold: dict(sorted(row.items())) for gold, row in sorted(confusion.items())},
    }


def _f1(precision: dict[str, Any], recall: dict[str, Any]) -> dict[str, Any]:
    p, r = precision["value"], recall["value"]
    if p is None or r is None or Decimal(p) + Decimal(r) == 0:
        return {"value": None, "reason": F1_UNDEFINED}
    value = 2 * Decimal(p) * Decimal(r) / (Decimal(p) + Decimal(r))
    return {"value": str(value.quantize(_PLACES, ROUND_HALF_EVEN)), "reason": None}


def _by_source(records: Sequence[ItemRecord]) -> dict[str, list[ItemRecord]]:
    groups: dict[str, list[ItemRecord]] = {}
    for record in records:
        groups.setdefault(record.item.source_id, []).append(record)
    return dict(sorted(groups.items()))


def task_metrics(records: Sequence[ItemRecord], task: Task) -> dict[str, Any]:
    """Jev against external labels, per source and pooled (with its warning)."""

    def one(group: Sequence[ItemRecord]) -> dict[str, Any]:
        eligible, excluded = _split_eligible(group, task)
        return {"excluded_ambiguous_mapping": excluded, **_score(eligible, task)}

    return {
        "label_basis": "external source labels (source or mapped); not human-reviewed gold",
        "by_source": {source: one(group) for source, group in _by_source(records).items()},
        "pooled": {"warning": POOLED_WARNING, **one(records)},
    }


def baseline_task_metrics(records: Sequence[ItemRecord], task: Task) -> dict[str, Any]:
    eligible, excluded = _split_eligible(records, task)
    if any(r.deterministic.provider != "deterministic" for r, _ in eligible):
        raise AssertionError("baseline routing must come from the deterministic provider")
    n = len(eligible)
    return {
        "note": BASELINE_NOTE,
        "proposes": False,
        "eligible": n,
        "excluded_ambiguous_mapping": excluded,
        "predictions": 0,
        "coverage": ratio(0, n, NO_ELIGIBLE),
        "overall_accuracy": {"value": None, "reason": BASELINE_NOTE},
        "answered_accuracy": {"value": None, "reason": NO_PREDICTIONS},
    }


def _route_counts(decisions: Sequence[RoutingDecision]) -> dict[str, Any]:
    return {
        "n": len(decisions),
        "routes": dict(sorted(Counter(d.route.value for d in decisions).items())),
        "reason_codes": dict(sorted(Counter(r.code for d in decisions for r in d.reasons).items())),
    }


def descriptive(records: Sequence[ItemRecord]) -> dict[str, Any]:
    """Counts only: no correctness is implied by any number here."""
    jev = [r for r in records if r.outcome is not None]
    proposals: Counter[str] = Counter()
    support: Counter[str] = Counter()
    for outcome in (r.outcome for r in jev if r.outcome is not None):
        if outcome.fallback_reason is not None:
            proposals[f"fallback.{outcome.fallback_reason}"] += 1
            continue
        for name, proposal in (
            ("category", outcome.category),
            ("subcategory", outcome.subcategory),
        ):
            if proposal is None:  # unreachable: a successful outcome carries both proposals
                raise ValueError("a successful outcome without its proposal")
            if len(proposal.top_labels) > 1:
                proposals[f"{name}.tie"] += 1
            elif proposal.label is None:
                proposals[f"{name}.none"] += 1
        for p in outcome.attribute_support:
            support["tie" if len(p.top_labels) > 1 else str(p.label)] += 1
    titles: dict[str, dict[str, int]] = {}
    for source, group in _by_source(records).items():
        labelled = [(r, r.item.label(Task.CATEGORY)) for r in group]
        labelled = [
            (r, lab.value) for r, lab in labelled if lab is not None and lab.value != NONE_LABEL
        ]
        hits = sum(1 for r, value in labelled if mentions(r.state.text.title or "", value))
        titles[source] = {"labelled_items": len(labelled), "title_contains_category_word": hits}
    return {
        "note": "descriptive counts; no expected route or ambiguity label exists",
        "deterministic_routes": _route_counts([r.deterministic for r in records]),
        "jev_routes": _route_counts([r.jev for r in jev if r.jev is not None]),
        "jev_items_without_outcome": len(records) - len(jev),
        "jev_abstention_counts": dict(sorted(proposals.items())),
        "jev_support_labels": dict(sorted(support.items())),
        "title_word_disclosure": {
            "note": "a category word in the seller's own title is evidence, not label leakage",
            "matching": MATCHING_VERSION,
            "by_source": titles,
        },
        "missing_required_fields": missing_required(records),
    }


def missing_required(records: Sequence[ItemRecord]) -> dict[str, Any]:
    """Per source: clean items whose M13 run reports `missing_required`, by field. Missing seller
    claims are never filled in; each one routes its listing to manual review."""
    by_source = {}
    for source, group in _by_source(records).items():
        fields: Counter[str] = Counter()
        any_missing = 0
        for record in group:
            missing = {
                f.field
                for f in record.normalization.findings
                if f.kind.value == "missing_required" and f.field is not None
            }
            any_missing += bool(missing)
            fields.update(missing)
        by_source[source] = {
            "items": len(group),
            "items_missing_any_required": any_missing,
            "by_field": {
                field: ratio(count, len(group), NO_ELIGIBLE)
                for field, count in sorted(fields.items())
            },
        }
    return {
        "note": "missing seller claims are kept missing, never fabricated; each routes to review",
        "by_source": by_source,
    }
