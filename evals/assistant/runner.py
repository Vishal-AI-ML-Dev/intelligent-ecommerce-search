"""Mocked runner: each `aq-1` case through the real M16 workflow (Milestone 17 groundwork).

Only retrieval (a fixed retriever returning the case's catalog records) and generation (a
scripted provider returning the case's results in order) are replaced. Nothing is sent over the
network; there is no live mode.
"""

from dataclasses import dataclass

from ecommerce_search.assistant.models import AssistantResponse
from ecommerce_search.assistant.retrieval import Retrieval
from ecommerce_search.assistant.workflow import AssistantLimits, answer
from ecommerce_search.generation.provider import GenerationRequest, GenerationResult
from ecommerce_search.search.query import normalize_query
from evals.assistant.checks import CheckResult, run_checks
from evals.assistant.contracts import EvalCase

MOCK_SEARCH_VERSION = "mock-fixed-evidence"
LIMITS = AssistantLimits(evidence_k=10, max_output_tokens=512, deadline_seconds=30)
MAX_MESSAGE_LENGTH = 1000


class ScriptExhausted(Exception):
    """The workflow asked for more generation results than the case scripted."""


class FixedRetriever:
    def __init__(self, case: EvalCase):
        self.case = case

    def retrieve(self, query: str, top_k: int) -> Retrieval:
        return Retrieval(
            search_version=MOCK_SEARCH_VERSION,
            products=self.case.evidence[:top_k],
            retrieval_ms=0.0,
        )


class ScriptedProvider:
    provider = "scripted-mock"
    model = "none"

    def __init__(self, case: EvalCase):
        self.remaining = list(case.scripted)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if not self.remaining:
            raise ScriptExhausted
        scripted = self.remaining.pop(0)
        return GenerationResult(
            draft=scripted.draft,
            failure=scripted.failure,
            input_tokens=scripted.input_tokens,
            output_tokens=scripted.output_tokens,
        )


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    response: AssistantResponse | None
    checks: tuple[CheckResult, ...]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)


def run_case(case: EvalCase) -> CaseResult:
    provider = None if case.generation == "off" else ScriptedProvider(case)
    try:
        response = answer(
            normalize_query(case.message, MAX_MESSAGE_LENGTH),
            retriever=FixedRetriever(case),
            generator=provider,
            limits=LIMITS,
        )
    except ScriptExhausted:
        return CaseResult(case, None, (), error="workflow requested an unscripted generation")
    checks = run_checks(case, response)
    if provider is not None and provider.remaining:
        unused = CheckResult(
            "script_consumed", False, f"{len(provider.remaining)} scripted result(s) unused"
        )
        checks = (*checks, unused)
    else:
        checks = (*checks, CheckResult("script_consumed", True))
    return CaseResult(case, response, checks)


def run_cases(cases: tuple[EvalCase, ...]) -> tuple[CaseResult, ...]:
    return tuple(run_case(case) for case in cases)
