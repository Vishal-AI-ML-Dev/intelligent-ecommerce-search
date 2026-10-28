"""Secret-safe creation of throwaway LOGIN roles for Milestone 18 integration fixtures.

PostgreSQL cannot bind a password as a parameter of `CREATE ROLE`, so the SQL text must carry
either the password or a verifier. The plaintext password never enters SQL text here: the text
carries a SCRAM-SHA-256 verifier (RFC 5802/7677, the stored format, accepted pre-computed). The
verifier is treated as a secret too (it permits offline guessing and server impersonation), so:

* the statement goes through the raw DBAPI cursor, never SQLAlchemy, whose statement logging
  (`echo`, the `sqlalchemy.engine` logger) would otherwise record it;
* before it, the session turns off every server-side path that could record statement text
  (`log_statement`, `log_min_error_statement`, duration logging and sampling,
  `track_activities`, `pg_stat_statements.track_utility`), and `RESET ALL` follows. These are
  superuser settings; if any cannot be set, nothing is created;
* a failure becomes `RoleSetupError` carrying only the exception type and SQLSTATE, raised
  outside the `except` block, so no driver message (which may quote the statement) is chained.
"""

import base64
import contextlib
import hashlib
import hmac
import re
import secrets

from sqlalchemy import Connection

SCRAM_ITERATIONS = 4096  # PostgreSQL's default `scram_iterations`
_ROLE = re.compile(r"m18t_[a-z]+_[0-9a-f]{10}")
_PASSWORD = re.compile(r"[A-Za-z0-9_-]{16,128}")  # `secrets.token_urlsafe` output; SASLprep no-op
# Session settings that keep the statement out of the server log and statistics views.
SESSION_GUARDS = (
    "SET log_statement = 'none'",
    "SET log_min_error_statement = 'panic'",
    "SET log_min_duration_statement = -1",
    "SET log_min_duration_sample = -1",
    "SET log_transaction_sample_rate = 0",
    "SET track_activities = off",
    "SET pg_stat_statements.track_utility = off",
)


class RoleSetupError(RuntimeError):
    """Role creation failed; carries no SQL text, password or verifier."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def scram_sha256_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """`SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>` for `password`."""
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


def create_login_role(conn: Connection, role: str, password: str) -> None:
    """`CREATE ROLE <role> LOGIN ...` (on an AUTOCOMMIT superuser connection) without the
    password or its verifier reaching SQLAlchemy logs, server logs, errors or tracebacks."""
    if not _ROLE.fullmatch(role) or not _PASSWORD.fullmatch(password):
        raise RoleSetupError("refusing an unexpected role name or password form")
    failure: str | None = None
    cursor = None
    try:
        cursor = conn.connection.dbapi_connection.cursor()
        for guard in SESSION_GUARDS:
            cursor.execute(guard)
        cursor.execute(
            f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
            f"PASSWORD '{scram_sha256_verifier(password)}'"
        )
    except Exception as exc:  # noqa: BLE001 - re-raised below without its message
        failure = f"{type(exc).__name__} (sqlstate {getattr(exc, 'sqlstate', None)})"
    finally:
        if cursor is not None:
            with contextlib.suppress(Exception):  # an aborted session is discarded by the caller
                cursor.execute("RESET ALL")
            cursor.close()
    if failure is not None:
        raise RoleSetupError(f"creating a test role failed: {failure}")
