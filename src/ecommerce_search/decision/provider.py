"""DecisionProvider interface and the deterministic provider (ADR-003, Milestones 6 and 11).

A provider receives the normalized query and the deterministic parse and returns a
`DecisionResult`. The deterministic provider returns the parse unchanged. The optional Jev provider
(Milestone 11, `decision/jev_provider.py`) returns the same unchanged parse plus a `JevOutcome`:
bounded category and semantic-intent proposals with the raw provider probabilities and the
provider's own confidence, exactly as returned. No gate confidence exists yet: it is derived only
after calibration in Milestone 12, and nothing here applies a proposal to search.
"""

import math
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.query_understanding.models import Intent, QueryUnderstanding
from ecommerce_search.query_understanding.parser import parse_normalized_query

JevFallbackReason = Literal[
    "timeout",
    "transport_error",
    "auth_error",
    "rate_limited",
    "server_error",
    "request_rejected",
    "invalid_response",
    "model_mismatch",
    "deadline_exceeded",
    "saturated",
    "provider_closed",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _JevProposal(_Frozen):
    """One validated Choice answer. `raw_choice`, `provider_probabilities` and
    `provider_confidence` are stored exactly as TypeSafe returned them; `top_labels` lists every
    label sharing the highest probability, and `label` is set only when that top label is unique
    and is not the explicit "none" option."""

    label: Category | Intent | None  # narrowed by each subclass
    raw_choice: str
    top_labels: tuple[str, ...] = Field(min_length=1)
    provider_probabilities: dict[str, float]
    provider_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.raw_choice not in self.top_labels:
            raise ValueError("raw_choice must be one of the top labels")
        if not set(self.top_labels) <= set(self.provider_probabilities):
            raise ValueError("top labels must have probabilities")
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in self.provider_probabilities.values()):
            raise ValueError("probabilities must be finite and within [0, 1]")
        if self.label is not None and self.top_labels != (self.label.value,):
            raise ValueError("label must be the unique top label")
        return self


class CategoryProposal(_JevProposal):
    decision_type: Literal["category"] = "category"
    label: Category | None


class IntentProposal(_JevProposal):
    decision_type: Literal["semantic_intent"] = "semantic_intent"
    label: Intent | None


class JevOutcome(_Frozen):
    """Telemetry and proposals of one Jev call. Either both proposals are present and
    `fallback_reason` is None, or no proposal is present and `fallback_reason` says why."""

    model: str
    response_model: str | None = None
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    fallback_reason: JevFallbackReason | None = None
    category: CategoryProposal | None = None
    semantic_intent: IntentProposal | None = None

    @model_validator(mode="after")
    def _proposals_xor_fallback(self) -> Self:
        proposals = (self.category, self.semantic_intent)
        if self.fallback_reason is None and None in proposals:
            raise ValueError("a successful outcome needs both proposals")
        if self.fallback_reason is not None and proposals != (None, None):
            raise ValueError("a fallback outcome carries no proposal")
        return self


class DecisionResult(_Frozen):
    provider: Literal["deterministic", "jev"] = "deterministic"
    provider_version: Literal["qu-1", "jq-1"] = "qu-1"
    understanding: QueryUnderstanding
    jev: JevOutcome | None = None

    @model_validator(mode="after")
    def _provider_fields_agree(self) -> Self:
        expected = ("jq-1", True) if self.provider == "jev" else ("qu-1", False)
        if (self.provider_version, self.jev is not None) != expected:
            raise ValueError("provider, provider_version and jev outcome disagree")
        return self


@runtime_checkable
class DecisionProvider(Protocol):
    def understand(self, query: str, deterministic_result: QueryUnderstanding) -> DecisionResult:
        """Return a decision for `query`, given the deterministic parse of the same query."""
        ...


class DeterministicDecisionProvider:
    """Local, network-free provider: the deterministic parse is the decision."""

    def understand(self, query: str, deterministic_result: QueryUnderstanding) -> DecisionResult:
        return DecisionResult(understanding=deterministic_result)


class QueryUnderstandingFailed(Exception):
    """The provider returned an invalid result. The message never contains query text."""

    def __init__(self) -> None:
        super().__init__("query understanding failed")


def understand_normalized_query(
    normalized_query: str, provider: DecisionProvider
) -> DecisionResult:
    """Parse once, call the provider once and validate its result."""
    deterministic_result = parse_normalized_query(normalized_query)
    result = provider.understand(normalized_query, deterministic_result)
    if not isinstance(result, DecisionResult) or result.understanding.raw_query != normalized_query:
        raise QueryUnderstandingFailed()
    return result
