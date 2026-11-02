"""`return-classification-1`: typed input and output of return-reason classification.

**Input** keeps the M18 kinds apart. Customer statements (the reason code, return comments and
support complaints) and inspection observations are separate fields, and text is held exactly as
given: matching works on a normalized copy (`text.py`) and never changes the input.

**Output** is an inferred layer. A prediction cites the evidence records it came from and the
rule that matched; it does not copy statements or observations, except the original substring a
text rule matched. `suggested_responsible_areas` is a routing hint, not a fault assignment.
`tier` is a rule-confidence tier, not a probability. There is no refund, fault, abuse,
root-cause or corrective-action field.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecommerce_search.returns.classification.taxonomy import (
    TAXONOMY_VERSION,
    ResponsibleArea,
    ReturnCategory,
)
from ecommerce_search.returns.contracts import (
    ComplaintTopic,
    EvidenceType,
    FreeText,
    InspectionCondition,
    InspectionObservation,
    ReasonCode,
    RecordId,
    Return,
    ReturnComment,
    SupportComplaint,
)

CONTRACT_VERSION = "return-classification-1"
RULES_VERSION = "return-baseline-rules-1"
METHOD = "deterministic_baseline"
STATEMENTS_MAX = 50
INSPECTIONS_MAX = 20


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


# ---- input ---------------------------------------------------------------------------------


class CustomerStatement(_Model):
    """A return comment or support complaint, as the customer wrote it."""

    evidence_type: Literal[EvidenceType.RETURN_COMMENT, EvidenceType.SUPPORT_COMPLAINT]
    evidence_id: RecordId
    text: FreeText
    customer_topic: ComplaintTopic | None = None

    @model_validator(mode="after")
    def _topic(self) -> Self:
        if self.customer_topic is not None and self.evidence_type is EvidenceType.RETURN_COMMENT:
            raise ValueError("customer_topic is only defined for support complaints")
        return self


class InspectionInput(_Model):
    """What an inspector saw. The free-text note is not used by the Part 1 baseline."""

    observation_id: RecordId
    condition: InspectionCondition


class ClassificationInput(_Model):
    return_id: RecordId
    customer_reason_code: ReasonCode
    customer_statements: Annotated[
        tuple[CustomerStatement, ...], Field(max_length=STATEMENTS_MAX)
    ] = ()
    inspection_observations: Annotated[
        tuple[InspectionInput, ...], Field(max_length=INSPECTIONS_MAX)
    ] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        keys = [(s.evidence_type, s.evidence_id) for s in self.customer_statements]
        ids = [o.observation_id for o in self.inspection_observations]
        if len(set(keys)) != len(keys) or len(set(ids)) != len(ids):
            raise ValueError("evidence IDs must be unique per type")
        return self


def input_from_records(
    record: Return,
    comments: Sequence[ReturnComment] = (),
    inspections: Sequence[InspectionObservation] = (),
    complaints: Sequence[SupportComplaint] = (),
) -> ClassificationInput:
    """Build an input from M18 records. Which complaints belong to a return is the caller's
    choice; comments and inspections must name this return."""
    if any(c.return_id != record.return_id for c in (*comments, *inspections)):
        raise ValueError("a comment or inspection belongs to another return")
    statements = [
        CustomerStatement(
            evidence_type=EvidenceType.RETURN_COMMENT, evidence_id=c.comment_id, text=c.text
        )
        for c in comments
    ] + [
        CustomerStatement(
            evidence_type=EvidenceType.SUPPORT_COMPLAINT,
            evidence_id=c.complaint_id,
            text=c.text,
            customer_topic=c.customer_topic,
        )
        for c in complaints
    ]
    return ClassificationInput(
        return_id=record.return_id,
        customer_reason_code=record.customer_reason_code,
        customer_statements=tuple(statements),
        inspection_observations=tuple(
            InspectionInput(observation_id=o.observation_id, condition=o.condition)
            for o in inspections
        ),
    )


# ---- output --------------------------------------------------------------------------------


class SourceKind(StrEnum):
    REASON_CODE = "reason_code"
    COMPLAINT_TOPIC = "complaint_topic"
    CUSTOMER_TEXT = "customer_text"
    INSPECTION = "inspection"


class MatchKind(StrEnum):
    STRUCTURED = "structured"  # an enumerated field value
    EXACT = "exact"  # every token equal after matching normalization
    FUZZY = "fuzzy"  # at least one token within the bounded misspelling distance


class ConfidenceTier(StrEnum):
    """A rule tier from source agreement. Not a probability and not calibrated."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceRef(_Model):
    source_kind: SourceKind
    evidence_type: EvidenceType
    evidence_id: str
    field: Literal["customer_reason_code", "customer_topic", "text", "condition"]
    rule_id: str
    match: MatchKind
    value: str | None = None  # the enumerated value, for structured fields
    span: tuple[int, int] | None = None  # code-point offsets into the original text
    matched_text: str | None = None  # the original substring at `span`, unchanged


class Resolution(StrEnum):
    SINGLE_CATEGORY = "single_category"
    # The evidence supports a concern shared by the candidates (for example "a defect") but
    # not any one of them: an abstention between alternatives, not several predictions.
    UNRESOLVED_CANDIDATES = "unresolved_candidates"


class CategoryPrediction(_Model):
    """One supported concern: a single category, or unresolved candidates.

    `tier` rates the support for the concern as a whole. With unresolved candidates it says
    nothing about any one candidate, and no candidate is asserted.
    """

    resolution: Resolution
    candidates: tuple[ReturnCategory, ...]
    category: ReturnCategory | None  # set only when `resolution` is `single_category`
    tier: ConfidenceTier
    suggested_responsible_areas: tuple[ResponsibleArea, ...]
    supporting: tuple[EvidenceRef, ...]
    contradicting: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def _resolution(self) -> Self:
        single = self.resolution is Resolution.SINGLE_CATEGORY
        if single != (len(self.candidates) == 1) or single != (self.category is not None):
            raise ValueError("resolution, candidates and category disagree")
        if self.category is not None and self.category != self.candidates[0]:
            raise ValueError("category must be the single candidate")
        return self


class SuppressedMatch(_Model):
    """A text match that did not become a signal: negated, or a misspelling with no product
    context nearby (`fuzzy_without_context`)."""

    ref: EvidenceRef
    categories: tuple[ReturnCategory, ...]
    reason: Literal["negated", "fuzzy_without_context"]


class InspectionEffect(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    UNCORROBORATED = "uncorroborated"  # would support a category no customer statement gives


class InspectionFinding(_Model):
    ref: EvidenceRef
    effect: InspectionEffect
    categories: tuple[ReturnCategory, ...]


class ReviewReason(StrEnum):
    """Fixed reasons for routing a return to a person. Routing hides or rejects nothing."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LOW_CONFIDENCE = "low_confidence"
    UNSUPPORTED_DISTINCTION = "unsupported_distinction"
    CONFLICTING_STATEMENTS = "conflicting_statements"
    STATEMENT_INSPECTION_CONFLICT = "statement_inspection_conflict"
    CONFLICTING_INSPECTIONS = "conflicting_inspections"
    UNCORROBORATED_ITEM_DIFFERENCE = "uncorroborated_item_difference"


class ClassificationStatus(StrEnum):
    CLASSIFIED = "classified"
    ABSTAINED = "abstained"


class ClassificationResult(_Model):
    contract_version: Literal["return-classification-1"] = CONTRACT_VERSION
    taxonomy_version: Literal["return-categories-1"] = TAXONOMY_VERSION
    rules_version: Literal["return-baseline-rules-1"] = RULES_VERSION
    method: Literal["deterministic_baseline"] = METHOD
    tier_semantics: Literal["rule_tier_not_probability"] = "rule_tier_not_probability"
    return_id: str
    status: ClassificationStatus
    predictions: tuple[CategoryPrediction, ...]
    review_reasons: tuple[ReviewReason, ...]
    needs_review: bool
    inspection_findings: tuple[InspectionFinding, ...]
    suppressed_matches: tuple[SuppressedMatch, ...]
