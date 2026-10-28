"""M18-04C: the retailer return read routes, hermetically (contracts §7, §7.1).

The database layer (`api/returns_read.py`) is replaced by fakes, so these tests cover the HTTP
contract: access, parameter validation and safe errors, cursors (`filters_sha256`, malformed and
mismatched cursors, page-size changes), response allowlists and the `/returns` OpenAPI snapshot.
`read_snapshot` is checked against a recording fake engine (READ ONLY, REPEATABLE READ, rolled
back). Real SQL, ordering with ties and provenance on stored rows are integration tests
(`tests/integration/test_returns_api_read_integration.py`).
"""

import base64
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from ecommerce_search.api import returns as api
from ecommerce_search.api import returns_read
from ecommerce_search.api import returns_schemas as rs
from ecommerce_search.api.app import create_app

VIEWER = secrets.token_urlsafe(32)
IMPORTER = secrets.token_urlsafe(32)
OTHER = secrets.token_urlsafe(32)
HOSTILE = "zq<script>"
LIST, BATCHES = "/returns", "/returns/import-batches"
DETAIL = "/returns/records/synthetic-returns/1/SYN-RET-00001"
BATCH_ID = UUID("6f1d2a8e-3c4b-4d5e-8f90-a1b2c3d4e5f6")
BATCH = f"{BATCHES}/{BATCH_ID}"
READ_ROUTES = (LIST, DETAIL, BATCHES, BATCH)
SNAPSHOT = Path(__file__).with_name("returns_openapi_snapshot.json")
T0 = datetime(2026, 2, 8, 10, 15, tzinfo=UTC)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def app_logs(caplog) -> str:
    return "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(("ecommerce_search", "uvicorn", "fastapi", "starlette"))
    )


def assert_safe_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code and body["detail"] == api.DETAILS[code]
    assert set(body) <= {"detail", "code", "parameters"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    return body


# ---- fakes -----------------------------------------------------------------------------------


def summary(n: int, *, requested_at: datetime = T0, version: int = 1) -> rs.ReturnSummary:
    return rs.ReturnSummary(
        ref=rs.ReturnRef(
            dataset_id="synthetic-returns", dataset_version=version, return_id=f"SYN-RET-{n:05d}"
        ),
        requested_at=requested_at,
        status="requested",
        quantity=1,
        customer_reason_code="SIZE_TOO_SMALL",
        product=rs.ProductRef(
            product_id="SYN-SHO-0001",
            sku="SYN-SHO-0001",
            title="Shoe",
            category="shoes",
            brand="B",
        ),
        order_id=f"SYN-ORD-{n:05d}",
        order_item_id=f"SYN-OI-{n:05d}",
        comment_count=0,
        inspection_count=0,
        is_synthetic=True,
        provenance=provenance(f"SYN-RET-{n:05d}"),
    )


def batch(n: int, *, imported_at: datetime = T0) -> rs.BatchSummary:
    return rs.BatchSummary(
        batch_id=UUID(int=n),
        dataset_id="synthetic-returns",
        dataset_version=1,
        contract_version="return-data-v1",
        transformation_version="returns-import-1",
        redaction_version="redaction-1",
        bundle_sha256="a" * 64,
        format="json",
        file_count=1,
        total_bytes=10,
        counts={
            e: rs.BatchEntityCounts(received=1, new=1, unchanged=0) for e in api.contracts.Entity
        },
        imported_by_label="local-importer",
        imported_at=imported_at,
    )


class FakeRead:
    """Stands in for `returns_read`: records each call and returns `limit + 1`-style rows."""

    def __init__(self) -> None:
        self.returns: list[rs.ReturnSummary] = [summary(n) for n in range(1, 4)]
        self.batches: list[rs.BatchSummary] = [batch(n) for n in range(1, 4)]
        self.detail: rs.ReturnDetail | None = None
        self.batch_detail: rs.BatchDetail | None = None
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def list_returns(self, engine, filters, after):
        self._maybe_fail()
        self.calls.append(("list_returns", filters, after))
        return self.returns[: filters.limit + 1]

    def get_return(self, engine, dataset_id, dataset_version, return_id):
        self._maybe_fail()
        self.calls.append(("get_return", dataset_id, dataset_version, return_id))
        return self.detail

    def list_batches(self, engine, limit, after):
        self._maybe_fail()
        self.calls.append(("list_batches", limit, after))
        return self.batches[: limit + 1]

    def get_batch(self, engine, batch_id):
        self._maybe_fail()
        self.calls.append(("get_batch", batch_id))
        return self.batch_detail


@pytest.fixture
def read(monkeypatch):
    fake = FakeRead()
    for name in ("list_returns", "get_return", "list_batches", "get_batch"):
        monkeypatch.setattr(returns_read, name, getattr(fake, name))
    return fake


@pytest.fixture
def make_client(make_settings):
    clients: list[TestClient] = []

    def _make(**overrides) -> TestClient:
        values = {"returns_viewer_token": VIEWER, "returns_importer_token": IMPORTER}
        values.update(overrides)
        test_client = TestClient(create_app(make_settings(**values)))
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield _make
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def get(client, path, token=VIEWER, **params):
    return client.get(path, params=params or None, headers=bearer(token))


# ---- access (AC-11, the 7-route matrix together with the 04A/04B tests) -----------------------


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_fail_closed_without_configured_tokens(make_client, read, path):
    client = make_client(returns_viewer_token=None, returns_importer_token=None)
    assert_safe_error(get(client, path), 503, "retailer_access_not_configured")
    assert not read.calls


@pytest.mark.parametrize("path", READ_ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, bearer(OTHER), {"Authorization": f"Basic {VIEWER}"}, {"Authorization": "Bearer"}],
    ids=["missing", "unknown", "wrong-scheme", "empty"],
)
def test_read_routes_refuse_missing_or_unknown_tokens(client, read, path, headers):
    response = client.get(path, headers=headers)
    assert_safe_error(response, 401, "unauthorized")
    assert response.headers["www-authenticate"] == "Bearer"
    assert not read.calls


@pytest.mark.parametrize("token", [VIEWER, IMPORTER], ids=["viewer", "importer"])
def test_both_tokens_carry_the_read_scope(client, read, token):
    read.detail = detail_model()
    read.batch_detail = rs.BatchDetail(**batch(1).model_dump(), files=[])
    for path in READ_ROUTES:
        response = get(client, path, token)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store"


def test_access_is_checked_before_read_parameters(client, read):
    response = client.get(LIST, params={"limit": HOSTILE})
    assert_safe_error(response, 401, "unauthorized")
    assert HOSTILE not in response.text and not read.calls


# ---- parameters (E1, E4, D6a) and safe errors --------------------------------------------------


@pytest.mark.parametrize(
    ("params", "name", "code"),
    [
        ({"limit": "0"}, "limit", "greater_than_equal"),
        ({"limit": "101"}, "limit", "less_than_equal"),
        ({"limit": HOSTILE}, "limit", "int_parsing"),
        ({"status": "refunded"}, "status", "enum"),
        ({"customer_reason_code": "FRAUD"}, "customer_reason_code", "enum"),
        ({"category": "toaster"}, "category", "enum"),
        ({"dataset_id": "Bad_Id"}, "dataset_id", "format"),
        ({"dataset_version": "0"}, "dataset_version", "greater_than_equal"),
        ({"product_id": "-bad"}, "product_id", "format"),
        ({"batch_id": HOSTILE}, "batch_id", "uuid_parsing"),
        ({"requested_from": "2026-02-08T10:15:00"}, "requested_from", "format"),
        ({"requested_to": "2026-02-08"}, "requested_to", "format"),
        ({"has_comment": "maybe"}, "has_comment", "bool_parsing"),
    ],
)
def test_invalid_list_parameters_are_named_without_their_values(client, read, params, name, code):
    response = get(client, LIST, **params)
    body = assert_safe_error(response, 422, "invalid_parameter")
    assert body["parameters"] == [{"name": name, "code": code}]
    for value in params.values():
        assert value not in response.text
    assert not read.calls


@pytest.mark.parametrize(
    ("path", "name", "code"),
    [
        ("/returns/records/Bad_Id/1/SYN-RET-00001", "dataset_id", "format"),
        (
            "/returns/records/synthetic-returns/0/SYN-RET-00001",
            "dataset_version",
            "greater_than_equal",
        ),
        ("/returns/records/synthetic-returns/x/SYN-RET-00001", "dataset_version", "int_parsing"),
        ("/returns/records/synthetic-returns/1/-bad", "return_id", "format"),
        ("/returns/records/synthetic-returns/1/a%0A", "return_id", "format"),
        (f"{BATCHES}/zq-not-a-uuid", "batch_id", "uuid_parsing"),
    ],
)
def test_malformed_path_parameters_are_422s(client, read, path, name, code):
    body = assert_safe_error(get(client, path), 422, "invalid_parameter")
    assert body["parameters"] == [{"name": name, "code": code}]
    assert not read.calls


def test_unknown_records_are_404s_with_fixed_texts(client, read):
    assert_safe_error(get(client, DETAIL), 404, "return_not_found")
    assert_safe_error(get(client, BATCH), 404, "batch_not_found")
    assert read.calls == [
        ("get_return", "synthetic-returns", 1, "SYN-RET-00001"),
        ("get_batch", BATCH_ID),
    ]


@pytest.mark.parametrize("path", READ_ROUTES)
def test_database_errors_are_safe_500s_that_log_only_the_type(client, read, path, caplog):
    caplog.set_level(logging.DEBUG)
    read.error = OperationalError("SELECT secret FROM x", {"p": HOSTILE}, Exception("conn pw=1"))
    response = get(client, path)
    assert_safe_error(response, 500, "internal_error")
    logs = app_logs(caplog)
    for fragment in (HOSTILE, "SELECT secret", "pw=1"):
        assert fragment not in response.text and fragment not in logs
    assert '"error_type":"OperationalError"' in logs


def test_a_broken_listing_snapshot_is_a_safe_500(client, monkeypatch):
    def broken(*args):
        raise returns_read.SnapshotContractError()

    monkeypatch.setattr(returns_read, "get_return", broken)
    assert_safe_error(get(client, DETAIL), 500, "internal_error")


# ---- filters_sha256 (E3) ---------------------------------------------------------------------


def filters(**values) -> rs.ReturnFilters:
    return rs.ReturnFilters.model_validate(values)


def test_filters_hash_excludes_limit_cursor_and_absent_filters():
    base = rs.filters_sha256(filters(status="received").canonical_filters())
    assert base == rs.filters_sha256(filters(status="received", limit=7).canonical_filters())
    assert base == rs.filters_sha256(
        filters(status="received", cursor="anything").canonical_filters()
    )
    assert base != rs.filters_sha256(filters().canonical_filters())
    assert base != rs.filters_sha256(filters(status="closed").canonical_filters())


def test_filters_hash_normalizes_timestamps_to_utc():
    a = filters(requested_from="2026-02-08T15:45:00+05:30", requested_to="2026-03-01T00:00:00Z")
    b = filters(
        requested_from="2026-02-08T10:15:00.000000Z", requested_to="2026-02-28T19:00:00-05:00"
    )
    assert (
        a.canonical_filters()
        == b.canonical_filters()
        == {
            "requested_from": "2026-02-08T10:15:00.000000Z",
            "requested_to": "2026-03-01T00:00:00.000000Z",
        }
    )
    assert rs.filters_sha256(a.canonical_filters()) == rs.filters_sha256(b.canonical_filters())


def test_canonical_filters_are_the_validated_values_used_by_the_query():
    f = filters(
        dataset_id="synthetic-returns",
        dataset_version="2",
        status="received",
        customer_reason_code="ARRIVED_DAMAGED",
        category="shoes",
        product_id="SYN-SHO-0001",
        has_comment="true",
        has_inspection="false",
        batch_id=str(BATCH_ID).upper(),
    )
    assert f.canonical_filters() == {
        "dataset_id": "synthetic-returns",
        "dataset_version": 2,
        "status": "received",
        "customer_reason_code": "ARRIVED_DAMAGED",
        "category": "shoes",
        "product_id": "SYN-SHO-0001",
        "has_comment": True,
        "has_inspection": False,
        "batch_id": str(BATCH_ID),
    }
    clauses, params = returns_read._filter_sql(f)
    assert params == {
        "dataset_id": "synthetic-returns",
        "dataset_version": 2,
        "status": "received",
        "customer_reason_code": "ARRIVED_DAMAGED",
        "category": "shoes",
        "product_id": "SYN-SHO-0001",
        "batch_id": BATCH_ID,
    }
    assert len(clauses) == 9 and clauses[-1].startswith("NOT EXISTS")
    assert not any(str(value) in " ".join(clauses) for value in params.values())


# ---- cursors ---------------------------------------------------------------------------------

SHA = rs.filters_sha256({})


def position(**over) -> rs.ReturnPosition:
    values = {
        "requested_at": T0,
        "dataset_id": "synthetic-returns",
        "dataset_version": 1,
        "return_id": "SYN-RET-00001",
    }
    return rs.ReturnPosition(**(values | over))


def raw_cursor(payload: dict, *, sort: bool = True, pad: bool = False) -> str:
    blob = json.dumps(payload, sort_keys=sort, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(blob).decode()
    return encoded if pad else encoded.rstrip("=")


def payload(**over) -> dict:
    values = {
        "v": 1,
        "requested_at": "2026-02-08T10:15:00.000000Z",
        "dataset_id": "synthetic-returns",
        "dataset_version": 1,
        "return_id": "SYN-RET-00001",
        "filters_sha256": SHA,
    }
    return {k: v for k, v in (values | over).items() if v is not ...}


def test_a_return_cursor_round_trips_and_has_one_encoding():
    p = position(
        requested_at=datetime(
            2026, 2, 8, 15, 45, 0, 123, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
    )
    cursor = rs.return_cursor(p, SHA)
    assert rs.decode_return_cursor(cursor, SHA) == p
    assert cursor == raw_cursor(payload(requested_at="2026-02-08T10:15:00.000123Z"))


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!!!",
        "a" * (rs.CURSOR_MAX_LENGTH + 1),
        "eyJ2IjoxfQ",  # {"v":1}
        raw_cursor(payload(), pad=True),
        raw_cursor(payload(), sort=False),
        raw_cursor(payload(v=2)),
        raw_cursor(payload(v=True)),
        raw_cursor(payload(extra=1)),
        raw_cursor(payload(return_id=...)),
        raw_cursor(payload(requested_at="2026-02-08T15:45:00+05:30")),  # not canonical
        raw_cursor(payload(requested_at="2026-02-30T00:00:00.000000Z")),
        raw_cursor(payload(requested_at=1)),
        raw_cursor(payload(dataset_id="Bad")),
        raw_cursor(payload(dataset_version=0)),
        raw_cursor(payload(dataset_version=True)),
        raw_cursor(payload(dataset_version="1")),
        raw_cursor(payload(return_id="-x")),
        raw_cursor(payload(filters_sha256="A" * 64)),
        base64.urlsafe_b64encode(b"\xff\xfe").decode().rstrip("="),
        base64.urlsafe_b64encode(b"[1]").decode().rstrip("="),
    ],
)
def test_malformed_return_cursors_are_refused(cursor):
    with pytest.raises(rs.CursorError) as caught:
        rs.decode_return_cursor(cursor, SHA)
    assert caught.value.code == "malformed_cursor"


def test_a_cursor_from_other_filters_is_a_mismatch():
    cursor = rs.return_cursor(position(), rs.filters_sha256({"status": "received"}))
    with pytest.raises(rs.CursorError) as caught:
        rs.decode_return_cursor(cursor, SHA)
    assert caught.value.code == "cursor_mismatch"


def test_batch_cursors_round_trip_and_refuse_return_cursors():
    p = rs.BatchPosition(imported_at=T0, batch_id=BATCH_ID)
    assert rs.decode_batch_cursor(rs.batch_cursor(p, SHA), SHA) == p
    for bad in (
        rs.return_cursor(position(), SHA),
        rs.encode_cursor(
            {
                "imported_at": "2026-02-08T10:15:00.000000Z",
                "batch_id": str(BATCH_ID).upper(),
                "filters_sha256": SHA,
            }
        ),
        rs.encode_cursor(
            {"imported_at": "2026-02-08T10:15:00.000000Z", "batch_id": "x", "filters_sha256": SHA}
        ),
    ):
        with pytest.raises(rs.CursorError) as caught:
            rs.decode_batch_cursor(bad, SHA)
        assert caught.value.code == "malformed_cursor"


# ---- paging through the routes ---------------------------------------------------------------


def test_the_list_pages_with_a_cursor_from_the_last_item(client, read):
    first = get(client, LIST, limit="2", status="requested")
    assert first.status_code == 200
    body = first.json()
    assert [i["ref"]["return_id"] for i in body["items"]] == ["SYN-RET-00001", "SYN-RET-00002"]
    assert body["limit"] == 2 and body["next_cursor"]
    second = get(client, LIST, limit="2", status="requested", cursor=body["next_cursor"])
    assert second.status_code == 200
    _, _, after = read.calls[-1]
    assert after == position(return_id="SYN-RET-00002")
    assert second.json()["next_cursor"] is not None  # the fake returns the same rows again


def test_the_last_page_has_no_cursor(client, read):
    body = get(client, LIST, limit="3").json()
    assert len(body["items"]) == 3 and body["next_cursor"] is None


def test_a_cursor_stays_valid_when_only_the_page_size_changes(client, read):
    cursor = get(client, LIST, limit="1").json()["next_cursor"]
    assert get(client, LIST, limit="50", cursor=cursor).status_code == 200


def test_a_cursor_reused_with_other_filters_is_refused_and_logged_by_code(client, read, caplog):
    caplog.set_level(logging.DEBUG)
    cursor = get(client, LIST, limit="1").json()["next_cursor"]
    calls = len(read.calls)
    response = get(client, LIST, limit="1", status="closed", cursor=cursor)
    assert_safe_error(response, 400, "cursor_mismatch")
    assert cursor not in response.text and cursor not in app_logs(caplog)
    assert '"event":"returns_cursor_rejected"' in app_logs(caplog)
    assert len(read.calls) == calls


def test_a_malformed_cursor_is_refused_without_echo(client, read, caplog):
    caplog.set_level(logging.DEBUG)
    response = get(client, LIST, cursor=HOSTILE)
    assert_safe_error(response, 400, "malformed_cursor")
    assert HOSTILE not in response.text and HOSTILE not in app_logs(caplog)
    assert not read.calls


def test_a_return_cursor_is_malformed_on_the_batch_list(client, read):
    cursor = get(client, LIST, limit="1").json()["next_cursor"]
    assert_safe_error(get(client, BATCHES, cursor=cursor), 400, "malformed_cursor")


def test_the_batch_list_pages_with_its_own_cursor(client, read):
    body = get(client, BATCHES, limit="2").json()
    assert [i["batch_id"] for i in body["items"]] == [str(UUID(int=1)), str(UUID(int=2))]
    get(client, BATCHES, limit="2", cursor=body["next_cursor"])
    assert read.calls[-1] == (
        "list_batches",
        2,
        rs.BatchPosition(imported_at=T0, batch_id=UUID(int=2)),
    )


# ---- READ ONLY, REPEATABLE READ (AC-13 (2), D7) ----------------------------------------------


class RecordingConnection:
    def __init__(self, log: list) -> None:
        self.log = log

    def execution_options(self, **options):
        self.log.append(("options", options))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append(("close",))

    def begin(self):
        log = self.log

        class Tx:
            def __enter__(self):
                log.append(("begin",))

            def __exit__(self, *exc):
                log.append(("end",))

        return Tx()

    def execute(self, statement, params=None):
        self.log.append(("execute", str(statement).split()[:4]))

        class Result:
            def mappings(self):
                return self

            def all(self):
                return []

            def one_or_none(self):
                return None

        return Result()

    def rollback(self):
        self.log.append(("rollback",))


class RecordingEngine:
    def __init__(self) -> None:
        self.log: list = []

    def connect(self):
        return RecordingConnection(self.log)


@pytest.mark.parametrize(
    "call",
    [
        lambda e: returns_read.list_returns(e, filters(), None),
        lambda e: returns_read.get_return(e, "synthetic-returns", 1, "SYN-RET-00001"),
        lambda e: returns_read.list_batches(e, 25, None),
        lambda e: returns_read.get_batch(e, BATCH_ID),
    ],
    ids=["list", "detail", "batches", "batch"],
)
def test_every_read_runs_in_one_read_only_repeatable_read_transaction(call):
    engine = RecordingEngine()
    call(engine)
    log = engine.log
    assert log[0] == ("options", {"isolation_level": "REPEATABLE READ"})
    assert log[1] == ("begin",)
    assert log[2] == ("execute", ["SET", "TRANSACTION", "READ", "ONLY"])
    assert all(entry[0] == "execute" and entry[1][0] == "SELECT" for entry in log[3:-3])
    assert log[-3:] == [("rollback",), ("end",), ("close",)]


def test_capabilities_still_reads_no_database(client, read, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("capabilities must not open a read snapshot")

    monkeypatch.setattr(returns_read, "read_snapshot", refuse)
    assert get(client, "/returns/capabilities").status_code == 200


def test_the_read_module_never_names_the_raw_contents_table():
    source = Path(returns_read.__file__).read_text(encoding="utf-8")
    assert "return_import_file_contents" not in source.replace(
        "`return_import_file_contents` is never referenced", ""
    )
    assert "raw_bytes" not in source


# ---- listing snapshot fields (D6d) -----------------------------------------------------------


def test_snapshot_text_fields_are_read_verbatim_from_the_catalog_line():
    line = json.dumps(
        {"title": " T ", "brand": "B", "category": "shoes", "currency": "INR", "price": "Rs 4,599"}
    )
    assert returns_read._snapshot_fields(line) == {
        "title": " T ",
        "brand": "B",
        "category": "shoes",
        "currency": "INR",
        "price": "Rs 4,599",
    }


SNAPSHOT_TEXT = {"title": "T", "brand": "B", "category": "shoes", "currency": "INR"}


@pytest.mark.parametrize("price", [33999, "Rs 44,999", "33999", 0])
def test_the_snapshot_price_keeps_its_raw_json_type(price):
    """M1: an integer stays an integer and seller text stays text; nothing is coerced."""
    fields = returns_read._snapshot_fields(json.dumps(SNAPSHOT_TEXT | {"price": price}))
    assert fields["price"] == price and type(fields["price"]) is type(price)
    model = rs.ListingSnapshot(
        catalog_dataset_id="synthetic-seed",
        catalog_dataset_version="1",
        raw_sha256="b" * 64,
        matches_current_catalog=False,
        **fields,
    )
    dumped = model.model_dump(mode="json")["price"]
    assert dumped == price and type(dumped) is type(price)


@pytest.mark.parametrize("price", [None, True, 33999.0, 33999.5, [1], {"v": 1}], ids=repr)
def test_a_snapshot_price_of_another_json_type_is_a_contract_error(price):
    with pytest.raises(returns_read.SnapshotContractError):
        returns_read._snapshot_fields(json.dumps(SNAPSHOT_TEXT | {"price": price}))


def test_a_snapshot_line_without_a_price_is_a_contract_error_not_a_null():
    with pytest.raises(returns_read.SnapshotContractError):
        returns_read._snapshot_fields(json.dumps(SNAPSHOT_TEXT))


@pytest.mark.parametrize("price", [True, 1.5, None])
def test_the_snapshot_model_refuses_coercible_prices(price):
    with pytest.raises(ValidationError):
        rs.ListingSnapshot(
            catalog_dataset_id="synthetic-seed",
            catalog_dataset_version="1",
            raw_sha256="b" * 64,
            matches_current_catalog=False,
            **SNAPSHOT_TEXT,
            price=price,
        )


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "[]",
        json.dumps({"title": "T", "brand": "B", "category": "shoes", "price": 1}),
        json.dumps(
            {"title": "T", "brand": None, "category": "shoes", "currency": "INR", "price": 1}
        ),
        json.dumps({"title": 1, "brand": "B", "category": "shoes", "currency": "INR", "price": 1}),
    ],
)
def test_a_snapshot_line_without_a_text_field_is_a_contract_error_not_a_null(line):
    with pytest.raises(returns_read.SnapshotContractError):
        returns_read._snapshot_fields(line)


# ---- response allowlists (AC-05) and the OpenAPI snapshot (D6e) ------------------------------


def provenance(record_id: str, *, batch_id: UUID = BATCH_ID, row: int = 1) -> rs.Provenance:
    return rs.Provenance(
        source_type="synthetic",
        source_name="n",
        license=None,
        source_record_id=record_id,
        dataset_version=1,
        ingestion_timestamp=T0,
        transformation_version="returns-import-1",
        is_synthetic=True,
        batch_id=batch_id,
        source_file="bundle.json",
        source_row=row,
    )


def detail_model() -> rs.ReturnDetail:
    s = summary(1)
    return rs.ReturnDetail(
        ref=s.ref,
        requested_at=T0,
        status="received",
        quantity=1,
        received_at=T0 + timedelta(days=3),
        order=rs.OrderFacts(
            order_id="SYN-ORD-00001",
            customer_ref="SYN-CUST-00001",
            ordered_at=T0,
            currency="INR",
            provenance=provenance("SYN-ORD-00001"),
        ),
        order_item=rs.OrderItemFacts(
            order_item_id="SYN-OI-00001",
            quantity=1,
            unit_price=Decimal("4599"),
            provenance=provenance("SYN-OI-00001"),
        ),
        product=s.product,
        listing_snapshot=rs.ListingSnapshot(
            catalog_dataset_id="synthetic-seed",
            catalog_dataset_version="1",
            raw_sha256="b" * 64,
            matches_current_catalog=True,
            title="Shoe",
            brand="B",
            category="shoes",
            currency="INR",
            price="Rs 4,599",
        ),
        customer_statements=rs.CustomerStatements(
            customer_reason_code="ARRIVED_DAMAGED", comments=[]
        ),
        inspection_observations=[],
        shipment=None,
        related_reviews=[],
        related_complaints=[],
        correction_cases=[],
        evidence_presence=rs.EvidencePresence(
            return_comments=0,
            inspection_observations=0,
            shipment=0,
            delivery_events=0,
            related_reviews=0,
            related_complaints=0,
            correction_cases=0,
        ),
        provenance=provenance("SYN-RET-00001"),
    )


ALLOWLISTS = {
    rs.ReturnSummary: {
        "ref", "requested_at", "status", "quantity", "customer_reason_code", "product",
        "order_id", "order_item_id", "comment_count", "inspection_count", "is_synthetic",
        "provenance",
    },
    rs.ReturnRef: {"dataset_id", "dataset_version", "return_id"},
    rs.ProductRef: {"product_id", "sku", "title", "category", "brand"},
    rs.ReturnPage: {"items", "next_cursor", "limit"},
    rs.ReturnDetail: {
        "ref", "requested_at", "status", "quantity", "received_at", "order", "order_item",
        "product", "listing_snapshot", "customer_statements", "inspection_observations",
        "shipment", "related_reviews", "related_complaints", "correction_cases",
        "evidence_presence", "provenance",
    },
    rs.OrderFacts: {"order_id", "customer_ref", "ordered_at", "currency", "provenance"},
    rs.OrderItemFacts: {"order_item_id", "quantity", "unit_price", "provenance"},
    rs.ListingSnapshot: {
        "catalog_dataset_id", "catalog_dataset_version", "raw_sha256",
        "matches_current_catalog", "title", "brand", "category", "currency", "price",
    },
    rs.CustomerStatements: {"customer_reason_code", "comments"},
    rs.ReturnComment: {"comment_id", "submitted_at", "text", "provenance"},
    rs.InspectionObservation: {
        "observation_id", "observed_at", "inspector_ref", "condition", "note", "provenance",
    },
    rs.Shipment: {
        "shipment_id", "carrier_ref", "status", "shipped_at", "promised_delivery_by",
        "delivered_at", "delivery_events", "provenance",
    },
    rs.DeliveryEvent: {"event_id", "occurred_at", "event_type", "source", "provenance"},
    rs.RelatedReview: {
        "review_id", "product_id", "order_item_id", "rating", "submitted_at", "text", "provenance",
    },
    rs.RelatedComplaint: {
        "complaint_id", "order_id", "order_item_id", "product_id", "opened_at", "channel",
        "customer_topic", "text", "provenance",
    },
    rs.CorrectionCaseRef: {
        "case_id", "focus_area", "status", "opened_at", "evidence_count", "provenance",
    },
    rs.EvidencePresence: {
        "return_comments", "inspection_observations", "shipment", "delivery_events",
        "related_reviews", "related_complaints", "correction_cases",
    },
    rs.Provenance: {
        "source_type", "source_name", "license", "source_record_id", "dataset_version",
        "ingestion_timestamp", "transformation_version", "is_synthetic", "batch_id",
        "source_file", "source_row",
    },
    rs.BatchEntityCounts: {"received", "new", "unchanged"},
    rs.ReturnsErrorBody: {"detail", "code"},
    rs.ParameterError: {"name", "code"},
    rs.ReturnsValidationErrorBody: {"detail", "code", "parameters"},
    rs.BatchSummary: {
        "batch_id", "dataset_id", "dataset_version", "contract_version",
        "transformation_version", "redaction_version", "bundle_sha256", "format", "file_count",
        "total_bytes", "counts", "imported_by_label", "imported_at",
    },
    rs.BatchFile: {"name", "entity", "media_type", "size_bytes", "raw_sha256", "row_count"},
    rs.BatchPage: {"items", "next_cursor", "limit"},
}  # fmt: skip


@pytest.mark.parametrize("model", list(ALLOWLISTS), ids=lambda m: m.__name__)
def test_response_models_have_exact_field_allowlists(model):
    assert set(model.model_fields) == ALLOWLISTS[model]
    assert model.model_config["extra"] == "forbid"


def test_the_batch_detail_adds_only_file_metadata():
    assert set(rs.BatchDetail.model_fields) == ALLOWLISTS[rs.BatchSummary] | {"files"}


def test_no_response_field_names_a_decision_score_or_flag():
    forbidden = ("score", "flag", "fraud", "refund", "fault", "verdict", "decision", "resolv")
    for model in [*ALLOWLISTS, rs.BatchDetail]:
        for name in model.model_fields:
            assert not any(word in name for word in forbidden), (model.__name__, name)


def test_detail_json_uses_canonical_timestamps_and_money(client, read):
    read.detail = detail_model()
    body = get(client, DETAIL).json()
    assert body["requested_at"] == "2026-02-08T10:15:00.000000Z"
    assert body["order_item"]["unit_price"] == "4599.00"
    assert body["provenance"]["license"] is None
    assert body["provenance"]["batch_id"] == str(BATCH_ID)


def returns_openapi(schema: dict) -> dict:
    """The `/returns` operations and every component schema they reach."""
    paths = {p: v for p, v in schema["paths"].items() if api.is_returns_path(p)}
    components = schema["components"]["schemas"]
    needed: set[str] = set()
    pending = [paths]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in needed:
                    needed.add(name)
                    pending.append(components[name])
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return {"paths": paths, "schemas": {name: components[name] for name in sorted(needed)}}


def test_the_returns_openapi_matches_the_committed_snapshot(make_settings):
    current = returns_openapi(create_app(make_settings()).openapi())
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == committed, (
        "The /returns OpenAPI changed; review the change, then regenerate "
        f"{SNAPSHOT.name} (owner review per contracts §7.1 D6e)."
    )


def test_uuid4_batch_ids_and_other_uuid_forms_are_accepted_as_paths(client, read):
    batch_id = uuid4()
    get(client, f"{BATCHES}/{str(batch_id).upper()}")
    assert read.calls[-1] == ("get_batch", batch_id)


def test_read_routes_never_log_query_values(client, read, caplog):
    caplog.set_level(logging.DEBUG)
    get(client, LIST, product_id="SYN-SHO-0001", status="received")
    assert "SYN-SHO-0001" not in app_logs(caplog)


# ---- per-record provenance (AC-04) -----------------------------------------------------------

NESTED_RECORDS = (
    rs.OrderFacts,
    rs.OrderItemFacts,
    rs.ReturnComment,
    rs.InspectionObservation,
    rs.Shipment,
    rs.DeliveryEvent,
    rs.RelatedReview,
    rs.RelatedComplaint,
    rs.CorrectionCaseRef,
)


@pytest.mark.parametrize("model", NESTED_RECORDS, ids=lambda m: m.__name__)
def test_every_nested_source_record_has_its_own_required_provenance(model):
    field = model.model_fields["provenance"]
    assert field.annotation is rs.Provenance and field.is_required()


def test_the_summary_carries_the_returns_own_required_provenance():
    """§7 amendment (owner, 2026-10-28): `ReturnSummary` gains `provenance`."""
    field = rs.ReturnSummary.model_fields["provenance"]
    assert field.annotation is rs.Provenance and field.is_required()


def test_summary_rows_take_provenance_from_the_returns_own_references():
    flat = " ".join((returns_read._RETURN_COLUMNS + returns_read._RETURN_FROM).split())
    for join in (
        "JOIN return_datasets d ON d.id = r.dataset_pk",
        "JOIN return_import_batches rb ON rb.id = r.batch_pk",
        "JOIN return_import_files rf ON rf.id = r.source_file_pk",
    ):
        assert join in flat
    for column in (
        "d.source_type AS pv_source_type",
        "r.source_record_id AS pv_source_record_id",
        "rb.imported_at AS pv_ingestion_timestamp",
        "rb.batch_id AS pv_batch_id",
        "rf.name AS pv_source_file",
        "r.source_row AS pv_source_row",
        "r.is_synthetic AS pv_is_synthetic",
    ):
        assert column in flat


def test_the_summary_json_carries_provenance(client, read):
    item = get(client, LIST, limit="1").json()["items"][0]
    assert item["provenance"]["source_record_id"] == item["ref"]["return_id"]
    assert item["provenance"]["batch_id"] == str(BATCH_ID)


def test_a_nested_rows_provenance_comes_from_its_own_columns_only():
    row = {
        "comment_id": "SYN-RCM-00001",
        "submitted_at": T0,
        "text": "redacted",
        "pv_source_type": "synthetic",
        "pv_source_name": "n",
        "pv_license": "CC0-1.0",
        "pv_source_record_id": "SYN-RCM-00001",
        "pv_dataset_version": 20,
        "pv_ingestion_timestamp": T0 + timedelta(hours=1),
        "pv_transformation_version": "returns-import-1",
        "pv_is_synthetic": True,
        "pv_batch_id": UUID(int=7),
        "pv_source_file": "return_comments.csv",
        "pv_source_row": 9,
    }
    record = returns_read._record(row)
    assert set(record) == {"comment_id", "submitted_at", "text", "provenance"}
    assert record["provenance"] == rs.Provenance(
        source_type="synthetic",
        source_name="n",
        license="CC0-1.0",
        source_record_id="SYN-RCM-00001",
        dataset_version=20,
        ingestion_timestamp=T0 + timedelta(hours=1),
        transformation_version="returns-import-1",
        is_synthetic=True,
        batch_id=UUID(int=7),
        source_file="return_comments.csv",
        source_row=9,
    )


def test_a_nested_row_missing_a_provenance_column_fails_rather_than_borrowing_one():
    with pytest.raises(ValidationError):
        returns_read._record({"comment_id": "SYN-RCM-00001", "pv_source_type": "synthetic"})


@pytest.mark.parametrize(
    "query",
    [
        returns_read._ORDER,
        returns_read._ORDER_ITEM,
        returns_read._COMMENTS,
        returns_read._OBSERVATIONS,
        returns_read._SHIPMENT,
        returns_read._EVENTS,
        returns_read._REVIEWS,
        returns_read._COMPLAINTS,
        returns_read._CASES,
    ],
)
def test_nested_queries_join_provenance_through_the_records_own_references(query):
    flat = " ".join(query.split())
    for join in (
        "JOIN return_datasets pd ON pd.id = x.dataset_pk",
        "JOIN return_import_batches pb ON pb.id = x.batch_pk",
        "JOIN return_import_files pf ON pf.id = x.source_file_pk",
    ):
        assert join in flat
    assert "x.source_record_id AS pv_source_record_id" in flat
    assert "x.source_row AS pv_source_row" in flat


# ---- repeated query parameters (I4) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "query", "names"),
    [
        (LIST, "status=closed&status=received", ["status"]),
        (LIST, "limit=1&limit=2&cursor=a&cursor=a", ["cursor", "limit"]),
        (LIST, f"product_id={HOSTILE}&product_id=SYN-SHO-0001", ["product_id"]),
        (BATCHES, "limit=1&limit=1", ["limit"]),
    ],
)
def test_repeated_query_parameters_are_safe_422s(client, read, path, query, names):
    response = client.get(f"{path}?{query}", headers=bearer(VIEWER))
    body = assert_safe_error(response, 422, "invalid_parameter")
    assert body["parameters"] == [{"name": n, "code": "repeated"} for n in names]
    assert HOSTILE not in response.text and "SYN-SHO-0001" not in response.text
    assert not read.calls


def test_unknown_query_parameters_stay_ignored(client, read):
    response = client.get(f"{LIST}?bogus=1&bogus=2&zq=x", headers=bearer(VIEWER))
    assert response.status_code == 200
    assert read.calls[-1][1].canonical_filters() == {}


def test_repeated_parameters_are_checked_after_access(client, read):
    response = client.get(f"{LIST}?status=closed&status=received")
    assert_safe_error(response, 401, "unauthorized")


# ---- OpenAPI error responses -----------------------------------------------------------------

READ_OPERATIONS = {
    "/returns": {"200", "400", "401", "422", "500", "503"},
    "/returns/records/{dataset_id}/{dataset_version}/{return_id}": {
        "200", "401", "404", "422", "500", "503"
    },
    "/returns/import-batches": {"200", "400", "401", "422", "500", "503"},
    "/returns/import-batches/{batch_id}": {"200", "401", "404", "422", "500", "503"},
}  # fmt: skip


def test_read_routes_document_the_safe_error_shapes_only(make_settings):
    schema = create_app(make_settings()).openapi()
    for path, statuses in READ_OPERATIONS.items():
        responses = schema["paths"][path]["get"]["responses"]
        assert set(responses) == statuses, path
        for status, response in responses.items():
            if status == "200":
                continue
            ref = response["content"]["application/json"]["schema"]["$ref"]
            expected = "ReturnsValidationErrorBody" if status == "422" else "ReturnsErrorBody"
            assert ref == f"#/components/schemas/{expected}", (path, status)
        assert "WWW-Authenticate" in responses["401"]["headers"]
    returns_paths = json.dumps(returns_openapi(schema)["paths"])
    assert "HTTPValidationError" not in returns_paths


def test_04a_and_04b_operations_keep_their_documented_responses(make_settings):
    schema = create_app(make_settings()).openapi()
    paths = schema["paths"]
    assert set(paths["/returns/capabilities"]["get"]["responses"]) == {"200"}
    assert set(paths["/returns/import/validate"]["post"]["responses"]) == {"200"}
    assert set(paths["/returns/import"]["post"]["responses"]) == {"201"}
    # 04B's `ImportReport` counts keep their component name (no collision with batch counts).
    report = schema["components"]["schemas"]["ImportReport"]
    assert "#/components/schemas/EntityCounts" in json.dumps(report)
