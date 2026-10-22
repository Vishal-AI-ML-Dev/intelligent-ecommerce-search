"""scripts/m16_live_smoke.py: the request budget, stop-on-failure and dry-run guards, with a
fake provider. No network call."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from ecommerce_search.assistant.workflow import AssistantLimits
from ecommerce_search.generation.provider import GenerationResult, GroundedDraft

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def smoke():
    name = "m16_live_smoke_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "m16_live_smoke.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[name]


LIMITS = AssistantLimits(evidence_k=5, max_output_tokens=512, deadline_seconds=30)


def ok(pid):
    return GenerationResult(
        draft=GroundedDraft(
            outcome="answer",
            selected=({"product_id": pid, "relevant_facts": ("ram_gb",)},),
            summary="A synthetic match.",
        ),
        failure=None,
        input_tokens=10,
        output_tokens=5,
    )


def bad():
    return GenerationResult(
        draft=GroundedDraft(outcome="answer", selected=(), summary=""),
        failure=None,
        input_tokens=10,
        output_tokens=5,
    )


class Fake:
    provider = "fake"
    model = "fake-model"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return self.results.pop(0)


def test_budget_is_five_and_counts_repairs(smoke):
    fake = Fake([bad(), bad(), bad(), bad(), bad(), bad()])
    report = smoke.run_trial(smoke.BudgetedProvider(fake), LIMITS)
    assert smoke.MAX_GENERATION_REQUESTS == 5
    assert fake.calls == 4  # 2 cases x (call + repair); never more than the budget
    assert report["requests_sent"] == 4


def test_the_budget_refuses_the_request_past_the_limit(smoke):
    fake = Fake([bad(), bad(), bad()])
    provider = smoke.BudgetedProvider(fake, budget=3)
    report = smoke.run_trial(provider, LIMITS)
    assert fake.calls == 3
    assert report["stopped"] == "budget_exhausted"


def test_first_provider_failure_stops_everything(smoke):
    quota = GenerationResult(
        draft=None, failure="rate_limited", input_tokens=None, output_tokens=None
    )
    fake = Fake([quota, ok("SYN-LAP-001")])
    report = smoke.run_trial(smoke.BudgetedProvider(fake), LIMITS)
    assert fake.calls == 1
    assert report["stopped"] == "provider_failure:rate_limited"
    assert report["cases"] == [{"message": smoke.CASES[0][0], "failure": "rate_limited"}]


def test_successful_cases_record_compatibility_facts(smoke):
    fake = Fake([ok("SYN-LAP-001"), ok("SYN-LAP-011")])
    report = smoke.run_trial(smoke.BudgetedProvider(fake), LIMITS)
    assert report["stopped"] is None and report["requests_sent"] == 2
    first = report["cases"][0]
    assert first["outcome"] == "answer" and first["validation"] == "passed"
    assert first["selected"] == [{"product_id": "SYN-LAP-001", "verification": "all_verified"}]


def test_synthetic_records_only(smoke):
    for _, products in smoke.CASES:
        assert all(p.product_id.startswith("SYN-") for p in products)
        assert all("Synthetic" in (p.description or "") for p in products)


def test_dry_run_sends_nothing_and_never_prints_the_key(smoke, monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-fake-key-not-real")
    assert smoke.main([]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert plan["dry_run"] is True and plan["key_configured"] is True
    assert "gm-fake-key-not-real" not in out


def test_every_request_is_logged_including_the_repair_codes(smoke):
    quota = GenerationResult(
        draft=None, failure="server_error", input_tokens=None, output_tokens=None
    )
    fake = Fake([bad(), quota])
    report = smoke.run_trial(smoke.BudgetedProvider(fake), LIMITS)
    first, second = report["requests"]
    assert first["structured_output_parsed"] is True and first["repair_of"] is None
    assert (first["input_tokens"], first["output_tokens"]) == (10, 5)
    assert second["failure"] == "server_error"
    assert second["repair_of"] == ["answer_without_products", "summary_empty"]
