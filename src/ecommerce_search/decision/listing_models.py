"""Listing-decision contracts (Milestone 14, question set `jl-1`). Offline: no SDK, database,
settings or network.

A `ListingDecisionState` is what a listing-decision provider may see, built deterministically from
one validated raw listing and its M13 normalization (`listing_questions.build_listing_state`):

* a reference to the exact M13 run (`raw_sha256`, `output_sha256`, rules and taxonomy versions);
* the seller's raw title and description, whole and unchanged, as untrusted data. Seller
  category, subcategory and every other structured field are never part of the text;
* one attribute-support question per eligible M13 candidate claim, identified by the claim's
  stable ordinal in the canonical `ListingNormalization.candidates` order, its field, canonical
  value and source reference. The claim is what is being checked, not an expected answer, and no
  expected support label exists anywhere in the state;
* every other candidate, listed as omitted with the reason it was not asked. An omitted candidate
  was not assessed.

Proposals keep the provider's raw answer, top labels, probabilities and own confidence.
`gate_confidence` is always None: no calibration exists, and nothing here applies, gates or routes
a proposal. Attribute support uses `supported` / `contradicted` / `not_stated`; a value the text
does not state is `not_stated`, never `contradicted`.
"""

import hashlib
import math
from enum import StrEnum
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecommerce_search.catalog import taxonomy as tx
from ecommerce_search.decision.provider import JevFallbackReason
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.rules import Basis, ValueType

QUESTION_SET_VERSION = "jl-1"
NONE_LABEL = "none"
CATEGORY_QUESTION = "category"
SUBCATEGORY_QUESTION = "subcategory"
TEXT_INPUT = "listing_text"  # model-visible input of the category and subcategory questions
CLAIMS_INPUT = "listing_claims"  # model-visible input of the attribute-support questions

# Non-numeric seller fields whose normalized claims may be checked against the listing text.
# Numeric fields stay deterministic (M13). Excluded on purpose: title and description (they are the
# evidence), category and subcategory (decision targets; never shown to the model), price,
# currency and availability (commercial, not product attributes), camera (megapixel and other
# numeric specifications) and size_system (the unit system of the numeric shoe size).
ATTRIBUTE_ALLOWLIST: tuple[str, ...] = (
    "anc",
    "brand",
    "color",
    "connectivity",
    "gender",
    "gpu",
    "material",
    "operating_system",
    "processor",
    "storage_interface",
    "storage_type",
    "wireless",
)
MAX_ATTRIBUTE_QUESTIONS = 8
NUMERIC_VALUE_TYPES = frozenset({ValueType.INTEGER, ValueType.DECIMAL})

CATEGORY_LABELS: tuple[str, ...] = (*(c.value for c in tx.Category), NONE_LABEL)
SUBCATEGORY_CATEGORY: dict[str, tx.Category] = {
    sub: category for category, subs in tx.SUBCATEGORIES.items() for sub in sorted(subs)
}
SUBCATEGORY_LABELS: tuple[str, ...] = (*SUBCATEGORY_CATEGORY, NONE_LABEL)

if sum(len(s) for s in tx.SUBCATEGORIES.values()) != len(SUBCATEGORY_CATEGORY):
    raise AssertionError("subcategory names must be unique across categories")
if not set(ATTRIBUTE_ALLOWLIST) <= set(nr.FIELD_VALUE_TYPES) or any(
    nr.FIELD_VALUE_TYPES[f] in NUMERIC_VALUE_TYPES for f in ATTRIBUTE_ALLOWLIST
):
    raise AssertionError("the attribute allowlist must name non-numeric listing fields")


def attribute_question_id(ordinal: int) -> str:
    return f"attribute_support[{ordinal}]"


class SupportLabel(StrEnum):
    SUPPORTED = "supported"  # the title or description states this value
    CONTRADICTED = "contradicted"  # the text states a different, incompatible value
    NOT_STATED = "not_stated"  # the text does not state it or does not settle it


class TextStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"  # no such key
    EMPTY = "empty"  # null or whitespace only (M13 empty_value)
    NOT_TEXT = "not_text"  # a number or boolean


class OmissionReason(StrEnum):
    """Why a candidate has no question, checked in this order."""

    TEXT_EXTRACTION = "text_extraction"  # read from title/description: it quotes the evidence
    NOT_NORMALIZED = "not_normalized"  # unsupported, uncertain or invalid: no canonical value
    NUMERIC_FIELD = "numeric_field"  # numeric fields stay deterministic
    NOT_ALLOWLISTED = "not_allowlisted"
    QUESTION_CAP = "question_cap"  # eligible, but MAX_ATTRIBUTE_QUESTIONS were already asked


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_SHA256 = r"^[0-9a-f]{64}$"


class M13RunReference(_Frozen):
    raw_sha256: str = Field(pattern=_SHA256)
    output_sha256: str = Field(pattern=_SHA256)
    rules_version: str = Field(min_length=1)
    rules_digest: str = Field(pattern=_SHA256)
    taxonomy_version: str = Field(min_length=1)


class ListingText(_Frozen):
    """The seller's raw title and description exactly as received (never trimmed or
    truncated); a value is present only when its status is `present`."""

    title: str | None = None
    title_status: TextStatus
    description: str | None = None
    description_status: TextStatus

    @model_validator(mode="after")
    def _status(self) -> Self:
        for value, status in (
            (self.title, self.title_status),
            (self.description, self.description_status),
        ):
            if (status is TextStatus.PRESENT) != (value is not None):
                raise ValueError("a text value is present exactly when its status is present")
            if value is not None and not value.split():
                raise ValueError("a present text value is not blank")
        return self


class ClaimReference(_Frozen):
    """One existing M13 candidate claim: a normalized seller_field value. Its value is the claim
    to be checked, never an expected answer."""

    ordinal: int = Field(ge=0)  # index in the canonical ListingNormalization.candidates order
    field: str
    canonical_value: str = Field(min_length=1)
    basis: Literal[Basis.SELLER_FIELD] = Basis.SELLER_FIELD
    source_key: str = Field(min_length=1)  # the exact raw key
    raw_text: str = Field(min_length=1)  # the exact raw value text
    rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _allowlisted(self) -> Self:
        if self.field not in nr.FIELD_VALUE_TYPES:
            raise ValueError(f"unknown listing field {self.field!r}")
        if nr.FIELD_VALUE_TYPES[self.field] in NUMERIC_VALUE_TYPES:
            raise ValueError(f"{self.field} is numeric; numeric claims are never asked")
        if self.field not in ATTRIBUTE_ALLOWLIST:
            raise ValueError(f"{self.field} is not in the jl-1 attribute allowlist")
        return self


class AttributeQuestion(_Frozen):
    question_id: str
    claim: ClaimReference

    @model_validator(mode="after")
    def _id(self) -> Self:
        if self.question_id != attribute_question_id(self.claim.ordinal):
            raise ValueError("question_id must name the claim ordinal")
        return self


class OmittedCandidate(_Frozen):
    ordinal: int = Field(ge=0)
    field: str
    reason: OmissionReason


class ListingDecisionState(_Frozen):
    question_set_version: Literal["jl-1"] = QUESTION_SET_VERSION
    question_set_digest: str = Field(pattern=_SHA256)
    m13: M13RunReference
    text: ListingText
    candidate_count: int = Field(ge=0)
    attribute_questions: tuple[AttributeQuestion, ...] = ()
    omitted_candidates: tuple[OmittedCandidate, ...] = ()

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        asked = [q.claim.ordinal for q in self.attribute_questions]
        omitted = [o.ordinal for o in self.omitted_candidates]
        if asked != sorted(set(asked)) or omitted != sorted(set(omitted)):
            raise ValueError("questions and omissions are in strictly ascending ordinal order")
        if sorted(asked + omitted) != list(range(self.candidate_count)):
            raise ValueError("every candidate is either asked or omitted, exactly once")
        if len(asked) > MAX_ATTRIBUTE_QUESTIONS:
            raise ValueError(f"at most {MAX_ATTRIBUTE_QUESTIONS} attribute questions")
        capped = [
            o.ordinal for o in self.omitted_candidates if o.reason is OmissionReason.QUESTION_CAP
        ]
        if capped and (len(asked) != MAX_ATTRIBUTE_QUESTIONS or min(capped) < max(asked)):
            raise ValueError("question_cap applies only after the cap is reached, in order")
        for o in self.omitted_candidates:
            if o.reason is OmissionReason.QUESTION_CAP and o.field not in ATTRIBUTE_ALLOWLIST:
                raise ValueError("only allowlisted candidates can be omitted by the cap")
        return self

    def canonical_json(self) -> bytes:
        return nr.canonical_json(self.model_dump(mode="json"))

    def state_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def question_ordinals(self) -> tuple[int, ...]:
        return tuple(q.claim.ordinal for q in self.attribute_questions)


# ---- proposals and outcome ---------------------------------------------------------------


class _Proposal(_Frozen):
    """One validated provider answer. `raw_choice`, `provider_probabilities` and
    `provider_confidence` are kept exactly as returned; `top_labels` lists, in label order, every
    label sharing the highest probability; `label` is set exactly when that top label is unique
    and is not the abstaining label."""

    LABELS: ClassVar[tuple[str, ...]]
    ABSTAIN: ClassVar[str | None]

    label: str | None
    raw_choice: str
    top_labels: tuple[str, ...] = Field(min_length=1)
    provider_probabilities: dict[str, float]
    provider_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    gate_confidence: None = None  # never set before calibration

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        labels = self.LABELS
        if not set(self.provider_probabilities) <= set(labels):
            raise ValueError("probabilities name unknown labels")
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in self.provider_probabilities.values()):
            raise ValueError("probabilities must be finite and within [0, 1]")
        if self.top_labels != tuple(lbl for lbl in labels if lbl in self.top_labels):
            raise ValueError("top labels must be known, unique and in label order")
        if not set(self.top_labels) <= set(self.provider_probabilities):
            raise ValueError("top labels must have probabilities")
        others = [p for k, p in self.provider_probabilities.items() if k not in self.top_labels]
        if others and min(self.provider_probabilities[k] for k in self.top_labels) < max(others):
            raise ValueError("a label outside the top labels has a higher probability")
        if self.raw_choice not in self.top_labels:
            raise ValueError("raw_choice must be one of the top labels")
        unique = len(self.top_labels) == 1 and self.top_labels[0] != self.ABSTAIN
        if self.label != (self.top_labels[0] if unique else None):
            raise ValueError("label is set exactly when the top label is unique and not abstaining")
        return self


class CategoryProposal(_Proposal):
    LABELS = CATEGORY_LABELS
    ABSTAIN = NONE_LABEL
    decision_type: Literal["category"] = "category"


class SubcategoryProposal(_Proposal):
    LABELS = SUBCATEGORY_LABELS
    ABSTAIN = NONE_LABEL
    decision_type: Literal["subcategory"] = "subcategory"


class AttributeSupportProposal(_Proposal):
    """A support answer for the claim with this ordinal. `not_stated` is a real answer."""

    LABELS = tuple(s.value for s in SupportLabel)
    ABSTAIN = None
    decision_type: Literal["attribute_support"] = "attribute_support"
    ordinal: int = Field(ge=0)


# Allowlisted causes of an `invalid_response` fallback, recorded only where the validator
# can tell them apart. None means not recorded (for example outcomes from before this field).
RejectionCode = Literal[
    "sdk_response_validation",  # the SDK rejected the response body against its schema
    "answers_not_mapping",
    "question_set_mismatch",  # a missing, extra, unasked or omitted question
    "response_model_missing",
    "usage_invalid",  # a token count that is not a non-negative integer
    "answer_not_choice",
    "choice_out_of_set",
    "probability_labels_mismatch",
    "probability_out_of_range",
    "probability_sum",
    "confidence_out_of_range",
    "choice_not_top",
    "proposal_contract",  # the validated answers did not form a valid outcome
]


class ListingDecisionOutcome(_Frozen):
    """The answer to one state. The deterministic baseline proposes nothing. A model provider
    either returns every proposal (category, subcategory and one support answer per asked claim)
    with no `fallback_reason`, or no proposal and a `fallback_reason`."""

    provider: Literal["deterministic", "jev"]
    question_set_version: Literal["jl-1"] = QUESTION_SET_VERSION
    state_sha256: str = Field(pattern=_SHA256)
    model: str | None = None
    response_model: str | None = None
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    # Token accounting over the listing's planned calls (M14 runner fix): `complete` when every
    # planned call returned both counts; `partial` when only some did (the tokens are the sum
    # of those calls, and the other calls, for example a failed call B, are unaccounted);
    # `none` when no call did. None for outcomes recorded before this field existed.
    usage: Literal["complete", "partial", "none"] | None = None
    rejection: RejectionCode | None = None  # invalid_response fallbacks only
    rejection_call: Literal["A", "B"] | None = None
    rejection_question: str | None = Field(
        default=None, pattern=r"^(category|subcategory|attribute_support\[[0-9]+\])$"
    )
    fallback_reason: JevFallbackReason | None = None
    category: CategoryProposal | None = None
    subcategory: SubcategoryProposal | None = None
    attribute_support: tuple[AttributeSupportProposal, ...] = ()

    @model_validator(mode="after")
    def _provider_fields(self) -> Self:
        ordinals = [p.ordinal for p in self.attribute_support]
        if ordinals != sorted(set(ordinals)):
            raise ValueError("attribute support is in strictly ascending ordinal order")
        has_any = (self.category, self.subcategory) != (None, None) or bool(self.attribute_support)
        if self.provider == "deterministic":
            telemetry = (
                self.model,
                self.response_model,
                self.latency_ms,
                self.request_id,
                self.input_tokens,
                self.output_tokens,
                self.usage,
                self.rejection,
                self.fallback_reason,
            )
            if has_any or any(v is not None for v in telemetry):
                raise ValueError(
                    "the deterministic baseline carries no proposal or model telemetry"
                )
            return self
        if self.model is None or self.latency_ms is None:
            raise ValueError("a model outcome records its model and latency")
        if self.fallback_reason is not None and has_any:
            raise ValueError("a fallback outcome carries no proposal")
        if self.fallback_reason is None and None in (self.category, self.subcategory):
            raise ValueError("a successful outcome needs the category and subcategory proposals")
        counted = (self.input_tokens, self.output_tokens)
        if self.rejection is not None and self.fallback_reason != "invalid_response":
            raise ValueError("a rejection code is recorded only for invalid_response")
        if self.rejection is None and (
            self.rejection_call is not None or self.rejection_question is not None
        ):
            raise ValueError("a rejection call or question needs a rejection code")
        if self.usage == "none" and counted != (None, None):
            raise ValueError("usage none carries no token counts")
        if self.usage in ("complete", "partial") and None in counted:
            raise ValueError("complete or partial usage carries both token counts")
        return self


class ListingDecisionMismatch(ValueError):
    """An outcome does not answer the given state."""


def validate_outcome(state: ListingDecisionState, outcome: ListingDecisionOutcome) -> None:
    """Raise ListingDecisionMismatch unless `outcome` answers exactly the questions of `state`."""
    if outcome.question_set_version != state.question_set_version:
        raise ListingDecisionMismatch("question set version differs")
    if outcome.state_sha256 != state.state_sha256():
        raise ListingDecisionMismatch("state_sha256 differs")
    answered = tuple(p.ordinal for p in outcome.attribute_support)
    answers_expected = outcome.provider == "jev" and outcome.fallback_reason is None
    if answers_expected and answered != state.question_ordinals():
        raise ListingDecisionMismatch("attribute answers must match the asked ordinals exactly")
