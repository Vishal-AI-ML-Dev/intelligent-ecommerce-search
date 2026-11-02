"""M19 Part 2: classification and complaint-group storage on a disposable database.

Covers migration 0008 (upgrade, downgrade, and no change to any earlier table), the
`return-classification-grants-1` grants, append-only history, idempotent recording, a new run
for a new rules version, round-tripping stored results, M18 rows unchanged, and one recorded run
over the 180 synthetic returns. The run report gives counts of inferred outputs, not accuracy.

One throwaway `ecommerce_search_test_*` database per module: migrated to head, the repository
catalog ingested, the M18 release imported as the importer role, and three new LOGIN roles with
random names and generated passwords (never printed) created through `tests/returns_roles.py`.
Classification runs as the classifier role. The database and roles are dropped afterwards.
"""

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from returns_support import scratch
from sqlalchemy import Engine, create_engine, pool, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from ecommerce_search.config import get_settings
from ecommerce_search.ingestion.service import ingest_file
from ecommerce_search.models.return_classifications import RETURN_CLASSIFICATION_TABLES
from ecommerce_search.models.returns import RETURN_TABLES
from ecommerce_search.returns import grants as rg
from ecommerce_search.returns.classification import cli, store
from ecommerce_search.returns.classification import grants as cg
from ecommerce_search.returns.classification.baseline import classify
from ecommerce_search.returns.classification.contracts import RULES_VERSION
from ecommerce_search.returns.service import commit_import
from returns_release import ROOT, json_parts
from returns_roles import create_login_role

pytestmark = pytest.mark.integration

DATASET = ("synthetic-returns", 1)
SEED = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
SEED_PROVENANCE = ROOT / "data" / "seed" / "catalog_seed_v1.provenance.json"
CHECKED_AT = datetime(2026, 11, 2, tzinfo=UTC)


@dataclass
class Db:
    name: str
    owner: Engine
    importer: Engine
    app: Engine
    classifier: Engine
    roles: dict[str, str]  # kind -> role name
    passwords: dict[str, str] = field(repr=False)  # generated; never printed


def _engine(settings, database: str, role: str, password: str) -> Engine:
    url = settings.database_url(database=database).set(username=role, password=password)
    return create_engine(url, poolclass=pool.NullPool)


@pytest.fixture(scope="module")
def db(settings):
    name = scratch(f"ecommerce_search_test_{uuid.uuid4().hex[:12]}")
    suffix = uuid.uuid4().hex[:10]
    roles = {kind: f"m18t_cls{kind}_{suffix}" for kind in ("app", "imp", "wr")}
    passwords = {role: secrets.token_urlsafe(24) for role in roles.values()}
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    engines: list[Engine] = []
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            for role, password in passwords.items():
                create_login_role(conn, role, password)
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.attributes["database"] = name
        command.upgrade(config, "head")
        owner = create_engine(settings.database_url(database=name), poolclass=pool.NullPool)
        engines.append(owner)
        outcome = ingest_file(owner, SEED, SEED_PROVENANCE)
        assert outcome.result is not None and outcome.result.inserted == 240
        with owner.begin() as conn:
            rg.apply_grants(conn, app_role=roles["app"], importer_role=roles["imp"])
            cg.apply_classification_grants(conn, app_role=roles["app"], classifier_role=roles["wr"])
        importer, app, classifier = (
            _engine(settings, name, roles[k], passwords[roles[k]]) for k in ("imp", "app", "wr")
        )
        engines += [importer, app, classifier]
        report = commit_import(
            importer, json_parts(), imported_by_label="test-importer", checked_at=CHECKED_AT
        )
        assert report.status == "imported"
        yield Db(name, owner, importer, app, classifier, roles, passwords)
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in roles.values():
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def m18_state(db: Db) -> dict[str, tuple[int, str | None]]:
    """Row count and an ordered digest of every M18 table."""
    state = {}
    with db.owner.connect() as conn:
        for table in RETURN_TABLES:
            digest = "md5(string_agg(t::text, '|' ORDER BY t::text))"
            sql = f"SELECT count(*), {digest} FROM {table} t"  # noqa: S608 - fixed table names
            state[table] = conn.execute(text(sql)).one()
    return state


def new_table_counts(db: Db) -> dict[str, int]:
    with db.owner.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in RETURN_CLASSIFICATION_TABLES
        }


@pytest.fixture(scope="module")
def recorded(db):
    before = m18_state(db)
    report = store.record_classification_run(db.classifier, *DATASET)
    return before, report


# ---- grants --------------------------------------------------------------------------------


def test_classifier_and_api_roles_have_exactly_the_part_2_privileges(db):
    with db.owner.connect() as conn:
        cg.verify_classification_grants(conn, db.roles["app"], db.roles["wr"])
        rg.verify_grants(conn, db.roles["app"], db.roles["imp"])  # M18 grants still hold
    with db.classifier.connect() as conn, pytest.raises(ProgrammingError):
        conn.execute(text("UPDATE returns SET quantity = quantity WHERE false"))
    with db.classifier.connect() as conn, pytest.raises(ProgrammingError):
        conn.execute(text("SELECT 1 FROM return_import_file_contents LIMIT 1"))
    with db.classifier.connect() as conn, pytest.raises(ProgrammingError):
        conn.execute(text("SELECT 1 FROM products LIMIT 1"))
    for statement in (
        "SELECT 1 FROM return_classification_runs LIMIT 1",
        "INSERT INTO return_complaint_group_runs DEFAULT VALUES",
    ):
        with db.app.connect() as conn, pytest.raises(ProgrammingError):
            conn.execute(text(statement))


def test_only_the_classifier_role_may_record(db):
    for engine in (db.owner, db.importer, db.app):
        with pytest.raises(store.StoreError):
            store.record_classification_run(engine, *DATASET)


# ---- one run over the 180 synthetic returns -------------------------------------------------


def test_a_run_over_the_synthetic_returns_records_every_return(db, recorded):
    _, report = recorded
    assert report.status == "recorded"
    assert (report.contract_version, report.taxonomy_version, report.rules_version) == (
        "return-classification-1",
        "return-categories-1",
        RULES_VERSION,
    )
    assert report.tier_semantics == "rule_tier_not_probability"
    assert report.complaint_link_policy == "complaints-by-order-item-1"
    assert report.return_count == 180
    assert report.classified_count + report.abstained_count == 180
    assert "suspected_abuse" not in report.single_category_counts
    assert "insufficient_evidence" not in report.single_category_counts
    counts = new_table_counts(db)
    assert counts["return_classification_runs"] == 1
    assert counts["return_classifications"] == 180
    # Counts of inferred outputs on synthetic data; printed for the Part 2 report, not accuracy.
    print("\nM19-PART2-RUN-REPORT " + json.dumps(report.model_dump(mode="json"), sort_keys=True))


def test_recording_never_changes_m18_rows(db, recorded):
    before, _ = recorded
    assert m18_state(db) == before


def test_each_result_cites_its_source_records_and_round_trips(db, recorded):
    _, report = recorded
    with db.classifier.connect() as conn:
        pk = store.dataset_pk(conn, *DATASET)
        loaded = {entry.item.return_id: entry for entry in store.load_inputs(conn, pk)}
        rows = conn.execute(
            text("SELECT id, return_id FROM return_classifications WHERE run_id = :r"),
            {"r": report.run_id},
        ).all()
        assert len(rows) == len(loaded) == 180
        for cid, return_id in rows:
            entry = loaded[return_id]
            assert store.read_result(conn, cid) == classify(entry.item)
            stored = conn.execute(
                text(
                    "SELECT evidence_type, evidence_id, record_sha256 "
                    "FROM return_classification_inputs WHERE classification_id = :c "
                    "ORDER BY ordinal"
                ),
                {"c": cid},
            ).all()
            assert {(t, i): s for t, i, s in stored} == entry.record_sha256


def test_rerunning_the_same_versions_is_idempotent(db, recorded):
    _, first = recorded
    before = new_table_counts(db)
    again = store.record_classification_run(db.classifier, *DATASET)
    assert again.status == "already_recorded" and again.run_id == first.run_id
    assert again.model_copy(update={"status": "recorded"}) == first
    assert new_table_counts(db) == before


def test_history_is_append_only(db, recorded):
    for statement in (
        "UPDATE return_classifications SET needs_review = needs_review",
        "DELETE FROM return_category_predictions",
        "TRUNCATE return_classification_evidence CASCADE",
    ):
        with db.owner.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(statement))


def test_stored_predictions_can_never_name_abuse(db, recorded):
    with db.owner.begin() as conn:
        cid = conn.execute(text("SELECT min(id) FROM return_classifications")).scalar_one()
    with db.owner.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO return_category_predictions (classification_id, ordinal, "
                "resolution, category, candidates, tier, suggested_areas) VALUES (:c, 99, "
                "'single_category', 'suspected_abuse', ARRAY['suspected_abuse'], 'low', "
                "ARRAY['operations_investigation'])"
            ),
            {"c": cid},
        )


# ---- complaint groups ----------------------------------------------------------------------


def test_groups_keep_all_members_and_only_qualifying_members_make_patterns(db, recorded):
    _, run = recorded
    report = store.record_group_run(db.classifier, run.run_id)
    assert report.status == "recorded" and report.min_pattern_size == 3
    assert report.grouped_return_count + report.ungrouped_return_count == 180
    assert all(p.qualifying_member_count >= 3 for p in report.patterns)
    assert report.pattern_count == len(report.patterns)
    assert sum(report.non_pattern_size_counts.values()) == (
        report.group_count - report.pattern_count
    )
    with db.owner.connect() as conn:
        mismatched = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_groups g WHERE g.member_count <> "
                "(SELECT count(*) FROM return_complaint_group_members m WHERE m.group_id = g.id)"
            )
        ).scalar_one()
        abuse = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_groups "
                "WHERE 'suspected_abuse' = ANY(candidates)"
            )
        ).scalar_one()
        # the stored qualifying count equals the members flagged as counting, and no member
        # counts unless its prediction is medium/high, uncontradicted and conflict-free
        miscounted = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_groups g WHERE g.qualifying_member_count <> "
                "(SELECT count(*) FROM return_complaint_group_members m "
                "WHERE m.group_id = g.id AND m.counts_toward_pattern)"
            )
        ).scalar_one()
        wrongly_counted = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_group_members m "
                "JOIN return_category_predictions p ON p.classification_id = m.classification_id "
                "AND p.ordinal = m.prediction_ordinal "
                "JOIN return_classifications c ON c.id = m.classification_id "
                "WHERE m.counts_toward_pattern AND (p.tier = 'low' "
                "OR c.review_reasons && ARRAY['conflicting_statements', "
                "'statement_inspection_conflict', 'conflicting_inspections'] "
                "OR EXISTS (SELECT 1 FROM return_classification_evidence e "
                "WHERE e.classification_id = m.classification_id "
                "AND e.prediction_ordinal = m.prediction_ordinal AND e.kind = 'contradicting'))"
            )
        ).scalar_one()
        patterns_on_weak_members = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_groups g JOIN return_complaint_group_runs r "
                "ON r.id = g.group_run_id WHERE g.is_pattern <> "
                "(g.qualifying_member_count >= r.min_pattern_size)"
            )
        ).scalar_one()
        mixed_patterns = conn.execute(
            text(
                "SELECT count(*) FROM return_complaint_groups "
                "WHERE signature = 'sizing_fit:mixed' AND is_pattern"
            )
        ).scalar_one()
    assert (mismatched, abuse, miscounted, wrongly_counted) == (0, 0, 0, 0)
    assert (patterns_on_weak_members, mixed_patterns) == (0, 0)
    again = store.record_group_run(db.classifier, run.run_id)
    assert again.status == "already_recorded" and again.group_run_id == report.group_run_id
    print("\nM19-PART2-GROUP-REPORT " + json.dumps(report.model_dump(mode="json"), sort_keys=True))


# ---- read-back verifies its source records (P4) --------------------------------------------


def _first_classification_with(conn, evidence_type: str) -> int:
    return conn.execute(
        text(
            "SELECT min(classification_id) FROM return_classification_inputs "
            "WHERE evidence_type = :t"
        ),
        {"t": evidence_type},
    ).scalar_one()


def test_read_back_fails_safely_when_a_source_record_is_missing(db, recorded):
    # In a transaction that is rolled back: an input row naming a record that does not exist.
    with db.owner.connect() as conn, conn.begin() as tx:
        cid = _first_classification_with(conn, "return_comment")
        store.read_result(conn, cid)  # verifies before the change
        conn.execute(
            text(
                "INSERT INTO return_classification_inputs "
                "(classification_id, ordinal, evidence_type, evidence_id, record_sha256) "
                "VALUES (:c, 999, 'return_comment', 'NO-SUCH-COMMENT', :s)"
            ),
            {"c": cid, "s": "0" * 64},
        )
        with pytest.raises(store.SourceMismatchError, match="missing"):
            store.read_result(conn, cid)
        tx.rollback()


def test_read_back_fails_safely_when_a_source_checksum_differs(db, recorded):
    # A real comment of another return, recorded with a checksum that is not its own.
    with db.owner.connect() as conn, conn.begin() as tx:
        cid = _first_classification_with(conn, "return_comment")
        other = conn.execute(
            text(
                "SELECT rc.comment_id FROM return_comments rc WHERE rc.comment_id NOT IN "
                "(SELECT evidence_id FROM return_classification_inputs "
                "WHERE classification_id = :c) ORDER BY rc.comment_id LIMIT 1"
            ),
            {"c": cid},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO return_classification_inputs "
                "(classification_id, ordinal, evidence_type, evidence_id, record_sha256) "
                "VALUES (:c, 999, 'return_comment', :e, :s)"
            ),
            {"c": cid, "e": other, "s": "f" * 64},
        )
        with pytest.raises(store.SourceMismatchError, match="changed"):
            store.read_result(conn, cid)
        tx.rollback()


def test_read_back_refuses_evidence_outside_the_recorded_inputs(db, recorded):
    with db.owner.connect() as conn, conn.begin() as tx:
        cid = _first_classification_with(conn, "inspection_observation")
        conn.execute(
            text(
                "INSERT INTO return_classification_evidence (classification_id, ordinal, kind, "
                "effect, categories, source_kind, evidence_type, evidence_id, field, rule_id, "
                "match, value) VALUES (:c, 999, 'inspection_finding', 'neutral', ARRAY[]::text[], "
                "'inspection', 'inspection_observation', 'NOT-AN-INPUT', 'condition', "
                "'inspection:not_assessable', 'structured', 'not_assessable')"
            ),
            {"c": cid},
        )
        with pytest.raises(store.SourceMismatchError, match="outside"):
            store.read_result(conn, cid)
        tx.rollback()


def test_the_cli_records_as_the_classifier_role(db, recorded, monkeypatch, capsys):
    role = db.roles["wr"]
    monkeypatch.setenv("POSTGRES_DB", db.name)
    monkeypatch.setenv("POSTGRES_USER", role)
    monkeypatch.setenv("POSTGRES_PASSWORD", db.passwords[role])
    get_settings.cache_clear()
    try:
        code = cli.main(["record", "--dataset-id", DATASET[0], "--dataset-version", "1"])
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    out = json.loads(captured.out)
    assert out["classification"]["status"] == "already_recorded"
    assert out["groups"]["status"] == "already_recorded"
    assert db.passwords[role] not in captured.out + captured.err


def test_part_3_scoring_reads_the_stored_run_through_verified_read_back(
    db, recorded, monkeypatch, capsys, tmp_path
):
    """`python -m evals.returns score --run-id` against the stored run, with ONE TEST FIXTURE
    label row in `tmp_path` (not a human judgement; never the real label sheet)."""
    from evals.returns import cli as eval_cli
    from evals.returns.labels import COLUMNS

    from returns_release import ROOT as REPO

    _, run = recorded
    split_file = REPO / "evals" / "returns" / "labelling" / "return-reason-split-1.json"
    first_dev = sorted(json.loads(split_file.read_text(encoding="utf-8"))["sets"]["dev"])[0]
    cells = dict.fromkeys(COLUMNS, "") | {
        "item": "1",
        "return_id": first_dev,
        "insufficient_evidence": "1",
        "annotator": "fixture",
    }
    sheet = tmp_path / "fixture-labels.csv"
    sheet.write_text(",".join(COLUMNS) + "\n" + ",".join(cells.values()) + "\n")
    role = db.roles["wr"]
    monkeypatch.setenv("POSTGRES_DB", db.name)
    monkeypatch.setenv("POSTGRES_USER", role)
    monkeypatch.setenv("POSTGRES_PASSWORD", db.passwords[role])
    get_settings.cache_clear()
    try:
        code = eval_cli.main(
            ["score", "--split", str(split_file), "--set", "dev", "--labels", str(sheet)]
            + ["--run-id", str(run.run_id)]
        )
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert code == 0, captured.err
    report = json.loads(captured.out)
    assert report["run"]["source"] == "stored_run" and report["run"]["run_id"] == run.run_id
    assert report["coverage"]["numerator"] == 1 and report["coverage"]["denominator"] == 60
    assert db.passwords[role] not in captured.out + captured.err


# ---- versions (last: adds a second run) ----------------------------------------------------


def test_a_new_rules_version_creates_a_new_run(db, recorded, monkeypatch):
    _, first = recorded
    monkeypatch.setattr(store, "RULES_VERSION", "return-baseline-rules-test-2")
    second = store.record_classification_run(db.classifier, *DATASET)
    assert second.status == "recorded" and second.run_id != first.run_id
    assert second.rules_version == "return-baseline-rules-test-2"
    assert second.return_count == first.return_count == 180
    with db.owner.connect() as conn:
        per_run = dict(
            conn.execute(
                text("SELECT run_id, count(*) FROM return_classifications GROUP BY run_id")
            ).all()
        )
    assert per_run == {first.run_id: 180, second.run_id: 180}


# ---- migration 0008 ------------------------------------------------------------------------


def _snapshot(engine: Engine) -> dict[str, object]:
    with engine.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
            )
        ).all()
        constraints = conn.execute(
            text(
                "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
            )
        ).all()
        triggers = conn.execute(
            text(
                "SELECT tgrelid::regclass::text, tgname FROM pg_trigger "
                "WHERE NOT tgisinternal ORDER BY 1, 2"
            )
        ).all()
        functions = (
            conn.execute(
                text(
                    "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
                    "ORDER BY 1"
                )
            )
            .scalars()
            .all()
        )
    return {
        "columns": columns,
        "constraints": constraints,
        "triggers": triggers,
        "functions": functions,
    }


def test_0008_upgrades_and_downgrades_without_touching_earlier_tables(
    settings, scratch_database, migrator
):
    engine = create_engine(settings.database_url(database=scratch_database))
    try:
        migrator.upgrade(scratch_database, "0007")
        at_0007 = _snapshot(engine)
        migrator.upgrade(scratch_database)
        at_head = _snapshot(engine)
        new_tables = {row[0] for row in at_head["columns"]} - {row[0] for row in at_0007["columns"]}
        assert new_tables == set(RETURN_CLASSIFICATION_TABLES)
        for key in ("columns", "constraints", "triggers"):
            old = [row for row in at_head[key] if row[0] not in new_tables]
            assert old == at_0007[key], key  # no earlier table changed
        assert set(at_head["functions"]) - set(at_0007["functions"]) == {
            "return_inference_append_only"
        }
        migrator.downgrade(scratch_database, "0007")
        assert _snapshot(engine) == at_0007
        migrator.upgrade(scratch_database)
        assert _snapshot(engine) == at_head
    finally:
        engine.dispose()
