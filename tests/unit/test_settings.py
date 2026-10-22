import pytest
from pydantic import AliasChoices, Field, SecretStr, ValidationError

from ecommerce_search.config import Settings
from ecommerce_search.config.settings import _secret_input_names

ENV_KEYS = [
    "APP_ENV",
    "LOG_LEVEL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "DB_POOL_TIMEOUT_SECONDS",
    "DB_STATEMENT_TIMEOUT_MS",
    "SEARCH_LEXICAL_K",
    "SEARCH_DEFAULT_TOP_K",
    "SEARCH_MAX_QUERY_LENGTH",
    "SEARCH_DENSE_K",
    "SEARCH_CANDIDATE_K",
    "SEARCH_RRF_K",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_MODEL_REVISION",
    "EMBEDDING_MODELS_DIR",
    "EMBEDDING_BATCH_SIZE",
    "RERANKER_ENABLED",
    "RERANKER_MODEL_ID",
    "RERANKER_MODEL_REVISION",
    "RERANKER_BATCH_SIZE",
    "SEARCH_RERANK_DEPTH",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults(make_settings):
    s = make_settings()
    assert s.postgres_host == "127.0.0.1"
    assert s.postgres_port == 5432
    assert s.postgres_db == "ecommerce_search"
    assert s.log_level == "INFO"
    assert s.db_connect_timeout_seconds == 3
    assert s.db_pool_timeout_seconds == 10
    assert s.db_statement_timeout_ms == 30_000


def test_environment_override(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("APP_ENV", "production")
    s = Settings(_env_file=None)
    assert (s.postgres_host, s.postgres_port, s.app_env) == ("db", 6543, "production")


def test_password_is_required():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_env": "staging"},
        {"log_level": "LOUD"},
        {"postgres_port": 0},
        {"postgres_port": 70000},
        {"postgres_password": ""},
        {"db_pool_timeout_seconds": 0},
        {"db_statement_timeout_ms": 0},
        {"db_statement_timeout_ms": -5},
        {"db_statement_timeout_ms": 2_147_483_648},
        {"search_lexical_k": 0},
        {"search_default_top_k": 0},
        {"search_max_query_length": 0},
        {"search_lexical_k": 5, "search_default_top_k": 6},
    ],
)
def test_invalid_values_rejected(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_password_not_in_repr(make_settings):
    s = make_settings(postgres_password="super-secret-value")  # noqa: S106
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s)


def test_database_url_escapes_special_characters(make_settings):
    s = make_settings(postgres_password="p@ss:w/rd#1")  # noqa: S106
    url = s.database_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss:w/rd#1"  # noqa: S105
    rendered = url.render_as_string(hide_password=False)
    assert "p%40ss%3Aw%2Frd%231" in rendered
    assert "p@ss:w/rd#1" not in rendered


def test_database_url_hides_password_by_default(make_settings):
    assert "unit-test-password" not in str(make_settings().database_url())


def test_database_override(make_settings):
    assert make_settings().database_url(database="other").database == "other"


def test_timeouts_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "1500")
    s = Settings(_env_file=None)
    assert (s.db_pool_timeout_seconds, s.db_statement_timeout_ms) == (7, 1500)


def test_statement_timeout_must_be_an_integer(make_settings):
    with pytest.raises(ValidationError):
        make_settings(db_statement_timeout_ms="1; DROP TABLE x")


def test_search_defaults(make_settings):
    s = make_settings()
    assert (s.search_lexical_k, s.search_default_top_k, s.search_max_query_length) == (50, 10, 200)


def test_search_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("SEARCH_LEXICAL_K", "20")
    monkeypatch.setenv("SEARCH_DEFAULT_TOP_K", "5")
    monkeypatch.setenv("SEARCH_MAX_QUERY_LENGTH", "80")
    s = Settings(_env_file=None)
    assert (s.search_lexical_k, s.search_default_top_k, s.search_max_query_length) == (20, 5, 80)


# ---- Milestone 4: dense search and embedding settings ---------------------------------------------


def test_dense_and_embedding_defaults_pin_the_reviewed_model(make_settings):
    s = make_settings()
    assert s.search_dense_k == 50 and s.embedding_batch_size == 32
    assert s.embedding_model_id == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.embedding_model_revision == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    assert s.embedding_spec().dimension == 384
    assert s.embedding_models_dir is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_model_id": "BAAI/bge-small-en-v1.5"},  # not in the production registry
        {"embedding_model_id": "org/unreviewed"},
        {"embedding_model_revision": "main"},
        {"embedding_model_revision": "f" * 40},
        {"search_dense_k": 0},
        {"search_dense_k": 5, "search_default_top_k": 6},
        {"embedding_batch_size": 0},
        {"embedding_batch_size": 257},
    ],
)
def test_invalid_embedding_settings_rejected(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_models_dir_defaults_to_the_repository_root_and_can_be_overridden(make_settings, tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert make_settings().resolved_models_dir() == root / "models"
    assert make_settings(embedding_models_dir=tmp_path).resolved_models_dir() == tmp_path


def test_dense_settings_read_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("SEARCH_DENSE_K", "20")
    monkeypatch.setenv("EMBEDDING_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    s = Settings(_env_file=None)
    assert (s.search_dense_k, s.embedding_models_dir, s.embedding_batch_size) == (20, tmp_path, 8)


# ---- Milestone 5: hybrid search settings ----------------------------------------------------------


def test_hybrid_defaults(make_settings):
    s = make_settings()
    assert (s.search_lexical_k, s.search_dense_k, s.search_candidate_k) == (50, 50, 50)
    assert s.search_rrf_k == 100


@pytest.mark.parametrize(
    "overrides",
    [
        {"search_candidate_k": 0},
        {"search_candidate_k": 2001},
        {"search_rrf_k": 0},
        {"search_rrf_k": 1001},
        {"search_rrf_k": "sixty"},
        {"search_candidate_k": 5, "search_default_top_k": 6},
        {"search_lexical_k": 10, "search_dense_k": 10, "search_candidate_k": 21},
    ],
)
def test_invalid_hybrid_settings_rejected(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_candidate_k_may_equal_the_sum_of_source_depths(make_settings):
    s = make_settings(search_lexical_k=10, search_dense_k=15, search_candidate_k=25)
    assert s.search_candidate_k == 25
    assert make_settings(search_rrf_k=1).search_rrf_k == 1
    assert make_settings(search_rrf_k=1000).search_rrf_k == 1000


def test_hybrid_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("SEARCH_CANDIDATE_K", "30")
    monkeypatch.setenv("SEARCH_RRF_K", "20")
    s = Settings(_env_file=None)
    assert (s.search_candidate_k, s.search_rrf_k) == (30, 20)


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("60", 60), ("1000", 1000)])
def test_rrf_k_environment_override_within_range(monkeypatch, raw, expected):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("SEARCH_RRF_K", raw)
    assert Settings(_env_file=None).search_rrf_k == expected


@pytest.mark.parametrize("raw", ["0", "1001"])
def test_rrf_k_environment_override_out_of_range_rejected(monkeypatch, raw):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("SEARCH_RRF_K", raw)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ---- Milestone 8: reranker settings (ADR-007) -------------------------------------------------

RERANKER_ID = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_REV = "233902d25c440f23af6f7d6e94d2946bac0bee0a"


def test_reranker_defaults_pin_the_reviewed_model(make_settings):
    from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2

    s = make_settings()
    assert s.reranker_enabled is True
    assert (s.reranker_model_id, s.reranker_model_revision) == (RERANKER_ID, RERANKER_REV)
    assert (s.reranker_batch_size, s.search_rerank_depth) == (16, 20)
    assert s.reranker_spec() is MS_MARCO_MINILM_L6_V2


@pytest.mark.parametrize(
    ("overrides", "field", "expected"),
    [
        ({"reranker_enabled": False}, "reranker_enabled", False),
        ({"reranker_batch_size": 1}, "reranker_batch_size", 1),
        ({"reranker_batch_size": 256}, "reranker_batch_size", 256),
        ({"search_rerank_depth": 1}, "search_rerank_depth", 1),
        ({"search_rerank_depth": 50}, "search_rerank_depth", 50),  # == search_candidate_k
        (
            {"search_candidate_k": 10, "search_rerank_depth": 10},
            "search_rerank_depth",
            10,
        ),
        (
            {
                "search_lexical_k": 1000,
                "search_dense_k": 1000,
                "search_candidate_k": 2000,
                "search_rerank_depth": 1000,
            },
            "search_rerank_depth",
            1000,
        ),
        (
            {"reranker_enabled": False, "search_candidate_k": 10, "search_rerank_depth": 10},
            "search_rerank_depth",
            10,
        ),
    ],
)
def test_valid_reranker_overrides(make_settings, overrides, field, expected):
    assert getattr(make_settings(**overrides), field) == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"reranker_model_id": "cross-encoder/ms-marco-MiniLM-L12-v2"},
        {"reranker_model_id": "org/unreviewed"},
        {"reranker_model_id": RERANKER_ID.upper()},
        {"reranker_model_id": None},
        {"reranker_model_revision": "main"},
        {"reranker_model_revision": "f" * 40},
        {"reranker_model_revision": RERANKER_REV.upper()},
        {"reranker_model_revision": None},
        {"reranker_batch_size": 0},
        {"reranker_batch_size": 257},
        {"reranker_batch_size": True},
        {"reranker_batch_size": 16.0},
        {"reranker_batch_size": "16.0"},
        {"reranker_batch_size": "1e1"},
        {"reranker_batch_size": ""},
        {"reranker_batch_size": None},
        {"search_rerank_depth": 0},
        {"search_rerank_depth": 51},  # > search_candidate_k (50)
        {"search_rerank_depth": False},
        {"search_rerank_depth": 20.5},
        {"search_candidate_k": 10},  # the default depth 20 would exceed it
        # Depth is validated even when reranking is disabled: the configuration is valid as a whole.
        {"reranker_enabled": False, "search_candidate_k": 10},
        {
            "search_lexical_k": 1000,
            "search_dense_k": 1000,
            "search_candidate_k": 2000,
            "search_rerank_depth": 1001,
        },
        {"reranker_enabled": 1},
        {"reranker_enabled": 0},
        {"reranker_enabled": "yes"},
        {"reranker_enabled": "1"},
        {"reranker_enabled": "on"},
        {"reranker_enabled": ""},
        {"reranker_enabled": None},
    ],
)
def test_invalid_reranker_settings_rejected(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_reranker_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("RERANKER_ENABLED", "FALSE")
    monkeypatch.setenv("RERANKER_MODEL_ID", RERANKER_ID)
    monkeypatch.setenv("RERANKER_MODEL_REVISION", RERANKER_REV)
    monkeypatch.setenv("RERANKER_BATCH_SIZE", "8")
    monkeypatch.setenv("SEARCH_RERANK_DEPTH", "10")
    s = Settings(_env_file=None)
    assert (s.reranker_enabled, s.reranker_batch_size, s.search_rerank_depth) == (False, 8, 10)
    assert s.reranker_spec().model_id == RERANKER_ID


@pytest.mark.parametrize(("raw", "expected"), [("true", True), (" True ", True), ("false", False)])
def test_reranker_enabled_accepts_only_true_or_false_words(monkeypatch, raw, expected):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("RERANKER_ENABLED", raw)
    assert Settings(_env_file=None).reranker_enabled is expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("RERANKER_ENABLED", "yes"),
        ("RERANKER_ENABLED", "0"),
        ("RERANKER_BATCH_SIZE", "0"),
        ("RERANKER_BATCH_SIZE", "257"),
        ("RERANKER_BATCH_SIZE", "8.0"),
        ("SEARCH_RERANK_DEPTH", "51"),
        ("SEARCH_RERANK_DEPTH", "-1"),
        ("RERANKER_MODEL_ID", "org/unreviewed"),
        ("RERANKER_MODEL_REVISION", "main"),
    ],
)
def test_invalid_reranker_environment_rejected(monkeypatch, key, raw):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv(key, raw)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_reranker_settings_do_not_reach_the_pure_reranking_modules():
    import ecommerce_search.reranking.provider as provider
    import ecommerce_search.reranking.spec as spec

    for module in (spec, provider):
        assert "Settings" not in vars(module) and "get_settings" not in vars(module)


# Milestone 11: optional Jev provider (ADR-005). Configuration errors fail validation, even while
# Jev is disabled; runtime call failures fall back (tests/unit/search/test_jev_provider.py).
FAKE_KEY = "ts-test-key-not-real-7f3a9c"


def test_jev_is_disabled_by_default_and_needs_no_key(make_settings):
    s = make_settings()
    assert (s.jev_enabled, s.typesafe_api_key, s.jev_config()) == (False, None, None)
    assert (s.jev_model, s.jev_base_url) == ("jev-1.13.0", "https://api.typesafe.ai")
    assert (s.jev_attempt_timeout_seconds, s.jev_max_retries, s.jev_retry_budget_seconds) == (
        1.25,
        1,
        3.0,
    )
    assert (s.jev_deadline_seconds, s.jev_max_in_flight) == (3.0, 8)


def test_enabled_jev_builds_its_config(make_settings):
    config = make_settings(jev_enabled=True, typesafe_api_key=f"  {FAKE_KEY} ").jev_config()
    assert config is not None
    assert config.api_key.get_secret_value() == FAKE_KEY
    assert (config.model, config.base_url, config.max_retries) == (
        "jev-1.13.0",
        "https://api.typesafe.ai",
        1,
    )


def test_a_key_without_enablement_still_builds_no_config(make_settings):
    assert make_settings(typesafe_api_key=FAKE_KEY).jev_config() is None


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_key_is_unset(make_settings, empty):
    assert make_settings(typesafe_api_key=empty).typesafe_api_key is None
    with pytest.raises(ValidationError):
        make_settings(jev_enabled=True, typesafe_api_key=empty)


@pytest.mark.parametrize(
    "overrides",
    [
        {"jev_enabled": True},
        {"jev_enabled": "yes"},
        {"jev_enabled": 1},
        {"typesafe_api_key": "has space"},
        {"typesafe_api_key": "nön-ascii"},
        {"jev_model": "jev-latest"},
        {"jev_model": "jev-preview"},
        {"jev_model": "jev"},
        {"jev_model": "JEV-1.13.0"},
        {"jev_model": "jev-1.13"},
        {"jev_base_url": "http://api.typesafe.ai"},
        {"jev_base_url": "https://user:pw@api.typesafe.ai"},
        {"jev_base_url": "https://api.typesafe.ai?x=1"},
        {"jev_base_url": "https://"},
        {"jev_attempt_timeout_seconds": 0},
        {"jev_attempt_timeout_seconds": 11},
        {"jev_attempt_timeout_seconds": "nan"},
        {"jev_attempt_timeout_seconds": True},
        {"jev_retry_budget_seconds": "inf"},
        {"jev_retry_budget_seconds": 31},
        {"jev_max_retries": 3},
        {"jev_max_retries": -1},
        {"jev_max_retries": "1.0"},
        {"jev_max_retries": True},
        # Retries must stay reachable: retries * (attempt timeout + 0.25 s backoff) < budget.
        {"jev_max_retries": 2},
        {"jev_attempt_timeout_seconds": 2.75},
        {"jev_retry_budget_seconds": 1.5},
        # FR-DEC-3 deadline and in-flight bound.
        {"jev_deadline_seconds": 0},
        {"jev_deadline_seconds": 31},
        {"jev_deadline_seconds": "nan"},
        {"jev_deadline_seconds": True},
        {"jev_max_in_flight": 0},
        {"jev_max_in_flight": 65},
        {"jev_max_in_flight": True},
        {"jev_max_in_flight": "1.5"},
        # The attempt timeout and retry budget must fit inside the deadline.
        {"jev_deadline_seconds": 2.5},
        {"jev_deadline_seconds": 1.0, "jev_retry_budget_seconds": 1.0, "jev_max_retries": 0},
    ],
)
def test_invalid_jev_settings_fail_even_while_disabled(make_settings, overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_valid_jev_overrides(make_settings):
    s = make_settings(
        jev_model="jev-1.14.0",
        jev_base_url="https://api.typesafe.ai/",
        jev_max_retries=0,
        jev_attempt_timeout_seconds=0.5,
        jev_retry_budget_seconds=0.5,
    )
    assert (s.jev_model, s.jev_base_url, s.jev_max_retries) == (
        "jev-1.14.0",
        "https://api.typesafe.ai",
        0,
    )


def test_jev_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("JEV_ENABLED", "TRUE")
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    monkeypatch.setenv("JEV_ATTEMPT_TIMEOUT_SECONDS", "1.0")
    monkeypatch.setenv("JEV_MAX_RETRIES", "1")
    monkeypatch.setenv("JEV_RETRY_BUDGET_SECONDS", "2.5")
    monkeypatch.setenv("JEV_DEADLINE_SECONDS", "2.75")
    monkeypatch.setenv("JEV_MAX_IN_FLIGHT", "4")
    s = Settings(_env_file=None)
    config = s.jev_config()
    assert config is not None and config.api_key.get_secret_value() == FAKE_KEY
    assert (config.attempt_timeout_seconds, config.retry_budget_seconds) == (1.0, 2.5)
    assert (config.deadline_seconds, config.max_in_flight) == (2.75, 4)


def test_the_key_never_appears_in_reprs_or_validation_errors(make_settings):
    s = make_settings(jev_enabled=True, typesafe_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(s) and FAKE_KEY not in repr(s.jev_config())
    # A model-level error from an unrelated field would otherwise echo every input value.
    for overrides in (
        {"typesafe_api_key": FAKE_KEY, "search_default_top_k": 60},
        {"typesafe_api_key": f"{FAKE_KEY} x"},
        {"typesafe_api_key": FAKE_KEY, "jev_max_retries": 2},
    ):
        with pytest.raises(ValidationError) as caught:
            make_settings(**overrides)
        assert FAKE_KEY not in str(caught.value) and FAKE_KEY not in repr(caught.value)


# Structured-error disclosure (fake secrets only). Pydantic records raw inputs in `errors()` and
# `json()` even with hide_input_in_errors=True, and a model-level error records the whole input
# dict; `_SecretInputSource` wraps secret values as SecretStr in every source before validation.
FAKE_PW = "pw-test-not-real-4b2e"
SOURCES = ["init", "env", "dotenv", "secrets_dir"]


def _assert_no_fake_secret(error: ValidationError) -> None:
    text = " ".join(
        [
            str(error),
            repr(error),
            repr(error.errors()),
            error.json(),
            repr(error.errors(include_input=False)),
        ]
    )
    assert FAKE_KEY not in text and FAKE_PW not in text


def _settings_from(source, tmp_path, monkeypatch, *, key=FAKE_KEY, cls=Settings, **init):
    """FAKE_PW and `key` arrive through exactly one source; `init` overrides always come in as
    constructor arguments."""
    kwargs: dict = {"_env_file": None}
    if source == "init":
        kwargs.update(postgres_password=FAKE_PW, typesafe_api_key=key)
    elif source == "env":
        monkeypatch.setenv("POSTGRES_PASSWORD", FAKE_PW)
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
    elif source == "dotenv":
        dotenv = tmp_path / "fake.env"
        dotenv.write_text(
            f"POSTGRES_PASSWORD={FAKE_PW}\nTYPESAFE_API_KEY={key}\n", encoding="utf-8"
        )
        kwargs["_env_file"] = dotenv
    else:
        secrets = tmp_path / "secrets"
        secrets.mkdir()
        (secrets / "postgres_password").write_text(FAKE_PW, encoding="utf-8")
        (secrets / "typesafe_api_key").write_text(key, encoding="utf-8")
        kwargs["_secrets_dir"] = secrets
    return cls(**kwargs, **init)


def test_secret_fields_are_derived_from_secretstr_annotations():
    assert _secret_input_names(Settings.model_fields) == {
        "postgres_password",
        "typesafe_api_key",
        "gemini_api_key",
    }


@pytest.mark.parametrize("source", SOURCES)
def test_secret_values_still_arrive_unchanged_from_every_source(source, tmp_path, monkeypatch):
    s = _settings_from(source, tmp_path, monkeypatch, jev_enabled=True)
    assert s.postgres_password.get_secret_value() == FAKE_PW
    assert s.database_url().password == FAKE_PW
    assert s.jev_config().api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(s) and FAKE_PW not in repr(s)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "init",
    [{"search_default_top_k": 60}, {"jev_max_retries": 2}, {"search_rerank_depth": 51}],
    ids=["model-level-top-k", "model-level-jev-budget", "model-level-rerank-depth"],
)
def test_model_level_errors_never_carry_fake_secrets(source, init, tmp_path, monkeypatch):
    with pytest.raises(ValidationError) as caught:
        _settings_from(source, tmp_path, monkeypatch, **init)
    _assert_no_fake_secret(caught.value)


@pytest.mark.parametrize("source", SOURCES)
def test_key_format_errors_never_carry_the_fake_key(source, tmp_path, monkeypatch):
    with pytest.raises(ValidationError) as caught:
        _settings_from(source, tmp_path, monkeypatch, key=f"{FAKE_KEY}\tx")
    assert [e["loc"] for e in caught.value.errors()] == [("typesafe_api_key",)]
    _assert_no_fake_secret(caught.value)


def test_existing_secret_validation_is_preserved(make_settings, monkeypatch):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, postgres_password="", typesafe_api_key=FAKE_KEY)
    assert [e["type"] for e in caught.value.errors()] == ["too_short"]
    _assert_no_fake_secret(caught.value)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, typesafe_api_key=FAKE_KEY)
    assert [e["type"] for e in caught.value.errors()] == ["missing"]
    _assert_no_fake_secret(caught.value)
    with pytest.raises(ValidationError) as caught:
        make_settings(postgres_password=FAKE_PW, jev_enabled=True)
    _assert_no_fake_secret(caught.value)
    assert make_settings(typesafe_api_key="   ").typesafe_api_key is None


def test_secret_source_precedence_is_unchanged(tmp_path, monkeypatch):
    # Default order: constructor > environment > dotenv > secrets directory.
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw-env-fake")
    dotenv = tmp_path / "fake.env"
    dotenv.write_text(
        "POSTGRES_PASSWORD=pw-dotenv-fake\nTYPESAFE_API_KEY=key-dotenv-fake\n", encoding="utf-8"
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "postgres_password").write_text("pw-secrets-fake", encoding="utf-8")
    (secrets / "typesafe_api_key").write_text("key-secrets-fake", encoding="utf-8")

    def build(**init):
        s = Settings(_env_file=dotenv, _secrets_dir=secrets, **init)
        return s.postgres_password.get_secret_value(), s.typesafe_api_key.get_secret_value()

    assert build(typesafe_api_key="key-init-fake") == ("pw-env-fake", "key-init-fake")
    assert build() == ("pw-env-fake", "key-dotenv-fake")
    monkeypatch.delenv("POSTGRES_PASSWORD")
    dotenv.write_text("POSTGRES_PASSWORD=pw-dotenv-fake\n", encoding="utf-8")
    assert build() == ("pw-dotenv-fake", "key-secrets-fake")


class _AliasedSettings(Settings):
    typesafe_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("typesafe_api_key", "TS_TEST_ALIAS_KEY")
    )


def test_aliased_secret_inputs_are_masked_too(monkeypatch):
    assert "ts_test_alias_key" in _secret_input_names(_AliasedSettings.model_fields)
    s = _AliasedSettings(_env_file=None, postgres_password=FAKE_PW, TS_TEST_ALIAS_KEY=FAKE_KEY)
    assert s.typesafe_api_key.get_secret_value() == FAKE_KEY
    for build in (
        lambda: _AliasedSettings(
            _env_file=None,
            postgres_password=FAKE_PW,
            TS_TEST_ALIAS_KEY=FAKE_KEY,
            search_default_top_k=60,
        ),
        lambda: _AliasedSettings(
            _env_file=None, postgres_password=FAKE_PW, search_default_top_k=60
        ),
    ):
        monkeypatch.setenv("TS_TEST_ALIAS_KEY", FAKE_KEY)
        with pytest.raises(ValidationError) as caught:
            build()
        _assert_no_fake_secret(caught.value)
    assert (
        _AliasedSettings(
            _env_file=None, postgres_password=FAKE_PW
        ).typesafe_api_key.get_secret_value()
        == FAKE_KEY
    )
