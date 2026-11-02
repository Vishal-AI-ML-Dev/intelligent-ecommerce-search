"""create return classification history

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02

Creates the Milestone 19 Part 2 inferred tables, separate from the M18 return data: one row per
classification run (`return_classification_runs`, unique by `run_key_sha256`), one result per
return, the source records each result used, its predictions, their evidence, and per-SKU
complaint groups of a run. The DDL is written out literally (no application imports) so the
migration stays frozen when application code changes. The upgrade creates structure only; it
grants nothing (the versioned grants script `ecommerce_search.returns.classification.grants`
runs after it). No M18, catalog, search, embedding, listing or decision table is altered, and
no index beyond the constraint-backing ones is created.

Append-only: the new trigger function `return_inference_append_only()` blocks UPDATE, DELETE
and TRUNCATE on all eight tables. Stored predictions never carry `suspected_abuse` or
`insufficient_evidence`.

Downgrade drops the eight tables (their triggers go with them) and the trigger function,
destroying the stored classification history. M18 data is not touched. Only run it against a
database you fully control.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA = "~ '^[0-9a-f]{64}$'"
TABLES = (
    "return_classification_runs",
    "return_classifications",
    "return_classification_inputs",
    "return_category_predictions",
    "return_classification_evidence",
    "return_complaint_group_runs",
    "return_complaint_groups",
    "return_complaint_group_members",
)
CATEGORIES = (
    "ARRAY['sizing_fit', 'expectation_mismatch', 'colour_image_mismatch', "
    "'incorrect_specification', 'product_quality_defect', 'damaged_product', "
    "'wrong_item_delivered', 'logistics_delay', 'manufacturing_defect', 'change_of_mind', "
    "'suspected_abuse', 'insufficient_evidence']::text[]"
)
AREAS = (
    "ARRAY['listing', 'product', 'listing_photography', 'catalog', 'quality', 'supplier', "
    "'packaging', 'logistics', 'fulfilment', 'warehouse', 'customer_choice', "
    "'operations_investigation', 'manual_investigation']::text[]"
)
REVIEW_REASONS = (
    "ARRAY['insufficient_evidence', 'low_confidence', 'unsupported_distinction', "
    "'conflicting_statements', 'statement_inspection_conflict', 'conflicting_inspections', "
    "'uncorroborated_item_difference']::text[]"
)
EVIDENCE_TYPES = "('return', 'return_comment', 'support_complaint', 'inspection_observation')"


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    t = "return_classification_runs"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset_pk", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("tier_semantics", sa.Text(), nullable=False),
        sa.Column("complaint_link_policy", sa.Text(), nullable=False),
        sa.Column("input_sha256", sa.Text(), nullable=False),
        sa.Column("run_key_sha256", sa.Text(), nullable=False),
        sa.Column("return_count", sa.Integer(), nullable=False),
        sa.Column("classified_count", sa.Integer(), nullable=False),
        sa.Column("abstained_count", sa.Integer(), nullable=False),
        sa.Column("needs_review_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["dataset_pk"], ["return_datasets.id"], name="fk_return_classification_runs_dataset_pk"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_classification_runs"),
        sa.UniqueConstraint("run_key_sha256", name="uq_return_classification_runs_run_key_sha256"),
        _ck(t, "contract_version_not_blank", "btrim(contract_version) <> ''"),
        _ck(t, "taxonomy_version_not_blank", "btrim(taxonomy_version) <> ''"),
        _ck(t, "rules_version_not_blank", "btrim(rules_version) <> ''"),
        _ck(t, "method_valid", "method IN ('deterministic_baseline')"),
        _ck(t, "tier_semantics_valid", "tier_semantics IN ('rule_tier_not_probability')"),
        _ck(t, "complaint_link_policy_not_blank", "btrim(complaint_link_policy) <> ''"),
        _ck(t, "input_sha256_format", f"input_sha256 {SHA}"),
        _ck(t, "run_key_sha256_format", f"run_key_sha256 {SHA}"),
        _ck(
            t,
            "counts_consistent",
            "return_count >= 0 AND classified_count >= 0 AND abstained_count >= 0 AND "
            "needs_review_count >= 0 AND classified_count + abstained_count = return_count AND "
            "needs_review_count <= return_count",
        ),
    )

    t = "return_classifications"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("return_pk", sa.BigInteger(), nullable=False),
        sa.Column("return_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("review_reasons", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("prediction_count", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.Text(), nullable=False),
        sa.Column("result_sha256", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["return_classification_runs.id"], name="fk_return_classifications_run_id"
        ),
        sa.ForeignKeyConstraint(
            ["return_pk"], ["returns.id"], name="fk_return_classifications_return_pk"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_classifications"),
        sa.UniqueConstraint("run_id", "return_pk", name="uq_return_classifications_run_return"),
        _ck(t, "status_valid", "status IN ('classified', 'abstained')"),
        _ck(t, "review_reasons_valid", f"review_reasons <@ {REVIEW_REASONS}"),
        _ck(t, "needs_review_consistent", "needs_review = (cardinality(review_reasons) > 0)"),
        _ck(
            t,
            "prediction_count_consistent",
            "prediction_count >= 0 AND (status = 'abstained') = (prediction_count = 0)",
        ),
        _ck(t, "input_sha256_format", f"input_sha256 {SHA}"),
        _ck(t, "result_sha256_format", f"result_sha256 {SHA}"),
    )

    t = "return_classification_inputs"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("classification_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("record_sha256", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification_id"],
            ["return_classifications.id"],
            name="fk_return_classification_inputs_classification_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_classification_inputs"),
        sa.UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_classification_inputs_ordinal"
        ),
        sa.UniqueConstraint(
            "classification_id",
            "evidence_type",
            "evidence_id",
            name="uq_return_classification_inputs_record",
        ),
        _ck(t, "ordinal_nonneg", "ordinal >= 0"),
        _ck(t, "evidence_type_valid", f"evidence_type IN {EVIDENCE_TYPES}"),
        _ck(t, "evidence_id_not_empty", "evidence_id <> ''"),
        _ck(t, "record_sha256_format", f"record_sha256 {SHA}"),
    )

    t = "return_category_predictions"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("classification_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("candidates", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("suggested_areas", sa.ARRAY(sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification_id"],
            ["return_classifications.id"],
            name="fk_return_category_predictions_classification_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_category_predictions"),
        sa.UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_category_predictions_ordinal"
        ),
        _ck(t, "ordinal_nonneg", "ordinal >= 0"),
        _ck(t, "resolution_valid", "resolution IN ('single_category', 'unresolved_candidates')"),
        _ck(t, "candidates_valid", f"cardinality(candidates) >= 1 AND candidates <@ {CATEGORIES}"),
        _ck(
            t,
            "candidates_never_abuse_or_insufficient",
            "NOT (candidates && ARRAY['suspected_abuse', 'insufficient_evidence']::text[])",
        ),
        _ck(
            t,
            "resolution_consistent",
            "(resolution = 'single_category') = (category IS NOT NULL) AND "
            "(resolution = 'single_category') = (cardinality(candidates) = 1) AND "
            "(category IS NULL OR category = candidates[1])",
        ),
        _ck(t, "tier_valid", "tier IN ('high', 'medium', 'low')"),
        _ck(
            t,
            "suggested_areas_valid",
            f"cardinality(suggested_areas) >= 1 AND suggested_areas <@ {AREAS}",
        ),
    )

    t = "return_classification_evidence"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("classification_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("prediction_ordinal", sa.Integer(), nullable=True),
        sa.Column("effect", sa.Text(), nullable=True),
        sa.Column("suppression_reason", sa.Text(), nullable=True),
        sa.Column("categories", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("match", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["classification_id"],
            ["return_classifications.id"],
            name="fk_return_classification_evidence_classification_id",
        ),
        sa.ForeignKeyConstraint(
            ["classification_id", "prediction_ordinal"],
            [
                "return_category_predictions.classification_id",
                "return_category_predictions.ordinal",
            ],
            name="fk_return_classification_evidence_prediction",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_classification_evidence"),
        sa.UniqueConstraint(
            "classification_id", "ordinal", name="uq_return_classification_evidence_ordinal"
        ),
        _ck(t, "ordinal_nonneg", "ordinal >= 0"),
        _ck(
            t,
            "kind_valid",
            "kind IN ('supporting', 'contradicting', 'inspection_finding', 'suppressed_match')",
        ),
        _ck(
            t,
            "prediction_ordinal_by_kind",
            "(kind IN ('supporting', 'contradicting')) = (prediction_ordinal IS NOT NULL)",
        ),
        _ck(
            t,
            "effect_by_kind",
            "(kind = 'inspection_finding') = (effect IS NOT NULL) AND (effect IS NULL OR "
            "effect IN ('supports', 'contradicts', 'neutral', 'uncorroborated'))",
        ),
        _ck(
            t,
            "suppression_reason_by_kind",
            "(kind = 'suppressed_match') = (suppression_reason IS NOT NULL) AND "
            "(suppression_reason IS NULL OR "
            "suppression_reason IN ('negated', 'fuzzy_without_context'))",
        ),
        _ck(
            t,
            "categories_by_kind",
            "(kind IN ('inspection_finding', 'suppressed_match')) = (categories IS NOT NULL) AND "
            f"(categories IS NULL OR categories <@ {CATEGORIES})",
        ),
        _ck(
            t,
            "source_kind_valid",
            "source_kind IN ('reason_code', 'complaint_topic', 'customer_text', 'inspection')",
        ),
        _ck(t, "evidence_type_valid", f"evidence_type IN {EVIDENCE_TYPES}"),
        _ck(t, "evidence_id_not_empty", "evidence_id <> ''"),
        _ck(
            t,
            "field_valid",
            "field IN ('customer_reason_code', 'customer_topic', 'text', 'condition')",
        ),
        _ck(t, "rule_id_not_empty", "rule_id <> ''"),
        _ck(t, "match_valid", "match IN ('structured', 'exact', 'fuzzy')"),
        _ck(
            t,
            "span_or_value",
            "(field = 'text') = (span_start IS NOT NULL) AND "
            "(span_start IS NULL) = (span_end IS NULL) AND "
            "(span_start IS NULL OR (span_start >= 0 AND span_end > span_start)) AND "
            "(field = 'text') = (value IS NULL)",
        ),
    )

    t = "return_complaint_group_runs"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("classification_run_id", sa.BigInteger(), nullable=False),
        sa.Column("grouping_version", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("min_pattern_size", sa.Integer(), nullable=False),
        sa.Column("run_key_sha256", sa.Text(), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=False),
        sa.Column("pattern_count", sa.Integer(), nullable=False),
        sa.Column("grouped_return_count", sa.Integer(), nullable=False),
        sa.Column("ungrouped_return_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["classification_run_id"],
            ["return_classification_runs.id"],
            name="fk_return_complaint_group_runs_classification_run_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_complaint_group_runs"),
        sa.UniqueConstraint("run_key_sha256", name="uq_return_complaint_group_runs_run_key_sha256"),
        _ck(t, "grouping_version_not_blank", "btrim(grouping_version) <> ''"),
        _ck(t, "method_valid", "method IN ('rule_signature')"),
        _ck(t, "min_pattern_size_valid", "min_pattern_size >= 2"),
        _ck(t, "run_key_sha256_format", f"run_key_sha256 {SHA}"),
        _ck(
            t,
            "counts_consistent",
            "group_count >= 0 AND pattern_count >= 0 AND pattern_count <= group_count AND "
            "grouped_return_count >= 0 AND ungrouped_return_count >= 0",
        ),
    )

    t = "return_complaint_groups"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("group_run_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("candidates", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("qualifying_member_count", sa.Integer(), nullable=False),
        sa.Column("is_pattern", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_run_id"],
            ["return_complaint_group_runs.id"],
            name="fk_return_complaint_groups_group_run_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_complaint_groups"),
        sa.UniqueConstraint("group_run_id", "ordinal", name="uq_return_complaint_groups_ordinal"),
        sa.UniqueConstraint(
            "group_run_id", "product_id", "signature", name="uq_return_complaint_groups_signature"
        ),
        _ck(t, "ordinal_nonneg", "ordinal >= 0"),
        _ck(t, "product_id_not_blank", "btrim(product_id) <> ''"),
        _ck(t, "signature_not_blank", "btrim(signature) <> ''"),
        _ck(t, "candidates_valid", f"cardinality(candidates) >= 1 AND candidates <@ {CATEGORIES}"),
        _ck(t, "member_count_positive", "member_count >= 1"),
        _ck(
            t,
            "qualifying_member_count_valid",
            "qualifying_member_count >= 0 AND qualifying_member_count <= member_count",
        ),
        _ck(t, "pattern_has_qualifying_members", "NOT is_pattern OR qualifying_member_count >= 2"),
    )

    t = "return_complaint_group_members"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_id", sa.BigInteger(), nullable=False),
        sa.Column("prediction_ordinal", sa.Integer(), nullable=False),
        sa.Column("counts_toward_pattern", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["return_complaint_groups.id"],
            name="fk_return_complaint_group_members_group_id",
        ),
        sa.ForeignKeyConstraint(
            ["classification_id", "prediction_ordinal"],
            [
                "return_category_predictions.classification_id",
                "return_category_predictions.ordinal",
            ],
            name="fk_return_complaint_group_members_prediction",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_complaint_group_members"),
        sa.UniqueConstraint(
            "group_id", "classification_id", name="uq_return_complaint_group_members_member"
        ),
    )

    op.execute(
        """
        CREATE FUNCTION return_inference_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'return classification history is append-only (% on %)',
                TG_OP, TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update_delete "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION return_inference_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate "
            f"BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION return_inference_append_only()"
        )


def downgrade() -> None:
    # Destroys the stored classification history only; dropping a table drops its triggers.
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION return_inference_append_only()")
