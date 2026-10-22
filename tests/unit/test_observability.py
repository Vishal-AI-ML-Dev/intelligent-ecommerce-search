"""Milestone 24A: request IDs, one completion log per request, query-free formatted logs."""

import asyncio
import io
import json
import logging
import random
import re
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from ecommerce_search.api import observability as obs
from ecommerce_search.api.app import create_app

MARKER = "zqx7privatemarker"
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
test_logger = logging.getLogger("ecommerce_search.tests.observability")


@pytest.fixture
def formatted_logs(caplog):
    """The production handler (formatter + request-ID filter) writing to a buffer."""
    caplog.set_level(logging.INFO)
    stream = io.StringIO()
    handler = obs.build_log_handler(stream)
    # The application's loggers only; the test client's own httpx logger records full URLs.
    handler.addFilter(logging.Filter("ecommerce_search"))
    root = logging.getLogger()
    root.addHandler(handler)
    yield stream.getvalue
    root.removeHandler(handler)


def completion_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        start = line.find("{")
        if start != -1 and '"event":"request_completed"' in line:
            out.append(json.loads(line[start:]))
    return out


@pytest.fixture
def app(make_settings):
    app = create_app(make_settings())

    @app.get("/_t/echo")
    async def echo() -> dict:
        return {"request_id": obs.current_request_id()}

    @app.get("/_t/slow")
    async def slow() -> dict:
        await asyncio.sleep(random.uniform(0, 0.02))  # noqa: S311 - test jitter only
        return {"request_id": obs.current_request_id()}

    @app.get("/_t/sync")
    def sync() -> dict:
        return {"request_id": obs.current_request_id()}

    @app.get("/_t/items/{item_id}")
    def item(item_id: int) -> dict:
        test_logger.error("fixed failure code")
        return {"ok": True}

    @app.get("/_t/boom")
    def boom() -> dict:
        raise RuntimeError(f"internal detail {MARKER}")

    return app


@pytest.mark.parametrize("value", ["abcdefgh", "A1-b2-C3-d4", "x" * 64, "0" * 36])
def test_a_valid_incoming_id_is_echoed_and_used(app, value):
    response = TestClient(app).get("/_t/echo", headers={"X-Request-ID": value})
    assert response.headers["X-Request-ID"] == value
    assert response.json() == {"request_id": value}


@pytest.mark.parametrize(
    "value", ["short", "x" * 65, "has space here", "semi;colon", "under_score", "dot.dot.dot", ""]
)
def test_an_invalid_incoming_id_is_replaced(app, value):
    response = TestClient(app).get("/_t/echo", headers={"X-Request-ID": value})
    assert UUID4.fullmatch(response.headers["X-Request-ID"])
    assert response.json() == {"request_id": response.headers["X-Request-ID"]}


def test_a_missing_id_is_generated_and_unique(app):
    client = TestClient(app)
    first = client.get("/_t/echo").headers["X-Request-ID"]
    second = client.get("/_t/echo").headers["X-Request-ID"]
    assert UUID4.fullmatch(first) and UUID4.fullmatch(second) and first != second


def test_sync_endpoints_in_the_threadpool_see_the_id(app):
    response = TestClient(app).get("/_t/sync", headers={"X-Request-ID": "sync-id-0001"})
    assert response.json() == {"request_id": "sync-id-0001"}


def test_concurrent_requests_never_mix_ids_and_context_is_reset(app):
    async def run() -> list[tuple[str, httpx.Response]]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            ids = [f"concurrent-{i:04d}" for i in range(40)]
            paths = ["/_t/slow", "/_t/sync"]
            responses = await asyncio.gather(
                *(
                    client.get(paths[i % 2], headers={"X-Request-ID": rid})
                    for i, rid in enumerate(ids)
                )
            )
            return list(zip(ids, responses, strict=True))

    for rid, response in asyncio.run(run()):
        assert response.headers["X-Request-ID"] == rid
        assert response.json() == {"request_id": rid}
    assert obs.current_request_id() is None


def test_one_completion_line_per_request_with_route_template_only(app, formatted_logs):
    client = TestClient(app)
    client.get("/_t/items/987654", headers={"X-Request-ID": "route-test-01"})
    client.get(f"/no/such/{MARKER}?q={MARKER}", headers={"X-Request-ID": "route-test-02"})
    text = formatted_logs()
    events = completion_events(text)
    assert [(e["request_id"], e["route"], e["status"], e["method"]) for e in events] == [
        ("route-test-01", "/_t/items/{item_id}", 200, "GET"),
        ("route-test-02", obs.UNMATCHED_ROUTE, 404, "GET"),
    ]
    assert all(isinstance(e["duration_ms"], float) for e in events)
    assert "987654" not in text and MARKER not in text and "/no/such" not in text


def test_existing_style_error_logs_carry_the_request_id(app, formatted_logs):
    TestClient(app).get("/_t/items/1", headers={"X-Request-ID": "error-log-id-1"})
    (line,) = [ln for ln in formatted_logs().splitlines() if "fixed failure code" in ln]
    assert "request_id=error-log-id-1" in line


def test_logs_outside_a_request_use_a_placeholder(formatted_logs):
    test_logger.warning("outside any request")
    (line,) = [ln for ln in formatted_logs().splitlines() if "outside any request" in ln]
    assert f"request_id={obs.NO_REQUEST_ID} " in line


def test_validation_errors_keep_their_body_and_get_the_header(app, formatted_logs):
    response = TestClient(app).get("/_t/items/not-a-number")
    assert response.status_code == 422
    assert UUID4.fullmatch(response.headers["X-Request-ID"])
    assert response.json()["detail"][0]["loc"] == ["path", "item_id"]
    (event,) = completion_events(formatted_logs())
    assert event["status"] == 422 and event["route"] == "/_t/items/{item_id}"


def test_unhandled_500_has_the_plain_body_the_header_and_no_detail(app, formatted_logs):
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_t/boom", headers={"X-Request-ID": "boom-request-1"})
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["X-Request-ID"] == "boom-request-1"
    assert MARKER not in response.text
    text = formatted_logs()
    (event,) = completion_events(text)
    assert (event["request_id"], event["status"], event["route"]) == (
        "boom-request-1",
        500,
        "/_t/boom",
    )
    assert MARKER not in text


def test_unhandled_500_body_matches_starlette_without_the_middleware():
    # The middleware must not change the 500 body Starlette sends on its own.
    from fastapi import FastAPI

    bare = FastAPI()

    @bare.get("/boom")
    def boom() -> dict:
        raise RuntimeError("x")

    bare_response = TestClient(bare, raise_server_exceptions=False).get("/boom")
    assert bare_response.status_code == 500 and bare_response.text == "Internal Server Error"
    assert bare_response.headers["content-type"] == "text/plain; charset=utf-8"


def test_an_unusual_method_is_logged_as_a_fixed_label(app, formatted_logs):
    TestClient(app).request("PROPFIND", "/_t/echo")
    (event,) = completion_events(formatted_logs())
    assert event["method"] == "OTHER" and event["status"] == 405


def test_api_paths_are_unchanged_by_the_middleware(make_settings):
    paths = set(create_app(make_settings()).openapi()["paths"])
    assert not any(p.startswith("/_t") for p in paths)
    assert {"/search/reranked", "/health"} <= paths


# ---- uvicorn access log -----------------------------------------------------------------

ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'


def test_uvicorn_access_log_format_is_the_one_the_filter_expects():
    root = Path(uvicorn.__file__).parent / "protocols" / "http"
    for name in ("h11_impl.py", "httptools_impl.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert f"'{ACCESS_FORMAT}'" in source, name
        assert "get_path_with_query_string(self.scope)" in source, name


def test_access_log_lines_lose_the_query_string_and_client_address(make_settings):
    create_app(make_settings())  # installs the filter once
    create_app(make_settings())
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, obs.AccessLogRedactionFilter) for f in access.filters) == 1
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    access.addHandler(handler)
    level = access.level
    access.setLevel(logging.INFO)
    try:
        access.info(
            ACCESS_FORMAT,
            "203.0.113.9:51234",
            "GET",
            f"/search/reranked?q={MARKER}&top_k=5",
            "1.1",
            200,
        )
    finally:
        access.removeHandler(handler)
        access.setLevel(level)
    assert stream.getvalue().strip() == '- - "GET /search/reranked HTTP/1.1" 200'


def test_served_page_and_assets_are_logged_by_template_not_file_name(
    make_settings, tmp_path, formatted_logs
):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    client = TestClient(create_app(make_settings(frontend_dist_dir=dist)))
    assert client.get("/").status_code == 200
    assert client.get("/assets/app-abc123.js").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
    text = formatted_logs()
    assert [(e["route"], e["status"]) for e in completion_events(text)] == [
        ("/", 200),
        ("/assets/{path}", 200),
        ("/assets/{path}", 404),
    ]
    assert "app-abc123" not in text and "missing.js" not in text
