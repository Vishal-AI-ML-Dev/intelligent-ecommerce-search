"""create listing decision history

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21

Creates the Milestone 14 inferred-decision tables, separate from the M13 seller data: one row
per recorded listing decision (`listing_decision_runs`, linked to the exact M13 normalization run
it was built from), its provider proposals, their per-label probabilities and its routing
reasons. The DDL is written out
literally (no application imports) so the migration stays frozen when application code changes.
The upgrade creates structure only; rows are written by
`python -m ecommerce_search.decision.listing_cli decide`. No M13, catalog, search or embedding
table is altered, and no index beyond the constraint-backing ones is created.

Decision history is append-only: the M13 trigger function `listing_history_append_only()`
(migration 0005) blocks UPDATE, DELETE and TRUNCATE on all four tables.

`gate_confidence` must be NULL (no calibration exists), and under routing policy `lr-1` neither
the route nor the deterministic route may be `auto_accept`. There is no rejection route.

Downgrade drops the four tables (their triggers go with them), destroying the stored decision
history. It keeps `listing_history_append_only()`, which 0005 owns. Only run it against a
database you fully control.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA = "~ '^[0-9a-f]{64}$'"
TABLES = (
    "listing_decision_runs",
    "listing_decision_proposals",
    "listing_decision_probabilities",
    "listing_decision_reasons",
)
ROUTES = "('auto_accept', 'accept_with_warning', 'manual_review')"
FALLBACK_REASONS = (
    "('timeout', 'transport_error', 'auth_error', 'rate_limited', 'server_error', "
    "'request_rejected', 'invalid_response', 'model_mismatch', 'deadline_exceeded', "
    "'saturated', 'provider_closed')"
)
REJECTION_CODES = (
    "('sdk_response_validation', 'answers_not_mapping', 'question_set_mismatch', "
    "'response_model_missing', 'usage_invalid', 'answer_not_choice', 'choice_out_of_set', "
    "'probability_labels_mismatch', 'probability_out_of_range', 'probability_sum', "
    "'confidence_out_of_range', 'choice_not_top', 'proposal_contract')"
)


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    op.create_table(
        "listing_decision_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("normalization_run_id", sa.BigInteger(), nullable=False),
        sa.Column("normalization_output_sha256", sa.Text(), nullable=False),
        sa.Column("question_set_version", sa.Text(), nullable=False),
        sa.Column("question_set_digest", sa.Text(), nullable=False),
        sa.Column("state_sha256", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("response_model", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Double(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("usage", sa.Text(), nullable=True),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("rejection", sa.Text(), nullable=True),
        sa.Column("rejection_call", sa.Text(), nullable=True),
        sa.Column("rejection_question", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("policy_digest", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("deterministic_route", sa.Text(), nullable=False),
        sa.Column("proposal_count", sa.Integer(), nullable=False),
        sa.Column("reason_count", sa.Integer(), nullable=False),
        sa.Column("outcome_sha256", sa.Text(), nullable=False),
        sa.Column("decision_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["normalization_run_id"],
            ["listing_normalization_runs.id"],
            name="fk_listing_decision_runs_normalization_run_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_decision_runs"),
        _ck(
            "listing_decision_runs",
            "normalization_output_sha256_format",
            f"normalization_output_sha256 {SHA}",
        ),
        _ck(
            "listing_decision_runs",
            "question_set_version_not_blank",
            "btrim(question_set_version) <> ''",
        ),
        _ck("listing_decision_runs", "question_set_digest_format", f"question_set_digest {SHA}"),
        _ck("listing_decision_runs", "state_sha256_format", f"state_sha256 {SHA}"),
        _ck("listing_decision_runs", "provider_valid", "provider IN ('deterministic', 'jev')"),
        _ck(
            "listing_decision_runs",
            "latency_ms_valid",
            "latency_ms IS NULL OR (latency_ms >= 0 AND latency_ms < 'Infinity'::float8)",
        ),
        _ck(
            "listing_decision_runs",
            "tokens_nonneg",
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0)",
        ),
        _ck(
            "listing_decision_runs",
            "usage_valid",
            "usage IS NULL OR usage IN ('complete', 'partial', 'none')",
        ),
        _ck(
            "listing_decision_runs",
            "fallback_reason_valid",
            f"fallback_reason IS NULL OR fallback_reason IN {FALLBACK_REASONS}",
        ),
        _ck(
            "listing_decision_runs",
            "rejection_valid",
            f"rejection IS NULL OR (rejection IN {REJECTION_CODES} "
            "AND fallback_reason = 'invalid_response')",
        ),
        _ck(
            "listing_decision_runs",
            "rejection_call_valid",
            "rejection_call IS NULL OR (rejection_call IN ('A', 'B') AND rejection IS NOT NULL)",
        ),
        _ck(
            "listing_decision_runs",
            "rejection_question_valid",
            "rejection_question IS NULL OR (rejection IS NOT NULL AND rejection_question ~ "
            "'^(category|subcategory|attribute_support\\[[0-9]+\\])$')",
        ),
        _ck(
            "listing_decision_runs",
            "deterministic_has_no_telemetry",
            "provider <> 'deterministic' OR (model IS NULL AND response_model IS NULL AND "
            "request_id IS NULL AND latency_ms IS NULL AND input_tokens IS NULL AND "
            "output_tokens IS NULL AND usage IS NULL AND fallback_reason IS NULL AND "
            "rejection IS NULL AND proposal_count = 0)",
        ),
        _ck(
            "listing_decision_runs",
            "jev_records_model_and_latency",
            "provider <> 'jev' OR (model IS NOT NULL AND latency_ms IS NOT NULL)",
        ),
        _ck(
            "listing_decision_runs",
            "fallback_has_no_proposals",
            "fallback_reason IS NULL OR proposal_count = 0",
        ),
        _ck("listing_decision_runs", "policy_version_not_blank", "btrim(policy_version) <> ''"),
        _ck("listing_decision_runs", "policy_digest_format", f"policy_digest {SHA}"),
        _ck("listing_decision_runs", "route_valid", f"route IN {ROUTES}"),
        _ck(
            "listing_decision_runs", "deterministic_route_valid", f"deterministic_route IN {ROUTES}"
        ),
        _ck(
            "listing_decision_runs",
            "lr1_no_auto_accept",
            "policy_version <> 'lr-1' OR "
            "(route <> 'auto_accept' AND deterministic_route <> 'auto_accept')",
        ),
        _ck("listing_decision_runs", "proposal_count_nonneg", "proposal_count >= 0"),
        _ck("listing_decision_runs", "reason_count_positive", "reason_count >= 1"),
        _ck("listing_decision_runs", "outcome_sha256_format", f"outcome_sha256 {SHA}"),
        _ck("listing_decision_runs", "decision_sha256_format", f"decision_sha256 {SHA}"),
    )

    op.create_table(
        "listing_decision_proposals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("decision_run_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("claim_ordinal", sa.Integer(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("raw_choice", sa.Text(), nullable=False),
        sa.Column("provider_confidence", sa.Double(), nullable=False),
        sa.Column("gate_confidence", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["decision_run_id"],
            ["listing_decision_runs.id"],
            name="fk_listing_decision_proposals_decision_run_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_decision_proposals"),
        sa.UniqueConstraint(
            "decision_run_id", "ordinal", name="uq_listing_decision_proposals_run_ordinal"
        ),
        _ck("listing_decision_proposals", "ordinal_nonneg", "ordinal >= 0"),
        _ck(
            "listing_decision_proposals",
            "decision_type_valid",
            "decision_type IN ('category', 'subcategory', 'attribute_support')",
        ),
        _ck(
            "listing_decision_proposals",
            "claim_ordinal_only_for_support",
            "(decision_type = 'attribute_support') = (claim_ordinal IS NOT NULL) AND "
            "(claim_ordinal IS NULL OR claim_ordinal >= 0)",
        ),
        _ck("listing_decision_proposals", "raw_choice_not_empty", "raw_choice <> ''"),
        _ck(
            "listing_decision_proposals",
            "provider_confidence_range",
            "provider_confidence >= 0 AND provider_confidence <= 1",
        ),
        _ck("listing_decision_proposals", "gate_confidence_unset", "gate_confidence IS NULL"),
    )

    op.create_table(
        "listing_decision_probabilities",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("proposal_id", sa.BigInteger(), nullable=False),
        sa.Column("label_ordinal", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("probability", sa.Double(), nullable=False),
        sa.Column("is_top", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["listing_decision_proposals.id"],
            name="fk_listing_decision_probabilities_proposal_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_decision_probabilities"),
        sa.UniqueConstraint(
            "proposal_id",
            "label_ordinal",
            name="uq_listing_decision_probabilities_proposal_label_ordinal",
        ),
        sa.UniqueConstraint(
            "proposal_id", "label", name="uq_listing_decision_probabilities_proposal_label"
        ),
        _ck("listing_decision_probabilities", "label_ordinal_nonneg", "label_ordinal >= 0"),
        _ck("listing_decision_probabilities", "label_not_empty", "label <> ''"),
        _ck(
            "listing_decision_probabilities",
            "probability_range",
            "probability >= 0 AND probability <= 1",
        ),
    )

    op.create_table(
        "listing_decision_reasons",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("decision_run_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["decision_run_id"],
            ["listing_decision_runs.id"],
            name="fk_listing_decision_reasons_decision_run_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_decision_reasons"),
        sa.UniqueConstraint(
            "decision_run_id", "ordinal", name="uq_listing_decision_reasons_run_ordinal"
        ),
        _ck("listing_decision_reasons", "ordinal_nonneg", "ordinal >= 0"),
        _ck("listing_decision_reasons", "code_not_empty", "code <> ''"),
        _ck("listing_decision_reasons", "route_valid", f"route IN {ROUTES}"),
    )

    # Decision history is append-only too, with 0005's trigger function.
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


def downgrade() -> None:
    # Destroys the decision history. Dropping a table also drops its triggers; the trigger
    # function belongs to 0005 and stays.
    op.drop_table("listing_decision_reasons")
    op.drop_table("listing_decision_probabilities")
    op.drop_table("listing_decision_proposals")
    op.drop_table("listing_decision_runs")
