"""Deterministic consistency sample, adjudication queue and final labels (synthetic fixtures)."""

import random
from collections import Counter

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.constants import Label
from evals.contracts.models import (
    Confidence,
    ConstraintStatus,
    GoldField,
    IntentFamily,
    ReviewReason,
    Round,
    SampleItem,
    consistency_target,
)
from evals.contracts.review_sets import (
    build_adjudication_queue,
    build_consistency_sample,
    consistency_disagreements,
    derive_final_judgements,
)
from golden_support import T0, eligibility, judgement, query

from ecommerce_search.filtering.policy import FilterOperator

SEAL = "b" * 64
OTHER_SEAL = "c" * 64


def _primaries(spec: dict[str, list[Label]]):
    return [
        judgement(q, f"P{q[-2:]}-{i:03d}", label)
        for q, labels in spec.items()
        for i, label in enumerate(labels)
    ]


def _label_counts(sample, primaries):
    labels = {(j.query_id, j.product_id): j.label for j in primaries}
    return Counter(labels[(i.query_id, i.product_id)] for i in sample.items)


def test_sample_size_is_exactly_ceil_20_percent_overall():
    primaries = _primaries(
        {
            "gq-p01": [Label.RELEVANT] * 5 + [Label.NOT_RELEVANT] * 9,
            "gq-p02": [Label.BORDERLINE] * 3 + [Label.NOT_RELEVANT] * 12,
            "gq-p03": [Label.RELEVANT] * 8,
        }
    )
    sample = build_consistency_sample(primaries, SEAL, T0)
    assert sample.population_count == 37
    assert (
        sample.target_count
        == sample.actual_count
        == len(sample.items)
        == consistency_target(37)
        == 8
    )
    assert sum(c.count for c in sample.allocation) == 8


def test_many_tiny_strata_do_not_inflate_the_rate():
    # 30 strata of one item each: per-stratum ceil(20%) would select all 30.
    primaries = _primaries(
        {
            f"gq-p{q:02d}": [Label.RELEVANT, Label.BORDERLINE, Label.NOT_RELEVANT]
            for q in range(1, 11)
        }
    )
    sample = build_consistency_sample(primaries, SEAL, T0)
    assert sample.population_count == 30 and sample.actual_count == 6
    assert _label_counts(sample, primaries) == {
        Label.RELEVANT: 2,
        Label.BORDERLINE: 2,
        Label.NOT_RELEVANT: 2,
    }
    assert len({i.query_id for i in sample.items}) == 6  # spread across queries


def test_skewed_labels_take_what_exists_and_still_hit_the_target():
    primaries = _primaries(
        {
            "gq-p01": [Label.RELEVANT, Label.BORDERLINE] + [Label.NOT_RELEVANT] * 15,
            "gq-p02": [Label.BORDERLINE] + [Label.NOT_RELEVANT] * 15,
        }
    )
    sample = build_consistency_sample(primaries, SEAL, T0)
    assert sample.actual_count == consistency_target(34) == 7
    assert _label_counts(sample, primaries) == {
        Label.RELEVANT: 1,
        Label.BORDERLINE: 2,
        Label.NOT_RELEVANT: 4,
    }


def test_per_label_spread_is_at_most_one_when_supply_allows():
    primaries = _primaries(
        {
            "gq-p01": [Label.RELEVANT] * 10 + [Label.BORDERLINE] * 10 + [Label.NOT_RELEVANT] * 10,
            "gq-p02": [Label.RELEVANT] * 10 + [Label.BORDERLINE] * 10 + [Label.NOT_RELEVANT] * 11,
        }
    )
    counts = _label_counts(build_consistency_sample(primaries, SEAL, T0), primaries)
    assert max(counts.values()) - min(counts.values()) <= 1
    assert sum(counts.values()) == consistency_target(61) == 13


def test_sample_is_deterministic_and_independent_of_input_order():
    primaries = _primaries({"gq-p01": [Label.RELEVANT] * 7 + [Label.NOT_RELEVANT] * 9})
    shuffled = list(primaries)
    random.Random(7).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle, not crypto
    assert build_consistency_sample(primaries, SEAL, T0) == build_consistency_sample(
        shuffled, SEAL, T0
    )


def test_new_blind_order_depends_on_the_seal_but_selection_does_not():
    primaries = _primaries({"gq-p01": [Label.RELEVANT] * 20 + [Label.NOT_RELEVANT] * 20})
    a = build_consistency_sample(primaries, SEAL, T0)
    b = build_consistency_sample(primaries, OTHER_SEAL, T0)
    assert {(i.query_id, i.product_id) for i in a.items} == {
        (i.query_id, i.product_id) for i in b.items
    }
    assert [i.product_id for i in a.items] != [i.product_id for i in b.items]
    assert [i.blind_position for i in a.items] == list(range(1, 9))


def test_sample_items_carry_no_previous_label():
    assert set(SampleItem.model_fields) == {"query_id", "product_id", "blind_position"}


def test_targeted_review_flags_do_not_change_the_consistency_sample():
    plain = _primaries({"gq-p01": [Label.RELEVANT] * 6 + [Label.NOT_RELEVANT] * 6})
    flagged = [
        j.model_copy(update={"needs_adjudication": True, "catalog_issue": True}) for j in plain
    ]
    sample_plain = build_consistency_sample(plain, SEAL, T0)
    sample_flagged = build_consistency_sample(flagged, SEAL, T0)
    assert sample_plain == sample_flagged
    queue = build_adjudication_queue(flagged, [query("gq-p01", "a query")], [])
    assert len(queue) == 12  # every pair is targeted ...
    assert (
        sample_flagged.population_count == 12 and sample_flagged.actual_count == 3
    )  # ... N, T unchanged


def test_sample_refuses_non_primary_or_duplicate_input():
    with pytest.raises(ContractError, match="expected only primary"):
        build_consistency_sample([judgement("gq-p01", "P1", round_=Round.CONSISTENCY)], SEAL, T0)
    with pytest.raises(ContractError, match="duplicate primary"):
        build_consistency_sample([judgement("gq-p01", "P1"), judgement("gq-p01", "P1")], SEAL, T0)


def test_adjudication_queue_reasons():
    queries = [
        query("gq-p01", "plain query"),
        query("gq-p18", "apple", family=IntentFamily.AMBIGUOUS, interpretations=("a", "b")),
    ]
    primaries = [
        judgement("gq-p01", "P-clean"),
        judgement("gq-p01", "P-low", confidence=Confidence.LOW),
        judgement("gq-p01", "P-flag", needs_adjudication=True, rationale="Unsure."),
        judgement("gq-p01", "P-border", Label.BORDERLINE),
        judgement("gq-p01", "P-unknown", rationale="Attribute missing."),
        judgement("gq-p01", "P-catalog", Label.NOT_RELEVANT, catalog_issue=True),
        judgement("gq-p18", "P-amb", Label.NOT_RELEVANT),
    ]
    elig = [
        eligibility(
            "gq-p01", "P-unknown", [(GoldField.RAM_GB, FilterOperator.EQ, ConstraintStatus.UNKNOWN)]
        )
    ]
    queue = {
        (i.query_id, i.product_id): set(i.review_reasons)
        for i in build_adjudication_queue(primaries, queries, elig)
    }
    assert ("gq-p01", "P-clean") not in queue
    assert queue[("gq-p01", "P-low")] == {ReviewReason.LOW_CONFIDENCE}
    assert queue[("gq-p01", "P-flag")] == {ReviewReason.NEEDS_ADJUDICATION}
    assert queue[("gq-p01", "P-border")] == {ReviewReason.BORDERLINE}
    assert queue[("gq-p01", "P-unknown")] == {ReviewReason.HARD_CONSTRAINT_UNKNOWN}
    assert queue[("gq-p01", "P-catalog")] == {ReviewReason.CATALOG_ISSUE}
    assert queue[("gq-p18", "P-amb")] == {ReviewReason.AMBIGUOUS_QUERY}


def test_disagreements_and_final_derivation():
    primaries = [judgement("gq-p01", "P1"), judgement("gq-p01", "P2", Label.NOT_RELEVANT)]
    repeats = [judgement("gq-p01", "P1", Label.BORDERLINE, round_=Round.CONSISTENCY)]
    assert consistency_disagreements(primaries, repeats) == {("gq-p01", "P1")}
    adjudicated = [
        judgement(
            "gq-p01",
            "P1",
            Label.BORDERLINE,
            round_=Round.ADJUDICATION,
            review_reasons=(ReviewReason.CONSISTENCY_DISAGREEMENT,),
            changes_label=True,
            adjudication_reason="Near-miss substitute.",
        )
    ]
    finals = derive_final_judgements(primaries, adjudicated)
    assert [(f.product_id, f.label, f.decided_in) for f in finals] == [
        ("P1", Label.BORDERLINE, "adjudication"),
        ("P2", Label.NOT_RELEVANT, "primary"),
    ]
    with pytest.raises(ContractError, match="without a primary"):
        consistency_disagreements(primaries, [judgement("gq-p01", "P9", round_=Round.CONSISTENCY)])
