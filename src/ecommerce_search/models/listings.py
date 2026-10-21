"""Seller listing normalization history (Milestone 13).

Four append-only tables, separate from the catalog and the search/embedding indexes:

* `listing_versions`: the exact accepted raw bytes of one seller listing, per identity
  (`seller_id` + `seller_listing_ref`, compared exactly) and consecutive version number. The
  database checks that `raw_sha256` is the SHA-256 of `raw_bytes`, and a trigger requires
  versions to start at 1, increase by one and differ from the previous raw input.
* `listing_normalization_runs`: one normalization of one version under one rules version, with
  the rules digest, taxonomy version and output hash recorded as they were at the time.
* `listing_candidates` / `listing_findings`: the run's candidates and findings in canonical
  order (`ordinal`), with their evidence as recorded.

UPDATE, DELETE and TRUNCATE are blocked by triggers (migration 0005); dropping the tables in a
downgrade is not. Nothing here writes to, or references, catalog, search or embedding tables.

Enumerated values are TEXT + named CHECK constraints whose values come from the M13 contracts;
the migration repeats them literally and a test keeps both identical.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ecommerce_search.db.base import Base
from ecommerce_search.models.catalog import SHA256_FORMAT, in_values
from ecommerce_search.normalization.intake import IDENTITY_MAX_LENGTH, MAX_RAW_BYTES
from ecommerce_search.normalization.models import FindingKind, Outcome
from ecommerce_search.normalization.rules import Basis, ValueType


class ListingVersion(Base):
    __tablename__ = "listing_versions"
    __table_args__ = (
        UniqueConstraint(
            "seller_id",
            "seller_listing_ref",
            "version_number",
            name="uq_listing_versions_identity_version",
        ),
        CheckConstraint(
            f"char_length(seller_id) BETWEEN 1 AND {IDENTITY_MAX_LENGTH}",
            name="seller_id_length",
        ),
        CheckConstraint(
            f"char_length(seller_listing_ref) BETWEEN 1 AND {IDENTITY_MAX_LENGTH}",
            name="seller_listing_ref_length",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            f"octet_length(raw_bytes) BETWEEN 1 AND {MAX_RAW_BYTES}", name="raw_bytes_size"
        ),
        CheckConstraint(f"raw_sha256 {SHA256_FORMAT}", name="raw_sha256_format"),
        CheckConstraint(
            "raw_sha256 = encode(sha256(raw_bytes), 'hex')", name="raw_sha256_matches_bytes"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    seller_listing_ref: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # exactly as accepted
    raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ListingNormalizationRun(Base):
    __tablename__ = "listing_normalization_runs"
    __table_args__ = (
        UniqueConstraint(
            "listing_version_id",
            "rules_version",
            name="uq_listing_normalization_runs_version_rules",
        ),
        CheckConstraint("btrim(rules_version) <> ''", name="rules_version_not_blank"),
        CheckConstraint(f"rules_digest {SHA256_FORMAT}", name="rules_digest_format"),
        CheckConstraint("btrim(taxonomy_version) <> ''", name="taxonomy_version_not_blank"),
        CheckConstraint(f"output_sha256 {SHA256_FORMAT}", name="output_sha256_format"),
        CheckConstraint("candidate_count >= 0", name="candidate_count_nonneg"),
        CheckConstraint("finding_count >= 0", name="finding_count_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    listing_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("listing_versions.id", name="fk_listing_normalization_runs_version_id"),
        nullable=False,
    )
    rules_version: Mapped[str] = mapped_column(Text, nullable=False)
    rules_digest: Mapped[str] = mapped_column(Text, nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(Text, nullable=False)
    output_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ListingCandidate(Base):
    __tablename__ = "listing_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_listing_candidates_run_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint("field <> ''", name="field_not_empty"),
        CheckConstraint(in_values("basis", Basis), name="basis_valid"),
        CheckConstraint("source_key <> ''", name="source_key_not_empty"),
        CheckConstraint("raw_text <> ''", name="raw_text_not_empty"),
        CheckConstraint(
            "(span_start IS NULL AND span_end IS NULL) OR "
            "(span_start >= 0 AND span_end > span_start)",
            name="span_valid",
        ),
        CheckConstraint(in_values("outcome", Outcome), name="outcome_valid"),
        CheckConstraint(
            "value_type IS NULL OR " + in_values("value_type", ValueType), name="value_type_valid"
        ),
        CheckConstraint(
            "outcome = 'normalized' OR "
            "(normalized_value IS NULL AND value_type IS NULL AND unit IS NULL)",
            name="value_only_when_normalized",
        ),
        CheckConstraint(
            "outcome <> 'normalized' OR (normalized_value IS NOT NULL AND value_type IS NOT NULL)",
            name="normalized_has_value",
        ),
        CheckConstraint("rule_id <> ''", name="rule_id_not_empty"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("listing_normalization_runs.id", name="fk_listing_candidates_run_id"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)


class ListingFinding(Base):
    __tablename__ = "listing_findings"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_listing_findings_run_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonneg"),
        CheckConstraint(in_values("kind", FindingKind), name="kind_valid"),
        CheckConstraint("message <> ''", name="message_not_empty"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("listing_normalization_runs.id", name="fk_listing_findings_run_id"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_field: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
