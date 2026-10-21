"""Inferred listing decision history (Milestone 14).

Four append-only tables, separate from the M13 seller data they are derived from:

* `listing_decision_runs`: one recorded decision for one M13 normalization run (foreign key, plus
  a copy of that run's `output_sha256`): the `jl-1` question set and state hash, the provider and
  its telemetry and fallback/rejection exactly as returned, the routing policy version and
  digest, the route, and hashes of the canonical outcome and routing decision.
* `listing_decision_proposals`: the provider's proposals in outcome order (category,
  subcategory, then attribute support by claim ordinal), raw answer and provider confidence
  kept. `gate_confidence` is always NULL: no calibration exists.
* `listing_decision_probabilities`: each proposal's provider probabilities exactly as returned,
  one row per label (`label_ordinal` is its position in the question's label set), with
  `is_top` marking the labels sharing the highest probability.
* `listing_decision_reasons`: the routing reasons in canonical order.

A decision is an inference, never seller data: nothing here changes an M13 row, and no route is
a rejection or a claim that a human reviewed anything. UPDATE, DELETE and TRUNCATE are blocked by
triggers (migration 0006). Enumerated values are TEXT + named CHECK constraints whose values come
from the M14 contracts; the migration repeats them literally and a test keeps both identical.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ecommerce_search.db.base import Base
from ecommerce_search.decision.listing_models import ListingDecisionOutcome, RejectionCode
from ecommerce_search.decision.listing_routing import Route
from ecommerce_search.decision.provider import JevFallbackReason
from ecommerce_search.models.catalog import SHA256_FORMAT, in_values

PROVIDERS: tuple[str, ...] = get_args(ListingDecisionOutcome.model_fields["provider"].annotation)
USAGES: tuple[str, ...] = tuple(  # `usage` is `Literal[...] | None`
    v
    for arg in get_args(ListingDecisionOutcome.model_fields["usage"].annotation)
    for v in get_args(arg)
)
FALLBACK_REASONS: tuple[str, ...] = get_args(JevFallbackReason)
REJECTION_CODES: tuple[str, ...] = get_args(RejectionCode)
DECISION_TYPES = ("category", "subcategory", "attribute_support")
REJECTION_QUESTION_PATTERN = r"^(category|subcategory|attribute_support\[[0-9]+\])$"


def literal_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


class ListingDecisionRun(Base):
    __tablename__ = "listing_decision_runs"
    __table_args__ = (
        CheckConstraint(
            f"normalization_output_sha256 {SHA256_FORMAT}",
            name="normalization_output_sha256_format",
        ),
        CheckConstraint("btrim(question_set_version) <> ''", name="question_set_version_not_blank"),
        CheckConstraint(f"question_set_digest {SHA256_FORMAT}", name="question_set_digest_format"),
        CheckConstraint(f"state_sha256 {SHA256_FORMAT}", name="state_sha256_format"),
        CheckConstraint(literal_in("provider", PROVIDERS), name="provider_valid"),
        CheckConstraint(
            "latency_ms IS NULL OR (latency_ms >= 0 AND latency_ms < 'Infinity'::float8)",
            name="latency_ms_valid",
        ),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0)",
            name="tokens_nonneg",
        ),
        CheckConstraint("usage IS NULL OR " + literal_in("usage", USAGES), name="usage_valid"),
        CheckConstraint(
            "fallback_reason IS NULL OR " + literal_in("fallback_reason", FALLBACK_REASONS),
            name="fallback_reason_valid",
        ),
        CheckConstraint(
            "rejection IS NULL OR ("
            + literal_in("rejection", REJECTION_CODES)
            + " AND fallback_reason = 'invalid_response')",
            name="rejection_valid",
        ),
        CheckConstraint(
            "rejection_call IS NULL OR (rejection_call IN ('A', 'B') AND rejection IS NOT NULL)",
            name="rejection_call_valid",
        ),
        CheckConstraint(
            "rejection_question IS NULL OR (rejection IS NOT NULL AND rejection_question ~ "
            f"'{REJECTION_QUESTION_PATTERN}')",
            name="rejection_question_valid",
        ),
        CheckConstraint(
            "provider <> 'deterministic' OR (model IS NULL AND response_model IS NULL AND "
            "request_id IS NULL AND latency_ms IS NULL AND input_tokens IS NULL AND "
            "output_tokens IS NULL AND usage IS NULL AND fallback_reason IS NULL AND "
            "rejection IS NULL AND proposal_count = 0)",
            name="deterministic_has_no_telemetry",
        ),
        CheckConstraint(
            "provider <> 'jev' OR (model IS NOT NULL AND latency_ms IS NOT NULL)",
            name="jev_records_model_and_latency",
        ),
        CheckConstraint(
            "fallback_reason IS NULL OR proposal_count = 0", name="fallback_has_no_proposals"
        ),
        CheckConstraint("btrim(policy_version) <> ''", name="policy_version_not_blank"),
        CheckConstraint(f"policy_digest {SHA256_FORMAT}", name="policy_digest_format"),
        CheckConstraint(in_values("route", Route), name="route_valid"),
        CheckConstraint(in_values("deterministic_route", Route), name="deterministic_route_valid"),
        CheckConstraint(
            "policy_version <> 'lr-1' OR "
            "(route <> 'auto_accept' AND deterministic_route <> 'auto_accept')",
            name="lr1_no_auto_accept",
        ),
        CheckConstraint("proposal_count >= 0", name="proposal_count_nonneg"),
        CheckConstraint("reason_count >= 1", name="reason_count_positive"),
        CheckConstraint(f"outcome_sha256 {SHA256_FORMAT}", name="outcome_sha256_format"),
        CheckConstraint(f"decision_sha256 {SHA256_FORMAT}", name="decision_sha256_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    normalization_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "listing_normalization_runs.id",
            name="fk_listing_decision_runs_normalization_run_id",
        ),
        nullable=False,
    )
    normalization_output_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    question_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    question_set_digest: Mapped[str] = mapped_column(Text, nullable=False)
    state_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Double, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_call: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_digest: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    deterministic_route: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    decision_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ListingDecisionProposal(Base):
    __tablename__ = "listing_decision_proposals"
    __table_args__ = (
        UniqueConstraint(
            "decision_run_id", "ordinal", name="uq_listing_decision_proposals_run_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint(literal_in("decision_type", DECISION_TYPES), name="decision_type_valid"),
        CheckConstraint(
            "(decision_type = 'attribute_support') = (claim_ordinal IS NOT NULL) AND "
            "(claim_ordinal IS NULL OR claim_ordinal >= 0)",
            name="claim_ordinal_only_for_support",
        ),
        CheckConstraint("raw_choice <> ''", name="raw_choice_not_empty"),
        CheckConstraint(
            "provider_confidence >= 0 AND provider_confidence <= 1",
            name="provider_confidence_range",
        ),
        CheckConstraint("gate_confidence IS NULL", name="gate_confidence_unset"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    decision_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "listing_decision_runs.id", name="fk_listing_decision_proposals_decision_run_id"
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_type: Mapped[str] = mapped_column(Text, nullable=False)
    claim_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_choice: Mapped[str] = mapped_column(Text, nullable=False)
    provider_confidence: Mapped[float] = mapped_column(Double, nullable=False)
    gate_confidence: Mapped[float | None] = mapped_column(Double, nullable=True)


class ListingDecisionProbability(Base):
    __tablename__ = "listing_decision_probabilities"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "label_ordinal",
            name="uq_listing_decision_probabilities_proposal_label_ordinal",
        ),
        UniqueConstraint(
            "proposal_id", "label", name="uq_listing_decision_probabilities_proposal_label"
        ),
        CheckConstraint("label_ordinal >= 0", name="label_ordinal_nonneg"),
        CheckConstraint("label <> ''", name="label_not_empty"),
        CheckConstraint("probability >= 0 AND probability <= 1", name="probability_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "listing_decision_proposals.id",
            name="fk_listing_decision_probabilities_proposal_id",
        ),
        nullable=False,
    )
    label_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    probability: Mapped[float] = mapped_column(Double, nullable=False)
    is_top: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ListingDecisionReason(Base):
    __tablename__ = "listing_decision_reasons"
    __table_args__ = (
        UniqueConstraint(
            "decision_run_id", "ordinal", name="uq_listing_decision_reasons_run_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint("code <> ''", name="code_not_empty"),
        CheckConstraint(in_values("route", Route), name="route_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    decision_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("listing_decision_runs.id", name="fk_listing_decision_reasons_decision_run_id"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
