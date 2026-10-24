"""Versioned, idempotent grants for the return data tables (contracts §2.1, D2 with Q3).

Run as the schema owner after every migration and every restore:

    python -m ecommerce_search.returns.grants --database NAME --app-role R --importer-role R

The role names are parameters, so tests can use real per-test LOGIN roles. The roles must
already exist; this script never creates roles, sets passwords or changes role attributes. In
one transaction it:

1. preserves the API role's existing access: CONNECT, schema USAGE and SELECT on an
   enumerated list of every pre-M18 table (migrations 0001–0006, the tables the blanket
   `GRANT SELECT ON ALL TABLES` reached). It revokes nothing on those tables;
2. grants the API role SELECT on the M18 metadata and normalized tables;
3. revokes everything on `return_import_file_contents` from the API role and PUBLIC;
4. grants the importer role exactly SELECT + INSERT on the M18 metadata and normalized tables,
   INSERT only on the raw contents, column-level SELECT on the catalog reference columns, and
   USAGE on the M18 identity sequences;
5. fails loudly (and rolls back) unless the API role has no privilege on the raw contents, the
   importer has only INSERT there, and neither role is a superuser, a member of the table
   owner, a table owner, or a holder of default privileges.

Wiring this into the deploy kit (`setup.sh`, `restore.sh`) is M18-07 work.
"""

import argparse
import re
import sys

from sqlalchemy import Connection, create_engine, text

GRANTS_VERSION = "returns-grants-1"
SCHEMA = "public"
PRE_M18_TABLES: tuple[str, ...] = (
    "alembic_version",
    # 0002 catalog
    "catalog_datasets",
    "raw_catalog_records",
    "products",
    "laptop_specs",
    "phone_specs",
    "shoe_specs",
    "headphone_specs",
    "catalog_reviews",
    # 0003 / 0004 derived search tables
    "product_search_documents",
    "product_embeddings",
    # 0005 M13 listing history
    "listing_versions",
    "listing_normalization_runs",
    "listing_candidates",
    "listing_findings",
    # 0006 M14 listing decisions
    "listing_decision_runs",
    "listing_decision_proposals",
    "listing_decision_probabilities",
    "listing_decision_reasons",
)
RAW_CONTENTS_TABLE = "return_import_file_contents"
M18_READABLE_TABLES: tuple[str, ...] = (
    "return_datasets",
    "return_import_batches",
    "return_import_files",
    "orders",
    "order_items",
    "shipments",
    "delivery_events",
    "returns",
    "return_comments",
    "inspection_observations",
    "customer_reviews",
    "support_complaints",
    "correction_cases",
    "correction_case_evidence",
)
M18_TABLES = (*M18_READABLE_TABLES, RAW_CONTENTS_TABLE)
CATALOG_REFERENCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "products": ("product_id",),
    "raw_catalog_records": ("dataset_pk", "source_key", "raw_sha256"),
    "catalog_datasets": ("id", "dataset_id", "dataset_version"),
}
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class GrantsError(RuntimeError):
    pass


def _ident(name: str) -> str:
    if _ROLE_NAME.fullmatch(name) is None:
        raise GrantsError(f"not a plain lower-case identifier: {name!r}")
    return f'"{name}"'


def _table(name: str) -> str:
    return f"{SCHEMA}.{_ident(name)}"


def _require_objects(conn: Connection, app: str, importer: str) -> None:
    for role in (app, importer):
        if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
            continue
        raise GrantsError(f"role {role!r} does not exist")
    missing = [
        t
        for t in (*PRE_M18_TABLES, *M18_TABLES)
        if conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"}).scalar() is None
    ]
    if missing:
        raise GrantsError(f"tables missing (run `alembic upgrade head` first): {missing}")


def _identity_sequences(conn: Connection) -> list[str]:
    names = conn.execute(
        text(
            "SELECT s.relname FROM pg_depend d "
            "JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' "
            "JOIN pg_class t ON t.oid = d.refobjid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE d.deptype = 'i' AND n.nspname = :schema AND t.relname = ANY(:tables) "
            "ORDER BY s.relname"
        ),
        {"schema": SCHEMA, "tables": list(M18_TABLES)},
    ).scalars()
    return list(names)


def grant_statements(conn: Connection, app_role: str, importer_role: str) -> list[str]:
    app, importer = _ident(app_role), _ident(importer_role)
    database = conn.execute(text("SELECT current_database()")).scalar_one()
    db = f'"{database.replace(chr(34), chr(34) * 2)}"'
    statements = [
        f"GRANT CONNECT ON DATABASE {db} TO {app}, {importer}",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {app}, {importer}",
        # 1. preserve the API role's pre-M18 read access, explicitly
        *(f"GRANT SELECT ON {_table(t)} TO {app}" for t in PRE_M18_TABLES),
        # 2. API: metadata and normalized tables
        *(f"GRANT SELECT ON {_table(t)} TO {app}" for t in M18_READABLE_TABLES),
        # 3. raw contents: nobody but the owner reads them
        f"REVOKE ALL ON {_table(RAW_CONTENTS_TABLE)} FROM {app}, PUBLIC",
        # 4. importer: exactly the §2 privileges
        *(f"GRANT SELECT, INSERT ON {_table(t)} TO {importer}" for t in M18_READABLE_TABLES),
        f"GRANT INSERT ON {_table(RAW_CONTENTS_TABLE)} TO {importer}",
        *(
            f"GRANT SELECT ({', '.join(_ident(c) for c in columns)}) "
            f"ON {_table(table)} TO {importer}"
            for table, columns in CATALOG_REFERENCE_COLUMNS.items()
        ),
    ]
    statements += [
        f"GRANT USAGE ON SEQUENCE {_table(s)} TO {importer}" for s in _identity_sequences(conn)
    ]
    return statements


def _table_privileges(conn: Connection, role: str, table: str) -> set[str]:
    """Explicit table-level privileges of `role` (or PUBLIC when `role` is empty)."""
    rows = conn.execute(
        text(
            "SELECT a.privilege_type FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
            "WHERE n.nspname = :schema AND c.relname = :table AND a.grantee = "
            "CASE WHEN :role = '' THEN 0 ELSE (SELECT oid FROM pg_roles WHERE rolname = :role) END"
        ),
        {"schema": SCHEMA, "table": table, "role": role},
    ).scalars()
    return set(rows)


def _column_privileges(conn: Connection, role: str, table: str) -> set[tuple[str, str]]:
    rows = conn.execute(
        text(
            "SELECT att.attname, a.privilege_type FROM pg_attribute att "
            "JOIN pg_class c ON c.oid = att.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(att.attacl) a "
            "WHERE n.nspname = :schema AND c.relname = :table AND att.attnum > 0 "
            "AND a.grantee = CASE WHEN :role = '' THEN 0 "
            "ELSE (SELECT oid FROM pg_roles WHERE rolname = :role) END"
        ),
        {"schema": SCHEMA, "table": table, "role": role},
    ).all()
    return {(name, privilege) for name, privilege in rows}


def verify_grants(conn: Connection, app_role: str, importer_role: str) -> None:
    """The step-5 assertions. Raises `GrantsError` on the first violation."""
    raw = RAW_CONTENTS_TABLE
    for role in (app_role, ""):
        if _table_privileges(conn, role, raw) or _column_privileges(conn, role, raw):
            raise GrantsError(f"{role or 'PUBLIC'} has a privilege on {raw}")
    if conn.execute(
        text("SELECT has_table_privilege(:r, :t, 'SELECT')"),
        {"r": app_role, "t": f"{SCHEMA}.{raw}"},
    ).scalar_one():
        raise GrantsError(f"{app_role} can read {raw}")
    if _table_privileges(conn, importer_role, raw) != {"INSERT"} or _column_privileges(
        conn, importer_role, raw
    ):
        raise GrantsError(f"{importer_role} must have INSERT only on {raw}")
    for role in (app_role, importer_role):
        if conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = :r"), {"r": role}
        ).scalar_one():
            raise GrantsError(f"{role} is a superuser")
        owners = conn.execute(
            text(
                "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = ANY(:tables)"
            ),
            {"schema": SCHEMA, "tables": list(M18_TABLES)},
        ).scalars()
        for owner in owners:
            if owner == role:
                raise GrantsError(f"{role} owns M18 tables")
            if conn.execute(
                text("SELECT pg_has_role(:r, :o, 'USAGE')"), {"r": role, "o": owner}
            ).scalar_one():
                raise GrantsError(f"{role} is a member of the table owner {owner}")
        default_acl = conn.execute(
            text(
                "SELECT count(*) FROM pg_default_acl d "
                "LEFT JOIN LATERAL aclexplode(d.defaclacl) a ON true "
                "WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = :r) "
                "OR a.grantee = (SELECT oid FROM pg_roles WHERE rolname = :r)"
            ),
            {"r": role},
        ).scalar_one()
        if default_acl:
            raise GrantsError(f"{role} has default privileges")


def apply_grants(conn: Connection, *, app_role: str, importer_role: str) -> list[str]:
    """Apply and verify the grants inside the caller's transaction. Returns the statements."""
    if app_role == importer_role:
        raise GrantsError("the API role and the importer role must differ")
    for role in (app_role, importer_role):
        _ident(role)
    _require_objects(conn, app_role, importer_role)
    statements = grant_statements(conn, app_role, importer_role)
    for statement in statements:
        conn.execute(text(statement))
    verify_grants(conn, app_role, importer_role)
    return statements


def main(argv: list[str] | None = None) -> int:
    from ecommerce_search.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m ecommerce_search.returns.grants")
    parser.add_argument("--database", required=True)
    parser.add_argument("--app-role", required=True)
    parser.add_argument("--importer-role", required=True)
    args = parser.parse_args(argv)
    engine = create_engine(get_settings().database_url(database=args.database))
    try:
        with engine.begin() as conn:
            statements = apply_grants(
                conn, app_role=args.app_role, importer_role=args.importer_role
            )
    except GrantsError as exc:
        print(f"grants: FAILED ({GRANTS_VERSION}): {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"grants: applied {GRANTS_VERSION} ({len(statements)} statements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
