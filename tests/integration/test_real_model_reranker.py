"""Offline smoke tests with the real, pinned cross-encoder snapshot (ADR-007). `-m real_model`.

They need the snapshot fetched into the git-ignored `models/` directory (`model-fetch --kind
reranker`) and FAIL (they are not skipped) when it is missing or modified. Only the V3 API
regression (also marked `integration`) uses a database: a scratch database, never the development
one, with the embedding snapshot (ADR-006) as well. Python outbound sockets and name resolution
are blocked for the whole module, including the model loads (the database is reached by libpq).

Nothing here asserts relevance or quality: the pin is a compatibility pin. Scores are checked
only as raw logits (finite, ordered, deterministic, equal to the model's own unactivated output);
their values are not compared with any expectation of which document is better. Corruption and
missing-snapshot cases use temporary copies or empty temporary directories; the canonical
snapshot is only read.
"""

import importlib.util
import math
import shutil
import socket
import sys
from pathlib import Path

import pytest
from catalog_support import PROVENANCE, SEED
from fastapi.testclient import TestClient

from ecommerce_search.api.app import create_app
from ecommerce_search.embeddings.fetch import RERANKER_PROFILE, verify_manifest
from ecommerce_search.embeddings.sentence_transformers_provider import SentenceTransformerEmbedder
from ecommerce_search.embeddings.spec import (
    ALL_MINILM_L6_V2,
    repository_models_dir,
    snapshot_dir,
)
from ecommerce_search.ingestion.loader import load_catalog
from ecommerce_search.ingestion.service import ingest_file
from ecommerce_search.reranking.cross_encoder_provider import (
    CrossEncoderReranker,
    snapshot_config_problems,
)
from ecommerce_search.reranking.documents import build_rerank_document
from ecommerce_search.reranking.provider import RerankerUnavailable, score_documents
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2, RerankerModelSpec
from ecommerce_search.search.dense_indexing import embed

pytestmark = pytest.mark.real_model

SPEC = MS_MARCO_MINILM_L6_V2
ROOT = Path(__file__).resolve().parents[2]
QUERY = "lightweight laptop for programming"
DOCUMENTS = [
    "Dell Inspiron 14 laptop with 16GB RAM and 512GB SSD",
    "Nike running shoes for men, black mesh",
    "",
    "Sony wireless noise cancelling over-ear headphones with 30 hour battery",
]
BLOCKED: list[str] = []
_SOCKET_CONNECT = socket.socket.connect  # captured before the module guard replaces it
LOOPBACK = frozenset({"127.0.0.1", "::1"})


def _refuse(name):
    def refuse(*args, **kwargs):
        BLOCKED.append(name)
        raise OSError("network access is not allowed in real-model tests")

    return refuse


@pytest.fixture(scope="module", autouse=True)
def no_network():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", _refuse("getaddrinfo"))
        patch.setattr(socket, "create_connection", _refuse("create_connection"))
        patch.setattr(socket.socket, "connect", _refuse("connect"))
        for key, value in {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }.items():
            patch.setenv(key, value)
        yield


def models_dir() -> Path:
    directory = repository_models_dir()
    assert directory is not None
    problems = verify_manifest(directory, SPEC.model_id, SPEC.revision, profile=RERANKER_PROFILE)
    assert problems == [], f"reranker snapshot missing or modified (run model-fetch): {problems}"
    assert snapshot_config_problems(directory, SPEC) == []
    return directory


@pytest.fixture(scope="module")
def reranker(no_network):
    provider = CrossEncoderReranker(SPEC, models_dir())
    assert provider.load() is not None
    return provider


def test_loads_offline_from_the_pinned_snapshot_with_a_compatible_config(reranker):
    model = reranker._model
    assert reranker.loaded and reranker.load() is None
    assert model.num_labels == SPEC.num_labels == 1
    assert type(model.activation_fn).__name__ == "Identity"
    assert model.tokenizer.model_max_length == SPEC.max_length == 512
    assert model.max_seq_length == 512
    assert str(model.device) == "cpu" and model.training is False
    config = model.model.config
    assert config.architectures == ["BertForSequenceClassification"]
    assert (config.model_type, config.num_hidden_layers) == ("bert", 6)
    assert config.max_position_embeddings == 512 and config.num_labels == 1
    snapshot = snapshot_dir(models_dir(), SPEC.model_id, SPEC.revision)
    assert Path(model.model.name_or_path).resolve() == snapshot.resolve()


def test_pair_counts_include_the_tokenizers_special_tokens(reranker):
    tokenizer = reranker._model.tokenizer
    ids = tokenizer("running shoes", "nike shoe", add_special_tokens=True)["input_ids"]
    assert ids[0] == tokenizer.cls_token_id and ids.count(tokenizer.sep_token_id) == 2
    assert reranker.count_pair_tokens("running shoes", ["nike shoe"]) == [len(ids)]
    # An empty document still counts as a pair: [CLS] query [SEP] [SEP], as the library scores it.
    empty = reranker._model.preprocess([("running shoes", "")])["input_ids"]
    assert reranker.count_pair_tokens("running shoes", [""]) == [empty.shape[1]] == [5]


def test_pair_counts_equal_the_library_preprocessing(reranker):
    counts = reranker.count_pair_tokens(QUERY, DOCUMENTS)
    for document, count in zip(DOCUMENTS, counts, strict=True):
        features = reranker._model.preprocess([(QUERY, document)])
        assert features["input_ids"].shape == (1, count)


def test_scores_are_finite_plain_float_raw_logits_in_input_order(reranker):
    scores = reranker.score(QUERY, DOCUMENTS)
    assert len(scores) == len(DOCUMENTS)
    assert all(type(s) is float and math.isfinite(s) for s in scores)
    # Raw logits, not probabilities: each equals the classifier's own unactivated output for
    # that pair, scored alone (so input order is preserved), and is not its sigmoid.
    torch = reranker._torch
    model = reranker._model
    for document, score in zip(DOCUMENTS, scores, strict=True):
        # List form, as the library batches pairs: a bare `tokenizer(q, "")` would drop an empty
        # second segment, while batched pairs keep `[CLS] q [SEP] [SEP]`.
        encoded = model.tokenizer([QUERY], [document], return_tensors="pt", truncation=False)
        with torch.inference_mode():
            logit = float(model.model(**encoded).logits[0][0])
        assert math.isclose(score, logit, rel_tol=1e-4, abs_tol=1e-4)
        assert not math.isclose(score, 1 / (1 + math.exp(-logit)), abs_tol=1e-3)
    reversed_scores = reranker.score(QUERY, list(reversed(DOCUMENTS)))
    for a, b in zip(scores, reversed(reversed_scores), strict=True):
        assert math.isclose(a, b, rel_tol=1e-4, abs_tol=1e-4)


def test_repeated_scoring_is_deterministic_within_the_process(reranker):
    first = reranker.score(QUERY, DOCUMENTS)
    assert all(reranker.score(QUERY, DOCUMENTS) == first for _ in range(3))
    reranker.load()
    assert score_documents(reranker, QUERY, DOCUMENTS) == first


def _benchmark_queries() -> tuple[str, ...]:
    """The committed M5 hybrid benchmark query set (no new or invented queries)."""
    name = "benchmark_hybrid_for_reranker_test"
    if name not in sys.modules:
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "scripts" / "benchmark_hybrid.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].QUERIES


def test_every_seed_rd1_pair_fits_without_truncation(reranker):
    documents = [build_rerank_document(line.record) for line in load_catalog(SEED).lines]
    assert len(documents) == 240
    queries = list(_benchmark_queries())
    # Worst case at the default SEARCH_MAX_QUERY_LENGTH (200): one token per character.
    queries.append("!" * 200)
    longest = 0
    for query in queries:
        counts = reranker.count_pair_tokens(query, documents)
        longest = max(longest, *counts)
    assert longest <= SPEC.max_length


def test_an_overlength_pair_is_refused_before_inference(reranker, monkeypatch):
    long_document = " ".join(["laptop"] * 600)
    assert reranker.count_pair_tokens("q", [long_document])[0] > SPEC.max_length

    def no_inference(*args, **kwargs):
        raise AssertionError("an over-length pair reached the model")

    monkeypatch.setattr(reranker._model, "predict", no_inference)
    with pytest.raises(RerankerUnavailable) as info:
        reranker.score("q", ["short", long_document])
    assert info.value.reason == "input_too_long"


def test_a_missing_snapshot_is_snapshot_missing(tmp_path):
    with pytest.raises(RerankerUnavailable) as info:
        CrossEncoderReranker(SPEC, tmp_path).load()
    assert info.value.reason == "snapshot_missing"


def _temporary_copy(tmp_path: Path) -> Path:
    source = models_dir()
    target = tmp_path / "models"
    snapshot = snapshot_dir(source, SPEC.model_id, SPEC.revision)
    shutil.copytree(snapshot, snapshot_dir(target, SPEC.model_id, SPEC.revision))
    shutil.copytree(source / "manifests", target / "manifests")
    return target


def test_a_corrupt_copy_is_load_failed_and_the_canonical_snapshot_is_untouched(tmp_path):
    copy = _temporary_copy(tmp_path)
    vocab = snapshot_dir(copy, SPEC.model_id, SPEC.revision) / "vocab.txt"
    vocab.write_bytes(vocab.read_bytes() + b"x")
    with pytest.raises(RerankerUnavailable) as info:
        CrossEncoderReranker(SPEC, copy).load()
    assert info.value.reason == "load_failed"
    assert str(tmp_path) not in str(info.value)
    models_dir()  # the canonical snapshot still verifies


def test_an_extra_file_in_a_copy_is_load_failed(tmp_path):
    copy = _temporary_copy(tmp_path)
    (snapshot_dir(copy, SPEC.model_id, SPEC.revision) / "pytorch_model.bin").write_bytes(b"x")
    with pytest.raises(RerankerUnavailable) as info:
        CrossEncoderReranker(SPEC, copy).load()
    assert info.value.reason == "load_failed"


def test_a_spec_that_contradicts_the_snapshot_is_config_mismatch():
    other = RerankerModelSpec(model_id=SPEC.model_id, revision=SPEC.revision, max_length=256)
    with pytest.raises(RerankerUnavailable) as info:
        CrossEncoderReranker(other, models_dir()).load()
    assert info.value.reason == "config_mismatch"


V3_QUERY = "hp laptop 8gb ram"  # a fixed probe; no relevance is asserted
OTHER_ROUTES = ("/search", "/search/dense", "/search/hybrid", "/search/filtered")


def _strip(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != "latency_ms"}


def _record_checkouts(provider, engine, checked: list) -> None:
    """Wrap the provider's model work to record the pool's checked-out connections."""
    for name in ("load", "count_pair_tokens", "score", "embed_query"):
        original = getattr(provider, name, None)
        if original is None:
            continue

        def wrapper(*args, _name=name, _original=original, **kwargs):
            checked.append((_name, engine.pool.checkedout()))
            return _original(*args, **kwargs)

        setattr(provider, name, wrapper)


def _loopback_connect(sock, address, *args, **kwargs):
    """`socket.connect` limited to loopback addresses (anything else is refused and recorded)."""
    if isinstance(address, tuple) and address[0] in LOOPBACK:
        return _SOCKET_CONNECT(sock, address, *args, **kwargs)
    BLOCKED.append("connect")
    raise OSError("network access is not allowed in real-model tests")


@pytest.mark.integration
def test_v3_api_with_the_real_models_on_a_scratch_database(
    settings, migrated_engine, tmp_path, monkeypatch
):
    # The test client's event loop needs its loopback self-pipe (on Windows, `socketpair` connects
    # to 127.0.0.1). Name resolution and `create_connection` stay blocked by the module guard.
    monkeypatch.setattr(socket.socket, "connect", _loopback_connect)
    embedding_dir = repository_models_dir()
    assert (
        verify_manifest(embedding_dir, ALL_MINILM_L6_V2.model_id, ALL_MINILM_L6_V2.revision) == []
    )
    models_dir()  # the reranker snapshot verifies
    ingest_file(migrated_engine, SEED, PROVENANCE)
    embedder = SentenceTransformerEmbedder(ALL_MINILM_L6_V2, embedding_dir)
    assert embed(migrated_engine, embedder).inserted == 240
    scratch = settings.model_copy(update={"postgres_db": migrated_engine.url.database})
    assert scratch.postgres_db != settings.postgres_db  # never the development database
    assert scratch.postgres_db.startswith("ecommerce_search_test_")
    assert scratch.reranker_enabled and scratch.search_rerank_depth == 20

    # Enabled: the app's own lazily loaded reranker over the canonical snapshots.
    app = create_app(scratch)
    reranker = app.state.reranker
    assert isinstance(reranker, CrossEncoderReranker) and not reranker.loaded
    checked: list = []
    with TestClient(app) as client:
        engine = app.state.engine
        _record_checkouts(reranker, engine, checked)
        _record_checkouts(app.state.embedder, engine, checked)
        responses = [
            client.get("/search/reranked", params={"q": V3_QUERY, "top_k": 50}),
            client.post("/search/reranked", json={"query": V3_QUERY, "top_k": 50}),
            client.get("/search/reranked", params={"q": V3_QUERY, "top_k": 50}),
        ]
        v2 = client.get("/search/filtered", params={"q": V3_QUERY, "top_k": 50})
        assert engine.pool.checkedout() == 0
    assert all(r.status_code == 200 for r in responses) and v2.status_code == 200
    first = responses[0].json()
    assert first["reranker"]["status"] == "applied"
    assert first["reranker"]["model_id"] == SPEC.model_id
    assert first["reranker"]["model_revision"] == SPEC.revision
    assert first["reranker"]["score_type"] == "logit"
    assert first["candidate_count"] > 0
    assert first["reranker"]["reranked_count"] == min(20, first["candidate_count"])
    # Deterministic within the process: repeated GET/POST requests return identical bodies.
    assert _strip(first) == _strip(responses[1].json()) == _strip(responses[2].json())
    assert first["latency_ms"]["reranker_load_ms"] is not None
    assert responses[1].json()["latency_ms"]["reranker_load_ms"] is None  # loaded once
    # The candidate set, its RRF order and its counts are exactly V2's.
    v2_body = v2.json()
    v2_ids = [r["product_id"] for r in v2_body["results"]]
    assert sorted(r["product_id"] for r in first["results"]) == sorted(v2_ids)
    by_rrf = sorted(first["results"], key=lambda r: r["rrf_rank"])
    assert [r["product_id"] for r in by_rrf] == v2_ids
    for key in ("lexical_hit_count", "dense_hit_count", "fused_count", "candidate_count"):
        assert first[key] == v2_body[key], key
    assert first["applied_filters"] == v2_body["applied_filters"]
    size = first["reranker"]["reranked_count"]
    head, tail = first["results"][:size], first["results"][size:]
    scores = [r["rerank_score"] for r in head]
    assert all(type(score) is float and math.isfinite(score) for score in scores)
    assert scores == sorted(scores, reverse=True)  # raw logits, ordered; values not judged
    assert [r["rerank_rank"] for r in head] == list(range(1, size + 1))
    assert sorted(r["rrf_rank"] for r in head) == list(range(1, size + 1))
    assert all(r["rerank_rank"] is None and r["rerank_score"] is None for r in tail)
    # No database connection was checked out while either model loaded, counted or scored.
    assert {name for name, _n in checked} >= {"load", "count_pair_tokens", "score", "embed_query"}
    assert all(n == 0 for _name, n in checked)

    # Disabled by configuration: V2 order, and the reranker is never loaded.
    disabled = create_app(scratch.model_copy(update={"reranker_enabled": False}))
    with TestClient(disabled) as client:
        off = client.get("/search/reranked", params={"q": V3_QUERY, "top_k": 50})
    assert off.status_code == 200 and off.json()["reranker"]["status"] == "disabled"
    assert [r["product_id"] for r in off.json()["results"]] == v2_ids
    assert not disabled.state.reranker.loaded

    # A missing reranker snapshot affects V3 only; the other routes keep serving.
    missing = create_app(scratch)
    missing.state.reranker = CrossEncoderReranker(SPEC, tmp_path)  # an empty models directory
    with TestClient(missing) as client:
        failed = client.get("/search/reranked", params={"q": V3_QUERY})
        assert failed.status_code == 503 and failed.json() == {"detail": "search unavailable"}
        assert client.get("/health").status_code == 200
        for route in OTHER_ROUTES:
            assert client.get(route, params={"q": V3_QUERY}).status_code == 200, route
        assert missing.state.engine.pool.checkedout() == 0
    assert str(tmp_path) not in failed.text


def test_no_socket_was_used():
    assert BLOCKED == []
