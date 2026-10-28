"""M18-04C: the retailer return read routes over a real scratch database (contracts §7, §7.1).

Covers AC-12 (filters, the total order with ties at page sizes 1-5, cursors, qualified detail
identity, 404s), AC-04 (API provenance on stored rows), AC-13 (2) (each read runs READ ONLY,
REPEATABLE READ; a write inside one fails) and the API role's privileges being sufficient.

One throwaway `ecommerce_search_test_*` database per module, built exactly like the M18-03C
dataset tests: migrated to head, the repository catalog ingested, two new LOGIN roles with random
names and generated passwords under the `returns-grants-1` grants; a password never enters SQL
text, logs or failure output (`tests/returns_roles.py`). The synthetic
release is imported by the importer role under several dataset identities so that every
`requested_at` is tied across dataset IDs and versions, and one version also ties returns inside
the dataset. The app serves the routes as the API role. Database and roles are dropped afterwards.
Expected results are computed independently from the owner connection. Every record is SYNTHETIC.
"""

import copy
import json
import logging
import secrets
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from returns_support import scratch
from sqlalchemy import Engine, create_engine, pool, text
from sqlalchemy.exc import DBAPIError

import returns_roles
from ecommerce_search.api import returns_read
from ecommerce_search.api.app import create_app
from ecommerce_search.config import Settings
from ecommerce_search.ingestion.service import ingest_file
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import grants as rg
from ecommerce_search.returns.bundle import BUNDLE_NAME, Part
from ecommerce_search.returns.service import commit_import
from returns_release import ROOT, bundle
from returns_roles import RoleSetupError, create_login_role

pytestmark = pytest.mark.integration

LABEL = "test-importer"
VIEWER = secrets.token_urlsafe(32)
SEED = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
SEED_PROVENANCE = ROOT / "data" / "seed" / "catalog_seed_v1.provenance.json"
MAIN = "synthetic-returns"
ALT = "alt-returns"  # sorts before MAIN, so dataset_id is a real tie-breaker
# (dataset_id, dataset_version): version 10 sorts after 2 numerically but before it as text.
DATASETS = ((MAIN, 1), (MAIN, 2), (ALT, 1), (MAIN, 10))
TIED = (MAIN, 10)  # cancelled returns share one requested_at inside this dataset version
# Imported in two batches: the parents first, then the whole release, so a return and its
# comments come from different batches and files (per-record provenance, AC-04).
SPLIT = (MAIN, 20)
SPLIT_FIRST = ("orders", "order_items", "shipments", "delivery_events", "returns")


@dataclass
class Db:
    name: str
    owner: Engine
    app_role: str
    importer_role: str
    passwords: dict[str, str] = field(repr=False)  # generated; never printed
    batches: dict[tuple[str, int], str] = field(default_factory=dict)
    split_batches: dict[str, str] = field(default_factory=dict)


def _role_engine(settings, database: str, role: str, password: str) -> Engine:
    url = settings.database_url(database=database).set(username=role, password=password)
    return create_engine(url, poolclass=pool.NullPool)


def _tie_cancelled(body: dict) -> None:
    """Give every cancelled return the latest requested_at among them (still after its order,
    and a cancelled return has no received_at or inspection to bound it)."""
    cancelled = [r for r in body["returns"] if r["status"] == "cancelled"]
    latest = max(cf.parse_timestamp(r["requested_at"]) for r in cancelled)
    for r in cancelled:
        r["requested_at"] = cf.format_timestamp(latest)


def _release(dataset_id: str, version: int, entities: tuple[str, ...] | None = None) -> list[Part]:
    body = copy.deepcopy(bundle())
    body["dataset"] |= {"dataset_id": dataset_id, "dataset_version": version}
    if entities is not None:
        for key in [k for k, v in body.items() if isinstance(v, list) and k not in entities]:
            body.pop(key)
    if (dataset_id, version) == TIED:
        _tie_cancelled(body)
    return [Part(BUNDLE_NAME, json.dumps(body, ensure_ascii=False).encode("utf-8"))]


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
                # SQL text carries a SCRAM verifier only; failures carry no statement.
                create_login_role(conn, role, password)
        config = Config("alembic.ini")
        config.attributes["database"] = name
        command.upgrade(config, "head")
        owner = create_engine(settings.database_url(database=name), poolclass=pool.NullPool)
        engines.append(owner)
        outcome = ingest_file(owner, SEED, SEED_PROVENANCE)
        assert outcome.result is not None and outcome.result.inserted == 240
        with owner.begin() as conn:
            rg.apply_grants(conn, app_role=app, importer_role=importer)
        importer_engine = _role_engine(settings, name, importer, passwords[importer])
        engines.append(importer_engine)
        built = Db(name, owner, app, importer, passwords)
        for dataset_id, version in DATASETS:
            report = commit_import(
                importer_engine, _release(dataset_id, version), imported_by_label=LABEL
            )
            assert report.status == "imported", (dataset_id, version)
            built.batches[(dataset_id, version)] = str(report.batch_id)
        for step, entities in (("first", SPLIT_FIRST), ("rest", None)):
            report = commit_import(
                importer_engine, _release(*SPLIT, entities), imported_by_label=LABEL
            )
            assert report.status == "imported", step
            built.split_batches[step] = str(report.batch_id)
        yield built
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in (app, importer):
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


@pytest.fixture(scope="module")
def client(db, settings):
    """The real app, connected to the scratch database as the API role."""
    app_settings = Settings(
        _env_file=None,
        app_env="test",
        postgres_host=settings.postgres_host,
        postgres_port=settings.postgres_port,
        postgres_db=db.name,
        postgres_user=db.app_role,
        postgres_password=db.passwords[db.app_role],
        returns_viewer_token=VIEWER,
    )
    with TestClient(create_app(app_settings)) as test_client:
        test_client.headers["Authorization"] = f"Bearer {VIEWER}"
        yield test_client


# ---- independent expectations (owner connection) ---------------------------------------------


def expected_order(db: Db, where: str = "TRUE", params: dict | None = None) -> list[tuple]:
    """(dataset_id, dataset_version, return_id) in the §7 order, sorted in Python."""
    sql = f"""
        SELECT d.dataset_id, d.dataset_version, r.return_id, r.requested_at
          FROM returns r JOIN return_datasets d ON d.id = r.dataset_pk
          JOIN order_items oi ON oi.dataset_pk = r.dataset_pk AND oi.order_item_id = r.order_item_id
          JOIN products p ON p.product_id = oi.product_id
         WHERE {where}
    """  # noqa: S608 - fixed test SQL
    with db.owner.connect() as conn:
        rows = conn.execute(text(sql), params or {}).all()
    # Stable sorts: the ascending tie-breakers (bytewise, like COLLATE "C"), then requested_at desc.
    rows = sorted(
        rows, key=lambda r: (r.dataset_id.encode(), r.dataset_version, r.return_id.encode())
    )
    rows = sorted(rows, key=lambda r: r.requested_at, reverse=True)
    return [(r.dataset_id, r.dataset_version, r.return_id) for r in rows]


def keys(items: list[dict]) -> list[tuple]:
    return [
        (i["ref"]["dataset_id"], i["ref"]["dataset_version"], i["ref"]["return_id"]) for i in items
    ]


def walk(client, sizes, **params) -> list[tuple]:
    """Every page of `GET /returns` with these filters; page sizes cycle through `sizes`."""
    seen: list[tuple] = []
    cursor, n = None, 0
    while True:
        query = {**params, "limit": str(sizes[n % len(sizes)])}
        if cursor is not None:
            query["cursor"] = cursor
        response = client.get("/returns", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += keys(body["items"])
        cursor, n = body["next_cursor"], n + 1
        if cursor is None:
            return seen


# ---- order, ties and paging (AC-12) ----------------------------------------------------------


def test_the_release_is_tied_across_datasets_and_inside_one_version(db):
    with db.owner.connect() as conn:
        groups = conn.execute(
            text(
                "SELECT max(n) FROM (SELECT count(*) AS n FROM returns r "
                "JOIN return_datasets d ON d.id = r.dataset_pk "
                "WHERE d.dataset_id = :d AND d.dataset_version = :v GROUP BY requested_at) g"
            ),
            {"d": TIED[0], "v": TIED[1]},
        ).scalar_one()
        across = conn.execute(
            text("SELECT max(n) FROM (SELECT count(*) AS n FROM returns GROUP BY requested_at) g")
        ).scalar_one()
    assert groups >= 3 and across >= len(DATASETS) + groups - 1


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_paging_with_ties_loses_and_duplicates_nothing(db, client, size):
    params = {"customer_reason_code": "NO_LONGER_NEEDED"}
    expected = expected_order(db, "r.customer_reason_code = :c", {"c": "NO_LONGER_NEEDED"})
    assert len(expected) > 5 * len(DATASETS)
    assert walk(client, [size], **params) == expected


def test_page_size_changes_mid_traversal_keep_the_order(db, client):
    params = {"status": "cancelled"}
    expected = expected_order(db, "r.status = 'cancelled'")
    assert walk(client, [1, 5, 2, 4, 3], **params) == expected


def test_the_unfiltered_list_is_the_total_order(db, client):
    assert walk(client, [100]) == expected_order(db)


# ---- filters (§7, D6a, E3) -------------------------------------------------------------------


def one(db: Db, sql: str, **params):
    with db.owner.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


@pytest.mark.parametrize(
    ("params", "where", "bind"),
    [
        ({"dataset_id": ALT}, "d.dataset_id = :x", {"x": ALT}),
        ({"dataset_version": "10"}, "d.dataset_version = 10", {}),
        ({"status": "received"}, "r.status = 'received'", {}),
        (
            {"customer_reason_code": "ARRIVED_DAMAGED"},
            "r.customer_reason_code = 'ARRIVED_DAMAGED'",
            {},
        ),
        ({"category": "laptop"}, "p.category = 'laptop'", {}),
        (
            {"has_comment": "true"},
            "EXISTS (SELECT 1 FROM return_comments c WHERE c.dataset_pk = r.dataset_pk AND c.return_id = r.return_id)",
            {},
        ),
        (
            {"has_inspection": "false"},
            "NOT EXISTS (SELECT 1 FROM inspection_observations i WHERE i.dataset_pk = r.dataset_pk AND i.return_id = r.return_id)",
            {},
        ),
    ],
)
def test_each_filter_selects_exactly_the_matching_returns(db, client, params, where, bind):
    expected = expected_order(db, where, bind)
    assert expected
    assert walk(client, [100], **params) == expected


def test_product_and_batch_filters(db, client):
    product = one(
        db,
        "SELECT oi.product_id FROM returns r JOIN order_items oi "
        "ON oi.dataset_pk = r.dataset_pk AND oi.order_item_id = r.order_item_id LIMIT 1",
    )
    assert walk(client, [100], product_id=product) == expected_order(
        db, "oi.product_id = :p", {"p": product}
    )
    batch = db.batches[(MAIN, 2)]
    assert walk(client, [100], batch_id=batch) == expected_order(
        db, "d.dataset_id = :d AND d.dataset_version = 2", {"d": MAIN}
    )


def test_requested_from_is_inclusive_and_requested_to_exclusive(db, client):
    instant = one(db, "SELECT requested_at FROM returns ORDER BY requested_at LIMIT 1 OFFSET 40")
    at = cf.format_timestamp(instant)
    offset_form = instant.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
    from_items = walk(client, [100], requested_from=offset_form)
    to_items = walk(client, [100], requested_to=at)
    assert from_items == expected_order(db, "r.requested_at >= :t", {"t": instant})
    assert to_items == expected_order(db, "r.requested_at < :t", {"t": instant})
    assert set(from_items).isdisjoint(to_items)
    assert len(from_items) + len(to_items) == len(expected_order(db))


def test_a_cursor_is_refused_with_other_filters_but_kept_across_equivalent_timestamps(db, client):
    first = client.get(
        "/returns", params={"limit": "1", "requested_from": "2026-01-01T05:30:00+05:30"}
    )
    cursor = first.json()["next_cursor"]
    same = client.get(
        "/returns",
        params={"limit": "1", "requested_from": "2026-01-01T00:00:00Z", "cursor": cursor},
    )
    assert same.status_code == 200
    other = client.get("/returns", params={"limit": "1", "status": "closed", "cursor": cursor})
    assert other.status_code == 400 and other.json()["code"] == "cursor_mismatch"
    bad = client.get("/returns", params={"cursor": cursor[:-2]})
    assert bad.status_code == 400 and bad.json()["code"] == "malformed_cursor"


# ---- detail: identity, content and provenance (AC-12, AC-04) ---------------------------------


def detail(client, dataset_id: str, version: int, return_id: str):
    return client.get(f"/returns/records/{dataset_id}/{version}/{return_id}")


def test_the_same_return_id_in_two_versions_has_two_distinct_details(db, client):
    a, b = detail(client, MAIN, 1, "SYN-RET-00001"), detail(client, MAIN, 2, "SYN-RET-00001")
    assert a.status_code == b.status_code == 200
    pa, pb = a.json()["provenance"], b.json()["provenance"]
    assert (pa["dataset_version"], pb["dataset_version"]) == (1, 2)
    assert pa["batch_id"] == db.batches[(MAIN, 1)] and pb["batch_id"] == db.batches[(MAIN, 2)]
    assert a.json()["ref"] != b.json()["ref"]


def test_detail_content_matches_the_stored_records(db, client):
    # A return with comments and a correction case citing it.
    return_id = one(
        db,
        """SELECT r.return_id FROM returns r JOIN return_datasets d ON d.id = r.dataset_pk
            WHERE d.dataset_id = :d AND d.dataset_version = 1
              AND EXISTS (SELECT 1 FROM return_comments c
                           WHERE c.dataset_pk = r.dataset_pk AND c.return_id = r.return_id)
              AND EXISTS (SELECT 1 FROM correction_case_evidence e
                           WHERE e.dataset_pk = r.dataset_pk AND e.evidence_type = 'return'
                             AND e.evidence_id = r.return_id)
            ORDER BY r.return_id LIMIT 1""",
        d=MAIN,
    )
    response = detail(client, MAIN, 1, return_id)
    assert response.status_code == 200
    body = response.json()
    with db.owner.connect() as conn:
        pk = conn.execute(
            text("SELECT id FROM return_datasets WHERE dataset_id = :d AND dataset_version = 1"),
            {"d": MAIN},
        ).scalar_one()
        r = (
            conn.execute(
                text("SELECT * FROM returns WHERE dataset_pk = :pk AND return_id = :r"),
                {"pk": pk, "r": return_id},
            )
            .mappings()
            .one()
        )
        oi = (
            conn.execute(
                text("SELECT * FROM order_items WHERE dataset_pk = :pk AND order_item_id = :i"),
                {"pk": pk, "i": r["order_item_id"]},
            )
            .mappings()
            .one()
        )
        raw = (
            conn.execute(
                text(
                    "SELECT rcr.raw_line, rcr.id, p.raw_record_id FROM raw_catalog_records rcr "
                    "JOIN catalog_datasets cd ON cd.id = rcr.dataset_pk "
                    "JOIN products p ON p.product_id = rcr.source_key "
                    "WHERE cd.dataset_id = :d AND cd.dataset_version = :v AND rcr.source_key = :p"
                ),
                {
                    "d": oi["catalog_dataset_id"],
                    "v": oi["catalog_dataset_version"],
                    "p": oi["product_id"],
                },
            )
            .mappings()
            .one()
        )
        comments = conn.execute(
            text(
                "SELECT comment_id, text FROM return_comments WHERE dataset_pk = :pk "
                "AND return_id = :r ORDER BY submitted_at, comment_id"
            ),
            {"pk": pk, "r": return_id},
        ).all()
        dataset = (
            conn.execute(text("SELECT * FROM return_datasets WHERE id = :pk"), {"pk": pk})
            .mappings()
            .one()
        )
        source_file = conn.execute(
            text("SELECT name FROM return_import_files WHERE id = :f"), {"f": r["source_file_pk"]}
        ).scalar_one()

    assert body["requested_at"] == cf.format_timestamp(r["requested_at"])
    assert body["status"] == r["status"] and body["quantity"] == r["quantity"]
    assert {k: v for k, v in body["order_item"].items() if k != "provenance"} == {
        "order_item_id": oi["order_item_id"],
        "quantity": oi["quantity"],
        "unit_price": cf.format_money(oi["unit_price"]),
    }
    assert body["order_item"]["provenance"]["source_record_id"] == oi["order_item_id"]
    line = json.loads(raw["raw_line"])
    snap = body["listing_snapshot"]
    assert snap["raw_sha256"] == oi["catalog_raw_sha256"]
    assert {k: snap[k] for k in ("title", "brand", "category", "currency")} == {
        k: line[k] for k in ("title", "brand", "category", "currency")
    }
    assert snap["matches_current_catalog"] is (raw["id"] == raw["raw_record_id"])
    assert [(c["comment_id"], c["text"]) for c in body["customer_statements"]["comments"]] == [
        tuple(c) for c in comments
    ]
    assert body["customer_statements"]["customer_reason_code"] == r["customer_reason_code"]
    presence = body["evidence_presence"]
    assert presence["return_comments"] == len(comments) >= 1
    assert presence["inspection_observations"] == len(body["inspection_observations"])
    assert presence["correction_cases"] == len(body["correction_cases"]) >= 1
    assert all(c["evidence_count"] >= 1 for c in body["correction_cases"])
    if body["shipment"] is not None:
        assert presence["delivery_events"] == len(body["shipment"]["delivery_events"])
    assert body["provenance"] == {
        "source_type": dataset["source_type"],
        "source_name": dataset["source_name"],
        "license": dataset["license_identifier"],
        "source_record_id": return_id,
        "dataset_version": 1,
        "ingestion_timestamp": body["provenance"]["ingestion_timestamp"],
        "transformation_version": "returns-import-1",
        "is_synthetic": True,
        "batch_id": db.batches[(MAIN, 1)],
        "source_file": source_file,
        "source_row": r["source_row"],
    }


def test_every_listed_return_has_a_readable_detail(db, client):
    """Every stored return of one version serves a detail as the API role (privileges, joins
    and snapshot lines are complete)."""
    items = walk(client, [100], dataset_id=MAIN, dataset_version="1")
    assert len(items) == one(
        db,
        "SELECT count(*) FROM returns r JOIN return_datasets d "
        "ON d.id = r.dataset_pk WHERE d.dataset_id = :d AND "
        "d.dataset_version = 1",
        d=MAIN,
    )
    for dataset_id, version, return_id in items:
        assert detail(client, dataset_id, version, return_id).status_code == 200


def test_unknown_returns_and_batches_are_404s(client):
    assert detail(client, MAIN, 3, "SYN-RET-00001").json()["code"] == "return_not_found"
    assert detail(client, MAIN, 1, "SYN-RET-99999").json()["code"] == "return_not_found"
    missing = client.get(f"/returns/import-batches/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["code"] == "batch_not_found"


# ---- batches ---------------------------------------------------------------------------------


def test_batches_are_newest_first_with_file_metadata_only(db, client):
    pages, cursor = [], None
    while True:
        params = {"limit": "1"} | ({} if cursor is None else {"cursor": cursor})
        body = client.get("/returns/import-batches", params=params).json()
        pages += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    with db.owner.connect() as conn:
        expected = [
            str(row.batch_id)
            for row in conn.execute(
                text(
                    "SELECT batch_id FROM return_import_batches ORDER BY imported_at DESC, batch_id"
                )
            )
        ]
    assert [b["batch_id"] for b in pages] == expected
    one_batch = client.get(f"/returns/import-batches/{db.batches[(ALT, 1)]}").json()
    assert one_batch["dataset_id"] == ALT and one_batch["imported_by_label"] == LABEL
    assert [f["name"] for f in one_batch["files"]] == ["bundle.json"]
    assert set(one_batch["files"][0]) == {
        "name",
        "entity",
        "media_type",
        "size_bytes",
        "raw_sha256",
        "row_count",
    }


# ---- READ ONLY, REPEATABLE READ (AC-13 (2)) --------------------------------------------------


def test_read_snapshots_are_read_only_repeatable_read_and_refuse_writes(db):
    # As the owner (superuser), so only the transaction mode can stop the write.
    with returns_read.read_snapshot(db.owner) as conn:
        assert conn.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with pytest.raises(DBAPIError) as caught:
            conn.execute(text("CREATE TABLE m18_read_probe (x int)"))
        assert getattr(caught.value.orig, "sqlstate", None) == "25006"  # read_only_sql_transaction
    with db.owner.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('m18_read_probe')")).scalar_one() is None


# ---- per-record provenance, I1 and M1 (owner decisions 2026-10-28) ---------------------------


def stored_provenance(db: Db, table: str, key_column: str, key: str, dataset: tuple) -> dict:
    """The eight fields plus location of one stored record, from its own references."""
    sql = f"""
        SELECT d.source_type, d.source_name, d.license_identifier AS license,
               x.source_record_id, d.dataset_version, b.imported_at, b.transformation_version,
               x.is_synthetic, b.batch_id, f.name AS source_file, x.source_row
          FROM {table} x
          JOIN return_datasets d ON d.id = x.dataset_pk
          JOIN return_import_batches b ON b.id = x.batch_pk
          JOIN return_import_files f ON f.id = x.source_file_pk
         WHERE d.dataset_id = :d AND d.dataset_version = :v AND x.{key_column} = :k
    """  # noqa: S608 - fixed test SQL
    with db.owner.connect() as conn:
        row = conn.execute(text(sql), {"d": dataset[0], "v": dataset[1], "k": key}).mappings().one()
    return {
        **{k: v for k, v in row.items() if k not in ("imported_at", "batch_id")},
        "ingestion_timestamp": cf.format_timestamp(row["imported_at"]),
        "batch_id": str(row["batch_id"]),
    }


def test_nested_records_carry_their_own_provenance_not_the_parents(db, client):
    return_id = one(
        db,
        """SELECT r.return_id FROM returns r JOIN return_datasets d ON d.id = r.dataset_pk
            WHERE d.dataset_id = :d AND d.dataset_version = :v
              AND EXISTS (SELECT 1 FROM return_comments c
                           WHERE c.dataset_pk = r.dataset_pk AND c.return_id = r.return_id)
            ORDER BY r.return_id LIMIT 1""",
        d=SPLIT[0],
        v=SPLIT[1],
    )
    body = detail(client, *SPLIT, return_id).json()
    # Summaries (§7 amendment) carry each return's own provenance too.
    summaries = client.get(
        "/returns",
        params={"dataset_id": SPLIT[0], "dataset_version": str(SPLIT[1]), "limit": "100"},
    ).json()["items"]
    assert summaries
    for item in summaries:
        assert item["provenance"] == stored_provenance(
            db, "returns", "return_id", item["ref"]["return_id"], SPLIT
        )
        assert item["provenance"]["batch_id"] == db.split_batches["first"]
    # The return came with the first batch, its comments only with the second.
    assert body["provenance"]["batch_id"] == db.split_batches["first"]
    assert body["customer_statements"]["comments"]
    for comment in body["customer_statements"]["comments"]:
        assert comment["provenance"]["batch_id"] == db.split_batches["rest"]
        assert comment["provenance"] == stored_provenance(
            db, "return_comments", "comment_id", comment["comment_id"], SPLIT
        )
    nested = [
        ("orders", "order_id", body["order"]),
        ("order_items", "order_item_id", body["order_item"]),
        *(
            ("inspection_observations", "observation_id", o)
            for o in body["inspection_observations"]
        ),
        *(("customer_reviews", "review_id", r) for r in body["related_reviews"]),
        *(("support_complaints", "complaint_id", c) for c in body["related_complaints"]),
        *(("correction_cases", "case_id", c) for c in body["correction_cases"]),
    ]
    if body["shipment"] is not None:
        nested.append(("shipments", "shipment_id", body["shipment"]))
        nested += [("delivery_events", "event_id", e) for e in body["shipment"]["delivery_events"]]
    for table, key_column, record in nested:
        assert record["provenance"] == stored_provenance(
            db, table, key_column, record[key_column], SPLIT
        ), table


def _expected_cases(db: Db, dataset: tuple) -> dict[str, set[str]]:
    """I1, computed independently: return_id -> cases citing it, its comments or observations."""
    with db.owner.connect() as conn:
        pk = conn.execute(
            text("SELECT id FROM return_datasets WHERE dataset_id = :d AND dataset_version = :v"),
            {"d": dataset[0], "v": dataset[1]},
        ).scalar_one()

        def pairs(sql: str) -> list[tuple]:
            return [tuple(row) for row in conn.execute(text(sql), {"p": pk})]

        returns = [r for (r,) in pairs("SELECT return_id FROM returns WHERE dataset_pk = :p")]
        owner_of = {("return", r): r for r in returns}
        owner_of |= {
            ("return_comment", c): r
            for c, r in pairs(
                "SELECT comment_id, return_id FROM return_comments WHERE dataset_pk = :p"
            )
        }
        owner_of |= {
            ("inspection_observation", o): r
            for o, r in pairs(
                "SELECT observation_id, return_id FROM inspection_observations "
                "WHERE dataset_pk = :p"
            )
        }
        evidence = pairs(
            "SELECT case_id, evidence_type, evidence_id FROM correction_case_evidence "
            "WHERE dataset_pk = :p"
        )
    expected: dict[str, set[str]] = {r: set() for r in returns}
    for case_id, kind, evidence_id in evidence:
        if (kind, evidence_id) in owner_of:
            expected[owner_of[(kind, evidence_id)]].add(case_id)
    return expected


def test_correction_cases_follow_the_approved_evidence_linkage(db, client):
    """I1 (approved): positive and negative for every return of one version."""
    expected = _expected_cases(db, (MAIN, 1))
    with db.owner.connect() as conn:
        case_product = dict(
            conn.execute(
                text(
                    "SELECT cc.case_id, cc.product_id FROM correction_cases cc "
                    "JOIN return_datasets d ON d.id = cc.dataset_pk "
                    "WHERE d.dataset_id = :d AND d.dataset_version = 1"
                ),
                {"d": MAIN},
            ).all()
        )
    positives = negatives = 0
    for return_id, cases in expected.items():
        body = detail(client, MAIN, 1, return_id).json()
        shown = {c["case_id"] for c in body["correction_cases"]}
        assert shown == cases, return_id
        positives += bool(cases)
        # Negative: a case on the same product that cites none of this return's records.
        product = body["product"]["product_id"]
        negatives += sum(1 for c, p in case_product.items() if p == product and c not in cases)
    assert positives >= 1 and negatives >= 1


def test_the_snapshot_price_is_the_raw_catalog_value_uncoerced(db, client):
    """M1: an integer stays an integer and seller text stays text; never the current price."""
    with db.owner.connect() as conn:
        rows = conn.execute(
            text(
                """SELECT DISTINCT ON (oi.product_id) r.return_id, rcr.raw_line
                     FROM returns r JOIN return_datasets d ON d.id = r.dataset_pk
                     JOIN order_items oi ON oi.dataset_pk = r.dataset_pk
                      AND oi.order_item_id = r.order_item_id
                     JOIN catalog_datasets cd ON cd.dataset_id = oi.catalog_dataset_id
                      AND cd.dataset_version = oi.catalog_dataset_version
                     JOIN raw_catalog_records rcr ON rcr.dataset_pk = cd.id
                      AND rcr.source_key = oi.product_id
                    WHERE d.dataset_id = :d AND d.dataset_version = 1
                    ORDER BY oi.product_id, r.return_id"""
            ),
            {"d": MAIN},
        ).all()
    kinds = set()
    for return_id, raw_line in rows:
        raw_price = json.loads(raw_line)["price"]
        price = detail(client, MAIN, 1, return_id).json()["listing_snapshot"]["price"]
        assert price == raw_price and type(price) is type(raw_price)
        kinds.add(type(price))
    assert int in kinds


# ---- real role creation and authentication (secret-safe helper) -------------------------------


def _connects(settings, role: str, password: str, database: str) -> tuple[bool, str | None]:
    """Whether `role` authenticates; on failure only the SQLSTATE is kept."""
    url = settings.database_url(database=database).set(username=role, password=password)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT current_user")).scalar_one() == role, None
    except DBAPIError as exc:
        # psycopg may leave `sqlstate` unset for connection-phase errors; the server's fixed
        # message (it never contains the password) identifies an authentication failure.
        state = getattr(exc.orig, "sqlstate", None)
        if state is None and "password authentication failed" in str(exc.orig):
            state = "28P01"
        return False, state
    finally:
        engine.dispose()


def test_fixture_roles_are_scram_and_authenticate(db, settings):
    with db.owner.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolpassword LIKE 'SCRAM-SHA-256$4096:%' AS scram, rolsuper "
                "FROM pg_authid WHERE rolname = ANY(:roles)"
            ),
            {"roles": [db.app_role, db.importer_role]},
        ).all()
    assert {r.rolname for r in rows} == {db.app_role, db.importer_role}
    assert all(r.scram and not r.rolsuper for r in rows)
    for role in (db.app_role, db.importer_role):
        ok, _ = _connects(settings, role, db.passwords[role], db.name)
        assert ok, role
        ok, sqlstate = _connects(settings, role, secrets.token_urlsafe(24), db.name)
        assert not ok and sqlstate == "28P01", role  # invalid_password


def test_role_creation_on_postgres_leaks_neither_password_nor_verifier(
    db, settings, caplog, capsys, monkeypatch
):
    """A probe role on the real server, created twice (the second fails): neither the password
    nor any part of its verifier reaches logs, output, errors or tracebacks; the session guards
    are reset afterwards; the role authenticates and a wrong password does not."""
    salt = bytes(range(16))
    monkeypatch.setattr(returns_roles, "secrets", SimpleNamespace(token_bytes=lambda n: salt[:n]))
    role = f"m18t_probe_{uuid.uuid4().hex[:10]}"
    password = secrets.token_urlsafe(24)
    verifier = returns_roles.scram_sha256_verifier(password, salt=salt)
    salt_part, keys = verifier.split("$", 1)[1].split("$")
    parts = [password, verifier, salt_part.split(":")[1], *keys.split(":")]
    caplog.set_level(logging.DEBUG)
    admin = create_engine(
        settings.database_url(database=db.name), isolation_level="AUTOCOMMIT", echo=True
    )
    try:
        with admin.connect() as conn:
            create_login_role(conn, role, password)
            with pytest.raises(RoleSetupError) as duplicate:
                create_login_role(conn, role, password)
            guard = conn.exec_driver_sql("SHOW log_min_error_statement").scalar_one()
        ok, _ = _connects(settings, role, password, "postgres")
        wrong_ok, wrong_state = _connects(settings, role, secrets.token_urlsafe(24), "postgres")
        captured = capsys.readouterr()
        surfaces = [
            caplog.text,
            captured.out,
            captured.err,
            str(duplicate.value),
            repr(duplicate.value),
            "".join(traceback.format_exception(duplicate.value)),
        ]
        leaked = any(part in surface for part in parts for surface in surfaces)
        assert not leaked, "secret material reached a log, output or error surface"
        assert "CREATE ROLE" not in caplog.text
        assert str(duplicate.value).endswith("(sqlstate 42710)")  # duplicate_object
        assert duplicate.value.__context__ is None and duplicate.value.__cause__ is None
        assert guard != "panic"  # RESET ALL ran
        assert ok and not wrong_ok and wrong_state == "28P01"
    finally:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
            gone = conn.execute(
                text("SELECT count(*) FROM pg_roles WHERE rolname = :r"), {"r": role}
            ).scalar_one()
        admin.dispose()
    assert gone == 0
