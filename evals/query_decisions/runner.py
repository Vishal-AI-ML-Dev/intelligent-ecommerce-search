"""Resumable, budgeted Jev runner for the sealed M12 diagnostic batch.

One `jq-1` call per sealed query, strictly one at a time (`max_in_flight=1`) and with SDK retries
off (`max_retries=0`), so one dispatch is at most one HTTP attempt. Jev's question set, the
provider and the filter policy are unchanged; nothing here gates, calibrates or filters.

Accounting (`runs/<run-id>/journal.jsonl`, append-only, fsynced per line):

* A `dispatch` event is written and fsynced **before** the provider is called, so a crash can
  never hide an attempt. Its `result` follows. A dispatch without a result (crash, kill, or an
  output that failed the secret check) is an attempt with an unknown outcome.
* Every query is dispatched at most once. There is no resend path: a query whose attempt failed
  keeps its fallback (or unknown) outcome. `max_attempts` may not exceed the number of queries.
* `run.json` fixes the mode, cap, prices and seal binding when the run starts; a later invocation
  with different values is refused.

Stop conditions (a `stop` event is written; the next invocation refuses to continue until it is
started with `resume_approved=True`, which is itself journalled):

* the attempt cap, or the estimated-spend tripwire (live mode);
* any `auth_error`, `model_mismatch`, `request_rejected`, `saturated` or `provider_closed`;
* three consecutive transient fallbacks (timeout, transport, rate limit, server, deadline);
* `invalid_response` above 2 % once at least 20 results exist;
* the API key appearing in a serialized result (that result is dropped, never written);
* any unexpected exception or an interruption.

The estimated spend is computed from returned token counts and prices the operator supplies with
the date they were checked. It is a tripwire, not a billing cap: failed or cancelled requests may
be billed without returning usage, and no provider-side spend limit is known.

Output is redacted: the journal stores query ids and validated `JevOutcome`s (labels,
probabilities, token counts, latency, request id), never query text, keys, headers or exception
text. The dry-run mode drives the real provider against an in-memory transport that returns a
fixed synthetic answer; it never touches the network and its outcomes are not Jev output.
"""

import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    TypeAdapter,
    ValidationError,
)

from ecommerce_search.decision.provider import DecisionResult, JevOutcome
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.query_understanding.models import QueryUnderstanding
from evals.contracts.canonical import decode_json_object, encode_json_object
from evals.contracts.models import Sha256, UtcTimestamp
from evals.query_decisions.contracts import (
    QUESTION_SET_VERSION,
    DecisionQuery,
    DecisionQueryId,
    LabelSeal,
    QueryDecisionError,
)
from evals.query_decisions.labeling import load_sealed
from evals.runners.golden_io import safe_child, write_exclusive

RUNS_DIR = "runs"
RUN_FILE = "run.json"
JOURNAL_FILE = "journal.jsonl"
RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
TRANSIENT_FALLBACKS = frozenset(
    {"timeout", "transport_error", "rate_limited", "server_error", "deadline_exceeded"}
)
STOPPING_FALLBACKS = frozenset(
    {"auth_error", "model_mismatch", "request_rejected", "saturated", "provider_closed"}
)
CONSECUTIVE_TRANSIENT_LIMIT = 3
INVALID_RESPONSE_MAX_RATE = Decimal("0.02")
INVALID_RESPONSE_MIN_RESULTS = 20
DRY_RUN_MODEL = "jev-1.13.0"
# The only model approved for live diagnostic calls (owner, 2026-10-18).
APPROVED_LIVE_MODEL = "jev-1.13.0"

StopReason = Literal[
    "attempt_cap",
    "spend_tripwire",
    "auth_error",
    "model_mismatch",
    "request_rejected",
    "saturated",
    "provider_closed",
    "consecutive_transient",
    "invalid_response_rate",
    "secret_in_output",
    "unexpected_exception",
    "interrupted",
]


class RunMode(StrEnum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunHeader(_Frozen):
    schema_version: Literal["qd-run-2"] = "qd-run-2"
    run_id: Annotated[StrictStr, Field(pattern=RUN_ID_PATTERN)]
    mode: RunMode
    queries_sha256: Sha256
    labels_sha256: Sha256
    question_set_version: Literal["jq-1"] = QUESTION_SET_VERSION
    model: StrictStr
    max_attempts: Annotated[int, Field(ge=1)]
    max_retries: Literal[0] = 0
    max_in_flight: Literal[1] = 1
    price_usd_per_million_input: Decimal | None = None
    price_usd_per_million_output: Decimal | None = None
    price_checked_on: date | None = None
    max_estimated_usd: Decimal | None = None
    # The operator's recorded decision to send AI-authored queries in this live run. It does not
    # by itself authorize paid calls.
    ai_authored_approved: StrictBool = False

    def sort_key(self) -> tuple[str, ...]:
        return (self.run_id,)


class DispatchEvent(_Frozen):
    event: Literal["dispatch"] = "dispatch"
    attempt: Annotated[int, Field(ge=1)]
    query_id: DecisionQueryId
    at: UtcTimestamp


class ResultEvent(_Frozen):
    event: Literal["result"] = "result"
    attempt: Annotated[int, Field(ge=1)]
    query_id: DecisionQueryId
    at: UtcTimestamp
    outcome: JevOutcome


class StopEvent(_Frozen):
    event: Literal["stop"] = "stop"
    reason: StopReason
    at: UtcTimestamp
    detail: Annotated[StrictStr, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,79}$")] | None = None


class ResumeApprovedEvent(_Frozen):
    event: Literal["resume_approved"] = "resume_approved"
    at: UtcTimestamp


class CompleteEvent(_Frozen):
    event: Literal["complete"] = "complete"
    at: UtcTimestamp


JournalEvent = Annotated[
    DispatchEvent | ResultEvent | StopEvent | ResumeApprovedEvent | CompleteEvent,
    Field(discriminator="event"),
]
_EVENT = TypeAdapter(JournalEvent)


class Provider(Protocol):
    def understand(
        self, query: str, deterministic_result: QueryUnderstanding
    ) -> DecisionResult: ...

    def close(self) -> None: ...


@dataclass
class JournalState:
    attempts: int = 0
    dispatched: dict[str, int] = field(default_factory=dict)  # query id -> attempt
    results: dict[str, JevOutcome] = field(default_factory=dict)  # query id -> outcome
    blocked_by: str | None = None
    complete: bool = False

    @property
    def unresolved(self) -> list[str]:
        return sorted(q for q in self.dispatched if q not in self.results)

    def fallback_counts(self) -> Counter[str]:
        return Counter(o.fallback_reason or "valid" for o in self.results.values())


def read_journal(path: Path) -> tuple[list[JournalEvent], JournalState]:
    events: list[JournalEvent] = []
    if path.is_file():
        text = path.read_bytes().decode("utf-8")
        if text and not text.endswith("\n"):
            raise QueryDecisionError("the journal's last line is incomplete; inspect it by hand")
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                events.append(_EVENT.validate_json(line))
            except ValidationError:
                raise QueryDecisionError(f"journal line {number} is invalid") from None
    state = JournalState()
    acknowledged: set[str] = set()
    for event in events:
        if isinstance(event, DispatchEvent):
            if event.query_id in state.dispatched or event.attempt != state.attempts + 1:
                raise QueryDecisionError("journal dispatches are not unique and sequential")
            state.attempts = event.attempt
            state.dispatched[event.query_id] = event.attempt
        elif isinstance(event, ResultEvent):
            if state.dispatched.get(event.query_id) != event.attempt or (
                event.query_id in state.results
            ):
                raise QueryDecisionError("journal result without its dispatch")
            state.results[event.query_id] = event.outcome
        elif isinstance(event, StopEvent):
            state.blocked_by = f"stop: {event.reason}"
        elif isinstance(event, ResumeApprovedEvent):
            state.blocked_by = None
            acknowledged.update(state.unresolved)
        else:
            state.complete = True
    if state.blocked_by is None and set(state.unresolved) - acknowledged:
        state.blocked_by = "an attempt has no recorded outcome"
    return events, state


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    mode: RunMode
    attempts: int
    max_attempts: int
    results: int
    unresolved: int
    not_attempted: int
    outcomes: dict[str, int]
    estimated_usd: Decimal | None
    stopped: str | None
    complete: bool


def estimated_spend(header: RunHeader, outcomes: Sequence[JevOutcome]) -> Decimal | None:
    if header.price_usd_per_million_input is None or header.price_usd_per_million_output is None:
        return None
    tokens_in = sum(o.input_tokens or 0 for o in outcomes)
    tokens_out = sum(o.output_tokens or 0 for o in outcomes)
    million = Decimal(1_000_000)
    return (
        tokens_in * header.price_usd_per_million_input
        + tokens_out * header.price_usd_per_million_output
    ) / million


def _append(path: Path, event: BaseModel) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(event.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _stop_for(outcome: JevOutcome, state: JournalState, consecutive: int) -> StopReason | None:
    reason = outcome.fallback_reason
    if reason in STOPPING_FALLBACKS:
        return reason  # type: ignore[return-value]
    if consecutive >= CONSECUTIVE_TRANSIENT_LIMIT:
        return "consecutive_transient"
    total = len(state.results)
    invalid = state.fallback_counts()["invalid_response"]
    if total >= INVALID_RESPONSE_MIN_RESULTS and invalid > INVALID_RESPONSE_MAX_RATE * total:
        return "invalid_response_rate"
    return None


def live_refusal(
    seal: LabelSeal, *, prices_complete: bool, ai_authored_approved: bool
) -> str | None:
    """Why a live run over this sealed batch must not start, or None. Pure; checked by the CLI
    before `Settings` (and the key) is read, and again by `run_diagnostic`. In order: missing
    prices, date or tripwire; any synthetic fixture (always refused, approval or not); AI-authored
    queries without the recorded approval; an approval recorded for a batch without any."""
    if not prices_complete:
        return "live mode needs both prices, their check date and a tripwire"
    if seal.synthetic:
        return "synthetic test fixtures are never sent to Jev"
    if seal.ai_authored_queries and not ai_authored_approved:
        return "the batch has AI-authored queries; live mode needs --approve-ai-authored"
    if ai_authored_approved and not seal.ai_authored_queries:
        return "--approve-ai-authored was given but the batch has no AI-authored queries"
    return None


def run_diagnostic(
    work_dir: Path,
    header: RunHeader,
    provider_factory: Callable[[], Provider],
    *,
    secret: str | None = None,
    resume_approved: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    say: Callable[[str], None] = print,
) -> RunSummary:
    """Start or resume `header.run_id` over the sealed batch (see the module docstring)."""
    seal, queries, _labels = load_sealed(work_dir)
    if (header.queries_sha256, header.labels_sha256) != (seal.queries_sha256, seal.labels_sha256):
        raise QueryDecisionError("the run header does not match the sealed batch")
    if header.mode is RunMode.LIVE:
        if header.model != APPROVED_LIVE_MODEL:
            raise QueryDecisionError(f"live runs must use the approved model {APPROVED_LIVE_MODEL}")
        prices = (
            header.price_usd_per_million_input,
            header.price_usd_per_million_output,
            header.price_checked_on,
            header.max_estimated_usd,
        )
        refusal = live_refusal(
            seal,
            prices_complete=None not in prices,
            ai_authored_approved=header.ai_authored_approved,
        )
        if refusal is not None:
            raise QueryDecisionError(refusal)
        if not secret:
            raise QueryDecisionError("live mode needs the API key")
    elif header.ai_authored_approved:
        raise QueryDecisionError("the AI-authored approval applies to live runs only")
    if header.max_attempts > len(queries):
        raise QueryDecisionError("max_attempts may not exceed the number of queries (no resends)")

    run_file = safe_child(work_dir, f"{RUNS_DIR}/{header.run_id}/{RUN_FILE}")
    if run_file.is_file():
        if decode_json_object(run_file.read_bytes(), RunHeader) != header:
            raise QueryDecisionError("this run was started with different settings")
    else:
        write_exclusive(
            work_dir, {f"{RUNS_DIR}/{header.run_id}/{RUN_FILE}": encode_json_object(header)}
        )
    journal = safe_child(work_dir, f"{RUNS_DIR}/{header.run_id}/{JOURNAL_FILE}")

    _events, state = read_journal(journal)
    stopped: str | None = None
    if state.complete:
        say("the run is already complete; nothing to do")
    elif state.blocked_by is not None and not resume_approved:
        raise QueryDecisionError(f"the run is blocked ({state.blocked_by}); resume needs approval")
    elif state.blocked_by is None and resume_approved:
        raise QueryDecisionError("the run is not blocked; there is nothing to approve")
    else:
        if resume_approved:
            _append(journal, ResumeApprovedEvent(at=now()))
        stopped = _dispatch_pending(header, queries, state, journal, provider_factory, secret, now)
    _events, state = read_journal(journal)
    summary = RunSummary(
        run_id=header.run_id,
        mode=header.mode,
        attempts=state.attempts,
        max_attempts=header.max_attempts,
        results=len(state.results),
        unresolved=len(state.unresolved),
        not_attempted=len(queries) - len(state.dispatched),
        outcomes=dict(sorted(state.fallback_counts().items())),
        estimated_usd=estimated_spend(header, list(state.results.values())),
        stopped=stopped,
        complete=state.complete,
    )
    say(_summary_text(summary))
    return summary


def _dispatch_pending(
    header: RunHeader,
    queries: tuple[DecisionQuery, ...],
    state: JournalState,
    journal: Path,
    provider_factory: Callable[[], Provider],
    secret: str | None,
    now: Callable[[], datetime],
) -> StopReason | None:
    pending = [query for query in queries if query.query_id not in state.dispatched]
    if not pending:
        _append(journal, CompleteEvent(at=now()))
        return None

    def stop(reason: StopReason, detail: str | None = None) -> StopReason:
        _append(journal, StopEvent(reason=reason, at=now(), detail=detail))
        return reason

    consecutive = 0
    provider = provider_factory()
    try:
        for query in pending:
            if state.attempts >= header.max_attempts:
                return stop("attempt_cap")
            spent = estimated_spend(header, list(state.results.values()))
            cap = header.max_estimated_usd
            if cap is not None and spent is not None and spent >= cap:
                return stop("spend_tripwire")
            attempt = state.attempts + 1
            _append(journal, DispatchEvent(attempt=attempt, query_id=query.query_id, at=now()))
            state.attempts = attempt
            state.dispatched[query.query_id] = attempt
            try:
                result = provider.understand(query.text, parse_normalized_query(query.text))
            except KeyboardInterrupt:
                stop("interrupted")
                raise
            except Exception as error:  # never its text: it may echo the request
                return stop("unexpected_exception", type(error).__name__)
            if not isinstance(result, DecisionResult) or result.jev is None:
                return stop("unexpected_exception", "no_jev_outcome")
            event = ResultEvent(
                attempt=attempt, query_id=query.query_id, at=now(), outcome=result.jev
            )
            if secret and secret in event.model_dump_json():
                return stop("secret_in_output")
            _append(journal, event)
            state.results[query.query_id] = result.jev
            transient = result.jev.fallback_reason in TRANSIENT_FALLBACKS
            consecutive = consecutive + 1 if transient else 0
            reason = _stop_for(result.jev, state, consecutive)
            if reason is not None:
                return stop(reason)
        _append(journal, CompleteEvent(at=now()))
        return None
    finally:
        provider.close()


def _summary_text(summary: RunSummary) -> str:
    # Counts only: never query text, labels or provider answers.
    status = "complete" if summary.complete else f"stopped ({summary.stopped})"
    if not summary.complete and summary.stopped is None:
        status = "not complete"
    spend = "n/a" if summary.estimated_usd is None else f"{summary.estimated_usd:.6f}"
    outcomes = ", ".join(f"{k}={v}" for k, v in summary.outcomes.items()) or "none"
    return (
        f"run {summary.run_id} ({summary.mode.value}): {status}; attempts "
        f"{summary.attempts}/{summary.max_attempts}; results {summary.results}; "
        f"unknown outcome {summary.unresolved}; not attempted {summary.not_attempted}; "
        f"outcomes: {outcomes}; estimated spend USD {spend} (estimate, not a billing cap)"
    )


# --- providers ----------------------------------------------------------------------------------


def dry_run_provider() -> Provider:
    """The real Jev provider over an in-memory transport that always answers with the same
    synthetic, valid `none`/`none` response (zero tokens). No network, no key, not Jev output."""
    import httpx2
    from pydantic import SecretStr

    from ecommerce_search.decision.jev_provider import JevConfig, JevDecisionProvider
    from evals.query_decisions.contracts import CATEGORY_LABELS, INTENT_LABELS, NONE_LABEL

    def answer(labels: tuple[str, ...]) -> dict[str, object]:
        rest = (1.0 - 0.6) / (len(labels) - 1)
        probabilities = {label: 0.6 if label == NONE_LABEL else rest for label in labels}
        return {
            "type": "choice",
            "choice": NONE_LABEL,
            "confidence": 0.5,
            "probabilities": probabilities,
        }

    body = {
        "model": DRY_RUN_MODEL,
        "answers": {"category": answer(CATEGORY_LABELS), "semantic_intent": answer(INTENT_LABELS)},
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }

    def reply(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=body, headers={"x-typesafe-request-id": "dry-run"})

    config = JevConfig(
        api_key=SecretStr("ts-dry-run-key-not-real"),  # noqa: S106 - never sent anywhere
        model=DRY_RUN_MODEL,
        base_url="https://api.typesafe.ai",
        attempt_timeout_seconds=1.25,
        max_retries=0,
        retry_budget_seconds=3.0,
        deadline_seconds=3.0,
        max_in_flight=1,
    )
    return JevDecisionProvider(config, transport=httpx2.MockTransport(reply))


def live_provider_factory(settings: object) -> tuple[Callable[[], Provider], str, str]:
    """(factory, model, key) for live mode, from validated `Settings`: retries 0, one call in
    flight; every other Jev setting as configured. Does not need `JEV_ENABLED` and changes no
    setting or file."""
    from ecommerce_search.config import Settings
    from ecommerce_search.decision.jev_provider import JevConfig, JevDecisionProvider

    if not isinstance(settings, Settings) or settings.typesafe_api_key is None:
        raise QueryDecisionError("live mode needs TYPESAFE_API_KEY in the environment or .env")
    if settings.jev_model != APPROVED_LIVE_MODEL:
        raise QueryDecisionError(f"live runs must use the approved model {APPROVED_LIVE_MODEL}")
    config = JevConfig(
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
        base_url=settings.jev_base_url,
        attempt_timeout_seconds=settings.jev_attempt_timeout_seconds,
        max_retries=0,
        retry_budget_seconds=settings.jev_retry_budget_seconds,
        deadline_seconds=settings.jev_deadline_seconds,
        max_in_flight=1,
    )
    return (
        lambda: JevDecisionProvider(config),
        config.model,
        config.api_key.get_secret_value(),
    )
