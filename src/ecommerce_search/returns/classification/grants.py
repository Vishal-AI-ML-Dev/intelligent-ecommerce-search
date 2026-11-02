"""`return-classification-grants-1`: privileges for the migration-0008 tables.

Applied by the database owner after `alembic upgrade head` and after the M18 grants
(`ecommerce_search.returns.grants`, unchanged):

    python -m ecommerce_search.returns.classification.grants \
        --database <db> --app-role <api role> --classifier-role <writer role>

* **Classifier (writer) role:** `SELECT` on exactly the M18 inputs it reads (dataset
  provenance, returns, order items, return comments, inspection observations, support
  complaints) and `SELECT, INSERT` on the eight new tables, plus their identity sequences.
  Nothing else: no UPDATE or DELETE anywhere, no raw file contents, no catalog table.
* **API role:** no privilege on the new tables in Part 2 (read access is a Part 4 decision).
  M18 and earlier grants are not touched.

`verify_classification_grants` asserts this after applying, inside the same transaction.
"""

import argparse
import re
import sys

from sqlalchemy import Connection, create_engine, text

from ecommerce_search.returns.classification.store import NEW_TABLES
from ecommerce_search.returns.grants import RAW_CONTENTS_TABLE, SCHEMA, GrantsError

GRANTS_VERSION = "return-classification-grants-1"
CLASSIFIER_READ_TABLES = (
    "return_datasets",
    "returns",
    "order_items",
    "return_comments",
    "inspection_observations",
    "support_complaints",
)
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_ALL = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"


def _ident(name: str) -> str:
    if _ROLE_NAME.fullmatch(name) is None:
        raise GrantsError(f"not a plain lower-case identifier: {name!r}")
    return f'"{name}"'


def _table(name: str) -> str:
    return f"{SCHEMA}.{_ident(name)}"


def _sequences(conn: Connection) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT s.relname FROM pg_depend d "
                "JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' "
                "JOIN pg_class t ON t.oid = d.refobjid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE d.deptype = 'i' AND n.nspname = :schema AND t.relname = ANY(:tables) "
                "ORDER BY s.relname"
            ),
            {"schema": SCHEMA, "tables": list(NEW_TABLES)},
        ).scalars()
    )


def grant_statements(conn: Connection, app_role: str, classifier_role: str) -> list[str]:
    app, writer = _ident(app_role), _ident(classifier_role)
    database = conn.execute(text("SELECT current_database()")).scalar_one()
    db = f'"{database.replace(chr(34), chr(34) * 2)}"'
    return [
        f"GRANT CONNECT ON DATABASE {db} TO {writer}",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {writer}",
        *(f"GRANT SELECT ON {_table(t)} TO {writer}" for t in CLASSIFIER_READ_TABLES),
        *(f"GRANT SELECT, INSERT ON {_table(t)} TO {writer}" for t in NEW_TABLES),
        *(f"GRANT USAGE ON SEQUENCE {_table(s)} TO {writer}" for s in _sequences(conn)),
        *(f"REVOKE ALL ON {_table(t)} FROM {app}, PUBLIC" for t in NEW_TABLES),
    ]


def _has(conn: Connection, role: str, table: str, privileges: str) -> bool:
    return conn.execute(
        text("SELECT has_table_privilege(:r, :t, :p)"),
        {"r": role, "t": f"{SCHEMA}.{table}", "p": privileges},
    ).scalar_one()


def verify_classification_grants(conn: Connection, app_role: str, classifier_role: str) -> None:
    """Raises `GrantsError` on the first violation."""
    tables = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p') ORDER BY c.relname"
        ),
        {"schema": SCHEMA},
    ).scalars()
    for table in tables:
        if table in NEW_TABLES:
            allowed = {"SELECT", "INSERT"}
        elif table in CLASSIFIER_READ_TABLES:
            allowed = {"SELECT"}
        else:
            allowed = set()
        for privilege in _ALL.split(", "):
            granted = _has(conn, classifier_role, table, privilege)
            if granted != (privilege in allowed):
                raise GrantsError(f"{classifier_role}: {privilege} on {table} is {granted}")
        if table in NEW_TABLES and _has(conn, app_role, table, _ALL):
            raise GrantsError(f"{app_role} has a privilege on {table}")
    if _has(conn, classifier_role, RAW_CONTENTS_TABLE, _ALL):
        raise GrantsError(f"{classifier_role} has a privilege on {RAW_CONTENTS_TABLE}")
    superuser, owns = conn.execute(
        text(
            "SELECT r.rolsuper, EXISTS (SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND pg_has_role(:r, c.relowner, 'USAGE')) "
            "FROM pg_roles r WHERE r.rolname = :r"
        ),
        {"schema": SCHEMA, "r": classifier_role},
    ).one()
    if superuser or owns:
        raise GrantsError(f"{classifier_role} is a superuser or a member of a table owner")


def apply_classification_grants(
    conn: Connection, *, app_role: str, classifier_role: str
) -> list[str]:
    """Apply and verify inside the caller's transaction. Returns the statements."""
    if app_role == classifier_role:
        raise GrantsError("the API role and the classifier role must differ")
    for role in (app_role, classifier_role):
        _ident(role)
        if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
            raise GrantsError(f"role {role!r} does not exist")
    missing = [
        t
        for t in (*CLASSIFIER_READ_TABLES, *NEW_TABLES)
        if conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"}).scalar() is None
    ]
    if missing:
        raise GrantsError(f"tables missing (run `alembic upgrade head` first): {missing}")
    statements = grant_statements(conn, app_role, classifier_role)
    for statement in statements:
        conn.execute(text(statement))
    verify_classification_grants(conn, app_role, classifier_role)
    return statements


def main(argv: list[str] | None = None) -> int:
    from ecommerce_search.config import get_settings

    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.returns.classification.grants"
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--app-role", required=True)
    parser.add_argument("--classifier-role", required=True)
    args = parser.parse_args(argv)
    engine = create_engine(get_settings().database_url(database=args.database))
    try:
        with engine.begin() as conn:
            statements = apply_classification_grants(
                conn, app_role=args.app_role, classifier_role=args.classifier_role
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
