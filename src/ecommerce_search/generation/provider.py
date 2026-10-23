"""GenerationProvider interface and the grounded-draft output contract (Milestone 16, ADR-009).

A provider receives fixed instructions and one user message (the shopper request plus the
retrieved catalog records as delimited data) and returns a `GenerationResult`: either a
Pydantic-validated `GroundedDraft` or a fixed failure code. Providers never raise for remote
failures, never retry on their own and never log prompt or response text.

The draft is deliberately narrow: the model may only choose answer or abstain, select retrieved
product IDs and name which catalog fields support each selection. It writes no free text, so no
model-written claim can reach a user; every displayed explanation is rendered by the assistant
from catalog values (`assistant/explain.py`).
"""

from dataclasses import dataclass
from typing import Literal, Protocol, get_args, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# Catalog fields a selection may cite. `description` is excluded: it is long seller text.
FactField = Literal[
    "title",
    "brand",
    "category",
    "subcategory",
    "price",
    "rating",
    "review_count",
    "availability",
    "ram_gb",
    "storage_gb",
    "storage_type",
    "storage_interface",
]
FACT_FIELDS: tuple[str, ...] = get_args(FactField)

MAX_SELECTED_PRODUCTS = 3
MAX_FACTS_PER_PRODUCT = 4

GenerationFailure = Literal[
    "timeout",
    "transport_error",
    "auth_error",
    "rate_limited",
    "server_error",
    "request_rejected",
    "refused",
    "truncated",
    "invalid_response",
    # Refused by the local usage guard (`generation/guard.py`); nothing was sent.
    "local_concurrency_limit",
    "local_rate_limit",
    "local_daily_cap",
]
# Diagnostics only: an allowlisted origin of a failure (never raw error text). A timeout from the
# HTTP client and an HTTP 408/504 from the server share the code `timeout`; this tells them apart.
FailureDetail = Literal[
    "client_timeout",
    "client_transport_error",
    "http_400",
    "http_401",
    "http_403",
    "http_404",
    "http_408",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
    "http_other",
    "no_candidates",
    "no_candidate_text",
    "unparsable_json",
    "schema_invalid",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SelectedProduct(_Frozen):
    product_id: str = Field(min_length=1, max_length=64)
    relevant_facts: tuple[FactField, ...] = Field(min_length=1, max_length=MAX_FACTS_PER_PRODUCT)


class GroundedDraft(_Frozen):
    """The only structure a provider may return. Semantic checks live in `assistant/validate.py`."""

    outcome: Literal["answer", "abstain"]
    selected: tuple[SelectedProduct, ...] = Field(max_length=MAX_SELECTED_PRODUCTS)


@dataclass(frozen=True)
class GenerationRequest:
    instructions: str
    user_content: str
    max_output_tokens: int


@dataclass(frozen=True)
class GenerationResult:
    """`draft` xor `failure`. Token counts are None when the provider did not report them:
    unknown, not zero."""

    draft: GroundedDraft | None
    failure: GenerationFailure | None
    input_tokens: int | None
    output_tokens: int | None
    failure_detail: FailureDetail | None = None

    def __post_init__(self) -> None:
        if (self.draft is None) == (self.failure is None):
            raise ValueError("exactly one of draft and failure must be set")
        if self.failure_detail is not None and self.failure is None:
            raise ValueError("failure_detail is only set with a failure")


@runtime_checkable
class GenerationProvider(Protocol):
    provider: str
    model: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...
