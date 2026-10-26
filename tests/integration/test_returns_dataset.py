"""M18-03C: the synthetic release files through the existing scoped importer, and
`returns verify`, on a scratch database with the real catalog and real per-module roles.

Covers AC-08 (verify: missing, extra and changed counts; partial-then-rest reaches the release
`records_sha256`), AC-14 (`scenarios.json` refused as an import part) and verify's read-only
behaviour.

One throwaway `ecommerce_search_test_*` database per module: migrated to head, the repository
catalog ingested with the existing catalog ingest, and two new LOGIN roles with random names
and generated passwords (never printed) given the `returns-grants-1` grants. Every import runs
as the importer role; verify runs as the API role. Version 1 holds the release as generated;
other dataset versions hold the same records under another descriptor version, so tests never
see each other's rows. The database and roles are dropped afterwards.
"""

import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from returns_support import scratch
from sqlalchemy import Engine, create_engine, pool, text

from ecommerce_search.config import get_settings
from ecommerce_search.ingestion.service import ingest_file
from ecommerce_search.models.returns import RETURN_TABLES
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import cli
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import grants as rg
from ecommerce_search.returns.bundle import BUNDLE_NAME, Part
from ecommerce_search.returns.redaction import matches_any
from ecommerce_search.returns.service import commit_import, validate_import
from ecommerce_search.returns.verify import verify_dataset
from returns_release import (
    BUNDLE,
    CSV_DIR,
    ROOT,
    SCENARIOS,
    bundle,
    csv_parts,
    json_parts,
    provenance,
)

pytestmark = pytest.mark.integration

LABEL = "test-importer"
CHECKED_AT = datetime(2026, 10, 26, tzinfo=UTC)
E = rc.Entity
PARENTS = ("orders", "order_items", "shipments", "delivery_events")
SEED = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
SEED_PROVENANCE = ROOT / "data" / "seed" / "catalog_seed_v1.provenance.json"


@dataclass
class Db:
    name: str
    owner: Engine
    importer: Engine
    app: Engine
    importer_role: str
    app_role: str
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
        outcome = ingest_file(owner, SEED, SEED_PROVENANCE)
        assert outcome.result is not None and outcome.result.inserted == 240
        with owner.begin() as conn:
            rg.apply_grants(conn, app_role=app, importer_role=importer)
        engines += [
            _role_engine(settings, name, importer, passwords[importer]),
            _role_engine(settings, name, app, passwords[app]),
        ]
        yield Db(name, owner, engines[1], engines[2], importer, app, passwords)
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in (app, importer):
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


# ---- helpers -------------------------------------------------------------------------------


def commit(db: Db, parts):
    return commit_import(db.importer, parts, imported_by_label=LABEL, checked_at=CHECKED_AT)


def verify(db: Db, parts, engine: Engine | None = None, **kwargs):
    return verify_dataset(
        db.app if engine is None else engine, parts, checked_at=CHECKED_AT, **kwargs
    )


def release_json(version: int, *, entities=None, edit=None, **dataset) -> list[Part]:
    """The release records under another dataset version (and descriptor edits);
    `entities` keeps only those arrays, `edit` changes the parsed bundle in place."""
    body = bundle()
    body["dataset"] |= {"dataset_version": version, **dataset}
    if entities is not None:
        for e in E:
            if e.value not in entities:
                body.pop(e.value)
    if edit is not None:
        edit(body)
    return [Part(BUNDLE_NAME, json.dumps(body, ensure_ascii=False).encode("utf-8"))]


def table_counts(db: Db) -> dict[str, int]:
    with db.owner.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in RETURN_TABLES
        }


def stored_records_sha256(db: Db, version: int) -> str:
    rows = []
    with db.owner.connect() as conn:
        pk = conn.execute(
            text("SELECT id FROM return_datasets WHERE dataset_id = :d AND dataset_version = :v"),
            {"d": "synthetic-returns", "v": version},
        ).scalar_one()
        for e in E:
            rows += [
                (e.value, rid, sha)
                for rid, sha in conn.execute(
                    text(
                        f"SELECT source_record_id, record_sha256 FROM {e.value} "  # noqa: S608
                        "WHERE dataset_pk = :pk"
                    ),
                    {"pk": pk},
                )
            ]
    return cf.records_sha256(rows)


def differences(report) -> dict[str, tuple[int, int, int]]:
    return {
        e: (c.missing, c.extra, c.changed)
        for e, c in report.counts.items()
        if (c.missing, c.extra, c.changed) != (0, 0, 0)
    }


@pytest.fixture(scope="module")
def imported(db):
    """The JSON release files, imported once as dataset version 1."""
    before = table_counts(db)
    report = commit(db, json_parts())
    return before, report


# ---- the release as generated ---------------------------------------------------------------


def test_the_release_imports_through_the_scoped_importer_and_verifies(db, imported):
    before, report = imported
    prov = provenance()
    assert report.status == "imported" and report.error_count == 0
    assert report.bundle_sha256 == prov["bundle_sha256"]["json"]
    assert {e: (c.received, c.new, c.unchanged) for e, c in report.counts.items()} == {
        e: (n, n, 0) for e, n in prov["record_counts"].items()
    }
    after = table_counts(db)
    for e, n in prov["record_counts"].items():
        assert after[e] - before[e] == n
    assert stored_records_sha256(db, 1) == prov["records_sha256"]

    result = verify(db, json_parts())
    assert result.status == "match"
    assert result.dataset_stored is True and result.descriptor_matches is True
    assert result.expected_records_sha256 == result.stored_records_sha256 == prov["records_sha256"]
    assert differences(result) == {}
    assert {e: c.stored for e, c in result.counts.items()} == prov["record_counts"]


def test_stored_free_text_is_redacted_and_every_row_is_synthetic(db, imported):
    with db.owner.connect() as conn:
        texts = conn.execute(text("SELECT text FROM return_comments")).scalars().all()
        redacted = conn.execute(
            text(
                "SELECT sum(redacted_email_count + redacted_upi_count + redacted_number_count "
                "+ redacted_pin_count) FROM return_comments"
            )
        ).scalar_one()
        for e in E:
            assert (
                conn.execute(
                    text(f"SELECT count(*) FROM {e.value} WHERE NOT is_synthetic")  # noqa: S608
                ).scalar_one()
                == 0
            )
    assert len(texts) == provenance()["record_counts"]["return_comments"]
    assert not any(matches_any(t) for t in texts)
    assert redacted >= 10


def test_repeated_imports_are_idempotent_in_both_formats(db, imported):
    _, first = imported
    counts = table_counts(db)
    again = commit(db, json_parts())
    assert (again.status, again.batch_id) == ("already_imported", first.batch_id)
    other = commit(db, csv_parts())
    assert other.status == "no_new_records" and other.batch_id is None
    assert all(c.unchanged == c.received for c in other.counts.values())
    assert validate_import(db.app, json_parts(), checked_at=CHECKED_AT).status == "already_imported"
    assert validate_import(db.app, csv_parts(), checked_at=CHECKED_AT).status == "no_new_records"
    assert table_counts(db) == counts
    assert verify(db, csv_parts()).status == "match"


# ---- verify: missing, extra and changed --------------------------------------------------


def test_partial_import_then_the_rest_reaches_the_release_checksum(db):
    version = 2
    prov = provenance()
    first = commit(db, release_json(version, entities=PARENTS))
    assert first.status == "imported"

    partial = verify(db, release_json(version))
    assert partial.status == "mismatch"
    assert differences(partial) == {
        e: (n, 0, 0) for e, n in prov["record_counts"].items() if e not in PARENTS
    }
    assert partial.expected_records_sha256 == prov["records_sha256"]
    assert partial.stored_records_sha256 != prov["records_sha256"]

    rest = [e.value for e in E if e.value not in PARENTS]
    second = commit(db, release_json(version, entities=rest))
    assert second.status == "imported"
    assert {e: c.new for e, c in second.counts.items() if c.received} == {
        e: prov["record_counts"][e] for e in rest
    }

    full = verify(db, release_json(version))
    assert full.status == "match" and differences(full) == {}
    assert stored_records_sha256(db, version) == prov["records_sha256"]
    assert commit(db, release_json(version)).status == "no_new_records"


def _extra_review(body: dict) -> None:
    review = dict(body["customer_reviews"][0])
    body["customer_reviews"] = [review | {"review_id": "SYN-REV-09999", "text": "Extra record."}]
    for e in E:
        if e.value != "customer_reviews":
            body.pop(e.value)


def _changed_review(body: dict) -> None:
    body["customer_reviews"][0]["text"] = "Changed after release."


def test_verify_detects_extra_and_changed_records(db):
    version = 3
    assert commit(db, release_json(version)).status == "imported"
    assert verify(db, release_json(version)).status == "match"

    assert commit(db, release_json(version, edit=_extra_review)).status == "imported"
    extra = verify(db, release_json(version))
    assert extra.status == "mismatch"
    assert differences(extra) == {"customer_reviews": (0, 1, 0)}
    assert extra.expected_records_sha256 == provenance()["records_sha256"]

    changed = verify(db, release_json(version, edit=_changed_review))
    assert changed.status == "mismatch"
    assert differences(changed) == {"customer_reviews": (0, 1, 1)}


def test_verify_reports_a_different_descriptor_and_an_unstored_version(db, imported):
    other = verify(db, release_json(1, notes="A different note."))
    assert other.status == "mismatch"
    assert other.descriptor_matches is False and differences(other) == {}

    absent = verify(db, release_json(99))
    assert absent.status == "mismatch" and absent.dataset_stored is False
    assert absent.descriptor_matches is None
    assert differences(absent) == {e: (n, 0, 0) for e, n in provenance()["record_counts"].items()}
    assert absent.stored_records_sha256 == cf.records_sha256([])


def test_verify_reads_in_a_read_only_snapshot_and_never_writes(db, imported):
    seen = {}

    def checkpoint(name, conn):
        seen[name] = (
            conn.execute(text("SHOW transaction_read_only")).scalar_one(),
            conn.execute(text("SHOW transaction_isolation")).scalar_one(),
        )

    counts = table_counts(db)
    for engine in (db.app, db.importer):
        assert verify(db, json_parts(), engine=engine, checkpoint=checkpoint).status == "match"
        assert seen == {"snapshot": ("on", "repeatable read")}
    assert verify(db, release_json(99)).status == "mismatch"
    assert table_counts(db) == counts


# ---- scenarios.json is never imported (AC-14) -------------------------------------------


def test_scenarios_json_is_refused_as_an_import_part(db, imported):
    counts = table_counts(db)
    scenarios = Part("scenarios.json", SCENARIOS.read_bytes())
    for parts in (json_parts() + [scenarios], [scenarios]):
        report = commit(db, parts)
        assert report.status == "invalid" and report.batch_id is None
        assert "unexpected_file" in {e.code for e in report.errors}
    assert table_counts(db) == counts


@pytest.fixture
def cli_env(db, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", db.name)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _as_role(env, db: Db, role: str) -> None:
    env.setenv("POSTGRES_USER", role)
    env.setenv("POSTGRES_PASSWORD", db.passwords[role])
    get_settings.cache_clear()


def test_cli_import_refuses_a_directory_holding_scenarios_json(
    db, imported, cli_env, tmp_path, capsys
):
    folder = tmp_path / "csv"
    folder.mkdir()
    for path in CSV_DIR.iterdir():
        (folder / path.name).write_bytes(path.read_bytes())
    (folder / "scenarios.json").write_bytes(SCENARIOS.read_bytes())
    _as_role(cli_env, db, db.importer_role)
    cli_env.setenv("RETURNS_IMPORTER_LABEL", "cli-test")
    counts = table_counts(db)
    assert cli.main(["import", str(folder)]) == cli.EXIT_INVALID
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "invalid" and "scenarios" not in json.dumps(out)
    assert table_counts(db) == counts


def test_cli_verify_matches_the_release_files_in_both_forms(db, imported, cli_env, capsys):
    _as_role(cli_env, db, db.app_role)
    counts = table_counts(db)
    for path in (BUNDLE, CSV_DIR):
        assert cli.main(["verify", str(path)]) == cli.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "match"
        assert out["stored_records_sha256"] == provenance()["records_sha256"]
    assert cli.main(["verify", str(BUNDLE.parent.parent / "scenarios.json")]) == cli.EXIT_INVALID
    capsys.readouterr()
    assert table_counts(db) == counts
