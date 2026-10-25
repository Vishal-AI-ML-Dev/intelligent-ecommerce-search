"""Phase A validation of one upload without any database access (contracts §4.3 steps 1-4).

`validate_upload` runs, in order:

1. envelope and limits, 2. encoding and parsing (`bundle.py`);
3. field rules: the `contracts.py` models (CSV integers and booleans via `canonical.py`),
   `looks_personal` screening of record IDs, `carrier_ref` and dataset text (§5.1), and
   `redaction-1` of free text;
4. uniqueness and structural rules inside the bundle (§3.1): natural keys, one shipment per
   order, the non-cancelled quantity rule, cross-record time order and nothing later than
   `checked_at`, an inspection only on a received or closed return, and product consistency of
   reviews, complaints and correction-case evidence.

Step 4 rejects only what the bundle alone proves impossible. A reference to a record that is
not in the bundle may point to a stored record, so it is never an error here: references,
catalog rows (step 5) and every stored-data rule are left to `DEFERRED_CHECKS`, which this
module does not run. Conflicting evidence (§3.1, e.g. a "damaged" comment and an `as_new`
inspection) is valid and is never flagged. A report without errors therefore does **not** mean
the upload is valid or that a later commit will succeed.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, get_args

from pydantic import BaseModel, ConfigDict, PlainSerializer, ValidationError

from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns.bundle import BundleFile, Format, ParsedBundle, Part, parse_upload
from ecommerce_search.returns.redaction import Redaction, matches_any, redact, safe_preview
from ecommerce_search.returns.versions import (
    CONTRACT_VERSION,
    REDACTION_VERSION,
    TRANSFORMATION_VERSION,
)

E = rc.Entity

# Not run by Phase A steps 1-4: catalog references (step 5) and every rule that needs stored
# rows (Phase B, re-run under the advisory lock on commit). Reserved for the import service.
DEFERRED_CHECKS = (
    "catalog_references",  # unknown_product, snapshot_mismatch
    "dataset_descriptor",  # dataset_mismatch
    "bundle_already_imported",
    "stored_natural_keys",  # unchanged versus conflict_existing
    "stored_references",  # unknown_reference, incl. every reference not found in the bundle
    "stored_time_rules",
    "stored_shipments",  # duplicate_shipment against stored shipments
    "stored_quantities",  # quantity_exceeded including stored non-cancelled returns
    # Evidence-to-product relations not decided by the bundle alone, including every
    # `shipment` and `delivery_event` link: an order's stored items may contain the product.
    "stored_evidence_relations",
)

# Values never previewed: free text, actor references (dataset text is never previewed).
_NO_PREVIEW = frozenset({"text", "note", "customer_ref", "inspector_ref", "opened_by_ref"})
_PREVIEW_CODES = frozenset({"enum", "format", "type", "out_of_range"})
_CUSTOM_CODES = frozenset(
    {"format", "type", "required", "too_long", "out_of_range", "not_synthetic", "temporal_order"}
)
# Bounded by checked_at; `promised_delivery_by` is a promise, not an event time.
_NOT_TIME_BOUNDED = frozenset({"promised_delivery_by"})


def id_fields(model: type[rc.ReturnRecord]) -> tuple[str, ...]:
    """Record-ID fields screened for personal-data patterns (§5.1): IDs and `carrier_ref`."""
    return tuple(
        name
        for name in model.model_fields
        if (name.endswith("_id") and name != "catalog_dataset_id") or name == "carrier_ref"
    )


def csv_kinds(model: type[BaseModel]) -> dict[str, type]:
    """Fields whose CSV text is converted before validation (integers and booleans)."""
    kinds = {}
    for name, info in model.model_fields.items():
        args = get_args(info.annotation) or (info.annotation,)
        if bool in args:
            kinds[name] = bool
        elif int in args:
            kinds[name] = int
    return kinds


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    record: rc.ReturnRecord
    file: str
    row: int
    redaction: Redaction | None  # the free-text field in `redaction-1` form, if any


class PhaseAReport(BaseModel):
    """Result of Phase A steps 1-4. Not an `ImportReport`: no status, no new/unchanged counts,
    and `deferred_checks` have not run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = CONTRACT_VERSION
    transformation_version: str = TRANSFORMATION_VERSION
    redaction_version: str = REDACTION_VERSION
    format: Format | None
    bundle_sha256: str | None
    checked_at: Annotated[datetime, PlainSerializer(cf.format_timestamp, return_type=str)]
    received: dict[str, int]
    phases_completed: tuple[str, ...]
    deferred_checks: tuple[str, ...] = DEFERRED_CHECKS
    error_count: int
    error_count_is_lower_bound: bool
    errors_truncated: bool
    errors: tuple[er.RowError, ...]


@dataclass(frozen=True)
class UploadValidation:
    report: PhaseAReport
    files: tuple[BundleFile, ...] = ()
    dataset: rc.DatasetDescriptor | None = None
    records: dict[rc.Entity, tuple[ValidatedRecord, ...]] = field(default_factory=dict)


# ---- step 3: field rules -------------------------------------------------------------------


def _code(error_type: str) -> str:
    if error_type in _CUSTOM_CODES:
        return error_type
    if error_type == "missing":
        return "required"
    if error_type == "extra_forbidden":
        return "unknown_column"
    if error_type == "enum":
        return "enum"
    if error_type.endswith("_type"):
        return "type"
    return "format"


def _preview(name: str | None, code: str, raw: dict) -> str | None:
    if name is None or name in _NO_PREVIEW or code not in _PREVIEW_CODES:
        return None
    value = raw.get(name)
    return safe_preview(value) if isinstance(value, str) else None


def _field_errors(
    model: type[BaseModel],
    values: dict,
    convert: dict[str, type],
    make,
    *,
    preview: bool,
) -> tuple[BaseModel | None, list[er.RowError]]:
    errs: list[er.RowError] = []
    failed: set[str] = set()
    data = {}
    for name, value in values.items():
        if value is None and name in model.model_fields:
            continue  # absent optional value; an unknown key with null is still reported
        kind = convert.get(name)
        if kind is not None and isinstance(value, str):
            try:
                value = cf.parse_csv_boolean(value) if kind is bool else cf.parse_csv_integer(value)
            except cf.CanonicalFormError as exc:
                errs.append(
                    make(exc.code, name, _preview(name, exc.code, values) if preview else None)
                )
                failed.add(name)
                continue
        data[name] = value
    try:
        instance = model.model_validate(data)
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_context=False, include_url=False):
            loc = item["loc"]
            name = loc[0] if loc and isinstance(loc[0], str) else None
            if name in failed:
                continue
            code = _code(item["type"])
            shown = er.safe_key(name) if name is not None else None
            errs.append(make(code, shown, _preview(name, code, values) if preview else None))
        return None, errs
    return (None if errs else instance), errs


def _check_dataset(parsed: ParsedBundle) -> tuple[rc.DatasetDescriptor | None, list[er.RowError]]:
    file = parsed.dataset_file

    def make(code: str, name: str | None, _: str | None) -> er.RowError:
        at = er.pointer("dataset", name) if name else "/dataset"
        return er.row_error(code, file, pointer=at, field=name)

    raw = parsed.dataset
    model, errs = _field_errors(rc.DatasetDescriptor, raw, {}, make, preview=False)
    for name, value in sorted(raw.items()):
        if (
            isinstance(value, str)
            and name in rc.DatasetDescriptor.model_fields
            and matches_any(value)
            and not any(e.field == name for e in errs)
        ):
            errs.append(make("looks_personal", name, None))
            model = None
    return model, errs


def _check_rows(
    parsed: ParsedBundle,
) -> tuple[dict[E, list[ValidatedRecord]], list[er.RowError], bool]:
    valid: dict[E, list[ValidatedRecord]] = {}
    errs: list[er.RowError] = []
    excluded = False
    for entity in E:
        model = rc.ENTITY_MODELS[entity]
        convert = csv_kinds(model) if parsed.format == "csv" else {}
        ids = id_fields(model)
        text_field = rc.FREE_TEXT_FIELDS.get(entity)
        valid[entity] = []
        for raw in parsed.rows.get(entity, []):

            def make(code, name, preview, raw=raw, entity=entity):
                at = er.pointer(entity.value, raw.row - 1, *([name] if name else []))
                return er.row_error(
                    code,
                    raw.file,
                    entity=entity.value,
                    row=raw.row,
                    line=raw.line,
                    pointer=at if parsed.format == "json" else None,
                    field=name,
                    value_preview=preview,
                )

            if not isinstance(raw.values, dict):
                errs.append(make("type", None, None))
                excluded = True
                continue
            record, row_errs = _field_errors(model, raw.values, convert, make, preview=True)
            if record is not None:
                for name in ids:
                    value = getattr(record, name)
                    if value is not None and matches_any(value):
                        row_errs.append(make("looks_personal", name, None))
            if row_errs:
                errs.extend(row_errs)
                excluded = True
                continue
            text = getattr(record, text_field) if text_field else None
            valid[entity].append(
                ValidatedRecord(record, raw.file, raw.row, None if text is None else redact(text))
            )
    return valid, errs, excluded


# ---- step 4: structure inside the bundle ---------------------------------------------------


class _Structure:
    def __init__(self, valid: dict[E, list[ValidatedRecord]], checked_at: datetime) -> None:
        self.errs: list[er.RowError] = []
        self.checked_at = checked_at
        self.by_id: dict[E, dict[str, Any]] = {}
        self.items_by_order: dict[str, list[Any]] = defaultdict(list)
        self.rows: dict[E, list[ValidatedRecord]] = {}
        for entity, records in valid.items():
            seen: dict[str, Any] = {}
            kept = []
            for v in records:
                key = v.record.source_record_id()
                if key in seen:
                    field = v.record.ID_FIELDS[0] if len(v.record.ID_FIELDS) == 1 else None
                    self.error(v, "duplicate_in_bundle", field)
                    continue
                seen[key] = v.record
                kept.append(v)
            self.by_id[entity] = seen
            self.rows[entity] = kept
        for item in self.by_id[E.ORDER_ITEMS].values():
            self.items_by_order[item.order_id].append(item)

    def error(self, v: ValidatedRecord, code: str, field: str | None) -> None:
        entity = v.record.ENTITY.value
        self.errs.append(
            er.row_error(
                code,
                v.file,
                entity=entity,
                row=v.row,
                pointer=er.pointer(entity, v.row - 1, *([field] if field else []))
                if v.file.endswith(".json")
                else None,
                field=field,
            )
        )

    def get(self, entity: E, record_id: str | None) -> Any:
        return None if record_id is None else self.by_id[entity].get(record_id)

    def order_of_item(self, item_id: str | None) -> Any:
        item = self.get(E.ORDER_ITEMS, item_id)
        return None if item is None else self.get(E.ORDERS, item.order_id)

    def not_before(self, v: ValidatedRecord, earlier: datetime | None, field: str) -> None:
        later = getattr(v.record, field)
        if earlier is not None and later is not None and later < earlier:
            self.error(v, "temporal_order", field)

    def product_of_item(self, item_id: str | None) -> str | None:
        item = self.get(E.ORDER_ITEMS, item_id)
        return None if item is None else item.product_id

    def product_of_return(self, return_id: str | None) -> str | None:
        ret = self.get(E.RETURNS, return_id)
        return None if ret is None else self.product_of_item(ret.order_item_id)

    def run(self) -> list[er.RowError]:
        for records in self.rows.values():
            for v in records:
                for name, value in v.record:
                    if (
                        isinstance(value, datetime)
                        and name not in _NOT_TIME_BOUNDED
                        and value > self.checked_at
                    ):
                        self.error(v, "temporal_order", name)
        self._shipments()
        self._returns()
        self._statements()
        self._evidence()
        return self.errs

    def _shipments(self) -> None:
        orders_seen: set[str] = set()
        for v in self.rows[E.SHIPMENTS]:
            s = v.record
            if s.order_id in orders_seen:
                self.error(v, "duplicate_shipment", "order_id")
            orders_seen.add(s.order_id)
            order = self.get(E.ORDERS, s.order_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "shipped_at")
                self.not_before(v, order.ordered_at, "delivered_at")
        for v in self.rows[E.DELIVERY_EVENTS]:
            shipment = self.get(E.SHIPMENTS, v.record.shipment_id)
            order = None if shipment is None else self.get(E.ORDERS, shipment.order_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "occurred_at")

    def _returns(self) -> None:
        returned: dict[str, int] = defaultdict(int)
        for v in self.rows[E.RETURNS]:
            r = v.record
            order = self.order_of_item(r.order_item_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "requested_at")
            if r.status is rc.ReturnStatus.CANCELLED:
                continue
            item = self.get(E.ORDER_ITEMS, r.order_item_id)
            # A stored item holds at most ORDER_ITEM_QUANTITY_MAX, so exceeding it is definite.
            limit = rc.ORDER_ITEM_QUANTITY_MAX if item is None else item.quantity
            returned[r.order_item_id] += r.quantity
            if returned[r.order_item_id] > limit:
                self.error(v, "quantity_exceeded", "quantity")
        for v in self.rows[E.INSPECTION_OBSERVATIONS]:
            ret = self.get(E.RETURNS, v.record.return_id)
            if ret is None:
                continue
            if ret.status not in (rc.ReturnStatus.RECEIVED, rc.ReturnStatus.CLOSED):
                self.error(v, "inconsistent_reference", "return_id")
            self.not_before(v, ret.requested_at, "observed_at")

    def _statements(self) -> None:
        for v in self.rows[E.RETURN_COMMENTS]:
            ret = self.get(E.RETURNS, v.record.return_id)
            order = None if ret is None else self.order_of_item(ret.order_item_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "submitted_at")
        for v in self.rows[E.CUSTOMER_REVIEWS]:
            r = v.record
            product = self.product_of_item(r.order_item_id)
            if product is not None and product != r.product_id:
                self.error(v, "inconsistent_reference", "product_id")
            order = self.order_of_item(r.order_item_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "submitted_at")
        for v in self.rows[E.SUPPORT_COMPLAINTS]:
            c = v.record
            item = self.get(E.ORDER_ITEMS, c.order_item_id)
            if item is not None:
                if c.order_id is not None and item.order_id != c.order_id:
                    self.error(v, "inconsistent_reference", "order_id")
                if c.product_id is not None and item.product_id != c.product_id:
                    self.error(v, "inconsistent_reference", "product_id")
            order_id = c.order_id or (None if item is None else item.order_id)
            order = self.get(E.ORDERS, order_id)
            if order is not None:
                self.not_before(v, order.ordered_at, "opened_at")

    def _evidence_product(self, kind: rc.EvidenceType, target: str) -> str | None:
        """The evidence's product when the bundle alone determines it; else None."""
        T = rc.EvidenceType
        if kind is T.RETURN:
            return self.product_of_return(target)
        if kind is T.RETURN_COMMENT:
            comment = self.get(E.RETURN_COMMENTS, target)
            return None if comment is None else self.product_of_return(comment.return_id)
        if kind is T.INSPECTION_OBSERVATION:
            obs = self.get(E.INSPECTION_OBSERVATIONS, target)
            return None if obs is None else self.product_of_return(obs.return_id)
        if kind is T.CUSTOMER_REVIEW:
            review = self.get(E.CUSTOMER_REVIEWS, target)
            return None if review is None else review.product_id
        if kind is T.SUPPORT_COMPLAINT:
            complaint = self.get(E.SUPPORT_COMPLAINTS, target)
            if complaint is None:
                return None
            return complaint.product_id or self.product_of_item(complaint.order_item_id)
        # Shipments and delivery events relate through an order that contains the product;
        # stored items of that order may contain it, so a missing match is never definite.
        return None

    def _evidence(self) -> None:
        for v in self.rows[E.CORRECTION_CASE_EVIDENCE]:
            ev = v.record
            case = self.get(E.CORRECTION_CASES, ev.case_id)
            if case is None:
                continue
            product = self._evidence_product(ev.evidence_type, ev.evidence_id)
            if product is not None and product != case.product_id:
                self.error(v, "inconsistent_reference", "evidence_id")


# ---- entry point ---------------------------------------------------------------------------


def validate_upload(parts: Sequence[Part], *, checked_at: datetime) -> UploadValidation:
    """Run Phase A steps 1-4 in memory. Raises only `TypeError` for wrong argument types."""
    if not isinstance(checked_at, datetime) or checked_at.utcoffset() is None:
        raise TypeError("checked_at must be an aware datetime")
    collector = er.ErrorCollector()
    parsed = parse_upload(parts, collector)
    phases = parsed.phases_completed
    dataset = None
    valid: dict[E, list[ValidatedRecord]] = {}
    excluded = parsed.excluded_input
    if not parsed.stopped and not collector.full:
        errs: list[er.RowError] = []
        if isinstance(parsed.dataset, dict):
            dataset, dataset_errs = _check_dataset(parsed)
            errs.extend(dataset_errs)
        valid, row_errs, dropped = _check_rows(parsed)
        errs.extend(row_errs)
        excluded = excluded or dropped
        collector.add_phase(errs)
        phases.append(er.PHASE_FIELDS)
        if not collector.full:
            structure = _Structure(valid, checked_at)
            collector.add_phase(structure.run())
            phases.append(er.PHASE_BUNDLE_STRUCTURE)
            valid = structure.rows
    skipped = tuple(phases) != er.PHASES or excluded
    report = PhaseAReport(
        format=parsed.format,
        bundle_sha256=parsed.bundle_sha256,
        checked_at=checked_at,
        received=dict(parsed.received),
        phases_completed=tuple(phases),
        error_count=collector.count,
        error_count_is_lower_bound=collector.stopped or (collector.count > 0 and skipped),
        errors_truncated=collector.count > er.RETURNED_LIMIT,
        errors=collector.returned(),
    )
    if collector.count:
        return UploadValidation(report=report, files=parsed.files)
    return UploadValidation(
        report=report,
        files=parsed.files,
        dataset=dataset,
        records={entity: tuple(records) for entity, records in valid.items()},
    )
