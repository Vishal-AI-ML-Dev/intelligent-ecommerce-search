"""Typed `return-data-v1` record contracts (contracts §1, §3).

One frozen Pydantic model per entity holds exactly that entity's contract fields in canonical
form (`canonical.py`). The models validate field rules and row-local consistency only; rules
that need other records (references, snapshot equality, cross-table time order, one shipment
per order, the quantity aggregate, evidence-to-product relations) belong to the import service
and the database backstops.

Validation errors carry `RowError` codes (§4.8) as their error type where the code is defined
here (`format`, `type`, `required`, `too_long`, `out_of_range`, `not_synthetic`,
`temporal_order`); enumerations fail with Pydantic's `enum` type. Inputs are never echoed in
errors (`hide_input_in_errors`).

The kinds stay distinct by construction: customer statements (`returns.customer_reason_code`,
return comments, support complaints, customer reviews) and inspection observations are
separate records. There is no hypothesis, decision, refund, fault or responsibility field.
"""

import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    Strict,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ecommerce_search.returns import canonical as cf

# Patterns without anchors; matched with `re.fullmatch` (a trailing `$` would accept "\n").
DATASET_ID_PATTERN = r"[a-z0-9][a-z0-9-]{0,47}"
RECORD_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
ACTOR_REF_PATTERN = r"SYN-(CUST|INSP|STAFF)-[0-9]{5}"
CATALOG_VERSION_PATTERN = r"[1-9][0-9]{0,8}"  # catalog_datasets.dataset_version (text)
DATASET_VERSION_MAX = 999_999_999
RECORD_ID_MAX = 64
EVIDENCE_KEY_SEPARATOR = "|"  # outside the record-ID alphabet
TEXT_MAX = 2000
NOTE_MAX = 1000
DATASET_LONG_TEXT_MAX = 2000
DATASET_SHORT_TEXT_MAX = 200
ORDER_ITEM_QUANTITY_MAX = 10


class Entity(StrEnum):
    """The eleven entities; each value is also its table name and CSV base name."""

    ORDERS = "orders"
    ORDER_ITEMS = "order_items"
    SHIPMENTS = "shipments"
    DELIVERY_EVENTS = "delivery_events"
    RETURNS = "returns"
    RETURN_COMMENTS = "return_comments"
    INSPECTION_OBSERVATIONS = "inspection_observations"
    CUSTOMER_REVIEWS = "customer_reviews"
    SUPPORT_COMPLAINTS = "support_complaints"
    CORRECTION_CASES = "correction_cases"
    CORRECTION_CASE_EVIDENCE = "correction_case_evidence"


class SourceType(StrEnum):
    SYNTHETIC = "synthetic"  # the only source type v1 accepts


class Currency(StrEnum):
    INR = "INR"


class ShipmentStatus(StrEnum):
    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    RETURNED_TO_ORIGIN = "returned_to_origin"
    UNKNOWN = "unknown"


class DeliveryEventType(StrEnum):
    DISPATCHED = "dispatched"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELIVERY_ATTEMPT_FAILED = "delivery_attempt_failed"
    DELAY_NOTIFIED = "delay_notified"
    DAMAGE_REPORTED_BY_CARRIER = "damage_reported_by_carrier"
    RETURNED_TO_ORIGIN = "returned_to_origin"


class DeliveryEventSource(StrEnum):
    CARRIER = "carrier"
    WAREHOUSE = "warehouse"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    IN_TRANSIT_TO_WAREHOUSE = "in_transit_to_warehouse"
    RECEIVED = "received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ReasonCode(StrEnum):
    """`return-reason-codes-1`: the customer's own choice, a statement, not a classification."""

    SIZE_TOO_SMALL = "SIZE_TOO_SMALL"
    SIZE_TOO_LARGE = "SIZE_TOO_LARGE"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    LOOKS_DIFFERENT_FROM_IMAGE = "LOOKS_DIFFERENT_FROM_IMAGE"
    SPECIFICATION_DIFFERENT = "SPECIFICATION_DIFFERENT"
    DEFECTIVE_OR_NOT_WORKING = "DEFECTIVE_OR_NOT_WORKING"
    ARRIVED_DAMAGED = "ARRIVED_DAMAGED"
    WRONG_ITEM_RECEIVED = "WRONG_ITEM_RECEIVED"
    ARRIVED_LATE = "ARRIVED_LATE"
    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    OTHER = "OTHER"
    NOT_PROVIDED = "NOT_PROVIDED"


class InspectionCondition(StrEnum):
    """What an inspector saw. Not a decision on refund, fault or conduct."""

    AS_NEW = "as_new"
    USED_GOOD = "used_good"
    DAMAGED = "damaged"
    FUNCTIONAL_FAULT_CONFIRMED = "functional_fault_confirmed"
    NO_FAULT_FOUND = "no_fault_found"
    ITEM_DIFFERS_FROM_ORDER = "item_differs_from_order"
    PARTS_MISSING = "parts_missing"
    NOT_ASSESSABLE = "not_assessable"


class ComplaintChannel(StrEnum):
    EMAIL = "email"
    CHAT = "chat"
    PHONE = "phone"
    OTHER = "other"


class ComplaintTopic(StrEnum):
    DELIVERY = "delivery"
    DAMAGED = "damaged"
    WRONG_ITEM = "wrong_item"
    PRODUCT_ISSUE = "product_issue"
    RETURN_PROCESS = "return_process"
    OTHER = "other"


class CorrectionFocusArea(StrEnum):
    """The area a person asked to investigate. Not a finding."""

    LISTING_TEXT = "listing_text"
    SIZE_INFORMATION = "size_information"
    SPECIFICATION = "specification"
    IMAGERY = "imagery"
    PACKAGING = "packaging"
    PRODUCT_QUALITY = "product_quality"
    FULFILMENT = "fulfilment"
    LOGISTICS = "logistics"
    OTHER = "other"


class CorrectionCaseStatus(StrEnum):
    OPENED = "opened"  # D5: no transitions in v1


class EvidenceType(StrEnum):
    RETURN = "return"
    RETURN_COMMENT = "return_comment"
    INSPECTION_OBSERVATION = "inspection_observation"
    CUSTOMER_REVIEW = "customer_review"
    SUPPORT_COMPLAINT = "support_complaint"
    SHIPMENT = "shipment"
    DELIVERY_EVENT = "delivery_event"


EVIDENCE_ENTITIES: dict[EvidenceType, Entity] = {
    EvidenceType.RETURN: Entity.RETURNS,
    EvidenceType.RETURN_COMMENT: Entity.RETURN_COMMENTS,
    EvidenceType.INSPECTION_OBSERVATION: Entity.INSPECTION_OBSERVATIONS,
    EvidenceType.CUSTOMER_REVIEW: Entity.CUSTOMER_REVIEWS,
    EvidenceType.SUPPORT_COMPLAINT: Entity.SUPPORT_COMPLAINTS,
    EvidenceType.SHIPMENT: Entity.SHIPMENTS,
    EvidenceType.DELIVERY_EVENT: Entity.DELIVERY_EVENTS,
}
# 64 + 1 + len("inspection_observation") + 1 + 64
EVIDENCE_KEY_MAX = 2 * RECORD_ID_MAX + 2 + max(len(t.value) for t in EvidenceType)


# ---- field types ---------------------------------------------------------------------------


def _error(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, message)


def _canonical(parse: Callable[[object], Any]) -> Callable[[object], Any]:
    def validate(value: object) -> Any:
        try:
            return parse(value)
        except cf.CanonicalFormError as exc:
            raise _error(exc.code, str(exc)) from None

    return validate


def _pattern(pattern: str) -> Callable[[str], str]:
    compiled = re.compile(pattern)

    def validate(value: str) -> str:
        if compiled.fullmatch(value) is None:
            raise _error("format", "value does not match the required pattern")
        return value

    return validate


def _text(maximum: int, *, require_visible: bool = False) -> Callable[[str], str]:
    def validate(value: str) -> str:
        if value == "" or (require_visible and value.strip() == ""):
            raise _error("required", "text must not be empty or whitespace only")
        if len(value) > maximum:  # code points
            raise _error("too_long", "text is longer than allowed")
        return value

    return validate


def _int_range(low: int, high: int | None = None) -> Callable[[int], int]:
    def validate(value: int) -> int:
        if value < low or (high is not None and value > high):
            raise _error("out_of_range", "integer is out of range")
        return value

    return validate


def _synthetic(value: bool) -> bool:
    if value is not True:
        raise _error("not_synthetic", "v1 accepts synthetic records only")
    return value


def _synthetic_source(value: object) -> object:
    if isinstance(value, str) and value != SourceType.SYNTHETIC.value:
        raise _error("not_synthetic", "v1 accepts source_type synthetic only")
    return value


Timestamp = Annotated[
    datetime,
    BeforeValidator(_canonical(cf.parse_timestamp)),
    PlainSerializer(cf.format_timestamp, return_type=str),
]
CalendarDate = Annotated[
    date,
    BeforeValidator(_canonical(cf.parse_date)),
    PlainSerializer(cf.format_date, return_type=str),
]
Money = Annotated[
    Decimal,
    BeforeValidator(_canonical(cf.parse_money)),
    PlainSerializer(cf.format_money, return_type=str),
]
StrictText = Annotated[str, Strict()]
RecordId = Annotated[str, Strict(), AfterValidator(_pattern(RECORD_ID_PATTERN))]
DatasetId = Annotated[str, Strict(), AfterValidator(_pattern(DATASET_ID_PATTERN))]
ActorRef = Annotated[str, Strict(), AfterValidator(_pattern(ACTOR_REF_PATTERN))]
CatalogVersion = Annotated[str, Strict(), AfterValidator(_pattern(CATALOG_VERSION_PATTERN))]
Sha256 = Annotated[str, Strict(), AfterValidator(_pattern(r"[0-9a-f]{64}"))]
FreeText = Annotated[str, Strict(), AfterValidator(_text(TEXT_MAX, require_visible=True))]
OptionalText = Annotated[str, Strict(), AfterValidator(_text(TEXT_MAX))]
Note = Annotated[str, Strict(), AfterValidator(_text(NOTE_MAX))]
DatasetLongText = Annotated[str, Strict(), AfterValidator(_text(DATASET_LONG_TEXT_MAX))]
DatasetShortText = Annotated[str, Strict(), AfterValidator(_text(DATASET_SHORT_TEXT_MAX))]
SyntheticFlag = Annotated[bool, Strict(), AfterValidator(_synthetic)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def canonical(self) -> dict[str, Any]:
        """Every contract field in canonical JSON form; absent optional values are `None`."""
        return self.model_dump(mode="json")


# ---- dataset descriptor --------------------------------------------------------------------


class DatasetDescriptor(_Contract):
    """The `dataset` block. A stored dataset version is immutable (`dataset_mismatch`)."""

    dataset_id: DatasetId
    dataset_version: Annotated[int, Strict(), AfterValidator(_int_range(1, DATASET_VERSION_MAX))]
    source_type: Annotated[SourceType, BeforeValidator(_synthetic_source)]
    source_name: DatasetShortText
    source_description: DatasetLongText
    rights_note: DatasetLongText
    license_identifier: DatasetShortText | None = None
    authored_on: CalendarDate
    generator: DatasetShortText
    is_synthetic: SyntheticFlag
    notes: DatasetLongText | None = None

    def descriptor_sha256(self) -> str:
        return cf.descriptor_sha256(self.canonical())


# ---- entity records ------------------------------------------------------------------------


class ReturnRecord(_Contract):
    """Base of the eleven entity records."""

    ENTITY: ClassVar[Entity]
    ID_FIELDS: ClassVar[tuple[str, ...]]

    def source_record_id(self) -> str:
        return EVIDENCE_KEY_SEPARATOR.join(str(getattr(self, f)) for f in self.ID_FIELDS)

    def record_sha256(self) -> str:
        return cf.record_sha256(self.ENTITY.value, self.canonical())


def _temporal(earlier: datetime | None, later: datetime | None, message: str) -> None:
    if earlier is not None and later is not None and earlier > later:
        raise _error("temporal_order", message)


class Order(ReturnRecord):
    ENTITY = Entity.ORDERS
    ID_FIELDS = ("order_id",)

    order_id: RecordId
    customer_ref: ActorRef
    ordered_at: Timestamp
    currency: Currency
    is_synthetic: SyntheticFlag


class OrderItem(ReturnRecord):
    """`sku` is derived as `product_id` (D3) and is not an input. The snapshot triple must equal
    the stored `raw_catalog_records` row for `source_key = product_id` (checked on import)."""

    ENTITY = Entity.ORDER_ITEMS
    ID_FIELDS = ("order_item_id",)

    order_item_id: RecordId
    order_id: RecordId
    product_id: RecordId
    quantity: Annotated[int, Strict(), AfterValidator(_int_range(1, ORDER_ITEM_QUANTITY_MAX))]
    unit_price: Money
    catalog_dataset_id: DatasetId
    catalog_dataset_version: CatalogVersion
    catalog_raw_sha256: Sha256
    is_synthetic: SyntheticFlag


class Shipment(ReturnRecord):
    ENTITY = Entity.SHIPMENTS
    ID_FIELDS = ("shipment_id",)

    shipment_id: RecordId
    order_id: RecordId
    carrier_ref: RecordId
    status: ShipmentStatus
    shipped_at: Timestamp | None = None
    promised_delivery_by: Timestamp | None = None
    delivered_at: Timestamp | None = None
    is_synthetic: SyntheticFlag

    @model_validator(mode="after")
    def _delivery(self) -> Self:
        delivered = self.status is ShipmentStatus.DELIVERED
        if delivered and self.delivered_at is None:
            raise _error("required", "delivered_at is required when status is delivered")
        if not delivered and self.delivered_at is not None:
            raise _error("format", "delivered_at is allowed only when status is delivered")
        _temporal(self.shipped_at, self.delivered_at, "shipped_at is after delivered_at")
        return self


class DeliveryEvent(ReturnRecord):
    ENTITY = Entity.DELIVERY_EVENTS
    ID_FIELDS = ("event_id",)

    event_id: RecordId
    shipment_id: RecordId
    occurred_at: Timestamp
    event_type: DeliveryEventType
    source: DeliveryEventSource
    is_synthetic: SyntheticFlag


class Return(ReturnRecord):
    """A snapshot of the exporter's state. No refund, resolution, fault or responsibility field."""

    ENTITY = Entity.RETURNS
    ID_FIELDS = ("return_id",)

    return_id: RecordId
    order_item_id: RecordId
    requested_at: Timestamp
    quantity: Annotated[int, Strict(), AfterValidator(_int_range(1))]
    customer_reason_code: ReasonCode
    status: ReturnStatus
    received_at: Timestamp | None = None
    is_synthetic: SyntheticFlag

    @model_validator(mode="after")
    def _receipt(self) -> Self:
        if self.status is ReturnStatus.RECEIVED and self.received_at is None:
            raise _error("required", "received_at is required when status is received")
        if self.status is ReturnStatus.CANCELLED and self.received_at is not None:
            raise _error("format", "received_at is not allowed on a cancelled return")
        _temporal(self.requested_at, self.received_at, "requested_at is after received_at")
        return self


class ReturnComment(ReturnRecord):
    """A customer statement."""

    ENTITY = Entity.RETURN_COMMENTS
    ID_FIELDS = ("comment_id",)

    comment_id: RecordId
    return_id: RecordId
    submitted_at: Timestamp
    text: FreeText
    is_synthetic: SyntheticFlag


class InspectionObservation(ReturnRecord):
    """What was seen at inspection. Not a decision on refund, fault or conduct."""

    ENTITY = Entity.INSPECTION_OBSERVATIONS
    ID_FIELDS = ("observation_id",)

    observation_id: RecordId
    return_id: RecordId
    observed_at: Timestamp
    inspector_ref: ActorRef
    condition: InspectionCondition
    note: Note | None = None
    is_synthetic: SyntheticFlag


class CustomerReview(ReturnRecord):
    """A product review. Never a return event."""

    ENTITY = Entity.CUSTOMER_REVIEWS
    ID_FIELDS = ("review_id",)

    review_id: RecordId
    product_id: RecordId
    order_item_id: RecordId | None = None
    rating: Annotated[int, Strict(), AfterValidator(_int_range(1, 5))]
    submitted_at: Timestamp
    text: OptionalText | None = None
    is_synthetic: SyntheticFlag


class SupportComplaint(ReturnRecord):
    """A customer statement."""

    ENTITY = Entity.SUPPORT_COMPLAINTS
    ID_FIELDS = ("complaint_id",)

    complaint_id: RecordId
    order_id: RecordId | None = None
    order_item_id: RecordId | None = None
    product_id: RecordId | None = None
    opened_at: Timestamp
    channel: ComplaintChannel
    customer_topic: ComplaintTopic | None = None
    text: FreeText
    is_synthetic: SyntheticFlag

    @model_validator(mode="after")
    def _subject(self) -> Self:
        if self.order_id is None and self.order_item_id is None and self.product_id is None:
            raise _error("required", "one of order_id, order_item_id, product_id is required")
        return self


class CorrectionCase(ReturnRecord):
    """An opened investigation. `focus_area` is what was asked about, not a finding."""

    ENTITY = Entity.CORRECTION_CASES
    ID_FIELDS = ("case_id",)

    case_id: RecordId
    product_id: RecordId
    catalog_dataset_id: DatasetId
    catalog_dataset_version: CatalogVersion
    catalog_raw_sha256: Sha256
    opened_at: Timestamp
    opened_by_ref: ActorRef
    focus_area: CorrectionFocusArea
    status: CorrectionCaseStatus
    note: Note | None = None
    is_synthetic: SyntheticFlag


class CorrectionCaseEvidence(ReturnRecord):
    """Links a case to one stored record of the same dataset version. Natural key: all three."""

    ENTITY = Entity.CORRECTION_CASE_EVIDENCE
    ID_FIELDS = ("case_id", "evidence_type", "evidence_id")

    case_id: RecordId
    evidence_type: EvidenceType
    evidence_id: RecordId
    is_synthetic: SyntheticFlag


ENTITY_MODELS: dict[Entity, type[ReturnRecord]] = {
    model.ENTITY: model
    for model in (
        Order,
        OrderItem,
        Shipment,
        DeliveryEvent,
        Return,
        ReturnComment,
        InspectionObservation,
        CustomerReview,
        SupportComplaint,
        CorrectionCase,
        CorrectionCaseEvidence,
    )
}
# Free-text fields: stored only in `redaction-1` form, with per-kind counts (§5.1).
FREE_TEXT_FIELDS: dict[Entity, str] = {
    Entity.RETURN_COMMENTS: "text",
    Entity.INSPECTION_OBSERVATIONS: "note",
    Entity.CUSTOMER_REVIEWS: "text",
    Entity.SUPPORT_COMPLAINTS: "text",
    Entity.CORRECTION_CASES: "note",
}
REDACTION_KINDS = ("email", "upi", "number", "pin")
