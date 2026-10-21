"""create listing normalization history

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

Creates the Milestone 13 seller-listing history tables: exact raw listing versions,
normalization runs, and their candidates and findings. The DDL is written out literally (no
application imports) so the migration stays frozen when application code changes. The upgrade
creates structure only; rows are written by `python -m ecommerce_search.normalization submit`.
No catalog, search or embedding table is touched, and no index beyond the constraint-backing
ones is created.

History is append-only: triggers block UPDATE, DELETE and TRUNCATE on all four tables, and a
trigger on `listing_versions` requires each identity's versions to start at 1, increase by one
and differ from the previous raw input.

Downgrade drops the four tables (their triggers go with them) and both trigger functions,
destroying the stored listing history. Only run it against a database you fully control.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA = "~ '^[0-9a-f]{64}$'"
TABLES = (
    "listing_versions",
    "listing_normalization_runs",
    "listing_candidates",
    "listing_findings",
)


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "listing_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("seller_id", sa.Text(), nullable=False),
        sa.Column("seller_listing_ref", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_sha256", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_versions"),
        sa.UniqueConstraint(
            "seller_id",
            "seller_listing_ref",
            "version_number",
            name="uq_listing_versions_identity_version",
        ),
        _ck("listing_versions", "seller_id_length", "char_length(seller_id) BETWEEN 1 AND 128"),
        _ck(
            "listing_versions",
            "seller_listing_ref_length",
            "char_length(seller_listing_ref) BETWEEN 1 AND 128",
        ),
        _ck("listing_versions", "version_number_positive", "version_number >= 1"),
        _ck("listing_versions", "raw_bytes_size", "octet_length(raw_bytes) BETWEEN 1 AND 65536"),
        _ck("listing_versions", "raw_sha256_format", f"raw_sha256 {SHA}"),
        _ck(
            "listing_versions",
            "raw_sha256_matches_bytes",
            "raw_sha256 = encode(sha256(raw_bytes), 'hex')",
        ),
    )

    op.create_table(
        "listing_normalization_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("listing_version_id", sa.BigInteger(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("rules_digest", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Text(), nullable=False),
        sa.Column("output_sha256", sa.Text(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_version_id"],
            ["listing_versions.id"],
            name="fk_listing_normalization_runs_version_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_normalization_runs"),
        sa.UniqueConstraint(
            "listing_version_id",
            "rules_version",
            name="uq_listing_normalization_runs_version_rules",
        ),
        _ck("listing_normalization_runs", "rules_version_not_blank", "btrim(rules_version) <> ''"),
        _ck("listing_normalization_runs", "rules_digest_format", f"rules_digest {SHA}"),
        _ck(
            "listing_normalization_runs",
            "taxonomy_version_not_blank",
            "btrim(taxonomy_version) <> ''",
        ),
        _ck("listing_normalization_runs", "output_sha256_format", f"output_sha256 {SHA}"),
        _ck("listing_normalization_runs", "candidate_count_nonneg", "candidate_count >= 0"),
        _ck("listing_normalization_runs", "finding_count_nonneg", "finding_count >= 0"),
    )

    op.create_table(
        "listing_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("value_type", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["listing_normalization_runs.id"], name="fk_listing_candidates_run_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_candidates"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_listing_candidates_run_ordinal"),
        _ck("listing_candidates", "ordinal_nonneg", "ordinal >= 0"),
        _ck("listing_candidates", "field_not_empty", "field <> ''"),
        _ck(
            "listing_candidates",
            "basis_valid",
            "basis IN ('seller_field', 'title', 'description')",
        ),
        _ck("listing_candidates", "source_key_not_empty", "source_key <> ''"),
        _ck("listing_candidates", "raw_text_not_empty", "raw_text <> ''"),
        _ck(
            "listing_candidates",
            "span_valid",
            "(span_start IS NULL AND span_end IS NULL) OR "
            "(span_start >= 0 AND span_end > span_start)",
        ),
        _ck(
            "listing_candidates",
            "outcome_valid",
            "outcome IN ('normalized', 'unsupported', 'uncertain', 'invalid')",
        ),
        _ck(
            "listing_candidates",
            "value_type_valid",
            "value_type IS NULL OR value_type IN ('integer', 'decimal', 'text', 'enum', 'boolean')",
        ),
        _ck(
            "listing_candidates",
            "value_only_when_normalized",
            "outcome = 'normalized' OR "
            "(normalized_value IS NULL AND value_type IS NULL AND unit IS NULL)",
        ),
        _ck(
            "listing_candidates",
            "normalized_has_value",
            "outcome <> 'normalized' OR (normalized_value IS NOT NULL AND value_type IS NOT NULL)",
        ),
        _ck("listing_candidates", "rule_id_not_empty", "rule_id <> ''"),
    )

    op.create_table(
        "listing_findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("related_field", sa.Text(), nullable=True),
        sa.Column("source_key", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["listing_normalization_runs.id"], name="fk_listing_findings_run_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_findings"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_listing_findings_run_ordinal"),
        _ck("listing_findings", "ordinal_nonneg", "ordinal >= 0"),
        _ck(
            "listing_findings",
            "kind_valid",
            "kind IN ('value_conflict', 'cross_field_contradiction', 'incompatible_attribute', "
            "'incompatible_subcategory', 'brand_category_mismatch', 'unsupported_attribute', "
            "'compatibility_unchecked', 'missing_required', 'missing_expected', 'empty_value')",
        ),
        _ck("listing_findings", "message_not_empty", "message <> ''"),
    )

    # Listing history is append-only: block UPDATE, DELETE and TRUNCATE on every table.
    op.execute(
        """
        CREATE FUNCTION listing_history_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only listing history', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update_delete "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION listing_history_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate "
            f"BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION listing_history_append_only()"
        )

    # Versions of one identity start at 1, increase by one, and never repeat the raw input of
    # the version directly before (A -> B -> A is three versions; A -> A is one).
    op.execute(
        """
        CREATE FUNCTION listing_versions_check_sequence() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            prev_number integer;
            prev_sha text;
            prev_bytes bytea;
        BEGIN
            SELECT version_number, raw_sha256, raw_bytes
              INTO prev_number, prev_sha, prev_bytes
              FROM listing_versions
             WHERE seller_id = NEW.seller_id AND seller_listing_ref = NEW.seller_listing_ref
             ORDER BY version_number DESC
             LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.version_number <> 1 THEN
                    RAISE EXCEPTION 'listing_versions: the first version must be 1';
                END IF;
            ELSIF NEW.version_number <> prev_number + 1 THEN
                RAISE EXCEPTION 'listing_versions: versions must be consecutive';
            ELSIF NEW.raw_sha256 = prev_sha AND NEW.raw_bytes = prev_bytes THEN
                RAISE EXCEPTION 'listing_versions: raw input repeats the previous version';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_listing_versions_sequence "
        "BEFORE INSERT ON listing_versions "
        "FOR EACH ROW EXECUTE FUNCTION listing_versions_check_sequence()"
    )


def downgrade() -> None:
    # Destroys the listing history. Dropping a table also drops its triggers (DROP does not
    # fire the append-only triggers).
    op.drop_table("listing_findings")
    op.drop_table("listing_candidates")
    op.drop_table("listing_normalization_runs")
    op.drop_table("listing_versions")
    op.execute("DROP FUNCTION listing_versions_check_sequence()")
    op.execute("DROP FUNCTION listing_history_append_only()")
