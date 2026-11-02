"""`return-reason-scoring-1`: compare stored baseline results with human labels.

Only returns of the chosen set that the human labelled are scored; coverage is reported. The
test set is refused unless `final=True` (the final comparison).

**Rules**
* *Baseline categories* are its `single_category` predictions. An unresolved prediction never
  counts as a prediction of any one of its candidates.
* *Per-category rows* (every taxonomy category except `insufficient_evidence`): TP, FP and FN
  per return; precision and recall with 95% Wilson intervals. A human
  `defect_kind_undetermined` mark counts toward no specific defect kind.
* *Concern rows* score unresolved sets at the level they resolve to:
  - `defect_any_kind`: the baseline predicts it with a single `product_quality_defect` or
    `manufacturing_defect`, or the unresolved defect pair. The human marks it with either kind
    or with `defect_kind_undetermined`.
  - `description_either`: the baseline predicts it with a single `expectation_mismatch` or
    `incorrect_specification`, or that unresolved pair. The human marks it with either.
* *Abstention* is compared with the human's `insufficient_evidence` as a 2x2 table, with the
  abstention and review (`needs_review`) rates.
* *Exact-set match:* the baseline set is its single categories, plus
  `defect_kind_undetermined` for an unresolved defect pair, plus `insufficient_evidence` when
  it abstains. It is compared with the human set. An unresolved expectation/specification pair
  adds the token `unresolved:expectation_mismatch|incorrect_specification`, which no human
  label equals, so such returns never match exactly.
* *Small samples:* every proportion carries its numerator and denominator. A row with a
  denominator below `MIN_DENOMINATOR` is flagged `too_few_to_interpret`. There is no single
  headline accuracy, no macro or micro average, and no agreement statistic (one annotator).
"""

import math
from typing import Any

from ecommerce_search.returns.classification.contracts import (
    ClassificationResult,
    ClassificationStatus,
    Resolution,
)
from ecommerce_search.returns.classification.taxonomy import ReturnCategory
from evals.returns.labels import DEFECT_KIND_UNDETERMINED, INSUFFICIENT, HumanLabel

SCORING_VERSION = "return-reason-scoring-1"
MIN_DENOMINATOR = 10
Z95 = 1.959963984540054
DEFECT = frozenset({"product_quality_defect", "manufacturing_defect"})
DESCRIPTION = frozenset({"expectation_mismatch", "incorrect_specification"})
UNRESOLVED_DESCRIPTION = "unresolved:expectation_mismatch|incorrect_specification"
CATEGORY_ROWS = tuple(c.value for c in ReturnCategory if c.value != INSUFFICIENT)


class ScoringError(ValueError):
    """A fixed, already-safe message."""


def wilson(successes: int, n: int) -> tuple[float, float] | None:
    """The 95% Wilson score interval, or `None` when `n` is 0."""
    if n == 0:
        return None
    p = successes / n
    z2 = Z95 * Z95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def proportion(successes: int, n: int) -> dict[str, Any]:
    return {
        "numerator": successes,
        "denominator": n,
        "value": round(successes / n, 4) if n else None,
        "wilson95": wilson(successes, n),
        "too_few_to_interpret": n < MIN_DENOMINATOR,
    }


def _singles(result: ClassificationResult) -> set[str]:
    return {
        p.category.value
        for p in result.predictions
        if p.resolution is Resolution.SINGLE_CATEGORY and p.category is not None
    }


def _unresolved(result: ClassificationResult) -> set[frozenset[str]]:
    return {
        frozenset(c.value for c in p.candidates)
        for p in result.predictions
        if p.resolution is Resolution.UNRESOLVED_CANDIDATES
    }


def baseline_set(result: ClassificationResult) -> frozenset[str]:
    tokens = _singles(result)
    unresolved = _unresolved(result)
    if DEFECT in unresolved:
        tokens.add(DEFECT_KIND_UNDETERMINED)
    if DESCRIPTION in unresolved:
        tokens.add(UNRESOLVED_DESCRIPTION)
    if result.status is ClassificationStatus.ABSTAINED:
        tokens.add(INSUFFICIENT)
    return frozenset(tokens)


def _row(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": proportion(tp, tp + fp),
        "recall": proportion(tp, tp + fn),
    }


def score(
    set_name: str,
    set_return_ids: frozenset[str],
    results: dict[str, ClassificationResult],
    labels: dict[str, HumanLabel],
    *,
    final: bool = False,
    run_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if set_name == "test" and not final:
        raise ScoringError("the test set is scored only in the final comparison (final=True)")
    if set_name not in ("dev", "test"):
        raise ScoringError("only the dev and test sets are scored")
    if any(rid not in set_return_ids for rid in labels):
        raise ScoringError("a label belongs to a return outside this set")
    scored = sorted(labels)
    missing = [rid for rid in scored if rid not in results]
    if missing:
        raise ScoringError(f"{len(missing)} labelled returns have no baseline result")

    counts = {row: [0, 0, 0] for row in (*CATEGORY_ROWS, "defect_any_kind", "description_either")}
    abstention = {"abstained_and_human_insufficient": 0, "abstained_but_human_labelled": 0}
    abstention |= {"classified_but_human_insufficient": 0, "classified_and_human_labelled": 0}
    exact = needs_review = 0
    for rid in scored:
        result, human = results[rid], labels[rid].tokens
        singles, unresolved = _singles(result), _unresolved(result)
        predicted_rows = {c: c in singles for c in CATEGORY_ROWS}
        gold_rows = {c: c in human for c in CATEGORY_ROWS}
        predicted_rows["defect_any_kind"] = bool(singles & DEFECT) or DEFECT in unresolved
        gold_rows["defect_any_kind"] = bool(human & (DEFECT | {DEFECT_KIND_UNDETERMINED}))
        predicted_rows["description_either"] = (
            bool(singles & DESCRIPTION) or DESCRIPTION in unresolved
        )
        gold_rows["description_either"] = bool(human & DESCRIPTION)
        for row, c in counts.items():
            p, g = predicted_rows[row], gold_rows[row]
            c[0] += p and g
            c[1] += p and not g
            c[2] += g and not p
        abstained = result.status is ClassificationStatus.ABSTAINED
        insufficient = INSUFFICIENT in human
        key = (
            ("abstained_and_human_insufficient" if insufficient else "abstained_but_human_labelled")
            if abstained
            else (
                "classified_but_human_insufficient"
                if insufficient
                else "classified_and_human_labelled"
            )
        )
        abstention[key] += 1
        exact += baseline_set(result) == human
        needs_review += result.needs_review
    n = len(scored)
    abstained_total = (
        abstention["abstained_and_human_insufficient"] + abstention["abstained_but_human_labelled"]
    )
    human_insufficient = (
        abstention["abstained_and_human_insufficient"]
        + abstention["classified_but_human_insufficient"]
    )
    return {
        "scoring_version": SCORING_VERSION,
        "set": set_name,
        "run": run_info or {},
        "annotators": sorted({labels[rid].annotator for rid in scored}),
        "coverage": proportion(n, len(set_return_ids)),
        "categories": {row: _row(*counts[row]) for row in CATEGORY_ROWS},
        "concerns": {row: _row(*counts[row]) for row in ("defect_any_kind", "description_either")},
        "abstention": {
            "table": abstention,
            "abstention_rate": proportion(abstained_total, n),
            "abstention_precision": proportion(
                abstention["abstained_and_human_insufficient"], abstained_total
            ),
            "insufficient_evidence_recall": proportion(
                abstention["abstained_and_human_insufficient"], human_insufficient
            ),
        },
        "review_rate": proportion(needs_review, n),
        "exact_set_match": proportion(exact, n),
        "limits": [
            "synthetic, project-authored data; not real-world accuracy",
            "one annotator: no inter-annotator agreement",
            f"rows with a denominator below {MIN_DENOMINATOR} are too few to interpret",
            "no single headline accuracy is reported",
        ],
    }
