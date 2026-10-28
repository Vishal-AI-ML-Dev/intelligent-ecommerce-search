"""Retailer return read API (Milestone 18-04C, contracts §7, §7.1): query parameters, response
models and opaque page cursors.

**Responses** are exact field allowlists (`extra="forbid"`, AC-05). Timestamps use the contract's
canonical UTC form and money its two-digit decimal text (`returns/canonical.py`). Free text is the
stored redacted form only (`redaction-1`). There is no score, flag, verdict, refund, fault or
customer-level aggregate field: conflicting evidence is listed side by side (§3.1).

**Cursors** (§7, §7.1) are base64url (no padding) of compact JSON with sorted keys. They carry the
last item's position in the total order plus `filters_sha256`: the SHA-256 of the canonical JSON of
the *validated* filters that the query itself uses (absent filters, `limit` and `cursor`
excluded; timestamps in canonical UTC). A cursor that is not exactly such an encoding is
`malformed_cursor`; one minted under other filters is `cursor_mismatch`.
"""

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc

CURSOR_VERSION = 1
CURSOR_MAX_LENGTH = 512  # far above any valid cursor; longer values are refused undecoded
LIMIT_DEFAULT = 25
LIMIT_MAX = 100
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RECORD_ID = re.compile(rc.RECORD_ID_PATTERN)
_DATASET_ID = re.compile(rc.DATASET_ID_PATTERN)

DatasetVersion = Annotated[int, Field(ge=1, le=rc.DATASET_VERSION_MAX)]
Limit = Annotated[int, Field(ge=1, le=LIMIT_MAX)]
# M1: the raw catalog `price` as stored, a JSON integer or seller text; never coerced.
RawPrice = Annotated[StrictInt | StrictStr, Field(union_mode="left_to_right")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Query(BaseModel):
    """Query parameters. Values are never echoed in errors (§5.1)."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


# ---- query parameters ------------------------------------------------------------------------


class ReturnFilters(_Query):
    """`GET /returns` (§7 list filters, §7.1 D6a/E4). `requested_from` is inclusive and
    `requested_to` exclusive; both need RFC 3339 with an explicit offset."""

    dataset_id: rc.DatasetId | None = None
    dataset_version: DatasetVersion | None = None
    status: rc.ReturnStatus | None = None
    customer_reason_code: rc.ReasonCode | None = None
    category: Category | None = None
    product_id: rc.RecordId | None = None
    requested_from: rc.Timestamp | None = None
    requested_to: rc.Timestamp | None = None
    has_comment: bool | None = None
    has_inspection: bool | None = None
    batch_id: UUID | None = None
    limit: Limit = LIMIT_DEFAULT
    cursor: str | None = None

    def canonical_filters(self) -> dict[str, Any]:
        """The provided filters in canonical JSON form; `limit` and `cursor` are not filters."""
        return self.model_dump(mode="json", exclude={"limit", "cursor"}, exclude_none=True)


class BatchPageParameters(_Query):
    """`GET /returns/import-batches` (§7.1 D6c): the cursor pattern with no filters."""

    limit: Limit = LIMIT_DEFAULT
    cursor: str | None = None

    def canonical_filters(self) -> dict[str, Any]:
        return {}


def filters_sha256(filters: dict[str, Any]) -> str:
    blob = json.dumps(filters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


# ---- cursors ---------------------------------------------------------------------------------


class CursorError(Exception):
    """`malformed_cursor` or `cursor_mismatch`."""

    def __init__(self, code: Literal["malformed_cursor", "cursor_mismatch"]) -> None:
        super().__init__(code)
        self.code = code


def encode_cursor(payload: dict[str, Any]) -> str:
    blob = json.dumps(
        {"v": CURSOR_VERSION, **payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return base64.urlsafe_b64encode(blob.encode("ascii")).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str, keys: frozenset[str]) -> dict[str, Any]:
    """The payload of a cursor that is exactly `encode_cursor` output with these keys."""
    if len(cursor) > CURSOR_MAX_LENGTH or not _BASE64URL.fullmatch(cursor):
        raise CursorError("malformed_cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw.decode("ascii"))
    except (binascii.Error, ValueError):  # UnicodeDecodeError and JSONDecodeError included
        raise CursorError("malformed_cursor") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != keys | {"v", "filters_sha256"}
        or payload["v"] != CURSOR_VERSION
        or type(payload["v"]) is not int
        or not isinstance(payload["filters_sha256"], str)
        or not _SHA256.fullmatch(payload["filters_sha256"])
    ):
        raise CursorError("malformed_cursor")
    if encode_cursor({k: v for k, v in payload.items() if k != "v"}) != cursor:
        raise CursorError("malformed_cursor")  # one encoding per position
    return payload


def _canonical_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise CursorError("malformed_cursor")
    try:
        parsed = cf.parse_timestamp(value)
    except cf.CanonicalFormError:
        raise CursorError("malformed_cursor") from None
    if cf.format_timestamp(parsed) != value:
        raise CursorError("malformed_cursor")
    return parsed


def _check_filters(payload: dict[str, Any], expected_sha256: str) -> None:
    if payload["filters_sha256"] != expected_sha256:
        raise CursorError("cursor_mismatch")


class ReturnPosition(_Model):
    """A position in the §7 total order of returns."""

    requested_at: datetime
    dataset_id: str
    dataset_version: int
    return_id: str


def return_cursor(position: ReturnPosition, sha256: str) -> str:
    return encode_cursor(
        {
            "requested_at": cf.format_timestamp(position.requested_at),
            "dataset_id": position.dataset_id,
            "dataset_version": position.dataset_version,
            "return_id": position.return_id,
            "filters_sha256": sha256,
        }
    )


def decode_return_cursor(cursor: str, expected_sha256: str) -> ReturnPosition:
    payload = _decode_cursor(
        cursor, frozenset({"requested_at", "dataset_id", "dataset_version", "return_id"})
    )
    requested_at = _canonical_instant(payload["requested_at"])
    dataset_id, version, return_id = (
        payload["dataset_id"],
        payload["dataset_version"],
        payload["return_id"],
    )
    if (
        not isinstance(dataset_id, str)
        or not _DATASET_ID.fullmatch(dataset_id)
        or type(version) is not int
        or not 1 <= version <= rc.DATASET_VERSION_MAX
        or not isinstance(return_id, str)
        or not _RECORD_ID.fullmatch(return_id)
    ):
        raise CursorError("malformed_cursor")
    _check_filters(payload, expected_sha256)
    return ReturnPosition(
        requested_at=requested_at,
        dataset_id=dataset_id,
        dataset_version=version,
        return_id=return_id,
    )


class BatchPosition(_Model):
    """A position in the batch order: `imported_at` desc, then `batch_id` asc (§7.1 E2)."""

    imported_at: datetime
    batch_id: UUID


def batch_cursor(position: BatchPosition, sha256: str) -> str:
    return encode_cursor(
        {
            "imported_at": cf.format_timestamp(position.imported_at),
            "batch_id": str(position.batch_id),
            "filters_sha256": sha256,
        }
    )


def decode_batch_cursor(cursor: str, expected_sha256: str) -> BatchPosition:
    payload = _decode_cursor(cursor, frozenset({"imported_at", "batch_id"}))
    imported_at = _canonical_instant(payload["imported_at"])
    raw_id = payload["batch_id"]
    try:
        batch_id = UUID(raw_id) if isinstance(raw_id, str) else None
    except ValueError:
        batch_id = None
    if batch_id is None or str(batch_id) != raw_id:
        raise CursorError("malformed_cursor")
    _check_filters(payload, expected_sha256)
    return BatchPosition(imported_at=imported_at, batch_id=batch_id)


# ---- error bodies (documentation of the safe `/returns` error shape, §7, §6.2 A2) -------------


class ReturnsErrorBody(_Model):
    detail: str
    code: str


class ParameterError(_Model):
    name: str
    code: str


class ReturnsValidationErrorBody(_Model):
    detail: str
    code: Literal["invalid_parameter"]
    parameters: list[ParameterError]


# ---- returns ---------------------------------------------------------------------------------


class ReturnRef(_Model):
    dataset_id: str
    dataset_version: int
    return_id: str


class ProductRef(_Model):
    """The product as it is in the current catalog (`products`); `sku` = `product_id` (D3)."""

    product_id: str
    sku: str
    title: str
    category: str
    brand: str


class Provenance(_Model):
    """MASTER_PLAN §9's eight fields (plan C4/D13, Option A) plus the import location, resolved
    from the record's own dataset, batch and source-file references (never copied from a
    parent record)."""

    source_type: str
    source_name: str
    license: str | None  # `license_identifier` is optional in the dataset contract (§2)
    source_record_id: str
    dataset_version: int
    ingestion_timestamp: rc.Timestamp
    transformation_version: str
    is_synthetic: bool
    batch_id: UUID
    source_file: str
    source_row: int


class ReturnSummary(_Model):
    ref: ReturnRef
    requested_at: rc.Timestamp
    status: rc.ReturnStatus
    quantity: int
    customer_reason_code: rc.ReasonCode
    product: ProductRef
    order_id: str
    order_item_id: str
    comment_count: int
    inspection_count: int
    is_synthetic: bool
    provenance: Provenance  # §7 amendment (owner, 2026-10-28): the return's own provenance


class ReturnPage(_Model):
    items: list[ReturnSummary]
    next_cursor: str | None
    limit: int


class OrderFacts(_Model):
    order_id: str
    customer_ref: str
    ordered_at: rc.Timestamp
    currency: rc.Currency
    provenance: Provenance


class OrderItemFacts(_Model):
    order_item_id: str
    quantity: int
    unit_price: rc.Money
    provenance: Provenance


class ListingSnapshot(_Model):
    """The immutable catalog line referenced by the order item (D4). The fields are the values
    on that raw line, verbatim and uncoerced; `price` keeps its raw JSON type, an integer or the
    seller's text (M1, §7.1). `matches_current_catalog` says whether the product's current
    catalog record is that same line."""

    catalog_dataset_id: str
    catalog_dataset_version: str
    raw_sha256: str
    matches_current_catalog: bool
    title: str
    brand: str
    category: str
    currency: str
    price: RawPrice


class ReturnComment(_Model):
    comment_id: str
    submitted_at: rc.Timestamp
    text: str  # redacted
    provenance: Provenance


class CustomerStatements(_Model):
    customer_reason_code: rc.ReasonCode
    comments: list[ReturnComment]


class InspectionObservation(_Model):
    observation_id: str
    observed_at: rc.Timestamp
    inspector_ref: str
    condition: rc.InspectionCondition
    note: str | None  # redacted
    provenance: Provenance


class DeliveryEvent(_Model):
    event_id: str
    occurred_at: rc.Timestamp
    event_type: rc.DeliveryEventType
    source: rc.DeliveryEventSource
    provenance: Provenance


class Shipment(_Model):
    shipment_id: str
    carrier_ref: str
    status: rc.ShipmentStatus
    shipped_at: rc.Timestamp | None
    promised_delivery_by: rc.Timestamp | None
    delivered_at: rc.Timestamp | None
    delivery_events: list[DeliveryEvent]
    provenance: Provenance


class RelatedReview(_Model):
    review_id: str
    product_id: str
    order_item_id: str | None
    rating: int
    submitted_at: rc.Timestamp
    text: str | None  # redacted
    provenance: Provenance


class RelatedComplaint(_Model):
    complaint_id: str
    order_id: str | None
    order_item_id: str | None
    product_id: str | None
    opened_at: rc.Timestamp
    channel: rc.ComplaintChannel
    customer_topic: rc.ComplaintTopic | None
    text: str  # redacted
    provenance: Provenance


class CorrectionCaseRef(_Model):
    case_id: str
    focus_area: rc.CorrectionFocusArea
    status: rc.CorrectionCaseStatus
    opened_at: rc.Timestamp
    evidence_count: int
    provenance: Provenance


class EvidencePresence(_Model):
    """Counts per kind; zero means "not recorded", never "none happened"."""

    return_comments: int
    inspection_observations: int
    shipment: int
    delivery_events: int
    related_reviews: int
    related_complaints: int
    correction_cases: int


class ReturnDetail(_Model):
    ref: ReturnRef
    requested_at: rc.Timestamp
    status: rc.ReturnStatus
    quantity: int
    received_at: rc.Timestamp | None
    order: OrderFacts
    order_item: OrderItemFacts
    product: ProductRef
    listing_snapshot: ListingSnapshot
    customer_statements: CustomerStatements
    inspection_observations: list[InspectionObservation]
    shipment: Shipment | None
    related_reviews: list[RelatedReview]
    related_complaints: list[RelatedComplaint]
    correction_cases: list[CorrectionCaseRef]
    evidence_presence: EvidencePresence
    provenance: Provenance


# ---- import batches --------------------------------------------------------------------------


class BatchEntityCounts(_Model):
    received: int
    new: int
    unchanged: int


class BatchSummary(_Model):
    batch_id: UUID
    dataset_id: str
    dataset_version: int
    contract_version: str
    transformation_version: str
    redaction_version: str
    bundle_sha256: str
    format: Literal["json", "csv"]
    file_count: int
    total_bytes: int
    counts: dict[rc.Entity, BatchEntityCounts]
    imported_by_label: str
    imported_at: rc.Timestamp


class BatchFile(_Model):
    """File metadata only; the stored bytes are never selected (§2)."""

    name: str
    entity: rc.Entity | None
    media_type: str
    size_bytes: int
    raw_sha256: str
    row_count: int | None


class BatchDetail(BatchSummary):
    files: list[BatchFile]


class BatchPage(_Model):
    items: list[BatchSummary]
    next_cursor: str | None
    limit: int
