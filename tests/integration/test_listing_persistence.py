"""M13-3 listing persistence on throwaway databases (never the development database).

Every database here comes from the `scratch_database` / `migrated_engine` /
`writable_seeded_engine` fixtures, and each test confirms the target name before writing. All
listings are SYNTHETIC test fixtures.
"""

import hashlib
import json
import threading
import time

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.orm import Session

from ecommerce_search.models.listings import (
    ListingCandidate,
    ListingFinding,
    ListingNormalizationRun,
    ListingVersion,
)
from ecommerce_search.normalization import cli, store
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.models import FindingKind, Outcome
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.store import (
    IntegrityFailure,
    ListingNotFound,
    NormalizationMismatch,
    NotMigrated,
    candidate_record,
    finding_record,
    listing_lock_key,
    load_version,
    read_listing,
    submit_listing,
)

pytestmark = pytest.mark.integration

SCRATCH_PREFIX = "ecommerce_search_test_"
LISTING_TABLES = (
    "listing_versions",
    "listing_normalization_runs",
    "listing_candidates",
    "listing_findings",
)
CATALOG_AND_SEARCH_TABLES = (
    "catalog_datasets",
    "raw_catalog_records",
    "products",
    "laptop_specs",
    "phone_specs",
    "shoe_specs",
    "headphone_specs",
    "catalog_reviews",
    "product_search_documents",
    "product_embeddings",
)

# SYNTHETIC listings. COVER_1 + COVER_2 produce every finding kind, outcome, basis and value
# type (asserted in tests/unit/normalization/test_listing_store_records.py for the same data).
COVER_1 = {
    "seller_id": "SYN-TEST-SELLER",
    "seller_listing_ref": "SYN-COVER-1",
    "category": "laptop",
    "subcategory": "gaming-shoe",
    "brand": "Nike",
    "title": "Zénith 🔥 laptop 16GB RAM 512GB NVMe SSD up to 64GB RAM",
    "description": "Ships with 1TB HDD",
    "price": "₹40,000",
    "currency": "inr",
    "availability": "in_stock",
    "ram_gb": "8 gb",
    "storage_type": "NVMe SSD",
    "screen_size_inches": "999 inch",
    "weight_kg": "",
    "size": "9",
    "rating": 4.5,
    "review_count": 12,
    "color_code": "x",
}
COVER_2 = {
    "seller_id": "SYN-TEST-SELLER",
    "seller_listing_ref": "SYN-COVER-2",
    "brand": "hp",
    "wireless": True,
    "connectivity": "wired",
    "storage_type": "SATA",
    "ram_gb": "16 GiB",
    "anc": None,
}


def raw_of(listing: dict, *, sort_keys: bool = False) -> bytes:
    return json.dumps(listing, ensure_ascii=False, sort_keys=sort_keys).encode("utf-8")


def listing(ref: str = "SYN-REF-1", **fields) -> bytes:
    return raw_of({"seller_id": "SYN-TEST-SELLER", "seller_listing_ref": ref, **fields})


def scratch(engine) -> None:
    """Confirm the write target is a throwaway test database before any write."""
    assert engine.url.database.startswith(SCRATCH_PREFIX), engine.url.database


def submit(engine, data: bytes):
    scratch(engine)
    return submit_listing(engine, parse_raw_listing(data))


def counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in LISTING_TABLES
        }


def table_contents(engine, tables) -> dict[str, tuple[int, str]]:
    """Row count plus an order-independent digest of every row's full text for each table."""
    with engine.connect() as conn:
        return {
            t: tuple(
                conn.execute(
                    text(
                        f"SELECT count(*), md5(coalesce(string_agg(r::text, '|' ORDER BY r::text), '')) "  # noqa: S608
                        f"FROM {t} r"
                    )
                ).one()
            )
            for t in tables
        }


def read(engine, seller="SYN-TEST-SELLER", ref="SYN-REF-1", version=None):
    return read_listing(engine, seller, ref, version)


# ---- write path ---------------------------------------------------------------------------


def test_submit_stores_exact_raw_bytes_and_every_record_faithfully(migrated_engine):
    for data in (raw_of(COVER_1), raw_of(COVER_2)):
        raw = parse_raw_listing(data)
        expected = normalize_listing(raw)
        result = submit(migrated_engine, data)
        assert (result.version_number, result.version_created, result.run_created) == (
            1,
            True,
            True,
        )
        assert result.output_sha256 == expected.output_sha256()
        stored = read(migrated_engine, ref=raw.identity.seller_listing_ref)
        assert stored.raw_bytes == data  # exact UTF-8 bytes, non-ASCII and astral included
        assert stored.raw_sha256 == hashlib.sha256(data).hexdigest() == raw.raw_sha256
        (run,) = stored.runs
        assert (run.rules_version, run.rules_digest, run.taxonomy_version) == (
            expected.rules_version,
            expected.rules_digest,
            expected.taxonomy_version,
        )
        assert run.candidates == tuple(candidate_record(c) for c in expected.candidates)
        assert run.findings == tuple(finding_record(f) for f in expected.findings)
        assert run.recorded_output() == expected.model_dump(mode="json")
        assert run.output_sha256 == expected.output_sha256()
    with migrated_engine.connect() as conn:
        kinds = set(conn.execute(text("SELECT DISTINCT kind FROM listing_findings")).scalars())
        outcomes = set(
            conn.execute(text("SELECT DISTINCT outcome FROM listing_candidates")).scalars()
        )
    assert kinds == {k.value for k in FindingKind}
    assert outcomes == {o.value for o in Outcome}


def test_consecutive_identical_submission_is_idempotent(migrated_engine):
    data = listing(brand="hp", ram_gb="16 gb")
    first = submit(migrated_engine, data)
    before = table_contents(migrated_engine, LISTING_TABLES)
    again = submit(migrated_engine, data)
    assert (again.version_number, again.version_created, again.run_created) == (1, False, False)
    assert again.output_sha256 == first.output_sha256
    assert table_contents(migrated_engine, LISTING_TABLES) == before


def test_a_b_a_creates_three_versions_and_key_order_is_a_new_version(migrated_engine):
    a, b = listing(brand="hp"), listing(brand="dell")
    numbers = [submit(migrated_engine, data).version_number for data in (a, b, a)]
    assert numbers == [1, 2, 3]
    assert read(migrated_engine, version=3).raw_bytes == read(migrated_engine, version=1).raw_bytes
    assert read(migrated_engine).version_number == 3
    assert read(migrated_engine, version=2).raw_bytes == b
    # Same content, different key order: a new raw version, but the same output hash.
    reordered = raw_of(json.loads(a), sort_keys=True)
    assert reordered != a
    fourth = submit(migrated_engine, reordered)
    assert fourth.version_number == 4 and fourth.version_created
    assert fourth.output_sha256 == read(migrated_engine, version=3).runs[0].output_sha256
    assert counts(migrated_engine)["listing_versions"] == 4


def test_identity_is_exact(migrated_engine):
    submit(migrated_engine, raw_of({"seller_id": "SYN-S", "seller_listing_ref": "SYN-R"}))
    submit(migrated_engine, raw_of({"seller_id": "syn-s", "seller_listing_ref": "SYN-R"}))
    submit(migrated_engine, raw_of({"seller_id": "SYN-S", "seller_listing_ref": "syn-r"}))
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT seller_id, seller_listing_ref, version_number FROM listing_versions")
        ).all()
    assert sorted(rows) == [("SYN-S", "SYN-R", 1), ("SYN-S", "syn-r", 1), ("syn-s", "SYN-R", 1)]


def test_changed_output_under_the_same_rules_version_fails_loudly(migrated_engine, monkeypatch):
    data = listing(brand="hp", ram_gb="16 gb")
    submit(migrated_engine, data)
    before = table_contents(migrated_engine, LISTING_TABLES)
    real = store.normalize_listing

    def changed(raw):  # same rules version and digest, different findings
        result = real(raw)
        return result.model_copy(update={"findings": result.findings[1:]})

    monkeypatch.setattr(store, "normalize_listing", changed)
    with pytest.raises(NormalizationMismatch) as excinfo:
        submit(migrated_engine, data)
    assert "new rules version" in str(excinfo.value) and "Nothing was written" in str(excinfo.value)

    def other_digest(raw):  # same rules version, different digest
        return real(raw).model_copy(update={"rules_digest": "d" * 64})

    monkeypatch.setattr(store, "normalize_listing", other_digest)
    with pytest.raises(NormalizationMismatch):
        submit(migrated_engine, data)
    assert table_contents(migrated_engine, LISTING_TABLES) == before


def test_a_new_rules_version_adds_a_run_and_keeps_the_old_one(migrated_engine, monkeypatch):
    data = listing(brand="hp")
    old = submit(migrated_engine, data)
    real = store.normalize_listing
    monkeypatch.setattr(
        store,
        "normalize_listing",
        lambda raw: real(raw).model_copy(update={"rules_version": "listing-norm-synthetic-2"}),
    )
    new = submit(migrated_engine, data)
    assert (new.version_number, new.version_created, new.run_created) == (1, False, True)
    runs = read(migrated_engine).runs
    assert [r.rules_version for r in runs] == [nr.RULES_VERSION, "listing-norm-synthetic-2"]
    assert runs[0].output_sha256 == old.output_sha256


@pytest.mark.parametrize("failing_step", ["_add_run", "_add_candidates", "_add_findings"])
def test_a_failure_leaves_no_partial_submission(migrated_engine, monkeypatch, failing_step):
    submit(migrated_engine, listing(brand="hp"))  # version 1 exists and must stay intact
    before = table_contents(migrated_engine, LISTING_TABLES)
    real = getattr(store, failing_step)

    def fail_after(*args, **kwargs):
        real(*args, **kwargs)  # the rows are flushed inside the transaction, then it fails
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(store, failing_step, fail_after)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        submit(migrated_engine, listing(brand="dell"))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        submit(migrated_engine, listing("SYN-REF-NEW", brand="dell"))
    assert table_contents(migrated_engine, LISTING_TABLES) == before


# ---- concurrency --------------------------------------------------------------------------


def _hold_lock_and_release_after_waiters(engine, key: int, waiters: int, work):
    """Hold the identity's advisory lock until `waiters` sessions are blocked on it, so the
    submissions really overlap, then release it and collect the results."""
    results, errors = [], []
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})

        def run(item):
            try:
                results.append(work(item))
            except Exception as exc:  # noqa: BLE001 - reported by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(waiters)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            blocked = holder.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                )
            ).scalar_one()
            if blocked == waiters:
                break
            time.sleep(0.05)
        assert blocked == waiters, f"only {blocked} of {waiters} submissions were waiting"
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        holder.commit()
        for thread in threads:
            thread.join(timeout=60)
    assert not errors, errors
    return results


def test_concurrent_different_submissions_get_consecutive_versions(settings, migrated_engine):
    scratch(migrated_engine)
    engine = create_engine(migrated_engine.url, pool_size=10)
    try:
        identity = parse_raw_listing(listing()).identity
        results = _hold_lock_and_release_after_waiters(
            engine,
            listing_lock_key(identity),
            6,
            lambda i: submit_listing(engine, parse_raw_listing(listing(price=f"{100 + i}"))),
        )
    finally:
        engine.dispose()
    assert sorted(r.version_number for r in results) == [1, 2, 3, 4, 5, 6]
    assert all(r.version_created and r.run_created for r in results)
    assert counts(migrated_engine)["listing_versions"] == 6
    assert counts(migrated_engine)["listing_normalization_runs"] == 6


def test_concurrent_identical_submissions_store_one_version(settings, migrated_engine):
    scratch(migrated_engine)
    engine = create_engine(migrated_engine.url, pool_size=10)
    data = listing(brand="hp", ram_gb="16 gb")
    try:
        identity = parse_raw_listing(data).identity
        results = _hold_lock_and_release_after_waiters(
            engine,
            listing_lock_key(identity),
            6,
            lambda _i: submit_listing(engine, parse_raw_listing(data)),
        )
    finally:
        engine.dispose()
    assert {r.version_number for r in results} == {1}
    assert sum(r.version_created for r in results) == 1
    assert sum(r.run_created for r in results) == 1
    assert len({r.output_sha256 for r in results}) == 1
    assert counts(migrated_engine)["listing_versions"] == 1
    assert counts(migrated_engine)["listing_normalization_runs"] == 1


# ---- database-level guarantees ------------------------------------------------------------


def _insert_version(conn, number: int, data: bytes, sha: str | None = None):
    conn.execute(
        text(
            "INSERT INTO listing_versions (seller_id, seller_listing_ref, version_number, "
            "raw_bytes, raw_sha256) VALUES ('SYN-S', 'SYN-R', :n, :b, :h)"
        ),
        {"n": number, "b": data, "h": sha or hashlib.sha256(data).hexdigest()},
    )


def _fails(engine, fragment, action):
    with (
        pytest.raises((IntegrityError, InternalError, ProgrammingError)) as excinfo,
        engine.begin() as conn,
    ):
        action(conn)
    assert fragment in str(excinfo.value.orig)


def test_database_enforces_version_sequence_uniqueness_and_raw_hash(migrated_engine):
    scratch(migrated_engine)
    a = b'{"seller_id":"SYN-S","seller_listing_ref":"SYN-R"}'
    b = b'{"seller_id":"SYN-S", "seller_listing_ref":"SYN-R"}'
    _fails(migrated_engine, "first version must be 1", lambda c: _insert_version(c, 2, a))
    with migrated_engine.begin() as conn:
        _insert_version(conn, 1, a)
    _fails(migrated_engine, "repeats the previous version", lambda c: _insert_version(c, 2, a))
    _fails(migrated_engine, "consecutive", lambda c: _insert_version(c, 3, b))
    _fails(
        migrated_engine,
        "ck_listing_versions_raw_sha256_matches_bytes",
        lambda c: _insert_version(c, 2, b, sha="0" * 64),
    )
    _fails(
        migrated_engine,
        "ck_listing_versions_raw_bytes_size",
        lambda c: c.execute(
            text(
                "INSERT INTO listing_versions (seller_id, seller_listing_ref, version_number, "
                "raw_bytes, raw_sha256) VALUES ('SYN-S2', 'SYN-R', 1, ''::bytea, :h)"
            ),
            {"h": hashlib.sha256(b"").hexdigest()},
        ),
    )
    with migrated_engine.begin() as conn:
        _insert_version(conn, 2, b)
        _insert_version(conn, 3, a)  # A -> B -> A
    # The unique constraint backs the lock: a duplicate version number cannot be stored even
    # when the sequence trigger is bypassed.
    with migrated_engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE listing_versions DISABLE TRIGGER trg_listing_versions_sequence")
        )
    try:
        _fails(
            migrated_engine,
            "uq_listing_versions_identity_version",
            lambda c: _insert_version(c, 3, b),
        )
    finally:
        with migrated_engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE listing_versions ENABLE TRIGGER trg_listing_versions_sequence")
            )


def test_history_is_append_only(migrated_engine):
    submit(migrated_engine, raw_of(COVER_1))
    before = table_contents(migrated_engine, LISTING_TABLES)
    statements = {
        "listing_versions": "UPDATE listing_versions SET version_number = 9",
        "listing_normalization_runs": "UPDATE listing_normalization_runs SET rules_version = 'x'",
        "listing_candidates": "UPDATE listing_candidates SET normalized_value = NULL",
        "listing_findings": "UPDATE listing_findings SET message = 'x'",
    }
    for table, update in statements.items():
        # Fixed table names from this module, not input.
        deletes = (f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE")  # noqa: S608
        for statement in (update, *deletes):
            with (
                pytest.raises((InternalError, ProgrammingError, IntegrityError)) as excinfo,
                migrated_engine.begin() as conn,
            ):
                conn.execute(text(statement))
            assert "append-only listing history" in str(excinfo.value.orig), statement
    assert table_contents(migrated_engine, LISTING_TABLES) == before


def test_downgrade_and_scratch_cleanup_still_work_with_stored_history(
    settings, scratch_database, migrator
):
    migrator.upgrade(scratch_database)
    engine = create_engine(settings.database_url(database=scratch_database))
    try:
        submit(engine, raw_of(COVER_1))
        migrator.downgrade(scratch_database, "0004")
        with engine.connect() as conn:
            tables = set(
                conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
            functions = set(
                conn.execute(
                    text(
                        "SELECT proname FROM pg_proc WHERE proname IN "
                        "('listing_history_append_only', 'listing_versions_check_sequence')"
                    )
                ).scalars()
            )
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert not tables & set(LISTING_TABLES) and functions == set() and version == "0004"
        assert {"products", "product_search_documents", "product_embeddings"} <= tables
        migrator.upgrade(scratch_database)
        assert counts(engine) == dict.fromkeys(LISTING_TABLES, 0)
        submit(engine, raw_of(COVER_1))
        migrator.downgrade(scratch_database, "base")
    finally:
        engine.dispose()
    # The fixture then drops the whole scratch database (DROP DATABASE ... WITH (FORCE)).


# ---- read path ----------------------------------------------------------------------------


def test_read_is_as_recorded_for_a_historical_run(migrated_engine, monkeypatch):
    # A run recorded under an older SYNTHETIC rules version, with a retired rule id and field
    # today's contracts would reject. It is read and verified as recorded, never re-normalized,
    # and its digest need not equal today's.
    data = listing(brand="hp")
    submit(migrated_engine, data)
    historical_candidate = {
        "field": "retired_field",
        "basis": "seller_field",
        "source_key": "brand",
        "raw_text": "hp",
        "span_start": None,
        "span_end": None,
        "outcome": "normalized",
        "normalized_value": "hp-as-recorded-then",
        "value_type": "text",
        "unit": None,
        "rule_id": "retired.rule",
    }
    output = {
        "rules_version": "listing-norm-0-synthetic",
        "rules_digest": "0" * 64,
        "taxonomy_version": "synthetic-taxonomy-0",
        "candidates": [historical_candidate],
        "findings": [],
    }
    output_sha = hashlib.sha256(nr.canonical_json(output)).hexdigest()
    with migrated_engine.begin() as conn:
        version_id = conn.execute(text("SELECT id FROM listing_versions")).scalar_one()
        run_id = conn.execute(
            text(
                "INSERT INTO listing_normalization_runs (listing_version_id, rules_version, "
                "rules_digest, taxonomy_version, output_sha256, candidate_count, finding_count, "
                "created_at) VALUES (:v, :rv, :rd, :tv, :o, 1, 0, now() - interval '1 day') "
                "RETURNING id"
            ),
            {
                "v": version_id,
                "rv": output["rules_version"],
                "rd": output["rules_digest"],
                "tv": output["taxonomy_version"],
                "o": output_sha,
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO listing_candidates (run_id, ordinal, field, basis, source_key, "
                "raw_text, outcome, normalized_value, value_type, rule_id) VALUES "
                "(:r, 0, 'retired_field', 'seller_field', 'brand', 'hp', 'normalized', "
                "'hp-as-recorded-then', 'text', 'retired.rule')"
            ),
            {"r": run_id},
        )

    def refuse(*_args, **_kwargs):
        raise AssertionError("reading history must not re-run normalization")

    monkeypatch.setattr(store, "normalize_listing", refuse)
    stored = read(migrated_engine)
    historical, current = stored.runs  # ordered by created time
    assert historical.rules_version == "listing-norm-0-synthetic"
    assert historical.recorded_output() == output and historical.output_sha256 == output_sha
    assert current.rules_version == nr.RULES_VERSION

    argv = ["show", "--database", migrated_engine.url.database]
    argv += ["--seller", "SYN-TEST-SELLER", "--ref", "SYN-REF-1"]
    assert cli.main(argv) == 0


def test_show_cli_reads_latest_and_specific_versions(migrated_engine, capsysbinary, tmp_path):
    database = migrated_engine.url.database
    scratch(migrated_engine)
    a, b = listing(brand="hp"), listing(brand="dell")
    for data, name in ((a, "a.json"), (b, "b.json")):
        path = tmp_path / name
        path.write_bytes(data)
        assert cli.main(["submit", "--database", database, "--file", str(path)]) == 0
    out = capsysbinary.readouterr().out.decode("utf-8")
    assert f"target database: {database}" in out and "version 2 created" in out
    before = table_contents(migrated_engine, LISTING_TABLES)
    base = ["show", "--database", database, "--seller", "SYN-TEST-SELLER", "--ref", "SYN-REF-1"]
    assert cli.main(base) == 0
    latest = json.loads(capsysbinary.readouterr().out)
    assert (latest["version_number"], latest["latest_version_number"]) == (2, 2)
    assert latest["raw_text"].encode("utf-8") == b
    assert latest["runs"][0]["same_as_current_rules"] is True
    assert cli.main([*base, "--version", "1"]) == 0
    first = json.loads(capsysbinary.readouterr().out)
    assert first["version_number"] == 1 and first["raw_text"].encode("utf-8") == a
    assert cli.main([*base, "--version", "3"]) == 1
    assert b"has no version 3 (latest is 2)" in capsysbinary.readouterr().err
    assert cli.main([*base[:-1], "SYN-ABSENT"]) == 1
    assert b"no stored listing" in capsysbinary.readouterr().err
    assert table_contents(migrated_engine, LISTING_TABLES) == before  # show is read-only


def _tamper(engine, table: str, statement: str, params: dict | None = None):
    scratch(engine)
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
        conn.execute(text(statement), params or {})
        conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))


@pytest.mark.parametrize(
    ("table", "statement", "fragment"),
    [
        (
            "listing_candidates",
            "UPDATE listing_candidates SET normalized_value = 'Tampered' "
            "WHERE outcome = 'normalized'",
            "do not reproduce output_sha256",
        ),
        (
            "listing_findings",
            "DELETE FROM listing_findings WHERE ordinal = 0",
            "finding rows",
        ),
        (
            "listing_normalization_runs",
            "UPDATE listing_normalization_runs SET rules_digest = repeat('e', 64)",
            "do not reproduce output_sha256",
        ),
    ],
)
def test_integrity_failures_are_detected_on_read(
    migrated_engine, capsysbinary, table, statement, fragment
):
    submit(migrated_engine, raw_of(COVER_1))
    _tamper(migrated_engine, table, statement)
    with pytest.raises(IntegrityFailure) as excinfo:
        read(migrated_engine, ref="SYN-COVER-1")
    assert fragment in str(excinfo.value)
    argv = ["show", "--database", migrated_engine.url.database]
    argv += ["--seller", "SYN-TEST-SELLER", "--ref", "SYN-COVER-1"]
    assert cli.main(argv) == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b"" and b"failed its integrity check" in captured.err


def test_raw_tampering_is_blocked_by_the_database_and_detected_on_read(migrated_engine):
    submit(migrated_engine, raw_of(COVER_1))
    with pytest.raises((IntegrityError, InternalError)):
        _tamper(
            migrated_engine,
            "listing_versions",
            "UPDATE listing_versions SET raw_bytes = raw_bytes || ' '::bytea",
        )
    # With the hash check satisfied too, the identity no longer matches the stored row.
    other = raw_of({**COVER_1, "seller_listing_ref": "SYN-OTHER"})
    _tamper(
        migrated_engine,
        "listing_versions",
        "UPDATE listing_versions SET raw_bytes = :b, raw_sha256 = :h",
        {"b": other, "h": hashlib.sha256(other).hexdigest()},
    )
    with pytest.raises(IntegrityFailure, match="identity does not match"):
        read(migrated_engine, ref="SYN-COVER-1")


def test_reading_an_absent_listing_or_version(migrated_engine):
    with pytest.raises(ListingNotFound):
        read(migrated_engine)
    submit(migrated_engine, listing())
    with Session(migrated_engine) as session, pytest.raises(ListingNotFound):
        load_version(session, "SYN-TEST-SELLER", "SYN-REF-1", 2)


# ---- not migrated, isolation --------------------------------------------------------------


def test_a_database_below_0005_refuses_cleanly(settings, scratch_database, migrator, tmp_path):
    migrator.upgrade(scratch_database, "0004")
    engine = create_engine(settings.database_url(database=scratch_database))
    try:
        with pytest.raises(NotMigrated, match="alembic upgrade head"):
            submit(engine, listing())
        with pytest.raises(NotMigrated):
            read(engine)
        path = tmp_path / "one.json"
        path.write_bytes(listing())
        assert cli.main(["submit", "--database", scratch_database, "--file", str(path)]) == 1
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0004"
            )
    finally:
        engine.dispose()


def test_submit_and_show_never_touch_catalog_or_search_tables(writable_seeded_engine, tmp_path):
    engine = writable_seeded_engine
    scratch(engine)
    before = table_contents(engine, CATALOG_AND_SEARCH_TABLES)
    assert before["products"][0] == 240  # the seeded catalog is really there
    for i, data in enumerate((raw_of(COVER_1), raw_of(COVER_2), listing(brand="hp"))):
        submit(engine, data)
        path = tmp_path / f"{i}.json"
        path.write_bytes(data)
        assert cli.main(["submit", "--database", engine.url.database, "--file", str(path)]) == 0
    argv = ["show", "--database", engine.url.database]
    assert cli.main([*argv, "--seller", "SYN-TEST-SELLER", "--ref", "SYN-COVER-1"]) == 0
    assert table_contents(engine, CATALOG_AND_SEARCH_TABLES) == before
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(ListingVersion)).scalar_one() == 3
        assert conn.execute(select(func.count()).select_from(ListingNormalizationRun)).scalar_one()
        assert conn.execute(select(func.count()).select_from(ListingCandidate)).scalar_one()
        assert conn.execute(select(func.count()).select_from(ListingFinding)).scalar_one()
