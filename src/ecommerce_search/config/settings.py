from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

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


class Settings(BaseSettings):
    """Typed application configuration read from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    @field_validator("reranker_batch_size", "search_rerank_depth", mode="before")
    @classmethod
    def _strict_integer(cls, value: object) -> object:
        # No bool (True == 1) and no float/decimal coercion; environment strings of digits only.
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError("must be an integer")
        if isinstance(value, str) and not value.strip().isdecimal():
            raise ValueError("must be an integer")
        return value

    @field_validator("reranker_enabled", mode="before")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
