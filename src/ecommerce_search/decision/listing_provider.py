"""ListingDecisionProvider interface and the deterministic no-model baseline (Milestone 14).

A provider receives a `ListingDecisionState` and returns a `ListingDecisionOutcome` for exactly
that state. The deterministic baseline proposes nothing: category, subcategory and attribute
support stay with the M13 deterministic record, and nothing is accepted, rejected or routed here.
"""

from typing import Protocol, runtime_checkable

from ecommerce_search.decision.listing_models import (
    ListingDecisionMismatch,
    ListingDecisionOutcome,
    ListingDecisionState,
    validate_outcome,
)
from ecommerce_search.decision.listing_questions import build_listing_state
from ecommerce_search.normalization.intake import RawListing
from ecommerce_search.normalization.models import ListingNormalization


@runtime_checkable
class ListingDecisionProvider(Protocol):
    def decide(self, state: ListingDecisionState) -> ListingDecisionOutcome:
        """Return the outcome for `state`."""
        ...


class DeterministicListingDecisionProvider:
    """Local, network-free baseline: no model, no proposal."""

    def decide(self, state: ListingDecisionState) -> ListingDecisionOutcome:
        return ListingDecisionOutcome(provider="deterministic", state_sha256=state.state_sha256())


class ListingDecisionFailed(Exception):
    """The provider returned an invalid outcome. The message never contains listing text."""

    def __init__(self) -> None:
        super().__init__("listing decision failed")


def decide_listing(
    raw: RawListing, normalization: ListingNormalization, provider: ListingDecisionProvider
) -> tuple[ListingDecisionState, ListingDecisionOutcome]:
    """Build the state once, call the provider once and validate its outcome against the state."""
    state = build_listing_state(raw, normalization)
    outcome = provider.decide(state)
    if not isinstance(outcome, ListingDecisionOutcome):
        raise ListingDecisionFailed()
    try:
        validate_outcome(state, outcome)
    except ListingDecisionMismatch as exc:
        raise ListingDecisionFailed() from exc
    return state, outcome
