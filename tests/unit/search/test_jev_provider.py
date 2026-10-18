"""Milestone 11 Jev provider (ADR-005), fully mocked: the real TypeSafe SDK runs against an
in-memory `httpx2.MockTransport`, so request building, response parsing, error mapping and the
SDK's own retry policy are exercised without any network access or real key."""

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import math
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from ecommerce_search.api.app import create_app
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.decision import (
    DecisionResult,
    DeterministicDecisionProvider,
    JevConfig,
    JevConfigurationError,
    JevDecisionProvider,
    build_decision_provider,
)
from ecommerce_search.decision import jev_provider as jev
from ecommerce_search.decision.provider import CategoryProposal, JevOutcome
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.query_understanding.models import Intent

# Unawaited coroutines (RuntimeWarning), unclosed loops or sockets (ResourceWarning) and warnings
# raised during garbage collection (unraisable) fail the test; none counts as successful cleanup.
pytestmark = pytest.mark.filterwarnings(
    "error::RuntimeWarning",
    "error::ResourceWarning",
    "error::pytest.PytestUnraisableExceptionWarning",
)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def loopback_only_connect(monkeypatch):
    """Strict no-network guard for this module. The provider's event loop needs a self-pipe, and
    on Windows `socket.socketpair()` builds it with a loopback connection, so connect is allowed
    to 127.0.0.1/::1 only and refused everywhere else. Name resolution and `create_connection`
    stay blocked by the package conftest; every Jev call goes through an in-memory transport."""
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host not in _LOOPBACK_HOSTS:
            raise RuntimeError("network access is not allowed in search unit tests")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


KEY = "ts-test-key-not-real-7f3a9c"
MODEL = "jev-1.13.0"
QUERY = "gaming laptop 16gb 512gb ssd under 80k"
CATEGORY_LABELS = [c.value for c in Category] + ["none"]
INTENT_LABELS = [i.value for i in Intent] + ["none"]


def _probabilities(labels: list[str], top: dict[str, float]) -> dict[str, float]:
    rest = [label for label in labels if label not in top]
    share = (1.0 - sum(top.values())) / len(rest) if rest else 0.0
    return {label: top.get(label, share) for label in labels}


def _answer(labels: list[str], choice: str, top: dict[str, float] | None = None, **overrides):
    answer = {
        "type": "choice",
        "choice": choice,
        "confidence": 0.8125,
        "probabilities": _probabilities(labels, top or {choice: 0.85}),
    }
    answer.update(overrides)
    return answer


def _body(category=None, intent=None, **overrides) -> dict:
    body = {
        "model": MODEL,
        "answers": {
            "category": category or _answer(CATEGORY_LABELS, "laptop"),
            "semantic_intent": intent or _answer(INTENT_LABELS, "gaming"),
        },
        "usage": {"input_tokens": 321, "output_tokens": 0},
    }
    body.update(overrides)
    return body


def _ok(body: dict | None = None) -> httpx2.Response:
    return httpx2.Response(
        200, json=_body() if body is None else body, headers={"x-typesafe-request-id": "req-1"}
    )


class Server:
    """Replays one scripted reply per attempt (the last one repeats) and records requests."""

    def __init__(self, *replies: httpx2.Response | Callable[[], httpx2.Response]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return reply() if callable(reply) else reply


def _config(**overrides) -> JevConfig:
    values = {
        "api_key": SecretStr(KEY),
        "model": MODEL,
        "base_url": "https://api.typesafe.ai",
        "attempt_timeout_seconds": 1.25,
        "max_retries": 1,
        "retry_budget_seconds": 3.0,
        "deadline_seconds": 3.0,
        "max_in_flight": 8,
    }
    values.update(overrides)
    return JevConfig(**values)


def _provider(server: Server, **overrides) -> JevDecisionProvider:
    return JevDecisionProvider(_config(**overrides), transport=httpx2.MockTransport(server))


def _understand(server: Server, **overrides) -> DecisionResult:
    provider = _provider(server, **overrides)
    try:
        return provider.understand(QUERY, parse_normalized_query(QUERY))
    finally:
        provider.close()


@pytest.fixture(autouse=True)
def restore_sdk_logger_level():
    sdk_logger = logging.getLogger("typesafe_sdk")
    level = sdk_logger.level
    yield
    sdk_logger.setLevel(level)


def _loop_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == jev.LOOP_THREAD_NAME and t.is_alive()]


@pytest.fixture(autouse=True)
def no_leaked_threads():
    """Every provider a test builds must be closed, and no thread started during the test (the
    loop thread, the loop's default executor, test workers) may outlive it."""
    assert _loop_threads() == []
    before = set(threading.enumerate())
    yield
    assert _loop_threads() == []
    assert [t.name for t in threading.enumerate() if t not in before] == []


@pytest.fixture(autouse=True)
def no_asyncio_error_logs(caplog):
    """asyncio and concurrent.futures log, rather than raise, a pending task destroyed, an
    exception never retrieved and a failing callback (such as one scheduled on a closed loop).
    Any such record fails the test; it is never accepted as cleanup."""
    yield
    noisy = ("asyncio", "concurrent.futures")
    errors = [r.getMessage() for r in caplog.records if r.name in noisy and r.levelno >= 40]
    assert errors == []


# --- success path -------------------------------------------------------------------------------


def test_valid_response_attaches_bounded_proposals_beside_the_unchanged_parse() -> None:
    deterministic = parse_normalized_query(QUERY)
    provider = _provider(Server(_ok()))
    result = provider.understand(QUERY, deterministic)
    provider.close()

    assert result.understanding is deterministic
    assert (result.provider, result.provider_version) == ("jev", "jq-1")
    outcome = result.jev
    assert outcome is not None and outcome.fallback_reason is None
    assert (outcome.model, outcome.response_model, outcome.request_id) == (MODEL, MODEL, "req-1")
    assert (outcome.input_tokens, outcome.output_tokens) == (321, 0)
    assert outcome.latency_ms >= 0
    assert outcome.category is not None and outcome.semantic_intent is not None
    assert outcome.category.label is Category.LAPTOP
    assert outcome.semantic_intent.label is Intent.GAMING
    assert outcome.category.top_labels == ("laptop",)


def test_raw_probabilities_and_provider_confidence_are_kept_separately_as_returned() -> None:
    returned = _answer(CATEGORY_LABELS, "phone", {"phone": 0.6, "laptop": 0.3}, confidence=0.5)
    result = _understand(Server(_ok(_body(category=returned))))
    proposal = result.jev.category
    assert proposal.provider_probabilities == returned["probabilities"]
    assert proposal.provider_confidence == 0.5
    assert proposal.provider_confidence != proposal.provider_probabilities["phone"]
    assert "gate_confidence" not in CategoryProposal.model_fields
    assert not any("gate" in name for name in JevOutcome.model_fields)


def test_numeric_constraints_and_every_parsed_field_stay_deterministic() -> None:
    result = _understand(Server(_ok()))
    assert result.understanding.model_dump() == parse_normalized_query(QUERY).model_dump()
    assert result.understanding.ram_gb == 16 and result.understanding.max_price is not None


def test_request_is_one_typed_call_over_closed_label_sets_with_no_numeric_questions() -> None:
    server = Server(_ok())
    _understand(server)
    assert len(server.requests) == 1
    request = server.requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.typesafe.ai/v1/systemone")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "content-encoding" not in request.headers
    body = json.loads(request.content)
    assert set(body) == {"state", "model", "questions"}
    assert (body["state"], body["model"]) == (QUERY, MODEL)
    questions = body["questions"]
    assert set(questions) == {"category", "semantic_intent"}
    assert all(question["type"] == "choice" for question in questions.values())
    assert list(questions["category"]["criteria"]) == CATEGORY_LABELS
    assert list(questions["semantic_intent"]["criteria"]) == INTENT_LABELS


def test_explicit_none_option_is_kept_but_proposes_no_label() -> None:
    body = _body(category=_answer(CATEGORY_LABELS, "none"), intent=_answer(INTENT_LABELS, "none"))
    outcome = _understand(Server(_ok(body))).jev
    assert outcome.fallback_reason is None
    assert (outcome.category.label, outcome.category.raw_choice) == (None, "none")
    assert (outcome.semantic_intent.label, outcome.semantic_intent.top_labels) == (None, ("none",))


@pytest.mark.parametrize(
    ("choice", "top", "expected_top"),
    [
        ("phone", {"laptop": 0.45, "phone": 0.45}, ("laptop", "phone")),
        ("laptop", {"laptop": 0.45, "phone": 0.45}, ("laptop", "phone")),
        ("none", {"laptop": 0.4, "none": 0.4}, ("laptop", "none")),
        ("shoes", {"laptop": 0.2, "phone": 0.2, "shoes": 0.2, "headphones": 0.2}, None),
    ],
    ids=["second-of-two", "first-of-two", "label-and-none", "uniform"],
)
def test_equal_top_probabilities_keep_every_top_label_and_propose_none(
    choice, top, expected_top
) -> None:
    body = _body(category=_answer(CATEGORY_LABELS, choice, top))
    outcome = _understand(Server(_ok(body))).jev
    assert outcome.fallback_reason is None
    proposal = outcome.category
    assert proposal.raw_choice == choice and proposal.label is None
    assert proposal.top_labels == (expected_top or tuple(CATEGORY_LABELS))


# --- invalid responses are rejected as a whole --------------------------------------------------


def _set_category(**changes) -> dict:
    return _body(category=_answer(CATEGORY_LABELS, "laptop", **changes))


def _without(mapping: dict, key: str) -> dict:
    return {k: v for k, v in mapping.items() if k != key}


_PROBS = _probabilities(CATEGORY_LABELS, {"laptop": 0.85})
_INVALID_BODIES = {
    "choice-out-of-set": _body(category=_answer(CATEGORY_LABELS, "tablet", {"laptop": 0.85})),
    "choice-not-top": _body(category=_answer(CATEGORY_LABELS, "phone", {"laptop": 0.85})),
    "missing-label": _set_category(probabilities=_without(_PROBS, "none")),
    "extra-label": _set_category(probabilities={**_PROBS, "tablet": 0.0}),
    "probability-above-one": _set_category(probabilities={**_PROBS, "laptop": 1.2}),
    "negative-probability": _set_category(probabilities={**_PROBS, "none": -0.05}),
    "sum-not-one": _set_category(probabilities={**_PROBS, "laptop": 0.5}),
    "bool-probability": _set_category(probabilities={**_PROBS, "none": True}),
    "confidence-above-one": _set_category(confidence=1.5),
    "negative-confidence": _set_category(confidence=-0.1),
    "string-confidence": _set_category(confidence="0.9"),
    "noul-answer": _body(category={"type": "noul", "noul": 0.9}),
    "missing-answer": {**_body(), "answers": _without(_body()["answers"], "semantic_intent")},
    "extra-answer": {
        **_body(),
        "answers": {**_body()["answers"], "brand": _answer(CATEGORY_LABELS, "laptop")},
    },
    "missing-model": _without(_body(), "model"),
    "negative-tokens": _body(usage={"input_tokens": -1, "output_tokens": 0}),
}


@pytest.mark.parametrize("body", list(_INVALID_BODIES.values()), ids=list(_INVALID_BODIES))
def test_invalid_answers_fall_back_safely_without_any_proposal(body) -> None:
    server = Server(_ok(body))
    result = _understand(server)
    assert result.understanding == parse_normalized_query(QUERY)
    outcome = result.jev
    assert outcome.fallback_reason == "invalid_response"
    assert (outcome.category, outcome.semantic_intent) == (None, None)
    assert len(server.requests) == 1  # a successful HTTP status is never retried


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"", b'{"model": "jev-1.13.0", "answers": NaN, "usage": {}}', b"[]"],
    ids=["malformed", "empty", "nan-literal", "array"],
)
def test_unparseable_bodies_fall_back_as_invalid_responses(content) -> None:
    result = _understand(Server(httpx2.Response(200, content=content)))
    assert result.jev.fallback_reason == "invalid_response"


def test_a_different_answering_model_is_a_model_mismatch() -> None:
    result = _understand(Server(_ok(_body(model="jev-1.14.0"))))
    assert result.jev.fallback_reason == "model_mismatch"
    assert result.jev.category is None


# --- runtime failures, retries and timing -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason", "attempts"),
    [
        (500, "server_error", 2),
        (503, "server_error", 2),
        (529, "server_error", 2),
        (429, "rate_limited", 2),
        (408, "timeout", 2),
        (401, "auth_error", 1),
        (403, "auth_error", 1),
        (400, "request_rejected", 1),
        (404, "request_rejected", 1),
        (413, "request_rejected", 1),
        (422, "request_rejected", 1),
    ],
)
def test_http_failures_map_to_fixed_reasons_with_one_sdk_retry_layer(status, reason, attempts):
    server = Server(httpx2.Response(status, json={"error": "scripted"}))
    result = _understand(server)
    assert result.jev.fallback_reason == reason
    assert len(server.requests) == attempts
    assert result.understanding == parse_normalized_query(QUERY)


def test_a_retried_failure_can_recover_on_the_second_attempt() -> None:
    server = Server(httpx2.Response(503), _ok())
    result = _understand(server)
    assert result.jev.fallback_reason is None
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx2.ReadTimeout("scripted"), "timeout"),
        (httpx2.ConnectTimeout("scripted"), "timeout"),
        (httpx2.ConnectError("scripted"), "transport_error"),
    ],
    ids=["read-timeout", "connect-timeout", "connect-error"],
)
def test_transport_failures_are_retried_once_then_fall_back(error, reason) -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise error

    provider = JevDecisionProvider(_config(), transport=httpx2.MockTransport(handler))
    result = provider.understand(QUERY, parse_normalized_query(QUERY))
    provider.close()
    assert result.jev.fallback_reason == reason
    assert len(calls) == 2


def test_zero_retries_makes_exactly_one_attempt() -> None:
    server = Server(httpx2.Response(503))
    assert _understand(server, max_retries=0).jev.fallback_reason == "server_error"
    assert len(server.requests) == 1


def test_retry_after_beyond_the_budget_stops_without_sleeping() -> None:
    server = Server(httpx2.Response(429, headers={"Retry-After": "10"}))
    started = time.perf_counter()
    result = _understand(server)
    elapsed = time.perf_counter() - started
    assert result.jev.fallback_reason == "rate_limited"
    assert len(server.requests) == 1
    assert elapsed < 1.0


def test_retry_after_is_honoured_and_is_not_capped_by_the_backoff_maximum() -> None:
    # 0.6 s is longer than JEV_BACKOFF_MAX_SECONDS (0.25 s): the SDK waits the server's value.
    server = Server(httpx2.Response(429, headers={"retry-after-ms": "600"}), _ok())
    started = time.perf_counter()
    result = _understand(server)
    elapsed = time.perf_counter() - started
    assert result.jev.fallback_reason is None
    assert len(server.requests) == 2
    # Margin for coarse Windows timers; still twice the backoff cap.
    assert elapsed >= 0.5 >= 2 * jev.JEV_BACKOFF_MAX_SECONDS


def test_the_retry_budget_is_not_a_wall_clock_deadline() -> None:
    # Each attempt takes 0.4 s. After the first: 0.4 s + <= 0.1 s backoff < 0.7 s budget, so the
    # SDK retries, and it never interrupts the second attempt: the call ends after the budget.
    def slow(response: httpx2.Response) -> Callable[[], httpx2.Response]:
        def reply() -> httpx2.Response:
            time.sleep(0.4)
            return response

        return reply

    server = Server(slow(httpx2.Response(503)), slow(_ok()))
    started = time.perf_counter()
    result = _understand(server, retry_budget_seconds=0.7)
    elapsed = time.perf_counter() - started
    assert result.jev.fallback_reason is None
    assert len(server.requests) == 2
    assert elapsed > 0.7


def test_the_attempt_timeout_reaches_httpx2_as_a_per_operation_timeout() -> None:
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.extensions["timeout"])
        return _ok()

    provider = JevDecisionProvider(
        _config(attempt_timeout_seconds=0.75), transport=httpx2.MockTransport(handler)
    )
    provider.understand(QUERY, parse_normalized_query(QUERY))
    provider.close()
    assert seen == [{"connect": 0.75, "read": 0.75, "write": 0.75, "pool": 0.75}]


# --- configuration, secrets and isolation -------------------------------------------------------


@pytest.mark.parametrize("bad_key", ["", "   ", "has space", "nön-ascii", "tab\tkey"])
def test_invalid_keys_fail_at_build_time_with_a_fixed_message(bad_key) -> None:
    with pytest.raises(JevConfigurationError) as caught:
        JevDecisionProvider(
            _config(api_key=SecretStr(bad_key)), transport=httpx2.MockTransport(Server())
        )
    assert str(caught.value) == "jev configuration invalid"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_an_invalid_retry_budget_fails_at_build_time() -> None:
    with pytest.raises(JevConfigurationError):
        JevDecisionProvider(_config(retry_budget_seconds=math.nan))


def test_build_decision_provider_is_deterministic_unless_configured() -> None:
    assert type(build_decision_provider(None)) is DeterministicDecisionProvider
    provider = build_decision_provider(_config())
    assert isinstance(provider, JevDecisionProvider)
    provider.close()


def test_sdk_debug_body_logging_is_suppressed() -> None:
    logging.getLogger("typesafe_sdk").setLevel(logging.DEBUG)
    _provider(Server()).close()
    assert logging.getLogger("typesafe_sdk").getEffectiveLevel() >= logging.WARNING


def test_key_and_query_never_reach_logs_results_or_reprs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    results = [
        _understand(Server(_ok())),
        _understand(Server(httpx2.Response(401, json={"error": f"bad key {KEY}"}))),
        _understand(Server(httpx2.Response(422, json={"detail": QUERY}))),
        _understand(Server(_ok(_set_category(confidence=7)))),
    ]
    provider = _provider(Server())
    texts = [caplog.text, repr(provider), repr(_config()), *(r.model_dump_json() for r in results)]
    provider.close()
    for text in texts:
        assert KEY not in text
    assert QUERY not in caplog.text
    reasons = [r.jev.fallback_reason for r in results]
    assert reasons == [None, "auth_error", "request_rejected", "invalid_response"]
    assert caplog.text.count("jev_decision model=jev-1.13.0") == 4


def test_the_app_still_uses_the_deterministic_provider_when_jev_is_enabled(make_settings) -> None:
    app = create_app(make_settings(jev_enabled=True, typesafe_api_key=KEY))
    assert type(app.state.decision_provider) is DeterministicDecisionProvider


def test_decision_result_rejects_inconsistent_jev_fields() -> None:
    understanding = parse_normalized_query(QUERY)
    fallback = JevOutcome(model=MODEL, latency_ms=1.0, fallback_reason="timeout")
    with pytest.raises(ValidationError):
        DecisionResult(understanding=understanding, provider="jev", provider_version="jq-1")
    with pytest.raises(ValidationError):
        DecisionResult(understanding=understanding, jev=fallback)
    with pytest.raises(ValidationError):
        JevOutcome(model=MODEL, latency_ms=1.0)
    with pytest.raises(ValidationError):
        CategoryProposal(
            label=Category.LAPTOP,
            raw_choice="laptop",
            top_labels=("laptop", "phone"),
            provider_probabilities={"laptop": 0.5, "phone": 0.5},
            provider_confidence=0.0,
        )


_DISABLED_PROBE = """
import sys, threading
from ecommerce_search.config import Settings
from ecommerce_search.decision import build_decision_provider
from ecommerce_search.query_understanding import parse_normalized_query
settings = Settings(_env_file=None, postgres_password="x")
provider = build_decision_provider(settings.jev_config())
provider.understand("hp laptop", parse_normalized_query("hp laptop"))
loaded = sorted(n for n in sys.modules if n.split(".")[0] in ("typesafe_sdk", "httpx2", "tenacity"))
print(type(provider).__name__, settings.jev_config(), ",".join(loaded), threading.active_count())
"""


def test_disabled_jev_needs_no_key_and_never_imports_the_sdk() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JEV_", "TYPESAFE_"))}
    done = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-c", _DISABLED_PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    # No key, no SDK import, and only the main thread (no loop thread was started).
    assert done.stdout.split() == ["DeterministicDecisionProvider", "None", "1"]


# --- provisional tolerances: boundary tests (validation is not loosened) -------------------------


def _category_probs(laptop: float) -> dict[str, float]:
    # The other four labels share exactly 0.2, so the sum is 0.8 + laptop.
    return {"laptop": laptop, "phone": 0.05, "shoes": 0.05, "headphones": 0.05, "none": 0.05}


@pytest.mark.parametrize(
    ("laptop", "accepted"),
    [(0.8, True), (0.8009, True), (0.7991, True), (0.8011, False), (0.7989, False)],
    ids=["exact", "sum+0.0009", "sum-0.0009", "sum+0.0011", "sum-0.0011"],
)
def test_probability_sum_tolerance_boundary(laptop, accepted) -> None:
    assert jev.PROBABILITY_SUM_TOLERANCE == 1e-3
    body = _set_category(probabilities=_category_probs(laptop))
    outcome = _understand(Server(_ok(body))).jev
    if accepted:
        assert outcome.fallback_reason is None
        assert outcome.category.provider_probabilities["laptop"] == laptop
    else:
        assert outcome.fallback_reason == "invalid_response"


@pytest.mark.parametrize(
    ("gap", "expected_top", "expected_label"),
    [
        (0.0, ("laptop", "phone"), None),
        (5e-10, ("laptop", "phone"), None),
        (2e-9, ("laptop",), Category.LAPTOP),
    ],
    ids=["equal", "within-tie-tolerance", "beyond-tie-tolerance"],
)
def test_top_tie_tolerance_boundary(gap, expected_top, expected_label) -> None:
    assert jev.TOP_TIE_TOLERANCE == 1e-9
    probabilities = {"laptop": 0.45, "phone": 0.45 - gap, "shoes": 0.05, "headphones": 0.05}
    probabilities["none"] = 1.0 - sum(probabilities.values())
    body = _set_category(probabilities=probabilities)
    proposal = _understand(Server(_ok(body))).jev.category
    assert (proposal.top_labels, proposal.label) == (expected_top, expected_label)


# --- disabled mode uses the existing deterministic-provider contract -----------------------------


def test_disabled_mode_is_the_deterministic_contract_with_no_jev_outcome() -> None:
    # Disabled is not a Jev fallback: no Jev provider exists, so no fallback reason is recorded.
    # The deterministic result itself (provider="deterministic", jev=None) shows no call was made.
    deterministic = parse_normalized_query(QUERY)
    result = build_decision_provider(None).understand(QUERY, deterministic)
    assert result == DeterministicDecisionProvider().understand(QUERY, deterministic)
    assert (result.provider, result.provider_version, result.jev) == ("deterministic", "qu-1", None)
    assert result.understanding is deterministic


# --- FR-DEC-3 deadline: bounded caller wait, cancellation, saturation and cleanup ----------------

# Scheduling tolerance for caller-wait assertions (OS thread scheduling, the GIL and event-loop
# latency). The deadline bounds the caller's wait plus this tolerance; it is not hard real time.
TOL = 0.5


def _wait_until(predicate, timeout: float = TOL) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _loop_task_count(provider: JevDecisionProvider) -> int:
    async def count() -> int:
        return len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    return asyncio.run_coroutine_threadsafe(count(), provider._loop).result(TOL)


def _deadline_config(deadline: float = 0.3, **overrides) -> JevConfig:
    values = {
        "deadline_seconds": deadline,
        "attempt_timeout_seconds": deadline,
        "retry_budget_seconds": deadline,
    }
    values.update(overrides)
    return _config(**values)


def test_deadline_cancels_a_slow_call_and_bounds_the_caller_wait() -> None:
    attempts: list[float] = []
    cancelled = threading.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(time.perf_counter())
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _ok()

    provider = JevDecisionProvider(_deadline_config(0.3), transport=httpx2.MockTransport(handler))
    try:
        deterministic = parse_normalized_query(QUERY)
        started = time.perf_counter()
        result = provider.understand(QUERY, deterministic)
        elapsed = time.perf_counter() - started
        assert result.jev.fallback_reason == "deadline_exceeded"
        assert (result.jev.category, result.jev.semantic_intent) == (None, None)
        assert result.understanding is deterministic
        assert 0.25 <= elapsed <= 0.3 + TOL
        assert cancelled.wait(TOL), "the in-flight attempt was not cancelled"
        assert _wait_until(lambda: _loop_task_count(provider) == 0)
        time.sleep(0.4)  # longer than the SDK backoff: no retry may follow a cancellation
        assert len(attempts) == 1
    finally:
        provider.close()


def test_cancellation_during_a_retry_wait_prevents_the_retry() -> None:
    # The server asks for a 1.5 s wait; the 0.3 s deadline cancels the SDK during that sleep.
    server = Server(httpx2.Response(429, headers={"retry-after-ms": "1500"}), _ok())
    provider = JevDecisionProvider(
        _deadline_config(0.3, retry_budget_seconds=5.0), transport=httpx2.MockTransport(server)
    )
    try:
        result = provider.understand(QUERY, parse_normalized_query(QUERY))
        assert result.jev.fallback_reason == "deadline_exceeded"
        time.sleep(1.5 + TOL)  # past the moment the retry would have started
        assert len(server.requests) == 1
    finally:
        provider.close()


def test_results_racing_the_deadline_are_used_whole_or_discarded_whole() -> None:
    # Completion lands near the deadline; each call must be a full success within the bound or a
    # clean deadline fallback, never a mix, and never a late result.
    async def handler(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(0.2)
        return _ok()

    provider = JevDecisionProvider(_deadline_config(0.2), transport=httpx2.MockTransport(handler))
    try:
        seen = set()
        for _ in range(8):
            started = time.perf_counter()
            outcome = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
            assert time.perf_counter() - started <= 0.2 + TOL
            if outcome.fallback_reason is None:
                assert outcome.category is not None and outcome.semantic_intent is not None
            else:
                assert outcome.fallback_reason == "deadline_exceeded"
                assert (outcome.category, outcome.semantic_intent) == (None, None)
            seen.add(outcome.fallback_reason)
            assert _wait_until(lambda: _loop_task_count(provider) == 0)
        assert seen <= {None, "deadline_exceeded"}
    finally:
        provider.close()


def test_a_call_that_ignores_cancellation_never_surfaces_and_holds_its_slot() -> None:
    finished = threading.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)  # keeps running after cancellation, like a slow server
        finished.set()
        return _ok()

    provider = JevDecisionProvider(
        _deadline_config(0.2, max_in_flight=1), transport=httpx2.MockTransport(handler)
    )
    try:
        first = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
        assert first.fallback_reason == "deadline_exceeded" and first.category is None
        # The cancelled call is still unwinding, so it still holds the only slot.
        second = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
        assert second.fallback_reason == "saturated"
        assert finished.wait(1.0 + TOL)
        assert _wait_until(lambda: _loop_task_count(provider) == 0)
        # Its late result was never returned; the released slot serves the next call.
        third = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
        assert third.fallback_reason == "deadline_exceeded"
    finally:
        provider.close()


def test_late_results_are_discarded_and_only_logged_without_content(caplog) -> None:
    caplog.set_level(logging.INFO, logger=jev.__name__)
    done: concurrent.futures.Future = concurrent.futures.Future()
    done.set_result(object())
    jev._discard_late_result(done)
    failed: concurrent.futures.Future = concurrent.futures.Future()
    failed.set_exception(RuntimeError("late failure"))
    jev._discard_late_result(failed)  # exception retrieved, nothing logged
    cancelled: concurrent.futures.Future = concurrent.futures.Future()
    cancelled.cancel()
    jev._discard_late_result(cancelled)
    assert [r.getMessage() for r in caplog.records] == ["jev_late_result_discarded"]


def test_saturation_fails_fast_without_queueing() -> None:
    started, release = threading.Event(), threading.Event()
    attempts: list[float] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(time.perf_counter())
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return _ok()

    provider = JevDecisionProvider(
        _deadline_config(5.0, max_in_flight=1), transport=httpx2.MockTransport(handler)
    )
    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(provider.understand(QUERY, parse_normalized_query(QUERY)))
    )
    try:
        worker.start()
        assert started.wait(TOL)
        t0 = time.perf_counter()
        saturated = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
        assert saturated.fallback_reason == "saturated"
        assert time.perf_counter() - t0 < TOL
        assert len(attempts) == 1  # the rejected call never reached the transport
        release.set()
        worker.join(5.0)
        assert results[0].jev.fallback_reason is None
        again = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
        assert again.fallback_reason is None
    finally:
        release.set()
        provider.close()


def test_close_cancels_pending_calls_and_is_idempotent(monkeypatch) -> None:
    import typesafe_sdk

    closed: list[bool] = []
    original_aclose = typesafe_sdk.AsyncTypeSafeClient.aclose

    async def counting_aclose(self) -> None:
        closed.append(True)
        await original_aclose(self)

    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "aclose", counting_aclose)
    started, cancelled = threading.Event(), threading.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _ok()

    provider = JevDecisionProvider(_deadline_config(10.0), transport=httpx2.MockTransport(handler))
    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(provider.understand(QUERY, parse_normalized_query(QUERY)))
    )
    worker.start()
    assert started.wait(TOL)
    t0 = time.perf_counter()
    provider.close()
    worker.join(TOL)
    assert not worker.is_alive() and time.perf_counter() - t0 < 1.0 + TOL
    assert cancelled.is_set()
    assert results[0].jev.fallback_reason == "provider_closed"
    assert provider._loop.is_closed() and not provider._thread.is_alive()
    assert closed == [True]
    provider.close()  # idempotent
    assert closed == [True]
    after = provider.understand(QUERY, parse_normalized_query(QUERY))
    assert after.jev.fallback_reason == "provider_closed"
    assert after.understanding == parse_normalized_query(QUERY)


def test_a_failed_loop_start_closes_the_client_and_leaves_no_thread(monkeypatch) -> None:
    import typesafe_sdk

    closed: list[bool] = []
    original_aclose = typesafe_sdk.AsyncTypeSafeClient.aclose

    async def counting_aclose(self) -> None:
        closed.append(True)
        await original_aclose(self)

    def failing_start(self) -> None:
        raise RuntimeError("thread start failed (test)")

    loops = _recording_loops(monkeypatch)
    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "aclose", counting_aclose)
    monkeypatch.setattr(threading.Thread, "start", failing_start)
    with pytest.raises(RuntimeError, match="test"):
        JevDecisionProvider(_config(), transport=httpx2.MockTransport(Server()))
    monkeypatch.undo()
    assert closed == [True]
    assert len(loops) == 1 and loops[0].is_closed()
    assert _loop_threads() == []


def test_configuration_failures_start_no_thread(monkeypatch) -> None:
    loops = _recording_loops(monkeypatch)
    for config in (
        _config(api_key=SecretStr("has space")),
        _config(deadline_seconds=0),
        _config(deadline_seconds=math.inf),
        _config(max_in_flight=0),
        _config(max_in_flight=True),
    ):
        with pytest.raises(JevConfigurationError):
            JevDecisionProvider(config, transport=httpx2.MockTransport(Server()))
        assert _loop_threads() == []
    # Only the invalid key gets as far as creating a loop (limits are checked first); it is closed.
    assert len(loops) == 1 and loops[0].is_closed()


def test_one_loop_and_client_serve_every_call_and_no_tasks_leak() -> None:
    provider = _provider(Server(_ok()))
    try:
        loop, thread, client = provider._loop, provider._thread, provider._client
        for _ in range(3):
            outcome = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
            assert outcome.fallback_reason is None
        assert (provider._loop, provider._thread, provider._client) == (loop, thread, client)
        assert len(_loop_threads()) == 1
        assert _wait_until(lambda: _loop_task_count(provider) == 0)
    finally:
        provider.close()
    assert loop.is_closed() and not thread.is_alive()


def test_the_module_guard_still_refuses_non_loopback_connections() -> None:
    probe = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="network access"):
            probe.connect(("192.0.2.1", 443))  # TEST-NET-1, never routed
    finally:
        probe.close()


def test_a_failed_loop_creation_builds_no_client_and_no_thread(monkeypatch) -> None:
    import typesafe_sdk

    built: list[bool] = []
    original_init = typesafe_sdk.AsyncTypeSafeClient.__init__

    def counting_init(self, *args, **kwargs) -> None:
        built.append(True)
        original_init(self, *args, **kwargs)

    def failing_loop() -> None:
        raise RuntimeError("loop creation failed (test)")

    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "__init__", counting_init)
    monkeypatch.setattr(jev.asyncio, "new_event_loop", failing_loop)
    with pytest.raises(RuntimeError, match="test"):
        JevDecisionProvider(_config(), transport=httpx2.MockTransport(Server()))
    monkeypatch.undo()
    assert built == []
    assert _loop_threads() == []


def test_deadline_cancellations_never_leak_a_slot() -> None:
    # A near-zero deadline cancels every call. Once everything has unwound, all slots must be free
    # again: none lost, none released twice. This guards slot accounting only. An offline probe
    # (25 sequential calls, 1e-6 s deadline, CPython 3.12 on Windows) found every call had started
    # before its cancel arrived, so this test need not exercise cancellation before a task starts;
    # that case is forced directly in test_cancel_before_start_and_submission_failure_release_once.
    server = Server(_ok())
    provider = JevDecisionProvider(
        _deadline_config(1e-6, max_in_flight=2), transport=httpx2.MockTransport(server)
    )
    try:
        for _ in range(25):
            outcome = provider.understand(QUERY, parse_normalized_query(QUERY)).jev
            assert outcome.fallback_reason in {None, "deadline_exceeded", "saturated"}
        assert _wait_until(lambda: _loop_task_count(provider) == 0)
        assert provider._slots._value == 2  # BoundedSemaphore: every slot released, none twice
    finally:
        provider.close()


# --- close() bounds, late-result logging and startup cleanup -------------------------------------

# Patched _CLOSE_TIMEOUT_SECONDS for the obstructed-close tests: close() returns within CAP plus
# scheduling tolerance, and the loop thread finishes cleanup when the obstruction ends.
CAP = 0.2


def _recording_loops(monkeypatch) -> list[asyncio.AbstractEventLoop]:
    loops: list[asyncio.AbstractEventLoop] = []
    real_new_event_loop = asyncio.new_event_loop

    def recording() -> asyncio.AbstractEventLoop:
        loop = real_new_event_loop()
        loops.append(loop)
        return loop

    monkeypatch.setattr(jev.asyncio, "new_event_loop", recording)
    return loops


def _in_thread(target: Callable[[], object]) -> tuple[threading.Thread, list]:
    results: list = []

    def run() -> None:
        try:
            results.append(target())
        except BaseException as error:  # handed to the test, which asserts on it
            results.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    return worker, results


def _call(provider: JevDecisionProvider) -> Callable[[], DecisionResult]:
    return lambda: provider.understand(QUERY, parse_normalized_query(QUERY))


def test_a_result_that_survives_close_is_never_surfaced() -> None:
    # A call that swallows close()'s cancellation and still returns a valid response must not
    # reach its caller as a success once close() has started.
    started = threading.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        started.set()
        # Swallowed: the call goes on to complete with a valid body.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(10)
        return _ok()

    provider = JevDecisionProvider(_deadline_config(5.0), transport=httpx2.MockTransport(handler))
    worker, results = _in_thread(_call(provider))
    assert started.wait(TOL)
    provider.close()
    worker.join(TOL)
    assert not worker.is_alive()
    outcome = results[0].jev
    assert outcome.fallback_reason == "provider_closed"
    assert (outcome.category, outcome.semantic_intent) == (None, None)


class _CountingSlots:
    """Wraps the provider's BoundedSemaphore and counts acquisitions, releases and the most slots
    held at once. A second release of one slot still raises (the wrapped semaphore is bounded)."""

    def __init__(self, inner: threading.BoundedSemaphore) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.acquired = self.released = self.held = self.peak = 0

    def acquire(self, blocking: bool = True) -> bool:
        taken = self._inner.acquire(blocking=blocking)
        if taken:
            with self._lock:
                self.acquired += 1
                self.held += 1
                self.peak = max(self.peak, self.held)
        return taken

    def release(self) -> None:
        self._inner.release()
        with self._lock:
            self.released += 1
            self.held -= 1


def _counting_slots(provider: JevDecisionProvider) -> _CountingSlots:
    slots = _CountingSlots(provider._slots)
    provider._slots = slots
    return slots


def _counting_aclose(monkeypatch) -> list[bool]:
    import typesafe_sdk

    closed: list[bool] = []
    original_aclose = typesafe_sdk.AsyncTypeSafeClient.aclose

    async def counting_aclose(self) -> None:
        closed.append(True)
        await original_aclose(self)

    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "aclose", counting_aclose)
    return closed


def _assert_fully_cleaned_up(
    provider: JevDecisionProvider, closed: list[bool], slots: _CountingSlots
) -> None:
    # Actual state, not expected warnings: thread gone, loop closed, client closed exactly once,
    # no task left on the loop, every slot released exactly once.
    assert not provider._thread.is_alive()
    assert provider._loop.is_closed()
    assert closed == [True]
    assert list(asyncio.all_tasks(provider._loop)) == []
    assert slots.acquired == slots.released and slots.held == 0


def test_a_blocked_loop_never_holds_more_than_max_in_flight_and_skips_timed_out_calls(
    monkeypatch,
) -> None:
    closed = _counting_aclose(monkeypatch)
    started = threading.Event()
    requests: list[float] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(time.perf_counter())
        started.set()
        time.sleep(0.8)  # synchronous: blocks the provider's loop thread
        return _ok()

    provider = JevDecisionProvider(
        _deadline_config(0.2, max_in_flight=2), transport=httpx2.MockTransport(handler)
    )
    slots = _counting_slots(provider)
    first, first_results = _in_thread(_call(provider))
    assert started.wait(TOL)
    # The loop is blocked: one more call is admitted and queued, every further call is refused at
    # once, and nothing beyond max_in_flight is ever queued or running.
    t0 = time.perf_counter()
    reasons = [provider.understand(QUERY, parse_normalized_query(QUERY)).jev.fallback_reason]
    reasons += [
        provider.understand(QUERY, parse_normalized_query(QUERY)).jev.fallback_reason
        for _ in range(9)
    ]
    assert time.perf_counter() - t0 <= 0.2 + TOL
    assert reasons == ["deadline_exceeded"] + ["saturated"] * 9
    assert slots.peak == 2
    first.join(TOL)
    assert first_results[0].jev.fallback_reason == "deadline_exceeded"
    # When the loop resumes, the timed-out queued call is skipped: it starts no API work.
    assert _wait_until(lambda: slots.held == 0, 1.0 + TOL)
    assert len(requests) == 1
    provider.close()
    _assert_fully_cleaned_up(provider, closed, slots)


def test_close_during_finite_blocking_returns_promptly_and_the_loop_thread_cleans_up(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(jev, "_CLOSE_TIMEOUT_SECONDS", CAP)
    closed = _counting_aclose(monkeypatch)
    started = threading.Event()
    requests: list[float] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(time.perf_counter())
        started.set()
        time.sleep(0.6)  # synchronous, finite: blocks the provider's loop thread
        return _ok()

    caplog.set_level(logging.WARNING, logger=jev.__name__)
    provider = JevDecisionProvider(
        _deadline_config(5.0, max_in_flight=2), transport=httpx2.MockTransport(handler)
    )
    slots = _counting_slots(provider)
    running = _in_thread(_call(provider))
    assert started.wait(TOL)
    queued = _in_thread(_call(provider))  # admitted, queued behind the blocked loop
    assert _wait_until(lambda: slots.held == 2)
    t0 = time.perf_counter()
    provider.close()
    assert time.perf_counter() - t0 <= CAP + TOL
    assert caplog.messages == ["jev_provider_cleanup_pending"]
    # The loop thread finishes cleanup itself once the blocking ends.
    provider._thread.join(0.6 + TOL)
    _assert_fully_cleaned_up(provider, closed, slots)
    for worker, results in (running, queued):
        worker.join(TOL)
        assert results[0].jev.fallback_reason == "provider_closed"
    assert len(requests) == 1  # the queued call was skipped, never sent


def test_close_during_cancellation_resistant_work_cleans_up_when_that_work_ends(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(jev, "_CLOSE_TIMEOUT_SECONDS", CAP)
    closed = _counting_aclose(monkeypatch)
    started, resisted = threading.Event(), threading.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.6)  # resists cancellation for a finite time, loop not blocked
            resisted.set()
        return _ok()

    caplog.set_level(logging.WARNING, logger=jev.__name__)
    provider = JevDecisionProvider(_deadline_config(5.0), transport=httpx2.MockTransport(handler))
    slots = _counting_slots(provider)
    worker, results = _in_thread(_call(provider))
    assert started.wait(TOL)
    t0 = time.perf_counter()
    provider.close()
    assert time.perf_counter() - t0 <= CAP + TOL
    assert caplog.messages == ["jev_provider_cleanup_pending"]
    provider._thread.join(0.6 + TOL)
    assert resisted.is_set()
    _assert_fully_cleaned_up(provider, closed, slots)
    worker.join(TOL)
    # Its valid response arrived after close() began and is never surfaced.
    assert results[0].jev.fallback_reason == "provider_closed"


def test_cancel_before_start_and_submission_failure_release_once(monkeypatch) -> None:
    server = Server(_ok())
    provider = _provider(server)
    slots = _counting_slots(provider)
    try:
        # A task cancelled before its first step: started and cancelled in one loop callback.
        def start_then_cancel(call: jev._Call) -> None:
            provider._start(call)
            call.task.cancel()

        assert slots.acquire(blocking=False)  # what admission in understand() does
        call = jev._Call(QUERY, {})
        provider._loop.call_soon_threadsafe(start_then_cancel, call)
        with pytest.raises(concurrent.futures.CancelledError):
            call.future.result(TOL)
        assert _wait_until(lambda: slots.released == 1)
        assert (slots.acquired, slots.released) == (1, 1)

        # A submission that fails before anything is queued: the caller releases its slot.
        def failing_submit(*args) -> None:
            raise RuntimeError("submission failed (test)")

        monkeypatch.setattr(provider._loop, "call_soon_threadsafe", failing_submit)
        with pytest.raises(RuntimeError, match="test"):
            provider.understand(QUERY, parse_normalized_query(QUERY))
        monkeypatch.undo()
        assert (slots.acquired, slots.released) == (2, 2)
        assert server.requests == []  # neither call reached the transport

        # Nothing was lost or double-released: the provider still serves calls.
        assert provider.understand(QUERY, parse_normalized_query(QUERY)).jev.fallback_reason is None
        assert _wait_until(lambda: slots.held == 0)
        assert (slots.acquired, slots.released) == (3, 3)
    finally:
        provider.close()


def test_close_shuts_down_the_loop_default_executor() -> None:
    provider = _provider(Server(_ok()))
    before = set(threading.enumerate())

    async def use_default_executor() -> None:
        await asyncio.get_running_loop().run_in_executor(None, time.sleep, 0.01)

    asyncio.run_coroutine_threadsafe(use_default_executor(), provider._loop).result(TOL)
    assert [t for t in threading.enumerate() if t not in before] != []  # executor thread exists
    provider.close()
    # Joined by close(), not merely told to stop: nothing new is alive when close() returns.
    assert [t.name for t in threading.enumerate() if t not in before] == []


def test_concurrent_close_and_calls_resolve_cleanly() -> None:
    entered: list[float] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        entered.append(time.perf_counter())
        await asyncio.sleep(10)
        return _ok()

    provider = JevDecisionProvider(
        _deadline_config(5.0, max_in_flight=4), transport=httpx2.MockTransport(handler)
    )
    running = [_in_thread(_call(provider)) for _ in range(6)]
    assert _wait_until(lambda: len(entered) == 4)
    racing = [_in_thread(_call(provider)) for _ in range(3)]
    closers = [_in_thread(provider.close) for _ in range(2)]
    workers = running + racing + closers
    for worker, _ in workers:
        worker.join(1.0 + TOL)
    assert all(not worker.is_alive() for worker, _ in workers)
    first = sorted(results[0].jev.fallback_reason for _, results in running)
    assert first == ["provider_closed"] * 4 + ["saturated"] * 2
    for _, results in racing:
        assert results[0].jev.fallback_reason in {"provider_closed", "saturated"}
    assert [results for _, results in closers] == [[None], [None]]
    assert len(entered) == 4  # nothing reached the transport after close() began
    assert provider._loop.is_closed() and not provider._thread.is_alive()


def test_a_non_sdk_error_while_building_the_client_closes_the_loop(monkeypatch) -> None:
    import typesafe_sdk

    def failing_init(self, *args, **kwargs) -> None:
        raise ValueError("client construction failed (test)")

    loops = _recording_loops(monkeypatch)
    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "__init__", failing_init)
    with pytest.raises(ValueError, match="test"):
        JevDecisionProvider(_config(), transport=httpx2.MockTransport(Server()))
    monkeypatch.undo()
    assert len(loops) == 1 and loops[0].is_closed()
    assert _loop_threads() == []


def test_a_failing_client_close_is_logged_safely_and_the_loop_still_closes(
    monkeypatch, caplog
) -> None:
    import typesafe_sdk

    attempts: list[bool] = []

    async def failing_aclose(self) -> None:
        # A fake client close that fails; its message carries the fake key and the query.
        attempts.append(True)
        raise RuntimeError(f"close failed for {KEY} on {QUERY}")

    monkeypatch.setattr(typesafe_sdk.AsyncTypeSafeClient, "aclose", failing_aclose)
    caplog.set_level(logging.DEBUG)
    provider = _provider(Server(_ok()))
    slots = _counting_slots(provider)
    assert provider.understand(QUERY, parse_normalized_query(QUERY)).jev.fallback_reason is None
    t0 = time.perf_counter()
    provider.close()
    assert time.perf_counter() - t0 <= TOL
    # The documented outcome: the failure is logged by its type only, and the loop thread still
    # closes the loop and exits. No task or slot is left behind.
    assert attempts == [True]
    warned = [r.getMessage() for r in caplog.records if r.name == jev.__name__ and r.levelno >= 30]
    assert warned == ["jev_provider_cleanup_failed error=RuntimeError"]
    assert not provider._thread.is_alive() and provider._loop.is_closed()
    assert list(asyncio.all_tasks(provider._loop)) == []
    assert (slots.acquired, slots.released, slots.held) == (1, 1, 0)
    # Neither the fake key nor the query reaches any log record, message or traceback.
    formatter = logging.Formatter()
    for record in caplog.records:
        rendered = record.getMessage()
        if record.exc_info:
            rendered += formatter.formatException(record.exc_info)
        assert KEY not in rendered and QUERY not in rendered
    assert KEY not in caplog.text and QUERY not in caplog.text
