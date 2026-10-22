"""GenerationProvider interface and the grounded-draft output contract (Milestone 16, ADR-009).

A provider receives fixed instructions and one user message (the shopper request plus the
retrieved catalog records as delimited data) and returns a `GenerationResult`: either a
Pydantic-validated `GroundedDraft` or a fixed failure code. Providers never raise for remote
failures, never retry on their own and never log prompt or response text.

The draft is deliberately narrow. The model may only select retrieved product IDs, name which
catalog fields support each selection, and write a short summary. Prices, specifications and
availability are never taken from model text: the assistant renders them from catalog records.
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
]
FACT_FIELDS: tuple[str, ...] = get_args(FactField)

MAX_SELECTED_PRODUCTS = 3
MAX_FACTS_PER_PRODUCT = 4
MAX_SUMMARY_LENGTH = 400

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
    summary: str = Field(max_length=MAX_SUMMARY_LENGTH)


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

    def __post_init__(self) -> None:
        if (self.draft is None) == (self.failure is None):
            raise ValueError("exactly one of draft and failure must be set")


@runtime_checkable
class GenerationProvider(Protocol):
    provider: str
    model: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...
