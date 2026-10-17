"""Single-record contract rules (synthetic fixtures only)."""

from datetime import datetime, timedelta, timezone

import pytest
from evals.contracts.constants import (
    ALLOWED_RELATIONS,
    CONSISTENCY_STATISTIC_NAME,
    DISCLOSURE_SINGLE_ANNOTATOR,
    ELIGIBILITY_COMPLETION_CAP,
    LENIENT_RELEVANT,
    MAX_POOL_SIZE_PER_QUERY,
    MIN_CONSISTENCY_DELAY,
    NDCG_GAINS,
    NEGATIVES_PER_QUERY,
    POOL_DEPTH,
    STRICT_RELEVANT,
    Label,
    Relation,
)
from evals.contracts.models import (
    FP1_GOLD_FIELDS,
    AllocationCell,
    Confidence,
    ConsistencySample,
    ConstraintStatus,
    EligibilityRecord,
    GoldConstraint,
    GoldField,
    IntentFamily,
    Judgement,
    LabelChange,
    LabelChangeKind,
    OverallStatus,
    Partition,
    PartitionAssignment,
    ProductIntent,
    ProductIntentKind,
    ProvenanceEntry,
    QueueItem,
    ReviewReason,
    Round,
    SampleItem,
    SourceHit,
    SourceSystem,
    consistency_target,
    overall_status,
)
from golden_support import HANDLE, T0, eligibility, gold, judgement, query
from pydantic import ValidationError

from ecommerce_search.filtering.policy import FilterField, FilterOperator

SHA = "a" * 64

# ---- approved constants ------------------------------------------------------------------------


def test_approved_constants():
    assert (POOL_DEPTH, ELIGIBILITY_COMPLETION_CAP, NEGATIVES_PER_QUERY) == (20, 25, 4)
    assert MAX_POOL_SIZE_PER_QUERY == 60
    assert timedelta(hours=72) == MIN_CONSISTENCY_DELAY
    assert dict(NDCG_GAINS) == {Label.RELEVANT: 2, Label.BORDERLINE: 1, Label.NOT_RELEVANT: 0}
    assert {Label.RELEVANT} == STRICT_RELEVANT
    assert {Label.RELEVANT, Label.BORDERLINE} == LENIENT_RELEVANT
    assert DISCLOSURE_SINGLE_ANNOTATOR == (
        "The Golden Dataset was reviewed by one human annotator; "
        "no independent inter-annotator agreement is claimed."
    )
    assert CONSISTENCY_STATISTIC_NAME == "intra_annotator_consistency"
    assert "inter" not in CONSISTENCY_STATISTIC_NAME.replace("intra", "")


def test_relation_facet_partitions_the_relations():
    seen = [r for relations in ALLOWED_RELATIONS.values() for r in relations]
    assert sorted(seen) == sorted(Relation)
    assert Relation.COMPLEMENT in ALLOWED_RELATIONS[Label.NOT_RELEVANT]  # D3


def test_review_1_rounds_are_unchanged_and_review_2_adds_only_the_second_review():
    # review-1 (D5) had no second-reviewer round; the approved review-2 amendment adds exactly
    # one, kept distinct from the intra-annotator consistency round.
    assert {r.value for r in Round} == {"primary", "consistency", "adjudication", "second_review"}
    assert Round.SECOND_REVIEW is not Round.CONSISTENCY


# ---- queries and partitions --------------------------------------------------------------------


def test_query_text_must_be_whitespace_normalized_and_acceptable():
    assert query("gq-p01", "dell laptop").text == "dell laptop"
    with pytest.raises(ValidationError, match="whitespace-normalized"):
        query("gq-p01", "dell  laptop")
    with pytest.raises(ValidationError, match="not acceptable"):
        query("gq-p01", "dell\u200blaptop")  # zero-width space (Cf) is rejected by normalize_query
    with pytest.raises(ValidationError, match="not acceptable"):
        query("gq-p01", "x" * 201)


def test_query_id_prefix_must_match_partition():
    with pytest.raises(ValidationError, match="does not match partition"):
        query("gq-p01", "a b", partition=Partition.TEST)
    with pytest.raises(ValidationError, match="does not match partition"):
        PartitionAssignment(query_id="gq-t01", partition=Partition.PILOT_DEV)
    with pytest.raises(ValidationError, match="pattern"):
        query("q-1", "a b")


def test_seen_in_development_never_enters_test():  # D13
    assert query("gq-p10", "seen query", seen=True).seen_in_development
    with pytest.raises(ValidationError, match="seen in development"):
        query("gq-t01", "seen query", seen=True)


def test_ambiguous_queries_need_interpretations():
    with pytest.raises(ValidationError, match="at least two interpretations"):
        query("gq-p18", "apple", family=IntentFamily.AMBIGUOUS, interpretations=("Apple laptop",))
    ok = query(
        "gq-p18",
        "apple",
        family=IntentFamily.AMBIGUOUS,
        interpretations=("Apple laptop", "Apple phone"),
    )
    assert len(ok.interpretations) == 2


# ---- gold constraints and product intents -------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "operator"),
    [
        (GoldField.CATEGORY, "laptop", FilterOperator.EQ),
        (GoldField.BRAND, "boAt", FilterOperator.EQ),
        (GoldField.RAM_GB, 16, FilterOperator.EQ),
        (GoldField.STORAGE_GB, 1024, FilterOperator.EQ),
        (GoldField.STORAGE_TYPE, "SSD", FilterOperator.EQ),
        (GoldField.STORAGE_INTERFACE, "NVME", FilterOperator.EQ),
        (GoldField.PRICE, "40000.00", FilterOperator.LTE),
        (GoldField.PRICE, "20000.00", FilterOperator.GTE),
        (GoldField.PROCESSOR, "AMD Ryzen 7", FilterOperator.EQ),
        (GoldField.SUBCATEGORY, "running", FilterOperator.EQ),
        (GoldField.GENDER, "women", FilterOperator.EQ),
        (GoldField.ANC, True, FilterOperator.EQ),
        (GoldField.WIRELESS, False, FilterOperator.EQ),
        (GoldField.CONNECTIVITY, "bluetooth", FilterOperator.EQ),
    ],
)
def test_valid_gold_values(field, value, operator):
    assert gold("gq-p01", field, value, operator=operator).value == value


@pytest.mark.parametrize(
    ("field", "value", "operator", "message"),
    [
        (GoldField.CATEGORY, "smartwatch", FilterOperator.EQ, "not allowed"),
        (GoldField.BRAND, "hp", FilterOperator.EQ, "not allowed"),  # canonical spelling only
        (GoldField.RAM_GB, True, FilterOperator.EQ, "positive integer"),
        (GoldField.RAM_GB, "16", FilterOperator.EQ, "positive integer"),
        (GoldField.RAM_GB, 0, FilterOperator.EQ, "positive integer"),
        (GoldField.PRICE, "40000", FilterOperator.LTE, "2 places"),
        (GoldField.PRICE, 40000, FilterOperator.LTE, "2 places"),
        (GoldField.PRICE, "40000.00", FilterOperator.EQ, "lte or gte"),
        (GoldField.RAM_GB, 16, FilterOperator.LTE, "eq operator"),
        (GoldField.ANC, "true", FilterOperator.EQ, "boolean"),
        (GoldField.PROCESSOR, " i7", FilterOperator.EQ, "trimmed"),
    ],
)
def test_invalid_gold_values(field, value, operator, message):
    with pytest.raises(ValidationError, match=message):
        gold("gq-p01", field, value, operator=operator)


def test_fp1_flag_is_validated_and_in_sync_with_fp1():  # D7
    fp1 = {f.value for f in FilterField} - {"min_price", "max_price"} | {"price"}
    assert {f.value for f in FP1_GOLD_FIELDS} == fp1
    with pytest.raises(ValidationError, match="fp1_filterable must be False"):
        GoldConstraint(
            query_id="gq-p01",
            field=GoldField.ANC,
            operator=FilterOperator.EQ,
            value=True,
            hardness="hard",
            fp1_filterable=True,
            annotator_handle=HANDLE,
            recorded_at=T0,
        )


def test_handles_are_pseudonymous():  # D11
    for bad in ("Vishal", "ann-0", "someone@example.com", "ann-1 "):
        with pytest.raises(ValidationError):
            GoldConstraint(
                query_id="gq-p01",
                field=GoldField.CATEGORY,
                operator=FilterOperator.EQ,
                value="laptop",
                hardness="hard",
                fp1_filterable=True,
                annotator_handle=bad,
                recorded_at=T0,
            )


def test_timestamps_must_be_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    with pytest.raises(ValidationError, match="UTC"):
        judgement("gq-p01", "SYN-LAP-0001", at=datetime(2026, 10, 10, 12, tzinfo=ist))
    with pytest.raises(ValidationError, match="UTC"):
        judgement("gq-p01", "SYN-LAP-0001", at=datetime(2026, 10, 10, 12))  # noqa: DTZ001


def test_product_intent_is_soft_and_separate():  # D14
    intent = ProductIntent(
        query_id="gq-p04",
        kind=ProductIntentKind.SERIES,
        value="Lumen",
        annotator_handle=HANDLE,
        recorded_at=T0,
    )
    assert intent.hardness == "soft" and intent.evidence == "title_token"
    with pytest.raises(ValidationError):
        ProductIntent(
            query_id="gq-p04",
            kind=ProductIntentKind.SERIES,
            value="Lumen",
            hardness="hard",
            annotator_handle=HANDLE,
            recorded_at=T0,
        )


# ---- provenance and eligibility ---------------------------------------------------------------


def test_provenance_inclusion_rules():
    hit = SourceHit(system=SourceSystem.V1_HYBRID, rank=7, score="0.0198")
    assert ProvenanceEntry(
        query_id="gq-p01", product_id="P1", added_by="system_union", sources=(hit,)
    )
    with pytest.raises(ValidationError, match="system_union"):
        ProvenanceEntry(
            query_id="gq-p01", product_id="P1", added_by="eligibility_completion", sources=(hit,)
        )
    deep = SourceHit(system=SourceSystem.DENSE_ONLY, rank=35)
    assert ProvenanceEntry(
        query_id="gq-p01", product_id="P1", added_by="eligibility_completion", sources=(deep,)
    )
    with pytest.raises(ValidationError, match="needs a source rank"):
        ProvenanceEntry(
            query_id="gq-p01", product_id="P1", added_by="system_union", sources=(deep,)
        )
    unordered = (SourceHit(system=SourceSystem.V2_FILTERED, rank=1), hit)
    with pytest.raises(ValidationError, match="fixed system order"):
        ProvenanceEntry(
            query_id="gq-p01", product_id="P1", added_by="system_union", sources=unordered
        )
    with pytest.raises(ValidationError):
        SourceHit(system=SourceSystem.V0_LEXICAL, rank=1, score="nan")


def test_overall_status_rules():
    s = ConstraintStatus
    assert overall_status([]) is OverallStatus.NO_CONSTRAINTS
    assert overall_status([s.SATISFIED, s.UNKNOWN]) is OverallStatus.UNKNOWN
    assert overall_status([s.UNKNOWN, s.VIOLATED]) is OverallStatus.VIOLATED
    assert overall_status([s.SATISFIED, s.NOT_APPLICABLE]) is OverallStatus.VIOLATED
    assert overall_status([s.SATISFIED]) is OverallStatus.SATISFIED


def test_eligibility_overall_must_follow_checks_and_fp1_separately():
    record = eligibility(
        "gq-p12",
        "SYN-SHO-0002",
        [
            (GoldField.CATEGORY, FilterOperator.EQ, ConstraintStatus.SATISFIED),
            (GoldField.GENDER, FilterOperator.EQ, ConstraintStatus.VIOLATED),
        ],
    )
    assert record.hard_overall is OverallStatus.VIOLATED
    assert record.fp1_overall is OverallStatus.SATISFIED  # gender is not an fp-1 field
    with pytest.raises(ValidationError, match="hard_overall"):
        EligibilityRecord(**{**record.model_dump(), "hard_overall": "satisfied"})


# ---- judgements and label changes --------------------------------------------------------------


def test_relation_must_fit_label():
    with pytest.raises(ValidationError, match="not allowed"):
        judgement("gq-p01", "P1", Label.RELEVANT, relation=Relation.SUBSTITUTE)
    assert judgement("gq-p01", "P1", Label.NOT_RELEVANT, relation=Relation.COMPLEMENT)


def test_rationale_and_free_text_rules():
    base = judgement("gq-p01", "P1", Label.RELEVANT).model_dump()
    with pytest.raises(ValidationError, match="rationale is required"):
        Judgement(**{**base, "label": "borderline", "relation": "partial"})
    with pytest.raises(ValidationError, match="rationale is required"):
        Judgement(**{**base, "confidence": "low"})
    with pytest.raises(ValidationError, match="email"):
        Judgement(**{**base, "rationale": "ask me at x@example.com"})
    with pytest.raises(ValidationError, match="machine-specific"):
        Judgement(**{**base, "rationale": r"see C:\Users\someone\notes.txt"})


def test_adjudication_fields_only_in_adjudication():
    with pytest.raises(ValidationError, match="only allowed in the adjudication round"):
        judgement("gq-p01", "P1", review_reasons=(ReviewReason.BORDERLINE,))
    with pytest.raises(ValidationError, match="at least one review reason"):
        judgement("gq-p01", "P1", round_=Round.ADJUDICATION, changes_label=False)
    with pytest.raises(ValidationError, match="adjudication_reason"):
        judgement(
            "gq-p01",
            "P1",
            round_=Round.ADJUDICATION,
            review_reasons=(ReviewReason.BORDERLINE,),
            changes_label=True,
        )
    with pytest.raises(ValidationError, match="unique and sorted"):
        judgement(
            "gq-p01",
            "P1",
            round_=Round.ADJUDICATION,
            review_reasons=(ReviewReason.LOW_CONFIDENCE, ReviewReason.BORDERLINE),
            changes_label=False,
        )
    ok = judgement(
        "gq-p01",
        "P1",
        Label.NOT_RELEVANT,
        round_=Round.ADJUDICATION,
        review_reasons=(ReviewReason.BORDERLINE,),
        changes_label=True,
        adjudication_reason="Violates the stated price bound.",
        confidence=Confidence.MEDIUM,
    )
    assert ok.changes_label


def test_judgement_has_no_ai_label_source_field():
    assert not {"source", "model", "suggested_by", "ai_label"} & set(Judgement.model_fields)
    with pytest.raises(ValidationError):
        Judgement(**{**judgement("gq-p01", "P1").model_dump(), "source": "ai"})


def test_label_change_rules():
    base = dict(
        query_id="gq-p01",
        product_id="P1",
        reason="Adjudicated.",
        changed_by=HANDLE,
        changed_at=T0,
    )
    assert LabelChange(
        **base,
        kind=LabelChangeKind.ADJUDICATION,
        previous_label="relevant",
        new_label="borderline",
        from_golden_version=1,
        to_golden_version=1,
    )
    with pytest.raises(ValidationError, match="must change the label"):
        LabelChange(
            **base,
            kind=LabelChangeKind.ADJUDICATION,
            previous_label="relevant",
            new_label="relevant",
            from_golden_version=1,
            to_golden_version=1,
        )
    with pytest.raises(ValidationError, match="next golden version"):
        LabelChange(
            **base,
            kind=LabelChangeKind.POST_FREEZE_CORRECTION,
            previous_label="relevant",
            new_label="borderline",
            from_golden_version=1,
            to_golden_version=1,
        )


# ---- generated review sets -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "t"), [(0, 0), (1, 1), (5, 1), (6, 2), (10, 2), (11, 3), (1000, 200)]
)
def test_consistency_target_is_ceil_20_percent(n, t):
    assert consistency_target(n) == t


def test_consistency_sample_counts_must_agree():
    cell = AllocationCell(label=Label.RELEVANT, query_id="gq-p01", count=1)
    item = SampleItem(query_id="gq-p01", product_id="P1", blind_position=1)
    common = dict(primary_seal_sha256=SHA, primary_sealed_at=T0, allocation=(cell,), items=(item,))
    assert ConsistencySample(population_count=5, target_count=1, actual_count=1, **common)
    with pytest.raises(ValidationError, match="ceil"):
        ConsistencySample(population_count=5, target_count=2, actual_count=1, **common)
    with pytest.raises(ValidationError, match="exactly target_count"):
        ConsistencySample(population_count=6, target_count=2, actual_count=2, **common)


def test_queue_items_are_targeted_review_and_never_disagreements():
    item = QueueItem(query_id="gq-p01", product_id="P1", review_reasons=(ReviewReason.BORDERLINE,))
    assert item.selection == "targeted_review"
    with pytest.raises(ValidationError, match="not known"):
        QueueItem(
            query_id="gq-p01",
            product_id="P1",
            review_reasons=(ReviewReason.CONSISTENCY_DISAGREEMENT,),
        )
    with pytest.raises(ValidationError, match="non-empty"):
        QueueItem(query_id="gq-p01", product_id="P1", review_reasons=())
