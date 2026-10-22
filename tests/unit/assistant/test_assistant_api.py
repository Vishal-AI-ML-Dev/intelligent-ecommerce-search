"""Milestone 16: `POST /assistant/answer` with the retriever and generator replaced by fakes.

No database, model, network or provider call. Mocked only: not live-provider validation.
"""

import json
import logging
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_assistant_workflow import EVIDENCE, INJECTION, FakeGenerator, FakeRetriever

from ecommerce_search.api import assistant as assistant_api
from ecommerce_search.api.app import build_generation_provider, create_app
from ecommerce_search.api.dependencies import get_generation_provider
from ecommerce_search.api.schemas import RerankedSearchResult
from ecommerce_search.assistant.retrieval import RetrievalUnavailable
from ecommerce_search.generation.gemini_provider import GeminiGenerationProvider
from ecommerce_search.generation.provider import GenerationResult, GroundedDraft

PATH = "/assistant/answer"
SECRET_KEY = "gm-test-not-a-real-key"  # noqa: S105 - fake test value


@pytest.fixture
def client_for(make_settings):
    def _client(*, retriever=None, generator=None, **settings):
        app = create_app(make_settings(**settings))
        app.dependency_overrides[assistant_api.get_assistant_retriever] = lambda: (
            retriever or FakeRetriever()
        )
        if generator is not None:
            app.dependency_overrides[get_generation_provider] = lambda: generator
        return TestClient(app)

    return _client


def answer_draft(pid="P1"):
    return GenerationResult(
        draft=GroundedDraft(
            outcome="answer",
            selected=({"product_id": pid, "relevant_facts": ("price", "brand")},),
            summary="A Dell laptop matching the brand you asked for.",
        ),
        failure=None,
        input_tokens=900,
        output_tokens=150,
    )


def test_route_is_in_the_openapi_contract(make_settings):
    paths = create_app(make_settings()).openapi()["paths"]
    assert set(paths[PATH]) == {"post"}


def test_generation_is_off_by_default(make_settings):
    assert make_settings().assistant_generation_provider == "none"
    assert build_generation_provider(make_settings()) is None


def test_provider_is_built_only_when_configured_and_without_calls(make_settings):
    settings = make_settings(assistant_generation_provider="gemini", gemini_api_key=SECRET_KEY)
    provider = build_generation_provider(settings)
    assert isinstance(provider, GeminiGenerationProvider)
    assert provider.model == "gemini-3.5-flash-lite"
    provider.close()


def test_clarify_needs_no_retrieval(client_for):
    retriever = FakeRetriever()
    with client_for(retriever=retriever) as client:
        response = client.post(PATH, json={"message": "  hello   there "})
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "clarify"
    assert body["clarification"]["reason"] == "no_product_need"
    assert retriever.calls == []
    assert response.headers["X-Request-ID"]


def test_generation_off_returns_labelled_search_results(client_for):
    with client_for() as client:
        body = client.post(PATH, json={"message": "laptop for coding"}).json()
    assert body["outcome"] == "generation_unavailable"
    assert body["notice"].startswith("No generated answer is available")
    assert body["summary"] is None and body["recommendations"] == []
    assert [p["product_id"] for p in body["evidence"]] == ["P1", "P2", "P3"]
    assert body["evidence"][0]["price"] == "45000.00"  # decimal string, never a float
    assert body["generation"]["status"] == "disabled"
    assert body["generation"]["input_tokens"] is None


def test_grounded_answer_contract(client_for):
    with client_for(generator=FakeGenerator(answer_draft())) as client:
        body = client.post(PATH, json={"message": "dell laptop under rs 50000"}).json()
    assert body["outcome"] == "answer"
    assert body["recommendations"] == [
        {
            "product_id": "P1",
            "facts": [
                {"field": "price", "value": "45000.00"},
                {"field": "brand", "value": "Dell"},
            ],
            "constraint_checks": [
                {"field": "category", "requested": "laptop", "status": "passed"},
                {"field": "brand", "requested": "Dell", "status": "passed"},
                {"field": "max_price", "requested": "50000.00", "status": "passed"},
            ],
        }
    ]
    assert body["generation"] | {"estimated_cost_usd": None} == {
        "status": "used",
        "failure": None,
        "provider": "fake",
        "model": "fake-model",
        "calls": 1,
        "validation": "passed",
        "validation_errors": [],
        "input_tokens": 900,
        "output_tokens": 150,
        "estimated_cost_usd": None,
        "cost_status": "unknown_pricing_not_configured",
    }
    assert body["versions"] == {
        "prompt": "asst-prompt-1",
        "policy": "asst-policy-1",
        "evidence": "asst-evidence-1",
        "retriever": "v3_reranked",
    }


def test_configured_prices_give_an_estimate(client_for):
    with client_for(
        generator=FakeGenerator(answer_draft()),
        assistant_input_usd_per_mtok=Decimal("0.10"),
        assistant_output_usd_per_mtok=Decimal("0.50"),
    ) as client:
        body = client.post(PATH, json={"message": "dell laptop under rs 50000"}).json()
    assert body["generation"]["cost_status"] == "estimated"
    assert Decimal(body["generation"]["estimated_cost_usd"]) == Decimal("0.000165")


def test_search_failure_is_a_fixed_503(client_for):
    with client_for(retriever=FakeRetriever(fail=True)) as client:
        response = client.post(PATH, json={"message": "laptop for coding"})
    assert response.status_code == 503
    assert response.json() == {"detail": "assistant unavailable"}


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "laptop\u0000"},
        {"message": "laptop‮"},
        {"message": "x" * 201},
        {"message": "laptop", "extra": 1},
        {},
    ],
)
def test_invalid_input_is_422_without_echo(client_for, payload):
    with client_for() as client:
        response = client.post(PATH, json=payload)
    assert response.status_code == 422
    assert "x" * 201 not in response.text


def test_logs_carry_codes_and_counts_only(client_for, caplog):
    caplog.set_level(logging.INFO)
    message = "dell laptop under rs 50000"
    with client_for(generator=FakeGenerator(answer_draft())) as client:
        client.post(PATH, json={"message": message})
    text = "\n".join(record.getMessage() for record in caplog.records)
    completed = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == assistant_api.__name__ and "assistant_answer_completed" in r.getMessage()
    ]
    assert completed[0]["outcome"] == "answer"
    assert completed[0]["evidence_count"] == 3
    assert completed[0]["request_id"]
    for secret in (message, "Dell laptop", INJECTION, "A Dell laptop matching", "45000"):
        assert secret not in text


def test_retriever_adapter_reuses_v3_search_and_maps_failures(monkeypatch, make_settings):
    calls = []

    def fake_search(*args):
        calls.append(args)
        result = RerankedSearchResult(
            rank=1,
            rrf_score=0.01,
            lexical_rank=1,
            lexical_score=0.5,
            dense_rank=None,
            dense_score=None,
            product_id=EVIDENCE[0].product_id,
            title=EVIDENCE[0].title,
            brand="Dell",
            category="laptop",
            subcategory=None,
            description=None,
            price=Decimal("45000.00"),
            currency="INR",
            rating=4.2,
            review_count=10,
            availability="in_stock",
            rrf_rank=1,
            rerank_rank=1,
            rerank_score=1.5,
        )

        class Response:
            search_version = "v3_reranked"
            results = [result]

        return Response()

    monkeypatch.setattr(assistant_api, "run_reranked_search", fake_search)
    settings = make_settings()
    retriever = assistant_api.RerankedSearchRetriever("session", settings, None, None, "provider")
    retrieval = retriever.retrieve("laptop for coding", 5)
    assert calls[0][:7] == ("session", settings, None, None, "provider", "laptop for coding", 5)
    assert retrieval.search_version == "v3_reranked"
    assert retrieval.products[0].product_id == "P1"
    assert retrieval.products[0].price == Decimal("45000.00")

    def unavailable(*args):
        raise HTTPException(503, "search unavailable")

    monkeypatch.setattr(assistant_api, "run_reranked_search", unavailable)
    with pytest.raises(RetrievalUnavailable):
        retriever.retrieve("laptop for coding", 5)


@pytest.mark.parametrize(
    "overrides",
    [
        {"assistant_generation_provider": "gemini"},
        {"assistant_generation_provider": "gemini", "gemini_api_key": "  "},
        {"gemini_api_key": "bad key"},
        {"assistant_generation_provider": "anthropic", "gemini_api_key": SECRET_KEY},
        {"assistant_generation_model": "https://evil.test/model"},
        {"assistant_evidence_k": 10, "search_candidate_k": 5},
        {"assistant_input_usd_per_mtok": Decimal("1")},
        {"assistant_generation_max_output_tokens": 100_000},
        {"assistant_generation_timeout_seconds": 0},
    ],
)
def test_invalid_assistant_settings_fail_without_echoing_the_key(make_settings, overrides):
    with pytest.raises(ValueError) as excinfo:
        make_settings(**overrides)
    assert "bad key" not in str(excinfo.value)
