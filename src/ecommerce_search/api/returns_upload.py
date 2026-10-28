"""Bounded, in-memory multipart reading for the M18 import routes (contracts §4.4, §6.3; D1).

Only `python_multipart.MultipartParser`'s low-level callbacks are used: it never writes a
temporary file. `request.form()`, FastAPI `Form`/`File` parameters and python-multipart's
`FormParser`/`File` classes (which spill large parts to disk) are never used.

**Bounds while streaming.** The body is counted as it arrives, so a body without
`Content-Length` (chunked) is capped too. Over 9 MiB → 413 `payload_too_large`. The whole read
must finish within 30 s → 408 `upload_timeout`; the deadline starts immediately before waiting
for the first chunk, so it also covers a sender that sends no body bytes, and it is total, not
per chunk. It bounds body reading only, not the import service or the database. Starting a 13th
part → 400 `malformed_upload` at once: the rest of the body is neither read nor parsed, and no
partial bundle reaches the import service (contracts §4.4, §6.3).

**Library logs.** python-multipart logs parse diagnostics that contain input bytes and offsets
(for example "Expected boundary character 45, got 97 at index 2"). Its only logger,
`python_multipart.multipart`, is therefore disabled, stops propagating and gets a `NullHandler`,
so no record from it reaches any handler (contracts §5.1 "never values"). No other logger is
changed; parse failures are still logged by the app as `returns_upload_rejected` with the code.

**Parts.** A part needs `Content-Disposition: form-data` with a non-empty `name`; its
`filename`, its own `Content-Type` and any extended (`name*`) parameter are ignored and never
stored or logged. A missing or invalid disposition, a non-identity transfer encoding, a parser
error or a body that ends before the closing boundary → 400 `malformed_upload`. Well-formed
unknown or repeated names are passed on unchanged; the import service reports them as
`unexpected part #n` without echoing them.

**Memory.** Peak use is roughly the received chunks plus the joined parts (about 2 × 9 MiB) for
the single upload a process accepts at a time. That is an estimate, not a measured bound.
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from email.message import Message

import anyio.to_thread
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect, Request

from ecommerce_search.returns import bundle
from ecommerce_search.returns.bundle import Part

# contracts §4.4; equal to `api.returns.REQUEST_BODY_MAX_BYTES` (checked by a test).
REQUEST_BODY_MAX_BYTES = 9 * 1024 * 1024
# contracts §4.4/§6.3 (C2): total body-read deadline → 408, from before the first chunk.
BODY_READ_SECONDS = 30.0
# contracts §4.4/§6.3 (C1): starting part MAX_PARTS + 1 rejects the whole request (400).
MAX_PARTS = bundle.MAX_FILES
# Per part: Content-Disposition, Content-Type and Content-Transfer-Encoding fit with room to
# spare; each header line stays within the library's default size limit.
MAX_PART_HEADERS = 4
_IDENTITY_ENCODINGS = frozenset({b"7bit", b"8bit", b"binary"})
LIBRARY_LOGGER = "python_multipart.multipart"


def silence_library_logs() -> None:
    """Keeps python-multipart's input-derived diagnostics away from every handler."""
    library = logging.getLogger(LIBRARY_LOGGER)
    # Disabled: no record is created, so no handler anywhere (even one attached to this logger
    # directly) receives one. Not propagating: still nothing reaches the root if re-enabled.
    library.disabled = True
    library.propagate = False
    if not any(isinstance(h, logging.NullHandler) for h in library.handlers):
        # A handler must exist, or logging's last-resort handler would print WARNINGs to stderr.
        library.addHandler(logging.NullHandler())


silence_library_logs()


class UploadError(Exception):
    """An upload refused before the import service ran; `code` is a fixed `/returns` code."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _malformed() -> UploadError:
    return UploadError(400, "malformed_upload")


def multipart_boundary(content_type: str | None) -> bytes:
    """The boundary of a `multipart/form-data` request: 415 for any other media type and 400
    when the boundary is missing."""
    media_type, options = parse_options_header(content_type or "")
    if media_type.lower() != b"multipart/form-data":
        raise UploadError(415, "unsupported_media_type")
    boundary = options.get(b"boundary")
    if not boundary:
        raise _malformed()
    return boundary


def check_declared_length(content_length: str | None) -> None:
    """413 before any byte is read when a declared `Content-Length` is over the cap."""
    if content_length is None:
        return
    if not content_length.isascii() or not content_length.isdigit():
        raise _malformed()
    # Compared by length first: int() refuses strings of more than 4,300 digits.
    digits = content_length.lstrip("0") or "0"
    if len(digits) > len(str(REQUEST_BODY_MAX_BYTES)) or int(digits) > REQUEST_BODY_MAX_BYTES:
        raise UploadError(413, "payload_too_large")


def _name_count(disposition: bytes | None) -> int:
    """How many `name` parameters (any case, `name*` included) a Content-Disposition carries.

    python-multipart keeps only the last of repeated parameters, so the standard-library header
    parser is used for this count alone: a repeated name is ambiguous, so the header is invalid
    (C4; RFC 6266 §4.1). The name itself still comes from python-multipart."""
    header = Message()
    header["content-disposition"] = (disposition or b"").decode("latin-1")
    params = header.get_params(header="content-disposition", failobj=[])
    return sum(1 for key, _ in params[1:] if key.lower() == "name")


class _PartCollector:
    """MultipartParser callbacks that collect each part's name and bytes in memory."""

    def __init__(self) -> None:
        self.parts: list[Part] = []
        self.ended = False
        self._headers: dict[bytes, bytes] = {}
        self._field = bytearray()
        self._value = bytearray()
        self._name: str | None = None
        self._data: list[bytes] = []

    def callbacks(self) -> dict[str, Callable]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_end": self.on_end,
        }

    def on_part_begin(self) -> None:
        if len(self.parts) >= MAX_PARTS:
            raise _malformed()
        self._headers = {}
        self._name = None
        self._data = []

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def on_header_end(self) -> None:
        field = bytes(self._field).lower()
        if field in self._headers:
            raise _malformed()
        self._headers[field] = bytes(self._value)
        self._field.clear()
        self._value.clear()

    def on_headers_finished(self) -> None:
        raw = self._headers.get(b"content-disposition")
        disposition, options = parse_options_header(raw)
        name = options.get(b"name")
        if disposition.lower() != b"form-data" or not name or _name_count(raw) != 1:
            raise _malformed()
        encoding = self._headers.get(b"content-transfer-encoding")
        if encoding is not None and encoding.strip().lower() not in _IDENTITY_ENCODINGS:
            raise _malformed()
        # Header bytes are latin-1 on the wire; the name is only compared with canonical names.
        self._name = name.decode("latin-1")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self._data.append(data[start:end])

    def on_part_end(self) -> None:
        if self._name is None:
            raise _malformed()
        self.parts.append(Part(self._name, b"".join(self._data)))
        self._data = []

    def on_end(self) -> None:
        self.ended = True


async def read_upload(request: Request, boundary: bytes) -> list[Part]:
    """Every part of a complete multipart body, read within the size and time limits."""
    collector = _PartCollector()
    try:
        parser = MultipartParser(boundary, collector.callbacks(), max_header_count=MAX_PART_HEADERS)
    except FormParserError:
        raise _malformed() from None
    received = 0
    try:
        # Entered before the first receive, so a sender that sends nothing is bounded too.
        async with asyncio.timeout(BODY_READ_SECONDS):
            async for chunk in request.stream():
                received += len(chunk)
                if received > REQUEST_BODY_MAX_BYTES:
                    raise UploadError(413, "payload_too_large")
                if chunk:
                    parser.write(chunk)
    except TimeoutError:
        raise UploadError(408, "upload_timeout") from None
    except (FormParserError, ClientDisconnect):
        raise _malformed() from None
    parser.finalize()
    if not collector.ended:  # finalize() does not check for the closing boundary
        raise _malformed()
    return collector.parts


class ImportSlot:
    """Single-flight per process (contracts §4.4): one validate or import at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()


async def run_in_slot[T](slot: ImportSlot, work: Callable[[], T]) -> T:
    """Runs `work` in a worker thread and releases `slot` (already acquired) once it is done.

    The worker is not abandoned when the caller is cancelled (a client that disconnects): the
    slot stays held until the worker actually finishes, and cancelling the request does not
    undo anything the worker did. If cancellation arrives before the worker starts, the worker
    never runs and the slot is released here."""
    started = False

    def guarded() -> T:
        nonlocal started
        started = True
        try:
            return work()
        finally:
            slot.release()

    try:
        return await anyio.to_thread.run_sync(guarded, abandon_on_cancel=False)
    finally:
        if not started:
            slot.release()
