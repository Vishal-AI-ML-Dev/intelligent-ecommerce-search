"""M18-03B: the import service and CLI on a scratch database with real per-module roles.

Covers:
- AC-07: atomicity, with fault injection at every checkpoint and a database backstop;
- AC-08: §4.5 idempotency and incremental imports, and JSON/CSV equivalence;
- importer-role permissions and transaction modes;
- service-level concurrency T1–T8 (two real sessions; a test hook pauses a commit under the
  lock).

One throwaway `ecommerce_search_test_*` database per module, with two new LOGIN roles that
have random names and generated passwords (never printed) and the `returns-grants-1` grants.
Each test imports into its own dataset version, so tests never see each other's rows. The
database and roles are dropped afterwards. Every record is SYNTHETIC.
"""

import copy
import itertools
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field

import pytest
from alembic import command
from alembic.config import Config
from returns_support import add_catalog, raw_line_sha, scratch
from sqlalchemy import Engine, create_engine, pool, text

from ecommerce_search.config import get_settings
from ecommerce_search.models.returns import RETURN_TABLES
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import cli, service
from ecommerce_search.returns import grants as rg
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.service import (
    ImportRoleError,
    ImportStorageError,
    commit_import,
    validate_import,
)
from returns_samples import DATASET, RECORDS
from returns_uploads import CHECKED_AT, csv_parts, json_parts, manifest_bytes, validate

pytestmark = pytest.mark.integration

LABEL = "test-importer"
VERSIONS = itertools.count(1)
WAIT = 30  # seconds; generous bound so a broken lock never hangs the run


@dataclass
class Db:
    name: str
    owner: Engine  # the schema owner (the test superuser)
    importer: Engine
    app: Engine
    importer_role: str
    passwords: dict[str, str] = field(repr=False)  # generated; never printed


def _role_engine(settings, database: str, role: str, password: str) -> Engine:
    url = settings.database_url(database=database).set(username=role, password=password)
    return create_engine(url, poolclass=pool.NullPool)


@pytest.fixture(scope="module")
def db(settings):
    name = scratch(f"ecommerce_search_test_{uuid.uuid4().hex[:12]}")
    suffix = uuid.uuid4().hex[:10]
    app, importer = f"m18t_app_{suffix}", f"m18t_imp_{suffix}"
    passwords = {role: secrets.token_urlsafe(24) for role in (app, importer)}
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    engines: list[Engine] = []
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            for role, password in passwords.items():
                assert re.fullmatch(r"[A-Za-z0-9_-]+", password)  # safe to inline; never printed
                conn.execute(
                    text(
                        f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                        f"NOINHERIT PASSWORD '{password}'"
                    )
                )
        config = Config("alembic.ini")
        config.attributes["database"] = name
        command.upgrade(config, "head")
        owner = create_engine(settings.database_url(database=name), poolclass=pool.NullPool)
        engines.append(owner)
        with owner.begin() as conn:
            add_catalog(conn)
            rg.apply_grants(conn, app_role=app, importer_role=importer)
        engines += [
            _role_engine(settings, name, importer, passwords[importer]),
            _role_engine(settings, name, app, passwords[app]),
        ]
        yield Db(name, owner, engines[1], engines[2], importer, passwords)
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in (app, importer):
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


# ---- uploads -------------------------------------------------------------------------------


def rec(entity: str, **over) -> dict:
    record = copy.deepcopy(RECORDS[entity]) | over
    if "catalog_raw_sha256" in record and "catalog_raw_sha256" not in over:
        record["catalog_raw_sha256"] = raw_line_sha(record["product_id"])
    return record


def records(*entities: str, **lists: list[dict]) -> dict[str, list[dict]]:
    """The sample record of each named entity, plus `lists`; all eleven when nothing is named."""
    if not entities and not lists:
        entities = tuple(RECORDS)
    return {e: [rec(e)] for e in entities} | lists


def dataset(version: int, **over) -> dict:
    return DATASET | {"dataset_version": version} | over


def as_json(recs, version, **over):
    return json_parts(recs, dataset=dataset(version, **over))


def as_csv(recs, version, **over):
    return csv_parts(recs, dataset=dataset(version, **over))


def commit(db: Db, parts, **kwargs):
    return commit_import(
        db.importer, parts, imported_by_label=LABEL, checked_at=CHECKED_AT, **kwargs
    )


def check(db: Db, parts, engine: Engine | None = None, **kwargs):
    return validate_import(
        db.app if engine is None else engine, parts, checked_at=CHECKED_AT, **kwargs
    )


def table_counts(db: Db) -> dict[str, int]:
    with db.owner.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in RETURN_TABLES
        }


def dataset_pk(db: Db, version: int) -> int | None:
    with db.owner.connect() as conn:
        return conn.execute(
            text("SELECT id FROM return_datasets WHERE dataset_id = :d AND dataset_version = :v"),
            {"d": DATASET["dataset_id"], "v": version},
        ).scalar()


def stored_records_sha256(db: Db, version: int) -> str:
    pk = dataset_pk(db, version)
    rows = []
    with db.owner.connect() as conn:
        for entity in RECORDS:
            rows += [
                (entity, rid, sha)
                for rid, sha in conn.execute(
                    text(
                        f"SELECT source_record_id, record_sha256 FROM {entity} "  # noqa: S608
                        "WHERE dataset_pk = :pk"
                    ),
                    {"pk": pk},
                )
            ]
    return cf.records_sha256(rows)


def expected_records_sha256(parts) -> str:
    result = validate(parts)
    assert result.report.errors == ()
    return cf.records_sha256(
        (e.value, v.record.source_record_id(), v.record.record_sha256())
        for e, vs in result.records.items()
        for v in vs
    )


def codes(report) -> list[tuple[str | None, str, str | None]]:
    return [(e.entity, e.code, e.field) for e in report.errors]


# ---- AC-08: §4.5 idempotency and incremental imports --------------------------------------


def test_a_bundle_is_imported_once_and_the_same_bytes_are_already_imported(db):
    v = next(VERSIONS)
    parts = as_json(records(), v)
    before = table_counts(db)
    first = commit(db, parts)
    assert first.status == "imported" and first.mode == "commit" and first.errors == ()
    assert first.batch_id is not None
    assert first.phases_completed == (
        "envelope",
        "parsing",
        "fields",
        "bundle_structure",
        "catalog_references",
        "stored_data",
    )
    assert all(c.received == c.new == 1 and c.unchanged == 0 for c in first.counts.values())
    after = table_counts(db)
    assert {t: after[t] - before[t] for t in RETURN_TABLES} == dict.fromkeys(RETURN_TABLES, 1)

    for report in (commit(db, parts), check(db, parts)):
        assert report.status == "already_imported"
        assert report.batch_id == first.batch_id
        assert all(c.new == 0 and c.unchanged == 1 for c in report.counts.values())
    assert table_counts(db) == after


def test_the_same_records_in_the_other_format_are_no_new_records(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records(), v)).status == "imported"
    before = table_counts(db)
    for report in (check(db, as_csv(records(), v)), commit(db, as_csv(records(), v))):
        assert report.status == "no_new_records"
        assert report.batch_id is None and report.errors == ()
        assert all(c.new == 0 and c.unchanged == 1 for c in report.counts.values())
    assert table_counts(db) == before  # no batch, no files


def test_json_and_csv_forms_store_identical_rows_and_records_sha256(db):
    vj, vc = next(VERSIONS), next(VERSIONS)
    assert commit(db, as_json(records(), vj)).status == "imported"
    assert commit(db, as_csv(records(), vc)).status == "imported"
    skip = {"id", "dataset_pk", "batch_pk", "source_file_pk"}
    with db.owner.connect() as conn:
        for entity in RECORDS:
            rows = {}
            for v in (vj, vc):
                row = (
                    conn.execute(
                        text(f"SELECT * FROM {entity} WHERE dataset_pk = :pk"),  # noqa: S608
                        {"pk": dataset_pk(db, v)},
                    )
                    .mappings()
                    .one()
                )
                rows[v] = {k: val for k, val in row.items() if k not in skip}
            assert rows[vj] == rows[vc], entity
    expected = expected_records_sha256(as_json(records(), vj))
    assert stored_records_sha256(db, vj) == stored_records_sha256(db, vc) == expected


def test_a_partial_import_then_the_rest_reaches_the_full_records_sha256(db):
    v = next(VERSIONS)
    first = commit(db, as_json(records("orders", "order_items", "shipments"), v))
    assert first.status == "imported"
    rest = commit(db, as_csv(records(), v))
    assert rest.status == "imported" and rest.batch_id != first.batch_id
    for entity, c in rest.counts.items():
        stored_before = entity in ("orders", "order_items", "shipments")
        assert (c.new, c.unchanged) == ((0, 1) if stored_before else (1, 0)), entity
    assert stored_records_sha256(db, v) == expected_records_sha256(as_json(records(), v))


def test_an_incremental_import_may_reference_records_stored_by_earlier_batches(db):
    v = next(VERSIONS)
    item = rec("order_items", quantity=2)
    assert commit(db, as_json(records("orders", order_items=[item]), v)).status == "imported"
    later = records(
        returns=[rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)]
    )
    assert check(db, as_json(later, v)).status == "valid"
    report = commit(db, as_json(later, v))
    assert report.status == "imported"
    assert report.counts["returns"].new == 1 and report.counts["orders"].received == 0


def test_a_changed_record_with_a_stored_key_is_conflict_existing_and_writes_nothing(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records(), v)).status == "imported"
    changed = records(orders=[rec("orders", customer_ref="SYN-CUST-00002")])
    before = table_counts(db)
    for report in (check(db, as_json(changed, v)), commit(db, as_json(changed, v))):
        assert report.status == "invalid"
        assert codes(report) == [("orders", "conflict_existing", "order_id")]
        assert report.phases_completed[-1] == "stored_data"
        assert report.error_count_is_lower_bound is False
    assert table_counts(db) == before


def test_a_different_dataset_block_is_dataset_mismatch_and_writes_nothing(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records(), v)).status == "imported"
    before = table_counts(db)
    other = as_csv(records(), v, source_name="Another name", notes="changed")
    for report in (check(db, other), commit(db, other)):
        assert report.status == "invalid"
        assert [(e.file, e.code, e.field) for e in report.errors] == [
            ("manifest.json", "dataset_mismatch", "notes"),
            ("manifest.json", "dataset_mismatch", "source_name"),
        ]
        assert "stored_data" not in report.phases_completed
        assert report.error_count_is_lower_bound is True
    assert table_counts(db) == before


def test_stored_free_text_is_redacted_and_raw_bytes_are_kept_only_in_contents(db):
    v = next(VERSIONS)
    text_value = "Call 9000000001 or mail a@example.com"
    recs = records(*RECORDS, return_comments=[rec("return_comments", text=text_value)])
    parts = as_json(recs, v)
    report = commit(db, parts)
    assert report.status == "imported"
    with db.owner.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.text, c.redacted_email_count, c.redacted_number_count, b.imported_by_label,"
                " b.format, b.contract_version, b.redaction_version, b.transformation_version,"
                " f.name, f.raw_sha256, x.raw_bytes FROM return_comments c"
                " JOIN return_import_batches b ON b.id = c.batch_pk"
                " JOIN return_import_files f ON f.id = c.source_file_pk"
                " JOIN return_import_file_contents x ON x.file_pk = f.id"
                " WHERE c.dataset_pk = :pk"
            ),
            {"pk": dataset_pk(db, v)},
        ).one()
    assert row.text == "Call [NUMBER] or mail [EMAIL]"
    assert (row.redacted_email_count, row.redacted_number_count) == (1, 1)
    assert (row.imported_by_label, row.format, row.name) == (LABEL, "json", "bundle.json")
    assert (row.contract_version, row.redaction_version, row.transformation_version) == (
        "return-data-v1",
        "redaction-1",
        "returns-import-1",
    )
    assert bytes(row.raw_bytes) == parts[0].data
    assert text_value not in report.model_dump_json()


# ---- deferred and catalog rules through the service -----------------------------------------


@pytest.mark.parametrize(
    ("bundle", "expected", "phase"),
    [
        (
            {"order_items": [rec("order_items", product_id="SYN-PHO-0099")]},
            [("order_items", "unknown_product", "product_id")],
            "catalog_references",
        ),
        (
            {"order_items": [rec("order_items", catalog_raw_sha256="d" * 64)]},
            [("order_items", "snapshot_mismatch", "catalog_raw_sha256")],
            "catalog_references",
        ),
        (
            {"correction_cases": [rec("correction_cases", catalog_dataset_version="2")]},
            [("correction_cases", "snapshot_mismatch", "catalog_raw_sha256")],
            "catalog_references",
        ),
        (
            {"returns": [rec("returns", order_item_id="SYN-OI-404")]},
            [("returns", "unknown_reference", "order_item_id")],
            "stored_data",
        ),
    ],
    ids=["unknown-product", "snapshot-hash", "snapshot-version", "unknown-reference"],
)
def test_catalog_and_reference_errors_write_nothing(db, bundle, expected, phase):
    v = next(VERSIONS)
    before = table_counts(db)
    for report in (check(db, as_json(bundle, v)), commit(db, as_json(bundle, v))):
        assert report.status == "invalid"
        assert codes(report) == expected
        assert report.phases_completed[-1] == phase
        # a catalog error skips Phase B, so the count is only a lower bound
        assert report.error_count_is_lower_bound is (phase == "catalog_references")
    assert table_counts(db) == before


@pytest.mark.parametrize(
    ("stored", "bundle", "expected"),
    [
        (
            records("orders", "shipments"),
            {"shipments": [rec("shipments", shipment_id="SYN-SHP-2")]},
            [("shipments", "duplicate_shipment", "order_id")],
        ),
        (
            records("orders", "order_items", "returns"),
            {
                "returns": [
                    rec("returns", return_id="SYN-RET-2", status="requested")
                    | {"received_at": None}
                ]
            },
            [("returns", "quantity_exceeded", "quantity")],
        ),
        (
            records(
                "orders",
                "order_items",
                returns=[rec("returns", status="requested", received_at=None)],
            ),
            {"inspection_observations": [rec("inspection_observations")]},
            [("inspection_observations", "inconsistent_reference", "return_id")],
        ),
        (
            records("orders"),
            {"shipments": [rec("shipments", shipped_at="2026-01-01T00:00:00+05:30")]},
            [("shipments", "temporal_order", "shipped_at")],
        ),
        (
            records("orders", "order_items"),
            {
                "support_complaints": [
                    rec("support_complaints", order_item_id=None, product_id="SYN-LAP-0001")
                ]
            },
            [("support_complaints", "inconsistent_reference", "product_id")],
        ),
        (
            records("orders", "order_items", "shipments"),
            {
                "correction_cases": [rec("correction_cases", product_id="SYN-LAP-0001")],
                "correction_case_evidence": [
                    rec(
                        "correction_case_evidence",
                        evidence_type="shipment",
                        evidence_id="SYN-SHP-00001",
                    )
                ],
            },
            [("correction_case_evidence", "inconsistent_reference", "evidence_id")],
        ),
    ],
    ids=[
        "stored-shipment",
        "stored-quantity",
        "inspection-not-received",
        "time-vs-stored-order",
        "complaint-order-product",
        "shipment-evidence",
    ],
)
def test_rules_deferred_by_phase_a_are_checked_against_stored_records(db, stored, bundle, expected):
    v = next(VERSIONS)
    assert commit(db, as_json(stored, v)).status == "imported"
    assert validate(as_json(bundle, v)).report.errors == ()  # Phase A alone accepts it
    before = table_counts(db)
    for report in (check(db, as_json(bundle, v)), commit(db, as_json(bundle, v))):
        assert report.status == "invalid"
        assert codes(report) == expected
    assert table_counts(db) == before


def test_shipment_evidence_for_a_stored_order_with_the_product_is_accepted(db):
    v = next(VERSIONS)
    assert (
        commit(db, as_json(records("orders", "order_items", "shipments"), v)).status == "imported"
    )
    bundle = records(
        "correction_cases",
        correction_case_evidence=[
            rec("correction_case_evidence", evidence_type="delivery_event", evidence_id="SYN-DEV-1")
        ],
        delivery_events=[rec("delivery_events", event_id="SYN-DEV-1")],
    )
    assert commit(db, as_json(bundle, v)).status == "imported"


# ---- AC-07: atomicity ----------------------------------------------------------------------


class Injected(Exception):
    pass


CHECKPOINTS = (
    "phase_a",
    "catalog",
    "locked",
    "checked",
    "dataset",
    "batch",
    "files",
    *(f"rows:{e}" for e in RECORDS),
    "before_commit",
)


@pytest.mark.parametrize("fmt", ["json", "csv"])
@pytest.mark.parametrize("at", CHECKPOINTS)
def test_a_failure_at_any_point_of_a_commit_writes_nothing(db, at, fmt):
    v = next(VERSIONS)
    parts = (as_json if fmt == "json" else as_csv)(records(), v)
    seen = []

    def fail(name, conn):
        seen.append(name)
        if name == at:
            raise Injected

    before = table_counts(db)
    with pytest.raises(Injected):
        commit(db, parts, checkpoint=fail)
    assert seen[-1] == at
    assert table_counts(db) == before
    assert commit(db, parts).status == "imported"  # the same upload then commits normally


@pytest.mark.parametrize(
    "damage", ["missing_file", "unexpected_file", "checksum_mismatch", "row_count_mismatch"]
)
def test_an_inconsistent_csv_set_writes_nothing(db, damage):
    v = next(VERSIONS)
    parts = as_csv(records(), v)
    if damage == "missing_file":
        parts = [p for p in parts if p.name != "returns.csv"]
    elif damage == "unexpected_file":
        parts = [*parts, Part("notes.txt", b"x")]
    elif damage == "checksum_mismatch":
        parts = [Part(p.name, p.data + b"\r\n") if p.name == "orders.csv" else p for p in parts]
    else:  # the manifest lists the right checksums but one row too few for orders.csv
        two = records(orders=[rec("orders"), rec("orders", order_id="SYN-ORD-2")])
        files = {p.name: p.data for p in as_csv(records() | two, v) if p.name != "manifest.json"}
        rows = dict.fromkeys(files, 1)
        parts = [Part("manifest.json", manifest_bytes(files, rows, dataset=dataset(v)))]
        parts += [Part(name, data) for name, data in files.items()]
    before = table_counts(db)
    report = commit(db, parts)
    assert report.status == "invalid"
    assert damage in [e.code for e in report.errors]
    assert table_counts(db) == before


def test_a_check_missed_by_the_service_is_stopped_by_the_database(db, monkeypatch):
    v = next(VERSIONS)
    assert commit(db, as_json(records("orders", "order_items", "returns"), v)).status == "imported"
    monkeypatch.setattr(service, "check_stored", lambda *args, **kwargs: [])
    second = records(
        returns=[rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)]
    )
    before = table_counts(db)
    with pytest.raises(ImportStorageError) as caught:
        commit(db, as_json(second, v))  # the deferred quantity trigger refuses it at commit
    assert "quantity" not in str(caught.value) and caught.value.__cause__ is None
    assert table_counts(db) == before


# ---- permissions and transaction modes -----------------------------------------------------


def test_commit_refuses_any_role_but_the_least_privilege_importer(db):
    v = next(VERSIONS)
    before = table_counts(db)
    for engine, reason in ((db.owner, "superuser"), (db.app, "missing_privilege")):
        with pytest.raises(ImportRoleError) as caught:
            commit_import(engine, as_json(records(), v), imported_by_label=LABEL)
        assert caught.value.reason == reason
    assert table_counts(db) == before


def test_validate_works_as_the_api_role_and_as_the_importer(db):
    v = next(VERSIONS)
    for engine in (db.app, db.importer):
        assert check(db, as_json(records(), v), engine).status == "valid"


def test_validate_reads_one_read_only_snapshot_without_the_lock(db):
    seen = {}

    def probe(name, conn):
        if name == "snapshot":
            seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar()
            seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar()
            seen["locks"] = conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
                )
            ).scalar()

    assert check(db, as_json(records(), next(VERSIONS)), checkpoint=probe).status == "valid"
    assert seen == {"isolation": "repeatable read", "read_only": "on", "locks": 0}


def test_commit_runs_read_committed_under_the_dataset_version_lock(db):
    v = next(VERSIONS)
    seen = {}
    key = service.import_lock_key(DATASET["dataset_id"], v)

    def probe(name, conn):
        if name == "locked":
            seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar()
            seen["lock"] = conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                    "AND pid = pg_backend_pid() AND objsubid = 1 "
                    "AND ((classid::bigint << 32) | objid::bigint) = :k"
                ),
                {"k": key},
            ).scalar()
            seen["user"] = conn.execute(text("SELECT current_user")).scalar()

    assert commit(db, as_json(records(), v), checkpoint=probe).status == "imported"
    assert seen["isolation"] == "read committed"
    assert seen["user"] == db.importer_role
    assert seen["lock"] == 1


# ---- concurrency T1–T8 ---------------------------------------------------------------------


class Pause:
    """Pauses a commit at one checkpoint until released; optionally fails it there."""

    def __init__(self, at: str = "locked", fail: bool = False) -> None:
        self.at, self.fail = at, fail
        self.reached, self.release = threading.Event(), threading.Event()

    def __call__(self, name, conn):
        if name == self.at:
            self.reached.set()
            assert self.release.wait(WAIT)
            if self.fail:
                raise Injected


class Background(threading.Thread):
    def __init__(self, fn) -> None:
        super().__init__(daemon=True)
        self.fn, self.result, self.error = fn, None, None

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            self.error = exc

    def outcome(self):
        self.join(WAIT)
        assert not self.is_alive(), "timed out"
        if self.error is not None:
            raise self.error
        return self.result


def wait_for_lock_waiter(db: Db) -> None:
    """Until another session waits on an advisory lock (the second import queues)."""
    deadline = time.monotonic() + WAIT
    with db.owner.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
            ).scalar()
            if waiting:
                return
            time.sleep(0.05)
    raise AssertionError("the second import never waited on the lock")


def race(db: Db, first, second, *, fail_first: bool = False):
    """Commit `first` paused under the lock, start `second`, let it queue, then release."""
    pause = Pause(fail=fail_first)
    a = Background(lambda: commit(db, first, checkpoint=pause))
    a.start()
    assert pause.reached.wait(WAIT)
    b = Background(lambda: commit(db, second))
    b.start()
    wait_for_lock_waiter(db)
    pause.release.set()
    if fail_first:
        with pytest.raises(Injected):
            a.outcome()
        return None, b.outcome()
    return a.outcome(), b.outcome()


def batch_count(db: Db, version: int) -> int:
    with db.owner.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM return_import_batches b JOIN return_datasets d "
                "ON d.id = b.dataset_pk WHERE d.dataset_id = :d AND d.dataset_version = :v"
            ),
            {"d": DATASET["dataset_id"], "v": version},
        ).scalar_one()


def test_t1_the_same_bundle_committed_twice_concurrently(db):
    v = next(VERSIONS)
    a, b = race(db, as_json(records(), v), as_json(records(), v))
    assert (a.status, b.status) == ("imported", "already_imported")
    assert b.batch_id == a.batch_id
    assert batch_count(db, v) == 1


def test_t2_two_returns_of_a_quantity_one_item_concurrently(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records("orders", "order_items"), v)).status == "imported"

    def bundle(n):
        return as_json(
            records(
                returns=[
                    rec("returns", return_id=f"SYN-RET-{n}", status="requested", received_at=None)
                ]
            ),
            v,
        )

    a, b = race(db, bundle(1), bundle(2))
    assert a.status == "imported"
    assert (b.status, codes(b)) == ("invalid", [("returns", "quantity_exceeded", "quantity")])


def test_t3_the_same_natural_key_with_different_content_concurrently(db):
    v = next(VERSIONS)
    other = records(orders=[rec("orders", customer_ref="SYN-CUST-00002")])
    a, b = race(db, as_json(records("orders"), v), as_json(other, v))
    assert a.status == "imported"
    assert (b.status, codes(b)) == ("invalid", [("orders", "conflict_existing", "order_id")])


def test_t4_two_shipments_for_one_order_concurrently(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records("orders"), v)).status == "imported"
    second = records(shipments=[rec("shipments", shipment_id="SYN-SHP-2")])
    a, b = race(db, as_json(records("shipments"), v), as_json(second, v))
    assert a.status == "imported"
    assert (b.status, codes(b)) == ("invalid", [("shipments", "duplicate_shipment", "order_id")])


@pytest.mark.parametrize("first_commits", [True, False], ids=["commit", "rollback"])
def test_t5_a_bundle_referencing_an_order_only_in_a_paused_import(db, first_commits):
    v = next(VERSIONS)
    a, b = race(
        db,
        as_json(records("orders"), v),
        as_json(records("order_items"), v),
        fail_first=not first_commits,
    )
    if first_commits:
        assert (a.status, b.status) == ("imported", "imported")
    else:
        assert (b.status, codes(b)) == (
            "invalid",
            [("order_items", "unknown_reference", "order_id")],
        )
        assert batch_count(db, v) == 0


@pytest.mark.parametrize("same_descriptor", [True, False], ids=["same", "different"])
def test_t6_first_imports_of_a_new_dataset_version_concurrently(db, same_descriptor):
    v = next(VERSIONS)
    over = {} if same_descriptor else {"source_name": "Another name"}
    second = records(orders=[rec("orders", order_id="SYN-ORD-2")])
    a, b = race(db, as_json(records("orders"), v), as_json(second, v, **over))
    assert a.status == "imported"
    if same_descriptor:
        assert b.status == "imported"
    else:
        assert codes(b) == [(None, "dataset_mismatch", "source_name")]
    with db.owner.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT count(*) FROM return_datasets WHERE dataset_id = :d AND dataset_version = :v"
            ),
            {"d": DATASET["dataset_id"], "v": v},
        ).scalar_one()
    assert rows == 1


def test_t7_validate_during_a_paused_commit_sees_no_partial_rows_and_never_blocks(db):
    v = next(VERSIONS)
    pause = Pause(at="before_commit")  # every row is inserted, not yet committed
    a = Background(lambda: commit(db, as_json(records(), v), checkpoint=pause))
    a.start()
    assert pause.reached.wait(WAIT)
    try:
        started = time.monotonic()
        same = Background(lambda: check(db, as_json(records(), v)))
        child = Background(
            lambda: check(
                db,
                as_json(
                    records(
                        returns=[
                            rec(
                                "returns",
                                return_id="SYN-RET-2",
                                status="requested",
                                received_at=None,
                            )
                        ]
                    ),
                    v,
                ),
            )
        )
        same.start()
        child.start()
        assert same.outcome().status == "valid"  # not already_imported: nothing is visible
        assert codes(child.outcome()) == [("returns", "unknown_reference", "order_item_id")]
        assert time.monotonic() - started < WAIT
    finally:
        pause.release.set()
    assert a.outcome().status == "imported"
    assert check(db, as_json(records(), v)).status == "already_imported"


def test_t8_a_cancelled_and_a_new_return_on_one_item_concurrently(db):
    v = next(VERSIONS)
    assert commit(db, as_json(records("orders", "order_items"), v)).status == "imported"
    cancelled = records(
        returns=[rec("returns", return_id="SYN-RET-1", status="cancelled", received_at=None)]
    )
    requested = records(
        returns=[rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)]
    )
    a, b = race(db, as_json(cancelled, v), as_json(requested, v))
    assert (a.status, b.status) == ("imported", "imported")


# ---- CLI -----------------------------------------------------------------------------------


@pytest.fixture
def cli_env(db, monkeypatch):
    """The CLI's settings point at the scratch database; the role is chosen per test."""
    monkeypatch.setenv("POSTGRES_DB", db.name)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _as_role(env, db: Db, role: str | None) -> None:
    if role is not None:
        env.setenv("POSTGRES_USER", role)
        env.setenv("POSTGRES_PASSWORD", db.passwords[role])
    get_settings.cache_clear()


def test_cli_import_and_validate_as_the_importer_role(db, cli_env, tmp_path, capsys):
    v = next(VERSIONS)
    bundle = tmp_path / "returns_v1.json"
    bundle.write_bytes(as_json(records(), v)[0].data)
    folder = tmp_path / "csv"
    folder.mkdir()
    for part in as_csv(records(), v):
        (folder / part.name).write_bytes(part.data)
    cli_env.setenv("RETURNS_IMPORTER_LABEL", "cli-test")
    _as_role(cli_env, db, db.importer_role)
    assert cli.main(["validate", str(bundle)]) == 0
    assert '"status": "valid"' in capsys.readouterr().out
    assert cli.main(["import", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert '"status": "imported"' in out and '"mode": "commit"' in out
    assert cli.main(["import", str(bundle)]) == 0
    assert '"status": "already_imported"' in capsys.readouterr().out
    assert cli.main(["import", str(folder)]) == 0
    assert '"status": "no_new_records"' in capsys.readouterr().out
    with db.owner.connect() as conn:
        assert (
            conn.execute(
                text("SELECT imported_by_label FROM return_import_batches WHERE dataset_pk = :pk"),
                {"pk": dataset_pk(db, v)},
            ).scalar_one()
            == "cli-test"
        )


def test_cli_import_never_falls_back_to_other_credentials(db, cli_env, tmp_path, capsys):
    v = next(VERSIONS)
    bundle = tmp_path / "bundle.json"
    bundle.write_bytes(as_json(records(), v)[0].data)
    before = table_counts(db)
    # the configured admin credentials: refused as a superuser
    cli_env.setenv("RETURNS_IMPORTER_LABEL", "cli-test")
    _as_role(cli_env, db, None)
    assert cli.main(["import", str(bundle)]) == cli.EXIT_ERROR
    assert "(superuser)" in capsys.readouterr().err
    # the API role: refused before any write
    app_role = next(r for r in db.passwords if r != db.importer_role)
    _as_role(cli_env, db, app_role)
    assert cli.main(["import", str(bundle)]) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "(missing_privilege)" in err
    assert db.passwords[app_role] not in err and "postgresql" not in err
    # no label: refused before connecting
    cli_env.delenv("RETURNS_IMPORTER_LABEL")
    _as_role(cli_env, db, db.importer_role)
    assert cli.main(["import", str(bundle)]) == cli.EXIT_ERROR
    assert "RETURNS_IMPORTER_LABEL is required" in capsys.readouterr().err
    assert table_counts(db) == before


# ---- diagnostics never reveal credentials (regression for the 2026-10-26 disclosure) ---------


def _secrets(db: Db, settings) -> list[str]:
    return [*db.passwords.values(), settings.postgres_password.get_secret_value()]


def test_fixture_reprs_never_show_role_passwords_or_connection_strings(db, settings):
    shown = " ".join([repr(db), str(db), repr(db.owner), repr(db.importer), repr(db.app)])
    for secret in _secrets(db, settings):
        assert secret not in shown
    assert "passwords" not in repr(db)
    assert "***" in repr(db.importer)  # SQLAlchemy masks the URL password


def test_a_failed_connection_is_reported_without_credentials(
    db, settings, tmp_path, monkeypatch, capsys
):
    import traceback

    wrong = secrets.token_urlsafe(24)
    engine = _role_engine(settings, db.name, db.importer_role, wrong)
    try:
        with pytest.raises(ImportStorageError) as caught:
            validate_import(engine, as_json(records(), next(VERSIONS)), checked_at=CHECKED_AT)
    finally:
        engine.dispose()
    error = caught.value
    rendered = "".join(traceback.format_exception(error)) + repr(error) + str(error)
    for secret in [wrong, *_secrets(db, settings)]:
        assert secret not in rendered
    assert "postgresql" not in rendered and db.importer_role not in rendered
    assert error.__cause__ is None and error.__suppress_context__

    bundle = tmp_path / "bundle.json"
    bundle.write_bytes(as_json(records(), next(VERSIONS))[0].data)
    monkeypatch.setenv("POSTGRES_DB", db.name)
    monkeypatch.setenv("POSTGRES_USER", db.importer_role)
    monkeypatch.setenv("POSTGRES_PASSWORD", wrong)
    monkeypatch.setenv("RETURNS_IMPORTER_LABEL", "cli-test")
    get_settings.cache_clear()
    try:
        assert cli.main(["import", str(bundle)]) == cli.EXIT_ERROR
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "could not be completed by the database" in output
    for secret in [wrong, *_secrets(db, settings)]:
        assert secret not in output
    assert "postgresql" not in output and db.importer_role not in output
