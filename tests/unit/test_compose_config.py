import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecommerce_search.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")

FORWARDED = {
    "DB_CONNECT_TIMEOUT_SECONDS": "db_connect_timeout_seconds",
    "DB_POOL_TIMEOUT_SECONDS": "db_pool_timeout_seconds",
    "DB_STATEMENT_TIMEOUT_MS": "db_statement_timeout_ms",
    # Milestone 3: the api service reads these; db and migrate do not.
    "SEARCH_LEXICAL_K": "search_lexical_k",
    "SEARCH_DEFAULT_TOP_K": "search_default_top_k",
    "SEARCH_MAX_QUERY_LENGTH": "search_max_query_length",
    # Milestone 4: dense search (api only).
    "SEARCH_DENSE_K": "search_dense_k",
    # Milestone 5: hybrid search (api only).
    "SEARCH_CANDIDATE_K": "search_candidate_k",
    "SEARCH_RRF_K": "search_rrf_k",
}


def test_compose_forwarded_defaults_match_settings_and_env_example():
    for env_name, field in FORWARDED.items():
        default = Settings.model_fields[field].default
        assert f"{env_name}: ${{{env_name}:-{default}}}" in COMPOSE
        assert re.search(rf"^{env_name}={default}$", ENV_EXAMPLE, re.MULTILINE)


def test_migrate_does_not_receive_api_only_timeouts():
    migrate = COMPOSE.split("  migrate:")[1].split("  api:")[0]
    assert "<<:" not in migrate and "DB_POOL_TIMEOUT_SECONDS" not in migrate
    assert "DB_STATEMENT_TIMEOUT_MS" not in migrate
    assert "SEARCH_" not in migrate


def test_search_settings_reach_only_the_api_service():
    db = COMPOSE.split("  db:")[1].split("  migrate:")[0]
    api = COMPOSE.split("  api:")[1].split("volumes:")[0]
    assert "SEARCH_" not in db
    for name in ("SEARCH_LEXICAL_K", "SEARCH_DEFAULT_TOP_K", "SEARCH_MAX_QUERY_LENGTH"):
        assert name in api


def _service(name: str, following: str) -> str:
    return COMPOSE.split(f"  {name}:")[1].split(following)[0]


def test_only_the_api_gets_the_read_only_model_mount_and_embedding_settings():
    db = _service("db", "  migrate:")
    migrate = _service("migrate", "  api:")
    # the api service runs until the top-level `volumes:` key (column 0)
    api = re.split(r"^volumes:", COMPOSE.split("  api:")[1], flags=re.MULTILINE)[0]
    for section in (db, migrate):
        assert "/models" not in section and "EMBEDDING_" not in section
        assert "SEARCH_DENSE_K" not in section
    assert "      - ./models:/models:ro" in api  # read-only bind mount, never copied in
    assert "EMBEDDING_MODELS_DIR: /models" in api


def test_model_weights_are_never_copied_into_the_image():
    root_files = {
        name: (ROOT / name).read_text(encoding="utf-8") for name in ("Dockerfile", ".dockerignore")
    }
    assert "models" not in root_files["Dockerfile"]
    assert "models/" in root_files[".dockerignore"].splitlines()


def test_hybrid_settings_reach_only_the_api_service():
    db = _service("db", "  migrate:")
    migrate = _service("migrate", "  api:")
    api = re.split(r"^volumes:", COMPOSE.split("  api:")[1], flags=re.MULTILINE)[0]
    for name in ("SEARCH_CANDIDATE_K", "SEARCH_RRF_K"):
        assert name not in db and name not in migrate
        assert name in api
    assert "x-app-env" not in api and "SEARCH_" not in COMPOSE.split("services:")[0]


def test_the_provisional_rrf_k_default_is_forwarded_once_to_the_api():
    assert Settings.model_fields["search_rrf_k"].default == 100
    assert COMPOSE.count("SEARCH_RRF_K") == 2  # the key and its interpolation, on one line
    api = re.split(r"^volumes:", COMPOSE.split("  api:")[1], flags=re.MULTILINE)[0]
    assert "      SEARCH_RRF_K: ${SEARCH_RRF_K:-100}" in api
    assert re.search(r"^SEARCH_RRF_K=100$", ENV_EXAMPLE, re.MULTILINE)


# Milestone 8 (V3 reranking): forwarded to the api service only. Expected values are written out
# here (the provisional ADR-007 pin and the reviewed defaults), not derived from the settings.
RERANKER_FORWARDED = {
    "RERANKER_ENABLED": "true",
    "RERANKER_MODEL_ID": "cross-encoder/ms-marco-MiniLM-L6-v2",
    "RERANKER_MODEL_REVISION": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
    "RERANKER_BATCH_SIZE": "16",
    "SEARCH_RERANK_DEPTH": "20",
}


def _api() -> str:
    return re.split(r"^volumes:", COMPOSE.split("  api:")[1], flags=re.MULTILINE)[0]


def test_reranker_settings_are_forwarded_once_to_the_api_with_reviewed_defaults():
    api = _api()
    for name, default in RERANKER_FORWARDED.items():
        assert f"      {name}: ${{{name}:-{default}}}\n" in api, name
        keys = re.findall(rf"^\s+{name}:", COMPOSE, re.MULTILINE)
        assert len(keys) == 1, name  # one key, in the api service only
        assert re.search(rf"^{re.escape(name)}={re.escape(default)}$", ENV_EXAMPLE, re.MULTILINE)


def test_reranker_settings_never_reach_the_db_or_migrate_services():
    db = _service("db", "  migrate:")
    migrate = _service("migrate", "  api:")
    head = COMPOSE.split("services:")[0]  # the shared x-app-env anchor
    for section in (db, migrate, head):
        assert "RERANKER_" not in section and "SEARCH_RERANK_DEPTH" not in section


def test_forwarded_reranker_defaults_parse_to_the_settings_defaults(make_settings, monkeypatch):
    for name, value in RERANKER_FORWARDED.items():
        monkeypatch.setenv(name, value)
    from_env = make_settings()  # reads the forwarded values from the environment
    for field in (
        "reranker_enabled",
        "reranker_model_id",
        "reranker_model_revision",
        "reranker_batch_size",
        "search_rerank_depth",
    ):
        assert getattr(from_env, field) == Settings.model_fields[field].default, field


def test_valid_reranker_overrides_are_accepted(make_settings, monkeypatch):
    overrides = {"RERANKER_ENABLED": "false", "RERANKER_BATCH_SIZE": "4"}
    overrides |= {"SEARCH_RERANK_DEPTH": "10"}
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    settings = make_settings()
    assert settings.reranker_enabled is False
    assert (settings.reranker_batch_size, settings.search_rerank_depth) == (4, 10)


# Override strings as Compose would pass them through the environment; each must be refused.
INVALID_RERANKER_OVERRIDES = [
    ("RERANKER_ENABLED", "yes"),
    ("RERANKER_ENABLED", "1"),
    ("RERANKER_ENABLED", ""),
    ("RERANKER_MODEL_ID", "cross-encoder/ms-marco-MiniLM-L12-v2"),
    ("RERANKER_MODEL_ID", "CROSS-ENCODER/MS-MARCO-MINILM-L6-V2"),
    ("RERANKER_MODEL_REVISION", "main"),
    ("RERANKER_MODEL_REVISION", "f" * 40),
    ("RERANKER_BATCH_SIZE", "0"),
    ("RERANKER_BATCH_SIZE", "257"),
    ("RERANKER_BATCH_SIZE", "16.0"),
    ("RERANKER_BATCH_SIZE", "sixteen"),
    ("SEARCH_RERANK_DEPTH", "0"),
    ("SEARCH_RERANK_DEPTH", "51"),  # above the default SEARCH_CANDIDATE_K of 50
    ("SEARCH_RERANK_DEPTH", "20.5"),
]


@pytest.mark.parametrize(("name", "value"), INVALID_RERANKER_OVERRIDES)
def test_invalid_reranker_overrides_fail_settings(make_settings, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        make_settings()


def test_env_example_documents_the_reranker_constraints():
    block = ENV_EXAMPLE.split("# Cross-encoder reranking (Milestone 8, V3).", 1)[1]
    assert "provisional" in block and "ADR-007" in block
    assert "model-fetch --kind reranker" in block and "never" in block
    assert "downloaded at request time" in block
    assert "must not exceed SEARCH_CANDIDATE_K" in block


def test_existing_forwarded_settings_and_model_mount_are_unchanged():
    api = _api()
    for env_name, field in FORWARDED.items():
        default = Settings.model_fields[field].default
        assert COMPOSE.count(f"{env_name}: ${{{env_name}:-{default}}}") == 1
    assert api.count("      - ./models:/models:ro") == 1
    assert api.count("EMBEDDING_MODELS_DIR: /models") == 1
    assert "EMBEDDING_MODEL_ID" not in COMPOSE and "EMBEDDING_BATCH_SIZE" not in COMPOSE


def test_jev_settings_are_not_forwarded_to_any_service():
    # Milestone 11: the Jev provider is optional, off by default and unused by any endpoint.
    assert "JEV_" not in COMPOSE and "TYPESAFE_" not in COMPOSE
    assert re.search(r"^JEV_ENABLED=false$", ENV_EXAMPLE, re.MULTILINE)
    assert re.search(r"^TYPESAFE_API_KEY=$", ENV_EXAMPLE, re.MULTILINE)
