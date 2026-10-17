"""Inter-annotator agreement (`iaa-1`) for the review-2 amendment of M9.

Computed from two sealed rounds only: the primary labels (`ann-1`) and the blind second-review
labels (`ann-2`) on the cs-1 sample, before any consensus adjudication. Reported:

* the 3×3 confusion matrix (rows: primary label; columns: second-review label);
* observed agreement, chance agreement and unweighted Cohen's kappa on the three labels, and on
  the strict (relevant vs rest) and lenient (relevant or borderline vs not_relevant) binary
  collapses;
* per-stratum observed agreement, a stratum being one primary label (the cs-1 sample is
  stratified by exactly that label).

Arithmetic is exact (`Fraction`); values are 10-decimal strings rounded half-even (the `eval-1`
convention). Kappa is null when chance agreement is 1. Nothing here is a population estimate:
the sample over-represents rarer labels, and the report carries that caveat. This is
inter-annotator agreement, never intra-annotator consistency (which review-2 does not measure).
"""

from collections.abc import Sequence
from datetime import datetime
from fractions import Fraction

from evals.contracts.canonical import ContractError
from evals.contracts.constants import LABEL_ORDER, LENIENT_RELEVANT, STRICT_RELEVANT, Label
from evals.contracts.models import (
    AgreementReport,
    AgreementStatistics,
    ConsistencySample,
    Judgement,
    Round,
    StratumAgreement,
)
from evals.metrics.retrieval import fmt

Pair = tuple[str, str]


def _statistics[T](pairs: Sequence[tuple[T, T]], categories: Sequence[T]) -> AgreementStatistics:
    n = len(pairs)
    agreed = sum(1 for a, b in pairs if a == b)
    if n == 0:
        return AgreementStatistics(
            agreed_count=0, observed_agreement=None, expected_agreement=None, cohen_kappa=None
        )
    observed = Fraction(agreed, n)
    expected = sum(
        (
            Fraction(sum(1 for a, _ in pairs if a == c), n)
            * Fraction(sum(1 for _, b in pairs if b == c), n)
            for c in categories
        ),
        Fraction(0),
    )
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return AgreementStatistics(
        agreed_count=agreed,
        observed_agreement=fmt(observed),
        expected_agreement=fmt(expected),
        cohen_kappa=fmt(kappa),
    )


def label_pairs(
    primaries: Sequence[Judgement],
    sample: ConsistencySample,
    second_reviews: Sequence[Judgement],
) -> list[tuple[Label, Label]]:
    """(primary label, second-review label) for every sampled pair, in the sample's blind
    order. Every sampled pair must have exactly one judgement of each round."""
    primary = {(j.query_id, j.product_id): j for j in primaries if j.round is Round.PRIMARY}
    reviewed: dict[Pair, Judgement] = {}
    for j in second_reviews:
        pair = (j.query_id, j.product_id)
        if j.round is not Round.SECOND_REVIEW or pair in reviewed:
            raise ContractError(f"{pair!r}: not exactly one second-review judgement")
        reviewed[pair] = j
    sampled = [(i.query_id, i.product_id) for i in sample.items]
    if set(reviewed) != set(sampled) or not set(sampled) <= set(primary):
        raise ContractError("agreement needs one primary and one second-review label per item")
    return [(primary[p].label, reviewed[p].label) for p in sampled]


def build_agreement_report(
    primaries: Sequence[Judgement],
    sample: ConsistencySample,
    second_reviews: Sequence[Judgement],
    *,
    primary_annotator: str,
    second_annotator: str,
    second_review_sha256: str,
    second_review_sealed_at: datetime,
) -> AgreementReport:
    pairs = label_pairs(primaries, sample, second_reviews)
    index = {label: i for i, label in enumerate(LABEL_ORDER)}
    matrix = [[0, 0, 0] for _ in LABEL_ORDER]
    for a, b in pairs:
        matrix[index[a]][index[b]] += 1
    strata = []
    for label in LABEL_ORDER:
        row = matrix[index[label]]
        total, agreed = sum(row), row[index[label]]
        strata.append(
            StratumAgreement(
                primary_label=label,
                item_count=total,
                agreed_count=agreed,
                observed_agreement=fmt(Fraction(agreed, total)) if total else None,
            )
        )
    return AgreementReport(
        primary_annotator=primary_annotator,
        second_annotator=second_annotator,
        primary_seal_sha256=sample.primary_seal_sha256,
        second_review_sha256=second_review_sha256,
        second_review_sealed_at=second_review_sealed_at,
        item_count=len(pairs),
        confusion=tuple(tuple(row) for row in matrix),  # type: ignore[arg-type]
        three_label=_statistics(pairs, LABEL_ORDER),
        strict_binary=_statistics(
            [(a in STRICT_RELEVANT, b in STRICT_RELEVANT) for a, b in pairs], (True, False)
        ),
        lenient_binary=_statistics(
            [(a in LENIENT_RELEVANT, b in LENIENT_RELEVANT) for a, b in pairs], (True, False)
        ),
        strata=tuple(strata),  # type: ignore[arg-type]
        disagreement_count=sum(1 for a, b in pairs if a is not b),
    )
