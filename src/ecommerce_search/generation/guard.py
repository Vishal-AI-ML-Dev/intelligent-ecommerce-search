"""Usage guard around a generation provider (Milestone 16): quota and abuse bounds.

Every provider request, including a repair, must pass three in-process checks before it is sent:
at most `max_concurrency` requests in flight, at most `per_minute` requests in any 60-second
window, and at most `per_day` requests per UTC day. A refused request is not sent and never
waits or retries: it returns a fixed failure code, so the assistant shows an honest "generation
unavailable" state with plain search results. Limits are per process; the app runs one worker
(`Dockerfile`), so they apply to the whole server. Counters reset on restart.

This bounds how much quota the server can use. It is not an access restriction and does not
satisfy any provider audience, region or privacy condition.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ecommerce_search.generation.provider import (
    GenerationFailure,
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
)

WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class GuardLimits:
    max_concurrency: int
    per_minute: int
    per_day: int


class GuardedGenerationProvider:
    def __init__(
        self,
        inner: GenerationProvider,
        limits: GuardLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
    ) -> None:
        self.inner = inner
        self.limits = limits
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()
        self._in_flight = 0
        self._recent: deque[float] = deque()
        self._day = ""
        self._day_count = 0

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()

    def _admit(self) -> GenerationFailure | None:
        with self._lock:
            now = self._clock()
            while self._recent and now - self._recent[0] >= WINDOW_SECONDS:
                self._recent.popleft()
            day = self._today()
            if day != self._day:
                self._day, self._day_count = day, 0
            if self._in_flight >= self.limits.max_concurrency:
                return "local_concurrency_limit"
            if len(self._recent) >= self.limits.per_minute:
                return "local_rate_limit"
            if self._day_count >= self.limits.per_day:
                return "local_daily_cap"
            self._in_flight += 1
            self._recent.append(now)
            self._day_count += 1
            return None

    def generate(self, request: GenerationRequest) -> GenerationResult:
        refused = self._admit()
        if refused is not None:
            return GenerationResult(
                draft=None, failure=refused, input_tokens=None, output_tokens=None
            )
        try:
            return self.inner.generate(request)
        finally:
            with self._lock:
                self._in_flight -= 1
