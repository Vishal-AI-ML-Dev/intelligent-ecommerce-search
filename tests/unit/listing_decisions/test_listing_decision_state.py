"""M14-1 jl-1 question set and state builder: determinism, ordering, cap, omissions, no input
mutation, no target metadata in model-visible inputs, and whole (untruncated) seller text."""

import copy
import json

import pytest
from listing_decision_support import LAPTOP, built, raw_of, state_of

from ecommerce_search.decision import listing_questions as lq
from ecommerce_search.decision.listing_models import (
    ATTRIBUTE_ALLOWLIST,
    CLAIMS_INPUT,
    MAX_ATTRIBUTE_QUESTIONS,
    TEXT_INPUT,
    OmissionReason,
    TextStatus,
)
from ecommerce_search.normalization.normalizer import normalize_listing

# Pinned: any change to jl-1 wording, labels, allowlist, cap or ordering must be a deliberate
# question-set version decision.
JL1_DIGEST = "89bf43fed5799c1b8adbaa67ed30566a442fff1721aac4ea8c9fbea135724390"

MANY = {
    "seller_id": "s",
    "seller_listing_ref": "many",
    "title": "Everything listing",
    "brand": "Sony",
    "color": "Black",
    "material": "Plastic",
    "processor": "Snapdragon",
    "gpu": "Adreno",
    "operating_system": "Android",
    "storage_type": "SATA SSD",  # two candidates: storage_type and storage_interface
    "connectivity": "bluetooth",
    "wireless": True,
    "anc": True,
    "gender": "unisex",
}


def test_jl1_question_set_digest_is_pinned():
    assert lq.question_set_digest() == JL1_DIGEST
    assert state_of().question_set_digest == JL1_DIGEST


def test_same_input_gives_the_same_state_and_hash():
    first, second = state_of(), state_of()
    assert first == second
    assert first.state_sha256() == second.state_sha256()
    assert first.canonical_json() == second.canonical_json()
    assert state_of(LAPTOP | {"color": "Black"}).state_sha256() != first.state_sha256()


def test_key_order_changes_the_raw_hash_and_therefore_the_state_hash():
    reordered = dict(reversed(list(LAPTOP.items())))
    a, b = state_of(), state_of(reordered)
    assert a.m13.output_sha256 == b.m13.output_sha256
    assert a.attribute_questions == b.attribute_questions
    assert a.state_sha256() != b.state_sha256()  # the raw bytes differ


def test_questions_follow_ascending_candidate_ordinals_with_explicit_omissions():
    raw, n = built()
    s = lq.build_listing_state(raw, n)
    asked = s.question_ordinals()
    assert asked == tuple(sorted(asked))
    assert [q.claim.field for q in s.attribute_questions] == [
        "brand",
        "color",
        "processor",
        "storage_interface",
        "storage_type",
    ]
    reasons = {(o.field, o.reason) for o in s.omitted_candidates}
    assert reasons == {
        ("camera", OmissionReason.NOT_ALLOWLISTED),
        ("category", OmissionReason.NOT_ALLOWLISTED),
        ("description", OmissionReason.NOT_ALLOWLISTED),
        ("price", OmissionReason.NUMERIC_FIELD),
        ("ram_gb", OmissionReason.NUMERIC_FIELD),
        ("ram_gb", OmissionReason.TEXT_EXTRACTION),
        ("storage_gb", OmissionReason.TEXT_EXTRACTION),
        ("storage_interface", OmissionReason.TEXT_EXTRACTION),
        ("storage_type", OmissionReason.TEXT_EXTRACTION),
        ("subcategory", OmissionReason.NOT_ALLOWLISTED),
        ("title", OmissionReason.NOT_ALLOWLISTED),
    }
    for o in s.omitted_candidates:
        assert n.candidates[o.ordinal].field == o.field
    ids = [q.question_id for q in lq.questions(s)]
    assert ids == ["category", "subcategory", *(f"attribute_support[{o}]" for o in asked)]


def test_unnormalized_candidates_are_omitted_not_asked():
    s = state_of(LAPTOP | {"brand": "UnknownBrandXYZ", "storage_type": "NVMe"})
    omitted = {(o.field, o.reason) for o in s.omitted_candidates}
    assert ("brand", OmissionReason.NOT_NORMALIZED) in omitted
    assert ("storage_type", OmissionReason.NOT_NORMALIZED) in omitted
    assert {q.claim.field for q in s.attribute_questions} == {"color", "processor"}


def test_question_cap_keeps_the_lowest_ordinals_and_lists_the_rest_as_capped():
    raw, n = built(MANY)
    s = lq.build_listing_state(raw, n)
    eligible = [
        i
        for i, c in enumerate(n.candidates)
        if c.field in ATTRIBUTE_ALLOWLIST
        and c.outcome.value == "normalized"
        and c.basis.value == "seller_field"
    ]
    assert len(eligible) == 12 > MAX_ATTRIBUTE_QUESTIONS
    assert s.question_ordinals() == tuple(eligible[:MAX_ATTRIBUTE_QUESTIONS])
    capped = [o.ordinal for o in s.omitted_candidates if o.reason is OmissionReason.QUESTION_CAP]
    assert capped == eligible[MAX_ATTRIBUTE_QUESTIONS:]
    assert set(capped).isdisjoint(s.question_ordinals())
    assert len(lq.questions(s)) == 2 + MAX_ATTRIBUTE_QUESTIONS


def test_building_does_not_mutate_the_raw_listing_or_the_normalization():
    raw, n = built()
    raw_before = (raw.raw_bytes, raw.raw_sha256, copy.deepcopy(raw.fields), raw.identity)
    n_before = (n.model_dump(), n.output_sha256())
    s = lq.build_listing_state(raw, n)
    lq.model_inputs(s)
    lq.questions(s)
    lq.verify_listing_state(s, raw, n)
    assert (raw.raw_bytes, raw.raw_sha256, raw.fields, raw.identity) == raw_before
    assert (n.model_dump(), n.output_sha256()) == n_before


def test_model_inputs_carry_no_seller_category_or_target_metadata():
    s = state_of()
    inputs = lq.model_inputs(s)
    assert set(inputs) == {TEXT_INPUT, CLAIMS_INPUT}
    assert inputs[TEXT_INPUT] == {"title": LAPTOP["title"], "description": LAPTOP["description"]}
    assert set(inputs[CLAIMS_INPUT]) == {"title", "description", "claims"}
    claim_fields = {c["field"] for c in inputs[CLAIMS_INPUT]["claims"].values()}
    assert claim_fields.isdisjoint({"category", "subcategory"})
    dumped = json.dumps(inputs, ensure_ascii=False)
    for target in (
        "business",
        "TARGET",
        "Occasion",
        "product_category_tree",
        "seller-1",
        "ref-1",
        "40,000",
    ):
        assert target not in dumped
    for spec in lq.questions(s):
        assert spec.input_id in inputs


def test_target_words_naturally_in_the_title_are_kept_as_evidence():
    s = state_of(LAPTOP | {"title": "Lenovo IdeaPad business laptop"})
    assert lq.model_inputs(s)[TEXT_INPUT]["title"] == "Lenovo IdeaPad business laptop"


def test_instructions_are_fixed_text_never_seller_data():
    a, b = state_of(), state_of(MANY)
    texts = {spec.question_id: spec.instructions for spec in lq.questions(a)}
    texts_b = {spec.question_id: spec.instructions for spec in lq.questions(b)}
    assert texts["category"] == texts_b["category"] == lq.CATEGORY_INSTRUCTIONS
    assert texts["subcategory"] == texts_b["subcategory"] == lq.SUBCATEGORY_INSTRUCTIONS
    # distinctive seller strings (short ids such as "s" occur in any English text)
    seller_values = [
        v for v in (*LAPTOP.values(), *MANY.values()) if isinstance(v, str) and len(v) > 4
    ]
    for spec in (*lq.questions(a), *lq.questions(b)):
        assert spec.instructions.startswith("The state is a seller's product listing.")
        for value in seller_values:
            assert value not in spec.instructions


def test_instruction_like_seller_text_stays_quoted_data():
    s = state_of()
    inputs = lq.model_inputs(s)
    assert inputs[TEXT_INPUT]["description"] == LAPTOP["description"]  # unchanged, as a value
    encoded = json.dumps(inputs[TEXT_INPUT])
    assert json.loads(encoded) == inputs[TEXT_INPUT]


def test_long_text_is_passed_whole_never_truncated():
    description = ("Lightweight laptop with backlit keyboard. " * 1400).strip()
    assert len(description) > 50_000
    s = state_of(LAPTOP | {"description": description})
    assert s.text.description == description
    assert lq.model_inputs(s)[TEXT_INPUT]["description"] == description


def test_title_and_description_keep_exact_raw_text_and_report_missing_states():
    raw_title = "  HP   Pavilion  "
    s = state_of(LAPTOP | {"title": raw_title})
    assert s.text.title == raw_title  # not whitespace-collapsed
    cases = {
        TextStatus.ABSENT: {k: v for k, v in LAPTOP.items() if k != "description"},
        TextStatus.EMPTY: LAPTOP | {"description": "   "},
        TextStatus.NOT_TEXT: LAPTOP | {"description": 12},
    }
    for status, fields in cases.items():
        s = state_of(fields)
        assert (s.text.description, s.text.description_status) == (None, status)
        assert lq.model_inputs(s)[TEXT_INPUT]["description"] is None


def test_the_title_key_is_matched_like_m13_case_insensitively():
    fields = {k: v for k, v in LAPTOP.items() if k != "title"} | {" TITLE ": "Case Key Title"}
    s = state_of(fields)
    assert (s.text.title, s.text.title_status) == ("Case Key Title", TextStatus.PRESENT)


def test_support_criteria_never_turn_missing_text_into_a_contradiction():
    assert "not a contradiction" in lq.SUPPORT_CRITERIA["not_stated"]
    assert set(lq.SUPPORT_CRITERIA) == {"supported", "contradicted", "not_stated"}


def test_category_none_covers_accessories():
    assert "accessory" in lq.CATEGORY_CRITERIA["none"]


@pytest.mark.parametrize("fields", [LAPTOP, MANY])
def test_state_round_trips_through_json(fields):
    from ecommerce_search.decision.listing_models import ListingDecisionState

    s = state_of(fields)
    again = ListingDecisionState.model_validate_json(s.model_dump_json())
    assert again == s and again.state_sha256() == s.state_sha256()


def test_normalizing_the_same_bytes_twice_gives_the_same_state():
    raw = raw_of(LAPTOP)
    assert lq.build_listing_state(raw, normalize_listing(raw)) == lq.build_listing_state(
        raw, normalize_listing(raw)
    )
