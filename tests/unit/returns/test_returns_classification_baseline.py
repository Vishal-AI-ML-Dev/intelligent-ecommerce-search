"""M19 Part 1: the deterministic baseline on hand-written synthetic inputs.

These inputs check rule behaviour (negation, misleading keywords, Hinglish, Devanagari,
misspellings, empty and insufficient evidence, compatible and conflicting reasons). They are not
a human-reviewed evaluation set, were not taken from the M18 demo dataset or its scenarios, and
say nothing about accuracy."""

import pytest
from pydantic import ValidationError

from ecommerce_search.returns.classification.baseline import classify, match_text
from ecommerce_search.returns.classification.contracts import (
    CategoryPrediction,
    ClassificationInput,
    ClassificationStatus,
    ConfidenceTier,
    InspectionEffect,
    MatchKind,
    Resolution,
    ReviewReason,
    SourceKind,
)
from ecommerce_search.returns.classification.taxonomy import ResponsibleArea, ReturnCategory

C = ReturnCategory
R = ReviewReason
DEFECT = (C.PRODUCT_QUALITY_DEFECT, C.MANUFACTURING_DEFECT)


def run(code="OTHER", *texts, inspections=(), complaints=()):
    statements = [
        {"evidence_type": "return_comment", "evidence_id": f"c{i}", "text": t}
        for i, t in enumerate(texts)
    ] + [
        {
            "evidence_type": "support_complaint",
            "evidence_id": f"k{i}",
            "text": t,
            "customer_topic": topic,
        }
        for i, (t, topic) in enumerate(complaints)
    ]
    return classify(
        ClassificationInput(
            return_id="r1",
            customer_reason_code=code,
            customer_statements=statements,
            inspection_observations=[
                {"observation_id": f"o{i}", "condition": c} for i, c in enumerate(inspections)
            ],
        )
    )


def cats(result):
    return [p.candidates for p in result.predictions]


# ---- reason codes and text -----------------------------------------------------------------


def test_reason_code_alone_gives_a_medium_prediction_citing_the_return():
    result = run("ARRIVED_LATE")
    (prediction,) = result.predictions
    assert prediction.category is C.LOGISTICS_DELAY
    assert prediction.tier is ConfidenceTier.MEDIUM
    assert prediction.suggested_responsible_areas == (ResponsibleArea.LOGISTICS,)
    (ref,) = prediction.supporting
    assert (ref.source_kind, ref.evidence_type, ref.evidence_id) == (
        SourceKind.REASON_CODE,
        "return",
        "r1",
    )
    assert (ref.field, ref.value, ref.rule_id) == (
        "customer_reason_code",
        "ARRIVED_LATE",
        "code:ARRIVED_LATE",
    )
    assert not result.needs_review


@pytest.mark.parametrize("code", ["OTHER", "NOT_PROVIDED"])
def test_other_and_not_provided_are_classified_from_informative_text(code):
    result = run(code, "The shoes are too tight")
    assert cats(result) == [(C.SIZING_FIT,)]
    assert result.status is ClassificationStatus.CLASSIFIED


def test_text_agreeing_with_the_code_is_high_and_cites_both_sources():
    result = run("SIZE_TOO_SMALL", "Too small for me")
    (prediction,) = result.predictions
    assert prediction.tier is ConfidenceTier.HIGH
    kinds = [r.source_kind for r in prediction.supporting]
    assert kinds == [SourceKind.REASON_CODE, SourceKind.CUSTOMER_TEXT]


def test_text_evidence_points_at_the_unchanged_original_substring():
    text = "Delivery was SOOO late... it ARRIVED LATE by a week"
    result = run("OTHER", text)
    ref = result.predictions[0].supporting[0]
    assert ref.match is MatchKind.EXACT
    assert ref.matched_text == "ARRIVED LATE"
    assert text[ref.span[0] : ref.span[1]] == "ARRIVED LATE"


# ---- Hinglish, Devanagari, misspellings ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Shoe size chhota hai", [(C.SIZING_FIT,)]),
        ("bahut dheela hai", [(C.SIZING_FIT,)]),
        ("phone kaam nahi karta", [DEFECT]),
        ("packing kharab thi aur screen toot gayi", [(C.DAMAGED_PRODUCT,)]),
        ("galat saman bheja", [(C.WRONG_ITEM_DELIVERED,)]),
        ("rang alag hai photo se", [(C.COLOUR_IMAGE_MISMATCH,)]),
        ("parcel bahut late aaya", [(C.LOGISTICS_DELAY,)]),
        ("ab nahi chahiye", [(C.CHANGE_OF_MIND,)]),
        ("फ़ोन टूटा हुआ था", [(C.DAMAGED_PRODUCT,)]),
        ("जूता छोटा है", [(C.SIZING_FIT,)]),
    ],
)
def test_hinglish_and_devanagari(text, expected):
    assert cats(run("OTHER", text)) == expected


def test_repeated_letters_are_cut_before_matching():
    assert cats(run("OTHER", "tooo smalll")) == [(C.SIZING_FIT,)]


def test_misspellings_match_but_only_at_low_tier():
    result = run("OTHER", "the screen came damagd")
    (prediction,) = result.predictions
    assert prediction.category is C.DAMAGED_PRODUCT
    assert prediction.supporting[0].match is MatchKind.FUZZY
    assert prediction.supporting[0].matched_text == "damagd"
    assert prediction.tier is ConfidenceTier.LOW
    assert result.review_reasons == (R.LOW_CONFIDENCE,)


def test_a_misspelling_agreeing_with_the_code_is_not_low():
    (prediction,) = run("ARRIVED_DAMAGED", "screen was brokn").predictions
    assert prediction.tier is ConfidenceTier.HIGH


@pytest.mark.parametrize("text", ["refund denied", "the lamp is dimmed", "worked fine"])
def test_lookalike_words_do_not_fuzzy_match(text):
    assert run("OTHER", text).status is ClassificationStatus.ABSTAINED


@pytest.mark.parametrize(
    "text",
    [
        "the parcel arrived brokn",
        "phone screen craked",
        "box was damagd",
        "saman toota... no, samaan brokn aaya",
        "dsplay ok but the charger is brokn",
    ],
)
def test_misspellings_with_product_context_classify(text):
    assert cats(run("OTHER", text)) == [(C.DAMAGED_PRODUCT,)]


@pytest.mark.parametrize(
    "text",
    [
        "my broker said to return it",
        "I am a broker",
        "the broker, not the box, was the issue",  # context is in another clause
        "please ask my broker",
    ],
)
def test_lookalike_words_without_product_context_abstain(text):
    result = run("OTHER", text)
    assert result.status is ClassificationStatus.ABSTAINED
    assert result.review_reasons == (R.INSUFFICIENT_EVIDENCE,)
    (suppressed,) = result.suppressed_matches
    assert suppressed.reason == "fuzzy_without_context"
    assert suppressed.ref.matched_text.casefold() == "broker"


def test_a_fuzzy_match_without_context_never_contradicts():
    result = run("ARRIVED_DAMAGED", "my broker said so")
    (prediction,) = result.predictions
    assert prediction.category is C.DAMAGED_PRODUCT
    assert prediction.contradicting == ()
    assert prediction.tier is ConfidenceTier.MEDIUM
    assert not result.needs_review


def test_an_exact_token_in_the_phrase_is_its_own_context():
    # "late" matched exactly, so the misspelt "delivry" needs no product word.
    assert cats(run("OTHER", "delivry late")) == [(C.LOGISTICS_DELAY,)]


# ---- negation and misleading keywords ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "not damaged at all",
        "There is no damage",
        "without any damage",
        "toota nahi hai",
        "no delay in delivery",
        "it is not too small",
    ],
)
def test_negated_mentions_are_suppressed_not_predicted(text):
    result = run("OTHER", text)
    assert result.status is ClassificationStatus.ABSTAINED
    assert result.review_reasons == (R.INSUFFICIENT_EVIDENCE,)
    (suppressed,) = result.suppressed_matches
    assert suppressed.reason == "negated"


def test_negation_stops_at_a_clause_break():
    assert cats(run("OTHER", "No, it arrived damaged")) == [(C.DAMAGED_PRODUCT,)]
    assert cats(run("OTHER", "not my fault but it is broken")) == [(C.DAMAGED_PRODUCT,)]


def test_negation_inside_another_phrase_does_not_negate_its_neighbour():
    # "nahi chahiye" consumes its "nahi", so "chhota hai" is not negated.
    result = run("OTHER", "size chhota hai nahi chahiye")
    assert C.SIZING_FIT in [p.category for p in result.predictions]


def test_negated_and_affirmed_mentions_in_one_comment():
    result = run("OTHER", "toota nahi hai, bas rang alag hai")
    assert cats(result) == [(C.COLOUR_IMAGE_MISMATCH,)]
    assert result.suppressed_matches[0].ref.matched_text == "toota"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Colour is perfect, but it is too tight", [(C.SIZING_FIT,)]),
        ("I ordered this for my small son, it stopped working", [DEFECT]),
        ("Wrong address was my mistake, the item is fine", []),
        ("The quality is great and it was not late", []),
        ("packing kharab thi", [(C.DAMAGED_PRODUCT,)]),
    ],
)
def test_misleading_keywords(text, expected):
    assert cats(run("OTHER", text)) == expected


# ---- unsupported distinctions --------------------------------------------------------------


def test_a_defect_is_never_split_into_quality_or_manufacturing():
    result = run("DEFECTIVE_OR_NOT_WORKING", "manufacturing defect, stopped working")
    (prediction,) = result.predictions  # one unresolved concern, not two predictions
    assert prediction.resolution is Resolution.UNRESOLVED_CANDIDATES
    assert prediction.candidates == DEFECT
    assert prediction.category is None
    # The customer's words "manufacturing defect" only match the generic "defect" rule.
    text_rules = [r.rule_id for r in prediction.supporting if r.field == "text"]
    assert text_rules == ["text:defect:defect", "text:defect:stopped working"]
    assert all("manufacturing" not in r.rule_id for r in prediction.supporting)
    assert prediction.suggested_responsible_areas == (
        ResponsibleArea.QUALITY,
        ResponsibleArea.SUPPLIER,
    )
    assert R.UNSUPPORTED_DISTINCTION in result.review_reasons


def test_strong_defect_support_still_names_no_single_candidate():
    result = run(
        "DEFECTIVE_OR_NOT_WORKING", "not working", inspections=["functional_fault_confirmed"]
    )
    (prediction,) = result.predictions
    assert prediction.tier is ConfidenceTier.HIGH  # support for "a defect", not for one kind
    assert prediction.resolution is Resolution.UNRESOLVED_CANDIDATES
    assert prediction.category is None
    assert R.UNSUPPORTED_DISTINCTION in result.review_reasons


def test_resolution_category_and_candidates_must_agree():
    base = {
        "tier": "medium",
        "suggested_responsible_areas": ["quality"],
        "supporting": [],
    }
    with pytest.raises(ValidationError):
        CategoryPrediction(
            resolution="single_category",
            candidates=list(DEFECT),
            category="manufacturing_defect",
            **base,
        )
    with pytest.raises(ValidationError):
        CategoryPrediction(
            resolution="unresolved_candidates",
            candidates=list(DEFECT),
            category="manufacturing_defect",
            **base,
        )


def test_an_ambiguous_signal_supports_a_category_named_elsewhere():
    result = run("WRONG_ITEM_RECEIVED", "wrong colour sent")
    (prediction,) = result.predictions
    assert prediction.category is C.WRONG_ITEM_DELIVERED
    assert [r.rule_id for r in prediction.supporting] == [
        "code:WRONG_ITEM_RECEIVED",
        "text:colour_or_wrong_item:wrong colour",
    ]
    assert R.UNSUPPORTED_DISTINCTION not in result.review_reasons


def test_not_as_described_is_unresolved_unless_refined():
    assert cats(run("NOT_AS_DESCRIBED")) == [(C.EXPECTATION_MISMATCH, C.INCORRECT_SPECIFICATION)]
    refined = run("NOT_AS_DESCRIBED", "RAM kam hai")
    assert cats(refined) == [(C.INCORRECT_SPECIFICATION,)]
    assert refined.predictions[0].tier is ConfidenceTier.HIGH


# ---- several reasons and conflicts ---------------------------------------------------------


def test_compatible_reasons_are_kept_without_a_conflict():
    result = run("SIZE_TOO_SMALL", "too small and the delivery was late")
    assert cats(result) == [(C.SIZING_FIT,), (C.LOGISTICS_DELAY,)]
    assert R.CONFLICTING_STATEMENTS not in result.review_reasons


def test_opposite_size_directions_conflict():
    result = run("SIZE_TOO_SMALL", "too big honestly")
    assert cats(result) == [(C.SIZING_FIT,)]
    assert R.CONFLICTING_STATEMENTS in result.review_reasons


@pytest.mark.parametrize(
    ("code", "text", "expected"),
    [
        ("NO_LONGER_NEEDED", "it was broken", [(C.DAMAGED_PRODUCT,), (C.CHANGE_OF_MIND,)]),
        ("NO_LONGER_NEEDED", "stopped working anyway", [DEFECT, (C.CHANGE_OF_MIND,)]),
        (
            "NO_LONGER_NEEDED",
            "galat item bhi aaya",
            [(C.WRONG_ITEM_DELIVERED,), (C.CHANGE_OF_MIND,)],
        ),
        ("ARRIVED_DAMAGED", "ab nahi chahiye", [(C.DAMAGED_PRODUCT,), (C.CHANGE_OF_MIND,)]),
        (
            "WRONG_ITEM_RECEIVED",
            "changed my mind too",
            [(C.WRONG_ITEM_DELIVERED,), (C.CHANGE_OF_MIND,)],
        ),
    ],
)
def test_change_of_mind_coexisting_with_a_fault_is_compatible(code, text, expected):
    result = run(code, text)
    assert cats(result) == expected
    assert R.CONFLICTING_STATEMENTS not in result.review_reasons
    assert all(p.contradicting == () for p in result.predictions)


def test_a_denied_change_of_mind_contradicts_the_code():
    result = run("NO_LONGER_NEEDED", "not a change of mind, it arrived broken")
    mind = next(p for p in result.predictions if p.category is C.CHANGE_OF_MIND)
    assert [r.matched_text for r in mind.contradicting] == ["change of mind"]
    assert mind.tier is ConfidenceTier.LOW
    assert R.CONFLICTING_STATEMENTS in result.review_reasons


def test_a_negated_mention_in_the_same_record_is_not_a_cross_record_conflict():
    result = run("OTHER", "not damaged, it is broken")
    assert cats(result) == [(C.DAMAGED_PRODUCT,)]
    assert R.CONFLICTING_STATEMENTS not in result.review_reasons


def test_a_negated_mention_contradicts_the_same_category_from_another_record():
    result = run("ARRIVED_DAMAGED", "It is not damaged, just too tight")
    damaged = next(p for p in result.predictions if p.category is C.DAMAGED_PRODUCT)
    assert damaged.tier is ConfidenceTier.LOW
    assert [r.matched_text for r in damaged.contradicting] == ["damaged"]
    assert R.CONFLICTING_STATEMENTS in result.review_reasons


# ---- inspections ---------------------------------------------------------------------------


def test_inspection_support_raises_the_tier_and_is_cited():
    result = run("DEFECTIVE_OR_NOT_WORKING", inspections=["functional_fault_confirmed"])
    (prediction,) = result.predictions
    assert prediction.tier is ConfidenceTier.HIGH
    assert prediction.supporting[-1].source_kind is SourceKind.INSPECTION
    assert result.inspection_findings[0].effect is InspectionEffect.SUPPORTS


def test_inspection_contradiction_is_a_conflict_not_a_hard_filter():
    result = run("ARRIVED_DAMAGED", "screen toota hua tha", inspections=["as_new"])
    (prediction,) = result.predictions
    assert prediction.category is C.DAMAGED_PRODUCT  # kept, not removed
    assert prediction.tier is ConfidenceTier.LOW
    assert prediction.contradicting[0].value == "as_new"
    assert R.STATEMENT_INSPECTION_CONFLICT in result.review_reasons


def test_an_inspection_alone_never_creates_a_prediction():
    result = run("NOT_PROVIDED", inspections=["damaged"])
    assert result.status is ClassificationStatus.ABSTAINED
    assert result.inspection_findings[0].effect is InspectionEffect.UNCORROBORATED
    assert result.review_reasons == (R.INSUFFICIENT_EVIDENCE,)


def test_item_difference_without_a_wrong_item_statement_is_routed_without_accusation():
    result = run("SIZE_TOO_SMALL", inspections=["item_differs_from_order"])
    assert cats(result) == [(C.SIZING_FIT,)]
    assert R.UNCORROBORATED_ITEM_DIFFERENCE in result.review_reasons
    assert C.SUSPECTED_ABUSE not in {c for p in result.predictions for c in p.candidates}


def test_item_difference_supports_a_wrong_item_statement():
    result = run("WRONG_ITEM_RECEIVED", inspections=["item_differs_from_order"])
    assert result.predictions[0].tier is ConfidenceTier.HIGH
    assert not result.needs_review


def test_disagreeing_inspections_conflict():
    result = run("ARRIVED_DAMAGED", inspections=["as_new", "damaged"])
    assert R.CONFLICTING_INSPECTIONS in result.review_reasons


def test_neutral_inspections_change_nothing():
    result = run("ARRIVED_LATE", inspections=["not_assessable", "parts_missing"])
    assert [f.effect for f in result.inspection_findings] == [InspectionEffect.NEUTRAL] * 2
    assert result.predictions[0].tier is ConfidenceTier.MEDIUM


# ---- complaints ----------------------------------------------------------------------------


def test_a_complaint_topic_is_a_separate_customer_source():
    result = run("OTHER", complaints=[("please help", "wrong_item")])
    (prediction,) = result.predictions
    assert prediction.category is C.WRONG_ITEM_DELIVERED
    assert prediction.supporting[0].source_kind is SourceKind.COMPLAINT_TOPIC
    assert prediction.supporting[0].evidence_type == "support_complaint"


def test_broad_complaint_topics_do_not_classify():
    assert run("OTHER", complaints=[("please call me", "product_issue")]).status is (
        ClassificationStatus.ABSTAINED
    )


# ---- empty and insufficient evidence -------------------------------------------------------


@pytest.mark.parametrize("texts", [(), ("...",), ("ok",), ("Please process my return",), ("😀😀",)])
def test_insufficient_evidence_abstains(texts):
    result = run("NOT_PROVIDED", *texts)
    assert result.status is ClassificationStatus.ABSTAINED
    assert result.predictions == ()
    assert result.review_reasons == (R.INSUFFICIENT_EVIDENCE,)
    assert result.needs_review


def test_classification_is_deterministic_and_never_changes_the_input():
    inp = ClassificationInput(
        return_id="r1",
        customer_reason_code="OTHER",
        customer_statements=[
            {"evidence_type": "return_comment", "evidence_id": "c1", "text": "  TOOO  Tight!! "}
        ],
    )
    before = inp.model_dump()
    first, second = classify(inp), classify(inp)
    assert first == second
    assert inp.model_dump() == before


def test_match_text_reports_code_point_spans():
    ((phrase, start, end, fuzzy, negated),) = match_text("जूता छोटा है")
    assert (start, end, fuzzy, negated) == (5, 12, False, None)
    assert phrase.group.group_id == "sizing_small"
