"""The `return-data-v1` import service: validate or commit one upload (contracts §4.3–4.5, §4.8).

Both modes run the same checks, in this order:

* **Phase A** (no database transaction, no lock):
  - steps 1-4: `validate_upload`, in memory;
  - step 5, catalog references: each `product_id` must exist (`unknown_product`), and each
    listing snapshot triple must equal a stored `raw_catalog_records` row for that product
    (`snapshot_mismatch`). It runs in its own short read.
* **Phase B** (the stored-data checks):
  - the dataset descriptor (`dataset_mismatch`) and whether the `bundle_sha256` was already
    imported (`already_imported`);
  - stored natural keys: `unchanged` or `conflict_existing`;
  - the catalog references again;
  - the §3.1 rules over the bundle together with the stored records (`check_stored`):
    references, cross-table time order, one shipment per order, the non-cancelled quantity
    and the evidence-to-product relation.

**Validate mode** runs Phase B in a READ ONLY, REPEATABLE READ transaction without the lock.
Its result is advisory "as of `checked_at`", and it never writes.

**Commit mode** works in one READ COMMITTED transaction:
1. It first checks that the connected role is the least-privilege importer (`ImportRoleError`
   otherwise).
2. It takes the dataset-version advisory lock and re-runs every Phase B check under it.
3. Only for status `imported`, it inserts the dataset row (first import only), the batch, the
   metadata and contents of every file of the request, and the new rows only.

Any failure in commit mode writes nothing. FK, UNIQUE, CHECK and the deferred quantity trigger
remain the final guard, and a database refusal is raised as `ImportStorageError`, without
library text.

Every phase stops the run when it reports errors. `phases_completed` names the phases that ran
to the end, and `error_count_is_lower_bound` is true when a later phase was skipped after
errors. Nothing is logged, and no input value, file name, credential or library message
reaches a report or an exception.
"""

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError

from ecommerce_search.models import returns as rm
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.labels import is_valid_importer_label
from ecommerce_search.returns.validation import (
    UploadValidation,
    ValidatedRecord,
    check_stored,
    record_error,
    validate_upload,
)
from ecommerce_search.returns.versions import (
    CONTRACT_VERSION,
    REDACTION_VERSION,
    TRANSFORMATION_VERSION,
)

# The declared service surface (AC-13 (5)): two entry points, of which only `commit_import`
# writes, plus the report, its errors, the lock key and the role check. Nothing here refunds,
# orders, bans, sanctions or infers fraud or responsibility; a test pins this set exactly.
__all__ = (
    "EntityCounts",
    "ImportReport",
    "ImportRoleError",
    "ImportStorageError",
    "commit_import",
    "import_lock_key",
    "require_importer_role",
    "validate_import",
)

E = rc.Entity
Mode = Literal["validate", "commit"]
Status = Literal["valid", "invalid", "imported", "already_imported", "no_new_records"]
Checkpoint = Callable[[str, Connection | None], None]

PHASE_CATALOG = "catalog_references"  # Phase A step 5
PHASE_STORED = "stored_data"  # Phase B
# Entities whose `product_id` must exist in the catalog; the first two carry a snapshot triple.
_PRODUCT_ENTITIES = (E.ORDER_ITEMS, E.CORRECTION_CASES, E.CUSTOMER_REVIEWS, E.SUPPORT_COMPLAINTS)
_SNAPSHOT_ENTITIES = (E.ORDER_ITEMS, E.CORRECTION_CASES)
# Stored columns the Phase B rules read, besides `source_record_id` and `record_sha256`.
_STORED_COLUMNS: dict[E, tuple[str, ...]] = {
    E.ORDERS: ("order_id", "ordered_at"),
    E.ORDER_ITEMS: ("order_item_id", "order_id", "product_id", "quantity"),
    E.SHIPMENTS: ("shipment_id", "order_id"),
    E.DELIVERY_EVENTS: ("event_id", "shipment_id"),
    E.RETURNS: ("return_id", "order_item_id", "requested_at", "quantity", "status"),
    E.RETURN_COMMENTS: ("comment_id", "return_id"),
    E.INSPECTION_OBSERVATIONS: ("observation_id", "return_id"),
    E.CUSTOMER_REVIEWS: ("review_id", "product_id", "order_item_id"),
    E.SUPPORT_COMPLAINTS: ("complaint_id", "order_id", "order_item_id", "product_id"),
    E.CORRECTION_CASES: ("case_id", "product_id"),
    E.CORRECTION_CASE_EVIDENCE: ("case_id", "evidence_type", "evidence_id"),
}


class ImportRoleError(RuntimeError):
    """The connected role is not the least-privilege importer; nothing was written."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"the database role does not meet the importer requirements ({reason})")
        self.reason = reason


class ImportStorageError(RuntimeError):
    """The database refused the import or failed; the transaction was rolled back."""

    def __init__(self) -> None:
        super().__init__("the import could not be completed by the database; nothing was written")


class EntityCounts(BaseModel):
    """`new` and `unchanged` are null when the natural-key comparison did not run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    received: int
    new: int | None = None
    unchanged: int | None = None


class ImportReport(BaseModel):
    """The §4.8 report, the same for every interface (CLI, API)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = CONTRACT_VERSION
    mode: Mode
    status: Status
    bundle_sha256: str | None
    batch_id: uuid.UUID | None = None
    checked_at: Annotated[datetime, PlainSerializer(cf.format_timestamp, return_type=str)]
    counts: dict[str, EntityCounts]
    phases_completed: tuple[str, ...]
    error_count: int
    error_count_is_lower_bound: bool
    errors_truncated: bool
    errors: tuple[er.RowError, ...]


def import_lock_key(dataset_id: str, dataset_version: int) -> int:
    """Signed 64-bit key of the dataset-version advisory lock: SHA-256 of
    `returns-import:<dataset_id>:<dataset_version>` (never Python's process-randomized hash)."""
    digest = hashlib.sha256(f"returns-import:{dataset_id}:{dataset_version}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


# ---- the run -------------------------------------------------------------------------------


class _Run:
    """One validate or commit run: the error collector, the phases and the report."""

    def __init__(self, mode: Mode, upload: UploadValidation, checked_at: datetime) -> None:
        self.mode = mode
        self.upload = upload
        self.checked_at = checked_at
        self.phases = list(upload.report.phases_completed)
        self.collector = er.ErrorCollector()
        self.skipped = False
        received = upload.report.received
        self.counts = {e.value: EntityCounts(received=received.get(e.value, 0)) for e in E}

    def add(
        self, phase: str, errors: list[er.RowError], *, completed: bool = True, last: bool = False
    ) -> bool:
        """Collect one phase's errors; the phase is listed when all of its checks ran. True when
        the run continues with the next phase (a phase with errors stops the run)."""
        self.collector.add_phase(errors)
        if completed:
            self.phases.append(phase)
        if errors and (not completed or not last):
            self.skipped = True
        return not errors

    def report(self, status: Status, batch_id: uuid.UUID | None = None) -> ImportReport:
        count = self.collector.count
        return ImportReport(
            mode=self.mode,
            status="invalid" if count else status,
            bundle_sha256=self.upload.report.bundle_sha256,
            batch_id=batch_id,
            checked_at=self.checked_at,
            counts=self.counts,
            phases_completed=tuple(self.phases),
            error_count=count,
            error_count_is_lower_bound=self.collector.stopped or (count > 0 and self.skipped),
            errors_truncated=count > er.RETURNED_LIMIT,
            errors=self.collector.returned(),
        )

    def phase_a_report(self) -> ImportReport:
        """The report of a run that stopped in Phase A steps 1-4 (`validate_upload` errors)."""
        a = self.upload.report
        return ImportReport(
            mode=self.mode,
            status="invalid",
            bundle_sha256=a.bundle_sha256,
            checked_at=self.checked_at,
            counts=self.counts,
            phases_completed=a.phases_completed,
            error_count=a.error_count,
            error_count_is_lower_bound=a.error_count_is_lower_bound,
            errors_truncated=a.errors_truncated,
            errors=a.errors,
        )


def _catalog_errors(conn: Connection, upload: UploadValidation) -> list[er.RowError]:
    """Step 5. Reads only the catalog columns granted to the importer (contracts §2)."""
    records = upload.records
    products = {
        v.record.product_id
        for entity in _PRODUCT_ENTITIES
        for v in records.get(entity, ())
        if v.record.product_id is not None
    }
    if not products:
        return []
    ids = sorted(products)
    known = set(
        conn.execute(
            text("SELECT product_id FROM products WHERE product_id = ANY(:ids)"), {"ids": ids}
        ).scalars()
    )
    snapshots = {
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT r.source_key, d.dataset_id, d.dataset_version, r.raw_sha256 "
                "FROM raw_catalog_records r JOIN catalog_datasets d ON d.id = r.dataset_pk "
                "WHERE r.source_key = ANY(:ids)"
            ),
            {"ids": ids},
        )
    }
    errors = []
    for entity in _PRODUCT_ENTITIES:
        for v in records.get(entity, ()):
            r = v.record
            if r.product_id is None:
                continue
            if r.product_id not in known:
                errors.append(record_error(v, "unknown_product", "product_id"))
            elif (
                entity in _SNAPSHOT_ENTITIES
                and (
                    r.product_id,
                    r.catalog_dataset_id,
                    r.catalog_dataset_version,
                    r.catalog_raw_sha256,
                )
                not in snapshots
            ):
                errors.append(record_error(v, "snapshot_mismatch", "catalog_raw_sha256"))
    return errors


def _stored_dataset(conn: Connection, descriptor: rc.DatasetDescriptor) -> Any:
    table = rm.ReturnDataset.__table__
    return conn.execute(
        select(table).where(
            table.c.dataset_id == descriptor.dataset_id,
            table.c.dataset_version == descriptor.dataset_version,
        )
    ).first()


def _dataset_errors(upload: UploadValidation, stored: Any) -> list[er.RowError]:
    """`dataset_mismatch`, one error per differing descriptor field (no values)."""
    descriptor = upload.dataset
    if stored.descriptor_sha256 == descriptor.descriptor_sha256():
        return []
    file = "manifest.json" if upload.report.format == "csv" else "bundle.json"
    current = descriptor.canonical()
    fields = []
    for name, value in current.items():
        kept = getattr(stored, name)
        kept = cf.format_date(kept) if name == "authored_on" else kept
        if kept != value:
            fields.append(name)
    return [
        er.row_error("dataset_mismatch", file, pointer=er.pointer("dataset", name), field=name)
        for name in fields
    ] or [er.row_error("dataset_mismatch", file, pointer="/dataset")]


def _load_stored(conn: Connection, dataset_pk: int | None) -> dict[E, dict[str, Any]]:
    """Every stored record of the dataset version, with the columns the rules read."""
    stored: dict[E, dict[str, Any]] = {e: {} for e in E}
    if dataset_pk is None:
        return stored
    for entity, columns in _STORED_COLUMNS.items():
        table = rm.ENTITY_ROWS[entity].__table__
        rows = conn.execute(
            select(
                table.c.source_record_id,
                table.c.record_sha256,
                *(table.c[c] for c in columns),
            ).where(table.c.dataset_pk == dataset_pk)
        )
        for row in rows:
            values = row._asdict()
            if entity is E.RETURNS:
                values["status"] = rc.ReturnStatus(values["status"])
            stored[entity][row.source_record_id] = SimpleNamespace(**values)
    return stored


def _compare_keys(
    run: _Run, stored: Mapping[E, Mapping[str, Any]]
) -> tuple[dict[E, list[ValidatedRecord]], list[er.RowError]]:
    """Natural keys against stored rows: new, unchanged or `conflict_existing`."""
    new: dict[E, list[ValidatedRecord]] = {}
    errors = []
    for entity in E:
        new[entity] = []
        unchanged = 0
        for v in run.upload.records.get(entity, ()):
            kept = stored[entity].get(v.record.source_record_id())
            if kept is None:
                new[entity].append(v)
            elif kept.record_sha256 == v.record.record_sha256():
                unchanged += 1
            else:
                field = v.record.ID_FIELDS[0] if len(v.record.ID_FIELDS) == 1 else None
                errors.append(record_error(v, "conflict_existing", field))
        run.counts[entity.value] = EntityCounts(
            received=run.counts[entity.value].received, new=len(new[entity]), unchanged=unchanged
        )
    return new, errors


class _PhaseB:
    """The outcome of the Phase B checks inside one transaction."""

    def __init__(self) -> None:
        self.status: Status = "invalid"
        self.dataset_pk: int | None = None
        self.batch_id: uuid.UUID | None = None
        self.new: dict[E, list[ValidatedRecord]] = {}


def _phase_b(conn: Connection, run: _Run) -> _PhaseB:
    out = _PhaseB()
    upload = run.upload
    dataset = _stored_dataset(conn, upload.dataset)
    if dataset is not None:
        out.dataset_pk = dataset.id
        mismatch = _dataset_errors(upload, dataset)
        if mismatch:
            run.add(PHASE_STORED, mismatch, completed=False)
            return out
    stored = _load_stored(conn, out.dataset_pk)
    out.new, conflicts = _compare_keys(run, stored)
    if out.dataset_pk is not None:
        batches = rm.ReturnImportBatch.__table__
        existing = conn.execute(
            select(batches.c.batch_id).where(
                batches.c.dataset_pk == out.dataset_pk,
                batches.c.bundle_sha256 == upload.report.bundle_sha256,
            )
        ).scalar()
        if existing is not None:
            out.status, out.batch_id = "already_imported", existing
            return out
    errors = conflicts + _catalog_errors(conn, upload)
    errors += check_stored(upload.records, stored, checked_at=run.checked_at)
    if not run.add(PHASE_STORED, errors, last=True):
        return out
    out.status = "valid" if any(out.new.values()) else "no_new_records"
    return out


def _start(
    mode: Mode,
    parts: Sequence[Part],
    checked_at: datetime | None,
    engine: Engine,
    checkpoint: Checkpoint | None,
) -> tuple[_Run, ImportReport | None]:
    """Phase A: steps 1-4 in memory, then step 5 in its own short read."""
    checked_at = datetime.now(UTC) if checked_at is None else checked_at
    upload = validate_upload(parts, checked_at=checked_at)
    run = _Run(mode, upload, checked_at)
    _call(checkpoint, "phase_a", None)
    if upload.report.error_count:
        return run, run.phase_a_report()
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            catalog = _catalog_errors(conn, upload)
    except SQLAlchemyError:
        raise ImportStorageError() from None
    _call(checkpoint, "catalog", None)
    if not run.add(PHASE_CATALOG, catalog):
        return run, run.report("invalid")
    return run, None


def _call(checkpoint: Checkpoint | None, name: str, conn: Connection | None) -> None:
    if checkpoint is not None:
        checkpoint(name, conn)


# ---- validate mode -------------------------------------------------------------------------


def validate_import(
    engine: Engine,
    parts: Sequence[Part],
    *,
    checked_at: datetime | None = None,
    checkpoint: Checkpoint | None = None,
) -> ImportReport:
    """Validate one upload; never writes and never takes the import lock.

    Status `valid`, `invalid`, `already_imported` or `no_new_records`, advisory as of
    `checked_at`: a later commit re-checks everything."""
    run, done = _start("validate", parts, checked_at, engine, checkpoint)
    if done is not None:
        return done
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
            conn.begin(),
        ):
            conn.execute(text("SET TRANSACTION READ ONLY"))
            _call(checkpoint, "snapshot", conn)
            outcome = _phase_b(conn, run)
            conn.rollback()
    except SQLAlchemyError:
        raise ImportStorageError() from None
    return run.report(outcome.status, outcome.batch_id)


# ---- commit mode ---------------------------------------------------------------------------


def require_importer_role(conn: Connection) -> None:
    """Refuse any role but the least-privilege importer (contracts §2, `returns-grants-1`):
    no superuser, owner or member of the M18 table owner; INSERT on every M18 table and SELECT
    on all but the raw contents; no read of raw contents; no UPDATE, DELETE or TRUNCATE on any
    M18 or catalog table, and no catalog writes. Uses only the role's own catalog views."""
    me = conn.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    if me.rolsuper or me.rolbypassrls:
        raise ImportRoleError("superuser")
    tables = list(rm.RETURN_TABLES)
    owners = conn.execute(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) "
            "AND pg_has_role(current_user, c.relowner, 'MEMBER')"
        ),
        {"tables": tables},
    ).scalar_one()
    if owners:
        raise ImportRoleError("owner")

    def has(table: str, privilege: str) -> bool:
        return conn.execute(
            text("SELECT has_table_privilege(current_user, :t, :p)"),
            {"t": f"public.{table}", "p": privilege},
        ).scalar_one()

    raw = rm.ReturnImportFileContent.__tablename__
    if conn.execute(
        text("SELECT has_any_column_privilege(current_user, :t, 'SELECT')"),
        {"t": f"public.{raw}"},
    ).scalar_one():
        raise ImportRoleError("reads_raw_contents")
    for table in tables:
        if not has(table, "INSERT") or (table != raw and not has(table, "SELECT")):
            raise ImportRoleError("missing_privilege")
    for table in (*tables, "products", "raw_catalog_records", "catalog_datasets"):
        if any(has(table, p) for p in ("UPDATE", "DELETE", "TRUNCATE")):
            raise ImportRoleError("can_modify")
    for table in ("products", "raw_catalog_records", "catalog_datasets"):
        if has(table, "INSERT"):
            raise ImportRoleError("can_modify")


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _row_values(v: ValidatedRecord, dataset_pk: int, batch_pk: int, file_pk: int) -> dict:
    values = {name: _enum_value(value) for name, value in v.record}
    entity = v.record.ENTITY
    text_field = rc.FREE_TEXT_FIELDS.get(entity)
    if text_field is not None:  # only the redacted form is stored (§5.1)
        counts = {} if v.redaction is None else v.redaction.counts
        values[text_field] = None if v.redaction is None else v.redaction.text
        for kind in rc.REDACTION_KINDS:
            values[f"redacted_{kind}_count"] = counts.get(kind, 0)
    return values | {
        "dataset_pk": dataset_pk,
        "batch_pk": batch_pk,
        "source_file_pk": file_pk,
        "source_row": v.row,
        "source_record_id": v.record.source_record_id(),
        "record_sha256": v.record.record_sha256(),
    }


def _write(
    conn: Connection,
    run: _Run,
    outcome: _PhaseB,
    label: str,
    checkpoint: Checkpoint | None,
) -> uuid.UUID:
    upload = run.upload
    dataset_pk = outcome.dataset_pk
    if dataset_pk is None:
        descriptor = upload.dataset
        values = {name: _enum_value(value) for name, value in descriptor}
        dataset_pk = conn.execute(
            insert(rm.ReturnDataset.__table__)
            .values(**values, descriptor_sha256=descriptor.descriptor_sha256())
            .returning(rm.ReturnDataset.__table__.c.id)
        ).scalar_one()
    _call(checkpoint, "dataset", conn)
    batch_id = uuid.uuid4()
    counts = {
        f"{entity}_{kind}": getattr(c, kind)
        for entity, c in run.counts.items()
        for kind in ("received", "new", "unchanged")
    }
    batches = rm.ReturnImportBatch.__table__
    batch_pk = conn.execute(
        insert(batches)
        .values(
            batch_id=batch_id,
            dataset_pk=dataset_pk,
            contract_version=CONTRACT_VERSION,
            transformation_version=TRANSFORMATION_VERSION,
            redaction_version=REDACTION_VERSION,
            bundle_sha256=upload.report.bundle_sha256,
            format=upload.report.format,
            file_count=len(upload.files),
            total_bytes=sum(f.size_bytes for f in upload.files),
            imported_by_label=label,
            **counts,
        )
        .returning(batches.c.id)
    ).scalar_one()
    _call(checkpoint, "batch", conn)
    files = rm.ReturnImportFile.__table__
    file_pks: dict[str, int] = {}
    for f in upload.files:
        file_pks[f.name] = conn.execute(
            insert(files)
            .values(
                batch_pk=batch_pk,
                name=f.name,
                entity=None if f.entity is None else f.entity.value,
                media_type=f.media_type,
                size_bytes=f.size_bytes,
                raw_sha256=f.sha256,
                row_count=f.row_count,
            )
            .returning(files.c.id)
        ).scalar_one()
        # No RETURNING: the importer has INSERT only on the raw contents.
        conn.execute(
            insert(rm.ReturnImportFileContent.__table__).values(
                file_pk=file_pks[f.name], raw_bytes=f.data, raw_sha256=f.sha256
            )
        )
    _call(checkpoint, "files", conn)
    for entity in E:  # parents before children
        rows = [_row_values(v, dataset_pk, batch_pk, file_pks[v.file]) for v in outcome.new[entity]]
        if rows:
            conn.execute(insert(rm.ENTITY_ROWS[entity].__table__), rows)
        _call(checkpoint, f"rows:{entity.value}", conn)
    return batch_id


def commit_import(
    engine: Engine,
    parts: Sequence[Part],
    *,
    imported_by_label: str,
    checked_at: datetime | None = None,
    checkpoint: Checkpoint | None = None,
) -> ImportReport:
    """Validate and commit one upload atomically under the dataset-version lock.

    Status `imported`, `invalid`, `already_imported` or `no_new_records`; only `imported`
    writes. `engine` must connect as the least-privilege importer role. `checkpoint` is a test
    hook called at named points (`phase_a`, `catalog`, `locked`, `checked`, `dataset`, `batch`,
    `files`, `rows:<entity>`, `before_commit`); an exception raised there rolls back."""
    if not is_valid_importer_label(imported_by_label):
        raise ValueError("imported_by_label is not a valid importer label")
    run, done = _start("commit", parts, checked_at, engine, checkpoint)
    if done is not None:
        return done
    descriptor = run.upload.dataset
    key = import_lock_key(descriptor.dataset_id, descriptor.dataset_version)
    try:
        with (
            engine.connect().execution_options(isolation_level="READ COMMITTED") as conn,
            conn.begin(),
        ):
            require_importer_role(conn)
            conn.execute(select(func.pg_advisory_xact_lock(key)))
            _call(checkpoint, "locked", conn)
            outcome = _phase_b(conn, run)
            _call(checkpoint, "checked", conn)
            if outcome.status != "valid":
                conn.rollback()
                return run.report(outcome.status, outcome.batch_id)
            batch_id = _write(conn, run, outcome, imported_by_label, checkpoint)
            _call(checkpoint, "before_commit", conn)
    except SQLAlchemyError:
        raise ImportStorageError() from None
    return run.report("imported", batch_id)
