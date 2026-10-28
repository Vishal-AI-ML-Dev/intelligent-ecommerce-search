# M26A one-off staging setup, run inside the application image by the `setup` ECS task as the
# RDS master user, and by `setup-db.sh` / `m18-release.sh` on the single-EC2 stack. Writes to the
# staging database: migrations, the committed synthetic seed, embeddings; then the read-only
# checks, the database roles and the M18 grants script (docs/m18/release.md).
#   sh setup.sh            full setup of an EMPTY database (as before)
#   sh setup.sh --release  migrations, roles and grants only (M18 release on an existing database)
set -eu
alembic upgrade head
if [ "${1:-}" != "--release" ]; then
  python -m ecommerce_search.catalog ingest --database "$POSTGRES_DB"
  python -m ecommerce_search.search embed --database "$POSTGRES_DB"
  python -m ecommerce_search.search status --database "$POSTGRES_DB" --require-current
  python -m ecommerce_search.search embed-status --database "$POSTGRES_DB" --require-current
fi
python - <<'PY'
# Roles without a plaintext password in any SQL text: the password is turned into a SCRAM-SHA-256
# verifier here (RFC 5802/7677, the stored format PostgreSQL accepts pre-computed), and the
# verifier itself is kept out of server logs and statistics by session settings applied first.
# If those settings cannot be applied, no role is created or changed. Errors carry only the
# exception type and SQLSTATE, never the statement.
import base64
import hashlib
import hmac
import os
import secrets
import sys

import psycopg
from psycopg import sql

GUARDS = (
    "SET log_statement = 'none'",
    "SET log_min_error_statement = 'panic'",
    "SET log_min_duration_statement = -1",
    "SET log_min_duration_sample = -1",
    "SET log_transaction_sample_rate = 0",
    "SET track_activities = off",
    "SET pg_stat_statements.track_utility = off",
)


def verifier(password: str) -> str:
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 4096)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda data: base64.b64encode(data).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256$4096:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def fail(step: str, exc: Exception) -> None:
    print(f"setup: {step} failed: {type(exc).__name__} (sqlstate {getattr(exc, 'sqlstate', None)})",
          file=sys.stderr)
    sys.exit(1)


def role_statement(cur, name: str, password: str, attributes: str):
    exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    if exists and not password:
        return None  # an existing role is never altered without a new password
    verb = "ALTER ROLE" if exists else "CREATE ROLE"
    if password:
        login = sql.SQL("LOGIN PASSWORD {}").format(sql.Literal(verifier(password)))
    else:
        login = sql.SQL("NOLOGIN")
    extra = sql.SQL("" if exists else attributes)
    return sql.SQL("{} {} {} {}").format(sql.SQL(verb), sql.Identifier(name), login, extra)


db = os.environ["POSTGRES_DB"]
app_user, app_password = os.environ["APP_DB_USER"], os.environ["APP_DB_PASSWORD"]
importer_user = os.environ.get("IMPORTER_DB_USER", "returns_importer")
importer_password = os.environ.get("IMPORTER_DB_PASSWORD", "")
if not app_password:
    print("setup: APP_DB_PASSWORD is empty", file=sys.stderr)
    sys.exit(1)
with psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=int(os.environ["POSTGRES_PORT"]),
    dbname=db,
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    # RDS (managed stack) requires TLS; the single-EC2 Compose network sets SETUP_DB_SSLMODE=disable.
    sslmode=os.environ.get("SETUP_DB_SSLMODE", "require"),
    autocommit=True,
) as conn:
    with conn.cursor() as cur:
        try:
            for guard in GUARDS:
                cur.execute(guard)
        except psycopg.Error as exc:
            fail("log guards (no role was changed)", exc)
        try:
            # The API role keeps its pre-M18 behaviour: created, or its password re-set.
            app = role_statement(cur, app_user, app_password, "")
            # The importer is created once; NOLOGIN until a password is supplied.
            importer = role_statement(
                cur, importer_user, importer_password, "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            )
            for statement in (app, importer):
                if statement is not None:
                    cur.execute(statement)
        except psycopg.Error as exc:
            fail("role setup", exc)
        finally:
            cur.execute("RESET ALL")
print("setup: roles ready (passwords sent as SCRAM verifiers only)")
PY
# Explicit, versioned grants (contracts §2.1) instead of a blanket SELECT on every table: the API
# role keeps its pre-M18 reads and never reads raw return files.
python -m ecommerce_search.returns.grants --database "$POSTGRES_DB" \
  --app-role "$APP_DB_USER" --importer-role "${IMPORTER_DB_USER:-returns_importer}"
