"""Offline smoke tests with the real, pinned cross-encoder snapshot (ADR-007). `-m real_model`.

They need the snapshot fetched into the git-ignored `models/` directory (`model-fetch --kind
reranker`) and FAIL (they are not skipped) when it is missing or modified. No database is used.
Outbound sockets and name resolution are blocked for the whole module, including the model load.

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
from catalog_support import SEED

from ecommerce_search.embeddings.fetch import RERANKER_PROFILE, verify_manifest
from ecommerce_search.embeddings.spec import repository_models_dir, snapshot_dir
from ecommerce_search.ingestion.loader import load_catalog
from ecommerce_search.reranking.cross_encoder_provider import (
    CrossEncoderReranker,
    snapshot_config_problems,
)
from ecommerce_search.reranking.documents import build_rerank_document
from ecommerce_search.reranking.provider import RerankerUnavailable, score_documents
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2, RerankerModelSpec

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


def test_no_socket_was_used():
    assert BLOCKED == []
