"""M14-3 deterministic routing policy `lr-1`: approved rule table, escalation-only Jev,
fallbacks, input binding, determinism and property tests (seeded; no network, SDK or model)."""

import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest
from listing_decision_support import LAPTOP, built
from pydantic import ValidationError
from test_listing_decision_state import MANY

from ecommerce_search.decision import listing_routing as lr
from ecommerce_search.decision.listing_models import (
    CATEGORY_LABELS,
    SUBCATEGORY_LABELS,
    ListingDecisionOutcome,
    SupportLabel,
)
from ecommerce_search.decision.listing_provider import DeterministicListingDecisionProvider
from ecommerce_search.decision.listing_questions import build_listing_state
from ecommerce_search.decision.listing_routing import (
    Route,
    RoutingDecision,
    RoutingInputMismatch,
    RoutingReason,
    route_listing,
)
from ecommerce_search.decision.provider import JevFallbackReason
from ecommerce_search.normalization.models import FindingKind, Outcome

LR1_DIGEST = "7d67e7115531516cfcef159b3b904084e066130d77e7ff309ae0f273c6764a14"
SUPPORT_LABELS = tuple(s.value for s in SupportLabel)
AWW, MR = Route.ACCEPT_WITH_WARNING, Route.MANUAL_REVIEW
RISK = RoutingReason(code="risk.not_assessed", route=AWW)
CLEAN = {
    "seller_id": "seller-3",
    "seller_listing_ref": "clean",
    "title": "Sony WH-1000XM5 Wireless Headphones",
    "description": "Over-ear wireless headphones",
    "category": "headphones",
    "subcategory": "over-ear",
    "brand": "Sony",
    "price": "29990",
    "currency": "INR",
    "availability": "in_stock",
    "wireless": True,
    "anc": True,
    "connectivity": "bluetooth",
}


def _inputs(fields: dict):
    raw, normalization = built(fields)
    return normalization, build_listing_state(raw, normalization)


def _deterministic(normalization, state) -> RoutingDecision:
    outcome = DeterministicListingDecisionProvider().decide(state)
    return route_listing(normalization, state, outcome)


def _proposal(labels, top, confidence: float = 0.5, mass: float = 0.9) -> dict:
    """A valid proposal whose top labels are `top` (a tie when more than one), sharing `mass`."""
    top = [lbl for lbl in labels if lbl in top]
    rest = [lbl for lbl in labels if lbl not in top]
    high = mass / len(top) if rest else 1.0 / len(top)  # mass >= 0.8: mass/k > (1-mass)/r, k <= 3
    low = (1.0 - mass) / len(rest) if rest else 0.0
    probabilities = {lbl: (high if lbl in top else low) for lbl in labels}
    unique = top[0] if len(top) == 1 and top[0] != "none" else None
    return {
        "label": unique,
        "raw_choice": top[0],
        "top_labels": tuple(top),
        "provider_probabilities": probabilities,
        "provider_confidence": confidence,
    }


def _support(top, ordinal: int, confidence: float = 0.5, mass: float = 0.9) -> dict:
    proposal = _proposal(SUPPORT_LABELS, top, confidence, mass)
    if len(top) == 1:  # support has no abstaining label
        proposal["label"] = top[0]
    return {**proposal, "ordinal": ordinal}


def _jev(
    state,
    category=("headphones",),
    subcategory=("over-ear",),
    support=None,
    confidence: float = 0.5,
    mass: float = 0.9,
    **extra,
):
    support = support or {}
    return ListingDecisionOutcome(
        provider="jev",
        state_sha256=state.state_sha256(),
        model="jev-1.13.0",
        latency_ms=1.0,
        category=_proposal(CATEGORY_LABELS, category, confidence, mass),
        subcategory=_proposal(SUBCATEGORY_LABELS, subcategory, confidence, mass),
        attribute_support=tuple(
            _support(support.get(o, ("supported",)), o, confidence, mass)
            for o in state.question_ordinals()
        ),
        **extra,
    )


def _codes(decision: RoutingDecision) -> list[str]:
    return [r.code for r in decision.reasons]


# --- the approved rule table --------------------------------------------------------------------


def test_lr1_policy_digest_is_pinned() -> None:
    assert lr.policy_digest() == LR1_DIGEST
    assert lr.policy_as_dict()["auto_accept_reachable"] is False


def test_routes_are_exactly_the_three_automated_outcomes_and_none_rejects() -> None:
    assert [r.value for r in Route] == ["auto_accept", "accept_with_warning", "manual_review"]
    assert not any("reject" in r.value for r in Route)


def test_m13_signal_table_is_the_approved_one() -> None:
    assert lr.FINDING_ROUTES == {
        FindingKind.VALUE_CONFLICT: MR,
        FindingKind.CROSS_FIELD_CONTRADICTION: MR,
        FindingKind.INCOMPATIBLE_ATTRIBUTE: MR,
        FindingKind.INCOMPATIBLE_SUBCATEGORY: MR,
        FindingKind.BRAND_CATEGORY_MISMATCH: MR,
        FindingKind.MISSING_REQUIRED: MR,
        FindingKind.UNSUPPORTED_ATTRIBUTE: AWW,
        FindingKind.COMPATIBILITY_UNCHECKED: AWW,
        FindingKind.MISSING_EXPECTED: AWW,
        FindingKind.EMPTY_VALUE: AWW,
    }
    assert lr.CANDIDATE_ROUTES == {
        Outcome.NORMALIZED: None,
        Outcome.INVALID: MR,
        Outcome.UNCERTAIN: AWW,
        Outcome.UNSUPPORTED: AWW,
    }


def test_jev_signal_table_is_the_approved_one() -> None:
    assert lr.JEV_ROUTES == {
        "jev.category_disagrees_with_seller": MR,
        "jev.incompatible_category_subcategory": MR,
        "jev.claim_contradicted": MR,
        "jev.category_tie": AWW,
        "jev.category_none": AWW,
        "jev.subcategory_tie": AWW,
        "jev.subcategory_none": AWW,
        "jev.claim_tie": AWW,
    }
    assert lr.JEV_FALLBACK_ROUTE is AWW


# --- baseline and deterministic signals ----------------------------------------------------------


def test_a_clean_listing_gets_accept_with_warning_for_risk_not_assessed_only() -> None:
    normalization, state = _inputs(CLEAN)
    assert normalization.findings == ()
    assert all(c.outcome is Outcome.NORMALIZED for c in normalization.candidates)
    decision = _deterministic(normalization, state)
    assert (decision.route, decision.deterministic_route) == (AWW, AWW)
    assert decision.reasons == (RISK,)
    assert (decision.provider, decision.jev_fallback_reason) == ("deterministic", None)
    assert decision.state_sha256 == state.state_sha256()
    assert (decision.policy_version, decision.policy_digest) == ("lr-1", LR1_DIGEST)


def test_a_clean_listing_with_fully_agreeing_jev_is_still_accept_with_warning() -> None:
    normalization, state = _inputs(CLEAN)
    assert state.attribute_questions
    decision = route_listing(normalization, state, _jev(state))
    assert decision.route is AWW and decision.reasons == (RISK,)


def test_m13_findings_route_with_their_field_as_subject() -> None:
    decision = _deterministic(*_inputs(LAPTOP))
    assert decision.route is MR
    reasons = set(decision.reasons)
    assert RoutingReason(code="m13.incompatible_attribute", route=MR, subject="camera") in reasons
    assert RoutingReason(code="m13.missing_required", route=MR, subject="currency") in reasons
    assert RoutingReason(code="m13.missing_expected", route=AWW, subject="storage_gb") in reasons
    assert RoutingReason(code="m13.unsupported_attribute", route=AWW) in reasons
    assert RISK in reasons


def test_warning_level_m13_signals_alone_give_accept_with_warning() -> None:
    normalization, state = _inputs(CLEAN | {"Occasion": "Formal", "anc": None})
    kinds = {f.kind for f in normalization.findings}
    assert kinds == {FindingKind.UNSUPPORTED_ATTRIBUTE, FindingKind.EMPTY_VALUE}
    decision = _deterministic(normalization, state)
    assert decision.route is AWW
    assert _codes(decision) == ["m13.empty_value", "m13.unsupported_attribute", "risk.not_assessed"]


@pytest.mark.parametrize(
    ("changes", "code", "route", "subject"),
    [
        ({"price": "abc"}, "m13.candidate_unsupported", AWW, "price"),
        ({"price": "0"}, "m13.candidate_invalid", MR, "price"),
    ],
    ids=["unsupported", "invalid"],
)
def test_candidate_outcomes_route_as_approved(changes, code, route, subject) -> None:
    normalization, state = _inputs(CLEAN | changes)
    decision = _deterministic(normalization, state)
    assert decision.route is route
    assert _codes(decision) == sorted([code, "risk.not_assessed"])
    assert RoutingReason(code=code, route=route, subject=subject) in set(decision.reasons)


def test_an_uncertain_text_candidate_warns() -> None:
    normalization, state = _inputs(LAPTOP | {"title": "HP Laptop up to 32GB RAM"})
    assert any(c.outcome is Outcome.UNCERTAIN for c in normalization.candidates)
    decision = _deterministic(normalization, state)
    reason = RoutingReason(code="m13.candidate_uncertain", route=AWW, subject="ram_gb")
    assert reason in set(decision.reasons)


def test_claims_omitted_by_the_question_cap_warn_per_claim() -> None:
    normalization, state = _inputs(MANY)
    capped = [o for o in state.omitted_candidates if o.reason.value == "question_cap"]
    assert capped
    decision = _deterministic(normalization, state)
    got = {r.subject for r in decision.reasons if r.code == "jl1.question_cap"}
    assert got == {f"attribute_support[{o.ordinal}]" for o in capped}
    assert all(r.route is AWW for r in decision.reasons if r.code == "jl1.question_cap")


# --- Jev escalations ----------------------------------------------------------------------------


def test_jev_category_disagreeing_with_the_seller_needs_review() -> None:
    normalization, state = _inputs(CLEAN)
    decision = route_listing(
        normalization, state, _jev(state, category=("phone",), subcategory=("smartphone",))
    )
    assert decision.route is MR and decision.deterministic_route is AWW
    assert "jev.category_disagrees_with_seller" in _codes(decision)
    assert "jev.incompatible_category_subcategory" not in _codes(decision)


def test_an_incompatible_jev_category_subcategory_pair_needs_review() -> None:
    normalization, state = _inputs(CLEAN)
    decision = route_listing(normalization, state, _jev(state, subcategory=("running",)))
    assert decision.route is MR
    assert _codes(decision) == ["jev.incompatible_category_subcategory", "risk.not_assessed"]


def test_a_contradicted_claim_needs_review_with_its_question_id() -> None:
    normalization, state = _inputs(CLEAN)
    ordinal = state.question_ordinals()[1]
    decision = route_listing(
        normalization, state, _jev(state, support={ordinal: ("contradicted",)})
    )
    assert decision.route is MR
    assert RoutingReason(
        code="jev.claim_contradicted", route=MR, subject=f"attribute_support[{ordinal}]"
    ) in set(decision.reasons)


def test_not_stated_never_escalates() -> None:
    normalization, state = _inputs(CLEAN)
    support = dict.fromkeys(state.question_ordinals(), ("not_stated",))
    decision = route_listing(normalization, state, _jev(state, support=support))
    assert decision.reasons == (RISK,)


@pytest.mark.parametrize(
    ("category", "subcategory", "code"),
    [
        (("headphones", "phone"), ("over-ear",), "jev.category_tie"),
        (("none",), ("over-ear",), "jev.category_none"),
        (("headphones",), ("in-ear", "over-ear"), "jev.subcategory_tie"),
        (("headphones",), ("none",), "jev.subcategory_none"),
    ],
)
def test_ties_and_none_answers_warn(category, subcategory, code) -> None:
    normalization, state = _inputs(CLEAN)
    decision = route_listing(normalization, state, _jev(state, category, subcategory))
    assert decision.route is AWW
    assert _codes(decision) == sorted([code, "risk.not_assessed"])


def test_a_support_tie_warns_even_when_contradicted_is_among_the_top_labels() -> None:
    normalization, state = _inputs(CLEAN)
    ordinal = state.question_ordinals()[0]
    outcome = _jev(state, support={ordinal: ("supported", "contradicted")})
    decision = route_listing(normalization, state, outcome)
    assert decision.route is AWW
    assert RoutingReason(
        code="jev.claim_tie", route=AWW, subject=f"attribute_support[{ordinal}]"
    ) in set(decision.reasons)


def test_jev_never_downgrades_manual_review() -> None:
    normalization, state = _inputs(LAPTOP)
    agreeing = _jev(state, category=("laptop",), subcategory=("business",))
    decision = route_listing(normalization, state, agreeing)
    assert decision.deterministic_route is MR and decision.route is MR
    assert not [c for c in _codes(decision) if c.startswith("jev.")]


# --- fallbacks and disabled Jev -----------------------------------------------------------------


@pytest.mark.parametrize("reason", get_args(JevFallbackReason))
@pytest.mark.parametrize("fields", [CLEAN, LAPTOP], ids=["clean", "laptop"])
def test_a_jev_fallback_keeps_the_deterministic_route_and_records_its_reason(
    fields, reason
) -> None:
    normalization, state = _inputs(fields)
    outcome = ListingDecisionOutcome(
        provider="jev",
        state_sha256=state.state_sha256(),
        model="jev-1.13.0",
        latency_ms=1.0,
        fallback_reason=reason,
    )
    decision = route_listing(normalization, state, outcome)
    deterministic = _deterministic(normalization, state)
    assert decision.route is deterministic.route is decision.deterministic_route
    assert decision.jev_fallback_reason == reason
    fallback = RoutingReason(code=f"jev.fallback.{reason}", route=AWW)
    assert set(decision.reasons) == set(deterministic.reasons) | {fallback}


def test_disabled_jev_routes_on_deterministic_signals_only() -> None:
    normalization, state = _inputs(LAPTOP)
    decision = _deterministic(normalization, state)
    assert decision.provider == "deterministic" and decision.jev_fallback_reason is None
    assert not [c for c in _codes(decision) if c.startswith("jev.")]
    assert decision.route is decision.deterministic_route


# --- input binding ------------------------------------------------------------------------------


def test_an_outcome_for_another_state_produces_no_route() -> None:
    normalization, state = _inputs(CLEAN)
    _, other = _inputs(CLEAN | {"brand": "Bose"})
    with pytest.raises(RoutingInputMismatch):
        route_listing(normalization, state, _jev(other))


def test_a_normalization_from_another_listing_produces_no_route() -> None:
    _, state = _inputs(CLEAN)
    other, _ = _inputs(CLEAN | {"brand": "Bose"})
    with pytest.raises(RoutingInputMismatch):
        _deterministic(other, state)


def test_a_jev_outcome_missing_an_asked_claim_produces_no_route() -> None:
    normalization, state = _inputs(CLEAN)
    full = _jev(state)
    partial = full.model_copy(update={"attribute_support": full.attribute_support[1:]})
    with pytest.raises(RoutingInputMismatch):
        route_listing(normalization, state, partial)


# --- the decision contract ----------------------------------------------------------------------


def _decision_fields(**changes) -> dict:
    decision = _deterministic(*_inputs(CLEAN))
    return decision.model_dump() | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"route": Route.AUTO_ACCEPT},
        {"route": Route.AUTO_ACCEPT, "deterministic_route": Route.AUTO_ACCEPT},
        {"deterministic_route": MR},
        {"route": MR},
        {"reasons": ()},
        {"reasons": (RoutingReason(code="m13.missing_expected", route=AWW),)},
        {"jev_fallback_reason": "timeout"},
        {"route": "reject"},
    ],
    ids=[
        "auto-accept",
        "auto-accept-both",
        "jev-lowered-route",
        "route-not-strictest",
        "no-reasons",
        "no-risk-reason",
        "fallback-without-jev",
        "reject",
    ],
)
def test_the_decision_contract_rejects_inconsistent_routes(changes) -> None:
    with pytest.raises(ValidationError):
        RoutingDecision.model_validate(_decision_fields(**changes), strict=False)


def test_reasons_are_unique_and_canonically_ordered() -> None:
    decision = _deterministic(*_inputs(LAPTOP))
    keys = [(r.code, r.subject or "") for r in decision.reasons]
    assert keys == sorted(set(keys))
    with pytest.raises(ValidationError):
        RoutingDecision.model_validate(
            _decision_fields(reasons=tuple(reversed(decision.reasons))), strict=False
        )


def test_routing_is_deterministic_for_the_same_inputs() -> None:
    normalization, state = _inputs(LAPTOP)
    outcome = _jev(state, category=("phone", "laptop"), subcategory=("none",))
    first = route_listing(normalization, state, outcome)
    assert all(route_listing(normalization, state, outcome) == first for _ in range(5))
    again = _inputs(LAPTOP)
    assert route_listing(*again, outcome) == first


# --- property tests (seeded, exhaustive where small) --------------------------------------------

_FIXTURES = [CLEAN, LAPTOP, MANY, CLEAN | {"Occasion": "Formal"}]


def _random_top(rng: random.Random, labels) -> tuple[str, ...]:
    return tuple(rng.sample(list(labels), 1 if rng.random() < 0.7 else rng.randint(2, 3)))


@pytest.mark.parametrize("fields", _FIXTURES, ids=["clean", "laptop", "many", "warning"])
def test_property_jev_only_escalates_and_never_auto_accepts_or_rejects(fields) -> None:
    rng = random.Random(143)  # noqa: S311 - seeded test data, not security
    normalization, state = _inputs(fields)
    baseline = _deterministic(normalization, state)
    for _ in range(300):
        outcome = _jev(
            state,
            category=_random_top(rng, CATEGORY_LABELS),
            subcategory=_random_top(rng, SUBCATEGORY_LABELS),
            support={o: _random_top(rng, SUPPORT_LABELS) for o in state.question_ordinals()},
        )
        decision = route_listing(normalization, state, outcome)
        assert decision.route in (AWW, MR)
        assert decision.deterministic_route is baseline.route
        assert lr._SEVERITY[decision.route] >= lr._SEVERITY[baseline.route]
        assert set(baseline.reasons) <= set(decision.reasons)  # no deterministic signal dropped


def test_property_exhaustive_labels_for_one_claim_never_lower_the_route() -> None:
    normalization, state = _inputs(CLEAN | {"anc": None, "connectivity": None, "brand": None})
    assert len(state.question_ordinals()) == 1
    ordinal = state.question_ordinals()[0]
    baseline = _deterministic(normalization, state).route
    seen = set()
    for category, subcategory, support in itertools.product(
        CATEGORY_LABELS, SUBCATEGORY_LABELS, SUPPORT_LABELS
    ):
        outcome = _jev(state, (category,), (subcategory,), {ordinal: (support,)})
        decision = route_listing(normalization, state, outcome)
        assert decision.route is not Route.AUTO_ACCEPT
        assert lr._SEVERITY[decision.route] >= lr._SEVERITY[baseline]
        seen.add(decision.route)
    assert seen == {AWW, MR}


def test_property_confidence_and_probabilities_never_change_the_route() -> None:
    rng = random.Random(143)  # noqa: S311 - seeded test data, not security
    normalization, state = _inputs(CLEAN)
    for _ in range(100):
        category = _random_top(rng, CATEGORY_LABELS)
        subcategory = _random_top(rng, SUBCATEGORY_LABELS)
        support = {o: _random_top(rng, SUPPORT_LABELS) for o in state.question_ordinals()}
        decisions, probabilities = set(), set()
        # Same labels and top labels; every proposal's confidence and probabilities vary.
        for confidence, mass in itertools.product((0.0, 0.01, 0.5, 0.99, 1.0), (0.8, 0.9, 0.99)):
            outcome = _jev(state, category, subcategory, support, confidence, mass)
            probabilities.add(json.dumps(outcome.model_dump(mode="json"), sort_keys=True))
            decisions.add(route_listing(normalization, state, outcome))
        assert len(probabilities) == 15  # the inputs really differ
        assert len(decisions) == 1


# --- offline ------------------------------------------------------------------------------------


def test_routing_imports_no_sdk_settings_database_or_network_module() -> None:
    root = Path(__file__).resolve().parents[3]
    child = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "import ecommerce_search.decision.listing_routing;"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'typesafe_sdk','httpx','httpx2','sqlalchemy','psycopg','fastapi'} "
        "or m.startswith(('ecommerce_search.config','ecommerce_search.db')))))"
    )
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH")}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", child, str(root / "src")],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    )
    assert json.loads(result.stdout) == []
