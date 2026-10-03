"""Milestone 8 Session C: the concrete cross-encoder provider with fake model libraries.

`torch` and `sentence_transformers` are replaced in `sys.modules` by small fakes, so these tests
never import or load a real model. They cover the lifecycle (cheap construction, lazy single
load, serialized inference), the library call contract (CPU, offline, no remote code, Identity
activation, no truncation) and the sanitized failure boundary. The real pinned snapshot is
exercised only by the `real_model` tests."""

import contextlib
import json
import logging
import math
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from ecommerce_search.embeddings.fetch import MISSING_PROBLEM
from ecommerce_search.embeddings.spec import snapshot_dir
from ecommerce_search.reranking import cross_encoder_provider as cep
from ecommerce_search.reranking.cross_encoder_provider import (
    CrossEncoderReranker,
    snapshot_config_problems,
)
from ecommerce_search.reranking.provider import RerankerUnavailable, score_documents
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2, RerankerModelSpec

pytestmark = pytest.mark.usefixtures("no_socket_connect")

SPEC = MS_MARCO_MINILM_L6_V2
SECRET = "sk-SECRET-text C:\\private\\path"  # noqa: S105 - leak sentinel, not a credential


# ---- fake model libraries ------------------------------------------------------------------


class Identity:
    pass


class Sigmoid:
    pass


class FakeArray:
    def __init__(self, values, shape=None):
        self._values = values
        self.shape = shape if shape is not None else (len(values),)

    def tolist(self):
        return list(self._values)


def word_count_pairs(queries, documents, **kwargs):
    # [CLS] query [SEP] document [SEP]: words + 3 special tokens, like a wordpiece pair.
    return {
        "input_ids": [
            [0] * (len(q.split()) + len(d.split()) + 3)
            for q, d in zip(queries, documents, strict=True)
        ]
    }


class FakeTokenizer:
    def __init__(self, state, max_length):
        self.state = state
        self.model_max_length = max_length

    def __call__(self, queries, documents, **kwargs):
        with self.state.active_call():
            if self.state.tokenizer_delay:
                time.sleep(self.state.tokenizer_delay)
            self.state.tokenizer_calls.append((list(queries), list(documents), kwargs))
            if self.state.tokenizer_error is not None:
                raise self.state.tokenizer_error
            if self.state.tokenizer_output is not None:
                return self.state.tokenizer_output(queries, documents)
            return word_count_pairs(queries, documents)


class State:
    def __init__(self):
        self.constructions = []
        self.environ_at_construction = []
        self.construct_error = None
        self.construct_delay = 0.0
        self.num_labels = 1
        self.activation = None  # None = keep the passed activation_fn
        self.tokenizer_max = None  # None = the passed max_length
        self.device = "cpu"
        self.tokenizer_calls = []
        self.tokenizer_error = None
        self.tokenizer_output = None
        self.tokenizer_delay = 0.0
        self.predict_calls = []
        self.predict_error = None
        self.predict_output = None
        self.predict_delay = 0.0
        self.active = 0
        self.max_active = 0
        self.inference_modes = 0
        self.lock = threading.Lock()

    @contextlib.contextmanager
    def active_call(self):
        """Track how many tokenizer or predict calls run at once (the inference lock's job)."""
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1


def make_libraries(state):
    torch = types.ModuleType("torch")
    torch.nn = types.SimpleNamespace(Identity=Identity, Sigmoid=Sigmoid)

    @contextlib.contextmanager
    def inference_mode():
        state.inference_modes += 1
        yield

    torch.inference_mode = inference_mode

    class CrossEncoder:
        def __init__(self, path, **kwargs):
            state.environ_at_construction.append({k: os.environ.get(k) for k in cep.OFFLINE_ENV})
            if state.construct_delay:
                time.sleep(state.construct_delay)
            state.constructions.append((path, kwargs))
            if state.construct_error is not None:
                raise state.construct_error
            self.num_labels = state.num_labels
            self.activation_fn = state.activation() if state.activation else kwargs["activation_fn"]
            max_length = state.tokenizer_max or kwargs["max_length"]
            self.tokenizer = FakeTokenizer(state, max_length)
            self.max_seq_length = max_length
            self.device = state.device
            self.evaluated = False

        def eval(self):
            self.evaluated = True
            return self

        def predict(self, pairs, **kwargs):
            with state.active_call():
                if state.predict_delay:
                    time.sleep(state.predict_delay)
                state.predict_calls.append((list(pairs), kwargs))
                if state.predict_error is not None:
                    raise state.predict_error
                if state.predict_output is not None:
                    return state.predict_output(pairs)
                # Deterministic raw logits outside [0, 1]: a sigmoid would be visible.
                return FakeArray([float(len(d)) - 10.0 for _, d in pairs])

    st = types.ModuleType("sentence_transformers")
    st.CrossEncoder = CrossEncoder
    return torch, st


@pytest.fixture
def state(monkeypatch):
    s = State()
    torch, st = make_libraries(s)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    for key in cep.OFFLINE_ENV:
        monkeypatch.delenv(key, raising=False)
    return s


@pytest.fixture
def verified(monkeypatch):
    """Pretend the snapshot passed manifest and config verification (tested separately)."""
    calls = []
    monkeypatch.setattr(cep, "verify_manifest", lambda *a, **k: calls.append((a, k)) or [])
    monkeypatch.setattr(cep, "snapshot_config_problems", lambda *a: [])
    return calls


def provider(tmp_path, **kwargs):
    return CrossEncoderReranker(SPEC, tmp_path, **kwargs)


# ---- construction ----------------------------------------------------------------------------


def test_construction_touches_no_files_or_model(tmp_path, state):
    missing = tmp_path / "nowhere"
    reranker = CrossEncoderReranker(SPEC, missing)
    assert reranker.spec is SPEC and not reranker.loaded
    assert not missing.exists() and state.constructions == []


@pytest.mark.parametrize("batch_size", [0, 257, -1, True, 16.0, "16", None])
def test_batch_size_is_validated(tmp_path, batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        CrossEncoderReranker(SPEC, tmp_path, batch_size=batch_size)


@pytest.mark.parametrize("batch_size", [1, 16, 256])
def test_batch_size_bounds_are_inclusive(tmp_path, batch_size):
    CrossEncoderReranker(SPEC, tmp_path, batch_size=batch_size)


def test_spec_must_be_a_reranker_spec(tmp_path):
    with pytest.raises(TypeError):
        CrossEncoderReranker(SPEC.config(), tmp_path)  # type: ignore[arg-type]


# ---- loading ---------------------------------------------------------------------------------


def test_load_contract_cpu_offline_local_no_remote_code_identity(tmp_path, state, verified):
    reranker = provider(tmp_path)
    elapsed = reranker.load()
    assert isinstance(elapsed, float) and elapsed >= 0 and reranker.loaded
    ((path, kwargs),) = state.constructions
    assert Path(path) == snapshot_dir(tmp_path, SPEC.model_id, SPEC.revision)
    assert kwargs["device"] == "cpu"
    assert kwargs["trust_remote_code"] is False
    assert kwargs["local_files_only"] is True
    assert kwargs["max_length"] == SPEC.max_length == 512
    assert type(kwargs["activation_fn"]) is Identity
    assert "revision" not in kwargs and "token" not in kwargs
    assert state.environ_at_construction == [dict(cep.OFFLINE_ENV)]
    assert reranker._model.evaluated
    # Verification ran before loading, against the strict reranker profile.
    ((args, kw),) = verified
    assert args == (tmp_path, SPEC.model_id, SPEC.revision)
    assert kw["profile"].kind == "reranker" and kw["profile"].strict


def test_load_happens_once_and_returns_none_afterwards(tmp_path, state, verified):
    reranker = provider(tmp_path)
    assert reranker.load() is not None
    assert reranker.load() is None
    reranker.score("q", ["a"])
    reranker.count_pair_tokens("q", ["a"])
    assert len(state.constructions) == 1 and len(verified) == 1


def test_concurrent_first_loads_construct_one_model(tmp_path, state, verified):
    state.construct_delay = 0.05
    reranker = provider(tmp_path)
    results = []
    threads = [threading.Thread(target=lambda: results.append(reranker.load())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(state.constructions) == 1 and len(results) == 8
    assert sum(r is not None for r in results) == 1  # one load time; the others saw it loaded


def test_missing_snapshot_is_reported_before_any_model_import(tmp_path, state, monkeypatch):
    monkeypatch.delitem(sys.modules, "sentence_transformers")
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).load()
    assert info.value.reason == "snapshot_missing"
    assert "sentence_transformers" not in sys.modules and state.constructions == []


@pytest.mark.parametrize(
    "problems",
    [
        ["changed file model.safetensors"],
        ["snapshot contains unexpected files: x.bin"],
        ["manifest is not valid JSON"],
        ["manifest does not describe this model and revision"],
        [MISSING_PROBLEM, "changed file config.json"],
    ],
)
def test_integrity_problems_are_load_failed(tmp_path, state, monkeypatch, problems):
    monkeypatch.setattr(cep, "verify_manifest", lambda *a, **k: problems)
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).load()
    assert info.value.reason == "load_failed" and state.constructions == []


def test_unreadable_snapshot_is_load_failed(tmp_path, state, monkeypatch):
    def unreadable(*args, **kwargs):
        raise PermissionError(SECRET)

    monkeypatch.setattr(cep, "verify_manifest", unreadable)
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).load()
    assert info.value.reason == "load_failed" and SECRET not in str(info.value)


def test_config_problems_are_config_mismatch_before_loading(tmp_path, state, monkeypatch):
    monkeypatch.setattr(cep, "verify_manifest", lambda *a, **k: [])
    monkeypatch.setattr(cep, "snapshot_config_problems", lambda *a: ["differs"])
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).load()
    assert info.value.reason == "config_mismatch" and state.constructions == []


@pytest.mark.parametrize(
    "change",
    [
        {"num_labels": 2},
        {"activation": Sigmoid},
        {"tokenizer_max": 256},
        {"device": "cuda:0"},
    ],
)
def test_loaded_model_must_match_the_spec(tmp_path, state, verified, change):
    for key, value in change.items():
        setattr(state, key, value)
    reranker = provider(tmp_path)
    with pytest.raises(RerankerUnavailable) as info:
        reranker.load()
    assert info.value.reason == "config_mismatch" and not reranker.loaded


def test_library_failure_is_sanitized_and_not_cached(tmp_path, state, verified, caplog):
    caplog.set_level(logging.DEBUG)
    state.construct_error = OSError(SECRET)
    reranker = provider(tmp_path)
    with pytest.raises(RerankerUnavailable) as info:
        reranker.load()
    assert str(info.value) == "reranker unavailable: load_failed"
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert SECRET not in caplog.text
    state.construct_error = None
    assert reranker.load() is not None and reranker.loaded  # retried, then loaded


# ---- token counting --------------------------------------------------------------------------


def test_counts_pairs_with_special_tokens_and_without_truncation(tmp_path, state, verified):
    reranker = provider(tmp_path)
    counts = reranker.count_pair_tokens("running shoes", ["nike shoe", "a b c d"])
    assert counts == [7, 9]
    ((queries, documents, kwargs),) = state.tokenizer_calls
    assert queries == ["running shoes"] * 2 and documents == ["nike shoe", "a b c d"]
    assert kwargs["add_special_tokens"] is True
    assert kwargs["truncation"] is False and kwargs["padding"] is False


def test_empty_documents_do_no_model_work(tmp_path, state):
    reranker = provider(tmp_path / "absent")
    assert reranker.count_pair_tokens("q", []) == []
    assert reranker.score("q", []) == []
    assert reranker.score("q", ()) == []
    assert state.constructions == [] and not reranker.loaded


@pytest.mark.parametrize(
    "output",
    [
        lambda q, d: {},
        lambda q, d: {"input_ids": None},
        lambda q, d: {"input_ids": [[0]] * (len(d) + 1)},
        lambda q, d: {"input_ids": [5] * len(d)},
        lambda q, d: None,
    ],
)
def test_malformed_tokenizer_output_is_invalid_output(tmp_path, state, verified, output):
    state.tokenizer_output = output
    reranker = provider(tmp_path)
    for call in (reranker.count_pair_tokens, reranker.score):
        with pytest.raises(RerankerUnavailable) as info:
            call("q", ["a", "b"])
        assert info.value.reason == "invalid_output"
    assert state.predict_calls == []


def test_tokenizer_failure_is_score_failed(tmp_path, state, verified):
    state.tokenizer_error = RuntimeError(SECRET)
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).score("q", ["a"])
    assert info.value.reason == "score_failed" and SECRET not in str(info.value)


def test_overlength_pair_is_refused_before_inference(tmp_path, state, verified):
    reranker = provider(tmp_path)
    fits = " ".join(["w"] * (512 - 3 - 1))  # 1 query word + 3 special tokens = exactly 512
    assert reranker.count_pair_tokens("q", [fits]) == [512]
    assert reranker.score("q", [fits]) == [float(len(fits)) - 10.0]
    state.predict_calls.clear()
    with pytest.raises(RerankerUnavailable) as info:
        reranker.score("q", ["short", fits + " w"])
    assert info.value.reason == "input_too_long"
    assert state.predict_calls == []  # never truncated, never scored


def test_count_pair_tokens_reports_overlength_without_refusing(tmp_path, state, verified):
    # Counting returns the true length; refusing is the validator's (and score's) job.
    assert provider(tmp_path).count_pair_tokens("q", [" ".join(["w"] * 600)]) == [604]


# ---- scoring ---------------------------------------------------------------------------------


def test_scores_are_raw_logits_as_plain_floats_in_input_order(tmp_path, state, verified):
    reranker = provider(tmp_path, batch_size=4)
    documents = ["ccc", "a", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", ""]
    scores = reranker.score("query", documents)
    assert scores == [-7.0, -9.0, 20.0, -10.0]  # outside [0, 1]: no sigmoid applied
    assert all(type(s) is float for s in scores)
    ((pairs, kwargs),) = state.predict_calls
    assert pairs == [("query", d) for d in documents]
    assert type(kwargs["activation_fn"]) is Identity
    assert kwargs["apply_softmax"] is False and kwargs["batch_size"] == 4
    assert kwargs["convert_to_numpy"] is True and kwargs["show_progress_bar"] is False
    assert state.inference_modes == 1
    assert reranker.score("query", documents) == scores  # deterministic


def test_scores_are_not_thresholded(tmp_path, state, verified):
    state.predict_output = lambda pairs: FakeArray([-1e6, 0.0, 1e6])
    assert provider(tmp_path).score("q", ["a", "b", "c"]) == [-1e6, 0.0, 1e6]


def test_score_documents_validator_accepts_the_provider(tmp_path, state, verified):
    reranker = provider(tmp_path)
    reranker.load()
    assert score_documents(reranker, "q", ["aa", "b"]) == [-8.0, -9.0]


@pytest.mark.parametrize(
    "output",
    [
        lambda pairs: FakeArray([1.0] * len(pairs), shape=(len(pairs), 1)),
        lambda pairs: FakeArray([1.0] * (len(pairs) + 1)),
        lambda pairs: FakeArray([1.0, math.nan]),
        lambda pairs: FakeArray([1.0, math.inf]),
        lambda pairs: FakeArray([1.0, "2.0"]),
        lambda pairs: FakeArray([True, 1.0]),
        lambda pairs: [1.0, 2.0],  # not an array: no shape
        lambda pairs: None,
    ],
)
def test_malformed_model_output_is_invalid_output(tmp_path, state, verified, output):
    state.predict_output = output
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).score("q", ["a", "b"])
    assert info.value.reason == "invalid_output"


def test_inference_failure_is_score_failed_and_sanitized(tmp_path, state, verified, caplog):
    caplog.set_level(logging.DEBUG)
    state.predict_error = RuntimeError(SECRET)
    with pytest.raises(RerankerUnavailable) as info:
        provider(tmp_path).score("secret query text", ["secret document text"])
    assert str(info.value) == "reranker unavailable: score_failed"
    assert info.value.__cause__ is None and info.value.__suppress_context__
    for text in (SECRET, "secret query text", "secret document text"):
        assert text not in str(info.value) and text not in caplog.text


def test_scoring_and_token_counting_are_serialized(tmp_path, state, verified):
    # Tokenizer and predict calls share one activity counter: no two may overlap, across
    # concurrent score() and count_pair_tokens() calls on one instance.
    state.predict_delay = 0.02
    state.tokenizer_delay = 0.02
    reranker = provider(tmp_path)
    reranker.load()
    errors = []

    def work():
        try:
            reranker.score("q", ["a", "b"])
            reranker.count_pair_tokens("q", ["a"])
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(state.predict_calls) == 6
    assert len(state.tokenizer_calls) == 12  # 6 counts inside score() + 6 count_pair_tokens()
    assert state.max_active == 1


@pytest.mark.parametrize(
    ("query", "documents"),
    [(None, ["a"]), (b"q", ["a"]), ("q", "abc"), ("q", [b"a"]), ("q", ["a", 1]), ("q", None)],
)
def test_programming_errors_are_not_converted(tmp_path, state, verified, query, documents):
    reranker = provider(tmp_path)
    for call in (reranker.score, reranker.count_pair_tokens):
        with pytest.raises(TypeError):
            call(query, documents)
    assert state.constructions == []


# ---- offline snapshot configuration check ---------------------------------------------------

GOOD_CONFIG = {
    "_name_or_path": "cross-encoder/ms-marco-MiniLM-L-12-v2",  # upstream metadata, not identity
    "architectures": ["BertForSequenceClassification"],
    "model_type": "bert",
    "num_hidden_layers": 6,
    "id2label": {"0": "LABEL_0"},
    "max_position_embeddings": 512,
}
GOOD_TOKENIZER = {"model_max_length": 512, "tokenizer_class": "BertTokenizer"}


def write_snapshot_config(models_dir, config=GOOD_CONFIG, tokenizer=GOOD_TOKENIZER, spec=SPEC):
    snapshot = snapshot_dir(models_dir, spec.model_id, spec.revision)
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, value in (("config.json", config), ("tokenizer_config.json", tokenizer)):
        text = value if isinstance(value, str) else json.dumps(value)
        (snapshot / name).write_text(text, encoding="utf-8")
    return snapshot


def test_reviewed_config_has_no_problems(tmp_path):
    write_snapshot_config(tmp_path)
    assert snapshot_config_problems(tmp_path, SPEC) == []


@pytest.mark.parametrize(
    ("config_change", "tokenizer_change"),
    [
        ({"architectures": ["BertForMaskedLM"]}, {}),
        ({"model_type": "roberta"}, {}),
        ({"num_hidden_layers": 12}, {}),
        ({"id2label": {"0": "A", "1": "B"}}, {}),
        ({"max_position_embeddings": 256}, {}),
        ({"max_position_embeddings": "512"}, {}),
        ({}, {"model_max_length": 256}),
        ({}, {"model_max_length": 1000000000000000019884624838656}),
        ({"auto_map": {"AutoModel": "x.Y"}}, {}),
        ({}, {"auto_map": {"AutoTokenizer": "x.Y"}}),
    ],
)
def test_config_differences_are_reported(tmp_path, config_change, tokenizer_change):
    write_snapshot_config(
        tmp_path, {**GOOD_CONFIG, **config_change}, {**GOOD_TOKENIZER, **tokenizer_change}
    )
    problems = snapshot_config_problems(tmp_path, SPEC)
    assert problems and all(str(tmp_path) not in p for p in problems)


@pytest.mark.parametrize("bad", ["{not json", "[]", "\udcff"])
def test_unreadable_config_is_reported(tmp_path, bad):
    snapshot = write_snapshot_config(tmp_path)
    if bad == "\udcff":
        (snapshot / "config.json").write_bytes(b"\xff\xfe\x00")
    else:
        (snapshot / "config.json").write_text(bad, encoding="utf-8")
    assert snapshot_config_problems(tmp_path, SPEC) == [
        "model or tokenizer configuration is missing or not valid JSON"
    ]


def test_missing_config_is_reported(tmp_path):
    assert snapshot_config_problems(tmp_path, SPEC) == [
        "model or tokenizer configuration is missing or not valid JSON"
    ]


def test_a_model_without_a_reviewed_architecture_is_refused(tmp_path):
    other = RerankerModelSpec(model_id="test-org/other", revision="f" * 40, max_length=512)
    write_snapshot_config(tmp_path, spec=other)
    assert snapshot_config_problems(tmp_path, other) == [
        "no reviewed architecture is recorded for this model"
    ]
