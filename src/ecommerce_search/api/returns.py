"""Retailer return data API (Milestone 18, contracts §6-§7): access control and safe errors.

**Access.** Two static bearer tokens from settings (D1). With neither configured, every
`/returns` route fails closed with 503. A presented token is hashed with SHA-256 and compared in
constant time with both configured digests; the tokens themselves are never kept here, logged or
echoed. Buyer routes neither require nor read these tokens.

**Commit gate.** The API commit path needs four independent conditions together: the importer
token, `RETURNS_IMPORT_COMMIT_ENABLED`, the importer database credentials and
`RETURNS_IMPORTER_LABEL`. The importer token alone never enables writes. Commit uses its own
lazily built engine as the scoped importer role, never the read-only app engine.

**Errors.** `/returns` errors are `{"detail": str, "code": str}` with fixed texts. Validation
errors carry parameter names and codes only, never input values. Any other exception becomes a
500 that logs only the exception type and request ID. `ReturnsRoute` handles errors of matched
routes; `ReturnsPathMiddleware` covers what no route handles (unmatched paths, 405, slash
redirects) and adds `Cache-Control: no-store` to every `/returns` response. Neither touches any
other path.
"""

import hashlib
import hmac
import logging
import re
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ecommerce_search.api.observability import log_event
from ecommerce_search.config import Settings
from ecommerce_search.config.settings import is_returns_token
from ecommerce_search.db.engine import create_importer_engine
from ecommerce_search.returns import bundle, contracts, errors
from ecommerce_search.returns.versions import CONTRACT_VERSION

logger = logging.getLogger(__name__)

PREFIX = "/returns"
# contracts §4.4: the whole HTTP request body, read into memory with a streaming cap.
REQUEST_BODY_MAX_BYTES = 9 * 1024 * 1024
# contracts §4.4: validate and import requests in flight per process.
CONCURRENT_IMPORTS = 1
# Longer Authorization values are refused unread: "Bearer " plus the longest valid token.
AUTHORIZATION_MAX_LENGTH = 7 + 256
NO_STORE = "no-store"
# Set on the request scope by `ReturnsRoute`, so the middleware leaves its responses alone.
_HANDLED = "ecommerce_search.returns.handled"
_PARAMETER_CODE = re.compile(r"[a-z_]{1,40}")


class ReturnsScope(StrEnum):
    READ = "returns:read"
    VALIDATE = "returns:validate"
    IMPORT = "returns:import"


VIEWER_SCOPES = frozenset({ReturnsScope.READ, ReturnsScope.VALIDATE})
IMPORTER_SCOPES = VIEWER_SCOPES | {ReturnsScope.IMPORT}

# Fixed texts per error code; nothing caller-supplied is ever added.
DETAILS = {
    "retailer_access_not_configured": "Retailer access is not configured on this server.",
    "unauthorized": "A valid retailer access token is required.",
    "forbidden": "The access token does not allow this operation.",
    "invalid_parameter": "Invalid request parameters.",
    "not_found": "Not Found",
    "method_not_allowed": "Method Not Allowed",
    "request_failed": "The request could not be processed.",
    "internal_error": "Internal Server Error",
}
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class ReturnsError(Exception):
    """A `/returns` error response with a fixed code and text."""

    def __init__(self, status: int, code: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.headers = headers or {}


def error_response(
    status: int, code: str, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    return JSONResponse(
        {"detail": DETAILS[code], "code": code, **extra},
        status_code=status,
        headers={**(headers or {}), "Cache-Control": NO_STORE},
    )


# ---- access ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitGate:
    """The four conditions of the API commit path (contracts §6, plan §4); all are required."""

    commit_enabled: bool
    importer_token: bool
    importer_db_credentials: bool
    importer_label: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "CommitGate":
        return cls(
            commit_enabled=settings.returns_import_commit_enabled,
            importer_token=settings.returns_importer_token is not None,
            importer_db_credentials=settings.returns_importer_database_url() is not None,
            importer_label=settings.returns_importer_label is not None,
        )

    @property
    def open(self) -> bool:
        return (
            self.commit_enabled
            and self.importer_token
            and self.importer_db_credentials
            and self.importer_label
        )


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


@dataclass(frozen=True)
class ReturnsAccess:
    """SHA-256 digests of the configured tokens (never the tokens) and the commit gate. Settings
    are read once per app, so a changed token takes effect only at restart (contracts §6)."""

    viewer_digest: bytes | None = field(repr=False)
    importer_digest: bytes | None = field(repr=False)
    gate: CommitGate

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReturnsAccess":
        viewer, importer = settings.returns_viewer_token, settings.returns_importer_token
        return cls(
            viewer_digest=None if viewer is None else _digest(viewer.get_secret_value()),
            importer_digest=None if importer is None else _digest(importer.get_secret_value()),
            gate=CommitGate.from_settings(settings),
        )

    @property
    def configured(self) -> bool:
        return self.viewer_digest is not None or self.importer_digest is not None

    def scopes_for(self, token: str) -> frozenset[ReturnsScope] | None:
        """The token's scopes, or None. Both digests are always compared, in constant time."""
        presented = _digest(token)
        absent = bytes(32)  # never a SHA-256 digest in practice; compared only to keep timing
        viewer = hmac.compare_digest(presented, self.viewer_digest or absent)
        importer = hmac.compare_digest(presented, self.importer_digest or absent)
        if importer and self.importer_digest is not None:
            return IMPORTER_SCOPES
        if viewer and self.viewer_digest is not None:
            return VIEWER_SCOPES
        return None


def bearer_token(request: Request) -> str | None:
    """The token of exactly one well-formed `Authorization: Bearer <token>` header, or None.
    Oversized, repeated or malformed values are refused without further processing."""
    values = request.headers.getlist("authorization")
    if len(values) != 1 or len(values[0]) > AUTHORIZATION_MAX_LENGTH:
        return None
    scheme, separator, token = values[0].partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not is_returns_token(token):
        return None
    return token


@dataclass(frozen=True)
class Caller:
    scopes: frozenset[ReturnsScope]


def get_returns_access(request: Request) -> ReturnsAccess:
    return request.app.state.returns_access


def _denied(status: int, code: str, headers: dict[str, str] | None = None) -> ReturnsError:
    log_event(logger, "returns_access_denied", code=code)
    return ReturnsError(status, code, headers)


def require_scope(scope: ReturnsScope) -> Callable[..., Caller]:
    """Dependency: 503 when no token is configured, 401 for a missing, malformed or unknown
    token, 403 when the token lacks `scope` (contracts §6)."""

    def dependency(
        request: Request, access: Annotated[ReturnsAccess, Depends(get_returns_access)]
    ) -> Caller:
        if not access.configured:
            raise _denied(503, "retailer_access_not_configured")
        token = bearer_token(request)
        scopes = None if token is None else access.scopes_for(token)
        if scopes is None:
            raise _denied(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
        if scope not in scopes:
            raise _denied(403, "forbidden")
        return Caller(scopes)

    return dependency


class LazyImporterEngine:
    """The commit engine, built on first use (as the scoped importer role) and disposed at
    shutdown. Exists only while the commit gate is open."""

    def __init__(self, settings: Settings) -> None:
        url = settings.returns_importer_database_url()
        if url is None:
            raise ValueError("the importer database credentials are not configured")
        self._settings = settings
        self._url = url
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(built={self._engine is not None})"

    def get(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = create_importer_engine(self._settings, self._url)
            return self._engine

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


def build_importer_engine(settings: Settings, gate: CommitGate) -> LazyImporterEngine | None:
    return LazyImporterEngine(settings) if gate.open else None


# ---- errors ----------------------------------------------------------------------------------


def _parameters(exc: RequestValidationError) -> list[dict[str, str]]:
    """Parameter names (safe keys only) and error codes; never inputs, messages or contexts."""
    found: set[tuple[str, str]] = set()
    for error in exc.errors():
        loc = error.get("loc") or ()
        name = errors.safe_key(loc[1] if len(loc) > 1 else loc[0] if loc else None)
        kind = error.get("type")
        code = kind if isinstance(kind, str) and _PARAMETER_CODE.fullmatch(kind) else "invalid"
        found.add((name, code))
    return [{"name": name, "code": code} for name, code in sorted(found)]


class ReturnsRoute(APIRoute):
    """Route class of the `/returns` router: maps every error of a matched route to the safe
    `{"detail", "code"}` shape. Buyer routes keep FastAPI's default handlers."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            request.scope[_HANDLED] = True
            try:
                return await handle(request)
            except ReturnsError as exc:
                return error_response(exc.status, exc.code, exc.headers)
            except RequestValidationError as exc:
                return error_response(422, "invalid_parameter", parameters=_parameters(exc))
            except HTTPException as exc:  # Starlette's, so FastAPI's subclass is included
                return error_response(
                    exc.status_code, _HTTP_CODES.get(exc.status_code, "request_failed")
                )
            except Exception as exc:
                log_event(logger, "returns_internal_error", error_type=type(exc).__name__)
                return error_response(500, "internal_error")

        return safe_handler


def is_returns_path(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


class ReturnsPathMiddleware:
    """For `/returns` paths only: `Cache-Control: no-store` on every response, and the safe error
    shape for responses no `/returns` route produced: 404 for an unmatched path or a slash
    redirect (whose `Location` would echo the query string), and 405 (keeping `Allow`). Every
    other path passes through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_returns_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        replacement: JSONResponse | None = None

        async def send_safely(message: Message) -> None:
            nonlocal replacement
            if message["type"] == "http.response.start":
                status = message["status"]
                if not scope.get(_HANDLED) and (status in (404, 405) or 300 <= status < 400):
                    allow = MutableHeaders(scope=message).get("allow")
                    replacement = error_response(
                        405 if status == 405 else 404,
                        "method_not_allowed" if status == 405 else "not_found",
                        {"Allow": allow} if status == 405 and allow else None,
                    )
                    return
                MutableHeaders(scope=message)["Cache-Control"] = NO_STORE
            elif replacement is not None:
                if message["type"] == "http.response.body" and not message.get("more_body"):
                    await send_replacement(replacement)
                return
            await send(message)

        async def send_replacement(replacement: JSONResponse) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": replacement.status_code,
                    "headers": replacement.raw_headers,
                }
            )
            await send({"type": "http.response.body", "body": replacement.body})

        await self.app(scope, receive, send_safely)


# ---- routes ----------------------------------------------------------------------------------


class ReturnsLimits(BaseModel):
    """contracts §4.4."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_body_bytes: int
    bundle_bytes: int
    file_bytes: int
    manifest_bytes: int
    files: int
    bundle_rows: int
    entity_rows: int
    csv_columns: int
    csv_field_bytes: int
    text_code_points: int
    note_code_points: int
    dataset_long_text_code_points: int
    dataset_short_text_code_points: int
    errors_collected: int
    errors_returned: int
    concurrent_imports: int


class ReturnsCapabilities(BaseModel):
    """`GET /returns/capabilities` (contracts §7). Holds no credential or configuration detail
    beyond whether this caller may commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str
    scopes: list[ReturnsScope]
    can_validate: bool
    can_commit: bool
    limits: ReturnsLimits
    reason_codes: list[contracts.ReasonCode]


LIMITS = ReturnsLimits(
    request_body_bytes=REQUEST_BODY_MAX_BYTES,
    bundle_bytes=bundle.BUNDLE_MAX_BYTES,
    file_bytes=bundle.FILE_MAX_BYTES,
    manifest_bytes=bundle.MANIFEST_MAX_BYTES,
    files=bundle.MAX_FILES,
    bundle_rows=bundle.BUNDLE_MAX_ROWS,
    entity_rows=bundle.ENTITY_MAX_ROWS,
    csv_columns=bundle.CSV_MAX_COLUMNS,
    csv_field_bytes=bundle.CSV_FIELD_MAX_BYTES,
    text_code_points=contracts.TEXT_MAX,
    note_code_points=contracts.NOTE_MAX,
    dataset_long_text_code_points=contracts.DATASET_LONG_TEXT_MAX,
    dataset_short_text_code_points=contracts.DATASET_SHORT_TEXT_MAX,
    errors_collected=errors.COLLECTION_LIMIT,
    errors_returned=errors.RETURNED_LIMIT,
    concurrent_imports=CONCURRENT_IMPORTS,
)

_READ = require_scope(ReturnsScope.READ)

router = APIRouter(prefix=PREFIX, tags=["returns"], route_class=ReturnsRoute)


@router.get("/capabilities", response_model=ReturnsCapabilities)
def capabilities(
    caller: Annotated[Caller, Depends(_READ)],
    access: Annotated[ReturnsAccess, Depends(get_returns_access)],
) -> ReturnsCapabilities:
    """What this caller may do, and the import limits. No database access."""
    return ReturnsCapabilities(
        contract_version=CONTRACT_VERSION,
        scopes=sorted(caller.scopes),
        can_validate=ReturnsScope.VALIDATE in caller.scopes,
        can_commit=ReturnsScope.IMPORT in caller.scopes and access.gate.open,
        limits=LIMITS,
        reason_codes=list(contracts.ReasonCode),
    )
