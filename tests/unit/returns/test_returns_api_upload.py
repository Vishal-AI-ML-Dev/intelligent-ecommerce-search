"""M18-04B: the validate and import routes, and their bounded in-memory multipart reading.

Hermetic: no database is reached. The import service is replaced by a recording fake, except
where the real service is called on uploads that fail Phase A (which never touches the
database); the app engine is then a sentinel that fails if used. Tokens are generated per run.
"""

import asyncio
import logging
import secrets
import tempfile
from datetime import UTC, datetime

import anyio.to_thread
import pytest
from fastapi import params
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from ecommerce_search.api import returns as api
from ecommerce_search.api import returns_upload as upload
from ecommerce_search.api.app import create_app
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.service import ImportReport, ImportRoleError, ImportStorageError

VIEWER = secrets.token_urlsafe(32)
IMPORTER = secrets.token_urlsafe(32)
DB_PASSWORD = "importer-pw-test-not-real-4b2e"  # noqa: S105 - fake value, never a credential
LABEL = "local-importer"
VALIDATE = "/returns/import/validate"
IMPORT = "/returns/import"
BOUNDARY = "b0undary-test-4b"
MULTIPART = f"multipart/form-data; boundary={BOUNDARY}"
HOSTILE_NAME = "zq<script>-9876543210@example.com"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def app_logs(caplog) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(("ecommerce_search", "uvicorn", "fastapi", "starlette"))
    )


def assert_safe_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert body == {"detail": api.DETAILS[code], "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    return body


def part(name: str | None, data: bytes, *, headers: list[str] | None = None) -> bytes:
    lines = (
        headers
        if headers is not None
        else [
            f'Content-Disposition: form-data; name="{name}"; filename="{HOSTILE_NAME}.json"',
            "Content-Type: application/octet-stream",
        ]
    )
    return ("\r\n".join([f"--{BOUNDARY}", *lines, "", ""])).encode() + data + b"\r\n"


def body(*parts: bytes, close: bool = True) -> bytes:
    return b"".join(parts) + (f"--{BOUNDARY}--\r\n".encode() if close else b"")


def report(status: str = "valid", mode: str = "validate") -> ImportReport:
    return ImportReport(
        mode=mode,
        status=status,
        bundle_sha256="0" * 64,
        checked_at=datetime(2026, 10, 27, tzinfo=UTC),
        counts={},
        phases_completed=(),
        error_count=0,
        error_count_is_lower_bound=False,
        errors_truncated=False,
        errors=(),
    )


# ---- the reader, at the ASGI level -------------------------------------------------------------


def make_request(chunks: list[bytes], *, delay: float = 0.0, disconnect: bool = False):
    """A request whose body arrives in `chunks`; records how many chunks were consumed."""
    consumed: list[int] = []

    async def receive():
        if delay:
            await asyncio.sleep(delay)
        index = len(consumed)
        consumed.append(index)
        if disconnect and index == len(chunks):
            return {"type": "http.disconnect"}
        more = index < len(chunks) - 1 or disconnect
        data = chunks[index] if index < len(chunks) else b""
        return {"type": "http.request", "body": data, "more_body": more}

    scope = {"type": "http", "method": "POST", "path": VALIDATE, "headers": []}
    return Request(scope, receive), consumed


def read(chunks: list[bytes], **kwargs) -> list[Part]:
    request, _ = make_request(chunks, **kwargs)
    return asyncio.run(upload.read_upload(request, BOUNDARY.encode()))


def assert_upload_error(status: int, code: str, chunks: list[bytes], **kwargs) -> None:
    with pytest.raises(upload.UploadError) as caught:
        read(chunks, **kwargs)
    assert (caught.value.status, caught.value.code) == (status, code)


def test_parts_are_collected_by_field_name_and_client_file_names_are_ignored():
    data = body(part("manifest.json", b'{"a": 1}'), part("orders.csv", b"x,y\r\n1,2\r\n"))
    parts = read([data])
    assert parts == [Part("manifest.json", b'{"a": 1}'), Part("orders.csv", b"x,y\r\n1,2\r\n")]


def test_parts_split_across_many_small_chunks_are_reassembled():
    payload = bytes(range(256)) * 40 + b"\r\n--not-the-boundary\r\n"
    data = body(part("bundle.json", payload))
    chunks = [data[i : i + 7] for i in range(0, len(data), 7)]
    assert read(chunks) == [Part("bundle.json", payload)]


def test_a_body_without_the_closing_boundary_is_malformed():
    assert_upload_error(400, "malformed_upload", [body(part("bundle.json", b"{}"), close=False)])


def test_an_empty_body_is_malformed():
    assert_upload_error(400, "malformed_upload", [b""])


def test_starting_a_13th_part_rejects_at_once_without_reading_the_rest():
    head = b"".join(part(f"p{i}.csv", b"x") for i in range(12))
    thirteenth = part("p12.csv", b"x")
    request, consumed = make_request([head, thirteenth, b"never read", b"never read"])
    with pytest.raises(upload.UploadError) as caught:
        asyncio.run(upload.read_upload(request, BOUNDARY.encode()))
    assert (caught.value.status, caught.value.code) == (400, "malformed_upload")
    assert len(consumed) == 2


def test_twelve_parts_are_accepted():
    data = body(*(part(f"p{i}.csv", b"x") for i in range(12)))
    assert len(read([data])) == 12


def test_a_body_over_the_cap_is_refused_while_streaming(monkeypatch):
    monkeypatch.setattr(upload, "REQUEST_BODY_MAX_BYTES", 1000)
    start = part("bundle.json", b"")[:-2]  # a valid part start; its data continues below
    request, consumed = make_request([start + b"a" * 500, b"a" * 600, b"never read"])
    with pytest.raises(upload.UploadError) as caught:
        asyncio.run(upload.read_upload(request, BOUNDARY.encode()))
    assert (caught.value.status, caught.value.code) == (413, "payload_too_large")
    assert len(consumed) == 2


def test_the_cap_counts_the_epilogue_too(monkeypatch):
    monkeypatch.setattr(upload, "REQUEST_BODY_MAX_BYTES", 1000)
    complete = body(part("bundle.json", b"{}"))
    assert_upload_error(413, "payload_too_large", [complete, b"e" * 1000])


def test_the_read_deadline_is_total_not_per_chunk(monkeypatch):
    monkeypatch.setattr(upload, "BODY_READ_SECONDS", 0.3)
    data = body(part("bundle.json", b"x" * 100))
    # Each chunk arrives well within the deadline; together they do not.
    chunks = [data[i : i + 10] for i in range(0, len(data), 10)]
    assert_upload_error(408, "upload_timeout", chunks, delay=0.05)


def test_the_read_deadline_covers_a_sender_that_sends_no_body_bytes(monkeypatch):
    monkeypatch.setattr(upload, "BODY_READ_SECONDS", 0.2)
    waited: list[bool] = []

    async def silent():
        waited.append(True)
        await asyncio.Event().wait()  # never set: no body chunk ever arrives

    request = Request({"type": "http", "method": "POST", "path": VALIDATE, "headers": []}, silent)
    with pytest.raises(upload.UploadError) as caught:
        asyncio.run(upload.read_upload(request, BOUNDARY.encode()))
    assert (caught.value.status, caught.value.code) == (408, "upload_timeout")
    assert waited == [True]


def test_a_client_disconnect_is_malformed():
    assert_upload_error(400, "malformed_upload", [part("bundle.json", b"{}")], disconnect=True)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        ["Content-Type: application/json"],
        ['Content-Disposition: attachment; name="bundle.json"'],
        ["Content-Disposition: form-data"],
        ['Content-Disposition: form-data; name=""'],
        ["Content-Disposition: form-data; name*=utf-8''bundle.json"],
        ['Content-Disposition: form-data; filename="bundle.json"'],
        [
            'Content-Disposition: form-data; name="bundle.json"',
            'Content-Disposition: form-data; name="orders.csv"',
        ],
        ['Content-Disposition: form-data; name="bundle.json"', "Content-Transfer-Encoding: base64"],
        ['Content-Disposition: form-data; name="bundle.json"', "X: 1", "Y: 2", "Z: 3", "W: 4"],
        ["Bad Header Line"],
    ],
    ids=[
        "no-headers",
        "no-disposition",
        "not-form-data",
        "no-name",
        "empty-name",
        "extended-name-only",
        "filename-only",
        "repeated-disposition",
        "base64-encoding",
        "too-many-headers",
        "parser-error",
    ],
)
def test_invalid_part_headers_are_malformed(headers):
    data = body(part(None, b"{}", headers=headers))
    assert_upload_error(400, "malformed_upload", [data])


@pytest.mark.parametrize("encoding", ["binary", "8bit", "7BIT"])
def test_identity_transfer_encodings_are_accepted(encoding):
    headers = [
        'Content-Disposition: form-data; name="bundle.json"',
        f"Content-Transfer-Encoding: {encoding}",
    ]
    assert read([body(part(None, b"{}", headers=headers))]) == [Part("bundle.json", b"{}")]


def test_an_overlong_boundary_is_malformed():
    request, _ = make_request([b""])
    with pytest.raises(upload.UploadError) as caught:
        asyncio.run(upload.read_upload(request, b"b" * 257))
    assert caught.value.code == "malformed_upload"


@pytest.mark.parametrize(
    "content_type",
    [None, "", "application/json", "text/plain", "multipart/mixed; boundary=x", "form-data"],
)
def test_other_media_types_are_unsupported(content_type):
    with pytest.raises(upload.UploadError) as caught:
        upload.multipart_boundary(content_type)
    assert (caught.value.status, caught.value.code) == (415, "unsupported_media_type")


@pytest.mark.parametrize("content_type", ["multipart/form-data", "multipart/form-data; boundary="])
def test_a_missing_boundary_is_malformed(content_type):
    with pytest.raises(upload.UploadError) as caught:
        upload.multipart_boundary(content_type)
    assert (caught.value.status, caught.value.code) == (400, "malformed_upload")


def test_the_media_type_is_case_insensitive():
    assert upload.multipart_boundary("Multipart/Form-Data; boundary=abc") == b"abc"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (str(9 * 1024 * 1024 + 1), "payload_too_large"),
        ("12x", "malformed_upload"),
        ("-1", "malformed_upload"),
    ],
)
def test_declared_lengths_are_checked_before_reading(value, code):
    with pytest.raises(upload.UploadError) as caught:
        upload.check_declared_length(value)
    assert caught.value.code == code


def test_the_reader_cap_equals_the_published_request_cap():
    assert upload.REQUEST_BODY_MAX_BYTES == api.REQUEST_BODY_MAX_BYTES == 9 * 1024 * 1024


def test_huge_declared_lengths_are_oversize_not_errors():
    for value in ("9" * 5000, "1" + "0" * 30):
        with pytest.raises(upload.UploadError) as caught:
            upload.check_declared_length(value)
        assert caught.value.code == "payload_too_large"


def test_declared_lengths_within_the_cap_pass():
    upload.check_declared_length(None)
    upload.check_declared_length("0" * 40 + "17")
    upload.check_declared_length("0")
    upload.check_declared_length(str(9 * 1024 * 1024))


# ---- the single-flight slot --------------------------------------------------------------------


def test_the_slot_is_released_after_success_and_after_failure():
    slot = upload.ImportSlot()
    assert slot.acquire()
    assert asyncio.run(upload.run_in_slot(slot, lambda: 7)) == 7
    assert not slot.busy

    def fail():
        raise RuntimeError("boom")

    assert slot.acquire()
    with pytest.raises(RuntimeError):
        asyncio.run(upload.run_in_slot(slot, fail))
    assert not slot.busy


def test_a_cancelled_caller_keeps_the_slot_until_the_worker_finishes():
    import threading

    slot = upload.ImportSlot()
    running, release = threading.Event(), threading.Event()
    finished: list[bool] = []

    def work():
        running.set()
        release.wait(5)
        finished.append(True)

    async def scenario():
        assert slot.acquire()
        task = asyncio.create_task(upload.run_in_slot(slot, work))
        while not running.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        held_while_running = slot.busy and not finished
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return held_while_running

    assert asyncio.run(scenario())
    assert finished == [True] and not slot.busy


def test_cancellation_before_the_worker_starts_releases_the_slot(monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(anyio.to_thread, "run_sync", cancelled)
    slot = upload.ImportSlot()
    assert slot.acquire()
    ran: list[bool] = []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(upload.run_in_slot(slot, lambda: ran.append(True)))
    assert not ran and not slot.busy


# ---- the routes ----------------------------------------------------------------------------------


class FakeEngine:
    def __init__(self, url=None) -> None:
        self.url = url

    def dispose(self) -> None:
        pass


class Service:
    """Records every service call; returns or raises what the test sets."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.result: ImportReport | Exception = report()

    def validate(self, engine, parts):
        return self._call("validate", engine, parts, None)

    def commit(self, engine, parts, *, imported_by_label):
        return self._call("commit", engine, parts, imported_by_label)

    def _call(self, mode, engine, parts, label):
        self.calls.append((mode, engine, list(parts), label))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def service(monkeypatch):
    fake = Service()
    monkeypatch.setattr(api, "validate_import", fake.validate)
    monkeypatch.setattr(api, "commit_import", fake.commit)
    monkeypatch.setattr(api, "create_importer_engine", lambda settings, url: FakeEngine(url))
    return fake


@pytest.fixture
def make_client(make_settings, service, caplog):
    caplog.set_level(logging.DEBUG)
    clients = []

    def _make(*, commit: bool = True, **overrides) -> TestClient:
        values = {"returns_viewer_token": VIEWER, "returns_importer_token": IMPORTER}
        if commit:
            values.update(
                returns_import_commit_enabled=True,
                returns_importer_db_user="returns_importer",
                returns_importer_db_password=DB_PASSWORD,
                returns_importer_label=LABEL,
            )
        values.update(overrides)
        client = TestClient(create_app(make_settings(**values)))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def reads(monkeypatch):
    """Records whether the body reader ran."""
    calls: list[bool] = []
    original = api.read_upload

    async def spy(request, boundary):
        calls.append(True)
        return await original(request, boundary)

    monkeypatch.setattr(api, "read_upload", spy)
    return calls


VALID_BODY = body(part("bundle.json", b'{"contract_version": "return-data-v1"}'))


def post(client, path, token=VIEWER, data=VALID_BODY, content_type=MULTIPART, **headers):
    sent = {"Content-Type": content_type, **headers}
    if token is not None:
        sent.update(bearer(token))
    return client.post(path, content=data, headers=sent)


def test_validate_hands_the_parts_to_the_service_with_the_app_engine(client, service):
    response = post(client, VALIDATE)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == report().model_dump(mode="json")
    [(mode, engine, parts, label)] = service.calls
    assert mode == "validate" and engine is client.app.state.engine and label is None
    assert parts == [Part("bundle.json", b'{"contract_version": "return-data-v1"}')]


@pytest.mark.parametrize("token", [VIEWER, IMPORTER])
def test_both_tokens_may_validate(client, service, token):
    assert post(client, VALIDATE, token=token).status_code == 200


@pytest.mark.parametrize(
    ("status", "http"),
    [("imported", 201), ("already_imported", 200), ("no_new_records", 200), ("invalid", 422)],
)
def test_import_status_codes_follow_the_report(client, service, status, http):
    service.result = report(status, "commit")
    response = post(client, IMPORT, token=IMPORTER)
    assert response.status_code == http
    assert response.json() == report(status, "commit").model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store"
    [(mode, engine, _, label)] = service.calls
    assert mode == "commit" and label == LABEL
    assert isinstance(engine, FakeEngine) and engine.url.username == "returns_importer"
    assert engine is not client.app.state.engine


@pytest.mark.parametrize(
    ("path", "token", "status", "code"),
    [
        (VALIDATE, None, 401, "unauthorized"),
        (IMPORT, None, 401, "unauthorized"),
        (VALIDATE, "x" * 40, 401, "unauthorized"),
        (IMPORT, VIEWER, 403, "forbidden"),
    ],
)
def test_access_is_refused_before_the_body_is_read(
    client, service, reads, path, token, status, code
):
    assert_safe_error(post(client, path, token=token), status, code)
    assert not reads and not service.calls


def test_unconfigured_access_fails_closed_before_reading(make_client, service, reads):
    c = make_client(commit=False, returns_viewer_token=None, returns_importer_token=None)
    for path in (VALIDATE, IMPORT):
        assert_safe_error(post(c, path, token=IMPORTER), 503, "retailer_access_not_configured")
    assert not reads and not service.calls


@pytest.mark.parametrize(
    "missing",
    [
        {"returns_import_commit_enabled": False},
        {"returns_importer_db_user": None, "returns_importer_db_password": None},
        {"returns_importer_label": None},
    ],
    ids=["flag-off", "no-db-credentials", "no-label"],
)
def test_a_closed_commit_gate_refuses_import_before_reading(make_client, service, reads, missing):
    c = make_client(**missing)
    assert_safe_error(post(c, IMPORT, token=IMPORTER), 403, "import_commit_disabled")
    assert not reads and not service.calls
    # Validation stays available with the gate closed.
    assert post(c, VALIDATE, token=IMPORTER).status_code == 200


def test_an_unauthenticated_upload_with_a_wrong_media_type_gets_401(client, reads):
    response = post(client, VALIDATE, token=None, content_type="application/json")
    assert_safe_error(response, 401, "unauthorized")


def test_a_wrong_media_type_gets_415_before_the_body_is_read(client, service, reads):
    response = post(client, VALIDATE, content_type="application/json", data=b'{"a": 1}')
    assert_safe_error(response, 415, "unsupported_media_type")
    assert not reads and not service.calls


def test_a_declared_oversize_body_gets_413_before_the_slot_or_the_body(client, service, reads):
    client.app.state.returns_import_slot.acquire()  # busy: 413 must still come first
    try:
        response = post(client, VALIDATE, **{"Content-Length": str(9 * 1024 * 1024 + 1)})
    finally:
        client.app.state.returns_import_slot.release()
    assert_safe_error(response, 413, "payload_too_large")
    assert not reads and not service.calls


def test_a_busy_slot_gets_429_before_the_body_is_read(client, service, reads):
    slot = client.app.state.returns_import_slot
    assert slot.acquire()
    try:
        assert_safe_error(post(client, VALIDATE), 429, "import_busy")
        # 415 comes before 429.
        response = post(client, VALIDATE, content_type="text/plain")
        assert_safe_error(response, 415, "unsupported_media_type")
    finally:
        slot.release()
    assert not reads and not service.calls


def test_a_chunked_body_over_the_cap_gets_413(client, service, monkeypatch):
    monkeypatch.setattr(upload, "REQUEST_BODY_MAX_BYTES", 4096)

    def chunks():
        yield part("bundle.json", b"")
        for _ in range(10):
            yield b"x" * 1024

    response = client.post(
        VALIDATE, content=chunks(), headers={"Content-Type": MULTIPART, **bearer(VIEWER)}
    )
    assert_safe_error(response, 413, "payload_too_large")
    assert not service.calls and not client.app.state.returns_import_slot.busy


@pytest.mark.parametrize(
    "data",
    [
        body(*(part(f"p{i}.csv", b"x") for i in range(13))),
        body(part("bundle.json", b"{}"), close=False),
        body(part(None, b"{}", headers=['Content-Disposition: form-data; name=""'])),
    ],
    ids=["13-parts", "incomplete", "empty-name"],
)
def test_malformed_uploads_get_400_and_never_reach_the_service(client, service, data):
    assert_safe_error(post(client, VALIDATE, data=data), 400, "malformed_upload")
    assert not service.calls and not client.app.state.returns_import_slot.busy


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ImportStorageError(), 503, "import_storage_unavailable"),
        (ImportRoleError("superuser"), 403, "import_commit_disabled"),
        (RuntimeError(f"postgresql://returns_importer:{DB_PASSWORD}@db"), 500, "internal_error"),
    ],
)
def test_service_failures_are_safe_and_release_the_slot(
    client, service, caplog, error, status, code
):
    service.result = error
    response = post(client, IMPORT, token=IMPORTER)
    assert_safe_error(response, status, code)
    assert not client.app.state.returns_import_slot.busy
    assert DB_PASSWORD not in response.text and DB_PASSWORD not in app_logs(caplog)
    assert "postgresql://" not in app_logs(caplog)


def test_the_slot_is_free_again_after_every_outcome(client, service):
    slot = client.app.state.returns_import_slot
    for path, token in ((VALIDATE, VIEWER), (IMPORT, IMPORTER)):
        post(client, path, token=token)
        assert not slot.busy
    assert_safe_error(post(client, VALIDATE, data=b"garbage"), 400, "malformed_upload")
    assert not slot.busy


class Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


CRLF = bytes((13, 10))
MALFORMED_BODIES = [
    b"--" + BOUNDARY.encode() + b"Xgarbage-after-the-boundary" + CRLF,
    body(part(None, b"x", headers=["Bad Header Line"])),
    b"--" + BOUNDARY.encode() + CRLF + b"Content-Disposition: form-data; name=a" + CRLF + b"Z",
]


def test_library_parse_diagnostics_reach_no_handler(client, service, caplog, capfd):
    library = logging.getLogger(upload.LIBRARY_LOGGER)
    direct = Recorder()  # attached to the library logger itself
    root = Recorder()
    library.addHandler(direct)
    logging.getLogger().addHandler(root)
    try:
        # Sanity check: with the logger enabled, these bodies do produce library diagnostics.
        library.disabled = False
        for data in MALFORMED_BODIES:
            post(client, VALIDATE, data=data)
        assert direct.records, "the malformed bodies should trigger library diagnostics"
        upload.silence_library_logs()
        direct.records.clear()
        root.records.clear()
        caplog.clear()
        capfd.readouterr()
        for data in MALFORMED_BODIES:
            assert_safe_error(post(client, VALIDATE, data=data), 400, "malformed_upload")
    finally:
        upload.silence_library_logs()
        library.removeHandler(direct)
        logging.getLogger().removeHandler(root)
    assert not direct.records
    assert not [r for r in root.records if r.name.startswith("python_multipart")]
    assert not [r for r in caplog.records if r.name.startswith("python_multipart")]
    assert "boundary" not in capfd.readouterr().err
    # Application events still reach the handlers.
    assert any(r.name.startswith("ecommerce_search") for r in root.records)


def test_only_the_library_logger_is_configured():
    library = logging.getLogger(upload.LIBRARY_LOGGER)
    assert library.disabled is True and library.propagate is False
    assert any(type(h) is logging.NullHandler for h in library.handlers)
    for name in ("python_multipart", "ecommerce_search", "uvicorn", "uvicorn.access"):
        other = logging.getLogger(name)
        assert other.propagate is True and not other.disabled
    assert not logging.getLogger().disabled


def test_logs_carry_no_names_file_names_tokens_or_bodies(client, service, caplog):
    data = body(part(HOSTILE_NAME, b"secret-body-marker"))
    post(client, VALIDATE, data=data)
    post(client, VALIDATE, data=body(part(None, b"x", headers=["Bad Header Line"])))
    logs = app_logs(caplog)
    for fragment in ("zq<script>", "9876543210", "example.com", "secret-body-marker", VIEWER):
        assert fragment not in logs
    assert '"event":"returns_upload_rejected"' in logs


# ---- the real service on uploads that fail Phase A (no database) -------------------------------


class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError("the database must not be reached")


@pytest.fixture
def real_service_client(make_settings):
    values = {"returns_viewer_token": VIEWER, "returns_importer_token": IMPORTER}
    with TestClient(create_app(make_settings(**values))) as c:
        c.app.state.engine = NoDatabase()
        yield c


def test_unknown_and_repeated_names_are_reported_by_the_service_without_echo(real_service_client):
    data = body(
        part("bundle.json", b"{}"),
        part("bundle.json", b"{}"),
        part(HOSTILE_NAME, b"{}"),
    )
    response = post(real_service_client, VALIDATE, data=data)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "invalid" and result["mode"] == "validate"
    files = {error["file"] for error in result["errors"] if error["code"] == "unexpected_file"}
    assert files == {"unexpected part #2", "unexpected part #3"}
    assert "zq<script>" not in response.text and "example.com" not in response.text


def test_service_size_limits_still_apply_to_parts(real_service_client):
    data = body(part("manifest.json", b" " * (64 * 1024 + 1)))
    result = post(real_service_client, VALIDATE, data=data).json()
    assert {"code": "file_too_large", "file": "manifest.json"}.items() <= result["errors"][
        0
    ].items()


# ---- no temporary-file spill (AC-09) and no form parsing ---------------------------------------


def test_a_large_upload_is_read_without_any_temporary_file(client, service, monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("no temporary file may be created")

    for name in ("TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, refuse)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(Request, "form", refuse)
    payload = b"y" * (3 * 1024 * 1024)  # above python-multipart's 1 MiB in-memory file limit
    response = post(client, VALIDATE, data=body(part("bundle.json", payload)))
    assert response.status_code == 200
    assert service.calls[0][2] == [Part("bundle.json", payload)]
    assert list(tmp_path.iterdir()) == []


def test_no_route_declares_form_or_file_parameters(client):
    for route in client.app.routes:
        if isinstance(route, APIRoute):
            for field in route.dependant.body_params:
                assert not isinstance(field.field_info, params.Form | params.File), route.path
    for route in client.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/returns"):
            assert route.body_field is None, route.path


def test_the_import_routes_document_a_multipart_body(client):
    paths = client.app.openapi()["paths"]
    for path in (VALIDATE, IMPORT):
        content = paths[path]["post"]["requestBody"]["content"]
        assert set(content) == {"multipart/form-data"}


@pytest.mark.parametrize("path", [VALIDATE, IMPORT])
def test_wrong_methods_on_the_import_routes_are_safe_405s(client, service, path):
    response = client.get(f"{path}?token={VIEWER}", headers=bearer(VIEWER))
    assert_safe_error(response, 405, "method_not_allowed")
    assert response.headers["allow"] == "POST"
    assert VIEWER not in response.text and not service.calls


@pytest.mark.parametrize(
    "disposition",
    [
        'form-data; name="x"; name="bundle.json"',
        'form-data; name="bundle.json"; NAME="x"',
        'form-data; name="bundle.json"; Name=x',
        "form-data; name=\"bundle.json\"; name*=utf-8''x",
        "form-data; name*=utf-8''bundle.json",
        'form-data; name=""; name="bundle.json"',
    ],
    ids=[
        "repeated",
        "upper-case-repeat",
        "mixed-case-repeat",
        "name-and-name*",
        "name*-only",
        "empty-first",
    ],
)
def test_ambiguous_or_missing_names_are_malformed(client, service, disposition):
    data = body(part(None, b"{}", headers=[f"Content-Disposition: {disposition}"]))
    assert_safe_error(post(client, VALIDATE, data=data), 400, "malformed_upload")
    assert not service.calls and not client.app.state.returns_import_slot.busy


@pytest.mark.parametrize(
    "disposition",
    [
        'form-data; filename="a; name=x"; name="bundle.json"',
        'form-data; name="bundle.json"; filename="name=1"',
        "form-data;name=bundle.json",
    ],
    ids=["quoted-filename-with-name", "filename-text-name", "no-spaces"],
)
def test_a_single_name_is_accepted_whatever_the_filename_text(client, service, disposition):
    data = body(part(None, b"{}", headers=[f"Content-Disposition: {disposition}"]))
    assert post(client, VALIDATE, data=data).status_code == 200
    assert service.calls[0][2] == [Part("bundle.json", b"{}")]
