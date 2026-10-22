"""The UI's synthetic API fixtures are valid responses of the existing API contracts.

This checks fixture/API compatibility only: every `frontend/e2e/fixtures/*.json` body validates
against the Pydantic response model of `POST /search/reranked` and survives a round trip
unchanged (no extra or missing keys, price kept as a decimal string). The UI-facing TypeScript
shape of the same files is checked separately by `npm run typecheck` (`e2e/fixtures.ts`).
"""

import json
from pathlib import Path

import pytest

from ecommerce_search.api.schemas import RerankedSearchResponse, SearchUnavailable
from ecommerce_search.assistant.models import AssistantResponse, AssistantUnavailable

FIXTURES = Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures"
SUCCESS = sorted(FIXTURES.glob("synthetic-reranked-*.json"))
# Milestone 16: assistant fixtures (every file but the fixed 503 body).
ASSISTANT = sorted(
    p
    for p in FIXTURES.glob("synthetic-assistant-*.json")
    if p.name != "synthetic-assistant-503.json"
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_fixture_is_covered():
    names = {p.name for p in FIXTURES.glob("*.json")}
    assert names == {p.name for p in SUCCESS + ASSISTANT} | {
        "synthetic-503.json",
        "synthetic-assistant-503.json",
    }
    assert len(SUCCESS) >= 4


@pytest.mark.parametrize("path", SUCCESS, ids=lambda p: p.name)
def test_success_fixture_matches_the_response_contract(path):
    body = load(path)
    assert RerankedSearchResponse.model_validate(body).model_dump(mode="json") == body
    assert all(isinstance(r["price"], str) for r in body["results"])


def test_fixtures_cover_every_reranker_status_and_an_unscored_tail():
    bodies = [load(p) for p in SUCCESS]
    statuses = {b["reranker"]["status"] for b in bodies}
    assert statuses == {
        "applied",
        "disabled",
        "skipped_no_candidates",
        "skipped_no_searchable_text",
    }
    assert any(
        b["reranker"]["status"] == "applied"
        and any(r["rerank_score"] is None for r in b["results"])
        and any(r["rerank_score"] is not None for r in b["results"])
        for b in bodies
    )


def test_unavailable_fixture_matches_the_fixed_503_body():
    body = load(FIXTURES / "synthetic-503.json")
    assert SearchUnavailable.model_validate(body).model_dump(mode="json") == body


@pytest.mark.parametrize("path", ASSISTANT, ids=lambda p: p.name)
def test_assistant_fixture_matches_the_response_contract(path):
    body = load(path)
    assert AssistantResponse.model_validate(body).model_dump(mode="json") == body
    assert all(isinstance(p["price"], str) for p in body["evidence"])


def test_assistant_fixtures_cover_every_outcome():
    bodies = [load(p) for p in ASSISTANT]
    assert {b["outcome"] for b in bodies} == {
        "answer",
        "clarify",
        "abstain",
        "generation_unavailable",
    }
    assert {b["generation"]["status"] for b in bodies} >= {"used", "disabled", "failed"}


def test_assistant_unavailable_fixture_matches_the_fixed_503_body():
    body = load(FIXTURES / "synthetic-assistant-503.json")
    assert AssistantUnavailable.model_validate(body).model_dump(mode="json") == body
