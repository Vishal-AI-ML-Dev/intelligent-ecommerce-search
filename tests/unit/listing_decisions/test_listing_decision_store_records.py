"""M14-5 decision records without a database: the ORM CHECK values equal the contracts, and a
decision rebuilt from its stored rows reproduces its hashes (tampering is detected)."""

import json
import re
from dataclasses import replace
from typing import get_args

import pytest
from listing_decision_support import LAPTOP, built

from ecommerce_search.decision.listing_models import (
    CATEGORY_LABELS,
    SUBCATEGORY_LABELS,
    ListingDecisionOutcome,
    RejectionCode,
)
from ecommerce_search.decision.listing_provider import DeterministicListingDecisionProvider
from ecommerce_search.decision.listing_questions import build_listing_state
from ecommerce_search.decision.listing_routing import Route, route_listing
from ecommerce_search.decision.listing_store import (
    OUTCOME_FIELDS,
    StoredDecision,
    StoredProbability,
    StoredProposal,
    StoredReason,
    canonical_sha256,
    verify_decision,
)
from ecommerce_search.decision.provider import JevFallbackReason
from ecommerce_search.models.listing_decisions import (
    ListingDecisionProposal,
    ListingDecisionReason,
    ListingDecisionRun,
)
from ecommerce_search.normalization.store import IntegrityFailure

SHA = "a" * 64


def _check(model, name: str) -> str:
    return str(
        next(
            c for c in model.__table__.constraints if str(getattr(c, "name", "")).endswith(name)
        ).sqltext
    )


@pytest.mark.parametrize(
    ("model", "name", "values"),
    [
        (ListingDecisionRun, "provider_valid", {"deterministic", "jev"}),
        (ListingDecisionRun, "usage_valid", {"complete", "partial", "none"}),
        (ListingDecisionRun, "fallback_reason_valid", set(get_args(JevFallbackReason))),
        (ListingDecisionRun, "route_valid", {r.value for r in Route}),
        (ListingDecisionRun, "deterministic_route_valid", {r.value for r in Route}),
        (ListingDecisionReason, "route_valid", {r.value for r in Route}),
        (
            ListingDecisionProposal,
            "decision_type_valid",
            {"category", "subcategory", "attribute_support"},
        ),
    ],
)
def test_check_constraints_use_the_contract_values(model, name, values):
    assert set(re.findall(r"'([^']+)'", _check(model, name))) == values


def test_rejection_check_uses_the_allowlisted_codes():
    found = set(re.findall(r"'([^']+)'", _check(ListingDecisionRun, "rejection_valid")))
    assert found == set(get_args(RejectionCode)) | {"invalid_response"}


def test_routes_have_no_rejection_and_gate_confidence_must_be_null():
    assert "reject" not in _check(ListingDecisionRun, "route_valid")
    assert _check(ListingDecisionProposal, "gate_confidence_unset") == "gate_confidence IS NULL"


# ---- rebuilt records --------------------------------------------------------------------


def _jev_outcome(state) -> ListingDecisionOutcome:
    def proposal(labels, top, **extra):
        rest = [lbl for lbl in labels if lbl != top]
        probabilities = {lbl: (0.9 if lbl == top else 0.1 / len(rest)) for lbl in labels}
        return {
            "label": top,
            "raw_choice": top,
            "top_labels": [top],
            "provider_probabilities": probabilities,
            "provider_confidence": 0.75,
            **extra,
        }

    return ListingDecisionOutcome.model_validate(
        {
            "provider": "jev",
            "state_sha256": state.state_sha256(),
            "model": "jev-1.13.0",
            "latency_ms": 10.5,
            "category": proposal(CATEGORY_LABELS, "laptop", decision_type="category"),
            "subcategory": proposal(SUBCATEGORY_LABELS, "business", decision_type="subcategory"),
            "attribute_support": [
                proposal(
                    ("supported", "contradicted", "not_stated"),
                    "supported",
                    decision_type="attribute_support",
                    ordinal=o,
                )
                for o in state.question_ordinals()
            ],
        },
        strict=False,
    )


def _stored(outcome: ListingDecisionOutcome, normalization, state) -> StoredDecision:
    """What the store writes, read back without a database."""
    decision = route_listing(normalization, state, outcome)
    dumped = outcome.model_dump(mode="json")
    proposals = [outcome.category, outcome.subcategory, *outcome.attribute_support]
    rows = tuple(
        StoredProposal(
            ordinal=i,
            decision_type=p.decision_type,
            claim_ordinal=getattr(p, "ordinal", None),
            label=p.label,
            raw_choice=p.raw_choice,
            provider_confidence=p.provider_confidence,
            gate_confidence=None,
            probabilities=tuple(
                StoredProbability(
                    label_ordinal=type(p).LABELS.index(lbl),
                    label=lbl,
                    probability=v,
                    is_top=lbl in p.top_labels,
                )
                for lbl, v in sorted(
                    p.provider_probabilities.items(), key=lambda kv: type(p).LABELS.index(kv[0])
                )
            ),
        )
        for i, p in enumerate(q for q in proposals if q is not None)
    )
    return StoredDecision(
        decision_run_id=1,
        normalization_rules_version=normalization.rules_version,
        normalization_output_sha256=state.m13.output_sha256,
        linked_output_sha256=state.m13.output_sha256,
        question_set_version=state.question_set_version,
        question_set_digest=state.question_set_digest,
        policy_version=decision.policy_version,
        policy_digest=decision.policy_digest,
        route=decision.route.value,
        deterministic_route=decision.deterministic_route.value,
        proposal_count=len(rows),
        reason_count=len(decision.reasons),
        outcome_sha256=canonical_sha256(dumped),
        decision_sha256=canonical_sha256(decision.model_dump(mode="json")),
        created_at=None,
        telemetry={k: dumped[k] for k in OUTCOME_FIELDS},
        proposals=rows,
        reasons=tuple(
            StoredReason(ordinal=i, code=r.code, route=r.route.value, subject=r.subject)
            for i, r in enumerate(decision.reasons)
        ),
    )


@pytest.fixture(params=["deterministic", "jev"])
def stored(request) -> StoredDecision:
    raw, normalization = built(LAPTOP)
    state = build_listing_state(raw, normalization)
    if request.param == "deterministic":
        outcome = DeterministicListingDecisionProvider().decide(state)
    else:
        outcome = _jev_outcome(state)
    return _stored(outcome, normalization, state)


def test_a_faithful_record_reproduces_both_hashes(stored):
    verify_decision(stored)
    rebuilt = ListingDecisionOutcome.model_validate_json(json.dumps(stored.recorded_outcome()))
    assert canonical_sha256(rebuilt.model_dump(mode="json")) == stored.outcome_sha256


def test_tampered_records_are_detected(stored):
    tampered = [
        replace(
            stored,
            route=Route.MANUAL_REVIEW.value
            if stored.route != "manual_review"
            else Route.ACCEPT_WITH_WARNING.value,
        ),
        replace(stored, linked_output_sha256=SHA),
        replace(stored, reasons=stored.reasons[1:]),
        replace(stored, telemetry={**stored.telemetry, "fallback_reason": "timeout"}),
    ]
    if stored.proposals:
        first = stored.proposals[0]
        flipped = tuple(replace(p, is_top=not p.is_top) for p in first.probabilities)
        tampered += [
            replace(
                stored, proposals=(replace(first, probabilities=flipped), *stored.proposals[1:])
            ),
            replace(stored, proposals=stored.proposals[1:]),
            replace(
                stored, proposals=(stored.proposals[1], stored.proposals[0], *stored.proposals[2:])
            ),
        ]
    for record in tampered:
        with pytest.raises(IntegrityFailure, match="failed its integrity check"):
            verify_decision(record)
