"""Resumable, capped Jev runner for the frozen M14 dev set (`le-run-1`; M12 patterns, unchanged).

**Building and dry-running this runner sends nothing anywhere.** A live run sends listing content
to a third party. That needs a separate owner send approval, recorded as `send_approval_ref`.
Upstream rights for the sources remain unverified and are not cleared by this code.

**Binding.** `runs/<run-id>/run.json` is written once. It fixes:
- the mode;
- the set version and digest;
- the `jl-1`, `lr-1` and M13 rules digests;
- the model;
- the frozen ordered item list;
- the caps, token allowances, prices and approval reference.

A later invocation with any different value is refused. The items are the set's dev items in
`sha256(set_sha256:item_id)` order, cut to `max_items`. The order is stored, so a resume keeps
it. There is no test-split option.

**Journal.** `journal.jsonl` is append-only, and each line is fsynced:
- `dispatch` is written before the provider is called;
- each HTTP `attempt` (call `A` or `B`) is written by `run_transport.JournalingTransport` before
  the request is sent, and its `attempt_result` (status, request id, usage) after;
- `result` holds the validated outcome. A listing therefore keeps the usage of call A even when
  it ends in a fallback.

Nothing is ever resent: a dispatched listing is never dispatched again, and a call is never
attempted twice for one listing.

**Caps and cost.**
- `max_attempts` caps actual HTTP attempts, failed ones included. A listing is not started
  unless every call it may make (1, or 2 with claims) fits under the cap.
- Spend is estimated from journalled usage and the recorded prices.
- Before each listing, a reservation of `planned_calls x (input allowance x input price + output
  allowance x output price)` must fit under `max_estimated_usd`. This is a **tripwire, not a
  billing cap**: no enforceable provider-side ceiling is known.
- A call whose usage exceeds an allowance is recorded and stops the run.
- An attempt with no usage (a failure, timeout, cancellation or crash) is **unaccounted cost**.
  It stops the run, and the run can never continue. `--resume-approved` does not override it.
  Known spend and unaccounted attempts are reported separately; no bound is invented for the
  latter.

**Other stops** (resumable only with `resume_approved`):
- `auth_error`, `model_mismatch`, `request_rejected`, `saturated` or `provider_closed`;
- `invalid_response` above 2 % once 20 results exist;
- an unexpected exception or an interruption.

`binding_mismatch`, `secret_in_output`, `allowance_exceeded` and `unknown_cost` are terminal.

**Dry run.** It uses the real provider over `run_transport.SyntheticTransport` (no socket, no
credentials), and its outcomes are exported as synthetic.
"""

import contextlib
import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ecommerce_search.decision.listing_models import (
    ListingDecisionMismatch,
    ListingDecisionOutcome,
    ListingDecisionState,
    validate_outcome,
)
from ecommerce_search.decision.listing_questions import build_listing_state, question_set_digest
from ecommerce_search.decision.listing_routing import policy_digest
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.rules import rules_digest
from evals.listing_decisions.contracts import EvalSet, EvalSetError, Split
from evals.listing_decisions.evaluate import OUTCOMES_SCHEMA, load_raw

RUN_SCHEMA = "le-run-1"
RUNS_DIR = "runs"
RUN_FILE = "run.json"
JOURNAL_FILE = "journal.jsonl"
LOCK_FILE = "run.lock"
RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
APPROVED_MODEL = "jev-1.13.0"
SMOKE_MAX_ITEMS = 5
MILLION = Decimal(1_000_000)
STOPPING_FALLBACKS = frozenset(
    {"auth_error", "model_mismatch", "request_rejected", "saturated", "provider_closed"}
)
INVALID_RESPONSE_MAX_RATE = Decimal("0.02")
INVALID_RESPONSE_MIN_RESULTS = 20
StopReason = Literal[
    "attempt_cap",
    "spend_tripwire",
    "unknown_cost",
    "allowance_exceeded",
    "auth_error",
    "model_mismatch",
    "request_rejected",
    "saturated",
    "provider_closed",
    "invalid_response_rate",
    "binding_mismatch",
    "secret_in_output",
    "unexpected_exception",
    "interrupted",
    "new_invalid_under_exception",
    "exception_expired",
    "first_invalid_response",
]
TERMINAL_OR_CAP = frozenset({"attempt_cap", "spend_tripwire", "unknown_cost", "allowance_exceeded"})
TERMINAL_STOPS = frozenset(
    {
        "unknown_cost",
        "allowance_exceeded",
        "secret_in_output",
        "binding_mismatch",
        "new_invalid_under_exception",  # neither plain resume nor a new exception bypasses it
        "first_invalid_response",  # the recorded stop-on-first-invalid option
    }
)


class RunnerError(EvalSetError):
    """The runner refuses to start, continue or export. The message never contains a key."""


class RunMode(StrEnum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pricing(_Model):
    """Recorded prices. Input must cost something; output may be free (0), as the provider
    documents for `jev-1.13.0`. Negative, NaN and infinite prices are refused."""

    input_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    checked_on: date
    source: str = Field(min_length=1)  # where the prices were read (page or invoice)


class Allowance(_Model):
    """Tokens reserved per HTTP call for the pre-dispatch tripwire."""

    input_tokens_per_call: int = Field(gt=0)
    output_tokens_per_call: int = Field(ge=0)


class ExcludedRun(_Model):
    """A run whose dispatched items (results, fallbacks and unknowns alike) this run excludes,
    bound to that run's journal as it was when the selection was derived."""

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dispatched: int = Field(ge=0)


class RunHeader(_Model):
    schema_version: Literal["le-run-1"] = RUN_SCHEMA
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    mode: RunMode
    smoke: bool = False
    split: Literal["dev"] = "dev"
    set_version: str
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_set_digest: str
    routing_policy_digest: str
    normalization_rules_digest: str
    model: str
    items: tuple[str, ...] = Field(min_length=1)
    max_attempts: int = Field(ge=1)
    allowance: Allowance | None = None
    pricing: Pricing | None = None
    max_estimated_usd: Decimal | None = Field(default=None, gt=0)
    send_approval_ref: str | None = None
    # Remaining-only completion runs (both default off, so older headers are unchanged).
    excluded_runs: tuple[ExcludedRun, ...] = ()
    stop_on_first_invalid: bool = False

    @model_validator(mode="after")
    def _valid(self) -> "RunHeader":
        if len(set(self.items)) != len(self.items):
            raise ValueError("items are unique")
        if self.max_attempts > 2 * len(self.items):
            raise ValueError("max_attempts is at most two HTTP calls per item")
        if self.smoke and len(self.items) > SMOKE_MAX_ITEMS:
            raise ValueError(f"a smoke run has at most {SMOKE_MAX_ITEMS} items")
        priced = (self.allowance, self.pricing, self.max_estimated_usd)
        if any(v is not None for v in priced) and None in priced:
            raise ValueError("allowance, pricing and max_estimated_usd are given together")
        if self.mode is RunMode.LIVE:
            if None in priced or not self.send_approval_ref:
                raise ValueError(
                    "a live run needs allowances, prices, a spend tripwire and a send approval"
                )
            if self.model != APPROVED_MODEL:
                raise ValueError(f"live runs use the approved model {APPROVED_MODEL}")
        elif self.send_approval_ref is not None:
            raise ValueError("a dry run records no send approval")
        return self

    def reservation_usd(self, planned_calls: int) -> Decimal | None:
        if self.allowance is None or self.pricing is None:
            return None
        per_call = (
            self.allowance.input_tokens_per_call * self.pricing.input_usd_per_million
            + self.allowance.output_tokens_per_call * self.pricing.output_usd_per_million
        ) / MILLION
        return planned_calls * per_call


# --- journal ------------------------------------------------------------------------------------


class DispatchEvent(_Model):
    event: Literal["dispatch"] = "dispatch"
    seq: int = Field(ge=1)
    item_id: str
    state_sha256: str
    planned_calls: int = Field(ge=1, le=2)


class AttemptEvent(_Model):
    event: Literal["attempt"] = "attempt"
    attempt: int = Field(ge=1)
    item_id: str
    call: Literal["A", "B"]


class AttemptResultEvent(_Model):
    event: Literal["attempt_result"] = "attempt_result"
    attempt: int = Field(ge=1)
    http_status: int | None = None
    request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    error_type: str | None = None  # an exception class name only, never its text

    @property
    def usage_known(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class ResultEvent(_Model):
    event: Literal["result"] = "result"
    seq: int = Field(ge=1)
    item_id: str
    outcome: ListingDecisionOutcome


RefusalReason = Literal[
    "attempt_cap", "spend_tripwire", "unknown_cost", "allowance_exceeded", "repeated_call",
    "no_dispatch",
]  # fmt: skip


class RefusedEvent(_Model):
    """An HTTP request the runner refused before sending it. Nothing was sent."""

    event: Literal["refused"] = "refused"
    item_id: str | None
    call: Literal["A", "B"]
    reason: RefusalReason


class StopEvent(_Model):
    event: Literal["stop"] = "stop"
    reason: StopReason


class ResumeApprovedEvent(_Model):
    event: Literal["resume_approved"] = "resume_approved"


class CompleteEvent(_Model):
    event: Literal["complete"] = "complete"


class ContinuationExceptionEvent(_Model):
    """An owner-approved manual exception to the invalid-response rate stop. It is not a change
    to the quality criterion. While active, the run does not stop solely because the
    acknowledged invalid responses keep the cumulative rate above the threshold, and it stops
    on the next new invalid response, whatever the rate. Any later stop, completion or
    restart ends it; it is never renewed or reused."""

    event: Literal["continuation_exception"] = "continuation_exception"
    approval_ref: str = Field(min_length=1)
    acknowledged_stop: Literal["invalid_response_rate"] = "invalid_response_rate"
    acknowledged_results: int = Field(ge=0)
    acknowledged_invalid: int = Field(ge=0)


JournalEvent = Annotated[
    DispatchEvent
    | AttemptEvent
    | AttemptResultEvent
    | RefusedEvent
    | ResultEvent
    | StopEvent
    | ResumeApprovedEvent
    | ContinuationExceptionEvent
    | CompleteEvent,
    Field(discriminator="event"),
]
_EVENT: TypeAdapter[Any] = TypeAdapter(JournalEvent)


@dataclass
class JournalState:
    dispatched: dict[str, DispatchEvent] = field(default_factory=dict)
    results: dict[str, ListingDecisionOutcome] = field(default_factory=dict)
    attempts: dict[int, AttemptEvent] = field(default_factory=dict)
    attempt_results: dict[int, AttemptResultEvent] = field(default_factory=dict)
    refusals: list[RefusedEvent] = field(default_factory=list)
    stop: str | None = None
    acknowledged_unknown: set[str] = field(default_factory=set)
    complete: bool = False
    exceptions: list[ContinuationExceptionEvent] = field(default_factory=list)
    active_exception: ContinuationExceptionEvent | None = None
    threshold_ever_breached: bool = False

    @property
    def invalid_responses(self) -> int:
        return sum(1 for o in self.results.values() if o.fallback_reason == "invalid_response")

    @property
    def threshold_breached(self) -> bool:
        """The original rule on the current cumulative counts (never reset)."""
        n = len(self.results)
        return (
            n >= INVALID_RESPONSE_MIN_RESULTS
            and Decimal(self.invalid_responses) / Decimal(n) > INVALID_RESPONSE_MAX_RATE
        )

    def record_result(self, item_id: str, outcome: ListingDecisionOutcome) -> None:
        self.results[item_id] = outcome
        self.threshold_ever_breached |= self.threshold_breached

    def record_stop(self, reason: str) -> None:
        self.stop = reason
        self.active_exception = None  # any stop ends an exception: never reused

    @property
    def unknown_items(self) -> list[str]:
        return [i for i in self.dispatched if i not in self.results]

    @property
    def unaccounted_attempts(self) -> list[int]:
        return [
            a
            for a in self.attempts
            if a not in self.attempt_results or not self.attempt_results[a].usage_known
        ]

    def known_tokens(self) -> tuple[int, int]:
        known = [r for r in self.attempt_results.values() if r.usage_known]
        return sum(r.input_tokens or 0 for r in known), sum(r.output_tokens or 0 for r in known)

    def known_spend(self, pricing: Pricing | None) -> Decimal | None:
        if pricing is None:
            return None
        tokens_in, tokens_out = self.known_tokens()
        return (
            tokens_in * pricing.input_usd_per_million + tokens_out * pricing.output_usd_per_million
        ) / MILLION


def read_journal(path: Path) -> JournalState:
    state = JournalState()
    if not path.is_file():
        return state
    text = path.read_bytes().decode("utf-8")
    if text and not text.endswith("\n"):
        raise RunnerError("the journal's last line is incomplete; inspect it by hand")
    last_item: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            event = _EVENT.validate_json(line)
        except ValidationError:
            raise RunnerError(f"journal line {number} is invalid") from None
        if isinstance(event, DispatchEvent):
            if event.item_id in state.dispatched or event.seq != len(state.dispatched) + 1:
                raise RunnerError("journal dispatches are not unique and sequential")
            state.dispatched[event.item_id] = event
            last_item = event.item_id
        elif isinstance(event, AttemptEvent):
            same_call = any(
                a.item_id == event.item_id and a.call == event.call for a in state.attempts.values()
            )
            if event.attempt != len(state.attempts) + 1 or event.item_id != last_item or same_call:
                raise RunnerError("journal attempts are not sequential, current and unique")
            state.attempts[event.attempt] = event
        elif isinstance(event, AttemptResultEvent):
            if event.attempt not in state.attempts or event.attempt in state.attempt_results:
                raise RunnerError("journal attempt result without its attempt")
            state.attempt_results[event.attempt] = event
        elif isinstance(event, RefusedEvent):
            state.refusals.append(event)
        elif isinstance(event, ResultEvent):
            if event.item_id not in state.dispatched or event.item_id in state.results:
                raise RunnerError("journal result without its dispatch")
            state.record_result(event.item_id, event.outcome)
        elif isinstance(event, StopEvent):
            state.record_stop(event.reason)
        elif isinstance(event, ResumeApprovedEvent):
            state.stop = None
            state.acknowledged_unknown.update(state.unknown_items)
        elif isinstance(event, ContinuationExceptionEvent):
            state.exceptions.append(event)
            state.active_exception = event
        else:
            state.complete = True
            state.active_exception = None
    return state


class Journal:
    """Thread-safe, fsynced appends. Attempt hooks run on the provider's loop thread."""

    def __init__(
        self, path: Path, header: RunHeader, state: JournalState, secret: str | None = None
    ) -> None:
        self.path = path
        self.header = header
        self.state = state
        self._secret = secret
        self.current_item: str | None = None
        self._lock = threading.Lock()

    def append(self, event: BaseModel) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(event.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._apply(event)

    def _apply(self, event: BaseModel) -> None:
        s = self.state
        if isinstance(event, DispatchEvent):
            s.dispatched[event.item_id] = event
        elif isinstance(event, AttemptEvent):
            s.attempts[event.attempt] = event
        elif isinstance(event, AttemptResultEvent):
            s.attempt_results[event.attempt] = event
        elif isinstance(event, RefusedEvent):
            s.refusals.append(event)
        elif isinstance(event, ResultEvent):
            s.record_result(event.item_id, event.outcome)
        elif isinstance(event, StopEvent):
            s.record_stop(event.reason)
        elif isinstance(event, ResumeApprovedEvent):
            s.stop = None
            s.acknowledged_unknown.update(s.unknown_items)
        elif isinstance(event, ContinuationExceptionEvent):
            s.exceptions.append(event)
            s.active_exception = event
        elif isinstance(event, CompleteEvent):
            s.complete = True
            s.active_exception = None

    # AttemptHooks
    def before(self, call: str) -> int:
        """Check this one request before it is sent: an open dispatch, no repeat, the attempt
        cap, the spend reservation for one call and, for call B, call A's usage known and
        within its allowances. A refusal is journalled and nothing is sent."""
        with self._lock:
            item = self.current_item
            reason = self._refusal(item, call)
            attempt = len(self.state.attempts) + 1
        if reason is not None:
            self.append(RefusedEvent(item_id=item, call=call, reason=reason))  # type: ignore[arg-type]
            raise AttemptRefused(reason)
        self.append(AttemptEvent(attempt=attempt, item_id=item, call=call))  # type: ignore[arg-type]
        return attempt

    def _refusal(self, item: str | None, call: str) -> str | None:
        s, header = self.state, self.header
        if item is None:
            return "no_dispatch"
        mine = [(n, a) for n, a in s.attempts.items() if a.item_id == item]
        if any(a.call == call for _, a in mine):
            return "repeated_call"
        if len(s.attempts) >= header.max_attempts:
            return "attempt_cap"
        if call == "B":
            first = next((n for n, a in mine if a.call == "A"), None)
            result = s.attempt_results.get(first) if first is not None else None
            if result is None or not result.usage_known:
                return "unknown_cost"
            allowance = header.allowance
            if allowance is not None and (
                (result.input_tokens or 0) > allowance.input_tokens_per_call
                or (result.output_tokens or 0) > allowance.output_tokens_per_call
            ):
                return "allowance_exceeded"
        if s.unaccounted_attempts:
            return "unknown_cost"
        reservation, spent = header.reservation_usd(1), s.known_spend(header.pricing)
        if (
            reservation is not None
            and spent is not None
            and header.max_estimated_usd is not None
            and spent + reservation > header.max_estimated_usd
        ):
            return "spend_tripwire"
        return None

    def after(
        self,
        attempt: int,
        status: int | None,
        usage: tuple[int, int] | None,
        request_id: str | None,
        error_type: str | None,
    ) -> None:
        if request_id is not None and self._secret and self._secret in request_id:
            request_id = "[redacted]"
        self.append(
            AttemptResultEvent(
                attempt=attempt,
                http_status=status,
                request_id=request_id,
                input_tokens=None if usage is None else usage[0],
                output_tokens=None if usage is None else usage[1],
                error_type=error_type,
            )
        )


class AttemptRefused(Exception):
    """The runner refused an HTTP request before sending it; the message is the reason."""


# --- providers ----------------------------------------------------------------------------------


class Provider(Protocol):
    def decide(self, state: ListingDecisionState) -> ListingDecisionOutcome: ...

    def close(self) -> None: ...


ProviderFactory = Callable[[Journal], Provider]


def dry_run_factory(*, input_tokens: int = 200, output_tokens: int = 1) -> ProviderFactory:
    """The real provider over `SyntheticTransport`: no socket, no settings, no credentials."""

    def build(journal: Journal) -> Provider:
        from pydantic import SecretStr

        from ecommerce_search.decision.jev_listing_provider import JevListingDecisionProvider
        from ecommerce_search.decision.jev_provider import JevConfig
        from evals.listing_decisions.run_transport import (
            DRY_RUN_MODEL,
            JournalingTransport,
            SyntheticTransport,
        )

        config = JevConfig(
            api_key=SecretStr("ts-dry-run-key-not-real"),  # noqa: S106 - never sent anywhere
            model=DRY_RUN_MODEL,
            base_url="https://dry-run.invalid",
            attempt_timeout_seconds=1.25,
            max_retries=0,
            retry_budget_seconds=3.0,
            deadline_seconds=3.0,
            max_in_flight=1,
        )
        inner = SyntheticTransport(input_tokens, output_tokens)
        return JevListingDecisionProvider(config, transport=JournalingTransport(inner, journal))

    return build


def live_factory(settings: Any, *, inner: Any = None) -> tuple[ProviderFactory, str]:
    """(factory, key) for a live run from validated `Settings`: SDK retries 0, one call in
    flight, every request through `JournalingTransport`. `inner` replaces the real HTTP
    transport in mocked tests only."""
    from ecommerce_search.config import Settings
    from ecommerce_search.decision.jev_provider import JevConfig

    if not isinstance(settings, Settings) or settings.typesafe_api_key is None:
        raise RunnerError("a live run needs TYPESAFE_API_KEY in the environment or .env")
    if settings.jev_model != APPROVED_MODEL:
        raise RunnerError(f"live runs must use the approved model {APPROVED_MODEL}")
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

    def build(journal: Journal) -> Provider:
        import httpx2

        from ecommerce_search.decision.jev_listing_provider import JevListingDecisionProvider
        from evals.listing_decisions.run_transport import JournalingTransport

        transport = JournalingTransport(inner or httpx2.AsyncHTTPTransport(), journal)
        return JevListingDecisionProvider(config, transport=transport)

    return build, config.api_key.get_secret_value()


# --- runs ---------------------------------------------------------------------------------------


def frozen_items(eval_set: EvalSet, max_items: int) -> tuple[str, ...]:
    dev = [i.item_id for i in eval_set.items if i.split is Split.DEV]
    dev.sort(key=lambda i: hashlib.sha256(f"{eval_set.set_sha256}:{i}".encode()).hexdigest())
    return tuple(dev[:max_items])


def make_header(
    eval_set: EvalSet,
    *,
    run_id: str,
    mode: RunMode,
    max_items: int,
    max_attempts: int | None = None,
    smoke: bool = False,
    model: str = APPROVED_MODEL,
    allowance: Allowance | None = None,
    pricing: Pricing | None = None,
    max_estimated_usd: Decimal | None = None,
    send_approval_ref: str | None = None,
    excluded_runs: tuple[ExcludedRun, ...] = (),
    excluded_items: frozenset[str] = frozenset(),
    stop_on_first_invalid: bool = False,
) -> RunHeader:
    dev = frozen_items(eval_set, len(eval_set.items))
    items = tuple(i for i in dev if i not in excluded_items)[:max_items]
    try:
        return RunHeader(
            run_id=run_id,
            mode=mode,
            smoke=smoke,
            set_version=eval_set.set_version,
            set_sha256=eval_set.set_sha256,
            question_set_digest=question_set_digest(),
            routing_policy_digest=policy_digest(),
            normalization_rules_digest=rules_digest(),
            model=model,
            items=items,
            max_attempts=2 * len(items) if max_attempts is None else max_attempts,
            allowance=allowance,
            pricing=pricing,
            max_estimated_usd=max_estimated_usd,
            send_approval_ref=send_approval_ref,
            excluded_runs=excluded_runs,
            stop_on_first_invalid=stop_on_first_invalid,
        )
    except ValidationError as exc:
        raise RunnerError(f"invalid run header: {exc.errors()[0]['msg']}") from None


def exclusions(
    work_dir: Path, eval_set: EvalSet, run_ids: tuple[str, ...]
) -> tuple[tuple[ExcludedRun, ...], frozenset[str]]:
    """Read (never write) each excluded run: its journal hash and every item it dispatched,
    whatever the outcome. The run must be bound to this set and not be in progress."""
    bound, items = [], set()
    for run_id in run_ids:
        state, digest = _excluded_state(work_dir, eval_set, run_id)
        bound.append(
            ExcludedRun(run_id=run_id, journal_sha256=digest, dispatched=len(state.dispatched))
        )
        items |= set(state.dispatched)
    return tuple(bound), frozenset(items)


def _excluded_state(work_dir: Path, eval_set: EvalSet, run_id: str) -> tuple[JournalState, str]:
    run_dir = work_dir / RUNS_DIR / run_id
    if (run_dir / LOCK_FILE).exists():
        raise RunnerError(f"excluded run {run_id} is in progress (locked)")
    try:
        header = RunHeader.model_validate_json((run_dir / RUN_FILE).read_bytes())
        data = (run_dir / JOURNAL_FILE).read_bytes()
    except (OSError, ValidationError) as exc:
        raise RunnerError(f"cannot read excluded run {run_id} ({type(exc).__name__})") from None
    if (header.set_version, header.set_sha256) != (eval_set.set_version, eval_set.set_sha256):
        raise RunnerError(f"excluded run {run_id} is bound to another set")
    return read_journal(run_dir / JOURNAL_FILE), hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def _lock(run_dir: Path) -> Iterator[None]:
    path = run_dir / LOCK_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RunnerError("another invocation holds this run's lock") from None
    os.close(fd)
    try:
        yield
    finally:
        path.unlink()


def _bind(run_dir: Path, header: RunHeader) -> None:
    path = run_dir / RUN_FILE
    if path.exists():
        recorded = RunHeader.model_validate_json(path.read_bytes())
        if recorded != header:
            raise RunnerError("run.json differs from this invocation; parameters are frozen")
        return
    with open(path, "xb") as handle:
        handle.write(header.model_dump_json().encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class RunSummary:
    dispatched: int
    results: int
    unknown_items: int
    attempts: int
    unaccounted_attempts: int
    known_input_tokens: int
    known_output_tokens: int
    known_spend_usd: Decimal | None
    stop: str | None
    complete: bool
    # Quality is reported, never implied: completion is not acceptance.
    invalid_responses: int = 0
    invalid_rate: str | None = None
    threshold_breached: bool = False
    threshold_ever_breached: bool = False
    continuation_exceptions: tuple[dict, ...] = ()
    active_exception: str | None = None


def summarize(header: RunHeader, state: JournalState) -> RunSummary:
    tokens_in, tokens_out = state.known_tokens()
    return RunSummary(
        dispatched=len(state.dispatched),
        results=len(state.results),
        unknown_items=len(state.unknown_items),
        attempts=len(state.attempts),
        unaccounted_attempts=len(state.unaccounted_attempts),
        known_input_tokens=tokens_in,
        known_output_tokens=tokens_out,
        known_spend_usd=state.known_spend(header.pricing),
        stop=state.stop,
        complete=state.complete,
        invalid_responses=state.invalid_responses,
        invalid_rate=(
            str(Decimal(state.invalid_responses) / Decimal(len(state.results)))
            if state.results
            else None
        ),
        threshold_breached=state.threshold_breached,
        threshold_ever_breached=state.threshold_ever_breached,
        continuation_exceptions=tuple(e.model_dump() for e in state.exceptions),
        active_exception=(
            None if state.active_exception is None else state.active_exception.approval_ref
        ),
    )


def _refusal(state: JournalState, resume_approved: bool) -> str | None:
    if state.unaccounted_attempts:
        return (
            f"unresolved unknown cost: {len(state.unaccounted_attempts)} attempt(s) have no "
            "recorded usage; this run cannot continue (resume approval does not override this)"
        )
    if state.stop in TERMINAL_STOPS:
        return f"the run stopped terminally ({state.stop})"
    unacknowledged = set(state.unknown_items) - state.acknowledged_unknown
    if (state.stop is not None or unacknowledged) and not resume_approved:
        return "the run is blocked; review the journal and pass resume_approved to continue"
    return None


@dataclass(frozen=True)
class ContinuationRequest:
    """What the operator acknowledges: the approval reference and the exact current counts."""

    approval_ref: str
    acknowledged_results: int
    acknowledged_invalid: int


def _exception_refusal(state: JournalState, request: ContinuationRequest) -> str | None:
    if not request.approval_ref.strip():
        return "a continuation exception needs an approval reference"
    if state.stop != "invalid_response_rate":
        return (
            f"a continuation exception applies only to an invalid_response_rate stop "
            f"(latest stop: {state.stop})"
        )
    if state.unknown_items or state.unaccounted_attempts:
        return "a continuation exception needs no unknown items and no unaccounted attempts"
    if (len(state.results), state.invalid_responses) != (
        request.acknowledged_results,
        request.acknowledged_invalid,
    ):
        return (
            f"stale acknowledgement: the journal has {len(state.results)} results and "
            f"{state.invalid_responses} invalid responses"
        )
    return None


def _state_for(item_id: str, eval_set: EvalSet, raw_dir: Path) -> ListingDecisionState:
    raw = load_raw(raw_dir, eval_set.item(item_id))
    return build_listing_state(raw, normalize_listing(raw))


def _post_stop(
    header: RunHeader, state: JournalState, item_id: str, outcome: ListingDecisionOutcome
) -> str | None:
    mine = [n for n, a in state.attempts.items() if a.item_id == item_id]
    if any(n in state.unaccounted_attempts for n in mine):
        return "unknown_cost"
    allowance = header.allowance
    if allowance is not None:
        for n in mine:
            r = state.attempt_results[n]
            if (r.input_tokens or 0) > allowance.input_tokens_per_call or (
                r.output_tokens or 0
            ) > allowance.output_tokens_per_call:
                return "allowance_exceeded"
    if outcome.fallback_reason in STOPPING_FALLBACKS:
        return outcome.fallback_reason
    if header.stop_on_first_invalid and outcome.fallback_reason == "invalid_response":
        return "first_invalid_response"  # recorded option: no 20-result minimum applies
    exception = state.active_exception
    if exception is not None and state.invalid_responses > exception.acknowledged_invalid:
        return "new_invalid_under_exception"  # the next new invalid response, at any rate
    if state.threshold_breached:
        if exception is not None and state.invalid_responses == exception.acknowledged_invalid:
            return None  # only acknowledged failures keep the rate high (still reported)
        return "invalid_response_rate"
    return None


def run(
    work_dir: Path,
    eval_set: EvalSet,
    header: RunHeader,
    factory: ProviderFactory,
    *,
    resume_approved: bool = False,
    secret: str | None = None,
    exception: ContinuationRequest | None = None,
) -> RunSummary:
    """Dispatch the run's remaining items once each, stopping on the first stop condition."""
    if exception is not None and not resume_approved:
        raise RunnerError("a continuation exception also needs resume_approved")
    if eval_set.set_sha256 != header.set_sha256 or eval_set.set_version != header.set_version:
        raise RunnerError("the set does not match the run header")
    for item_id in header.items:
        if eval_set.item(item_id).split is not Split.DEV:
            raise RunnerError("runs are dev-only")
    for excluded in header.excluded_runs:  # the source journal must be exactly as bound
        state, digest = _excluded_state(work_dir, eval_set, excluded.run_id)
        if digest != excluded.journal_sha256:
            raise RunnerError(f"excluded run {excluded.run_id}'s journal changed since selection")
        if set(header.items) & set(state.dispatched):
            raise RunnerError(f"an item was already dispatched in {excluded.run_id}")
    run_dir = work_dir / RUNS_DIR / header.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _lock(run_dir):
        _bind(run_dir, header)
        journal_path = run_dir / JOURNAL_FILE
        state = read_journal(journal_path)
        journal = Journal(journal_path, header, state, secret)
        # An exception left active by an invocation that did not stop is never inherited. It is
        # expired in memory first; every check below runs before anything is written, and the
        # expiry is journalled only if this invocation is allowed to proceed.
        expired = state.active_exception is not None
        if expired:
            state.record_stop("exception_expired")
        refusal = _refusal(state, resume_approved)
        if refusal is None and exception is not None:
            refusal = _exception_refusal(state, exception)
        if refusal is not None:
            raise RunnerError(refusal)
        if expired:
            journal.append(StopEvent(reason="exception_expired"))
        if resume_approved and (state.stop is not None or state.unknown_items):
            journal.append(ResumeApprovedEvent())
        if exception is not None:  # fsynced before any new dispatch
            journal.append(
                ContinuationExceptionEvent(
                    approval_ref=exception.approval_ref,
                    acknowledged_results=exception.acknowledged_results,
                    acknowledged_invalid=exception.acknowledged_invalid,
                )
            )
        if state.complete:
            return summarize(header, state)
        provider = factory(journal)
        try:
            _dispatch_all(work_dir, eval_set, header, journal, provider, secret)
        finally:
            provider.close()
        return summarize(header, journal.state)


def _dispatch_all(
    work_dir: Path,
    eval_set: EvalSet,
    header: RunHeader,
    journal: Journal,
    provider: Provider,
    secret: str | None,
) -> None:
    state = journal.state
    for item_id in header.items:
        if item_id in state.dispatched:
            continue  # never resent, whatever its outcome
        listing = _state_for(item_id, eval_set, work_dir / "raw")
        planned = 2 if listing.attribute_questions else 1
        if len(state.attempts) + planned > header.max_attempts:
            journal.append(StopEvent(reason="attempt_cap"))
            return
        reservation = header.reservation_usd(planned)
        spent = state.known_spend(header.pricing)
        if (
            reservation is not None
            and spent is not None
            and header.max_estimated_usd is not None
            and spent + reservation > header.max_estimated_usd
        ):
            journal.append(StopEvent(reason="spend_tripwire"))
            return
        journal.append(
            DispatchEvent(
                seq=len(state.dispatched) + 1,
                item_id=item_id,
                state_sha256=listing.state_sha256(),
                planned_calls=planned,
            )
        )
        journal.current_item = item_id
        try:
            outcome = provider.decide(listing)
        except KeyboardInterrupt:
            journal.append(StopEvent(reason="interrupted"))
            raise
        except Exception:
            journal.append(StopEvent(reason="unexpected_exception"))
            raise
        finally:
            journal.current_item = None
        try:
            validate_outcome(listing, outcome)
        except ListingDecisionMismatch:
            journal.append(StopEvent(reason="binding_mismatch"))
            return
        result = ResultEvent(seq=state.dispatched[item_id].seq, item_id=item_id, outcome=outcome)
        if secret and secret in result.model_dump_json():
            journal.append(StopEvent(reason="secret_in_output"))  # the result is never written
            return
        journal.append(result)
        refused = [r for r in state.refusals if r.item_id == item_id]
        if refused:  # a refused request ends the run with its stable reason
            reason = refused[0].reason
            stop_reason = reason if reason in TERMINAL_OR_CAP else "unexpected_exception"
            journal.append(StopEvent(reason=stop_reason))  # type: ignore[arg-type]
            return
        stop = _post_stop(header, state, item_id, outcome)
        if stop is not None:
            journal.append(StopEvent(reason=stop))  # type: ignore[arg-type]
            return
    journal.append(CompleteEvent())


# --- export ---------------------------------------------------------------------------------------


def export_outcomes(work_dir: Path, run_id: str, out: Path) -> dict[str, int]:
    """Write `le-outcomes-1` from journalled results only (fallbacks included). Unknown items are
    left out and counted. Dry-run outcomes are marked synthetic."""
    import json

    run_dir = work_dir / RUNS_DIR / run_id
    try:
        header = RunHeader.model_validate_json((run_dir / RUN_FILE).read_bytes())
    except (OSError, ValidationError) as exc:
        raise RunnerError(f"cannot read the run header ({type(exc).__name__})") from None
    state = read_journal(run_dir / JOURNAL_FILE)
    lines = [
        json.dumps(
            {
                "schema_version": OUTCOMES_SCHEMA,
                "set_sha256": header.set_sha256,
                "provider": "jev",
                "question_set_digest": header.question_set_digest,
                "synthetic": header.mode is RunMode.DRY_RUN,
            },
            sort_keys=True,
        )
    ]
    for item_id in sorted(state.results):
        record = {"item_id": item_id, "outcome": state.results[item_id].model_dump(mode="json")}
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
    with open(out, "xb") as handle:
        handle.write(("\n".join(lines) + "\n").encode("utf-8"))
    return {
        "exported": len(state.results),
        "unknown_items_left_out": len(state.unknown_items),
        "not_attempted": len(header.items) - len(state.dispatched),
    }
