"""AC-03 / AC-13 (3): the M18 grants script, tested with real per-test LOGIN roles.

Each test gets a throwaway `ecommerce_search_test_*` database and two new roles with random
names and generated passwords (never printed). The real `search_app` / `returns_importer` roles
are never created or changed. Roles are dropped after each test (`DROP OWNED` inside the
scratch database, then `DROP ROLE`). All records are SYNTHETIC.
"""

import re
import secrets
import uuid
from dataclasses import dataclass

import pytest
from returns_support import Linked, add_catalog, scratch
from sqlalchemy import create_engine, pool, text
from sqlalchemy.exc import ProgrammingError

from ecommerce_search.returns import grants as rg
from returns_contract_sets import M18_TABLES

pytestmark = pytest.mark.integration

RAW = "return_import_file_contents"
READABLE = M18_TABLES - {RAW}
ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@dataclass
class Roles:
    database: str
    app: str
    importer: str
    passwords: dict[str, str]
    owner: object  # the schema-owner engine (the test superuser)
    settings: object

    def engine(self, role: str):
        url = self.settings.database_url(database=self.database).set(
            username=role, password=self.passwords[role]
        )
        return create_engine(url, poolclass=pool.NullPool)

    def apply(self) -> list[str]:
        with self.owner.begin() as conn:
            return rg.apply_grants(conn, app_role=self.app, importer_role=self.importer)


@pytest.fixture
def roles(settings, scratch_database):
    scratch(scratch_database)
    suffix = uuid.uuid4().hex[:10]
    app, importer = f"m18t_app_{suffix}", f"m18t_imp_{suffix}"
    passwords = {role: secrets.token_urlsafe(24) for role in (app, importer)}
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    owner = create_engine(settings.database_url(database=scratch_database))
    with admin.connect() as conn:
        for role, password in passwords.items():
            assert re.fullmatch(r"[A-Za-z0-9_-]+", password)  # safe to inline; never printed
            conn.execute(
                text(
                    f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                    f"NOINHERIT PASSWORD '{password}'"
                )
            )
    try:
        yield Roles(scratch_database, app, importer, passwords, owner, settings)
    finally:
        owner.dispose()
        cleanup = create_engine(
            settings.database_url(database=scratch_database), isolation_level="AUTOCOMMIT"
        )
        with cleanup.connect() as conn:
            for role in (app, importer):
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = :r"
                    ),
                    {"r": role},
                )
                conn.execute(text(f'REASSIGN OWNED BY "{role}" TO CURRENT_USER'))
                conn.execute(text(f'DROP OWNED BY "{role}"'))
        cleanup.dispose()
        with admin.connect() as conn:
            for role in (app, importer):
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


@pytest.fixture
def at_head(roles, migrator):
    """The scratch database at head with a catalog and one complete synthetic dataset."""
    migrator.upgrade(roles.database)
    with roles.owner.begin() as conn:
        add_catalog(conn)
        Linked(conn).add_all()
    return roles


def privileges(conn, role: str) -> set[tuple[str, str, str]]:
    """Every explicit privilege of `role` in the public schema: (relation, column, privilege)."""
    params = {"r": role}
    tables = conn.execute(
        text(
            "SELECT c.relname, '', a.privilege_type FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
            "WHERE n.nspname = 'public' AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = :r)"
        ),
        params,
    ).all()
    columns = conn.execute(
        text(
            "SELECT c.relname, att.attname, a.privilege_type FROM pg_attribute att "
            "JOIN pg_class c ON c.oid = att.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(att.attacl) a "
            "WHERE n.nspname = 'public' AND att.attnum > 0 "
            "AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = :r)"
        ),
        params,
    ).all()
    return {tuple(row) for row in (*tables, *columns)}


def effective(conn, role: str, tables) -> dict[tuple[str, str], bool]:
    return {
        (t, p): conn.execute(
            text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": f"public.{t}", "p": p}
        ).scalar_one()
        for t in tables
        for p in ALL_PRIVILEGES
    }


def denied(engine, sql: str) -> None:
    with pytest.raises(ProgrammingError) as excinfo, engine.begin() as conn:
        conn.execute(text(sql))
    assert excinfo.value.orig.sqlstate == "42501", str(excinfo.value.orig)  # insufficient_privilege


def public_tables(conn) -> list[str]:
    return list(
        conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        ).scalars()
    )


def legacy_setup_grants(conn, role: str, database: str) -> None:
    """What `infra/staging/setup.sh` and `deploy/ec2/restore.sh` grant today (pre-M18)."""
    conn.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"'))
    conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))


# ---- the API role ---------------------------------------------------------------------------


def test_the_api_role_reads_metadata_and_records_but_never_raw_contents(at_head):
    at_head.apply()
    app = at_head.engine(at_head.app)
    try:
        with app.connect() as conn:
            for table in sorted(READABLE | set(rg.PRE_M18_TABLES)):
                conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
            assert conn.execute(text("SELECT count(*) FROM return_import_files")).scalar_one() == 1
        denied(app, f"SELECT raw_bytes FROM {RAW}")  # noqa: S608
        denied(app, f"SELECT count(*) FROM {RAW}")  # noqa: S608
        denied(app, f"SELECT file_pk, raw_sha256 FROM {RAW}")  # noqa: S608
    finally:
        app.dispose()


def test_the_api_role_can_write_nothing(at_head):
    at_head.apply()
    with at_head.owner.connect() as conn:
        tables = public_tables(conn)
        rights = effective(conn, at_head.app, tables)
        column_writes = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND ("
                "has_any_column_privilege(:r, c.oid, 'INSERT') OR "
                "has_any_column_privilege(:r, c.oid, 'UPDATE'))"
            ),
            {"r": at_head.app},
        ).scalar_one()
    assert not [k for k, v in rights.items() if v and k[1] != "SELECT"]
    assert column_writes == 0
    app = at_head.engine(at_head.app)
    try:
        denied(app, "INSERT INTO return_datasets (dataset_id) VALUES ('x')")
        denied(app, "DELETE FROM orders")
    finally:
        app.dispose()


def test_the_api_role_has_exactly_the_planned_privileges(at_head):
    at_head.apply()
    with at_head.owner.connect() as conn:
        found = privileges(conn, at_head.app)
    expected = {(t, "", "SELECT") for t in (*rg.PRE_M18_TABLES, *READABLE)}
    assert found == expected


# ---- the importer role ---------------------------------------------------------------------


def test_the_importer_has_exactly_the_planned_privileges(at_head):
    at_head.apply()
    with at_head.owner.connect() as conn:
        found = privileges(conn, at_head.importer)
        sequences = set(
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'S'"
                )
            ).scalars()
        )
    expected = {(t, "", p) for t in READABLE for p in ("SELECT", "INSERT")}
    expected.add((RAW, "", "INSERT"))
    for table, columns in rg.CATALOG_REFERENCE_COLUMNS.items():
        expected |= {(table, c, "SELECT") for c in columns}
    m18_sequences = {s for s in sequences if any(s.startswith(f"{t}_") for t in M18_TABLES)}
    assert len(m18_sequences) == len(M18_TABLES) - 1  # every table but the contents has one
    expected |= {(s, "", "USAGE") for s in m18_sequences}
    assert found == expected


def test_the_importer_writes_a_complete_dataset_with_its_grants_only(at_head):
    at_head.apply()
    importer = at_head.engine(at_head.importer)
    try:
        with importer.begin() as conn:
            Linked(conn, version=2).add_all()  # incl. raw contents, without RETURNING
        with importer.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM return_datasets WHERE dataset_version = 2")
                ).scalar_one()
                == 1
            )
    finally:
        importer.dispose()


def test_the_importer_cannot_read_raw_contents_or_unlisted_catalog_columns(at_head):
    at_head.apply()
    importer = at_head.engine(at_head.importer)
    try:
        with importer.connect() as conn:
            conn.execute(text("SELECT product_id FROM products")).all()
            conn.execute(text("SELECT dataset_pk, source_key, raw_sha256 FROM raw_catalog_records"))
            conn.execute(text("SELECT id, dataset_id, dataset_version FROM catalog_datasets"))
        for sql in (
            f"SELECT raw_bytes FROM {RAW}",  # noqa: S608
            f"SELECT count(*) FROM {RAW}",  # noqa: S608
            "SELECT price FROM products",
            "SELECT * FROM products",
            "SELECT raw_line FROM raw_catalog_records",
            "SELECT rights_note FROM catalog_datasets",
            "SELECT count(*) FROM product_search_documents",
            "SELECT count(*) FROM product_embeddings",
            "SELECT count(*) FROM catalog_reviews",
            "SELECT count(*) FROM listing_versions",
            "SELECT count(*) FROM listing_decision_runs",
        ):
            denied(importer, sql)
    finally:
        importer.dispose()


def test_the_importer_cannot_update_delete_or_write_other_tables(at_head):
    at_head.apply()
    with at_head.owner.connect() as conn:
        tables = public_tables(conn)
        rights = effective(conn, at_head.importer, tables)
    granted = {k for k, v in rights.items() if v}
    assert not {k for k in granted if k[1] in ("UPDATE", "DELETE", "TRUNCATE", "TRIGGER")}
    assert {t for t, p in granted if p == "INSERT"} == M18_TABLES
    assert {t for t, p in granted if p == "SELECT"} == READABLE
    importer = at_head.engine(at_head.importer)
    try:
        denied(importer, "UPDATE orders SET currency = 'INR'")  # refused before the trigger
        denied(importer, "DELETE FROM returns")
        denied(importer, "TRUNCATE return_datasets CASCADE")
        denied(importer, "UPDATE products SET price = 1")
        denied(
            importer,
            "INSERT INTO catalog_reviews (review_batch_id) VALUES ('x')",
        )
    finally:
        importer.dispose()


# ---- preservation, raw-file revoke, idempotency and assertions -----------------------------


def test_the_api_role_keeps_every_pre_m18_privilege(roles, migrator):
    migrator.upgrade(roles.database, "0006")
    with roles.owner.begin() as conn:
        legacy_setup_grants(conn, roles.app, roles.database)
        # An unrelated non-SELECT privilege on an M14 table must survive too (Q3).
        conn.execute(text(f'GRANT REFERENCES ON listing_decision_runs TO "{roles.app}"'))
    with roles.owner.connect() as conn:
        tables = public_tables(conn)
        assert set(tables) == set(rg.PRE_M18_TABLES)
        before = privileges(conn, roles.app), effective(conn, roles.app, tables)
    migrator.upgrade(roles.database)
    roles.apply()
    with roles.owner.connect() as conn:
        after_privileges = {p for p in privileges(conn, roles.app) if p[0] in tables}
        after = after_privileges, effective(conn, roles.app, tables)
        database_rights = conn.execute(
            text(
                "SELECT has_database_privilege(:r, current_database(), 'CONNECT'), "
                "has_schema_privilege(:r, 'public', 'USAGE')"
            ),
            {"r": roles.app},
        ).one()
    assert after == before
    assert tuple(database_rights) == (True, True)


def test_the_script_removes_legacy_blanket_and_column_access_to_raw_contents(at_head):
    with at_head.owner.begin() as conn:
        legacy_setup_grants(conn, at_head.app, at_head.database)  # e.g. restore.sh after 0007
        conn.execute(text(f'GRANT SELECT (raw_bytes) ON {RAW} TO "{at_head.app}"'))
        conn.execute(text(f"GRANT SELECT ON {RAW} TO PUBLIC"))
    app = at_head.engine(at_head.app)
    try:
        with app.connect() as conn:  # the hazard the script exists for
            assert conn.execute(text(f"SELECT count(*) FROM {RAW}")).scalar_one() == 1  # noqa: S608
        at_head.apply()
        denied(app, f"SELECT raw_bytes FROM {RAW}")  # noqa: S608
        with app.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM returns")).scalar_one() == 1
    finally:
        app.dispose()
    with at_head.owner.connect() as conn:
        assert not [p for p in privileges(conn, at_head.app) if p[0] == RAW]
        public = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a "
                "WHERE c.relname = :t AND a.grantee = 0"
            ),
            {"t": RAW},
        ).scalar_one()
    assert public == 0


def test_the_script_is_idempotent(at_head):
    first = at_head.apply()
    with at_head.owner.connect() as conn:
        snapshot = privileges(conn, at_head.app), privileges(conn, at_head.importer)
    assert at_head.apply() == first
    with at_head.owner.connect() as conn:
        assert (privileges(conn, at_head.app), privileges(conn, at_head.importer)) == snapshot


def test_the_script_creates_no_default_privileges_or_ownership(at_head):
    at_head.apply()
    with at_head.owner.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_default_acl")).scalar_one() == 0
        owners = set(
            conn.execute(
                text(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                )
            ).scalars()
        )
    assert not owners & {at_head.app, at_head.importer}


def _refused(roles, match: str) -> None:
    with pytest.raises(rg.GrantsError, match=match):
        roles.apply()
    with roles.owner.connect() as conn:  # the failed run changed nothing
        assert privileges(conn, roles.app) == set()


def test_the_script_refuses_a_database_without_the_m18_tables(roles, migrator):
    migrator.upgrade(roles.database, "0006")
    _refused(roles, "tables missing")


def test_the_script_refuses_default_privileges(at_head):
    with at_head.owner.begin() as conn:
        conn.execute(
            text(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{at_head.importer}" IN SCHEMA public '
                f'GRANT SELECT ON TABLES TO "{at_head.app}"'
            )
        )
    _refused(at_head, "default privileges")


def test_the_script_refuses_a_role_that_owns_an_m18_table(at_head):
    with at_head.owner.begin() as conn:
        conn.execute(text(f'ALTER TABLE orders OWNER TO "{at_head.importer}"'))
    _refused(at_head, "owns M18 tables")


def test_the_script_refuses_bad_role_arguments(at_head):
    with pytest.raises(rg.GrantsError, match="must differ"), at_head.owner.begin() as conn:
        rg.apply_grants(conn, app_role=at_head.app, importer_role=at_head.app)
    with pytest.raises(rg.GrantsError, match="does not exist"), at_head.owner.begin() as conn:
        rg.apply_grants(conn, app_role=at_head.app, importer_role="m18t_missing_role")
    with pytest.raises(rg.GrantsError, match="identifier"), at_head.owner.begin() as conn:
        rg.apply_grants(conn, app_role='x"; DROP TABLE orders; --', importer_role=at_head.app)


def test_the_cli_applies_the_grants(at_head, capsys):
    argv = ["--database", at_head.database, "--app-role", at_head.app]
    assert rg.main([*argv, "--importer-role", at_head.importer]) == 0
    out = capsys.readouterr()
    assert "grants: applied returns-grants-1" in out.out
    assert all(p not in out.out + out.err for p in at_head.passwords.values())
    assert rg.main([*argv, "--importer-role", "m18t_missing_role"]) == 1
    assert "does not exist" in capsys.readouterr().err
