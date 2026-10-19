import json

import pytest
from pydantic import ValidationError

from ecommerce_search.api.schemas import (
    DenseSearchRequest,
    FilteredSearchRequest,
    HybridSearchRequest,
    RerankedSearchRequest,
    SearchRequest,
)

# Every POST search body model. The upper `top_k` bound comes from settings and is checked by the
# routes, so only the schema's own lower bound is exercised here.
REQUEST_MODELS = [
    SearchRequest,
    DenseSearchRequest,
    HybridSearchRequest,
    FilteredSearchRequest,
    RerankedSearchRequest,
]

NON_INTEGERS = [True, False, "3", 2.0, 2.5]


@pytest.mark.parametrize("model", REQUEST_MODELS)
@pytest.mark.parametrize("value", NON_INTEGERS, ids=repr)
def test_top_k_rejects_non_integers_instead_of_coercing(model, value):
    payload = {"query": "laptop", "top_k": value}
    with pytest.raises(ValidationError) as python_error:
        model.model_validate(payload)
    with pytest.raises(ValidationError) as json_error:
        model.model_validate_json(json.dumps(payload))
    for error in (python_error, json_error):
        assert [e["loc"] for e in error.value.errors()] == [("top_k",)]


@pytest.mark.parametrize("model", REQUEST_MODELS)
@pytest.mark.parametrize("value", [1, 5, 50, 1000])
def test_top_k_accepts_integers(model, value):
    payload = {"query": "laptop", "top_k": value}
    assert model.model_validate(payload).top_k == value
    assert model.model_validate_json(json.dumps(payload)).top_k == value


@pytest.mark.parametrize("model", REQUEST_MODELS)
@pytest.mark.parametrize("value", [0, -1])
def test_top_k_lower_bound_is_unchanged(model, value):
    with pytest.raises(ValidationError):
        model.model_validate({"query": "laptop", "top_k": value})


@pytest.mark.parametrize("model", REQUEST_MODELS)
def test_top_k_omitted_or_null_is_none(model):
    assert model.model_validate({"query": "laptop"}).top_k is None
    assert model.model_validate({"query": "laptop", "top_k": None}).top_k is None
    assert model.model_validate_json('{"query": "laptop", "top_k": null}').top_k is None
