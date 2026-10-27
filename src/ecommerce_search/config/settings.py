import hmac
import re
from decimal import Decimal
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    AliasPath,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import URL

from ecommerce_search.decision.jev_provider import JEV_BACKOFF_MAX_SECONDS, JevConfig
from ecommerce_search.embeddings.spec import (
    ALL_MINILM_L6_V2,
    EMBEDDING_MODELS,
    EmbeddingModelSpec,
    repository_models_dir,
)
from ecommerce_search.reranking.spec import (
    MS_MARCO_MINILM_L6_V2,
    RERANKER_MODELS,
    RerankerModelSpec,
    registered_reranker,
)
from ecommerce_search.returns.labels import is_valid_importer_label

# A versioned Jev model id; the moving aliases (`jev-latest`, `jev-preview`, `jev`) are rejected.
_JEV_PINNED_MODEL = re.compile(r"jev-\d+\.\d+\.\d+")
# The only model experimental V4 may call (the M12 diagnostic and calibration runs used it).
V4_JEV_MODEL = "jev-1.13.0"
# A plain generation model id (letters, digits, dots and hyphens); no URL, path or whitespace.
_GENERATION_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}")
# Retailer bearer tokens (contracts §6): at least 32 characters (`secrets.token_urlsafe(32)` gives
# 43), at most 256, RFC 6750 b64token characters only.
RETURNS_TOKEN_MIN_LENGTH = 32
RETURNS_TOKEN_MAX_LENGTH = 256
RETURNS_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-._~+/]+=*")
# A plain PostgreSQL role name (lower case, as created unquoted).
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def is_returns_token(value: str) -> bool:
    return (
        RETURNS_TOKEN_MIN_LENGTH <= len(value) <= RETURNS_TOKEN_MAX_LENGTH
        and RETURNS_TOKEN_PATTERN.fullmatch(value) is not None
    )


def _secret_input_names(fields: dict[str, FieldInfo]) -> frozenset[str]:
    """Lower-cased names and aliases of every field typed `SecretStr` (or `SecretStr | None`)."""
    names: set[str] = set()
    for name, field in fields.items():
        if field.annotation is not SecretStr and SecretStr not in get_args(field.annotation):
            continue
        names.add(name)
        aliases: list[object] = [field.alias, field.validation_alias]
        if isinstance(field.validation_alias, AliasChoices):
            aliases.extend(field.validation_alias.choices)
        for alias in aliases:
            if isinstance(alias, AliasPath):
                alias = alias.path[0]
            if isinstance(alias, str):
                names.add(alias)
    return frozenset(name.lower() for name in names)


class _SecretInputSource(PydanticBaseSettingsSource):
    """Wraps one settings source so secret values enter validation as `SecretStr`, never `str`.

    Pydantic records the raw input in structured errors (`errors()`, `json()`), and a model-level
    error records the whole input dict, even with `hide_input_in_errors`. Masking at the source
    keeps raw secrets out of that input; values, source order and precedence are unchanged.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        inner: PydanticBaseSettingsSource,
        names: frozenset[str],
    ) -> None:
        super().__init__(settings_cls)
        self._inner = inner
        self._names = names

    def _set_current_state(self, state: dict[str, Any]) -> None:
        super()._set_current_state(state)
        self._inner._set_current_state(state)

    def _set_settings_sources_data(self, states: dict[str, dict[str, Any]]) -> None:
        super()._set_settings_sources_data(states)
        self._inner._set_settings_sources_data(states)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._inner.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        return {
            key: SecretStr(value)
            if key.lower() in self._names and isinstance(value, str)
            else value
            for key, value in self._inner().items()
        }


class Settings(BaseSettings):
    """Typed application configuration read from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation error *messages* name the field but never echo input values. This does not
        # cover structured errors (`errors()`, `json()`); `_SecretInputSource` keeps raw secrets
        # out of those.
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The default sources in the default order, each with secret values wrapped.
        names = _secret_input_names(settings_cls.model_fields)
        return tuple(
            _SecretInputSource(settings_cls, source, names)
            for source in (init_settings, env_settings, dotenv_settings, file_secret_settings)
        )

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "ecommerce_search"
    postgres_user: str = "ecommerce_search"
    postgres_password: SecretStr = Field(min_length=1)

    db_connect_timeout_seconds: int = Field(default=3, ge=1)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: int = Field(default=10, ge=1)
    # PostgreSQL accepts statement_timeout as int32 milliseconds; 0 (disabled) is not allowed.
    db_statement_timeout_ms: int = Field(default=30_000, ge=1, le=2_147_483_647)

    # Lexical search (Milestone 3). `search_lexical_k` is the maximum accepted `top_k`.
    search_lexical_k: int = Field(default=50, ge=1, le=1000)
    search_default_top_k: int = Field(default=10, ge=1, le=1000)
    search_max_query_length: int = Field(default=200, ge=1, le=1000)

    # Dense search (Milestone 4). `search_dense_k` is the maximum accepted dense `top_k`.
    search_dense_k: int = Field(default=50, ge=1, le=1000)
    # The model must be an entry of the production registry (ADR-006) at its pinned revision:
    # settings select a reviewed model, they never introduce an unreviewed one.
    embedding_model_id: str = ALL_MINILM_L6_V2.model_id
    embedding_model_revision: str = ALL_MINILM_L6_V2.revision
    # None = the git-ignored `models/` directory at the repository root (source checkout).
    # The api container sets this to its read-only mount.
    embedding_models_dir: Path | None = None
    embedding_batch_size: int = Field(default=32, ge=1, le=256)

    # Hybrid search (Milestone 5). Source depths are `search_lexical_k` and `search_dense_k`;
    # `search_candidate_k` is the fused list length and the maximum accepted hybrid `top_k`.
    search_candidate_k: int = Field(default=50, ge=1, le=2000)
    # Reciprocal Rank Fusion constant. 100 was selected by the committed M5 Phase-S predeclared
    # tie rule; it remains provisional until Golden Dataset evaluation in M10 (see `RRF_K_STATUS`
    # in `search/hybrid.py`).
    search_rrf_k: int = Field(default=100, ge=1, le=1000)

    # Cross-encoder reranking (Milestone 8, V3). The model must be the reviewed registry entry at
    # its pinned revision (ADR-007: a provisional compatibility pin, not a quality selection).
    # The snapshot is read from the same models directory as the embedding model.
    reranker_enabled: bool = True
    reranker_model_id: str = MS_MARCO_MINILM_L6_V2.model_id
    reranker_model_revision: str = MS_MARCO_MINILM_L6_V2.revision
    reranker_batch_size: int = Field(default=16, ge=1, le=256)
    # How many fused candidates (the head of the hybrid list) are reranked; <= search_candidate_k.
    # Validated even when reranking is disabled: every accepted configuration is valid as a whole.
    search_rerank_depth: int = Field(default=20, ge=1, le=1000)

    # Optional Jev decision provider (Milestone 11, ADR-005). Off by default, no key needed while
    # off, and not wired into any endpoint. Configuration errors fail validation even while
    # disabled (as for the reranker); runtime call failures fall back to the deterministic result.
    jev_enabled: bool = False
    # The official TypeSafe variable name. Empty means unset; never logged or echoed in errors.
    typesafe_api_key: SecretStr | None = None
    jev_model: str = "jev-1.13.0"
    jev_base_url: str = "https://api.typesafe.ai"
    # Provisional, not benchmarked. The attempt timeout is httpx2's per-operation timeout and the
    # budget is the SDK retry budget; neither bounds the caller's wait. The deadline does (FR-DEC-3:
    # a bounded wait plus scheduling tolerance, not a hard real-time guarantee).
    jev_attempt_timeout_seconds: float = Field(default=1.25, gt=0, le=10, allow_inf_nan=False)
    jev_max_retries: int = Field(default=1, ge=0, le=2)
    jev_retry_budget_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    jev_deadline_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    # Calls in flight at once (including cancelled calls still unwinding); extra calls fall back.
    jev_max_in_flight: int = Field(default=8, ge=1, le=64)

    # Experimental V4 (`/search/routed`, Milestone 12): explicit opt-in to live Jev calls from that
    # route only. Off by default; while off, V4 returns the V3 baseline and builds no provider.
    # When on, the key is required and the model must be the approved pin; V4 always calls with
    # retries 0 and one call in flight (`v4_jev_config`). Independent of `jev_enabled`.
    search_v4_jev_enabled: bool = False

    # Built Search UI (Milestone 25A, ADR-008). None = API only (no page, no asset route). When
    # set it must be a Vite build output (`index.html` plus `assets/`), or settings fail: an
    # explicitly configured frontend is never silently hidden.
    frontend_dist_dir: Path | None = None

    # Shopping assistant (Milestone 16, ADR-009). Generation is off by default ("none"): the
    # assistant still clarifies and retrieves, and says that no generated answer is available.
    # "gemini" (Gemini Developer API, free tier) is the proposed optional provider; it needs the
    # key and is never required by tests. Limits are provisional bounds, not benchmarked values.
    assistant_generation_provider: Literal["none", "gemini"] = "none"
    # The Gemini API key variable name used by Google's docs. Empty means unset; never logged or
    # echoed in errors. Passed explicitly: GOOGLE_API_KEY and ambient Google credentials are
    # never read.
    gemini_api_key: SecretStr | None = None
    # Proposed, unverified: a stable free-tier model at the time of writing (ADR-009).
    assistant_generation_model: str = "gemini-3.5-flash-lite"
    # The SDK's per-request timeout (one attempt, SDK retries off); not a wall-clock bound.
    assistant_generation_timeout_seconds: float = Field(
        default=20, gt=0, le=60, allow_inf_nan=False
    )
    # No repair call is started once this much time has passed since the request began.
    assistant_generation_deadline_seconds: float = Field(
        default=30, gt=0, le=120, allow_inf_nan=False
    )
    # The draft is IDs and field names only (observed 18–120 output tokens including thinking).
    assistant_generation_max_output_tokens: int = Field(default=1024, ge=256, le=4096)
    # Usage guard (`generation/guard.py`): every provider request, repairs included, counts. A
    # refused request is not sent and is not retried. Per process (one worker); 0 per day = off.
    assistant_generation_max_concurrency: int = Field(default=2, ge=1, le=8)
    assistant_generation_requests_per_minute: int = Field(default=10, ge=1, le=60)
    assistant_generation_requests_per_day: int = Field(default=200, ge=0, le=1000)
    # Retrieved records given to the generator (the first V3 results). Capped at
    # search_candidate_k when used, so it never invalidates an existing search configuration.
    assistant_evidence_k: int = Field(default=5, ge=1, le=10)
    # Country eligibility (`assistant/eligibility.py`): the DB-IP "IP to Country Lite" CSV
    # (gzip) and the SHA-1 of its decompressed content as published by DB-IP. Unset, missing or
    # mismatched data = every visitor's region is unknown, so generation is skipped.
    assistant_country_db_path: Path | None = None
    assistant_country_db_sha1: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{40}$")
    # Comma-separated networks of the reverse proxy whose `X-Client-IP` header is trusted (the
    # EC2 Caddy container). Empty = never trust any forwarded header; use the TCP peer.
    assistant_trusted_proxy_cidrs: str = ""
    # Optional owner-supplied prices for cost estimates. Unset = cost reported as unknown.
    assistant_input_usd_per_mtok: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    assistant_output_usd_per_mtok: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    # Milestone 18: the label recorded on return import batches committed by the CLI (contracts
    # §6). Supplied provenance, not an authenticated identity. Unset = CLI imports are refused.
    returns_importer_label: str | None = None
    # Milestone 18 retailer access (contracts §6, D1): two static bearer tokens. Empty means unset;
    # with neither set every /returns route fails closed (503). Never logged or echoed.
    returns_viewer_token: SecretStr | None = None
    returns_importer_token: SecretStr | None = None
    # The API commit path also needs the importer token, the importer database credentials and
    # RETURNS_IMPORTER_LABEL (`api/returns.py` `CommitGate`). Off by default.
    returns_import_commit_enabled: bool = False
    # The scoped `returns_importer` role, used only by the API commit engine; both or neither.
    returns_importer_db_user: str | None = None
    returns_importer_db_password: SecretStr | None = None

    @field_validator("returns_importer_label", mode="before")
    @classmethod
    def _importer_label(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not is_valid_importer_label(value):
            raise ValueError(
                "returns_importer_label must be 1-64 letters, digits, '.', '_', ':' or '-' and "
                "must not look like personal data"
            )
        return value

    @field_validator(
        "returns_viewer_token",
        "returns_importer_token",
        "returns_importer_db_password",
        mode="before",
    )
    @classmethod
    def _empty_returns_secret_is_unset(cls, value: object) -> object:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(raw, str) and raw == "" else value

    @field_validator("returns_viewer_token", "returns_importer_token")
    @classmethod
    def _returns_token_format(
        cls, value: SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        # RFC 6750 b64token characters, so the token can be sent in an Authorization header.
        if value is not None and not is_returns_token(value.get_secret_value()):
            raise ValueError(
                f"{info.field_name} must be {RETURNS_TOKEN_MIN_LENGTH}-{RETURNS_TOKEN_MAX_LENGTH} "
                "letters, digits or '-._~+/=' characters"
            )
        return value

    @field_validator("returns_importer_db_user", mode="before")
    @classmethod
    def _importer_db_user(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not _ROLE_NAME.fullmatch(value):
            raise ValueError("returns_importer_db_user must be a plain role name")
        return value

    @model_validator(mode="after")
    def _returns_access_is_configured_consistently(self) -> "Settings":
        viewer, importer = self.returns_viewer_token, self.returns_importer_token
        if (
            viewer is not None
            and importer is not None
            and hmac.compare_digest(
                viewer.get_secret_value().encode(), importer.get_secret_value().encode()
            )
        ):
            raise ValueError("returns_viewer_token and returns_importer_token must differ")
        if (self.returns_importer_db_user is None) != (self.returns_importer_db_password is None):
            raise ValueError(
                "set both returns_importer_db_user and returns_importer_db_password or neither"
            )
        if self.returns_importer_db_user == self.postgres_user:
            raise ValueError("returns_importer_db_user must differ from postgres_user")
        return self

    @field_validator("frontend_dist_dir")
    @classmethod
    def _frontend_build(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not (value / "index.html").is_file() or not (value / "assets").is_dir():
            raise ValueError(
                "frontend_dist_dir must be a built frontend directory containing index.html and "
                "assets/ (run `npm run build` in frontend/)"
            )
        return value

    @field_validator(
        "reranker_batch_size",
        "search_rerank_depth",
        "jev_max_retries",
        "jev_max_in_flight",
        mode="before",
    )
    @classmethod
    def _strict_integer(cls, value: object) -> object:
        # No bool (True == 1) and no float/decimal coercion; environment strings of digits only.
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError("must be an integer")
        if isinstance(value, str) and not value.strip().isdecimal():
            raise ValueError("must be an integer")
        return value

    @field_validator(
        "jev_attempt_timeout_seconds",
        "jev_retry_budget_seconds",
        "jev_deadline_seconds",
        mode="before",
    )
    @classmethod
    def _strict_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ValueError("must be a number")
        return value

    @field_validator("typesafe_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        # Inspect without unwrapping, so a non-empty key stays a SecretStr in every error input.
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and not raw.strip():
            return None
        return value

    @field_validator("typesafe_api_key")
    @classmethod
    def _key_format(cls, value: SecretStr | None) -> SecretStr | None:
        # The SDK's own key rule (printable ASCII, no internal whitespace), without importing it.
        if value is not None:
            key = value.get_secret_value().strip()
            if not key.isascii() or not key.isprintable() or " " in key:
                raise ValueError("typesafe_api_key has an invalid format")
            return SecretStr(key)
        return value

    @field_validator("gemini_api_key")
    @classmethod
    def _gemini_key_format(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            key = value.get_secret_value().strip()
            if not key.isascii() or not key.isprintable() or " " in key:
                raise ValueError("gemini_api_key has an invalid format")
            return SecretStr(key)
        return value

    @field_validator("assistant_generation_model")
    @classmethod
    def _generation_model_id(cls, value: str) -> str:
        if not _GENERATION_MODEL_ID.fullmatch(value):
            raise ValueError("assistant_generation_model must be a plain model id")
        return value

    @model_validator(mode="after")
    def _assistant_is_configured_consistently(self) -> "Settings":
        if self.assistant_generation_provider == "gemini" and self.gemini_api_key is None:
            raise ValueError(
                "gemini_api_key is required when assistant_generation_provider is gemini"
            )
        if (self.assistant_input_usd_per_mtok is None) != (
            self.assistant_output_usd_per_mtok is None
        ):
            raise ValueError("set both assistant token prices or neither")
        return self

    @field_validator("jev_model", "jev_base_url", mode="before")
    @classmethod
    def _exact_jev_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value

    @field_validator("jev_model")
    @classmethod
    def _jev_model_is_pinned(cls, value: str) -> str:
        if not _JEV_PINNED_MODEL.fullmatch(value):
            raise ValueError("jev_model must be a versioned id such as jev-1.13.0, not an alias")
        return value

    @field_validator("jev_base_url")
    @classmethod
    def _jev_base_url_is_plain_https(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or value != value.strip()
        ):
            raise ValueError("jev_base_url must be a plain https URL")
        return value.rstrip("/")

    @field_validator("assistant_generation_model", mode="before")
    @classmethod
    def _exact_model_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value

    @field_validator(
        "reranker_enabled",
        "jev_enabled",
        "search_v4_jev_enabled",
        "returns_import_commit_enabled",
        mode="before",
    )
    @classmethod
    def _strict_boolean(cls, value: object) -> object:
        # Only a real bool or the words true/false (any case); no 1/0, yes/no, on/off.
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("must be true or false")

    @field_validator("reranker_model_id", "reranker_model_revision", mode="before")
    @classmethod
    def _exact_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value

    @model_validator(mode="after")
    def _default_top_k_within_limit(self) -> "Settings":
        if self.search_default_top_k > self.search_lexical_k:
            raise ValueError("search_default_top_k must not exceed search_lexical_k")
        if self.search_default_top_k > self.search_dense_k:
            raise ValueError("search_default_top_k must not exceed search_dense_k")
        if self.search_default_top_k > self.search_candidate_k:
            raise ValueError("search_default_top_k must not exceed search_candidate_k")
        return self

    @model_validator(mode="after")
    def _candidate_k_within_source_depths(self) -> "Settings":
        if self.search_candidate_k > self.search_lexical_k + self.search_dense_k:
            raise ValueError("search_candidate_k must not exceed search_lexical_k + search_dense_k")
        return self

    @model_validator(mode="after")
    def _embedding_model_is_registered(self) -> "Settings":
        spec = EMBEDDING_MODELS.get(self.embedding_model_id)
        if spec is None:
            raise ValueError("embedding_model_id is not in the reviewed model registry")
        if spec.revision != self.embedding_model_revision:
            raise ValueError("embedding_model_revision does not match the registry pin")
        return self

    @model_validator(mode="after")
    def _reranker_is_registered(self) -> "Settings":
        if self.reranker_model_id not in RERANKER_MODELS:
            raise ValueError("reranker_model_id is not in the reviewed reranker registry")
        if RERANKER_MODELS[self.reranker_model_id].revision != self.reranker_model_revision:
            raise ValueError("reranker_model_revision does not match the registry pin")
        return self

    @model_validator(mode="after")
    def _rerank_depth_within_candidates(self) -> "Settings":
        if self.search_rerank_depth > self.search_candidate_k:
            raise ValueError("search_rerank_depth must not exceed search_candidate_k")
        return self

    @model_validator(mode="after")
    def _jev_is_configured_consistently(self) -> "Settings":
        if self.jev_enabled and self.typesafe_api_key is None:
            raise ValueError("typesafe_api_key is required when jev_enabled is true")
        if self.search_v4_jev_enabled and self.typesafe_api_key is None:
            raise ValueError("typesafe_api_key is required when search_v4_jev_enabled is true")
        if self.search_v4_jev_enabled and self.jev_model != V4_JEV_MODEL:
            raise ValueError(f"search_v4_jev_enabled requires jev_model {V4_JEV_MODEL}")
        # Every configured retry must stay reachable after a timed-out attempt plus its backoff;
        # otherwise the budget would silently cancel retries that the configuration asks for.
        needed = self.jev_max_retries * (self.jev_attempt_timeout_seconds + JEV_BACKOFF_MAX_SECONDS)
        if needed >= self.jev_retry_budget_seconds:
            raise ValueError(
                "jev_retry_budget_seconds must exceed "
                "jev_max_retries * (jev_attempt_timeout_seconds + backoff max)"
            )
        # The deadline bounds the whole call: the attempt timeout and retry budget must fit in it.
        if self.jev_attempt_timeout_seconds > self.jev_deadline_seconds:
            raise ValueError("jev_attempt_timeout_seconds must not exceed jev_deadline_seconds")
        if self.jev_retry_budget_seconds > self.jev_deadline_seconds:
            raise ValueError("jev_retry_budget_seconds must not exceed jev_deadline_seconds")
        return self

    @field_validator("assistant_country_db_sha1", mode="before")
    @classmethod
    def _empty_sha1_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("assistant_trusted_proxy_cidrs")
    @classmethod
    def _valid_proxy_cidrs(cls, value: str) -> str:
        for part in filter(None, (p.strip() for p in value.split(","))):
            try:
                ip_network(part, strict=True)
            except ValueError:
                raise ValueError("assistant_trusted_proxy_cidrs must be valid networks") from None
        return value

    def trusted_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return tuple(
            ip_network(part, strict=True)
            for part in (p.strip() for p in self.assistant_trusted_proxy_cidrs.split(","))
            if part
        )

    def v4_jev_config(self) -> JevConfig | None:
        """The experimental V4 Jev configuration (retries 0, one call in flight), or None while
        V4 Jev routing is off."""
        if not self.search_v4_jev_enabled or self.typesafe_api_key is None:
            return None
        return JevConfig(
            api_key=self.typesafe_api_key,
            model=self.jev_model,
            base_url=self.jev_base_url,
            attempt_timeout_seconds=self.jev_attempt_timeout_seconds,
            max_retries=0,
            retry_budget_seconds=self.jev_retry_budget_seconds,
            deadline_seconds=self.jev_deadline_seconds,
            max_in_flight=1,
        )

    def jev_config(self) -> JevConfig | None:
        """The Jev provider configuration, or None while Jev is disabled."""
        if not self.jev_enabled or self.typesafe_api_key is None:
            return None
        return JevConfig(
            api_key=self.typesafe_api_key,
            model=self.jev_model,
            base_url=self.jev_base_url,
            attempt_timeout_seconds=self.jev_attempt_timeout_seconds,
            max_retries=self.jev_max_retries,
            retry_budget_seconds=self.jev_retry_budget_seconds,
            deadline_seconds=self.jev_deadline_seconds,
            max_in_flight=self.jev_max_in_flight,
        )

    def reranker_spec(self) -> RerankerModelSpec:
        return registered_reranker(self.reranker_model_id, self.reranker_model_revision)

    def embedding_spec(self) -> EmbeddingModelSpec:
        return EMBEDDING_MODELS[self.embedding_model_id]

    def resolved_models_dir(self) -> Path | None:
        return self.embedding_models_dir or repository_models_dir()

    def database_url(self, database: str | None = None) -> URL:
        """SQLAlchemy URL for the configured server (optionally another database)."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )

    def returns_importer_database_url(self) -> URL | None:
        """The same server and database as the app, as the scoped importer role; None while the
        importer credentials are unset."""
        if self.returns_importer_db_user is None or self.returns_importer_db_password is None:
            return None
        return self.database_url().set(
            username=self.returns_importer_db_user,
            password=self.returns_importer_db_password.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
