"""The integration fixtures' secret-safe role creation (`tests/returns_roles.py`).

Neither the synthetic password nor its SCRAM verifier (nor the verifier's salt, StoredKey or
ServerKey) may reach SQLAlchemy logs, any other log record, stdout/stderr, the exception message,
its `repr` or the rendered traceback. Secrets are generated per run and never printed: each check
computes a boolean first and asserts it with a fixed message. SQLAlchemy's logging path is driven
on an in-memory SQLite engine (no server, no file); driver failures are simulated by a fake DBAPI
cursor whose error message quotes the whole statement, as a driver error might.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import traceback
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

import returns_roles
from returns_roles import (
    SESSION_GUARDS,
    RoleSetupError,
    create_login_role,
    scram_sha256_verifier,
)

ROLE = "m18t_app_0123456789"
SALT = bytes(range(16))


def test_the_verifier_matches_the_rfc_7677_test_vector():
    """RFC 7677 §3: user "user", password "pencil", 4096 iterations. The verifier's keys must
    validate the RFC's client proof and reproduce its server signature."""
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = scram_sha256_verifier("pencil", salt=salt)
    head, keys = verifier.split("$", 1)[1].split("$")
    stored_key, server_key = (base64.b64decode(k) for k in keys.split(":"))
    assert head == "4096:W22ZaJ0SNY7soEsUEjb6gQ=="
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth_message = (
        f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
        f"c=biws,r={nonce}"
    ).encode()
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    client_key = bytes(a ^ b for a, b in zip(proof, client_signature, strict=True))
    assert hashlib.sha256(client_key).digest() == stored_key
    server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    assert base64.b64encode(server_signature) == b"6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


# ---- fakes -----------------------------------------------------------------------------------


class DriverError(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class FakeCursor:
    def __init__(self, log: list[str], fail_on: str | None) -> None:
        self.log, self.fail_on = log, fail_on
        self.closed = False

    def execute(self, statement: str) -> None:
        self.log.append(statement)
        if self.fail_on is not None and statement.startswith(self.fail_on):
            raise DriverError(f'syntax error at or near "{statement}"', "42601")

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """A SQLAlchemy-like connection exposing a fake DBAPI connection; its own `execute`
    methods fail the test if used."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[str] = []
        self.cursors: list[FakeCursor] = []
        outer = self

        class Dbapi:
            def cursor(self) -> FakeCursor:
                cursor = FakeCursor(outer.statements, fail_on)
                outer.cursors.append(cursor)
                return cursor

        class Proxied:
            dbapi_connection = Dbapi()

        self.connection = Proxied()

    def execute(self, *args, **kwargs):
        raise AssertionError("role SQL must not go through SQLAlchemy")

    exec_driver_sql = execute


@pytest.fixture
def fixed_salt(monkeypatch):
    # Only the helper's salt source; the test's own passwords stay random.
    monkeypatch.setattr(returns_roles, "secrets", SimpleNamespace(token_bytes=lambda n: SALT[:n]))


def secret_parts(password: str) -> dict[str, str]:
    """The password, its verifier and each verifier component."""
    verifier = scram_sha256_verifier(password, salt=SALT)
    salt_part, keys = verifier.split("$", 1)[1].split("$")
    stored_key, server_key = keys.split(":")
    return {
        "password": password,
        "verifier": verifier,
        "salt": salt_part.split(":")[1],
        "stored_key": stored_key,
        "server_key": server_key,
    }


def leaks(parts: dict[str, str], surfaces: dict[str, str]) -> list[str]:
    """Names (never values) of each secret part found on each surface."""
    return sorted(f"{p}->{s}" for p, v in parts.items() for s, t in surfaces.items() if v in t)


# ---- statements sent -------------------------------------------------------------------------


def test_the_role_sql_sends_a_verifier_after_the_session_guards_never_the_password(fixed_salt):
    password = secrets.token_urlsafe(24)
    conn = FakeConnection()
    create_login_role(conn, ROLE, password)
    *guards, create, reset = conn.statements
    assert tuple(guards) == SESSION_GUARDS
    assert reset == "RESET ALL"
    password_sent = password in "".join(conn.statements)
    assert not password_sent, "the synthetic password reached the SQL text"
    verifier_in_create = secret_parts(password)["verifier"] in create
    assert verifier_in_create, "the CREATE ROLE statement must carry the verifier"
    assert create.startswith(f'CREATE ROLE "{ROLE}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    assert all(c.closed for c in conn.cursors)


def test_a_guard_that_cannot_be_set_stops_before_create_role():
    conn = FakeConnection(fail_on="SET log_min_error_statement")
    with pytest.raises(RoleSetupError):
        create_login_role(conn, ROLE, secrets.token_urlsafe(24))
    assert not any(s.startswith("CREATE ROLE") for s in conn.statements)
    assert conn.statements[-1] == "RESET ALL"


def test_a_failed_create_leaks_neither_password_nor_verifier(fixed_salt, caplog, capsys):
    """The fake driver error quotes the whole statement, verifier included."""
    password = secrets.token_urlsafe(24)
    caplog.set_level(logging.DEBUG)
    conn = FakeConnection(fail_on="CREATE ROLE")
    with pytest.raises(RoleSetupError) as caught:
        create_login_role(conn, ROLE, password)
    error = caught.value
    captured = capsys.readouterr()
    surfaces = {
        "message": str(error),
        "repr": repr(error),
        "traceback": "".join(traceback.format_exception(error)),
        "logs": caplog.text,
        "stdout": captured.out,
        "stderr": captured.err,
    }
    found = leaks(secret_parts(password), surfaces)
    assert not found, f"secret material leaked: {found}"
    assert error.__cause__ is None and error.__context__ is None
    assert str(error) == "creating a test role failed: DriverError (sqlstate 42601)"


def test_sqlalchemy_statement_logging_never_sees_the_role_sql(
    fixed_salt, caplog, capsys, monkeypatch
):
    """With `echo=True` and every logger at DEBUG, the raw-cursor path is invisible to
    SQLAlchemy. Guards are skipped so the CREATE ROLE really reaches SQLite (which refuses it)."""
    monkeypatch.setattr(returns_roles, "SESSION_GUARDS", ())
    password = secrets.token_urlsafe(24)
    caplog.set_level(logging.DEBUG)
    engine = create_engine("sqlite://", echo=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")  # the logging path is live
        with pytest.raises(RoleSetupError) as caught:
            create_login_role(conn, ROLE, password)
    engine.dispose()
    captured = capsys.readouterr()
    surfaces = {
        "logs": caplog.text,
        "stdout": captured.out,
        "stderr": captured.err,
        "traceback": "".join(traceback.format_exception(caught.value)),
    }
    found = leaks(secret_parts(password), surfaces)
    assert not found, f"secret material leaked: {found}"
    assert "SELECT 1" in caplog.text and "CREATE ROLE" not in caplog.text
    assert "OperationalError" in str(caught.value)


@pytest.mark.parametrize(
    ("role", "password"),
    [
        ("postgres", "a" * 24),
        (ROLE, "short"),
        (ROLE, "x" * 24 + "'"),
        ('m18t_app_0123456789"', "a" * 24),
    ],
    ids=["unexpected-role", "short-password", "quote-in-password", "quote-in-role"],
)
def test_unexpected_names_or_passwords_are_refused_without_echo(role, password):
    conn = FakeConnection()
    with pytest.raises(RoleSetupError) as caught:
        create_login_role(conn, role, password)
    assert not conn.statements
    echoed = password in str(caught.value)
    assert not echoed, "the password was echoed"
