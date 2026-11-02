"""M18-02 storage on throwaway databases (never the development database).

AC-01 (database enum literals), AC-02 (keys, FK/UNIQUE/CHECK backstops via direct SQL that
bypasses any service, migration round trip), AC-03 (append-only), AC-04 (provenance available
per record), AC-05 (exact column allowlists by introspection), and the database backstops of
concurrency cases T2, T4 and T8. All records are SYNTHETIC.
"""

import hashlib
import itertools
import re
import threading
import time
import uuid

import pytest
from alembic.config import Config
from returns_support import (
    Linked,
    add_batch,
    add_catalog,
    add_dataset,
    add_file,
    insert,
    scratch,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, InternalError, ProgrammingError

from conftest import Migrator
from returns_contract_sets import (
    CONTRACT_FIELDS,
    ENUM_VALUES,
    FORBIDDEN_FIELD_WORDS,
    FORBIDDEN_WORD_EXCEPTIONS,
    M18_TABLES,
    STORAGE_COLUMNS,
)

pytestmark = pytest.mark.integration

VERSIONS = itertools.count(10)  # every test's dataset version is new


@pytest.fixture(scope="module")
def db(settings):
    """A throwaway database at head with a minimal catalog and one complete dataset version.

    Module-scoped: tests either fail inside a rolled-back transaction or commit only rows in
    dataset versions of their own."""
    name = f"ecommerce_search_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = None
    try:
        scratch(name)
        Migrator(Config("alembic.ini")).upgrade(name)
        engine = create_engine(settings.database_url(database=name))
        with engine.begin() as conn:
            add_catalog(conn)
            Linked(conn, version=1).add_all()
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


ENTITY_TABLES = sorted(CONTRACT_FIELDS)


def fails(engine, fragment, action):
    """`action(conn)` fails (at the statement or at commit); nothing it wrote is kept."""
    with (
        pytest.raises((IntegrityError, ProgrammingError, InternalError)) as excinfo,
        engine.begin() as conn,
    ):
        action(conn)
    assert fragment in str(excinfo.value.orig), str(excinfo.value.orig)
    return excinfo.value


def linked(conn, *entities: str) -> Linked:
    """A new dataset version holding `entities` (default: one record of every entity)."""
    ctx = Linked(conn, version=next(VERSIONS))
    if not entities:
        ctx.add_all()
    for entity in entities:
        ctx.add(entity)
    return ctx


# ---- AC-05: exact column allowlists by introspection ---------------------------------------


def _columns(engine) -> dict[str, set[str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        ).all()
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    return found


def test_every_m18_table_has_exactly_the_contract_columns(db):
    columns = _columns(db)
    assert {t: columns[t] for t in M18_TABLES} == STORAGE_COLUMNS


def test_no_m18_column_is_a_decision_outcome_or_hypothesis(db):
    for table, names in _columns(db).items():
        if table not in M18_TABLES:
            continue
        for name in names - FORBIDDEN_WORD_EXCEPTIONS:
            assert not set(name.split("_")) & FORBIDDEN_FIELD_WORDS, (table, name)


def test_raw_bytes_live_only_in_the_restricted_contents_table(db):
    with db.connect() as conn:
        binary = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type = 'bytea' "
                "AND table_name = ANY(:t)"
            ),
            {"t": sorted(M18_TABLES)},
        ).all()
    assert binary == [("return_import_file_contents", "raw_bytes")]


def test_record_ids_compare_bytewise(db):
    with db.connect() as conn:
        collations = dict(
            conn.execute(
                text(
                    "SELECT table_name || '.' || column_name, collation_name "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND column_name IN ('order_id', 'return_id', 'source_record_id', 'dataset_id')"
                    " AND table_name = ANY(:t)"
                ),
                {"t": sorted(M18_TABLES)},
            ).all()
        )
    assert collations and set(collations.values()) == {"C"}


# ---- AC-01: database enum literals equal the contract --------------------------------------


@pytest.mark.parametrize(
    ("constraint", "key"),
    [
        ("ck_shipments_status_valid", "shipment_status"),
        ("ck_delivery_events_event_type_valid", "delivery_event_type"),
        ("ck_delivery_events_source_valid", "delivery_event_source"),
        ("ck_returns_status_valid", "return_status"),
        ("ck_returns_customer_reason_code_valid", "reason_code"),
        ("ck_inspection_observations_condition_valid", "inspection_condition"),
        ("ck_support_complaints_channel_valid", "complaint_channel"),
        ("ck_support_complaints_customer_topic_valid", "complaint_topic"),
        ("ck_correction_cases_focus_area_valid", "focus_area"),
        ("ck_correction_cases_status_valid", "correction_case_status"),
        ("ck_correction_case_evidence_evidence_type_valid", "evidence_type"),
        ("ck_orders_currency_valid", "currency"),
        ("ck_return_datasets_source_type_valid", "source_type"),
    ],
)
def test_database_enum_literals_equal_the_contract(db, constraint, key):
    with db.connect() as conn:
        definition = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": constraint},
        ).scalar_one()
    assert set(re.findall(r"'([^']+)'", definition)) == ENUM_VALUES[key]


# ---- AC-02: a complete linked dataset and provenance (AC-04) -------------------------------


def test_a_complete_linked_dataset_is_stored(db):
    with db.connect() as conn:
        for table in M18_TABLES:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() >= 1, table  # noqa: S608


@pytest.mark.parametrize("entity", ENTITY_TABLES)
def test_every_record_has_the_eight_provenance_fields(db, entity):
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.source_type, d.source_name, d.license_identifier, r.source_record_id, "  # noqa: S608
                "d.dataset_version, b.imported_at, b.transformation_version, r.is_synthetic, "
                "d.dataset_id "
                f"FROM {entity} r JOIN return_import_batches b ON b.id = r.batch_pk "
                "JOIN return_datasets d ON d.id = r.dataset_pk "
                "WHERE d.dataset_version = 1"
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source_type == "synthetic" and row.source_name == "M18 test fixture"
    assert row.license_identifier is None  # available on the record; the fixture has none
    assert row.source_record_id and row.dataset_version == 1 and row.imported_at is not None
    assert row.transformation_version == "returns-import-1" and row.is_synthetic is True
    assert row.dataset_id == "synthetic-returns-test"


def test_the_same_record_id_in_another_dataset_version_is_a_different_record(db):
    with db.begin() as conn:
        ctx = linked(conn)
        order_ids = conn.execute(
            text("SELECT dataset_pk FROM orders WHERE order_id = 'SYN-ORD-00001'")
        ).scalars()
        assert ctx.dataset_pk in set(order_ids)


def test_a_152_character_evidence_key_is_stored(db):
    with db.begin() as conn:
        ctx = linked(conn)
        ctx.add("correction_cases", case_id="C" * 64)
        ctx.add(
            "correction_case_evidence",
            case_id="C" * 64,
            evidence_type="inspection_observation",
            evidence_id="E" * 64,
        )
        stored = conn.execute(
            text(
                "SELECT char_length(source_record_id) FROM correction_case_evidence "
                "WHERE dataset_pk = :d AND case_id = :c"
            ),
            {"d": ctx.dataset_pk, "c": "C" * 64},
        ).scalar_one()
    assert stored == 152


def test_the_same_bundle_may_exist_in_another_dataset(db):
    with db.begin() as conn:
        for _ in range(2):
            add_batch(conn, add_dataset(conn, version=next(VERSIONS)), bundle_sha256="b" * 64)


# ---- AC-02: UNIQUE backstops ---------------------------------------------------------------


def _bundle_of(conn, batch_pk):
    return conn.execute(
        text("SELECT bundle_sha256 FROM return_import_batches WHERE id = :b"), {"b": batch_pk}
    ).scalar_one()


UNIQUE_CASES = {
    "uq_orders_natural_key": lambda c, x: x.add("orders"),
    "uq_returns_natural_key": lambda c, x: x.add("returns"),
    "uq_shipments_one_per_order": lambda c, x: x.add("shipments", shipment_id="SYN-SHP-00002"),
    "uq_correction_case_evidence_natural_key": lambda c, x: x.add("correction_case_evidence"),
    "uq_return_datasets_identity": lambda c, x: add_dataset(
        c,
        version=c.execute(
            text("SELECT dataset_version FROM return_datasets WHERE id = :d"), {"d": x.dataset_pk}
        ).scalar_one(),
    ),
    "uq_return_import_batches_bundle": lambda c, x: add_batch(
        c, x.dataset_pk, bundle_sha256=_bundle_of(c, x.batch_pk)
    ),
    "uq_return_import_files_batch_name": lambda c, x: add_file(c, x.batch_pk),
}


@pytest.mark.parametrize("constraint", sorted(UNIQUE_CASES))
def test_unique_backstops(db, constraint):
    fails(db, constraint, lambda conn: UNIQUE_CASES[constraint](conn, linked(conn)))


# ---- AC-02: foreign-key backstops ----------------------------------------------------------


def _other_version(conn) -> Linked:
    return Linked(conn, version=next(VERSIONS))


FK_CASES = {
    "unknown order": (
        "fk_order_items_order",
        lambda c, x: x.add("order_items", order_item_id="SYN-OI-X", order_id="SYN-ORD-MISSING"),
    ),
    "unknown product": (
        "fk_order_items_product",
        lambda c, x: x.add(
            "order_items",
            order_item_id="SYN-OI-X",
            product_id="SYN-NOPE-0001",
            catalog_raw_sha256="a" * 64,
        ),
    ),
    "unknown catalog version": (
        "fk_order_items_catalog_dataset",
        lambda c, x: x.add("order_items", order_item_id="SYN-OI-X", catalog_dataset_version="2"),
    ),
    "review product differs from its order item": (
        "fk_customer_reviews_order_item_product",
        lambda c, x: x.add("customer_reviews", review_id="SYN-REV-X", product_id="SYN-LAP-0001"),
    ),
    "complaint order differs from its order item": (
        "fk_support_complaints_item_order",
        lambda c, x: (
            x.add("orders", order_id="SYN-ORD-00002"),
            x.add("support_complaints", complaint_id="SYN-CMP-X", order_id="SYN-ORD-00002"),
        ),
    ),
    "complaint product differs from its order item": (
        "fk_support_complaints_item_product",
        lambda c, x: x.add(
            "support_complaints", complaint_id="SYN-CMP-X", product_id="SYN-LAP-0001"
        ),
    ),
    "complaint about an unknown product": (
        "fk_support_complaints_product",
        lambda c, x: x.add(
            "support_complaints",
            complaint_id="SYN-CMP-X",
            order_item_id=None,
            product_id="SYN-NOPE-0001",
        ),
    ),
    "case on an unknown product": (
        "fk_correction_cases_product",
        lambda c, x: x.add(
            "correction_cases",
            case_id="SYN-CASE-X",
            product_id="SYN-NOPE-0001",
            catalog_raw_sha256="a" * 64,
        ),
    ),
    "case snapshot from an unknown catalog": (
        "fk_correction_cases_catalog_dataset",
        lambda c, x: x.add("correction_cases", case_id="SYN-CASE-X", catalog_dataset_id="other"),
    ),
    # References never cross dataset versions: the parents exist only in `x`'s version.
    "item in another version": (
        "fk_order_items_order",
        lambda c, x: _other_version(c).add("order_items"),
    ),
    "shipment in another version": (
        "fk_shipments_order",
        lambda c, x: _other_version(c).add("shipments"),
    ),
    "event in another version": (
        "fk_delivery_events_shipment",
        lambda c, x: _other_version(c).add("delivery_events"),
    ),
    "return in another version": (
        "fk_returns_order_item",
        lambda c, x: _other_version(c).add("returns"),
    ),
    "comment in another version": (
        "fk_return_comments_return",
        lambda c, x: _other_version(c).add("return_comments"),
    ),
    "inspection in another version": (
        "fk_inspection_observations_return",
        lambda c, x: _other_version(c).add("inspection_observations"),
    ),
    "complaint order in another version": (
        "fk_support_complaints_order",
        lambda c, x: _other_version(c).add(
            "support_complaints", order_item_id=None, product_id=None
        ),
    ),
    "complaint item in another version": (
        "fk_support_complaints_order_item",
        lambda c, x: _other_version(c).add("support_complaints", order_id=None, product_id=None),
    ),
    "evidence in another version": (
        "fk_correction_case_evidence_case",
        lambda c, x: _other_version(c).add("correction_case_evidence"),
    ),
    # A row's batch belongs to its dataset, and its source file to its batch.
    "batch of another dataset": (
        "fk_orders_batch",
        lambda c, x: x.add(
            "orders", order_id="SYN-ORD-X", columns={"batch_pk": _other_version(c).batch_pk}
        ),
    ),
    "file of another batch": (
        "fk_orders_source_file",
        lambda c, x: x.add(
            "orders", order_id="SYN-ORD-X", columns={"source_file_pk": _other_version(c).file_pk}
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(FK_CASES))
def test_foreign_key_backstops(db, case):
    constraint, action = FK_CASES[case]
    fails(db, constraint, lambda conn: action(conn, linked(conn)))


def test_file_contents_cannot_disagree_with_their_metadata(db):
    def mismatched_metadata(conn):
        ctx = linked(conn)
        meta_sha = hashlib.sha256(b"x").hexdigest()
        file_pk = insert(
            conn,
            "return_import_files",
            {
                "batch_pk": ctx.batch_pk,
                "name": "orders.csv",
                "entity": "orders",
                "media_type": "text/csv",
                "size_bytes": 1,
                "raw_sha256": meta_sha,
                "row_count": 0,
            },
        )
        insert(
            conn,
            "return_import_file_contents",
            {"file_pk": file_pk, "raw_bytes": b"y", "raw_sha256": hashlib.sha256(b"y").hexdigest()},
            returning=None,
        )

    def bytes_differ_from_checksum(conn):
        ctx = linked(conn)
        meta_sha = hashlib.sha256(b"x").hexdigest()
        file_pk = insert(
            conn,
            "return_import_files",
            {
                "batch_pk": ctx.batch_pk,
                "name": "orders.csv",
                "entity": "orders",
                "media_type": "text/csv",
                "size_bytes": 1,
                "raw_sha256": meta_sha,
                "row_count": 0,
            },
        )
        insert(
            conn,
            "return_import_file_contents",
            {"file_pk": file_pk, "raw_bytes": b"y", "raw_sha256": meta_sha},
            returning=None,
        )

    fails(db, "fk_return_import_file_contents_file", mismatched_metadata)
    fails(db, "ck_return_import_file_contents_raw_sha256_matches_bytes", bytes_differ_from_checksum)


# ---- AC-02: CHECK backstops ----------------------------------------------------------------

ORDER2 = {"order_id": "SYN-ORD-00002"}
CHECK_CASES = {
    "ck_orders_is_synthetic_true": ("orders", ORDER2, {"is_synthetic": False}),
    "ck_orders_source_record_id_matches": ("orders", ORDER2, {"source_record_id": "other"}),
    "ck_orders_record_sha256_format": ("orders", ORDER2, {"record_sha256": "A" * 64}),
    "ck_orders_source_row_positive": ("orders", ORDER2, {"source_row": 0}),
    "ck_orders_order_id_format": ("orders", ORDER2, {"order_id": "a|b", "source_record_id": "a|b"}),
    "ck_orders_source_record_id_length": ("orders", ORDER2, {"source_record_id": "A" * 65}),
    "ck_orders_customer_ref_format": ("orders", ORDER2, {"customer_ref": "real-person"}),
    "ck_orders_currency_valid": ("orders", ORDER2, {"currency": "USD"}),
    "ck_order_items_quantity_range": (
        "order_items",
        {"order_item_id": "SYN-OI-X"},
        {"quantity": 11},
    ),
    "ck_order_items_unit_price_nonneg": (
        "order_items",
        {"order_item_id": "SYN-OI-X"},
        {"unit_price": -1},
    ),
    "ck_order_items_catalog_raw_sha256_format": (
        "order_items",
        {"order_item_id": "SYN-OI-X"},
        {"catalog_raw_sha256": "x"},
    ),
    "ck_shipments_status_valid": (
        "shipments",
        {"shipment_id": "SYN-SHP-X"},
        {"status": "lost", "delivered_at": None},
    ),
    "ck_shipments_delivered_at_iff_delivered": (
        "shipments",
        {"shipment_id": "SYN-SHP-X"},
        {"status": "in_transit"},
    ),
    "ck_shipments_shipped_not_after_delivered": (
        "shipments",
        {"shipment_id": "SYN-SHP-X"},
        {"shipped_at": "2026-02-01T00:00:00+00:00"},
    ),
    "ck_shipments_carrier_ref_format": (
        "shipments",
        {"shipment_id": "SYN-SHP-X"},
        {"carrier_ref": "carrier a"},
    ),
    "ck_delivery_events_event_type_valid": (
        "delivery_events",
        {"event_id": "SYN-DEV-X"},
        {"event_type": "lost"},
    ),
    "ck_returns_received_has_received_at": (
        "returns",
        {"return_id": "SYN-RET-X"},
        {"received_at": None},
    ),
    "ck_returns_cancelled_has_no_received_at": (
        "returns",
        {"return_id": "SYN-RET-X"},
        {"status": "cancelled"},
    ),
    "ck_returns_requested_not_after_received": (
        "returns",
        {"return_id": "SYN-RET-X"},
        {"requested_at": "2026-02-01T00:00:00+00:00"},
    ),
    "ck_returns_quantity_positive": ("returns", {"return_id": "SYN-RET-X"}, {"quantity": 0}),
    "ck_returns_customer_reason_code_valid": (
        "returns",
        {"return_id": "SYN-RET-X"},
        {"customer_reason_code": "FRAUD"},
    ),
    "ck_return_comments_text_visible": (
        "return_comments",
        {"comment_id": "SYN-RCM-X"},
        {"text": " \t\n"},
    ),
    "ck_return_comments_text_length": (
        "return_comments",
        {"comment_id": "SYN-RCM-X"},
        {"text": "x" * 4001},
    ),
    "ck_return_comments_redaction_counts_valid": (
        "return_comments",
        {"comment_id": "SYN-RCM-X"},
        {"redacted_pin_count": -1},
    ),
    "ck_inspection_observations_redaction_counts_valid": (
        "inspection_observations",
        {"observation_id": "SYN-INS-X"},
        {"redacted_email_count": 1},
    ),
    "ck_inspection_observations_inspector_ref_format": (
        "inspection_observations",
        {"observation_id": "SYN-INS-X"},
        {"inspector_ref": "x"},
    ),
    "ck_inspection_observations_condition_valid": (
        "inspection_observations",
        {"observation_id": "SYN-INS-X"},
        {"condition": "fraud"},
    ),
    "ck_customer_reviews_rating_range": (
        "customer_reviews",
        {"review_id": "SYN-REV-X"},
        {"rating": 0},
    ),
    "ck_support_complaints_has_subject": (
        "support_complaints",
        {"complaint_id": "SYN-CMP-X"},
        {"order_id": None, "order_item_id": None, "product_id": None},
    ),
    "ck_support_complaints_channel_valid": (
        "support_complaints",
        {"complaint_id": "SYN-CMP-X"},
        {"channel": "fax"},
    ),
    "ck_correction_cases_status_valid": (
        "correction_cases",
        {"case_id": "SYN-CASE-X"},
        {"status": "resolved"},
    ),
    "ck_correction_cases_focus_area_valid": (
        "correction_cases",
        {"case_id": "SYN-CASE-X"},
        {"focus_area": "seller_fault"},
    ),
    "ck_correction_case_evidence_source_record_id_matches": (
        "correction_case_evidence",
        {"evidence_id": "SYN-RET-X"},
        {"source_record_id": "SYN-CASE-00001:return:SYN-RET-X"},
    ),
    "ck_correction_case_evidence_evidence_type_valid": (
        "correction_case_evidence",
        {"evidence_id": "SYN-RET-X"},
        {"evidence_type": "decision", "source_record_id": "SYN-CASE-00001|decision|SYN-RET-X"},
    ),
}


@pytest.mark.parametrize("constraint", sorted(CHECK_CASES))
def test_check_backstops(db, constraint):
    entity, record_over, columns = CHECK_CASES[constraint]

    def act(conn):
        ctx = linked(conn)
        if entity == "shipments":
            ctx.add("orders", **ORDER2)
            record_over_ = record_over | ORDER2
        else:
            record_over_ = record_over
        # `columns` are written as given, bypassing the contract model.
        insert(conn, entity, ctx.values(entity, **record_over_) | columns)

    fails(db, constraint, act)


METADATA_CASES = {
    "ck_return_datasets_source_type_valid": lambda c: add_dataset(
        c, version=next(VERSIONS), source_type="public_dataset"
    ),
    "ck_return_datasets_is_synthetic_true": lambda c: add_dataset(
        c, version=next(VERSIONS), is_synthetic=False
    ),
    "ck_return_datasets_dataset_id_format": lambda c: add_dataset(
        c, version=next(VERSIONS), dataset_id="Has Space"
    ),
    "ck_return_import_batches_orders_counts_valid": lambda c: add_batch(
        c, add_dataset(c, version=next(VERSIONS)), orders_received=2
    ),
    "ck_return_import_batches_new_total_positive": lambda c: add_batch(
        c,
        add_dataset(c, version=next(VERSIONS)),
        **{f"{e}_{k}": 0 for e in ENTITY_TABLES for k in ("received", "new")},
    ),
    "ck_return_import_batches_file_count_valid": lambda c: add_batch(
        c, add_dataset(c, version=next(VERSIONS)), file_count=2
    ),
    "ck_return_import_batches_contract_version_valid": lambda c: add_batch(
        c, add_dataset(c, version=next(VERSIONS)), contract_version="return-data-v2"
    ),
    "ck_return_import_files_entity_valid": lambda c: add_file(
        c, add_batch(c, add_dataset(c, version=next(VERSIONS))), name="evil.csv"
    ),
    "ck_return_import_files_size_bytes_range": lambda c: add_file(
        c,
        add_batch(c, add_dataset(c, version=next(VERSIONS))),
        name="manifest.json",
        content=b"x" * (64 * 1024 + 1),
    ),
}


@pytest.mark.parametrize("constraint", sorted(METADATA_CASES))
def test_metadata_check_backstops(db, constraint):
    fails(db, constraint, METADATA_CASES[constraint])


def test_file_entity_and_media_type_must_match_the_name(db):
    def wrong(**over):
        def act(conn):
            batch = add_batch(conn, add_dataset(conn, version=next(VERSIONS)))
            values = {
                "batch_pk": batch,
                "name": "orders.csv",
                "entity": "orders",
                "media_type": "text/csv",
                "size_bytes": 1,
                "raw_sha256": "a" * 64,
                "row_count": 0,
            }
            insert(conn, "return_import_files", values | over)

        return act

    fails(db, "ck_return_import_files_entity_matches_name", wrong(entity="returns"))
    fails(db, "ck_return_import_files_entity_matches_name", wrong(name="bundle.json"))
    fails(db, "ck_return_import_files_media_type_matches_name", wrong(media_type="text/plain"))
    fails(db, "ck_return_import_files_row_count_valid", wrong(row_count=None))


# ---- AC-03: append-only --------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(M18_TABLES))
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE {t} SET id = id",
        "DELETE FROM {t}",
        "TRUNCATE {t} CASCADE",
    ],
)
def test_every_m18_table_is_append_only(db, table, statement):
    if table == "return_import_file_contents" and statement.startswith("UPDATE"):
        statement = "UPDATE {t} SET file_pk = file_pk"
    with db.connect() as conn:
        before = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
    assert before >= 1
    fails(
        db, "is append-only return data", lambda conn: conn.execute(text(statement.format(t=table)))
    )
    with db.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == before  # noqa: S608


# ---- quantity invariant (deferred constraint trigger) and T8 -------------------------------


def _quantity_error(error) -> None:
    assert error.orig.sqlstate == "23514"
    assert error.orig.diag.constraint_name == "trg_returns_quantity_within_item"


def test_two_returns_cannot_exceed_a_quantity_one_item(db):
    def act(conn):
        ctx = linked(conn, "orders", "order_items")
        ctx.add("returns", return_id="R1", status="requested", received_at=None)
        ctx.add("returns", return_id="R2", status="requested", received_at=None)

    _quantity_error(fails(db, "non-cancelled quantity exceeds", act))


def test_a_single_return_over_the_item_quantity_is_rejected(db):
    def act(conn):
        ctx = linked(conn, "orders", "order_items")
        ctx.add("returns", return_id="R1", status="requested", received_at=None, quantity=2)

    _quantity_error(fails(db, "non-cancelled quantity exceeds", act))


def test_cancelled_returns_do_not_count_t8(db):
    with db.begin() as conn:
        ctx = linked(conn, "orders", "order_items")
        ctx.add("returns", return_id="R1", status="cancelled", received_at=None)
        ctx.add("returns", return_id="R2", status="cancelled", received_at=None)
        ctx.add("returns", return_id="R3", status="requested", received_at=None)


def test_returns_within_a_larger_item_quantity_add_up(db):
    with db.begin() as conn:
        ctx = linked(conn, "orders")
        ctx.add("order_items", quantity=2)
        ctx.add("returns", return_id="R1", status="requested", received_at=None)
        ctx.add("returns", return_id="R2", status="closed", received_at=None)

    def third(conn):  # a later transaction sees the committed returns
        ctx.add("returns", conn=conn, return_id="R3", status="requested", received_at=None)

    _quantity_error(fails(db, "non-cancelled quantity exceeds", third))
    with db.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM returns WHERE dataset_pk = :d"), {"d": ctx.dataset_pk}
        ).scalar_one()
    assert count == 2


def test_the_quantity_check_refuses_repeatable_read(db):
    with db.begin() as conn:
        ctx = linked(conn, "orders", "order_items")
    engine = db.execution_options(isolation_level="REPEATABLE READ")

    def act(conn):
        ctx.add("returns", conn=conn, return_id="R1", status="requested", received_at=None)

    with pytest.raises(DBAPIError) as excinfo:  # noqa: SIM117
        with engine.begin() as conn:
            act(conn)
    assert excinfo.value.orig.sqlstate == "25000"


def _wait_until_blocked(db, pid: int) -> None:
    with db.connect() as watcher:
        for _ in range(200):
            waiting = watcher.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :p"), {"p": pid}
            ).scalar_one_or_none()
            if waiting == "Lock":
                return
            time.sleep(0.05)
    raise AssertionError("the second session never waited on a lock")


def _in_thread(fn) -> tuple[threading.Thread, dict]:
    outcome: dict = {}

    def run():
        try:
            fn()
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def test_concurrent_returns_on_one_item_cannot_both_commit_t2_backstop(db):
    with db.begin() as conn:
        ctx = linked(conn, "orders", "order_items")
    first, second = db.connect(), db.connect()
    try:
        tx_first = first.begin()
        # The first writer checks now and keeps the item's advisory lock until it commits.
        first.execute(text("SET CONSTRAINTS trg_returns_quantity_within_item IMMEDIATE"))
        ctx.add("returns", conn=first, return_id="R1", status="requested", received_at=None)
        tx_second = second.begin()
        ctx.add("returns", conn=second, return_id="R2", status="requested", received_at=None)
        pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread, outcome = _in_thread(tx_second.commit)
        _wait_until_blocked(db, pid)  # the second commit waits for the first writer
        tx_first.commit()
        thread.join(30)
        assert not thread.is_alive()
        error = outcome.get("error")
        assert isinstance(error, IntegrityError), outcome
        _quantity_error(error)
    finally:
        first.close()
        second.close()
    with db.connect() as conn:
        assert conn.execute(
            text("SELECT array_agg(return_id) FROM returns WHERE dataset_pk = :d"),
            {"d": ctx.dataset_pk},
        ).scalar_one() == ["R1"]


def test_concurrent_second_shipments_for_one_order_cannot_both_commit_t4_backstop(db):
    with db.begin() as conn:
        ctx = linked(conn, "orders")
    first, second = db.connect(), db.connect()
    try:
        tx_first = first.begin()
        ctx.add("shipments", conn=first)
        tx_second = second.begin()
        pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread, outcome = _in_thread(
            lambda: ctx.add("shipments", conn=second, shipment_id="SYN-SHP-00002")
        )
        _wait_until_blocked(db, pid)  # the UNIQUE index makes the second insert wait
        tx_first.commit()
        thread.join(30)
        assert not thread.is_alive()
        error = outcome.get("error")
        assert isinstance(error, IntegrityError), outcome
        assert "uq_shipments_one_per_order" in str(error.orig)
        tx_second.rollback()
    finally:
        first.close()
        second.close()


def test_concurrent_creation_of_one_dataset_version_keeps_one_row_t6_backstop(db):
    version = next(VERSIONS)
    first, second = db.connect(), db.connect()
    try:
        tx_first = first.begin()
        add_dataset(first, version=version)
        second.begin()
        pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread, outcome = _in_thread(lambda: add_dataset(second, version=version))
        _wait_until_blocked(db, pid)
        tx_first.commit()
        thread.join(30)
        assert not thread.is_alive()
        error = outcome.get("error")
        assert isinstance(error, IntegrityError), outcome
        assert "uq_return_datasets_identity" in str(error.orig)
    finally:
        first.close()
        second.close()
    with db.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM return_datasets WHERE dataset_version = :v"),
                {"v": version},
            ).scalar_one()
            == 1
        )


# ---- AC-02: migration upgrade / downgrade / re-upgrade -------------------------------------

SNAPSHOT_QUERIES = {
    "columns": (
        "SELECT table_name, column_name, data_type, udt_name, collation_name, is_nullable, "
        "column_default, is_identity, identity_generation, numeric_precision, numeric_scale "
        "FROM information_schema.columns WHERE table_schema = 'public'"
    ),
    "constraints": (
        "SELECT t.relname, c.conname, c.contype, pg_get_constraintdef(c.oid), c.condeferrable, "
        "c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public'"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
    ),
    "triggers": (
        "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal"
    ),
    "functions": (
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid), md5(p.prosrc) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
    ),
    "sequences": (
        "SELECT sequencename, data_type, start_value, increment_by FROM pg_sequences "
        "WHERE schemaname = 'public'"
    ),
    "relations": (
        "SELECT c.relname, c.relkind, c.relacl::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
    ),
    "extensions": "SELECT extname, extversion FROM pg_extension",
}


def schema_snapshot(engine) -> dict[str, list]:
    with engine.connect() as conn:
        return {
            key: sorted(tuple(map(str, row)) for row in conn.execute(text(sql)).all())
            for key, sql in SNAPSHOT_QUERIES.items()
        }


def test_upgrade_downgrade_reupgrade_restores_both_schemas_exactly(
    settings, scratch_database, migrator
):
    scratch(scratch_database)
    engine = create_engine(settings.database_url(database=scratch_database))
    try:
        migrator.upgrade(scratch_database, "0006")
        at_0006 = schema_snapshot(engine)
        with engine.begin() as conn:  # pre-M18 data must survive the round trip
            add_catalog(conn)
        migrator.upgrade(scratch_database, "0007")
        at_0007 = schema_snapshot(engine)
        assert at_0007 != at_0006
        with engine.begin() as conn:  # M18 data present: downgrade still drops cleanly
            Linked(conn).add_all()
        migrator.downgrade(scratch_database, "0006")
        assert schema_snapshot(engine) == at_0006
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM products")).scalar_one() == 2
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0006"
            )
        # M19: head is now 0008, so the M18 round trip re-upgrades to 0007 explicitly (0008 has
        # its own round-trip test in test_returns_classification_integration.py).
        migrator.upgrade(scratch_database, "0007")
        assert schema_snapshot(engine) == at_0007
        migrator.upgrade(scratch_database, "0007")  # 0007 again: no-op
        assert schema_snapshot(engine) == at_0007
    finally:
        engine.dispose()


def test_0007_adds_only_return_tables_and_touches_no_existing_table(
    settings, scratch_database, migrator
):
    scratch(scratch_database)
    engine = create_engine(settings.database_url(database=scratch_database))
    try:
        migrator.upgrade(scratch_database, "0006")
        before = schema_snapshot(engine)
        migrator.upgrade(scratch_database, "0007")
        after = schema_snapshot(engine)
        new_tables = {row[0] for row in after["relations"] if row[1] == "r"} - {
            row[0] for row in before["relations"] if row[1] == "r"
        }
        assert new_tables == M18_TABLES
        for key in ("columns", "constraints", "triggers", "indexes"):
            old = [row for row in after[key] if row[0] not in M18_TABLES]
            assert old == before[key], key
        assert {r[0] for r in after["functions"]} - {r[0] for r in before["functions"]} == {
            "return_data_append_only",
            "returns_check_item_quantity",
        }
        with engine.connect() as conn:  # the migration grants nothing
            assert conn.execute(text("SELECT count(*) FROM pg_default_acl")).scalar_one() == 0
            granted = conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relname = ANY(:t) AND c.relacl IS NOT NULL"
                ),
                {"t": sorted(M18_TABLES)},
            ).scalar_one()
            assert granted == 0
    finally:
        engine.dispose()
