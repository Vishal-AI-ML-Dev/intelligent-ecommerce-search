"""Inferred return-reason classifications and complaint groups (Milestone 19 Part 2).

Eight append-only tables, separate from the M18 return data they are derived from. Nothing here
changes an M18 row, and nothing is a human label, a decision, a fault or a root cause.

* `return_classification_runs`: one run of one classifier version set (contract, taxonomy,
  rules, method, complaint-link policy) over one M18 dataset version. `run_key_sha256` is
  unique, so the same versions on the same inputs are recorded once.
* `return_classifications`: one result per return in a run: status, review reasons and hashes
  of the exact input and result.
* `return_classification_inputs`: the source records each result was built from, by type, ID
  and M18 `record_sha256`.
* `return_category_predictions`: the predictions in result order, with resolution, candidates,
  rule tier (not a probability) and suggested areas (routing hints, not fault).
* `return_classification_evidence`: supporting and contradicting evidence per prediction, plus
  inspection findings and suppressed matches. Text evidence stores code-point offsets into the
  source record, not a copy of the text.
* `return_complaint_group_runs`, `return_complaint_groups`, `return_complaint_group_members`:
  per-SKU groupings of a classification run. A group is a grouping, not a root cause; only
  groups with at least the run's `min_pattern_size` qualifying members (medium or high tier,
  not contradicted, no conflict on the return) are flagged `is_pattern`; each member row says
  whether it `counts_toward_pattern`.

UPDATE, DELETE and TRUNCATE are blocked by triggers (migration 0008). Enumerated values are TEXT
or TEXT[] with named CHECK constraints built from the Part 1 contracts; the migration repeats
them literally and a test keeps both identical. Stored predictions never carry
`suspected_abuse` or `insufficient_evidence`.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ecommerce_search.db.base import Base
from ecommerce_search.models.catalog import SHA256_FORMAT, in_values
from ecommerce_search.returns.classification.contracts import (
    ClassificationStatus,
    ConfidenceTier,
    InspectionEffect,
    MatchKind,
    Resolution,
    ReviewReason,
    SourceKind,
)
from ecommerce_search.returns.classification.taxonomy import (
    NOT_PREDICTED_BY_BASELINE,
    ResponsibleArea,
    ReturnCategory,
)

INPUT_EVIDENCE_TYPES = ("return", "return_comment", "support_complaint", "inspection_observation")
EVIDENCE_FIELDS = ("customer_reason_code", "customer_topic", "text", "condition")
EVIDENCE_KINDS = ("supporting", "contradicting", "inspection_finding", "suppressed_match")
SUPPRESSION_REASONS = ("negated", "fuzzy_without_context")
NEVER_STORED = tuple(c.value for c in ReturnCategory if c in NOT_PREDICTED_BY_BASELINE)


def _values(enum_cls: type[Enum] | tuple[str, ...]) -> str:
    items = enum_cls if isinstance(enum_cls, tuple) else tuple(m.value for m in enum_cls)
    return ", ".join(f"'{v}'" for v in items)


def text_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({_values(values)})"


def array_within(column: str, values: type[Enum] | tuple[str, ...]) -> str:
    return f"{column} <@ ARRAY[{_values(values)}]::text[]"


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReturnClassificationRun(Base):
    __tablename__ = "return_classification_runs"
    __table_args__ = (
        UniqueConstraint("run_key_sha256", name="uq_return_classification_runs_run_key_sha256"),
        CheckConstraint("btrim(contract_version) <> ''", name="contract_version_not_blank"),
        CheckConstraint("btrim(taxonomy_version) <> ''", name="taxonomy_version_not_blank"),
        CheckConstraint("btrim(rules_version) <> ''", name="rules_version_not_blank"),
        CheckConstraint("method IN ('deterministic_baseline')", name="method_valid"),
        CheckConstraint(
            "tier_semantics IN ('rule_tier_not_probability')", name="tier_semantics_valid"
        ),
        CheckConstraint(
            "btrim(complaint_link_policy) <> ''", name="complaint_link_policy_not_blank"
        ),
        CheckConstraint(f"input_sha256 {SHA256_FORMAT}", name="input_sha256_format"),
        CheckConstraint(f"run_key_sha256 {SHA256_FORMAT}", name="run_key_sha256_format"),
        CheckConstraint(
            "return_count >= 0 AND classified_count >= 0 AND abstained_count >= 0 AND "
            "needs_review_count >= 0 AND classified_count + abstained_count = return_count AND "
            "needs_review_count <= return_count",
            name="counts_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dataset_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("return_datasets.id", name="fk_return_classification_runs_dataset_pk"),
        nullable=False,
    )
    contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(Text, nullable=False)
    rules_version: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    tier_semantics: Mapped[str] = mapped_column(Text, nullable=False)
    complaint_link_policy: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    run_key_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    return_count: Mapped[int] = mapped_column(Integer, nullable=False)
    classified_count: Mapped[int] = mapped_column(Integer, nullable=False)
    abstained_count: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class ReturnClassification(Base):
    __tablename__ = "return_classifications"
    __table_args__ = (
        UniqueConstraint("run_id", "return_pk", name="uq_return_classifications_run_return"),
        CheckConstraint(in_values("status", ClassificationStatus), name="status_valid"),
        CheckConstraint(array_within("review_reasons", ReviewReason), name="review_reasons_valid"),
        CheckConstraint(
            "needs_review = (cardinality(review_reasons) > 0)", name="needs_review_consistent"
        ),
        CheckConstraint(
            "prediction_count >= 0 AND (status = 'abstained') = (prediction_count = 0)",
            name="prediction_count_consistent",
        ),
        CheckConstraint(f"input_sha256 {SHA256_FORMAT}", name="input_sha256_format"),
        CheckConstraint(f"result_sha256 {SHA256_FORMAT}", name="result_sha256_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("return_classification_runs.id", name="fk_return_classifications_run_id"),
        nullable=False,
    )
    return_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("returns.id", name="fk_return_classifications_return_pk"),
        nullable=False,
    )
    return_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    prediction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    result_sha256: Mapped[str] = mapped_column(Text, nullable=False)


class ReturnClassificationInput(Base):
    __tablename__ = "return_classification_inputs"
    __table_args__ = (
        UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_classification_inputs_ordinal"
        ),
        UniqueConstraint(
            "classification_id",
            "evidence_type",
            "evidence_id",
            name="uq_return_classification_inputs_record",
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint(text_in("evidence_type", INPUT_EVIDENCE_TYPES), name="evidence_type_valid"),
        CheckConstraint("evidence_id <> ''", name="evidence_id_not_empty"),
        CheckConstraint(f"record_sha256 {SHA256_FORMAT}", name="record_sha256_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    classification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "return_classifications.id", name="fk_return_classification_inputs_classification_id"
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_id: Mapped[str] = mapped_column(Text, nullable=False)
    record_sha256: Mapped[str] = mapped_column(Text, nullable=False)


class ReturnCategoryPrediction(Base):
    __tablename__ = "return_category_predictions"
    __table_args__ = (
        UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_category_predictions_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint(in_values("resolution", Resolution), name="resolution_valid"),
        CheckConstraint(
            "cardinality(candidates) >= 1 AND " + array_within("candidates", ReturnCategory),
            name="candidates_valid",
        ),
        CheckConstraint(
            f"NOT (candidates && ARRAY[{_values(NEVER_STORED)}]::text[])",
            name="candidates_never_abuse_or_insufficient",
        ),
        CheckConstraint(
            "(resolution = 'single_category') = (category IS NOT NULL) AND "
            "(resolution = 'single_category') = (cardinality(candidates) = 1) AND "
            "(category IS NULL OR category = candidates[1])",
            name="resolution_consistent",
        ),
        CheckConstraint(in_values("tier", ConfidenceTier), name="tier_valid"),
        CheckConstraint(
            "cardinality(suggested_areas) >= 1 AND "
            + array_within("suggested_areas", ResponsibleArea),
            name="suggested_areas_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    classification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "return_classifications.id", name="fk_return_category_predictions_classification_id"
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_areas: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)


class ReturnClassificationEvidence(Base):
    __tablename__ = "return_classification_evidence"
    __table_args__ = (
        UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_classification_evidence_ordinal"
        ),
        ForeignKeyConstraint(
            ["classification_id", "prediction_ordinal"],
            [
                "return_category_predictions.classification_id",
                "return_category_predictions.ordinal",
            ],
            name="fk_return_classification_evidence_prediction",
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint(text_in("kind", EVIDENCE_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind IN ('supporting', 'contradicting')) = (prediction_ordinal IS NOT NULL)",
            name="prediction_ordinal_by_kind",
        ),
        CheckConstraint(
            "(kind = 'inspection_finding') = (effect IS NOT NULL) AND "
            "(effect IS NULL OR " + in_values("effect", InspectionEffect) + ")",
            name="effect_by_kind",
        ),
        CheckConstraint(
            "(kind = 'suppressed_match') = (suppression_reason IS NOT NULL) AND "
            "(suppression_reason IS NULL OR "
            + text_in("suppression_reason", SUPPRESSION_REASONS)
            + ")",
            name="suppression_reason_by_kind",
        ),
        CheckConstraint(
            "(kind IN ('inspection_finding', 'suppressed_match')) = (categories IS NOT NULL) AND "
            "(categories IS NULL OR " + array_within("categories", ReturnCategory) + ")",
            name="categories_by_kind",
        ),
        CheckConstraint(in_values("source_kind", SourceKind), name="source_kind_valid"),
        CheckConstraint(text_in("evidence_type", INPUT_EVIDENCE_TYPES), name="evidence_type_valid"),
        CheckConstraint("evidence_id <> ''", name="evidence_id_not_empty"),
        CheckConstraint(text_in("field", EVIDENCE_FIELDS), name="field_valid"),
        CheckConstraint("rule_id <> ''", name="rule_id_not_empty"),
        CheckConstraint(in_values("match", MatchKind), name="match_valid"),
        CheckConstraint(
            "(field = 'text') = (span_start IS NOT NULL) AND "
            "(span_start IS NULL) = (span_end IS NULL) AND "
            "(span_start IS NULL OR (span_start >= 0 AND span_end > span_start)) AND "
            "(field = 'text') = (value IS NULL)",
            name="span_or_value",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    classification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "return_classifications.id", name="fk_return_classification_evidence_classification_id"
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    suppression_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_id: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    match: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReturnComplaintGroupRun(Base):
    __tablename__ = "return_complaint_group_runs"
    __table_args__ = (
        UniqueConstraint("run_key_sha256", name="uq_return_complaint_group_runs_run_key_sha256"),
        CheckConstraint("btrim(grouping_version) <> ''", name="grouping_version_not_blank"),
        CheckConstraint("method IN ('rule_signature')", name="method_valid"),
        CheckConstraint("min_pattern_size >= 2", name="min_pattern_size_valid"),
        CheckConstraint(f"run_key_sha256 {SHA256_FORMAT}", name="run_key_sha256_format"),
        CheckConstraint(
            "group_count >= 0 AND pattern_count >= 0 AND pattern_count <= group_count AND "
            "grouped_return_count >= 0 AND ungrouped_return_count >= 0",
            name="counts_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    classification_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "return_classification_runs.id",
            name="fk_return_complaint_group_runs_classification_run_id",
        ),
        nullable=False,
    )
    grouping_version: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    min_pattern_size: Mapped[int] = mapped_column(Integer, nullable=False)
    run_key_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pattern_count: Mapped[int] = mapped_column(Integer, nullable=False)
    grouped_return_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ungrouped_return_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class ReturnComplaintGroup(Base):
    __tablename__ = "return_complaint_groups"
    __table_args__ = (
        UniqueConstraint("group_run_id", "ordinal", name="uq_return_complaint_groups_ordinal"),
        UniqueConstraint(
            "group_run_id", "product_id", "signature", name="uq_return_complaint_groups_signature"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint("btrim(product_id) <> ''", name="product_id_not_blank"),
        CheckConstraint("btrim(signature) <> ''", name="signature_not_blank"),
        CheckConstraint(
            "cardinality(candidates) >= 1 AND " + array_within("candidates", ReturnCategory),
            name="candidates_valid",
        ),
        CheckConstraint("member_count >= 1", name="member_count_positive"),
        CheckConstraint(
            "qualifying_member_count >= 0 AND qualifying_member_count <= member_count",
            name="qualifying_member_count_valid",
        ),
        CheckConstraint(
            "NOT is_pattern OR qualifying_member_count >= 2", name="pattern_has_qualifying_members"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    group_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "return_complaint_group_runs.id", name="fk_return_complaint_groups_group_run_id"
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    candidates: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    qualifying_member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_pattern: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ReturnComplaintGroupMember(Base):
    __tablename__ = "return_complaint_group_members"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "classification_id", name="uq_return_complaint_group_members_member"
        ),
        ForeignKeyConstraint(
            ["classification_id", "prediction_ordinal"],
            [
                "return_category_predictions.classification_id",
                "return_category_predictions.ordinal",
            ],
            name="fk_return_complaint_group_members_prediction",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("return_complaint_groups.id", name="fk_return_complaint_group_members_group_id"),
        nullable=False,
    )
    classification_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prediction_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    counts_toward_pattern: Mapped[bool] = mapped_column(Boolean, nullable=False)


RETURN_CLASSIFICATION_TABLES: tuple[str, ...] = (
    "return_classification_runs",
    "return_classifications",
    "return_classification_inputs",
    "return_category_predictions",
    "return_classification_evidence",
    "return_complaint_group_runs",
    "return_complaint_groups",
    "return_complaint_group_members",
)
