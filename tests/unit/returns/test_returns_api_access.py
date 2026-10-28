"""M18-04A: retailer access control, the commit gate, safe `/returns` errors and capabilities.

Hermetic: no database is reached (the app engine is lazy, capabilities reads nothing, and the
importer engine is replaced by a fake wherever it would be built). Tokens are generated per test
run and never committed.
"""

import logging
import secrets
from itertools import product
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ecommerce_search.api import returns as api
from ecommerce_search.api.app import create_app
from ecommerce_search.config import Settings
from ecommerce_search.returns import contracts

VIEWER = secrets.token_urlsafe(32)
IMPORTER = secrets.token_urlsafe(32)
OTHER = secrets.token_urlsafe(32)
DB_PASSWORD = "importer-pw-test-not-real-7c1d"  # noqa: S105 - fake value, never a credential
LABEL = "local-importer"
CAPABILITIES = "/returns/capabilities"
# The buyer and documentation paths before Milestone 18 (OpenAPI view).
PRE_M18_PATHS = {
    "/health": {"get"},
    "/search": {"get", "post"},
    "/search/dense": {"get", "post"},
    "/search/hybrid": {"get", "post"},
    "/search/filtered": {"get", "post"},
    "/search/reranked": {"get", "post"},
    "/search/routed": {"get", "post"},
    "/assistant/status": {"get"},
    "/assistant/answer": {"post"},
}
CAPABILITY_FIELDS = {
    "contract_version",
    "scopes",
    "can_validate",
    "can_commit",
    "limits",
    "reason_codes",
}


def app_logs(caplog) -> str:
    """The application's log records only; the test client's own httpx logger records URLs."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(("ecommerce_search", "uvicorn", "fastapi", "starlette"))
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def returns_settings(make_settings):
    def _make(**overrides) -> Settings:
        values = {"returns_viewer_token": VIEWER, "returns_importer_token": IMPORTER}
        values.update(overrides)
        return make_settings(**values)

    return _make


@pytest.fixture
def client(returns_settings):
    with TestClient(create_app(returns_settings())) as test_client:
        yield test_client


@pytest.fixture
def built_engines(monkeypatch):
    """Replaces the importer engine factory; records every engine built and disposed."""
    built: list[FakeEngine] = []

    def fake(settings, url):
        engine = FakeEngine(url)
        built.append(engine)
        return engine

    monkeypatch.setattr(api, "create_importer_engine", fake)
    return built


class FakeEngine:
    def __init__(self, url) -> None:
        self.url = url
        self.disposed = 0

    def dispose(self) -> None:
        self.disposed += 1


def assert_safe_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert body == {"detail": api.DETAILS[code], "code": code, **body} and body["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    return body


# ---- settings --------------------------------------------------------------------------------


def test_returns_access_settings_default_to_unset_and_commit_off(make_settings):
    s = make_settings()
    assert s.returns_viewer_token is None and s.returns_importer_token is None
    assert s.returns_import_commit_enabled is False
    assert s.returns_importer_db_user is None and s.returns_importer_db_password is None
    assert s.returns_importer_database_url() is None


def test_empty_returns_values_mean_unset(make_settings):
    s = make_settings(
        returns_viewer_token="",
        returns_importer_token="",
        returns_importer_db_user="",
        returns_importer_db_password="",
    )
    assert s.returns_viewer_token is None and s.returns_importer_token is None
    assert s.returns_importer_db_user is None and s.returns_importer_db_password is None


@pytest.mark.parametrize("length", [32, 43, 256])
def test_tokens_of_32_to_256_b64token_characters_are_accepted(make_settings, length):
    token = ("aZ0-._~+/" * 40)[: length - 1] + "="
    assert make_settings(returns_viewer_token=token).returns_viewer_token is not None


@pytest.mark.parametrize(
    "token",
    [
        "a" * 31,
        "a" * 257,
        "a" * 30 + " b",
        "a" * 31 + "\t",
        " " + "a" * 32,
        "a" * 31 + "é",
        "a" * 31 + "=b",
        "a" * 31 + ",",
        "a" * 31 + "\n",
    ],
    ids=[
        "31",
        "257",
        "space",
        "tab",
        "leading-space",
        "non-ascii",
        "inner-pad",
        "comma",
        "newline",
    ],
)
@pytest.mark.parametrize("field", ["returns_viewer_token", "returns_importer_token"])
def test_invalid_tokens_are_refused_without_echoing_them(make_settings, field, token):
    with pytest.raises(ValidationError) as caught:
        make_settings(**{field: token})
    assert [e["loc"] for e in caught.value.errors()] == [(field,)]
    assert_no_secret(caught.value, token)


@pytest.mark.parametrize("source", ["init", "env"])
def test_equal_tokens_are_refused_without_echoing_them(make_settings, monkeypatch, source):
    if source == "env":
        monkeypatch.setenv("RETURNS_VIEWER_TOKEN", VIEWER)
        monkeypatch.setenv("RETURNS_IMPORTER_TOKEN", VIEWER)
        values = {}
    else:
        values = {"returns_viewer_token": VIEWER, "returns_importer_token": VIEWER}
    with pytest.raises(ValidationError) as caught:
        make_settings(**values)
    assert "must differ" in str(caught.value)
    assert_no_secret(caught.value, VIEWER)


def assert_no_secret(error: ValidationError, *values: str) -> None:
    text = " ".join(
        [str(error), repr(error), repr(error.errors()), error.json(), repr(error.errors())]
    )
    for value in values:
        assert value not in text


def test_returns_secrets_are_masked_settings_inputs():
    from ecommerce_search.config.settings import _secret_input_names

    names = _secret_input_names(Settings.model_fields)
    assert {
        "returns_viewer_token",
        "returns_importer_token",
        "returns_importer_db_password",
    } <= names
    assert "returns_importer_db_user" not in names


@pytest.mark.parametrize(
    "values",
    [
        {"returns_importer_db_user": "returns_importer"},
        {"returns_importer_db_password": DB_PASSWORD},
    ],
    ids=["user-only", "password-only"],
)
def test_importer_db_credentials_are_both_or_neither(make_settings, values):
    with pytest.raises(ValidationError) as caught:
        make_settings(**values)
    assert "or neither" in str(caught.value)
    assert_no_secret(caught.value, DB_PASSWORD)


def test_importer_db_user_must_differ_from_the_app_user(make_settings):
    with pytest.raises(ValidationError) as caught:
        make_settings(
            postgres_user="search_app",
            returns_importer_db_user="search_app",
            returns_importer_db_password=DB_PASSWORD,
        )
    assert "must differ from postgres_user" in str(caught.value)
    assert_no_secret(caught.value, DB_PASSWORD)


@pytest.mark.parametrize(
    "user", ["Returns", "1importer", "imp-orter", "imp orter", "a" * 64, "imp;drop"]
)
def test_importer_db_user_must_be_a_plain_role_name(make_settings, user):
    with pytest.raises(ValidationError) as caught:
        make_settings(returns_importer_db_user=user, returns_importer_db_password=DB_PASSWORD)
    assert [e["loc"] for e in caught.value.errors()] == [("returns_importer_db_user",)]
    assert user not in str(caught.value)


@pytest.mark.parametrize("value", ["1", "yes", "on", 1])
def test_commit_flag_accepts_only_true_or_false(make_settings, value):
    with pytest.raises(ValidationError):
        make_settings(returns_import_commit_enabled=value)
    assert make_settings(returns_import_commit_enabled="TRUE").returns_import_commit_enabled


def test_importer_url_uses_the_app_server_and_hides_the_password(make_settings):
    s = make_settings(
        postgres_host="db.internal",
        postgres_port=6543,
        postgres_db="ecommerce_search",
        returns_importer_db_user="returns_importer",
        returns_importer_db_password=DB_PASSWORD,
    )
    url = s.returns_importer_database_url()
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "returns_importer",
        DB_PASSWORD,
        "db.internal",
        6543,
        "ecommerce_search",
    )
    assert DB_PASSWORD not in str(url) and DB_PASSWORD not in repr(url)
    assert DB_PASSWORD not in repr(s) and VIEWER not in repr(s)
    assert s.database_url().username != url.username


# ---- token handling --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "headers", "status", "code"),
    [
        ({}, {}, 503, "retailer_access_not_configured"),
        ({}, bearer(VIEWER), 503, "retailer_access_not_configured"),
        ({"returns_viewer_token": VIEWER}, {}, 401, "unauthorized"),
        ({"returns_viewer_token": VIEWER}, bearer(IMPORTER), 401, "unauthorized"),
        ({"returns_viewer_token": VIEWER}, bearer(VIEWER), 200, None),
        ({"returns_importer_token": IMPORTER}, bearer(VIEWER), 401, "unauthorized"),
        ({"returns_importer_token": IMPORTER}, bearer(IMPORTER), 200, None),
    ],
    ids=["none", "none-with-token", "viewer-missing", "viewer-only", "viewer", "imp-only", "imp"],
)
def test_configuration_cases_fail_closed(make_settings, config, headers, status, code):
    with TestClient(create_app(make_settings(**config))) as c:
        response = c.get(CAPABILITIES, headers=headers)
    if code is None:
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
    else:
        assert_safe_error(response, status, code)


MALFORMED = {
    "empty": "",
    "scheme-only": "Bearer",
    "scheme-space": "Bearer ",
    "basic": f"Basic {VIEWER}",
    "no-scheme": VIEWER,
    "double-space": f"Bearer  {VIEWER}",
    "trailing-space": f"Bearer {VIEWER} ",
    "tab": f"Bearer\t{VIEWER}",
    "two-tokens": f"Bearer {VIEWER} {VIEWER}",
    "unknown": f"Bearer {OTHER}",
    "prefix": f"Bearer {VIEWER[:-1]}",
    "extended": f"Bearer {VIEWER}x",
    "short": "Bearer abc",
    "comma": f"Bearer {VIEWER},{IMPORTER}",
    "oversized": "Bearer " + "a" * 100_000,
    "oversized-valid-prefix": f"Bearer {VIEWER}" + "a" * 300,
}


@pytest.mark.parametrize("value", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_unknown_or_oversized_authorization_is_401(client, caplog, value):
    caplog.set_level(logging.DEBUG)
    response = client.get(CAPABILITIES, headers={"Authorization": value})
    assert_safe_error(response, 401, "unauthorized")
    assert response.headers["www-authenticate"] == "Bearer"
    assert value.strip() not in response.text if value.strip() else True
    for secret in (VIEWER, IMPORTER, OTHER):
        assert secret not in response.text and secret not in app_logs(caplog)


def test_non_ascii_authorization_bytes_are_401(client):
    response = client.get(
        CAPABILITIES, headers=[(b"authorization", b"Bearer " + VIEWER.encode() + b"\xe9")]
    )
    assert_safe_error(response, 401, "unauthorized")


def test_repeated_authorization_headers_are_401_even_when_both_are_valid(client):
    response = client.get(
        CAPABILITIES,
        headers=[("Authorization", f"Bearer {VIEWER}"), ("Authorization", f"Bearer {VIEWER}")],
    )
    assert_safe_error(response, 401, "unauthorized")


def test_the_scheme_is_case_insensitive(client):
    assert client.get(CAPABILITIES, headers={"Authorization": f"bearer {VIEWER}"}).is_success
    assert client.get(CAPABILITIES, headers={"Authorization": f"BEARER {IMPORTER}"}).is_success


def test_tokens_in_cookies_or_query_strings_are_ignored(client):
    client.cookies.set("Authorization", f"Bearer {VIEWER}")
    response = client.get(CAPABILITIES, params={"access_token": VIEWER, "token": VIEWER})
    assert_safe_error(response, 401, "unauthorized")
    assert VIEWER not in response.text


def test_scopes_are_viewer_read_validate_and_importer_adds_import(client):
    viewer = client.get(CAPABILITIES, headers=bearer(VIEWER)).json()
    importer = client.get(CAPABILITIES, headers=bearer(IMPORTER)).json()
    assert viewer["scopes"] == ["returns:read", "returns:validate"]
    assert importer["scopes"] == ["returns:import", "returns:read", "returns:validate"]
    assert viewer["can_validate"] and importer["can_validate"]


def test_a_token_lacking_the_scope_is_403(returns_settings):
    app = create_app(returns_settings())
    test_router = APIRouter(prefix="/returns/_t", route_class=api.ReturnsRoute)

    @test_router.get("/import-only")
    def import_only(
        caller: Annotated[api.Caller, Depends(api.require_scope(api.ReturnsScope.IMPORT))],
    ) -> dict:
        return {"ok": True}

    app.include_router(test_router)
    with TestClient(app) as c:
        assert_safe_error(
            c.get("/returns/_t/import-only", headers=bearer(VIEWER)), 403, "forbidden"
        )
        assert c.get("/returns/_t/import-only", headers=bearer(IMPORTER)).json() == {"ok": True}
        assert_safe_error(c.get("/returns/_t/import-only"), 401, "unauthorized")


def test_access_keeps_only_digests_of_the_tokens(returns_settings):
    access = api.ReturnsAccess.from_settings(returns_settings())
    text = repr(access) + str(vars(access))
    assert VIEWER not in text and IMPORTER not in text
    assert VIEWER.encode() not in (access.viewer_digest or b"")
    assert access.scopes_for(VIEWER) == api.VIEWER_SCOPES
    assert access.scopes_for(IMPORTER) == api.IMPORTER_SCOPES
    assert access.scopes_for(OTHER) is None


def test_both_digests_are_compared_for_every_token(returns_settings, monkeypatch):
    access = api.ReturnsAccess.from_settings(returns_settings())
    calls = []
    real = api.hmac.compare_digest

    def counting(a, b):
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(api.hmac, "compare_digest", counting)
    for token in (VIEWER, IMPORTER, OTHER):
        calls.clear()
        access.scopes_for(token)
        assert len(calls) == 2


def test_an_unset_slot_never_matches(make_settings):
    access = api.ReturnsAccess.from_settings(make_settings(returns_viewer_token=VIEWER))
    assert access.importer_digest is None
    assert access.scopes_for(IMPORTER) is None


# ---- rotation --------------------------------------------------------------------------------


def test_rotation_takes_effect_at_restart_and_the_old_token_stops_working(returns_settings):
    with TestClient(create_app(returns_settings())) as before:
        assert before.get(CAPABILITIES, headers=bearer(VIEWER)).is_success
    rotated = returns_settings(returns_viewer_token=OTHER)
    with TestClient(create_app(rotated)) as after:
        assert_safe_error(after.get(CAPABILITIES, headers=bearer(VIEWER)), 401, "unauthorized")
        assert after.get(CAPABILITIES, headers=bearer(OTHER)).is_success
        assert after.get(CAPABILITIES, headers=bearer(IMPORTER)).is_success


def test_emergency_revocation_unsets_every_token_and_fails_closed(make_settings):
    with TestClient(create_app(make_settings())) as c:
        for token in (VIEWER, IMPORTER):
            assert_safe_error(
                c.get(CAPABILITIES, headers=bearer(token)), 503, "retailer_access_not_configured"
            )


# ---- commit gate and importer engine ---------------------------------------------------------

GATE_CONDITIONS = ("commit_enabled", "importer_token", "importer_db_credentials", "label")


def gate_settings(returns_settings, flags: dict[str, bool]) -> Settings:
    values: dict = {"returns_import_commit_enabled": flags["commit_enabled"]}
    if not flags["importer_token"]:
        values["returns_importer_token"] = None
    if flags["importer_db_credentials"]:
        values["returns_importer_db_user"] = "returns_importer"
        values["returns_importer_db_password"] = DB_PASSWORD
    if flags["label"]:
        values["returns_importer_label"] = LABEL
    return returns_settings(**values)


@pytest.mark.parametrize(
    "flags",
    [dict(zip(GATE_CONDITIONS, combo, strict=True)) for combo in product((False, True), repeat=4)],
    ids=lambda f: "-".join(k for k, v in f.items() if v) or "none",
)
def test_commit_needs_every_gate_condition_together(returns_settings, built_engines, flags):
    settings = gate_settings(returns_settings, flags)
    gate = api.CommitGate.from_settings(settings)
    assert gate.open is all(flags.values())
    app = create_app(settings)
    assert (app.state.returns_importer_engine is not None) is gate.open
    assert built_engines == []  # lazy: nothing is built at app creation
    with TestClient(app) as c:
        viewer = c.get(CAPABILITIES, headers=bearer(VIEWER)).json()
        assert viewer["can_commit"] is False
        if flags["importer_token"]:
            importer = c.get(CAPABILITIES, headers=bearer(IMPORTER)).json()
            assert importer["can_commit"] is gate.open
    assert built_engines == []


def test_the_importer_token_alone_never_enables_commit(returns_settings, built_engines):
    with TestClient(create_app(returns_settings())) as c:
        body = c.get(CAPABILITIES, headers=bearer(IMPORTER)).json()
    assert "returns:import" in body["scopes"] and body["can_commit"] is False


def test_the_importer_engine_is_separate_lazy_and_disposed_at_shutdown(
    returns_settings, built_engines
):
    flags = dict.fromkeys(GATE_CONDITIONS, True)
    app = create_app(gate_settings(returns_settings, flags))
    holder = app.state.returns_importer_engine
    with TestClient(app):
        assert built_engines == []
        engine = holder.get()
        assert holder.get() is engine and len(built_engines) == 1
        assert engine is not app.state.engine
        assert engine.url.username == "returns_importer"
        assert app.state.engine.url.username != "returns_importer"
        assert DB_PASSWORD not in repr(holder)
    assert engine.disposed == 1


def test_the_real_importer_engine_has_one_connection_and_the_app_timeouts(returns_settings):
    from ecommerce_search.db.engine import create_importer_engine

    settings = gate_settings(returns_settings, dict.fromkeys(GATE_CONDITIONS, True))
    engine = create_importer_engine(settings, settings.returns_importer_database_url())
    try:
        assert engine.pool.size() == 1 and engine.pool._max_overflow == 0
        assert engine.url.username == "returns_importer"
        assert DB_PASSWORD not in repr(engine) and DB_PASSWORD not in str(engine.url)
    finally:
        engine.dispose()


def test_a_closed_gate_builds_no_importer_engine_holder(returns_settings, built_engines):
    settings = returns_settings(
        returns_import_commit_enabled=False,
        returns_importer_db_user="returns_importer",
        returns_importer_db_password=DB_PASSWORD,
        returns_importer_label=LABEL,
    )
    app = create_app(settings)
    assert app.state.returns_importer_engine is None
    with TestClient(app):
        pass
    assert built_engines == []


# ---- capabilities ----------------------------------------------------------------------------


def test_capabilities_has_exactly_the_contracted_fields_and_limits(client):
    body = client.get(CAPABILITIES, headers=bearer(VIEWER)).json()
    assert set(body) == CAPABILITY_FIELDS
    assert body["contract_version"] == "return-data-v1"
    assert body["reason_codes"] == [c.value for c in contracts.ReasonCode]
    assert body["limits"] == {
        "request_body_bytes": 9 * 1024 * 1024,
        "bundle_bytes": 8 * 1024 * 1024,
        "file_bytes": 5 * 1024 * 1024,
        "manifest_bytes": 64 * 1024,
        "files": 12,
        "bundle_rows": 10_000,
        "entity_rows": 5_000,
        "csv_columns": 30,
        "csv_field_bytes": 4096,
        "text_code_points": 2000,
        "note_code_points": 1000,
        "dataset_long_text_code_points": 2000,
        "dataset_short_text_code_points": 200,
        "errors_collected": 1000,
        "errors_returned": 200,
        "concurrent_imports": 1,
    }


def test_capabilities_exposes_no_credential_or_configuration(returns_settings, built_engines):
    settings = gate_settings(returns_settings, dict.fromkeys(GATE_CONDITIONS, True))
    settings = settings.model_copy(update={"postgres_host": "db-host-marker.internal"})
    with TestClient(create_app(settings)) as c:
        texts = [c.get(CAPABILITIES, headers=bearer(t)).text for t in (VIEWER, IMPORTER)]
    for text in texts:
        for value in (
            VIEWER,
            IMPORTER,
            DB_PASSWORD,
            "returns_importer",
            LABEL,
            "db-host-marker",
            "postgres",
            "commit_enabled",
        ):
            assert value not in text


def test_capabilities_responses_are_not_cached(client):
    response = client.get(CAPABILITIES, headers=bearer(VIEWER))
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


# ---- safe errors -----------------------------------------------------------------------------

HOSTILE = "zq<script>@example.com 9876543210 ' OR 1=1 --"


class _Strict(BaseModel):
    value: int


@pytest.fixture
def error_client(returns_settings, caplog):
    """The app plus test-only routes under /returns using the production route class."""
    app = create_app(returns_settings())
    test_router = APIRouter(prefix="/returns/_t", route_class=api.ReturnsRoute)

    @test_router.get("/typed")
    def typed(
        caller: Annotated[api.Caller, Depends(api.require_scope(api.ReturnsScope.READ))],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        flag: bool = False,
    ) -> dict:
        return {"limit": limit}

    @test_router.get("/pydantic-error")
    def pydantic_error() -> dict:
        _Strict(value=HOSTILE)
        return {}

    @test_router.get("/library-error")
    def library_error() -> dict:
        raise RuntimeError(f"connection to postgresql://user:{DB_PASSWORD}@host failed {HOSTILE}")

    @test_router.get("/http-error")
    def http_error() -> dict:
        raise HTTPException(418, detail=HOSTILE)

    @test_router.get("/starlette-http-error")
    def starlette_http_error() -> dict:
        # What Starlette itself raises, e.g. while parsing a request body.
        raise StarletteHTTPException(400, detail=HOSTILE)

    app.include_router(test_router)
    caplog.set_level(logging.DEBUG)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_query_validation_errors_carry_parameter_names_and_codes_only(error_client, caplog):
    response = error_client.get(
        "/returns/_t/typed", params={"limit": HOSTILE, "flag": HOSTILE}, headers=bearer(VIEWER)
    )
    body = assert_safe_error(response, 422, "invalid_parameter")
    assert body["parameters"] == [
        {"name": "flag", "code": "bool_parsing"},
        {"name": "limit", "code": "int_parsing"},
    ]
    assert set(body) == {"detail", "code", "parameters"}
    for fragment in ("zq<script>", "example.com", "9876543210", "OR 1=1", "input", "ctx"):
        assert fragment not in response.text
        assert fragment not in app_logs(caplog)


def test_out_of_range_parameters_are_reported_by_name_and_code(error_client):
    response = error_client.get("/returns/_t/typed?limit=1000", headers=bearer(VIEWER))
    body = assert_safe_error(response, 422, "invalid_parameter")
    assert body["parameters"] == [{"name": "limit", "code": "less_than_equal"}]
    assert "1000" not in response.text


def test_access_is_checked_before_parameters_are_reported(error_client):
    response = error_client.get("/returns/_t/typed", params={"limit": HOSTILE})
    # The access dependency runs while FastAPI solves the route's parameters, so an
    # unauthenticated caller learns nothing about parameter validity.
    assert_safe_error(response, 401, "unauthorized")
    assert "zq<script>" not in response.text


def test_a_pydantic_error_inside_a_route_is_a_safe_500(error_client, caplog):
    response = error_client.get("/returns/_t/pydantic-error")
    assert_safe_error(response, 500, "internal_error")
    assert HOSTILE not in response.text and "zq<script>" not in app_logs(caplog)
    assert '"error_type":"ValidationError"' in app_logs(caplog)


def test_a_library_error_is_a_safe_500_that_logs_only_its_type(error_client, caplog):
    response = error_client.get("/returns/_t/library-error")
    assert_safe_error(response, 500, "internal_error")
    for fragment in (DB_PASSWORD, "postgresql://", "zq<script>", "connection to"):
        assert fragment not in response.text and fragment not in app_logs(caplog)
    assert '"error_type":"RuntimeError"' in app_logs(caplog)


@pytest.mark.parametrize(("path", "status"), [("http-error", 418), ("starlette-http-error", 400)])
def test_http_exception_details_are_not_echoed(error_client, path, status):
    response = error_client.get(f"/returns/_t/{path}")
    assert_safe_error(response, status, "request_failed")
    assert "zq<script>" not in response.text


# ---- responses no /returns route produced ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/returns",
        "/returns/",
        f"/returns/unknown?token={VIEWER}&q=zq<script>",
        "/returns/records/zq%3Cscript%3E",
        f"/returns/capabilities/?access_token={VIEWER}",
    ],
    ids=["root", "root-slash", "unknown-with-query", "hostile-path", "slash-redirect"],
)
def test_unmatched_returns_paths_are_safe_404s(client, path):
    response = client.get(path, headers=bearer(VIEWER), follow_redirects=False)
    assert_safe_error(response, 404, "not_found")
    assert "location" not in response.headers
    assert VIEWER not in response.text and "zq" not in response.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_wrong_methods_are_safe_405s_with_allow(client, method):
    response = client.request(method, CAPABILITIES, headers=bearer(VIEWER), content=HOSTILE)
    assert_safe_error(response, 405, "method_not_allowed")
    assert response.headers["allow"] == "GET"
    assert "zq<script>" not in response.text


def test_head_on_a_returns_route_is_not_cached(client):
    response = client.head(CAPABILITIES, headers=bearer(VIEWER))
    assert response.headers["cache-control"] == "no-store"


def test_path_matching_is_exact_on_the_prefix(client):
    response = client.get("/returnsx")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}
    assert "cache-control" not in response.headers


# ---- buyer isolation and route inventory -----------------------------------------------------


def test_routes_are_the_pre_m18_set_plus_the_built_returns_routes_only(client):
    paths = {path: set(item) for path, item in client.app.openapi()["paths"].items()}
    assert paths == {
        **PRE_M18_PATHS,
        CAPABILITIES: {"get"},
        "/returns/import/validate": {"post"},  # M18-04B
        "/returns/import": {"post"},  # M18-04B
    }


@pytest.mark.parametrize("headers", [{}, bearer(VIEWER), bearer(IMPORTER), {"Authorization": "x"}])
def test_buyer_routes_neither_require_nor_read_retailer_tokens(
    make_settings, returns_settings, headers
):
    responses = []
    for settings in (make_settings(), returns_settings()):
        with TestClient(create_app(settings)) as c:
            responses.append(
                [
                    c.get("/search", headers=headers),  # missing q: FastAPI's own 422
                    c.get("/search", params={"q": "x", "top_k": "abc"}, headers=headers),
                    c.get("/nope", headers=headers),
                    c.post("/health", headers=headers),
                ]
            )
    for without, with_tokens in zip(*responses, strict=True):
        assert without.status_code == with_tokens.status_code
        assert without.json() == with_tokens.json()
        assert "cache-control" not in with_tokens.headers
        assert "www-authenticate" not in with_tokens.headers
    # The buyer 422 keeps FastAPI's default shape (it is not the /returns shape).
    assert "code" not in responses[1][0].json()
    assert responses[1][0].json()["detail"][0]["type"] == "missing"


def test_buyer_logs_and_responses_never_carry_retailer_tokens(returns_settings, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(returns_settings())) as c:
        c.get("/search", headers=bearer(VIEWER))
        c.get(CAPABILITIES, headers=bearer(VIEWER))
        c.get(CAPABILITIES, headers=bearer(OTHER))
    for token in (VIEWER, IMPORTER, OTHER):
        assert token not in app_logs(caplog)


def test_denials_are_logged_by_code_only(returns_settings, caplog):
    caplog.set_level(logging.INFO, logger="ecommerce_search")
    with TestClient(create_app(returns_settings())) as c:
        c.get(CAPABILITIES, headers=bearer(OTHER))
    events = [r.getMessage() for r in caplog.records if "returns_access_denied" in r.getMessage()]
    assert len(events) == 1 and '"code":"unauthorized"' in events[0]
    assert OTHER not in app_logs(caplog) and "Bearer" not in app_logs(caplog)


def test_settings_secret_values_are_secretstr(returns_settings):
    s = returns_settings(
        returns_importer_db_user="returns_importer", returns_importer_db_password=DB_PASSWORD
    )
    for name in ("returns_viewer_token", "returns_importer_token", "returns_importer_db_password"):
        assert isinstance(getattr(s, name), SecretStr)
