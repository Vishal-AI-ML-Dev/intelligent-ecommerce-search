"""Retrieval metrics against hand-calculated values. Every label, ranking and eligibility record
here is a SYNTHETIC TEST FIXTURE; no real system output or human label is used.

Fixture (query qA = gq-p01): judged pool P1 relevant, P2 borderline, P3 not_relevant,
P4 relevant, P5 not_relevant. Ranked run: P3, P1, X (unjudged), P2, P4, P5.

Hand calculation at K=5 (top five: P3, P1, X, P2, P4):
* strict: relevant judged = {P1, P4} (2); hits 2 -> P@5 = 2/5 = 0.4, R@5 = 2/2 = 1,
  first hit at rank 2 -> RR = 1/2.
* lenient: relevant judged = {P1, P2, P4} (3); hits 3 -> P@5 = 3/5 = 0.6, R@5 = 1, RR = 1/2.
* NDCG@5: gains (0, 2, 0, 1, 2); DCG = 2/log2(3) + 1/log2(5) + 2/log2(6)
  = 1.2618595071 + 0.4306765581 + 0.7737056145 = 2.4662416797;
  ideal (2, 2, 1, 0, 0): IDCG = 2/log2(2) + 2/log2(3) + 1/log2(4) = 2 + 1.2618595071 + 0.5
  = 3.7618595071; NDCG = 0.6555911179.
* irrelevant-result rate: P3 and X of 5 -> 0.4; unjudged = 1.
* CSR hard: P3 violated, X unknown, P1/P2/P4 satisfied -> 3/5 = 0.6, unknown 1/5 = 0.2,
  all-satisfied 0. CSR fp-1 (category only, satisfied for all five) -> 1, all-satisfied 1.
At K=10 (all six returned): strict P@10 = 2/10, lenient 3/10, irrelevant 3/6 = 0.5;
NDCG@10 adds a zero gain at rank 6, so it equals NDCG@5.

Query qB = gq-p02 (zero-relevant, no results) and qC = gq-p03 (zero-relevant, returns P6).
"""

import pytest
from evals.contracts.canonical import ContractError, canonical_json
from evals.contracts.constants import Label, Relation
from evals.contracts.models import ConstraintStatus, FinalJudgement, GoldField
from evals.metrics.retrieval import (
    K_VALUES,
    PRIMARY_K,
    evaluate_run,
    fmt,
    macro_value,
    query_value,
)
from golden_support import eligibility

from ecommerce_search.filtering.policy import FilterOperator

RELATION = {
    Label.RELEVANT: Relation.EXACT_MATCH,
    Label.BORDERLINE: Relation.SUBSTITUTE,
    Label.NOT_RELEVANT: Relation.OFF_TARGET,
}
A, B, C = "gq-p01", "gq-p02", "gq-p03"
LABELS = {
    (A, "P1"): Label.RELEVANT,
    (A, "P2"): Label.BORDERLINE,
    (A, "P3"): Label.NOT_RELEVANT,
    (A, "P4"): Label.RELEVANT,
    (A, "P5"): Label.NOT_RELEVANT,
    (B, "P6"): Label.NOT_RELEVANT,
    (B, "P7"): Label.NOT_RELEVANT,
    (C, "P6"): Label.NOT_RELEVANT,
}
RUN = {A: ["P3", "P1", "X", "P2", "P4", "P5"], B: [], C: ["P6"]}

SAT, VIO, UNK = ConstraintStatus.SATISFIED, ConstraintStatus.VIOLATED, ConstraintStatus.UNKNOWN
CAT, GENDER = GoldField.CATEGORY, GoldField.GENDER
STATUS_A = {
    "P1": (SAT, SAT),
    "P2": (SAT, SAT),
    "P3": (SAT, VIO),
    "P4": (SAT, SAT),
    "P5": (SAT, VIO),
    "X": (SAT, UNK),
}


def _finals():
    return [
        FinalJudgement(
            query_id=q,
            product_id=p,
            label=label,
            relation=RELATION[label],
            decided_in="primary",
            rubric_version="rubric-1",
        )
        for (q, p), label in LABELS.items()
    ]


def _eligibility(query_id, product_id):
    if query_id != A:
        return eligibility(query_id, product_id, [])
    category, gender = STATUS_A[product_id]
    return eligibility(
        query_id,
        product_id,
        [(CAT, FilterOperator.EQ, category), (GENDER, FilterOperator.EQ, gender)],
    )


@pytest.fixture(scope="module")
def result():
    return evaluate_run("synthetic-system", RUN, _finals(), eligibility=_eligibility)


@pytest.mark.parametrize(
    ("metric", "k", "definition", "expected"),
    [
        ("precision", 5, "strict", "0.4000000000"),
        ("precision", 5, "lenient", "0.6000000000"),
        ("recall", 5, "strict", "1.0000000000"),
        ("recall", 5, "lenient", "1.0000000000"),
        ("reciprocal_rank", 5, "strict", "0.5000000000"),
        ("reciprocal_rank", 5, "lenient", "0.5000000000"),
        ("ndcg", 5, "none", "0.6555911179"),
        ("irrelevant_result_rate", 5, "none", "0.4000000000"),
        ("unjudged", 5, "none", "1.0000000000"),
        ("csr", 5, "hard", "0.6000000000"),
        ("csr_unknown", 5, "hard", "0.2000000000"),
        ("csr_all_satisfied", 5, "hard", "0.0000000000"),
        ("csr", 5, "fp1", "1.0000000000"),
        ("csr_all_satisfied", 5, "fp1", "1.0000000000"),
        ("precision", 10, "strict", "0.2000000000"),
        ("precision", 10, "lenient", "0.3000000000"),
        ("irrelevant_result_rate", 10, "none", "0.5000000000"),
        ("ndcg", 10, "none", "0.6555911179"),
        ("zero_result", None, "none", "0.0000000000"),
        ("correct_empty", None, "strict", None),
    ],
)
def test_query_metrics_match_the_hand_calculation(result, metric, k, definition, expected):
    assert query_value(result, A, metric, k, definition) == expected


def test_strict_and_lenient_differ_and_are_both_reported(result):
    strict = query_value(result, A, "precision", PRIMARY_K, "strict")
    lenient = query_value(result, A, "precision", PRIMARY_K, "lenient")
    assert strict != lenient
    assert result.config.primary_definition == "strict"
    assert result.config.k_values == K_VALUES == (5, 10, 20)
    assert dict(result.config.gains) == {"relevant": 2, "borderline": 1, "not_relevant": 0}


def test_zero_relevant_queries_are_never_averaged_as_zero(result):
    for query_id in (B, C):
        for metric in ("recall", "reciprocal_rank"):
            for definition in ("strict", "lenient"):
                assert query_value(result, query_id, metric, 10, definition) is None
        assert query_value(result, query_id, "ndcg", 10, "none") is None
        assert query_value(result, query_id, "precision", 10, "strict") == "0.0000000000"
    assert query_value(result, B, "zero_result", None, "none") == "1.0000000000"
    assert query_value(result, B, "correct_empty", None, "strict") == "1.0000000000"
    assert query_value(result, B, "irrelevant_result_rate", 5, "none") is None
    assert query_value(result, C, "correct_empty", None, "strict") == "0.0000000000"
    assert query_value(result, C, "irrelevant_result_rate", 5, "none") == "1.0000000000"
    # No hard constraint: CSR is not applicable rather than 0 or 1.
    assert query_value(result, C, "csr", 5, "hard") is None


def test_macro_aggregation_reports_its_denominators(result):
    p5 = macro_value(result, "precision", 5, "strict")
    assert (p5.value, p5.n, p5.excluded) == ("0.1333333333", 3, 0)  # (0.4 + 0 + 0) / 3
    r5 = macro_value(result, "recall", 5, "strict")
    assert (r5.value, r5.n, r5.excluded) == ("1.0000000000", 1, 2)
    ndcg = macro_value(result, "ndcg", 10, "none")
    assert (ndcg.value, ndcg.n, ndcg.excluded) == ("0.6555911179", 1, 2)
    empty = macro_value(result, "correct_empty", None, "strict")
    assert (empty.value, empty.n, empty.excluded) == ("0.5000000000", 2, 1)  # B 1, C 0
    zero = macro_value(result, "zero_result", None, "none")
    assert (zero.value, zero.n) == ("0.3333333333", 3)
    csr = macro_value(result, "csr", 5, "hard")
    assert (csr.value, csr.n, csr.excluded) == ("0.6000000000", 1, 2)


def test_serialization_is_deterministic_and_versioned(result):
    again = evaluate_run(
        "synthetic-system",
        dict(reversed(list(RUN.items()))),
        _finals()[::-1],
        eligibility=_eligibility,
    )
    assert canonical_json(again) == canonical_json(result)
    assert result.config.evaluator_version == "eval-1"
    values = [v.value for q in result.per_query for v in q.values] + [m.value for m in result.macro]
    assert all(v is None or isinstance(v, str) for v in values)


def test_rounding_is_half_even_to_ten_places():
    from fractions import Fraction

    assert fmt(Fraction(1, 3)) == "0.3333333333"
    assert fmt(Fraction(2, 3)) == "0.6666666667"
    assert fmt(Fraction(25, 10**11)) == "0.0000000002"  # ...25 rounds to the even 2
    assert fmt(Fraction(35, 10**11)) == "0.0000000004"  # ...35 rounds to the even 4
    assert fmt(Fraction(0)) == "0.0000000000"
    assert fmt(None) is None
    with pytest.raises(ContractError):
        fmt(float("inf"))


def test_mismatched_or_malformed_runs_are_refused():
    with pytest.raises(ContractError, match="different queries"):
        evaluate_run("s", {A: RUN[A]}, _finals())
    with pytest.raises(ContractError, match="repeats"):
        evaluate_run("s", {**RUN, A: ["P1", "P1"]}, _finals())
    duplicate = [*_finals(), _finals()[0]]
    with pytest.raises(ContractError, match="duplicate"):
        evaluate_run("s", RUN, duplicate)


def test_without_eligibility_no_csr_is_reported():
    result = evaluate_run("s", RUN, _finals())
    assert not any(v.metric.startswith("csr") for q in result.per_query for v in q.values)
