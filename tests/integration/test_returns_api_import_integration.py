"""M18-04B: the import routes over a real scratch database (contracts §4.3, §6, §7).

The real path, end to end: HTTP → retailer access → commit gate → the lazily built importer
engine → the existing import service, as real least-privilege roles. Covers AC-09/AC-07/AC-08
through the API, AC-13 (2) for validate, and AC-11's commit gate on a live database:

1. a local import (gate open) commits as the importer role: 201 `imported`, the identical
   repeat 200 `already_imported`, and the batch records the configured importer label;
2. validate runs as the API role: 200 with the report, and all 15 return-data tables are
   unchanged;
3. commit disabled: a viewer token is refused by scope (403 `forbidden`) before the gate; an
   importer token with the commit flag off is refused by the gate (403
   `import_commit_disabled`); nothing is written;
4. role rejection: valid credentials for a disallowed (read-only) role reach the service's role
   check and give 403 `import_commit_disabled`, unlike a wrong password, which never connects
   and gives 503 `import_storage_unavailable`; the importer user equal to the app user is
   refused by Settings itself.

One throwaway `ecommerce_search_test_*` database per module (migrated, catalog ingested) and
three throwaway LOGIN roles (`m18t_app_*`, `m18t_imp_*`, `m18t_ro_*`) created by the
secret-safe `tests/returns_roles.py`; the read-only role receives the API role's grants. All are
dropped afterwards. Passwords never appear in responses, logs or assertion messages. Every
record is SYNTHETIC.
"""

import copy
import json
import logging
import secrets
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from returns_support import scratch
from sqlalchemy import Engine, create_engine, pool, text

from ecommerce_search.api.app import create_app
from ecommerce_search.config import Settings
from ecommerce_search.ingestion.service import ingest_file
from ecommerce_search.models.returns import RETURN_TABLES
from ecommerce_search.returns import grants as rg
from returns_release import ROOT, bundle
from returns_roles import create_login_role

pytestmark = pytest.mark.integration

VIEWER = secrets.token_urlsafe(32)
IMPORTER = secrets.token_urlsafe(32)
LABEL = "it-local-importer"
SEED = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
SEED_PROVENANCE = ROOT / "data" / "seed" / "catalog_seed_v1.provenance.json"
BOUNDARY = "it-boundary-04b"
DATASET = "synthetic-returns"


@dataclass
class Db:
    name: str
    owner: Engine
    app: str
    importer: str
    readonly: str
    passwords: dict[str, str] = field(repr=False)  # generated; never printed


@pytest.fixture(scope="module")
def db(settings):
    name = scratch(f"ecommerce_search_test_{uuid.uuid4().hex[:12]}")
    suffix = uuid.uuid4().hex[:10]
    roles = {"app": f"m18t_app_{suffix}", "imp": f"m18t_imp_{suffix}", "ro": f"m18t_ro_{suffix}"}
    passwords = {role: secrets.token_urlsafe(24) for role in roles.values()}
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    owner = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            for role, password in passwords.items():
                create_login_role(conn, role, password)
        config = Config("alembic.ini")
        config.attributes["database"] = name
        command.upgrade(config, "head")
        owner = create_engine(settings.database_url(database=name), poolclass=pool.NullPool)
        outcome = ingest_file(owner, SEED, SEED_PROVENANCE)
        assert outcome.result is not None and outcome.result.inserted == 240
        with owner.begin() as conn:
            rg.apply_grants(conn, app_role=roles["app"], importer_role=roles["imp"])
            # The read-only role gets exactly the API role's grants (no INSERT anywhere).
            rg.apply_grants(conn, app_role=roles["ro"], importer_role=roles["imp"])
        yield Db(name, owner, roles["app"], roles["imp"], roles["ro"], passwords)
    finally:
        if owner is not None:
            owner.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in roles.values():
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def app_settings(db: Db, settings: Settings, **overrides) -> Settings:
    """The app as the API role on the scratch database, with both retailer tokens."""
    values = {
        "app_env": "test",
        "postgres_host": settings.postgres_host,
        "postgres_port": settings.postgres_port,
        "postgres_db": db.name,
        "postgres_user": db.app,
        "postgres_password": db.passwords[db.app],
        "returns_viewer_token": VIEWER,
        "returns_importer_token": IMPORTER,
    }
    return Settings(_env_file=None, **(values | overrides))


def importer_settings(db: Db, settings: Settings, *, user: str, password: str, **overrides):
    """The gate's four conditions met (flag, importer token, DB credentials, label) unless an
    override changes one."""
    values = {
        "returns_import_commit_enabled": True,
        "returns_importer_db_user": user,
        "returns_importer_db_password": password,
        "returns_importer_label": LABEL,
    }
    return app_settings(db, settings, **(values | overrides))


@contextmanager
def client_for(app: Settings):
    with TestClient(create_app(app)) as test_client:
        yield test_client


def upload(version: int) -> bytes:
    """The release bundle as dataset `version`, as one multipart part named `bundle.json`."""
    body = copy.deepcopy(bundle())
    body["dataset"] |= {"dataset_id": DATASET, "dataset_version": version}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="bundle.json"; filename="ignored.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{BOUNDARY}--\r\n".encode()


def post(test_client: TestClient, path: str, token: str, version: int):
    return test_client.post(
        path,
        content=upload(version),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        },
    )


def table_counts(db: Db) -> dict[str, int]:
    with db.owner.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in RETURN_TABLES
        }


def assert_no_password(db: Db, *texts: str) -> None:
    leaked = any(p in t for p in db.passwords.values() for t in texts)
    assert not leaked, "a role password reached a response or log"


def assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"


def test_the_module_covers_all_fifteen_return_data_tables():
    assert len(RETURN_TABLES) == 15


# ---- 1. local import ---------------------------------------------------------------------------


def test_a_local_import_commits_as_the_importer_then_repeats_as_already_imported(
    db, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    before = table_counts(db)
    config = importer_settings(db, settings, user=db.importer, password=db.passwords[db.importer])
    with client_for(config) as test_client:
        assert test_client.app.state.returns_importer_engine is not None  # the gate is open
        first = post(test_client, "/returns/import", IMPORTER, 1)
        assert first.status_code == 201, first.json().get("code")
        report = first.json()
        assert report["mode"] == "commit" and report["status"] == "imported"
        after_first = table_counts(db)
        repeat = post(test_client, "/returns/import", IMPORTER, 1)
        assert repeat.status_code == 200
        assert repeat.json()["status"] == "already_imported"
        assert repeat.json()["batch_id"] == report["batch_id"]
        listed = test_client.get(
            f"/returns/import-batches/{report['batch_id']}",
            headers={"Authorization": f"Bearer {VIEWER}"},
        ).json()
    assert table_counts(db) == after_first  # the repeat wrote nothing
    assert all(after_first[t] > before[t] for t in ("return_import_batches", "returns"))
    with db.owner.connect() as conn:
        label = conn.execute(
            text("SELECT imported_by_label FROM return_import_batches WHERE batch_id = :b"),
            {"b": report["batch_id"]},
        ).scalar_one()
    assert label == LABEL and listed["imported_by_label"] == LABEL
    assert_no_password(db, first.text, repeat.text, caplog.text)


# ---- 2. validate only -------------------------------------------------------------------------


@pytest.mark.parametrize("token", [VIEWER, IMPORTER], ids=["viewer", "importer"])
def test_validate_runs_as_the_api_role_and_writes_nothing(db, settings, token, caplog):
    caplog.set_level(logging.DEBUG)
    before = table_counts(db)
    with client_for(app_settings(db, settings)) as test_client:
        response = post(test_client, "/returns/import/validate", token, 2)
    assert response.status_code == 200
    report = response.json()
    assert report["mode"] == "validate" and report["status"] == "valid"
    assert report["error_count"] == 0 and report["batch_id"] is None
    assert table_counts(db) == before
    assert_no_password(db, response.text, caplog.text)


# ---- 3. commit disabled -----------------------------------------------------------------------


def test_a_viewer_is_refused_by_scope_before_the_commit_gate(db, settings):
    before = table_counts(db)
    config = importer_settings(db, settings, user=db.importer, password=db.passwords[db.importer])
    with client_for(config) as test_client:
        response = post(test_client, "/returns/import", VIEWER, 3)
    assert_error(response, 403, "forbidden")
    assert table_counts(db) == before


def test_an_importer_with_the_commit_flag_off_is_refused_by_the_gate(db, settings):
    before = table_counts(db)
    config = importer_settings(
        db,
        settings,
        user=db.importer,
        password=db.passwords[db.importer],
        returns_import_commit_enabled=False,
    )
    with client_for(config) as test_client:
        assert test_client.app.state.returns_importer_engine is None  # never built
        response = post(test_client, "/returns/import", IMPORTER, 3)
        capabilities = test_client.get(
            "/returns/capabilities", headers={"Authorization": f"Bearer {IMPORTER}"}
        ).json()
    assert_error(response, 403, "import_commit_disabled")
    assert capabilities["can_commit"] is False
    assert table_counts(db) == before


# ---- 4. role rejection versus connection failure ----------------------------------------------


def test_valid_credentials_of_a_read_only_role_are_rejected_by_the_role_check(db, settings, caplog):
    caplog.set_level(logging.DEBUG)
    before = table_counts(db)
    config = importer_settings(db, settings, user=db.readonly, password=db.passwords[db.readonly])
    with client_for(config) as test_client:
        response = post(test_client, "/returns/import", IMPORTER, 4)
    assert_error(response, 403, "import_commit_disabled")
    assert '"event":"returns_import_role_rejected"' in caplog.text
    assert '"reason":"missing_privilege"' in caplog.text  # it connected and was checked
    assert table_counts(db) == before
    assert_no_password(db, response.text, caplog.text)


def test_a_wrong_importer_password_never_connects_and_is_a_storage_error(db, settings, caplog):
    caplog.set_level(logging.DEBUG)
    before = table_counts(db)
    wrong = secrets.token_urlsafe(24)
    config = importer_settings(db, settings, user=db.importer, password=wrong)
    with client_for(config) as test_client:
        response = post(test_client, "/returns/import", IMPORTER, 4)
    assert_error(response, 503, "import_storage_unavailable")
    assert '"event":"returns_import_storage_error"' in caplog.text
    assert "returns_import_role_rejected" not in caplog.text  # distinct from a role rejection
    assert table_counts(db) == before
    leaked = wrong in response.text or wrong in caplog.text
    assert not leaked, "the wrong password reached a response or log"
    assert_no_password(db, response.text, caplog.text)


def test_the_importer_user_equal_to_the_app_user_is_refused_by_settings(db, settings):
    with pytest.raises(ValidationError) as caught:
        importer_settings(db, settings, user=db.app, password=db.passwords[db.app])
    message = str(caught.value)
    assert "returns_importer_db_user must differ from postgres_user" in message
    assert_no_password(db, message)
