"""The UI's synthetic API fixtures are valid responses of the existing API contracts.

This checks fixture/API compatibility only: every `frontend/e2e/fixtures/*.json` body validates
against the Pydantic response model of `POST /search/reranked` and survives a round trip
unchanged (no extra or missing keys, price kept as a decimal string). The UI-facing TypeScript
shape of the same files is checked separately by `npm run typecheck` (`e2e/fixtures.ts`).

Milestone 18-05: every `frontend/e2e/fixtures/returns/*.json` body validates against the Pydantic
model of the `/returns` response it stands for, and the retailer tab's committed filter
vocabulary (`frontend/src/domains/returns/vocab.json`) equals the backend enums exactly.
"""

import json
from pathlib import Path

import pytest

from ecommerce_search.api import returns_schemas as rs
from ecommerce_search.api.returns import DETAILS, LIMITS, ReturnsCapabilities
from ecommerce_search.api.schemas import RerankedSearchResponse, SearchUnavailable
from ecommerce_search.assistant.models import AssistantResponse, AssistantUnavailable
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.errors import MESSAGES
from ecommerce_search.returns.service import ImportReport

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
FIXTURES = FRONTEND / "e2e" / "fixtures"
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


# ---- Milestone 18-05: retailer returns fixtures and vocabulary --------------------------------

RETURNS_FIXTURES = FIXTURES / "returns"
VOCAB = FRONTEND / "src" / "domains" / "returns" / "vocab.json"

# Each fixture file -> the response model of the route it stands for (contracts §7, §4.8).
RETURNS_MODELS = {
    "capabilities-importer.json": ReturnsCapabilities,
    "capabilities-viewer.json": ReturnsCapabilities,
    "report-valid.json": ImportReport,
    "report-imported.json": ImportReport,
    "report-import-invalid.json": ImportReport,
    "report-invalid-truncated.json": ImportReport,
    "report-invalid-lower-bound.json": ImportReport,
    "returns-page-1.json": rs.ReturnPage,
    "returns-page-2.json": rs.ReturnPage,
    "returns-page-empty.json": rs.ReturnPage,
    "return-detail-conflict.json": rs.ReturnDetail,
    "return-detail-not-recorded.json": rs.ReturnDetail,
    "batches-page.json": rs.BatchPage,
    "batches-page-empty.json": rs.BatchPage,
    "batch-detail.json": rs.BatchDetail,
    "error-unauthorized.json": rs.ReturnsErrorBody,
    "error-retailer-access-not-configured.json": rs.ReturnsErrorBody,
    "error-import-commit-disabled.json": rs.ReturnsErrorBody,
    "error-cursor-mismatch.json": rs.ReturnsErrorBody,
    "error-invalid-parameter.json": rs.ReturnsValidationErrorBody,
}


def returns_fixture(name: str) -> dict:
    return load(RETURNS_FIXTURES / name)


def test_every_returns_fixture_is_covered():
    assert {p.name for p in RETURNS_FIXTURES.glob("*.json")} == set(RETURNS_MODELS)


@pytest.mark.parametrize(("name", "model"), RETURNS_MODELS.items(), ids=list(RETURNS_MODELS))
def test_returns_fixture_matches_its_response_model(name, model):
    body = returns_fixture(name)
    assert model.model_validate(body).model_dump(mode="json") == body


def test_capabilities_fixtures_carry_the_published_limits_and_reason_codes():
    for name in ("capabilities-importer.json", "capabilities-viewer.json"):
        body = returns_fixture(name)
        assert body["limits"] == LIMITS.model_dump()
        assert body["reason_codes"] == list(rc.ReasonCode)
    assert returns_fixture("capabilities-importer.json")["can_commit"] is True
    assert returns_fixture("capabilities-viewer.json")["can_commit"] is False


def test_error_fixtures_carry_the_servers_fixed_texts():
    for name, model in RETURNS_MODELS.items():
        if model in (rs.ReturnsErrorBody, rs.ReturnsValidationErrorBody):
            body = returns_fixture(name)
            assert body["detail"] == DETAILS[body["code"]]


def test_report_fixtures_use_the_fixed_row_error_messages_and_cover_section_4_8():
    reports = {name: returns_fixture(name) for name in RETURNS_MODELS if name.startswith("report-")}
    for body in reports.values():
        assert all(e["message"] == MESSAGES[e["code"]] for e in body["errors"])
    truncated = reports["report-invalid-truncated.json"]
    assert truncated["errors_truncated"] and len(truncated["errors"]) == 200
    assert truncated["error_count"] > 200
    lower = reports["report-invalid-lower-bound.json"]
    assert lower["error_count_is_lower_bound"] and not lower["errors_truncated"]
    assert {b["status"] for b in reports.values()} == {"valid", "invalid", "imported"}
    assert reports["report-import-invalid.json"]["mode"] == "commit"


def test_detail_fixtures_cover_conflict_markup_and_missing_evidence():
    conflict = returns_fixture("return-detail-conflict.json")
    texts = [c["text"] for c in conflict["customer_statements"]["comments"]]
    assert any("<script>" in t for t in texts)
    assert any("Ignore all previous instructions" in t for t in texts)
    assert conflict["inspection_observations"][0]["condition"] == "as_new"
    assert conflict["customer_statements"]["customer_reason_code"] == "ARRIVED_DAMAGED"
    missing = returns_fixture("return-detail-not-recorded.json")
    assert set(missing["evidence_presence"].values()) == {0}
    assert missing["shipment"] is None
    assert isinstance(missing["listing_snapshot"]["price"], str)  # §7.1 M1 seller text


def test_returns_vocabulary_equals_the_backend_enums_exactly():
    vocab = json.loads(VOCAB.read_text(encoding="utf-8"))
    assert vocab == {
        "return_statuses": [s.value for s in rc.ReturnStatus],
        "reason_codes": [c.value for c in rc.ReasonCode],
        "categories": [c.value for c in Category],
    }
    # These enums are the ones the list filters validate against (§7.1 E4).
    fields = rs.ReturnFilters.model_fields
    assert fields["status"].annotation == rc.ReturnStatus | None
    assert fields["customer_reason_code"].annotation == rc.ReasonCode | None
    assert fields["category"].annotation == Category | None
