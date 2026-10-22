"""Request IDs, one completion log per request, and query-free logs (Milestone 24A).

Nothing here logs a query string, request or response body, header value, client address or
exception text. The request ID is the only caller-influenced value written, and only after it
matches `REQUEST_ID_PATTERN`.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{8,64}")
# Route label for requests no route matched; the raw URL is never logged.
UNMATCHED_ROUTE = "unmatched"
KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
NO_REQUEST_ID = "-"

request_logger = logging.getLogger("ecommerce_search.request")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    """The ID of the request being handled in this context, or None outside a request."""
    return _request_id.get()


def accept_or_generate(value: str | None) -> str:
    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """One structured (JSON) log line. Callers pass fixed codes and numbers only."""
    record = {"event": event, "request_id": current_request_id(), **fields}
    logger.info(json.dumps(record, separators=(",", ":"), sort_keys=True))


class RequestIdFilter(logging.Filter):
    """Adds `record.request_id` (or "-") so every formatted line can carry it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or NO_REQUEST_ID
        return True


def build_log_handler(stream=None) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestIdFilter())
    return handler


def configure_logging(level: str) -> None:
    """Like the former `logging.basicConfig`: acts only when the root logger has no handler."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.addHandler(build_log_handler())
    root.setLevel(level)


class AccessLogRedactionFilter(logging.Filter):
    """Removes the query string and client address from uvicorn's access log lines.

    uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with (client, method, path?query, version, status);
    `GET /search?q=...` would otherwise write the search text to the log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            client, method, path, version, status = args
            record.args = ("-", method, path.split("?", 1)[0], version, status)
        return True


def install_access_log_redaction() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogRedactionFilter) for f in access.filters):
        access.addFilter(AccessLogRedactionFilter())


def _route_label(scope: Scope) -> str:
    path = getattr(scope.get("route"), "path", None)
    if isinstance(path, str):
        return path
    # FastAPI does not set "route" for a Mount (the built UI's /assets). A matched Mount extends
    # root_path by its own fixed prefix, which is a template, not the requested file name.
    # "app_root_path" is only present once a Mount matched (a proxy root_path alone is ignored).
    app_root = scope.get("app_root_path")
    root = scope.get("root_path", "")
    if isinstance(root, str) and isinstance(app_root, str) and len(root) > len(app_root):
        return root[len(app_root) :] + "/{path}"
    return UNMATCHED_ROUTE


class RequestIdMiddleware:
    """Assigns a request ID, returns it on every response and logs one completion line.

    The ID lives in a context variable set for this request only and reset afterwards, so
    concurrent requests never share one. An unhandled exception gets Starlette's usual plain
    500 body (no exception detail) plus the ID header; the exception is then re-raised so the
    server's own error handling is unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = accept_or_generate(incoming)
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status: int | None = None

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            if status is None:
                response = PlainTextResponse("Internal Server Error", status_code=500)
                await response(scope, receive, send_with_id)
            raise
        finally:
            method = scope.get("method", "")
            log_event(
                request_logger,
                "request_completed",
                method=method if method in KNOWN_METHODS else "OTHER",
                route=_route_label(scope),
                # None only when no response started (for example a cancelled request).
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            _request_id.reset(token)
