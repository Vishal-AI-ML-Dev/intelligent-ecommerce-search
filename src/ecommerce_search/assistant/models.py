"""Request and response contract of `POST /assistant/answer` (Milestone 16).

Every product fact and every explanation in a response is rendered by the server from retrieved
catalog records. No model-written text is returned: the generator only selects product IDs and
names supporting catalog fields.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ecommerce_search.generation.provider import FactField

PROMPT_VERSION = "asst-prompt-2"
POLICY_VERSION = "asst-policy-3"
EVIDENCE_VERSION = "asst-evidence-2"

Outcome = Literal["answer", "clarify", "abstain", "generation_unavailable"]
ClarifyReason = Literal["conflicting_requirements", "ambiguous_requirement", "no_product_need"]
AbstainReason = Literal["no_matching_products", "model_abstained", "validation_failed"]
GenerationStatus = Literal["not_needed", "disabled", "skipped", "used", "failed"]
ValidationStatus = Literal["not_run", "passed", "repaired", "failed"]
CostStatus = Literal[
    "not_applicable", "estimated", "unknown_pricing_not_configured", "unknown_usage"
]
ConstraintField = Literal[
    "category",
    "brand",
    "min_price",
    "max_price",
    "ram_gb",
    "storage_gb",
    "storage_type",
    "storage_interface",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AssistantRequest(_Frozen):
    # The effective limit and character policy are those of search query text (normalize_query).
    message: str = Field(min_length=1, max_length=1000, description="The shopper's request.")


class EvidenceProduct(_Frozen):
    """One retrieved catalog record: the search API's display fields plus the typed spec fields
    of the catalog record. A spec field is None when the catalog does not give it (or the
    category has no such field); it is never inferred from title or description text."""

    search_rank: int
    product_id: str
    title: str
    brand: str
    category: str
    subcategory: str | None
    description: str | None
    price: Decimal = Field(description="Serialized as a decimal string (no float rounding).")
    currency: str
    rating: float | None
    review_count: int | None
    availability: str
    ram_gb: int | None = None
    storage_gb: int | None = None
    storage_type: str | None = None
    storage_interface: str | None = None


class CitedFact(_Frozen):
    field: FactField
    value: str | None = Field(description="Rendered from the catalog record, never model text.")


class ConstraintCheck(_Frozen):
    field: ConstraintField
    requested: str
    status: Literal["passed", "failed", "not_verified"] = Field(
        description="`not_verified`: the catalog record does not give this field, so the "
        "requirement is neither confirmed nor ruled out."
    )


class Recommendation(_Frozen):
    product_id: str
    facts: tuple[CitedFact, ...]
    constraint_checks: tuple[ConstraintCheck, ...]
    explanation: str = Field(
        description="Rendered deterministically from the stated requirements' check results and "
        "the cited structured catalog values; never model text."
    )
    verification: Literal["all_verified", "not_all_verified", "no_requirements"] = Field(
        description="`all_verified` only when every stated requirement passed on catalog "
        "fields; `not_all_verified` when any is `not_verified`."
    )


class Clarification(_Frozen):
    reason: ClarifyReason
    question: str


class GenerationInfo(_Frozen):
    status: GenerationStatus
    failure: str | None = None
    provider: str | None = None
    model: str | None = None
    calls: int = Field(ge=0, le=2)
    validation: ValidationStatus
    validation_errors: tuple[str, ...] = ()
    input_tokens: int | None = Field(description="None = unknown (not reported), not zero.")
    output_tokens: int | None = Field(description="None = unknown (not reported), not zero.")
    estimated_cost_usd: Decimal | None
    cost_status: CostStatus


class Versions(_Frozen):
    prompt: str = PROMPT_VERSION
    policy: str = POLICY_VERSION
    evidence: str = EVIDENCE_VERSION
    retriever: str | None


class AssistantLatency(_Frozen):
    understanding_ms: float
    retrieval_ms: float | None
    generation_ms: float | None
    total_ms: float


class AssistantResponse(_Frozen):
    outcome: Outcome
    notice: str = Field(description="Fixed, server-written explanation of the outcome.")
    clarification: Clarification | None
    abstain_reason: AbstainReason | None
    summary: str | None = Field(
        description="Server-rendered overview of an answer (policy asst-policy-3 and later; it "
        "was model-written before). Never model text; null unless the outcome is `answer`."
    )
    recommendations: tuple[Recommendation, ...]
    evidence: tuple[EvidenceProduct, ...] = Field(
        description="The retrieved catalog records (V3 search results) the answer may cite."
    )
    generation: GenerationInfo
    versions: Versions
    latency_ms: AssistantLatency


class AssistantStatus(_Frozen):
    """Public, secret-free configuration facts for the UI (no model or key details)."""

    generation_enabled: bool = Field(
        description="False when answer generation is turned off on this server (the default)."
    )
    provider_notice: str | None = Field(
        default=None,
        description="When generation is on: the provider privacy and use notice to display.",
    )
    geolocation_attribution: str | None = Field(
        default=None, description="When generation is on: the required dataset attribution text."
    )
    geolocation_attribution_url: str | None = None


class AssistantUnavailable(_Frozen):
    detail: Literal["assistant unavailable"]
