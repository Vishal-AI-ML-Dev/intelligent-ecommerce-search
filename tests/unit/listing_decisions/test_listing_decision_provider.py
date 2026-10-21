"""M14-1 ListingDecisionProvider Protocol, the deterministic no-model baseline and the
`decide_listing` validation path."""

import pytest
from listing_decision_support import LAPTOP, built, state_of

from ecommerce_search.decision.listing_models import ListingDecisionOutcome
from ecommerce_search.decision.listing_provider import (
    DeterministicListingDecisionProvider,
    ListingDecisionFailed,
    ListingDecisionProvider,
    decide_listing,
)


def test_baseline_satisfies_the_protocol_and_proposes_nothing():
    provider = DeterministicListingDecisionProvider()
    assert isinstance(provider, ListingDecisionProvider)
    raw, n = built()
    state, outcome = decide_listing(raw, n, provider)
    assert outcome.provider == "deterministic"
    assert outcome.state_sha256 == state.state_sha256()
    assert (outcome.category, outcome.subcategory, outcome.attribute_support) == (None, None, ())
    assert outcome.fallback_reason is None and outcome.model is None


def test_baseline_is_deterministic():
    raw, n = built()
    provider = DeterministicListingDecisionProvider()
    assert decide_listing(raw, n, provider) == decide_listing(raw, n, provider)


class _Returns:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def decide(self, state):
        self.calls += 1
        return self.outcome


def test_an_outcome_for_another_state_fails_without_listing_text():
    other = state_of(LAPTOP | {"color": "Black"})
    provider = _Returns(
        ListingDecisionOutcome(provider="deterministic", state_sha256=other.state_sha256())
    )
    with pytest.raises(ListingDecisionFailed) as exc:
        decide_listing(*built(), provider)
    assert str(exc.value) == "listing decision failed"
    assert provider.calls == 1


def test_a_non_outcome_result_fails():
    with pytest.raises(ListingDecisionFailed):
        decide_listing(*built(), _Returns({"provider": "deterministic"}))


def test_a_model_answer_missing_an_asked_claim_fails():
    raw, n = built()
    state = state_of()
    outcome = ListingDecisionOutcome(
        provider="jev",
        state_sha256=state.state_sha256(),
        model="m",
        latency_ms=1.0,
        category={
            "label": "laptop",
            "raw_choice": "laptop",
            "top_labels": ("laptop",),
            "provider_probabilities": {"laptop": 1.0},
            "provider_confidence": 1.0,
        },
        subcategory={
            "label": None,
            "raw_choice": "none",
            "top_labels": ("none",),
            "provider_probabilities": {"none": 1.0},
            "provider_confidence": 1.0,
        },
    )
    assert state.attribute_questions  # the fixture asks claims; none were answered
    with pytest.raises(ListingDecisionFailed):
        decide_listing(raw, n, _Returns(outcome))
