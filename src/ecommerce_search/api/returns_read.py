"""Database reads behind the retailer return read API (Milestone 18-04C, contracts §7, §7.1).

Every function runs inside `read_snapshot`: one READ ONLY, REPEATABLE READ transaction on the
read-only app engine, rolled back at the end (AC-13 (2)). SQL text is fixed; caller values are
always bind parameters. `return_import_file_contents` is never referenced.

**Order.** Returns: `requested_at` desc, then `dataset_id`, `dataset_version` and `return_id`
asc (text `COLLATE "C"`), which is total because those three are unique together. Batches:
`imported_at` desc, then `batch_id` asc. Pages are keyset pages: the seek predicate is the
exact complement of the order, and one extra row tells whether a next page exists.
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from ecommerce_search.api import returns_schemas as rs
from ecommerce_search.returns import contracts as rc

# The listing-snapshot text fields read from the raw catalog line (§7.1 D6d). Each is a string
# on every line of catalog v1; anything else is a contract breach and fails the request. `price`
# is read too, in its raw JSON type (M1).
SNAPSHOT_TEXT_FIELDS = ("title", "brand", "category", "currency")


class SnapshotContractError(Exception):
    """A stored catalog line lacks a snapshot field or holds it in another type."""


@contextmanager
def read_snapshot(engine: Engine) -> Iterator[Connection]:
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        conn.execute(text("SET TRANSACTION READ ONLY"))
        yield conn
        conn.rollback()


# ---- return list -----------------------------------------------------------------------------

_PREFIX = "pv_"  # provenance columns of a row

# The return's own provenance (§7 amendment 2026-10-28, AC-04): resolved through the return's
# own dataset (`d`), batch (`rb`) and source file (`rf`), as `pv_` columns.
_RETURN_COLUMNS = """
    d.dataset_id, d.dataset_version, r.return_id, r.requested_at, r.status, r.quantity,
    r.customer_reason_code, r.order_item_id, oi.order_id, r.is_synthetic,
    p.product_id, p.title, p.category, p.brand,
    (SELECT count(*) FROM return_comments c
      WHERE c.dataset_pk = r.dataset_pk AND c.return_id = r.return_id) AS comment_count,
    (SELECT count(*) FROM inspection_observations i
      WHERE i.dataset_pk = r.dataset_pk AND i.return_id = r.return_id) AS inspection_count,
    d.source_type AS pv_source_type, d.source_name AS pv_source_name,
    d.license_identifier AS pv_license, r.source_record_id AS pv_source_record_id,
    d.dataset_version AS pv_dataset_version, rb.imported_at AS pv_ingestion_timestamp,
    rb.transformation_version AS pv_transformation_version, r.is_synthetic AS pv_is_synthetic,
    rb.batch_id AS pv_batch_id, rf.name AS pv_source_file, r.source_row AS pv_source_row
"""
_RETURN_FROM = """
    FROM returns r
    JOIN return_datasets d ON d.id = r.dataset_pk
    JOIN return_import_batches rb ON rb.id = r.batch_pk
    JOIN return_import_files rf ON rf.id = r.source_file_pk
    JOIN order_items oi ON oi.dataset_pk = r.dataset_pk AND oi.order_item_id = r.order_item_id
    JOIN products p ON p.product_id = oi.product_id
"""
_RETURN_ORDER = """
    ORDER BY r.requested_at DESC, d.dataset_id COLLATE "C" ASC, d.dataset_version ASC,
             r.return_id COLLATE "C" ASC
"""
_HAS_COMMENT = (
    "EXISTS (SELECT 1 FROM return_comments c "
    "WHERE c.dataset_pk = r.dataset_pk AND c.return_id = r.return_id)"
)
_HAS_INSPECTION = (
    "EXISTS (SELECT 1 FROM inspection_observations i "
    "WHERE i.dataset_pk = r.dataset_pk AND i.return_id = r.return_id)"
)
# One fixed clause per filter; values are bound as the parameter of the same name.
_FILTER_CLAUSES = {
    "dataset_id": 'd.dataset_id = :dataset_id COLLATE "C"',
    "dataset_version": "d.dataset_version = :dataset_version",
    "status": "r.status = :status",
    "customer_reason_code": "r.customer_reason_code = :customer_reason_code",
    "category": "p.category = :category",
    "product_id": "oi.product_id = :product_id",
    "requested_from": "r.requested_at >= :requested_from",  # inclusive (D6a)
    "requested_to": "r.requested_at < :requested_to",  # exclusive (D6a)
    "batch_id": "rb.batch_id = :batch_id",
}
_RETURN_SEEK = (
    "(r.requested_at < :after_requested_at OR (r.requested_at = :after_requested_at AND "
    '(d.dataset_id COLLATE "C", d.dataset_version, r.return_id COLLATE "C") > '
    '(:after_dataset_id COLLATE "C", :after_dataset_version, :after_return_id COLLATE "C")))'
)


def _filter_sql(filters: rs.ReturnFilters) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for name, clause in _FILTER_CLAUSES.items():
        value = getattr(filters, name)
        if value is not None:
            clauses.append(clause)
            params[name] = value.value if isinstance(value, StrEnum) else value
    for flag, condition in (
        (filters.has_comment, _HAS_COMMENT),
        (filters.has_inspection, _HAS_INSPECTION),
    ):
        if flag is not None:
            clauses.append(condition if flag else f"NOT {condition}")
    return clauses, params


def _summary(row: Mapping[str, Any]) -> rs.ReturnSummary:
    return rs.ReturnSummary(
        ref=rs.ReturnRef(
            dataset_id=row["dataset_id"],
            dataset_version=row["dataset_version"],
            return_id=row["return_id"],
        ),
        requested_at=row["requested_at"],
        status=row["status"],
        quantity=row["quantity"],
        customer_reason_code=row["customer_reason_code"],
        product=_product(row),
        order_id=row["order_id"],
        order_item_id=row["order_item_id"],
        comment_count=row["comment_count"],
        inspection_count=row["inspection_count"],
        is_synthetic=row["is_synthetic"],
        provenance=_provenance(row),
    )


def _provenance(row: Mapping[str, Any]) -> rs.Provenance:
    """The record's own provenance from its `pv_` columns (every one is required)."""
    return rs.Provenance(
        **{k.removeprefix(_PREFIX): v for k, v in row.items() if k.startswith(_PREFIX)}
    )


def _product(row: Mapping[str, Any]) -> rs.ProductRef:
    return rs.ProductRef(
        product_id=row["product_id"],
        sku=row["product_id"],  # D3
        title=row["title"],
        category=row["category"],
        brand=row["brand"],
    )


def list_returns(
    engine: Engine, filters: rs.ReturnFilters, after: rs.ReturnPosition | None
) -> list[rs.ReturnSummary]:
    """Up to `filters.limit + 1` returns after `after`, in the total order."""
    clauses, params = _filter_sql(filters)
    if after is not None:
        clauses.append(_RETURN_SEEK)
        params |= {
            "after_requested_at": after.requested_at,
            "after_dataset_id": after.dataset_id,
            "after_dataset_version": after.dataset_version,
            "after_return_id": after.return_id,
        }
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_RETURN_COLUMNS} {_RETURN_FROM} {where} {_RETURN_ORDER} LIMIT :fetch"
    with read_snapshot(engine) as conn:
        rows = conn.execute(text(sql), params | {"fetch": filters.limit + 1}).mappings().all()
    return [_summary(row) for row in rows]


# ---- return detail ---------------------------------------------------------------------------

# Per-record provenance (AC-04): every nested record query aliases its own table as `x` and
# resolves the eight fields through *its own* dataset, batch and source-file references.
_PROVENANCE_COLUMNS = """
    pd.source_type AS pv_source_type, pd.source_name AS pv_source_name,
    pd.license_identifier AS pv_license, x.source_record_id AS pv_source_record_id,
    pd.dataset_version AS pv_dataset_version, pb.imported_at AS pv_ingestion_timestamp,
    pb.transformation_version AS pv_transformation_version, x.is_synthetic AS pv_is_synthetic,
    pb.batch_id AS pv_batch_id, pf.name AS pv_source_file, x.source_row AS pv_source_row
"""
_PROVENANCE_JOINS = """
    JOIN return_datasets pd ON pd.id = x.dataset_pk
    JOIN return_import_batches pb ON pb.id = x.batch_pk
    JOIN return_import_files pf ON pf.id = x.source_file_pk
"""


def _nested(columns: str, table: str, condition: str, order: str = "") -> str:
    """A nested-record query: fixed columns of `table` (aliased `x`) plus its provenance. Every
    argument is a constant of this module; caller values are bind parameters only."""
    return (
        f"SELECT {columns}, {_PROVENANCE_COLUMNS} FROM {table} x {_PROVENANCE_JOINS} "  # noqa: S608
        f"WHERE x.dataset_pk = :pk AND {condition} {order}"
    )


def _record(row: Mapping[str, Any]) -> dict[str, Any]:
    """A nested row's own fields plus its `provenance`."""
    fields = {k: v for k, v in row.items() if not k.startswith(_PREFIX)}
    fields["provenance"] = _provenance(row)
    return fields


_DETAIL = f"""
    SELECT {_RETURN_COLUMNS},
           r.dataset_pk, r.received_at,
           oi.catalog_dataset_id, oi.catalog_dataset_version, oi.catalog_raw_sha256,
           rcr.raw_line AS snapshot_line,
           (p.raw_record_id = rcr.id) AS matches_current_catalog
    {_RETURN_FROM}
    JOIN catalog_datasets cd
      ON cd.dataset_id = oi.catalog_dataset_id AND cd.dataset_version = oi.catalog_dataset_version
    JOIN raw_catalog_records rcr
      ON rcr.dataset_pk = cd.id AND rcr.source_key = oi.product_id
     AND rcr.raw_sha256 = oi.catalog_raw_sha256
    WHERE d.dataset_id = :dataset_id COLLATE "C" AND d.dataset_version = :dataset_version
      AND r.return_id = :return_id COLLATE "C"
"""
_ORDER = _nested(
    "x.order_id, x.customer_ref, x.ordered_at, x.currency",
    "orders",
    'x.order_id = :order_id COLLATE "C"',
)
_ORDER_ITEM = _nested(
    "x.order_item_id, x.quantity, x.unit_price",
    "order_items",
    'x.order_item_id = :order_item_id COLLATE "C"',
)
_COMMENTS = _nested(
    "x.comment_id, x.submitted_at, x.text",
    "return_comments",
    'x.return_id = :return_id COLLATE "C"',
    'ORDER BY x.submitted_at, x.comment_id COLLATE "C"',
)
_OBSERVATIONS = _nested(
    "x.observation_id, x.observed_at, x.inspector_ref, x.condition, x.note",
    "inspection_observations",
    'x.return_id = :return_id COLLATE "C"',
    'ORDER BY x.observed_at, x.observation_id COLLATE "C"',
)
_SHIPMENT = _nested(
    "x.shipment_id, x.carrier_ref, x.status, x.shipped_at, x.promised_delivery_by, x.delivered_at",
    "shipments",
    'x.order_id = :order_id COLLATE "C"',
)
_EVENTS = _nested(
    "x.event_id, x.occurred_at, x.event_type, x.source",
    "delivery_events",
    'x.shipment_id = :shipment_id COLLATE "C"',
    'ORDER BY x.occurred_at, x.event_id COLLATE "C"',
)
_REVIEWS = _nested(
    "x.review_id, x.product_id, x.order_item_id, x.rating, x.submitted_at, x.text",
    "customer_reviews",
    'x.order_item_id = :order_item_id COLLATE "C"',
    'ORDER BY x.submitted_at, x.review_id COLLATE "C"',
)
_COMPLAINTS = _nested(
    "x.complaint_id, x.order_id, x.order_item_id, x.product_id, x.opened_at, x.channel, "
    "x.customer_topic, x.text",
    "support_complaints",
    '(x.order_item_id = :order_item_id COLLATE "C" OR x.order_id = :order_id COLLATE "C")',
    'ORDER BY x.opened_at, x.complaint_id COLLATE "C"',
)
# Cases that cite this return, or one of its comments or inspection observations, as evidence
# (§7.1 I1, approved). `evidence_count` counts all of a case's evidence links.
_CASES = _nested(
    "x.case_id, x.focus_area, x.status, x.opened_at, "
    "(SELECT count(*) FROM correction_case_evidence e "
    "WHERE e.dataset_pk = x.dataset_pk AND e.case_id = x.case_id) AS evidence_count",
    "correction_cases",
    """EXISTS (
           SELECT 1 FROM correction_case_evidence e
            WHERE e.dataset_pk = x.dataset_pk AND e.case_id = x.case_id AND (
                  (e.evidence_type = 'return' AND e.evidence_id = :return_id COLLATE "C")
               OR (e.evidence_type = 'return_comment' AND e.evidence_id IN (
                     SELECT comment_id FROM return_comments
                      WHERE dataset_pk = :pk AND return_id = :return_id COLLATE "C"))
               OR (e.evidence_type = 'inspection_observation' AND e.evidence_id IN (
                     SELECT observation_id FROM inspection_observations
                      WHERE dataset_pk = :pk AND return_id = :return_id COLLATE "C"))))""",
    'ORDER BY x.opened_at, x.case_id COLLATE "C"',
)


def _snapshot_fields(line: str) -> dict[str, Any]:
    """The snapshot fields of a raw catalog line, verbatim: the text fields must be strings and
    `price` a JSON integer or string (M1); anything else is a contract breach, never coerced."""
    try:
        record = json.loads(line)
    except ValueError:
        raise SnapshotContractError() from None
    if not isinstance(record, dict):
        raise SnapshotContractError()
    values = {name: record.get(name) for name in SNAPSHOT_TEXT_FIELDS}
    if not all(isinstance(value, str) for value in values.values()):
        raise SnapshotContractError()
    price = record.get("price")
    if type(price) is not int and not isinstance(price, str):  # bool, float and null refused
        raise SnapshotContractError()
    return values | {"price": price}


def get_return(
    engine: Engine, dataset_id: str, dataset_version: int, return_id: str
) -> rs.ReturnDetail | None:
    """One return with every §7 detail section, read in one snapshot; None when absent."""
    key = {"dataset_id": dataset_id, "dataset_version": dataset_version, "return_id": return_id}
    with read_snapshot(engine) as conn:
        row = conn.execute(text(_DETAIL), key).mappings().one_or_none()
        if row is None:
            return None
        scope = {
            "pk": row["dataset_pk"],
            "return_id": return_id,
            "order_id": row["order_id"],
            "order_item_id": row["order_item_id"],
        }

        def fetch(sql: str, **params: Any) -> list[dict[str, Any]]:
            return [_record(r) for r in conn.execute(text(sql), scope | params).mappings()]

        (order,) = fetch(_ORDER)
        (order_item,) = fetch(_ORDER_ITEM)
        comments = fetch(_COMMENTS)
        observations = fetch(_OBSERVATIONS)
        shipments = fetch(_SHIPMENT)  # at most one per order (UNIQUE)
        events = fetch(_EVENTS, shipment_id=shipments[0]["shipment_id"]) if shipments else []
        reviews = fetch(_REVIEWS)
        complaints = fetch(_COMPLAINTS)
        cases = fetch(_CASES)

    shipment = (
        rs.Shipment(**shipments[0], delivery_events=[rs.DeliveryEvent(**e) for e in events])
        if shipments
        else None
    )
    return rs.ReturnDetail(
        ref=rs.ReturnRef(**key),
        requested_at=row["requested_at"],
        status=row["status"],
        quantity=row["quantity"],
        received_at=row["received_at"],
        order=rs.OrderFacts(**order),
        order_item=rs.OrderItemFacts(**order_item),
        product=_product(row),
        listing_snapshot=rs.ListingSnapshot(
            catalog_dataset_id=row["catalog_dataset_id"],
            catalog_dataset_version=row["catalog_dataset_version"],
            raw_sha256=row["catalog_raw_sha256"],
            matches_current_catalog=row["matches_current_catalog"],
            **_snapshot_fields(row["snapshot_line"]),
        ),
        customer_statements=rs.CustomerStatements(
            customer_reason_code=row["customer_reason_code"],
            comments=[rs.ReturnComment(**c) for c in comments],
        ),
        inspection_observations=[rs.InspectionObservation(**o) for o in observations],
        shipment=shipment,
        related_reviews=[rs.RelatedReview(**r) for r in reviews],
        related_complaints=[rs.RelatedComplaint(**c) for c in complaints],
        correction_cases=[rs.CorrectionCaseRef(**c) for c in cases],
        evidence_presence=rs.EvidencePresence(
            return_comments=len(comments),
            inspection_observations=len(observations),
            shipment=len(shipments),
            delivery_events=len(events),
            related_reviews=len(reviews),
            related_complaints=len(complaints),
            correction_cases=len(cases),
        ),
        provenance=_provenance(row),
    )


# ---- import batches --------------------------------------------------------------------------

_BATCH_COUNTS = ", ".join(
    f"b.{e.value}_{kind}" for e in rc.Entity for kind in ("received", "new", "unchanged")
)
# Fixed column names from `rc.Entity`; no caller value is interpolated.
_BATCH_SELECT = f"""
    SELECT b.id, b.batch_id, d.dataset_id, d.dataset_version, b.contract_version,
           b.transformation_version, b.redaction_version, b.bundle_sha256, b.format,
           b.file_count, b.total_bytes, b.imported_by_label, b.imported_at, {_BATCH_COUNTS}
      FROM return_import_batches b
      JOIN return_datasets d ON d.id = b.dataset_pk
"""  # noqa: S608
_BATCH_SEEK = (
    "WHERE (b.imported_at < :after_imported_at OR "
    "(b.imported_at = :after_imported_at AND b.batch_id > :after_batch_id))"
)
_BATCH_ORDER = "ORDER BY b.imported_at DESC, b.batch_id ASC"
_FILES = """
    SELECT name, entity, media_type, size_bytes, raw_sha256, row_count
      FROM return_import_files WHERE batch_pk = :batch_pk ORDER BY name COLLATE "C"
"""


def _batch_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": row["batch_id"],
        "dataset_id": row["dataset_id"],
        "dataset_version": row["dataset_version"],
        "contract_version": row["contract_version"],
        "transformation_version": row["transformation_version"],
        "redaction_version": row["redaction_version"],
        "bundle_sha256": row["bundle_sha256"],
        "format": row["format"],
        "file_count": row["file_count"],
        "total_bytes": row["total_bytes"],
        "counts": {
            e: rs.BatchEntityCounts(
                received=row[f"{e.value}_received"],
                new=row[f"{e.value}_new"],
                unchanged=row[f"{e.value}_unchanged"],
            )
            for e in rc.Entity
        },
        "imported_by_label": row["imported_by_label"],
        "imported_at": row["imported_at"],
    }


def list_batches(
    engine: Engine, limit: int, after: rs.BatchPosition | None
) -> list[rs.BatchSummary]:
    """Up to `limit + 1` batches after `after`, newest first."""
    params: dict[str, Any] = {"fetch": limit + 1}
    seek = ""
    if after is not None:
        seek = _BATCH_SEEK
        params |= {"after_imported_at": after.imported_at, "after_batch_id": after.batch_id}
    with read_snapshot(engine) as conn:
        rows = (
            conn.execute(text(f"{_BATCH_SELECT} {seek} {_BATCH_ORDER} LIMIT :fetch"), params)
            .mappings()
            .all()
        )
    return [rs.BatchSummary(**_batch_fields(row)) for row in rows]


def get_batch(engine: Engine, batch_id: UUID) -> rs.BatchDetail | None:
    with read_snapshot(engine) as conn:
        row = (
            conn.execute(
                text(f"{_BATCH_SELECT} WHERE b.batch_id = :batch_id"), {"batch_id": batch_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        files = conn.execute(text(_FILES), {"batch_pk": row["id"]}).mappings().all()
    return rs.BatchDetail(**_batch_fields(row), files=[rs.BatchFile(**f) for f in files])
