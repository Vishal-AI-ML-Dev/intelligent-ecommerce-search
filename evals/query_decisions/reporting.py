"""Reproducible offline report for one run over one sealed batch (`qd-report-1`).

Built from the sealed queries and labels, the run's `run.json` and journal, and the
deterministic parse recomputed now. It makes no call and writes nothing except the `--out` file
of the CLI (exclusive creation). It contains no timestamps, so the same inputs give the same
bytes. It does not replace `evaluate` (`qd-diagnostic-2`), whose report is unchanged.

Sections:

* `run`: attempts, results, valid results, fallbacks by reason, unknown outcomes (dispatched,
  no recorded result) and queries not attempted, each counted separately.
* `category` and `intent`: `category_metrics.classification_metrics` and
  `category_metrics.intent_metrics` for the deterministic parser and for Jev. Unclear golds are
  counted, never scored; `none` is a decision and abstention is not. For a category-only batch
  (`qd-cat-1` labels) intent was not annotated: every member is excluded from the intent
  metrics and counted in `not_annotated_excluded`, and the report carries
  `annotation_mode: category_only`. Reports over full-label batches are unchanged.
* `category_probability`: diagnostics of Jev's **raw provider probabilities** for category.
  These are not calibrated confidence and are never called so.
  - Eligible: queries whose gold is a single label (one category or `none`) and whose recorded
    outcome is valid. Ambiguous, multi-intent and unclear golds, fallbacks, unknown outcomes and
    unattempted queries are excluded and counted; no score is made up for them.
  - Brier score (multiclass): the mean over eligible queries of
    sum over the five labels (four categories and `none`) of (p_label - y_label)^2, where y is 1
    for the gold label and 0 otherwise. Range 0 to 2; lower is better.
  - Reliability: confidence is the highest raw probability. A query whose top probability is
    tied is excluded from the reliability bins and counted. Correct means the unique top label
    equals the gold label. Bins are `bins` equal-width intervals, (i/B, (i+1)/B] with the first
    bin also holding 0. For each bin: n, mean confidence, accuracy and their gap.
  - ECE: sum over non-empty bins of (n_bin / N) * |accuracy_bin - mean confidence_bin|, where N
    is the number of queries in the bins.
* `latency`: summaries of the provider-measured `latency_ms` of recorded outcomes (the client's
  wall time for the call, including any deadline wait), for all results and for valid results.
  Quantiles use the nearest-rank method: the value at 1-based rank ceil(q * n) of the sorted
  samples. Attempts without a recorded outcome have no timing and are counted as missing.
* `usage_and_cost`: returned token counts and an estimated cost from the prices recorded in
  `run.json`. This is an estimate from returned usage, not billing data. Attempts with unknown
  usage (no recorded outcome, or an outcome without token counts) are counted; they may still
  be billed, so the per-1,000-attempt figure counts them at zero and is a lower estimate.

Dry runs and synthetic fixtures are marked `synthetic`. The report is development diagnostic
evidence only, never held-out evidence.
"""

import math
from collections import Counter
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from evals.query_decisions.category_metrics import (
    classification_metrics,
    deterministic_intent_predictor,
    deterministic_predictor,
    intent_metrics,
    jev_intent_predictor,
    jev_predictor,
)
from evals.query_decisions.contracts import (
    CATEGORY_LABELS,
    AnnotationMode,
    GoldKind,
    QueryDecisionError,
)
from evals.query_decisions.labeling import load_sealed
from evals.query_decisions.partitions import MemberRecord, batch_records
from evals.query_decisions.runner import RunHeader, RunMode

REPORT_VERSION = "qd-report-1"
DEFAULT_BINS = 10  # a reporting choice, not a calibration parameter
QUANTILES = (Decimal("0.5"), Decimal("0.9"), Decimal("0.95"), Decimal("0.99"))
_PLACES = Decimal("0.000001")
_MILLION = Decimal(1_000_000)


def _fixed(value: Decimal) -> str:
    return str(value.quantize(_PLACES, ROUND_HALF_EVEN))


def _decimal(value: float) -> Decimal:
    return Decimal(repr(value))


def build_report(work_dir: Path, run_id: str, *, bins: int = DEFAULT_BINS) -> dict[str, Any]:
    """The `qd-report-1` report (see the module docstring)."""
    seal, _queries, _labels = load_sealed(work_dir)
    records = batch_records(work_dir, run_id)
    header = records[0].run if records else None
    if header is None:
        raise QueryDecisionError("no such run in the work directory")
    synthetic = header.mode is RunMode.DRY_RUN or seal.synthetic
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "evidence_scope": "development diagnostic; not held-out evidence",
        "synthetic": synthetic,
        "run_id": run_id,
        "mode": header.mode.value,
        "model": header.model,
        "question_set_version": header.question_set_version,
        "queries_sha256": seal.queries_sha256,
        "labels_sha256": seal.labels_sha256,
        "queries": len(records),
        "query_sources": {source.value: count for source, count in seal.counts().items()},
        "run": run_summary(records),
        "category": {
            "deterministic": classification_metrics(records, deterministic_predictor),
            "jev": classification_metrics(records, jev_predictor),
        },
        "intent": {
            "deterministic": intent_metrics(records, deterministic_intent_predictor),
            "jev": intent_metrics(records, jev_intent_predictor),
        },
        "category_probability": category_probability_diagnostics(records, bins=bins),
        "latency": latency_summary(records),
        "usage_and_cost": usage_and_cost(records, header),
    }
    if seal.annotation_mode is AnnotationMode.CATEGORY_ONLY:
        report["annotation_mode"] = seal.annotation_mode.value
    return report


def run_summary(records: Sequence[MemberRecord]) -> dict[str, Any]:
    results = [r.outcome for r in records if r.outcome is not None]
    fallbacks = Counter(o.fallback_reason for o in results if o.fallback_reason is not None)
    return {
        "attempted": sum(r.attempted for r in records),
        "results": len(results),
        "valid_results": sum(o.fallback_reason is None for o in results),
        "fallbacks": dict(sorted(fallbacks.items())),
        "unknown_outcome": sum(r.attempted and r.outcome is None for r in records),
        "not_attempted": sum(not r.attempted for r in records),
        "run_complete": all(r.run_complete for r in records),
    }


def category_probability_diagnostics(
    records: Sequence[MemberRecord], *, bins: int = DEFAULT_BINS
) -> dict[str, Any]:
    """Brier score and reliability of raw category probabilities (see the module docstring)."""
    if not 1 <= bins <= 100:
        raise ValueError("bins must be between 1 and 100")
    excluded: Counter[str] = Counter()
    squared_errors: list[Decimal] = []
    binned: list[list[tuple[Decimal, bool]]] = [[] for _ in range(bins)]
    for record in records:
        gold = record.category_gold
        outcome = record.outcome
        if gold.kind is GoldKind.UNCLEAR:
            excluded["unclear_gold"] += 1
            continue
        if gold.kind is not GoldKind.LABEL:
            excluded["ambiguous_gold"] += 1
            continue
        if not record.attempted:
            excluded["not_attempted"] += 1
            continue
        if outcome is None:
            excluded["unknown_outcome"] += 1
            continue
        if outcome.fallback_reason is not None or outcome.category is None:
            excluded["fallback"] += 1
            continue
        probabilities = {
            label: _decimal(value)
            for label, value in outcome.category.provider_probabilities.items()
        }
        squared_errors.append(
            sum(
                (probabilities[label] - (1 if label == gold.label else 0)) ** 2
                for label in CATEGORY_LABELS
            )
        )
        if len(outcome.category.top_labels) != 1:
            excluded["tied_top_for_reliability"] += 1
            continue
        confidence = max(probabilities.values())
        index = 0 if confidence == 0 else min(bins - 1, math.ceil(confidence * bins) - 1)
        binned[index].append((confidence, outcome.category.top_labels[0] == gold.label))

    scored = len(squared_errors)
    in_bins = sum(len(contents) for contents in binned)
    rows = []
    ece = Decimal(0)
    for index, contents in enumerate(binned):
        lower, upper = Decimal(index) / bins, Decimal(index + 1) / bins
        row: dict[str, Any] = {
            "interval": f"[{_fixed(lower)}, {_fixed(upper)}]"
            if index == 0
            else f"({_fixed(lower)}, {_fixed(upper)}]",
            "n": len(contents),
            "mean_raw_probability": None,
            "accuracy": None,
            "gap": None,
        }
        if contents:
            mean = sum(c for c, _ in contents) / len(contents)
            accuracy = Decimal(sum(correct for _, correct in contents)) / len(contents)
            gap = abs(accuracy - mean)
            ece += Decimal(len(contents)) / in_bins * gap
            row.update(
                mean_raw_probability=_fixed(mean), accuracy=_fixed(accuracy), gap=_fixed(gap)
            )
        rows.append(row)
    return {
        "probability_kind": "raw provider probability; not calibrated confidence",
        "labels": list(CATEGORY_LABELS),
        "eligible": scored,
        "excluded": dict(sorted(excluded.items())),
        "brier_score": None if scored == 0 else _fixed(sum(squared_errors) / scored),
        "brier_definition": "mean over eligible queries of sum over labels of (p - y)^2; 0 to 2",
        "reliability": {
            "bins": bins,
            "n": in_bins,
            "rows": rows,
            "ece": None if in_bins == 0 else _fixed(ece),
            "ece_definition": "sum over non-empty bins of n_bin / n * |accuracy - mean|",
        },
    }


def _nearest_rank(ordered: Sequence[Decimal], quantile: Decimal) -> Decimal:
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _summary(samples: Sequence[Decimal]) -> dict[str, Any]:
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "min": _fixed(ordered[0]),
        "max": _fixed(ordered[-1]),
        "mean": _fixed(sum(ordered) / len(ordered)),
        **{f"p{int(q * 100)}": _fixed(_nearest_rank(ordered, q)) for q in QUANTILES},
    }


def latency_summary(records: Sequence[MemberRecord]) -> dict[str, Any]:
    """Observed provider latency of recorded outcomes (see the module docstring)."""
    outcomes = [r.outcome for r in records if r.outcome is not None]
    return {
        "source": "JevOutcome.latency_ms, measured by the provider client per attempt",
        "quantile_method": "nearest rank: sorted samples, 1-based rank ceil(q * n)",
        "all_results_ms": _summary([_decimal(o.latency_ms) for o in outcomes]),
        "valid_results_ms": _summary(
            [_decimal(o.latency_ms) for o in outcomes if o.fallback_reason is None]
        ),
        "missing_timing": {
            "unknown_outcome": sum(r.attempted and r.outcome is None for r in records),
            "not_attempted": sum(not r.attempted for r in records),
        },
        "note": "one sequential run; not a latency benchmark and not latency under load",
    }


def usage_and_cost(records: Sequence[MemberRecord], header: RunHeader) -> dict[str, Any]:
    """Returned token usage and an estimated cost (see the module docstring)."""
    attempted = [r for r in records if r.attempted]
    known = [
        r.outcome
        for r in attempted
        if r.outcome is not None
        and r.outcome.input_tokens is not None
        and r.outcome.output_tokens is not None
    ]
    input_tokens = sum(o.input_tokens or 0 for o in known)
    output_tokens = sum(o.output_tokens or 0 for o in known)
    prices = (header.price_usd_per_million_input, header.price_usd_per_million_output)
    result: dict[str, Any] = {
        "kind": "estimate from returned usage and recorded prices; not billing data",
        "attempted": len(attempted),
        "attempts_with_returned_usage": len(known),
        "attempts_with_unknown_usage": len(attempted) - len(known),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "price_usd_per_million_input": None if prices[0] is None else str(prices[0]),
        "price_usd_per_million_output": None if prices[1] is None else str(prices[1]),
        "price_checked_on": None
        if header.price_checked_on is None
        else header.price_checked_on.isoformat(),
        "estimated_usd_returned_usage": None,
        "estimated_usd_per_1000_attempts_lower": None,
        "estimated_usd_per_1000_attempts_with_usage": None,
        "note": "attempts with unknown usage may still be billed; they count as zero here",
    }
    if prices[0] is None or prices[1] is None:
        result["note"] = "no prices recorded for this run; no cost estimate"
        return result
    cost = (input_tokens * prices[0] + output_tokens * prices[1]) / _MILLION
    result["estimated_usd_returned_usage"] = _fixed(cost)
    if attempted:
        result["estimated_usd_per_1000_attempts_lower"] = _fixed(cost / len(attempted) * 1000)
    if known:
        result["estimated_usd_per_1000_attempts_with_usage"] = _fixed(cost / len(known) * 1000)
    return result
