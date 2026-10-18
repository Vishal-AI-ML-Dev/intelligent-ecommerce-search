"""Decision providers (ADR-003): the interface, the deterministic implementation and the optional,
off-by-default Jev provider (Milestone 11, ADR-005)."""

from ecommerce_search.decision.jev_provider import (
    JevConfig,
    JevConfigurationError,
    JevDecisionProvider,
    build_decision_provider,
)
from ecommerce_search.decision.provider import (
    CategoryProposal,
    DecisionProvider,
    DecisionResult,
    DeterministicDecisionProvider,
    IntentProposal,
    JevOutcome,
    QueryUnderstandingFailed,
    understand_normalized_query,
)

__all__ = [
    "CategoryProposal",
    "DecisionProvider",
    "DecisionResult",
    "DeterministicDecisionProvider",
    "IntentProposal",
    "JevConfig",
    "JevConfigurationError",
    "JevDecisionProvider",
    "JevOutcome",
    "QueryUnderstandingFailed",
    "build_decision_provider",
    "understand_normalized_query",
]
