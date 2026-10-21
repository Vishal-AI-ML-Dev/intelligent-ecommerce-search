"""httpx2 transports for the M14 listing runner. Imported only when a provider is built, so the
evaluator and CLI import no HTTP client.

* `JournalingTransport` wraps the transport that actually sends. For every HTTP attempt it:
  - asks the runner's hooks first, which check the attempt cap and refuse a repeated call, then
    journal (and fsync) an `attempt` line naming call `A` (category and subcategory) or `B`
    (claims);
  - forwards the request;
  - records the outcome: the HTTP status, the request id, and the usage read from the response
    body, or the exception type when nothing came back.

  Every attempt passes through it, SDK retries included, so none can bypass the cap or the
  journal. The runner also sets SDK retries to 0.
* `SyntheticTransport` is the dry-run inner transport. It never delegates to anything, opens no
  socket and needs no key. It answers each `jl-1` question with its first label and fixed,
  configurable token counts. Its answers are not Jev output.
"""

import json
from collections.abc import Mapping
from typing import Any, Protocol

import httpx2

DRY_RUN_MODEL = "jev-1.13.0"


class AttemptHooks(Protocol):
    def before(self, call: str) -> int:
        """Journal the attempt and return its number, or raise to refuse it."""
        ...

    def after(
        self,
        attempt: int,
        status: int | None,
        usage: tuple[int, int] | None,
        request_id: str | None,
        error_type: str | None,
    ) -> None: ...


def call_of(body: Mapping[str, Any]) -> str:
    state = body.get("state")
    return "B" if isinstance(state, Mapping) and "claims" in state else "A"


def usage_of(content: bytes) -> tuple[int, int] | None:
    """(input, output) tokens from a response body, or None unless both are valid counts."""
    try:
        usage = json.loads(content).get("usage")
        counts = (usage["input_tokens"], usage["output_tokens"])
    except (ValueError, AttributeError, KeyError, TypeError):
        return None
    if all(isinstance(c, int) and not isinstance(c, bool) and c >= 0 for c in counts):
        return counts
    return None


class CallRefused(httpx2.TransportError):
    """A request the runner refused before sending. As a transport error, the SDK turns it into
    a provider fallback, so the listing still gets an outcome and keeps call A's usage. The
    exact reason is in the journal's `refused` event."""


class JournalingTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport, hooks: AttemptHooks) -> None:
        self._inner = inner
        self._hooks = hooks

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(await request.aread())
        try:
            attempt = self._hooks.before(call_of(body))
        except Exception as refusal:  # nothing was sent
            raise CallRefused(str(refusal), request=request) from None
        try:
            response = await self._inner.handle_async_request(request)
            content = await response.aread()
        except BaseException as exc:  # includes cancellation at the deadline: never accounted
            self._hooks.after(attempt, None, None, None, type(exc).__name__)
            raise
        request_id = response.headers.get("x-typesafe-request-id")
        self._hooks.after(attempt, response.status_code, usage_of(content), request_id, None)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class SyntheticTransport(httpx2.AsyncBaseTransport):
    """Offline by construction: it answers from the request alone and has no inner transport."""

    def __init__(self, input_tokens: int = 200, output_tokens: int = 1) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.requests = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(await request.aread())
        self.requests += 1
        answers = {}
        for question_id, question in body["questions"].items():
            labels = list(question["criteria"])
            answers[question_id] = {
                "type": "choice",
                "choice": labels[0],
                "confidence": 0.5,
                "probabilities": {
                    label: (1.0 if i == 0 else 0.0) for i, label in enumerate(labels)
                },
            }
        payload = {
            "model": body.get("model", DRY_RUN_MODEL),
            "answers": answers,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
        }
        return httpx2.Response(
            200, json=payload, headers={"x-typesafe-request-id": f"dry-{self.requests}"}
        )
