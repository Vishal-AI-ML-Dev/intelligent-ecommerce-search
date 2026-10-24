"""Return Data Foundation tables (Milestone 18, `return-data-v1`).

Four provenance and file layers, then the eleven entity tables (contracts §2–3):

* `return_datasets`: one immutable dataset version (synthetic only in v1) and the hash of its
  descriptor.
* `return_import_batches`: one committed import, with versions, `bundle_sha256` (unique per
  dataset) and per-entity received/new/unchanged counts.
* `return_import_files`: safe file metadata (canonical name, entity, media type, size,
  SHA-256, rows). The API may read it.
* `return_import_file_contents`: the exact uploaded bytes, restricted. Only the schema owner
  can read them; a composite foreign key on (`file_pk`, `raw_sha256`) and a CHECK on the bytes
  keep metadata and contents from disagreeing.
* entity tables: each row carries `dataset_pk`, `batch_pk`, `source_file_pk`, `source_row`,
  `source_record_id`, `record_sha256` and `is_synthetic`; the other provenance fields come
  through the non-null dataset and batch references (Option A, D13). References between
  entities are composite foreign keys on (`dataset_pk`, parent ID), so they never cross dataset
  versions even when the import service is bypassed.

Customer statements (reason code, comments, complaints, reviews) and inspection observations
are separate tables; nothing here is a hypothesis, decision, refund, fault or responsibility.
Free text is stored only in its `redaction-1` form with per-kind counts. The stored bound is
twice the input limit because a replacement token can be longer than the text it replaces
(`[EMAIL]` for a 6-character address); the input limit itself is enforced on import.

Every table is append-only (triggers in migration 0007), and a deferred constraint trigger
keeps the non-cancelled returned quantity of an order item within its quantity. Enumerated
values are TEXT + named CHECK constraints built from the `returns.contracts` enums; the
migration repeats them literally and tests keep both identical.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ecommerce_search.db.base import Base
from ecommerce_search.models.catalog import SHA256_FORMAT, in_values
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.versions import CONTRACT_VERSION

RECORD_ID_FORMAT = f"~ '^{rc.RECORD_ID_PATTERN}$'"
ACTOR_REF_FORMAT = f"~ '^{rc.ACTOR_REF_PATTERN}$'"
DATASET_ID_FORMAT = f"~ '^{rc.DATASET_ID_PATTERN}$'"
CATALOG_VERSION_FORMAT = f"~ '^{rc.CATALOG_VERSION_PATTERN}$'"
STORED_TEXT_MAX = 2 * rc.TEXT_MAX
STORED_NOTE_MAX = 2 * rc.NOTE_MAX
FILE_MAX_BYTES = 5 * 1024 * 1024
MANIFEST_MAX_BYTES = 64 * 1024
BUNDLE_MAX_ROWS = 10_000
ENTITY_MAX_ROWS = 5_000
MAX_FILES = 12
FILE_NAMES = ("bundle.json", "manifest.json", *(f"{e.value}.csv" for e in rc.Entity))
ID = Text(collation="C")  # record IDs compare bytewise


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def optional_in(column: str, enum_cls: type[Enum]) -> str:
    return f"{column} IS NULL OR {in_values(column, enum_cls)}"


def _id_format(column: str, *, optional: bool = False) -> CheckConstraint:
    condition = f"{column} {RECORD_ID_FORMAT}"
    if optional:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"{column}_format")


def _provenance(
    table: str, natural_key: tuple[str, ...], *, key_max: int = rc.RECORD_ID_MAX
) -> tuple:
    """Constraints shared by the eleven entity tables."""
    matches = " || '|' || ".join(natural_key)
    return (
        UniqueConstraint("dataset_pk", *natural_key, name=f"uq_{table}_natural_key"),
        ForeignKeyConstraint(
            ["batch_pk", "dataset_pk"],
            ["return_import_batches.id", "return_import_batches.dataset_pk"],
            name=f"fk_{table}_batch",
        ),
        ForeignKeyConstraint(
            ["source_file_pk", "batch_pk"],
            ["return_import_files.id", "return_import_files.batch_pk"],
            name=f"fk_{table}_source_file",
        ),
        CheckConstraint("source_row >= 1", name="source_row_positive"),
        CheckConstraint(
            f"char_length(source_record_id) BETWEEN 1 AND {key_max}",
            name="source_record_id_length",
        ),
        CheckConstraint(f"source_record_id = {matches}", name="source_record_id_matches"),
        CheckConstraint(f"record_sha256 {SHA256_FORMAT}", name="record_sha256_format"),
        CheckConstraint("is_synthetic", name="is_synthetic_true"),
    )


def _redaction_counts(column: str) -> CheckConstraint:
    counts = [f"redacted_{kind}_count" for kind in rc.REDACTION_KINDS]
    return CheckConstraint(
        " AND ".join(f"{c} >= 0" for c in counts)
        + f" AND ({column} IS NOT NULL OR {' + '.join(counts)} = 0)",
        name="redaction_counts_valid",
    )


def _catalog_snapshot(table: str) -> tuple:
    """The listing snapshot triple: an existing catalog dataset version and a raw-line hash.
    Equality with the stored `raw_catalog_records` row is checked by the import service."""
    return (
        ForeignKeyConstraint(
            ["catalog_dataset_id", "catalog_dataset_version"],
            ["catalog_datasets.dataset_id", "catalog_datasets.dataset_version"],
            name=f"fk_{table}_catalog_dataset",
        ),
        CheckConstraint(
            f"catalog_dataset_version {CATALOG_VERSION_FORMAT}",
            name="catalog_dataset_version_format",
        ),
        CheckConstraint(f"catalog_raw_sha256 {SHA256_FORMAT}", name="catalog_raw_sha256_format"),
    )


def _text_length(column: str, maximum: int, *, optional: bool) -> CheckConstraint:
    condition = f"char_length({column}) BETWEEN 1 AND {maximum}"
    if optional:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"{column}_length")


def _ts(nullable: bool = False) -> Any:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class _EntityRow:
    """Provenance columns of every entity row."""

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dataset_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_file_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[str] = mapped_column(ID, nullable=False)
    record_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)


class _Redacted:
    """Per-kind `redaction-1` replacement counts of the row's one free-text field."""

    redacted_email_count: Mapped[int] = mapped_column(Integer, nullable=False)
    redacted_upi_count: Mapped[int] = mapped_column(Integer, nullable=False)
    redacted_number_count: Mapped[int] = mapped_column(Integer, nullable=False)
    redacted_pin_count: Mapped[int] = mapped_column(Integer, nullable=False)


# ---- provenance and files ------------------------------------------------------------------


class ReturnDataset(Base):
    """One immutable dataset version. There is no content checksum here (contracts §4.7)."""

    __tablename__ = "return_datasets"
    __table_args__ = (
        UniqueConstraint("dataset_id", "dataset_version", name="uq_return_datasets_identity"),
        CheckConstraint(f"dataset_id {DATASET_ID_FORMAT}", name="dataset_id_format"),
        CheckConstraint(
            f"dataset_version BETWEEN 1 AND {rc.DATASET_VERSION_MAX}", name="dataset_version_range"
        ),
        CheckConstraint(in_values("source_type", rc.SourceType), name="source_type_valid"),
        _text_length("source_name", rc.DATASET_SHORT_TEXT_MAX, optional=False),
        _text_length("source_description", rc.DATASET_LONG_TEXT_MAX, optional=False),
        _text_length("rights_note", rc.DATASET_LONG_TEXT_MAX, optional=False),
        _text_length("license_identifier", rc.DATASET_SHORT_TEXT_MAX, optional=True),
        _text_length("generator", rc.DATASET_SHORT_TEXT_MAX, optional=False),
        CheckConstraint("is_synthetic", name="is_synthetic_true"),
        _text_length("notes", rc.DATASET_LONG_TEXT_MAX, optional=True),
        CheckConstraint(f"descriptor_sha256 {SHA256_FORMAT}", name="descriptor_sha256_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ID, nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_description: Mapped[str] = mapped_column(Text, nullable=False)
    rights_note: Mapped[str] = mapped_column(Text, nullable=False)
    license_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    authored_on: Mapped[date] = mapped_column(Date, nullable=False)
    generator: Mapped[str] = mapped_column(Text, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    descriptor_sha256: Mapped[str] = mapped_column(Text, nullable=False)


def _count_columns() -> list[Column]:
    return [
        Column(f"{entity.value}_{kind}", Integer, nullable=False)
        for entity in rc.Entity
        for kind in ("received", "new", "unchanged")
    ]


def _count_checks() -> list[CheckConstraint]:
    checks = [
        CheckConstraint(
            f"{e}_received >= 0 AND {e}_new >= 0 AND {e}_unchanged >= 0 AND "
            f"{e}_received = {e}_new + {e}_unchanged",
            name=f"{e}_counts_valid",
        )
        for e in (entity.value for entity in rc.Entity)
    ]
    # A batch exists only when something new was stored (otherwise `no_new_records`).
    total_new = " + ".join(f"{entity.value}_new" for entity in rc.Entity)
    checks.append(CheckConstraint(f"{total_new} >= 1", name="new_total_positive"))
    return checks


class ReturnImportBatch(Base):
    """One committed import. `imported_by_label` is a token label, never a person."""

    __table__ = Table(
        "return_import_batches",
        Base.metadata,
        Column("id", BigInteger, Identity(), primary_key=True),
        Column("batch_id", Uuid(), nullable=False),
        Column(
            "dataset_pk",
            BigInteger,
            ForeignKey("return_datasets.id", name="fk_return_import_batches_dataset"),
            nullable=False,
        ),
        Column("contract_version", Text, nullable=False),
        Column("transformation_version", Text, nullable=False),
        Column("redaction_version", Text, nullable=False),
        Column("bundle_sha256", Text, nullable=False),
        Column("format", Text, nullable=False),
        Column("file_count", Integer, nullable=False),
        Column("total_bytes", BigInteger, nullable=False),
        *_count_columns(),
        Column("imported_by_label", Text, nullable=False),
        Column("imported_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("batch_id", name="uq_return_import_batches_batch_id"),
        UniqueConstraint("dataset_pk", "bundle_sha256", name="uq_return_import_batches_bundle"),
        UniqueConstraint("id", "dataset_pk", name="uq_return_import_batches_id_dataset"),
        CheckConstraint(
            f"contract_version IN ('{CONTRACT_VERSION}')", name="contract_version_valid"
        ),
        _text_length("transformation_version", 64, optional=False),
        _text_length("redaction_version", 64, optional=False),
        CheckConstraint(f"bundle_sha256 {SHA256_FORMAT}", name="bundle_sha256_format"),
        CheckConstraint("format IN ('json', 'csv')", name="format_valid"),
        CheckConstraint(
            "(format = 'json' AND file_count = 1) OR "
            f"(format = 'csv' AND file_count BETWEEN 2 AND {MAX_FILES})",
            name="file_count_valid",
        ),
        CheckConstraint("total_bytes >= 1", name="total_bytes_positive"),
        *_count_checks(),
        _text_length("imported_by_label", 200, optional=False),
    )


class ReturnImportFile(Base):
    """Safe metadata of one stored file. Client file names are never stored: `name` is the
    canonical name (`bundle.json`, `manifest.json` or `<entity>.csv`)."""

    __tablename__ = "return_import_files"
    __table_args__ = (
        UniqueConstraint("batch_pk", "name", name="uq_return_import_files_batch_name"),
        UniqueConstraint("id", "raw_sha256", name="uq_return_import_files_id_sha"),
        UniqueConstraint("id", "batch_pk", name="uq_return_import_files_id_batch"),
        CheckConstraint(f"name IN ({_quoted(FILE_NAMES)})", name="name_valid"),
        CheckConstraint(optional_in("entity", rc.Entity), name="entity_valid"),
        CheckConstraint(
            "(name IN ('bundle.json', 'manifest.json')) = (entity IS NULL) AND "
            "(entity IS NULL OR name = entity || '.csv')",
            name="entity_matches_name",
        ),
        CheckConstraint(
            "media_type = CASE WHEN entity IS NULL THEN 'application/json' ELSE 'text/csv' END",
            name="media_type_matches_name",
        ),
        CheckConstraint(
            f"size_bytes BETWEEN 1 AND {FILE_MAX_BYTES} AND "
            f"(name <> 'manifest.json' OR size_bytes <= {MANIFEST_MAX_BYTES})",
            name="size_bytes_range",
        ),
        CheckConstraint(f"raw_sha256 {SHA256_FORMAT}", name="raw_sha256_format"),
        CheckConstraint(
            "(name = 'manifest.json') = (row_count IS NULL) AND (row_count IS NULL OR "
            f"row_count BETWEEN 0 AND CASE WHEN name = 'bundle.json' THEN {BUNDLE_MAX_ROWS} "
            f"ELSE {ENTITY_MAX_ROWS} END)",
            name="row_count_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    batch_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("return_import_batches.id", name="fk_return_import_files_batch"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(ID, nullable=False)
    entity: Mapped[str | None] = mapped_column(ID, nullable=True)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReturnImportFileContent(Base):
    """The exact uploaded bytes (restricted: no API or importer read access)."""

    __tablename__ = "return_import_file_contents"
    __table_args__ = (
        PrimaryKeyConstraint("file_pk", name="pk_return_import_file_contents"),
        ForeignKeyConstraint(
            ["file_pk", "raw_sha256"],
            ["return_import_files.id", "return_import_files.raw_sha256"],
            name="fk_return_import_file_contents_file",
        ),
        CheckConstraint(
            f"octet_length(raw_bytes) BETWEEN 1 AND {FILE_MAX_BYTES}", name="raw_bytes_size"
        ),
        CheckConstraint(f"raw_sha256 {SHA256_FORMAT}", name="raw_sha256_format"),
        CheckConstraint(
            "raw_sha256 = encode(sha256(raw_bytes), 'hex')", name="raw_sha256_matches_bytes"
        ),
    )

    file_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)


# ---- entities ------------------------------------------------------------------------------


class OrderRow(_EntityRow, Base):
    __tablename__ = "orders"
    __table_args__ = (
        *_provenance("orders", ("order_id",)),
        _id_format("order_id"),
        CheckConstraint(f"customer_ref {ACTOR_REF_FORMAT}", name="customer_ref_format"),
        CheckConstraint(in_values("currency", rc.Currency), name="currency_valid"),
    )

    order_id: Mapped[str] = mapped_column(ID, nullable=False)
    customer_ref: Mapped[str] = mapped_column(ID, nullable=False)
    ordered_at: Mapped[datetime] = _ts()
    currency: Mapped[str] = mapped_column(Text, nullable=False)


class OrderItemRow(_EntityRow, Base):
    """`sku` is `product_id` (D3); there is no variant key."""

    __tablename__ = "order_items"
    __table_args__ = (
        *_provenance("order_items", ("order_item_id",)),
        UniqueConstraint(
            "dataset_pk", "order_item_id", "order_id", name="uq_order_items_item_order"
        ),
        UniqueConstraint(
            "dataset_pk", "order_item_id", "product_id", name="uq_order_items_item_product"
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_order_items_order",
        ),
        ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_order_items_product"
        ),
        *_catalog_snapshot("order_items"),
        _id_format("order_item_id"),
        _id_format("order_id"),
        CheckConstraint(
            f"quantity BETWEEN 1 AND {rc.ORDER_ITEM_QUANTITY_MAX}", name="quantity_range"
        ),
        CheckConstraint("unit_price >= 0", name="unit_price_nonneg"),
    )

    order_item_id: Mapped[str] = mapped_column(ID, nullable=False)
    order_id: Mapped[str] = mapped_column(ID, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    catalog_dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)


class ShipmentRow(_EntityRow, Base):
    __tablename__ = "shipments"
    __table_args__ = (
        *_provenance("shipments", ("shipment_id",)),
        UniqueConstraint("dataset_pk", "order_id", name="uq_shipments_one_per_order"),
        ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_shipments_order",
        ),
        _id_format("shipment_id"),
        _id_format("order_id"),
        _id_format("carrier_ref"),
        CheckConstraint(in_values("status", rc.ShipmentStatus), name="status_valid"),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="delivered_at_iff_delivered",
        ),
        CheckConstraint(
            "shipped_at IS NULL OR delivered_at IS NULL OR shipped_at <= delivered_at",
            name="shipped_not_after_delivered",
        ),
    )

    shipment_id: Mapped[str] = mapped_column(ID, nullable=False)
    order_id: Mapped[str] = mapped_column(ID, nullable=False)
    carrier_ref: Mapped[str] = mapped_column(ID, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    shipped_at: Mapped[datetime | None] = _ts(nullable=True)
    promised_delivery_by: Mapped[datetime | None] = _ts(nullable=True)
    delivered_at: Mapped[datetime | None] = _ts(nullable=True)


class DeliveryEventRow(_EntityRow, Base):
    __tablename__ = "delivery_events"
    __table_args__ = (
        *_provenance("delivery_events", ("event_id",)),
        ForeignKeyConstraint(
            ["dataset_pk", "shipment_id"],
            ["shipments.dataset_pk", "shipments.shipment_id"],
            name="fk_delivery_events_shipment",
        ),
        _id_format("event_id"),
        _id_format("shipment_id"),
        CheckConstraint(in_values("event_type", rc.DeliveryEventType), name="event_type_valid"),
        CheckConstraint(in_values("source", rc.DeliveryEventSource), name="source_valid"),
    )

    event_id: Mapped[str] = mapped_column(ID, nullable=False)
    shipment_id: Mapped[str] = mapped_column(ID, nullable=False)
    occurred_at: Mapped[datetime] = _ts()
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class ReturnRow(_EntityRow, Base):
    """A return snapshot. The non-cancelled quantity per order item is checked by the deferred
    trigger `trg_returns_quantity_within_item` (migration 0007)."""

    __tablename__ = "returns"
    __table_args__ = (
        *_provenance("returns", ("return_id",)),
        ForeignKeyConstraint(
            ["dataset_pk", "order_item_id"],
            ["order_items.dataset_pk", "order_items.order_item_id"],
            name="fk_returns_order_item",
        ),
        _id_format("return_id"),
        _id_format("order_item_id"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint(
            in_values("customer_reason_code", rc.ReasonCode), name="customer_reason_code_valid"
        ),
        CheckConstraint(in_values("status", rc.ReturnStatus), name="status_valid"),
        CheckConstraint(
            "status <> 'received' OR received_at IS NOT NULL", name="received_has_received_at"
        ),
        CheckConstraint(
            "status <> 'cancelled' OR received_at IS NULL", name="cancelled_has_no_received_at"
        ),
        CheckConstraint(
            "received_at IS NULL OR requested_at <= received_at",
            name="requested_not_after_received",
        ),
    )

    return_id: Mapped[str] = mapped_column(ID, nullable=False)
    order_item_id: Mapped[str] = mapped_column(ID, nullable=False)
    requested_at: Mapped[datetime] = _ts()
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime | None] = _ts(nullable=True)


class ReturnCommentRow(_EntityRow, _Redacted, Base):
    """A customer statement (redacted)."""

    __tablename__ = "return_comments"
    __table_args__ = (
        *_provenance("return_comments", ("comment_id",)),
        ForeignKeyConstraint(
            ["dataset_pk", "return_id"],
            ["returns.dataset_pk", "returns.return_id"],
            name="fk_return_comments_return",
        ),
        _id_format("comment_id"),
        _id_format("return_id"),
        _text_length("text", STORED_TEXT_MAX, optional=False),
        CheckConstraint("text ~ '[^[:space:]]'", name="text_visible"),
        _redaction_counts("text"),
    )

    comment_id: Mapped[str] = mapped_column(ID, nullable=False)
    return_id: Mapped[str] = mapped_column(ID, nullable=False)
    submitted_at: Mapped[datetime] = _ts()
    text: Mapped[str] = mapped_column(Text, nullable=False)


class InspectionObservationRow(_EntityRow, _Redacted, Base):
    """What was seen at inspection; not a decision."""

    __tablename__ = "inspection_observations"
    __table_args__ = (
        *_provenance("inspection_observations", ("observation_id",)),
        ForeignKeyConstraint(
            ["dataset_pk", "return_id"],
            ["returns.dataset_pk", "returns.return_id"],
            name="fk_inspection_observations_return",
        ),
        _id_format("observation_id"),
        _id_format("return_id"),
        CheckConstraint(f"inspector_ref {ACTOR_REF_FORMAT}", name="inspector_ref_format"),
        CheckConstraint(in_values("condition", rc.InspectionCondition), name="condition_valid"),
        _text_length("note", STORED_NOTE_MAX, optional=True),
        _redaction_counts("note"),
    )

    observation_id: Mapped[str] = mapped_column(ID, nullable=False)
    return_id: Mapped[str] = mapped_column(ID, nullable=False)
    observed_at: Mapped[datetime] = _ts()
    inspector_ref: Mapped[str] = mapped_column(ID, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerReviewRow(_EntityRow, _Redacted, Base):
    """A product review, never a return event. An order item, when given, has the same product
    (composite foreign key)."""

    __tablename__ = "customer_reviews"
    __table_args__ = (
        *_provenance("customer_reviews", ("review_id",)),
        ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_customer_reviews_product"
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "product_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.product_id"],
            name="fk_customer_reviews_order_item_product",
        ),
        _id_format("review_id"),
        _id_format("order_item_id", optional=True),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        _text_length("text", STORED_TEXT_MAX, optional=True),
        _redaction_counts("text"),
    )

    review_id: Mapped[str] = mapped_column(ID, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_item_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = _ts()
    text: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupportComplaintRow(_EntityRow, _Redacted, Base):
    """A customer statement (redacted). Given references must agree with the order item (composite
    foreign keys); an order and product given without an item are checked on import."""

    __tablename__ = "support_complaints"
    __table_args__ = (
        *_provenance("support_complaints", ("complaint_id",)),
        ForeignKeyConstraint(
            ["dataset_pk", "order_id"],
            ["orders.dataset_pk", "orders.order_id"],
            name="fk_support_complaints_order",
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "order_item_id"],
            ["order_items.dataset_pk", "order_items.order_item_id"],
            name="fk_support_complaints_order_item",
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "order_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.order_id"],
            name="fk_support_complaints_item_order",
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "order_item_id", "product_id"],
            ["order_items.dataset_pk", "order_items.order_item_id", "order_items.product_id"],
            name="fk_support_complaints_item_product",
        ),
        ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_support_complaints_product"
        ),
        _id_format("complaint_id"),
        _id_format("order_id", optional=True),
        _id_format("order_item_id", optional=True),
        CheckConstraint(
            "num_nonnulls(order_id, order_item_id, product_id) >= 1", name="has_subject"
        ),
        CheckConstraint(in_values("channel", rc.ComplaintChannel), name="channel_valid"),
        CheckConstraint(
            optional_in("customer_topic", rc.ComplaintTopic), name="customer_topic_valid"
        ),
        _text_length("text", STORED_TEXT_MAX, optional=False),
        CheckConstraint("text ~ '[^[:space:]]'", name="text_visible"),
        _redaction_counts("text"),
    )

    complaint_id: Mapped[str] = mapped_column(ID, nullable=False)
    order_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    order_item_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    product_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = _ts()
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    customer_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class CorrectionCaseRow(_EntityRow, _Redacted, Base):
    """An opened investigation (status `opened` only, D5). `focus_area` is not a finding."""

    __tablename__ = "correction_cases"
    __table_args__ = (
        *_provenance("correction_cases", ("case_id",)),
        ForeignKeyConstraint(
            ["product_id"], ["products.product_id"], name="fk_correction_cases_product"
        ),
        *_catalog_snapshot("correction_cases"),
        _id_format("case_id"),
        CheckConstraint(f"opened_by_ref {ACTOR_REF_FORMAT}", name="opened_by_ref_format"),
        CheckConstraint(in_values("focus_area", rc.CorrectionFocusArea), name="focus_area_valid"),
        CheckConstraint(in_values("status", rc.CorrectionCaseStatus), name="status_valid"),
        _text_length("note", STORED_NOTE_MAX, optional=True),
        _redaction_counts("note"),
    )

    case_id: Mapped[str] = mapped_column(ID, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[datetime] = _ts()
    opened_by_ref: Mapped[str] = mapped_column(ID, nullable=False)
    focus_area: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CorrectionCaseEvidenceRow(_EntityRow, Base):
    """Links a case to one record of the same dataset version (checked on import; the target
    table depends on `evidence_type`). `source_record_id` is `case_id|evidence_type|evidence_id`,
    at most 152 characters."""

    __tablename__ = "correction_case_evidence"
    __table_args__ = (
        *_provenance(
            "correction_case_evidence",
            ("case_id", "evidence_type", "evidence_id"),
            key_max=rc.EVIDENCE_KEY_MAX,
        ),
        ForeignKeyConstraint(
            ["dataset_pk", "case_id"],
            ["correction_cases.dataset_pk", "correction_cases.case_id"],
            name="fk_correction_case_evidence_case",
        ),
        _id_format("case_id"),
        CheckConstraint(in_values("evidence_type", rc.EvidenceType), name="evidence_type_valid"),
        _id_format("evidence_id"),
    )

    case_id: Mapped[str] = mapped_column(ID, nullable=False)
    evidence_type: Mapped[str] = mapped_column(ID, nullable=False)
    evidence_id: Mapped[str] = mapped_column(ID, nullable=False)


ENTITY_ROWS: dict[rc.Entity, type[Base]] = {
    rc.Entity(model.__tablename__): model
    for model in (
        OrderRow,
        OrderItemRow,
        ShipmentRow,
        DeliveryEventRow,
        ReturnRow,
        ReturnCommentRow,
        InspectionObservationRow,
        CustomerReviewRow,
        SupportComplaintRow,
        CorrectionCaseRow,
        CorrectionCaseEvidenceRow,
    )
}
RETURN_TABLES: tuple[str, ...] = (
    "return_datasets",
    "return_import_batches",
    "return_import_files",
    "return_import_file_contents",
    *(entity.value for entity in rc.Entity),
)
