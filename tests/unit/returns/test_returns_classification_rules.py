"""M19 Part 1: taxonomy, rule tables, matching normalization and the classification contracts.

The examples here are hand-written synthetic unit-test inputs. They check rule behaviour; they
are not a human-reviewed evaluation set and say nothing about accuracy."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ecommerce_search.returns.classification import rules
from ecommerce_search.returns.classification import taxonomy as tx
from ecommerce_search.returns.classification.contracts import (
    ClassificationInput,
    ClassificationResult,
    CustomerStatement,
    input_from_records,
)
from ecommerce_search.returns.classification.text import (
    normalize_token,
    token_match,
    tokenize,
    within_one_edit,
)
from ecommerce_search.returns.contracts import (
    ComplaintTopic,
    InspectionCondition,
    InspectionObservation,
    ReasonCode,
    Return,
    ReturnComment,
    SupportComplaint,
)

PACKAGE = Path(tx.__file__).parent
C = tx.ReturnCategory


# ---- taxonomy ------------------------------------------------------------------------------


def test_the_taxonomy_keeps_all_twelve_section_19_categories_unmerged():
    assert [c.value for c in C] == [
        "sizing_fit",
        "expectation_mismatch",
        "colour_image_mismatch",
        "incorrect_specification",
        "product_quality_defect",
        "damaged_product",
        "wrong_item_delivered",
        "logistics_delay",
        "manufacturing_defect",
        "change_of_mind",
        "suspected_abuse",
        "insufficient_evidence",
    ]
    assert set(tx.SUGGESTED_AREAS) == set(C)


def test_suggested_areas_follow_the_section_19_table_cells():
    A = tx.ResponsibleArea
    assert tx.SUGGESTED_AREAS[C.SIZING_FIT] == (A.LISTING, A.PRODUCT)
    assert tx.SUGGESTED_AREAS[C.INCORRECT_SPECIFICATION] == (A.LISTING, A.CATALOG)
    assert tx.SUGGESTED_AREAS[C.DAMAGED_PRODUCT] == (A.PACKAGING, A.LOGISTICS)
    assert tx.SUGGESTED_AREAS[C.WRONG_ITEM_DELIVERED] == (A.FULFILMENT, A.WAREHOUSE)
    assert tx.SUGGESTED_AREAS[C.SUSPECTED_ABUSE] == (A.OPERATIONS_INVESTIGATION,)
    pair = (C.PRODUCT_QUALITY_DEFECT, C.MANUFACTURING_DEFECT)
    assert tx.suggested_areas(pair) == (A.QUALITY, A.SUPPLIER)


def _rule_categories():
    for rule in (*rules.REASON_CODE_MAP.values(), *rules.COMPLAINT_TOPIC_MAP.values()):
        if rule is not None:
            yield from rule.categories
    for rule in rules.INSPECTION_MAP.values():
        yield from rule.supports | rule.contradicts
    for group in rules.KEYWORD_GROUPS:
        yield from group.categories


def test_no_rule_can_produce_suspected_abuse_or_insufficient_evidence():
    assert not set(_rule_categories()) & tx.NOT_PREDICTED_BY_BASELINE


# ---- rule tables ---------------------------------------------------------------------------


def test_every_reason_code_topic_and_condition_has_an_explicit_entry():
    assert set(rules.REASON_CODE_MAP) == set(ReasonCode)
    assert set(rules.COMPLAINT_TOPIC_MAP) == set(ComplaintTopic)
    assert set(rules.INSPECTION_MAP) == set(InspectionCondition)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ReasonCode.SIZE_TOO_SMALL, {C.SIZING_FIT}),
        (ReasonCode.SIZE_TOO_LARGE, {C.SIZING_FIT}),
        (ReasonCode.NOT_AS_DESCRIBED, {C.EXPECTATION_MISMATCH, C.INCORRECT_SPECIFICATION}),
        (ReasonCode.LOOKS_DIFFERENT_FROM_IMAGE, {C.COLOUR_IMAGE_MISMATCH}),
        (ReasonCode.SPECIFICATION_DIFFERENT, {C.INCORRECT_SPECIFICATION}),
        (ReasonCode.DEFECTIVE_OR_NOT_WORKING, {C.PRODUCT_QUALITY_DEFECT, C.MANUFACTURING_DEFECT}),
        (ReasonCode.ARRIVED_DAMAGED, {C.DAMAGED_PRODUCT}),
        (ReasonCode.WRONG_ITEM_RECEIVED, {C.WRONG_ITEM_DELIVERED}),
        (ReasonCode.ARRIVED_LATE, {C.LOGISTICS_DELAY}),
        (ReasonCode.NO_LONGER_NEEDED, {C.CHANGE_OF_MIND}),
    ],
)
def test_reason_code_map(code, expected):
    assert rules.REASON_CODE_MAP[code].categories == expected


def test_other_and_not_provided_map_to_nothing():
    assert rules.REASON_CODE_MAP[ReasonCode.OTHER] is None
    assert rules.REASON_CODE_MAP[ReasonCode.NOT_PROVIDED] is None


def test_inspections_never_name_a_category_without_a_customer_statement_rule():
    statement = set()
    for rule in rules.REASON_CODE_MAP.values():
        if rule is not None:
            statement |= rule.categories
    for rule in rules.INSPECTION_MAP.values():
        assert (rule.supports | rule.contradicts) <= statement


def test_rule_ids_are_unique_and_phrases_are_stored_normalized():
    ids = [p.rule_id for p in rules.PHRASES]
    assert len(ids) == len(set(ids))
    for phrase in rules.PHRASES:
        assert phrase.tokens and all(normalize_token(t) == t for t in phrase.tokens)


def test_misleading_lone_words_are_not_rules():
    singles = {p.tokens[0] for p in rules.PHRASES if len(p.tokens) == 1}
    assert not singles & {"small", "big", "colour", "color", "wrong", "late", "size", "quality"}


def test_phrases_that_contain_negation_are_not_negatable():
    for phrase in rules.PHRASES:
        if set(phrase.tokens) & rules.NEGATORS:
            assert not phrase.group.negatable, phrase.rule_id


def test_fuzzy_allowlist_is_bounded():
    for token in rules.FUZZY_TOKENS:
        assert token.isascii() and token.isalpha() and len(token) >= 6
        assert normalize_token(token) == token
    lexicon = {t for p in rules.PHRASES for t in p.tokens}
    assert lexicon >= rules.FUZZY_TOKENS
    assert not rules.FUZZY_TOKENS & rules.NEGATORS


def test_the_classifier_never_reads_generator_scenarios_or_the_seed_dataset():
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "scenarios.json" not in source, path.name
        assert "data/seed" not in source and "returns_v1" not in source, path.name


# ---- matching normalization ----------------------------------------------------------------


def test_tokens_keep_original_offsets_and_normalize_a_copy():
    text = "Doesn't FIT, tooo smalll"
    tokens = tokenize(text)
    assert [t.norm for t in tokens] == ["doesnt", "fit", "too", "small"]
    assert [text[t.start : t.end] for t in tokens] == ["Doesn't", "FIT", "tooo", "smalll"]


def test_curly_apostrophes_and_dandas_are_handled():
    assert [t.norm for t in tokenize("Doesn’t fit")] == ["doesnt", "fit"]
    assert "।" in rules.CLAUSE_BREAK_CHARS


def test_devanagari_words_stay_whole():
    assert [t.norm for t in tokenize("फ़ोन टूटा हुआ")] == ["फ़ोन", "टूटा", "हुआ"]


def test_within_one_edit():
    assert within_one_edit("damaged", "damagd")
    assert within_one_edit("colour", "colur")
    assert within_one_edit("broken", "brokem")
    assert not within_one_edit("damaged", "dmgd")


def test_fuzzy_match_needs_permission_same_first_letter_and_length():
    assert token_match("damaged", "damagd", fuzzy=True) == "fuzzy"
    assert token_match("damaged", "damagd", fuzzy=False) is None
    assert token_match("damaged", "lamaged", fuzzy=True) is None
    assert token_match("toota", "tota", fuzzy=True) is None  # short and not ASCII-allowlisted
    assert token_match("damaged", "damaged", fuzzy=False) == "exact"


# ---- contracts -----------------------------------------------------------------------------


def test_input_rejects_empty_text_unknown_fields_and_duplicate_ids():
    with pytest.raises(ValidationError):
        CustomerStatement(evidence_type="return_comment", evidence_id="c1", text="   ")
    with pytest.raises(ValidationError):
        ClassificationInput(return_id="r1", customer_reason_code="OTHER", refund="yes")
    statement = {"evidence_type": "return_comment", "evidence_id": "c1", "text": "x"}
    with pytest.raises(ValidationError):
        ClassificationInput(
            return_id="r1", customer_reason_code="OTHER", customer_statements=[statement] * 2
        )


def test_topic_is_only_allowed_on_support_complaints():
    with pytest.raises(ValidationError):
        CustomerStatement(
            evidence_type="return_comment", evidence_id="c1", text="x", customer_topic="damaged"
        )
    with pytest.raises(ValidationError):
        CustomerStatement(evidence_type="customer_review", evidence_id="v1", text="x")


def test_result_has_no_decision_fault_or_abuse_fields():
    fields = set(ClassificationResult.model_fields)
    for forbidden in ("refund", "fault", "abuse", "root_cause", "action", "decision", "seller"):
        assert not any(forbidden in f for f in fields)


TS = "2026-10-01T10:00:00Z"


def test_input_from_m18_records_keeps_statements_and_inspections_apart():
    record = Return(
        return_id="r1",
        order_item_id="oi1",
        requested_at=TS,
        quantity=1,
        customer_reason_code="OTHER",
        status="received",
        received_at=TS,
        is_synthetic=True,
    )
    comment = ReturnComment(
        comment_id="c1", return_id="r1", submitted_at=TS, text="  Toota hua  ", is_synthetic=True
    )
    complaint = SupportComplaint(
        complaint_id="k1",
        product_id="p1",
        opened_at=TS,
        channel="chat",
        customer_topic="damaged",
        text="box damaged",
        is_synthetic=True,
    )
    observation = InspectionObservation(
        observation_id="o1",
        return_id="r1",
        observed_at=TS,
        inspector_ref="SYN-INSP-00001",
        condition="damaged",
        note="dent on lid",
        is_synthetic=True,
    )
    built = input_from_records(record, [comment], [observation], [complaint])
    assert [s.text for s in built.customer_statements] == ["  Toota hua  ", "box damaged"]
    assert built.customer_statements[1].customer_topic is ComplaintTopic.DAMAGED
    assert [o.condition for o in built.inspection_observations] == [InspectionCondition.DAMAGED]
    other = comment.model_copy(update={"return_id": "r2"})
    with pytest.raises(ValueError):
        input_from_records(record, [other])
