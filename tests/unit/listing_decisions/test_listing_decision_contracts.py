"""M14-1 contracts: proposals, outcomes, claim references, state coverage and the
evidence/reference consistency between a state, its raw listing and its M13 normalization."""

import pytest
from listing_decision_support import LAPTOP, built, raw_of, state_of
from pydantic import ValidationError

from ecommerce_search.decision.listing_models import (
    MAX_ATTRIBUTE_QUESTIONS,
    AttributeQuestion,
    AttributeSupportProposal,
    CategoryProposal,
    ClaimReference,
    ListingDecisionMismatch,
    ListingDecisionOutcome,
    ListingDecisionState,
    ListingText,
    OmissionReason,
    OmittedCandidate,
    SubcategoryProposal,
    TextStatus,
    validate_outcome,
)
from ecommerce_search.decision.listing_questions import (
    StateMismatch,
    build_listing_state,
    verify_listing_state,
)
from ecommerce_search.normalization.models import EvidenceMismatch
from ecommerce_search.normalization.normalizer import normalize_listing

HASH = "a" * 64


def category(**kw) -> CategoryProposal:
    fields = {
        "label": "laptop",
        "raw_choice": "laptop",
        "top_labels": ("laptop",),
        "provider_probabilities": {"laptop": 0.9, "none": 0.1},
        "provider_confidence": 0.9,
    }
    return CategoryProposal(**(fields | kw))


def support(ordinal: int, label: str = "supported", **kw) -> AttributeSupportProposal:
    fields = {
        "ordinal": ordinal,
        "label": label,
        "raw_choice": label,
        "top_labels": (label,),
        "provider_probabilities": {label: 0.8},
        "provider_confidence": 0.8,
    }
    return AttributeSupportProposal(**(fields | kw))


def subcategory(**kw) -> SubcategoryProposal:
    fields = {
        "label": None,
        "raw_choice": "none",
        "top_labels": ("none",),
        "provider_probabilities": {"none": 0.7, "business": 0.3},
        "provider_confidence": 0.7,
    }
    return SubcategoryProposal(**(fields | kw))


def jev_outcome(state, **kw) -> ListingDecisionOutcome:
    fields = {
        "provider": "jev",
        "state_sha256": state.state_sha256(),
        "model": "m",
        "latency_ms": 1.0,
        "category": category(),
        "subcategory": subcategory(),
        "attribute_support": tuple(support(o) for o in state.question_ordinals()),
    }
    return ListingDecisionOutcome(**(fields | kw))


# ---- proposals ---------------------------------------------------------------------------


def test_a_valid_proposal_keeps_raw_values_and_has_no_gate_confidence():
    p = category()
    assert (p.label, p.raw_choice, p.gate_confidence) == ("laptop", "laptop", None)


@pytest.mark.parametrize(
    "kw",
    [
        {"raw_choice": "tablet", "top_labels": ("tablet",), "label": None},  # out of set
        {"provider_probabilities": {"laptop": 0.9, "tablet": 0.1}},  # unknown probability label
        {"gate_confidence": 0.9},  # never set before calibration
        {"provider_probabilities": {"laptop": float("nan")}},
        {"provider_probabilities": {"laptop": 1.5}},
        {"provider_confidence": 1.01},
        {"provider_probabilities": {"laptop": 0.4, "phone": 0.6}},  # top label is not the max
        {"top_labels": ("phone", "laptop")},  # not in label order
        {"top_labels": ("laptop", "laptop")},  # duplicate
        {"raw_choice": "phone"},  # not a top label
        {"label": None},  # a unique non-none top must carry the label
        {"label": "phone"},
    ],
)
def test_invalid_category_proposals_are_rejected(kw):
    with pytest.raises(ValidationError):
        category(**kw)


def test_a_tie_or_a_none_answer_never_carries_a_label():
    tie = {
        "top_labels": ("laptop", "phone"),
        "provider_probabilities": {"laptop": 0.5, "phone": 0.5},
    }
    assert category(label=None, **tie).label is None
    with pytest.raises(ValidationError):
        category(label="laptop", **tie)
    none = {"raw_choice": "none", "top_labels": ("none",), "provider_probabilities": {"none": 1.0}}
    assert category(label=None, **none).label is None
    with pytest.raises(ValidationError):
        category(label="none", **none)


def test_subcategory_labels_are_the_taxonomy_names_plus_none():
    assert (
        subcategory(
            label="over-ear",
            raw_choice="over-ear",
            top_labels=("over-ear",),
            provider_probabilities={"over-ear": 1.0},
        ).label
        == "over-ear"
    )
    with pytest.raises(ValidationError):
        subcategory(
            label="gaming",
            raw_choice="gaming",
            top_labels=("gaming",),
            provider_probabilities={"gaming": 1.0},
        )


def test_support_labels_are_closed_and_not_stated_is_a_real_answer():
    assert support(0, "not_stated").label == "not_stated"
    for bad in ("unknown", "none", "true"):
        with pytest.raises(ValidationError):
            support(0, bad)


# ---- claim references and state coverage --------------------------------------------------


def claim(**kw) -> ClaimReference:
    fields = {
        "ordinal": 0,
        "field": "color",
        "canonical_value": "Black",
        "source_key": "color",
        "raw_text": "Black",
        "rule_id": "text.whitespace",
    }
    return ClaimReference(**(fields | kw))


@pytest.mark.parametrize(
    "kw",
    [
        {"field": "ram_gb", "canonical_value": "16"},  # numeric field
        {"field": "price", "canonical_value": "1.00"},
        {"field": "category", "canonical_value": "laptop"},  # a decision target
        {"field": "subcategory", "canonical_value": "business"},
        {"field": "camera", "canonical_value": "50MP"},  # not allowlisted
        {"field": "unknown"},
        {"basis": "title"},  # text extractions are never asked
        {"canonical_value": ""},
        {"ordinal": -1},
    ],
)
def test_claims_outside_the_allowlist_are_rejected(kw):
    with pytest.raises(ValidationError):
        claim(**kw)


def test_question_id_must_name_the_claim_ordinal():
    with pytest.raises(ValidationError):
        AttributeQuestion(question_id="attribute_support[1]", claim=claim())


def state(**kw) -> ListingDecisionState:
    base = state_of().model_dump()
    return ListingDecisionState.model_validate(base | kw, strict=False)


def test_every_candidate_is_asked_or_omitted_exactly_once():
    s = state_of()
    assert sorted(s.question_ordinals() + tuple(o.ordinal for o in s.omitted_candidates)) == list(
        range(s.candidate_count)
    )
    with pytest.raises(ValidationError):
        state(candidate_count=s.candidate_count + 1)
    with pytest.raises(ValidationError):
        state(omitted_candidates=s.omitted_candidates[1:])
    with pytest.raises(ValidationError):  # asked and omitted
        state(
            omitted_candidates=(
                *s.omitted_candidates,
                OmittedCandidate(
                    ordinal=s.question_ordinals()[0],
                    field="brand",
                    reason=OmissionReason.NOT_ALLOWLISTED,
                ),
            )
        )


def test_question_cap_reason_requires_the_cap_to_be_reached():
    s = state_of()
    assert len(s.attribute_questions) < MAX_ATTRIBUTE_QUESTIONS
    omitted = list(s.omitted_candidates)
    omitted[0] = omitted[0].model_copy(update={"reason": OmissionReason.QUESTION_CAP})
    with pytest.raises(ValidationError):
        state(omitted_candidates=tuple(omitted))


def test_listing_text_status_must_match_its_value():
    with pytest.raises(ValidationError):
        ListingText(title="x", title_status=TextStatus.ABSENT, description_status=TextStatus.ABSENT)
    with pytest.raises(ValidationError):
        ListingText(title_status=TextStatus.PRESENT, description_status=TextStatus.ABSENT)
    with pytest.raises(ValidationError):
        ListingText(
            title="  ", title_status=TextStatus.PRESENT, description_status=TextStatus.ABSENT
        )


# ---- outcomes ----------------------------------------------------------------------------


def test_deterministic_outcome_carries_no_proposal_or_model_telemetry():
    ListingDecisionOutcome(provider="deterministic", state_sha256=HASH)
    for kw in (
        {"category": category()},
        {"model": "m"},
        {"fallback_reason": "timeout"},
        {"attribute_support": (support(0),)},
        {"latency_ms": 1.0},
    ):
        with pytest.raises(ValidationError):
            ListingDecisionOutcome(provider="deterministic", state_sha256=HASH, **kw)


def test_model_outcome_is_all_proposals_or_a_fallback():
    s = state_of()
    jev_outcome(s)
    jev_outcome(s, category=None, subcategory=None, attribute_support=(), fallback_reason="timeout")
    with pytest.raises(ValidationError):
        jev_outcome(s, subcategory=None)
    with pytest.raises(ValidationError):
        jev_outcome(s, fallback_reason="timeout")
    with pytest.raises(ValidationError):
        jev_outcome(
            s, fallback_reason="not_a_reason", category=None, subcategory=None, attribute_support=()
        )
    with pytest.raises(ValidationError):
        jev_outcome(s, model=None)
    with pytest.raises(ValidationError):  # unsorted / duplicate ordinals
        jev_outcome(s, attribute_support=(support(3), support(3)))


def test_validate_outcome_requires_exactly_the_asked_ordinals_and_the_same_state():
    s = state_of()
    asked = s.question_ordinals()
    validate_outcome(s, jev_outcome(s))
    unknown = max(range(s.candidate_count + 5)) + 1
    for answers in (asked[1:], (*asked, unknown)):
        with pytest.raises(ListingDecisionMismatch):
            validate_outcome(
                s, jev_outcome(s, attribute_support=tuple(support(o) for o in answers))
            )
    omitted = s.omitted_candidates[0].ordinal  # an existing but omitted candidate
    with pytest.raises(ListingDecisionMismatch):
        validate_outcome(
            s,
            jev_outcome(s, attribute_support=tuple(support(o) for o in sorted((*asked, omitted)))),
        )
    with pytest.raises(ListingDecisionMismatch):
        validate_outcome(s, jev_outcome(s, state_sha256=HASH))
    other = state_of(LAPTOP | {"color": "Black"})
    with pytest.raises(ListingDecisionMismatch):
        validate_outcome(other, jev_outcome(s))


# ---- evidence and reference consistency ----------------------------------------------------


def test_state_references_the_exact_m13_run():
    raw, n = built()
    s = build_listing_state(raw, n)
    assert s.m13.raw_sha256 == raw.raw_sha256
    assert s.m13.output_sha256 == n.output_sha256()
    assert (s.m13.rules_version, s.m13.rules_digest, s.m13.taxonomy_version) == (
        n.rules_version,
        n.rules_digest,
        n.taxonomy_version,
    )
    for q in s.attribute_questions:
        c = n.candidates[q.claim.ordinal]
        assert (c.field, c.normalized_value, c.source_key, c.raw_text, c.rule_id) == (
            q.claim.field,
            q.claim.canonical_value,
            q.claim.source_key,
            q.claim.raw_text,
            q.claim.rule_id,
        )
    verify_listing_state(s, raw, n)


def test_a_normalization_of_another_listing_is_rejected():
    raw, _ = built()
    other = normalize_listing(raw_of(LAPTOP | {"color": "Black"}))
    with pytest.raises(EvidenceMismatch):
        build_listing_state(raw, other)


def test_a_state_from_another_listing_does_not_verify():
    raw, n = built()
    s = state_of(LAPTOP | {"seller_listing_ref": "ref-2"})  # same candidates, other raw bytes
    with pytest.raises(StateMismatch):
        verify_listing_state(s, raw, n)
    good = build_listing_state(raw, n)
    tampered = good.model_copy(update={"text": good.text.model_copy(update={"title": "Other"})})
    with pytest.raises(StateMismatch):
        verify_listing_state(tampered, raw, n)


def test_wrong_argument_types_raise_type_error():
    raw, n = built()
    with pytest.raises(TypeError):
        build_listing_state(LAPTOP, n)
    with pytest.raises(TypeError):
        build_listing_state(raw, n.model_dump())
