"""Milestone 16: the generation usage guard. Fake provider and clocks; no network."""

import threading

from test_assistant_workflow import EVIDENCE, LIMITS, FakeRetriever, run

from ecommerce_search.generation.guard import GuardedGenerationProvider, GuardLimits
from ecommerce_search.generation.provider import (
    GenerationRequest,
    GenerationResult,
    GroundedDraft,
)

REQUEST = GenerationRequest("rules", "data", 256)
OK = GenerationResult(
    draft=GroundedDraft(outcome="abstain", selected=()),
    failure=None,
    input_tokens=1,
    output_tokens=1,
)


class Counting:
    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return OK


class Clock:
    def __init__(self):
        self.now = 0.0
        self.day = "2026-10-23"


def guarded(inner, clock, *, concurrency=2, per_minute=3, per_day=5):
    return GuardedGenerationProvider(
        inner,
        GuardLimits(max_concurrency=concurrency, per_minute=per_minute, per_day=per_day),
        clock=lambda: clock.now,
        today=lambda: clock.day,
    )


def test_per_minute_limit_refuses_without_sending_and_recovers_after_the_window():
    inner, clock = Counting(), Clock()
    guard = guarded(inner, clock)
    assert [guard.generate(REQUEST).failure for _ in range(4)] == [
        None,
        None,
        None,
        "local_rate_limit",
    ]
    assert inner.calls == 3
    clock.now = 60.0
    assert guard.generate(REQUEST).failure is None
    assert inner.calls == 4


def test_daily_cap_counts_every_request_and_resets_on_a_new_utc_day():
    inner, clock = Counting(), Clock()
    guard = guarded(inner, clock, per_minute=60, per_day=2)
    results = [guard.generate(REQUEST).failure for _ in range(3)]
    assert results == [None, None, "local_daily_cap"] and inner.calls == 2
    clock.day = "2026-10-24"
    assert guard.generate(REQUEST).failure is None


def test_zero_per_day_disables_generation():
    inner, clock = Counting(), Clock()
    assert guarded(inner, clock, per_day=0).generate(REQUEST).failure == "local_daily_cap"
    assert inner.calls == 0


def test_concurrency_limit_refuses_while_requests_are_in_flight():
    release = threading.Event()
    entered = threading.Event()

    class Slow(Counting):
        def generate(self, request):
            self.calls += 1
            entered.set()
            release.wait(5)
            return OK

    inner, clock = Slow(), Clock()
    guard = guarded(inner, clock, concurrency=1, per_minute=60, per_day=60)
    worker = threading.Thread(target=guard.generate, args=(REQUEST,))
    worker.start()
    assert entered.wait(5)
    assert guard.generate(REQUEST).failure == "local_concurrency_limit"
    release.set()
    worker.join(5)
    assert guard.generate(REQUEST).failure is None
    assert inner.calls == 2


def test_a_guard_refusal_shows_an_honest_usage_limit_state():
    inner, clock = Counting(), Clock()
    guard = guarded(inner, clock, per_day=0)
    response = run("laptop for coding", guard, FakeRetriever(EVIDENCE), LIMITS)
    assert response.outcome == "generation_unavailable"
    assert response.generation.failure == "local_daily_cap"
    assert response.notice.startswith("No generated answer is available right now")
    assert "usage limit" in response.notice
    assert [p.product_id for p in response.evidence] == ["P1", "P2", "P3"]
    assert inner.calls == 0


def test_a_provider_quota_error_also_shows_the_usage_limit_state():
    from test_assistant_workflow import FakeGenerator, failed

    response = run("laptop for coding", FakeGenerator(failed("rate_limited")))
    assert response.outcome == "generation_unavailable"
    assert "usage limit" in response.notice
    assert response.generation.calls == 1  # no retry
