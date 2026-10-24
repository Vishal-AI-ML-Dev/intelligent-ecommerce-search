"""create return data foundation

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24

Creates the Milestone 18 `return-data-v1` tables (docs/m18/contracts.md §2–3): dataset
provenance (`return_datasets`), import batches, safe file metadata, the restricted raw file
contents, and the eleven entity tables. The DDL is written out literally (no application
imports) so the migration stays frozen when application code changes. The upgrade creates
structure only; it grants nothing (the versioned grants script `ecommerce_search.returns.grants`
runs after it). No catalog, search, embedding, listing or decision table is altered, and no
index beyond the constraint-backing ones is created.

Database backstops, so that a bypassed import service still cannot store inconsistent records:
* composite foreign keys on (`dataset_pk`, parent ID), so references never cross dataset
  versions; rows are tied to their batch's dataset and to a file of that batch;
* one shipment per order (UNIQUE), the 152-character evidence key, and the file contents bound
  to their metadata checksum by a composite foreign key and a CHECK on the bytes;
* append-only: triggers block UPDATE, DELETE and TRUNCATE on all fifteen tables;
* a deferred constraint trigger keeps the non-cancelled returned quantity of an order item
  within its quantity. It serializes writers of one order item with a transaction advisory lock
  (two-key form, class 18, so it never collides with the import service's one-key dataset lock)
  and refuses REPEATABLE READ, whose snapshot could miss a concurrent commit.

Downgrade drops the fifteen tables (their triggers go with them) and both trigger functions,
destroying all stored return data. Only run it against a database you fully control.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA = "~ '^[0-9a-f]{64}$'"
RECORD_ID = "~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'"
ACTOR_REF = "~ '^SYN-(CUST|INSP|STAFF)-[0-9]{5}$'"
ENTITIES = (
    "orders",
    "order_items",
    "shipments",
    "delivery_events",
    "returns",
    "return_comments",
    "inspection_observations",
    "customer_reviews",
    "support_complaints",
    "correction_cases",
    "correction_case_evidence",
)
TABLES = (
    "return_datasets",
    "return_import_batches",
    "return_import_files",
    "return_import_file_contents",
    *ENTITIES,
)
FILE_NAMES = (
    "'bundle.json', 'manifest.json', 'orders.csv', 'order_items.csv', 'shipments.csv', "
    "'delivery_events.csv', 'returns.csv', 'return_comments.csv', "
    "'inspection_observations.csv', 'customer_reviews.csv', 'support_complaints.csv', "
    "'correction_cases.csv', 'correction_case_evidence.csv'"
)
ENTITY_VALUES = (
    "'orders', 'order_items', 'shipments', 'delivery_events', 'returns', 'return_comments', "
    "'inspection_observations', 'customer_reviews', 'support_complaints', 'correction_cases', "
    "'correction_case_evidence'"
)
SHIPMENT_STATUSES = (
    "'pending', 'in_transit', 'delivered', 'delivery_failed', 'returned_to_origin', 'unknown'"
)
EVENT_TYPES = (
    "'dispatched', 'in_transit', 'out_for_delivery', 'delivered', 'delivery_attempt_failed', "
    "'delay_notified', 'damage_reported_by_carrier', 'returned_to_origin'"
)
RETURN_STATUSES = "'requested', 'in_transit_to_warehouse', 'received', 'closed', 'cancelled'"
REASON_CODES = (
    "'SIZE_TOO_SMALL', 'SIZE_TOO_LARGE', 'NOT_AS_DESCRIBED', 'LOOKS_DIFFERENT_FROM_IMAGE', "
    "'SPECIFICATION_DIFFERENT', 'DEFECTIVE_OR_NOT_WORKING', 'ARRIVED_DAMAGED', "
    "'WRONG_ITEM_RECEIVED', 'ARRIVED_LATE', 'NO_LONGER_NEEDED', 'OTHER', 'NOT_PROVIDED'"
)
CONDITIONS = (
    "'as_new', 'used_good', 'damaged', 'functional_fault_confirmed', 'no_fault_found', "
    "'item_differs_from_order', 'parts_missing', 'not_assessable'"
)
CHANNELS = "'email', 'chat', 'phone', 'other'"
TOPICS = "'delivery', 'damaged', 'wrong_item', 'product_issue', 'return_process', 'other'"
FOCUS_AREAS = (
    "'listing_text', 'size_information', 'specification', 'imagery', 'packaging', "
    "'product_quality', 'fulfilment', 'logistics', 'other'"
)
EVIDENCE_TYPES = (
    "'return', 'return_comment', 'inspection_observation', 'customer_review', "
    "'support_complaint', 'shipment', 'delivery_event'"
)
REDACTION_COUNTS = (
    "redacted_email_count",
    "redacted_upi_count",
    "redacted_number_count",
    "redacted_pin_count",
)


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _id() -> sa.Text:
    return sa.Text(collation="C")


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _length(table: str, column: str, maximum: int, optional: bool) -> sa.CheckConstraint:
    condition = f"char_length({column}) BETWEEN 1 AND {maximum}"
    if optional:
        condition = f"{column} IS NULL OR {condition}"
    return _ck(table, f"{column}_length", condition)


def _id_format(table: str, column: str, optional: bool = False) -> sa.CheckConstraint:
    condition = f"{column} {RECORD_ID}"
    if optional:
        condition = f"{column} IS NULL OR {condition}"
    return _ck(table, f"{column}_format", condition)


def _entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset_pk", sa.BigInteger(), nullable=False),
        sa.Column("batch_pk", sa.BigInteger(), nullable=False),
        sa.Column("source_file_pk", sa.BigInteger(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_record_id", _id(), nullable=False),
        sa.Column("record_sha256", sa.Text(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
    ]


def _entity_constraints(table: str, natural_key: tuple[str, ...], key_max: int = 64) -> list:
    matches = " || '|' || ".join(natural_key)
    return [
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("dataset_pk", *natural_key, name=f"uq_{table}_natural_key"),
        sa.ForeignKeyConstraint(
            ["batch_pk", "dataset_pk"],
            ["return_import_batches.id", "return_import_batches.dataset_pk"],
            name=f"fk_{table}_batch",
        ),
        sa.ForeignKeyConstraint(
            ["source_file_pk", "batch_pk"],
            ["return_import_files.id", "return_import_files.batch_pk"],
            name=f"fk_{table}_source_file",
        ),
        _ck(table, "source_row_positive", "source_row >= 1"),
        _ck(
            table,
            "source_record_id_length",
            f"char_length(source_record_id) BETWEEN 1 AND {key_max}",
        ),
        _ck(table, "source_record_id_matches", f"source_record_id = {matches}"),
        _ck(table, "record_sha256_format", f"record_sha256 {SHA}"),
        _ck(table, "is_synthetic_true", "is_synthetic"),
    ]


def _redaction_columns() -> list[sa.Column]:
    return [sa.Column(name, sa.Integer(), nullable=False) for name in REDACTION_COUNTS]


def _redaction_check(table: str, column: str) -> sa.CheckConstraint:
    return _ck(
        table,
        "redaction_counts_valid",
        " AND ".join(f"{c} >= 0" for c in REDACTION_COUNTS)
        + f" AND ({column} IS NOT NULL OR {' + '.join(REDACTION_COUNTS)} = 0)",
    )


def _snapshot(table: str) -> list:
    return [
        sa.ForeignKeyConstraint(
            ["catalog_dataset_id", "catalog_dataset_version"],
            ["catalog_datasets.dataset_id", "catalog_datasets.dataset_version"],
            name=f"fk_{table}_catalog_dataset",
        ),
        _ck(
            table,
            "catalog_dataset_version_format",
            "catalog_dataset_version ~ '^[1-9][0-9]{0,8}$'",
        ),
        _ck(table, "catalog_raw_sha256_format", f"catalog_raw_sha256 {SHA}"),
    ]


def _batch_counts() -> tuple[list[sa.Column], list[sa.CheckConstraint]]:
    columns, checks = [], []
    for e in ENTITIES:
        columns += [
            sa.Column(f"{e}_{kind}", sa.Integer(), nullable=False)
            for kind in ("received", "new", "unchanged")
        ]
        checks.append(
            _ck(
                "return_import_batches",
                f"{e}_counts_valid",
                f"{e}_received >= 0 AND {e}_new >= 0 AND {e}_unchanged >= 0 AND "
                f"{e}_received = {e}_new + {e}_unchanged",
            )
        )
    checks.append(
        _ck(
            "return_import_batches",
            "new_total_positive",
            f"{' + '.join(f'{e}_new' for e in ENTITIES)} >= 1",
        )
    )
    return columns, checks


def upgrade() -> None:
    op.create_table(
        "return_datasets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset_id", _id(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_description", sa.Text(), nullable=False),
        sa.Column("rights_note", sa.Text(), nullable=False),
        sa.Column("license_identifier", sa.Text(), nullable=True),
        sa.Column("authored_on", sa.Date(), nullable=False),
        sa.Column("generator", sa.Text(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("descriptor_sha256", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_return_datasets"),
        sa.UniqueConstraint("dataset_id", "dataset_version", name="uq_return_datasets_identity"),
        _ck("return_datasets", "dataset_id_format", "dataset_id ~ '^[a-z0-9][a-z0-9-]{0,47}$'"),
        _ck(
            "return_datasets",
            "dataset_version_range",
            "dataset_version BETWEEN 1 AND 999999999",
        ),
        _ck("return_datasets", "source_type_valid", "source_type IN ('synthetic')"),
        _length("return_datasets", "source_name", 200, False),
        _length("return_datasets", "source_description", 2000, False),
        _length("return_datasets", "rights_note", 2000, False),
        _length("return_datasets", "license_identifier", 200, True),
        _length("return_datasets", "generator", 200, False),
        _ck("return_datasets", "is_synthetic_true", "is_synthetic"),
        _length("return_datasets", "notes", 2000, True),
        _ck("return_datasets", "descriptor_sha256_format", f"descriptor_sha256 {SHA}"),
    )

    count_columns, count_checks = _batch_counts()
    op.create_table(
        "return_import_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_pk", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("transformation_version", sa.Text(), nullable=False),
        sa.Column("redaction_version", sa.Text(), nullable=False),
        sa.Column("bundle_sha256", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        *count_columns,
        sa.Column("imported_by_label", sa.Text(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk"], ["return_datasets.id"], name="fk_return_import_batches_dataset"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_import_batches"),
        sa.UniqueConstraint("batch_id", name="uq_return_import_batches_batch_id"),
        sa.UniqueConstraint("dataset_pk", "bundle_sha256", name="uq_return_import_batches_bundle"),
        sa.UniqueConstraint("id", "dataset_pk", name="uq_return_import_batches_id_dataset"),
        _ck(
            "return_import_batches",
            "contract_version_valid",
            "contract_version IN ('return-data-v1')",
        ),
        _length("return_import_batches", "transformation_version", 64, False),
        _length("return_import_batches", "redaction_version", 64, False),
        _ck("return_import_batches", "bundle_sha256_format", f"bundle_sha256 {SHA}"),
        _ck("return_import_batches", "format_valid", "format IN ('json', 'csv')"),
        _ck(
            "return_import_batches",
            "file_count_valid",
            "(format = 'json' AND file_count = 1) OR "
            "(format = 'csv' AND file_count BETWEEN 2 AND 12)",
        ),
        _ck("return_import_batches", "total_bytes_positive", "total_bytes >= 1"),
        *count_checks,
        _length("return_import_batches", "imported_by_label", 200, False),
    )

    op.create_table(
        "return_import_files",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("batch_pk", sa.BigInteger(), nullable=False),
        sa.Column("name", _id(), nullable=False),
        sa.Column("entity", _id(), nullable=True),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_pk"], ["return_import_batches.id"], name="fk_return_import_files_batch"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_return_import_files"),
        sa.UniqueConstraint("batch_pk", "name", name="uq_return_import_files_batch_name"),
        sa.UniqueConstraint("id", "raw_sha256", name="uq_return_import_files_id_sha"),
        sa.UniqueConstraint("id", "batch_pk", name="uq_return_import_files_id_batch"),
        _ck("return_import_files", "name_valid", f"name IN ({FILE_NAMES})"),
        _ck(
            "return_import_files", "entity_valid", f"entity IS NULL OR entity IN ({ENTITY_VALUES})"
        ),
        _ck(
            "return_import_files",
            "entity_matches_name",
            "(name IN ('bundle.json', 'manifest.json')) = (entity IS NULL) AND "
            "(entity IS NULL OR name = entity || '.csv')",
        ),
        _ck(
            "return_import_files",
            "media_type_matches_name",
            "media_type = CASE WHEN entity IS NULL THEN 'application/json' ELSE 'text/csv' END",
        ),
        _ck(
            "return_import_files",
            "size_bytes_range",
            "size_bytes BETWEEN 1 AND 5242880 AND (name <> 'manifest.json' OR size_bytes <= 65536)",
        ),
        _ck("return_import_files", "raw_sha256_format", f"raw_sha256 {SHA}"),
        _ck(
            "return_import_files",
            "row_count_valid",
            "(name = 'manifest.json') = (row_count IS NULL) AND (row_count IS NULL OR "
            "row_count BETWEEN 0 AND CASE WHEN name = 'bundle.json' THEN 10000 ELSE 5000 END)",
        ),
    )

    op.create_table(
        "return_import_file_contents",
        sa.Column("file_pk", sa.BigInteger(), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_sha256", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("file_pk", name="pk_return_import_file_contents"),
        sa.ForeignKeyConstraint(
            ["file_pk", "raw_sha256"],
            ["return_import_files.id", "return_import_files.raw_sha256"],
            name="fk_return_import_file_contents_file",
        ),
        _ck(
            "return_import_file_contents",
            "raw_bytes_size",
            "octet_length(raw_bytes) BETWEEN 1 AND 5242880",
        ),
        _ck("return_import_file_contents", "raw_sha256_format", f"raw_sha256 {SHA}"),
        _ck(
            "return_import_file_contents",
            "raw_sha256_matches_bytes",
            "raw_sha256 = encode(sha256(raw_bytes), 'hex')",
        ),
    )

    op.create_table(
        "orders",
        *_entity_columns(),
        sa.Column("order_id", _id(), nullable=False),
        sa.Column("customer_ref", _id(), nullable=False),
        _ts("ordered_at"),
        sa.Column("currency", sa.Text(), nullable=False),
        *_entity_constraints("orders", ("order_id",)),
        _id_format("orders", "order_id"),
        _ck("orders", "customer_ref_format", f"customer_ref {ACTOR_REF}"),
        _ck("orders", "currency_valid", "currency IN ('INR')"),
    )

    op.create_table(
        "order_items",
        *_entity_columns(),
        sa.Column("order_item_id", _id(), nullable=False),
        sa.Column("order_id", _id(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("catalog_dataset_id", sa.Text(), nullable=False),
        sa.Column("catalog_dataset_version", sa.Text(), nullable=False),
        sa.Column("catalog_raw_sha256", sa.Text(), nullable=False),
        *_entity_constraints("order_items", ("order_item_id",)),
        sa.UniqueConstraint(
            "dataset_pk", "order_item_id", "order_id", name="uq_order_items_item_order"
        ),
        sa.UniqueConstraint(
            "dataset_pk", "order_item_id", "product_id", name="uq_order_items_item_product"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_order_items_order",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_order_items_product"
        ),
        *_snapshot("order_items"),
        _id_format("order_items", "order_item_id"),
        _id_format("order_items", "order_id"),
        _ck("order_items", "quantity_range", "quantity BETWEEN 1 AND 10"),
        _ck("order_items", "unit_price_nonneg", "unit_price >= 0"),
    )

    op.create_table(
        "shipments",
        *_entity_columns(),
        sa.Column("shipment_id", _id(), nullable=False),
        sa.Column("order_id", _id(), nullable=False),
        sa.Column("carrier_ref", _id(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _ts("shipped_at", nullable=True),
        _ts("promised_delivery_by", nullable=True),
        _ts("delivered_at", nullable=True),
        *_entity_constraints("shipments", ("shipment_id",)),
        sa.UniqueConstraint("dataset_pk", "order_id", name="uq_shipments_one_per_order"),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_shipments_order",
        ),
        _id_format("shipments", "shipment_id"),
        _id_format("shipments", "order_id"),
        _id_format("shipments", "carrier_ref"),
        _ck("shipments", "status_valid", f"status IN ({SHIPMENT_STATUSES})"),
        _ck(
            "shipments",
            "delivered_at_iff_delivered",
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
        ),
        _ck(
            "shipments",
            "shipped_not_after_delivered",
            "shipped_at IS NULL OR delivered_at IS NULL OR shipped_at <= delivered_at",
        ),
    )

    op.create_table(
        "delivery_events",
        *_entity_columns(),
        sa.Column("event_id", _id(), nullable=False),
        sa.Column("shipment_id", _id(), nullable=False),
        _ts("occurred_at"),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        *_entity_constraints("delivery_events", ("event_id",)),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "shipment_id"],
            ["shipments.dataset_pk", "shipments.shipment_id"],
            name="fk_delivery_events_shipment",
        ),
        _id_format("delivery_events", "event_id"),
        _id_format("delivery_events", "shipment_id"),
        _ck("delivery_events", "event_type_valid", f"event_type IN ({EVENT_TYPES})"),
        _ck("delivery_events", "source_valid", "source IN ('carrier', 'warehouse')"),
    )

    op.create_table(
        "returns",
        *_entity_columns(),
        sa.Column("return_id", _id(), nullable=False),
        sa.Column("order_item_id", _id(), nullable=False),
        _ts("requested_at"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("customer_reason_code", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _ts("received_at", nullable=True),
        *_entity_constraints("returns", ("return_id",)),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_item_id"],
            ["order_items.dataset_pk", "order_items.order_item_id"],
            name="fk_returns_order_item",
        ),
        _id_format("returns", "return_id"),
        _id_format("returns", "order_item_id"),
        _ck("returns", "quantity_positive", "quantity >= 1"),
        _ck(
            "returns",
            "customer_reason_code_valid",
            f"customer_reason_code IN ({REASON_CODES})",
        ),
        _ck("returns", "status_valid", f"status IN ({RETURN_STATUSES})"),
        _ck(
            "returns",
            "received_has_received_at",
            "status <> 'received' OR received_at IS NOT NULL",
        ),
        _ck(
            "returns",
            "cancelled_has_no_received_at",
            "status <> 'cancelled' OR received_at IS NULL",
        ),
        _ck(
            "returns",
            "requested_not_after_received",
            "received_at IS NULL OR requested_at <= received_at",
        ),
    )

    op.create_table(
        "return_comments",
        *_entity_columns(),
        sa.Column("comment_id", _id(), nullable=False),
        sa.Column("return_id", _id(), nullable=False),
        _ts("submitted_at"),
        sa.Column("text", sa.Text(), nullable=False),
        *_redaction_columns(),
        *_entity_constraints("return_comments", ("comment_id",)),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "return_id"],
            ["returns.dataset_pk", "returns.return_id"],
            name="fk_return_comments_return",
        ),
        _id_format("return_comments", "comment_id"),
        _id_format("return_comments", "return_id"),
        _length("return_comments", "text", 4000, False),
        _ck("return_comments", "text_visible", "text ~ '[^[:space:]]'"),
        _redaction_check("return_comments", "text"),
    )

    op.create_table(
        "inspection_observations",
        *_entity_columns(),
        sa.Column("observation_id", _id(), nullable=False),
        sa.Column("return_id", _id(), nullable=False),
        _ts("observed_at"),
        sa.Column("inspector_ref", _id(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_redaction_columns(),
        *_entity_constraints("inspection_observations", ("observation_id",)),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "return_id"],
            ["returns.dataset_pk", "returns.return_id"],
            name="fk_inspection_observations_return",
        ),
        _id_format("inspection_observations", "observation_id"),
        _id_format("inspection_observations", "return_id"),
        _ck("inspection_observations", "inspector_ref_format", f"inspector_ref {ACTOR_REF}"),
        _ck("inspection_observations", "condition_valid", f"condition IN ({CONDITIONS})"),
        _length("inspection_observations", "note", 2000, True),
        _redaction_check("inspection_observations", "note"),
    )

    op.create_table(
        "customer_reviews",
        *_entity_columns(),
        sa.Column("review_id", _id(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("order_item_id", _id(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        _ts("submitted_at"),
        sa.Column("text", sa.Text(), nullable=True),
        *_redaction_columns(),
        *_entity_constraints("customer_reviews", ("review_id",)),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_customer_reviews_product"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "product_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.product_id"],
            name="fk_customer_reviews_order_item_product",
        ),
        _id_format("customer_reviews", "review_id"),
        _id_format("customer_reviews", "order_item_id", optional=True),
        _ck("customer_reviews", "rating_range", "rating BETWEEN 1 AND 5"),
        _length("customer_reviews", "text", 4000, True),
        _redaction_check("customer_reviews", "text"),
    )

    op.create_table(
        "support_complaints",
        *_entity_columns(),
        sa.Column("complaint_id", _id(), nullable=False),
        sa.Column("order_id", _id(), nullable=True),
        sa.Column("order_item_id", _id(), nullable=True),
        sa.Column("product_id", sa.Text(), nullable=True),
        _ts("opened_at"),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("customer_topic", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        *_redaction_columns(),
        *_entity_constraints("support_complaints", ("complaint_id",)),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_support_complaints_order",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_item_id"],
            ["order_items.dataset_pk", "order_items.order_item_id"],
            name="fk_support_complaints_order_item",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "order_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.order_id"],
            name="fk_support_complaints_item_order",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "product_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.product_id"],
            name="fk_support_complaints_item_product",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_support_complaints_product"
        ),
        _id_format("support_complaints", "complaint_id"),
        _id_format("support_complaints", "order_id", optional=True),
        _id_format("support_complaints", "order_item_id", optional=True),
        _ck(
            "support_complaints",
            "has_subject",
            "num_nonnulls(order_id, order_item_id, product_id) >= 1",
        ),
        _ck("support_complaints", "channel_valid", f"channel IN ({CHANNELS})"),
        _ck(
            "support_complaints",
            "customer_topic_valid",
            f"customer_topic IS NULL OR customer_topic IN ({TOPICS})",
        ),
        _length("support_complaints", "text", 4000, False),
        _ck("support_complaints", "text_visible", "text ~ '[^[:space:]]'"),
        _redaction_check("support_complaints", "text"),
    )

    op.create_table(
        "correction_cases",
        *_entity_columns(),
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("catalog_dataset_id", sa.Text(), nullable=False),
        sa.Column("catalog_dataset_version", sa.Text(), nullable=False),
        sa.Column("catalog_raw_sha256", sa.Text(), nullable=False),
        _ts("opened_at"),
        sa.Column("opened_by_ref", _id(), nullable=False),
        sa.Column("focus_area", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_redaction_columns(),
        *_entity_constraints("correction_cases", ("case_id",)),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_correction_cases_product"
        ),
        *_snapshot("correction_cases"),
        _id_format("correction_cases", "case_id"),
        _ck("correction_cases", "opened_by_ref_format", f"opened_by_ref {ACTOR_REF}"),
        _ck("correction_cases", "focus_area_valid", f"focus_area IN ({FOCUS_AREAS})"),
        _ck("correction_cases", "status_valid", "status IN ('opened')"),
        _length("correction_cases", "note", 2000, True),
        _redaction_check("correction_cases", "note"),
    )

    op.create_table(
        "correction_case_evidence",
        *_entity_columns(),
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("evidence_type", _id(), nullable=False),
        sa.Column("evidence_id", _id(), nullable=False),
        *_entity_constraints(
            "correction_case_evidence", ("case_id", "evidence_type", "evidence_id"), key_max=152
        ),
        sa.ForeignKeyConstraint(
            ["dataset_pk", "case_id"],
            ["correction_cases.dataset_pk", "correction_cases.case_id"],
            name="fk_correction_case_evidence_case",
        ),
        _id_format("correction_case_evidence", "case_id"),
        _ck(
            "correction_case_evidence",
            "evidence_type_valid",
            f"evidence_type IN ({EVIDENCE_TYPES})",
        ),
        _id_format("correction_case_evidence", "evidence_id"),
    )

    # Return data is append-only: block UPDATE, DELETE and TRUNCATE on every table.
    op.execute(
        """
        CREATE FUNCTION return_data_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only return data', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update_delete "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION return_data_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate "
            f"BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION return_data_append_only()"
        )

    # The non-cancelled quantity returned for one order item never exceeds its quantity. The
    # check runs at commit; the advisory lock makes a second writer of the same item wait for
    # the first to finish, and READ COMMITTED then lets it see the first writer's rows.
    op.execute(
        """
        CREATE FUNCTION returns_check_item_quantity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            item_quantity integer;
            returned integer;
        BEGIN
            IF NEW.status = 'cancelled' THEN
                RETURN NULL;
            END IF;
            IF current_setting('transaction_isolation') = 'repeatable read' THEN
                RAISE EXCEPTION 'returns: the quantity check cannot run under REPEATABLE READ'
                    USING ERRCODE = 'invalid_transaction_state';
            END IF;
            PERFORM pg_advisory_xact_lock(
                18, hashtext(NEW.dataset_pk::text || ':' || NEW.order_item_id)
            );
            SELECT quantity INTO item_quantity
              FROM public.order_items
             WHERE dataset_pk = NEW.dataset_pk AND order_item_id = NEW.order_item_id;
            SELECT COALESCE(sum(quantity), 0) INTO returned
              FROM public.returns
             WHERE dataset_pk = NEW.dataset_pk AND order_item_id = NEW.order_item_id
               AND status <> 'cancelled';
            IF returned > item_quantity THEN
                RAISE EXCEPTION 'returns: non-cancelled quantity exceeds the order item quantity'
                    USING ERRCODE = 'check_violation', TABLE = 'returns',
                          CONSTRAINT = 'trg_returns_quantity_within_item';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_returns_quantity_within_item "
        "AFTER INSERT ON returns DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION returns_check_item_quantity()"
    )


def downgrade() -> None:
    # Destroys all stored return data. Dropping a table also drops its triggers (DROP does not
    # fire the append-only triggers).
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION returns_check_item_quantity()")
    op.execute("DROP FUNCTION return_data_append_only()")
