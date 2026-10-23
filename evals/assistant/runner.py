"""Runner: each `aq-2` case through the real M16 workflow (Milestone 17).

Retrieval is always fixed to the case's frozen evidence. Generation is either the case's scripted
results (mocked mode) or one shared real provider (live mode, `__main__.py`, owner-approved runs
only). Every provider call passes through `RecordingProvider`, which times the call itself
(provider-call latency, separate from the workflow's end-to-end latency), keeps the tokens the
provider reported, enforces the run's request cap and minimum interval, and stops the run after
a failure that signals quota, access or transport trouble.

The request cap and interval are this harness's own limits. They are not Google's account or
model quotas, which this code does not know; a provider `rate_limited` result stops the run.

Each call record keeps its UTC start and the pacing wait spent before it (the minimum-interval
sleep runs inside the workflow's generation span, so workflow generation time includes it).
`on_result` receives each case as soon as it finishes, so a caller can persist it at once.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ecommerce_search.assistant.models import AssistantResponse
from ecommerce_search.assistant.retrieval import Retrieval
from ecommerce_search.assistant.workflow import AssistantLimits, answer
from ecommerce_search.generation.provider import (
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
)
from ecommerce_search.search.query import normalize_query
from evals.assistant.checks import CheckResult, run_checks
from evals.assistant.contracts import EvalCase

FIXED_SEARCH_VERSION = "frozen-case-evidence"
LIMITS = AssistantLimits(evidence_k=10, max_output_tokens=512, deadline_seconds=30)
MAX_MESSAGE_LENGTH = 1000
# A result with one of these failures stops the run: nothing further is sent.
STOP_FAILURES = frozenset(
    {"auth_error", "rate_limited", "request_rejected", "transport_error", "server_error", "timeout"}
)


class ScriptExhausted(Exception):
    """The workflow asked for more generation results than the case scripted."""


class RequestCapReached(Exception):
    """The run's request cap would be exceeded; the request was not sent."""


class FixedRetriever:
    def __init__(self, case: EvalCase):
        self.case = case

    def retrieve(self, query: str, top_k: int) -> Retrieval:
        return Retrieval(
            search_version=FIXED_SEARCH_VERSION,
            products=self.case.evidence[:top_k],
            retrieval_ms=0.0,
        )


class ScriptedProvider:
    provider = "scripted-mock"
    model = "none"

    def __init__(self, case: EvalCase):
        self.remaining = list(case.scripted)

    def generate(self, request: GenerationRequest) -> GenerationResult:
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
class CallRecord:
    case_id: str
    failure: str | None
    input_tokens: int | None
    output_tokens: int | None
    provider_ms: float
    started_utc: str | None = None
    pacing_wait_ms: float = 0.0


@dataclass
class RecordingProvider:
    inner: GenerationProvider
    max_requests: int | None = None
    min_interval_seconds: float = 0.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    calls: list[CallRecord] = field(default_factory=list)
    case_id: str = ""
    stopped: str | None = None
    _last_sent: float | None = None

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.stopped is not None:
            raise RequestCapReached  # defensive: the run loop stops before this
        if self.max_requests is not None and len(self.calls) >= self.max_requests:
            self.stopped = "request_cap_reached"
            raise RequestCapReached
        waited = 0.0
        if self._last_sent is not None:
            wait = self.min_interval_seconds - (self.clock() - self._last_sent)
            if wait > 0:
                self.sleep(wait)
                waited = round(wait * 1000, 3)
        self._last_sent = self.clock()
        started_utc = datetime.now(UTC).isoformat(timespec="milliseconds")
        started = time.perf_counter()
        result = self.inner.generate(request)
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        self.calls.append(
            CallRecord(
                self.case_id,
                result.failure,
                result.input_tokens,
                result.output_tokens,
                elapsed,
                started_utc,
                waited,
            )
        )
        if result.failure in STOP_FAILURES:
            self.stopped = f"provider_failure:{result.failure}"
        return result


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    response: AssistantResponse | None
    checks: tuple[CheckResult, ...]
    calls: tuple[CallRecord, ...] = ()
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)


@dataclass(frozen=True)
class RunResult:
    mode: str
    results: tuple[CaseResult, ...]
    not_run: tuple[str, ...]
    stopped: str | None
    provider: str | None
    model: str | None


def _run_one(
    case: EvalCase,
    recorder: RecordingProvider | None,
    mocked: bool,
    limits: AssistantLimits = LIMITS,
) -> CaseResult:
    generator = None if case.generation == "off" else recorder
    if recorder is not None:
        recorder.case_id = case.case_id
    before = 0 if recorder is None else len(recorder.calls)
    try:
        response = answer(
            normalize_query(case.message, MAX_MESSAGE_LENGTH),
            retriever=FixedRetriever(case),
            generator=generator,
            limits=limits,
        )
    except ScriptExhausted:
        error = "workflow requested an unscripted generation"
        response = None
    except RequestCapReached:
        error = "request cap reached before this case finished"
        response = None
    calls = () if recorder is None else tuple(recorder.calls[before:])
    if response is None:
        return CaseResult(case, None, (), calls, error=error)
    checks = run_checks(case, response)
    if mocked:
        inner = recorder.inner if recorder is not None else None
        unused = len(inner.remaining) if isinstance(inner, ScriptedProvider) else 0
        detail = f"{unused} scripted result(s) unused" if unused else ""
        checks = (*checks, CheckResult("script_consumed", not unused, detail))
    return CaseResult(case, response, checks, calls)


OnResult = Callable[[CaseResult], None]


def run_cases(cases: tuple[EvalCase, ...], on_result: OnResult | None = None) -> RunResult:
    """Mocked run: each case gets its own scripted provider."""
    results = []
    for case in cases:
        result = _run_one(case, RecordingProvider(ScriptedProvider(case)), mocked=True)
        if on_result is not None:
            on_result(result)
        results.append(result)
    return RunResult("mocked", tuple(results), (), None, "scripted-mock", "none")


def run_live(
    cases: tuple[EvalCase, ...],
    inner: GenerationProvider,
    *,
    max_requests: int,
    min_interval_seconds: float,
    limits: AssistantLimits = LIMITS,
    sleep: Callable[[float], None] = time.sleep,
    on_result: OnResult | None = None,
) -> RunResult:
    """Live run with one shared provider; stops at the cap or after a stop failure. Cases not
    reached are listed, never silently dropped."""
    recorder = RecordingProvider(
        inner, max_requests=max_requests, min_interval_seconds=min_interval_seconds, sleep=sleep
    )
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if recorder.stopped is not None:
            not_run = tuple(c.case_id for c in cases[index:])
            return RunResult("live", tuple(results), not_run, recorder.stopped, *_names(inner))
        result = _run_one(case, recorder, mocked=False, limits=limits)
        if on_result is not None:
            on_result(result)  # persisted before the next case is sent
        results.append(result)
    return RunResult("live", tuple(results), (), recorder.stopped, *_names(inner))


def _names(inner: GenerationProvider) -> tuple[str, str]:
    return inner.provider, inner.model


def run_case(case: EvalCase) -> CaseResult:
    return run_cases((case,)).results[0]
