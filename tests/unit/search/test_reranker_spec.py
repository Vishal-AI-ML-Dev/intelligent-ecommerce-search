"""Milestone 8 Session A: `RerankerModelSpec` validation and configuration hashing.

Only a test-local spec is used: there is no production reranker registry or pin yet."""

import dataclasses
import hashlib
import json

import pytest

from ecommerce_search.embeddings.spec import SpecError
from ecommerce_search.reranking import spec as rs
from ecommerce_search.reranking.documents import RERANK_DOCUMENT_VERSION
from ecommerce_search.reranking.spec import RerankerModelSpec

pytestmark = pytest.mark.usefixtures("no_socket_connect")

REV = "0123456789abcdef0123456789abcdef01234567"


def _spec(**overrides) -> RerankerModelSpec:
    values = {"model_id": "test-org/test-reranker", "revision": REV, "max_length": 512}
    values.update(overrides)
    return RerankerModelSpec(**values)


def test_valid_spec_defaults():
    spec = _spec()
    assert (spec.num_labels, spec.score_type) == (1, "logit")


@pytest.mark.parametrize(
    "model_id",
    ["", "no-slash", "a/b/c", "org/../x", "/name", "org/", " org/name", "org/na me", 5, None],
)
def test_model_id_is_validated(model_id):
    with pytest.raises(SpecError):
        _spec(model_id=model_id)


@pytest.mark.parametrize(
    "revision",
    ["main", "v1.0", REV[:-1], REV + "0", REV.upper(), "g" * 40, "", 123, None],
)
def test_revision_must_be_a_full_lowercase_commit_hash(revision):
    with pytest.raises(SpecError):
        _spec(revision=revision)


@pytest.mark.parametrize("max_length", [0, -1, 8193, True, False, 512.0, "512", None])
def test_max_length_bounds_and_type(max_length):
    with pytest.raises(SpecError):
        _spec(max_length=max_length)


@pytest.mark.parametrize("max_length", [1, 512, 8192])
def test_max_length_accepted_values(max_length):
    assert _spec(max_length=max_length).max_length == max_length


@pytest.mark.parametrize("num_labels", [0, 2, True, 1.0])
def test_num_labels_must_be_one(num_labels):
    with pytest.raises(SpecError):
        _spec(num_labels=num_labels)


@pytest.mark.parametrize(
    "score_type", ["probability", "sigmoid", "LOGIT", "", None, ["logit"], {"logit": 1}, b"logit"]
)
def test_score_type_must_be_logit(score_type):
    with pytest.raises(SpecError):
        _spec(score_type=score_type)


def test_spec_is_frozen():
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.max_length = 10  # type: ignore[misc]


def test_config_includes_the_document_version_and_hash_is_canonical():
    spec = _spec()
    assert spec.config() == {
        "model_id": "test-org/test-reranker",
        "revision": REV,
        "max_length": 512,
        "num_labels": 1,
        "score_type": "logit",
        "rerank_document_version": "rd-1",
    }
    assert RERANK_DOCUMENT_VERSION == "rd-1"
    expected = hashlib.sha256(
        json.dumps(spec.config(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert spec.config_sha256() == expected
    assert spec.config_sha256() == _spec().config_sha256()


@pytest.mark.parametrize(
    "change",
    [
        {"model_id": "test-org/other"},
        {"revision": "f" * 40},
        {"max_length": 256},
    ],
)
def test_every_field_changes_the_hash(change):
    assert _spec(**change).config_sha256() != _spec().config_sha256()


def test_document_version_changes_the_hash(monkeypatch):
    before = _spec().config_sha256()
    monkeypatch.setattr(rs, "RERANK_DOCUMENT_VERSION", "rd-2")
    assert _spec().config_sha256() != before


def test_no_production_registry_or_default_pin_exists_yet():
    names = {name for name in vars(rs) if not name.startswith("_")}
    assert not {n for n in names if "REGISTRY" in n or n.endswith("_MODELS") or "DEFAULT" in n}
    assert not [v for v in vars(rs).values() if isinstance(v, RerankerModelSpec)]


def test_hash_matches_an_independently_written_canonical_form():
    # Hand-written canonical JSON (sorted keys, no spaces), hashed here without the spec's code.
    canonical = (
        '{"max_length":512,"model_id":"test-org/test-reranker","num_labels":1,'
        '"rerank_document_version":"rd-1","revision":"' + REV + '","score_type":"logit"}'
    )
    assert _spec().config_sha256() == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_id": r"evil/C:\secret\path"},
        {"revision": "sk-SECRET-token"},
        {"max_length": "sk-SECRET-token"},
        {"num_labels": "sk-SECRET-token"},
        {"score_type": "sk-SECRET-token"},
    ],
)
def test_error_messages_never_echo_the_rejected_value(overrides):
    with pytest.raises(SpecError) as excinfo:
        _spec(**overrides)
    message = str(excinfo.value)
    assert "SECRET" not in message and "secret" not in message
