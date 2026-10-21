"""M14 listing runner (`le-run-1`): binding, per-attempt journal, caps, tripwire, unknown cost,
no resend, resume and export. Dry-run and mocked transports only: no network, no credentials."""

import asyncio
import json
import socket
from decimal import Decimal

import httpx2
import pytest
from evals.listing_decisions import runner as rn
from evals.listing_decisions.contracts import load_set
from evals.listing_decisions.evaluate import evaluate
from evals.listing_decisions.run_transport import JournalingTransport, SyntheticTransport
from listing_eval_support import write_fixture
from pydantic import SecretStr

from ecommerce_search.decision.jev_listing_provider import JevListingDecisionProvider
from ecommerce_search.decision.jev_provider import JevConfig
from ecommerce_search.decision.listing_models import ListingDecisionOutcome

_LOOPBACK = {"127.0.0.1", "::1"}
FAKE_KEY = "ts-live-key-123"  # noqa: S105 - a fake key for the secret check, never sent


@pytest.fixture(autouse=True)
def loopback_only(no_network, monkeypatch):
    """The provider's event loop needs a loopback self-pipe on Windows; nothing else may connect.
    Name resolution and outbound connections stay refused (package conftest)."""
    import _socket

    real = _socket.socket.connect  # the C method; socket.socket.connect is already refused

    def guarded(self, address):
        if not (isinstance(address, tuple) and address[0] in _LOOPBACK):
            raise RuntimeError("network access is not allowed in runner tests")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def work(tmp_path):
    set_path, _ = write_fixture(tmp_path / "set")
    return tmp_path / "set", load_set(set_path)


def _header(eval_set, **kw) -> rn.RunHeader:
    values = {"run_id": "r1", "mode": rn.RunMode.DRY_RUN, "max_items": 12} | kw
    return rn.make_header(eval_set, **values)


def _journal(work_dir, run_id="r1") -> list[dict]:
    path = work_dir / rn.RUNS_DIR / run_id / rn.JOURNAL_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _events(work_dir, run_id="r1") -> list[str]:
    return [e["event"] for e in _journal(work_dir, run_id)]


def _priced(max_usd: str = "1", input_allowance: int = 1000, output_allowance: int = 10) -> dict:
    return {
        "allowance": rn.Allowance(
            input_tokens_per_call=input_allowance, output_tokens_per_call=output_allowance
        ),
        "pricing": rn.Pricing(
            input_usd_per_million=Decimal("2"),
            output_usd_per_million=Decimal("8"),
            checked_on="2026-10-21",
            source="synthetic test price",
        ),
        "max_estimated_usd": Decimal(max_usd),
    }


def _mock_factory(handler, *, deadline: float = 3.0, model: str = "jev-1.13.0"):
    """The real provider over a scripted inner transport (mocked tests only)."""

    def build(journal):
        config = JevConfig(
            api_key=SecretStr("ts-test-key-not-real"),
            model=model,
            base_url="https://mock.invalid",
            attempt_timeout_seconds=5.0,
            max_retries=0,
            retry_budget_seconds=5.0,
            deadline_seconds=deadline,
            max_in_flight=1,
        )
        inner = httpx2.MockTransport(handler)
        return JevListingDecisionProvider(config, transport=JournalingTransport(inner, journal))

    return build


def _synthetic_reply(request: httpx2.Request, *, model="jev-1.13.0", usage=(200, 1)):
    body = json.loads(request.content)
    answers = {
        qid: {
            "type": "choice",
            "choice": list(q["criteria"])[0],
            "confidence": 0.5,
            "probabilities": {k: (1.0 if i == 0 else 0.0) for i, k in enumerate(q["criteria"])},
        }
        for qid, q in body["questions"].items()
    }
    payload = {"model": model, "answers": answers}
    if usage is not None:
        payload["usage"] = {"input_tokens": usage[0], "output_tokens": usage[1]}
    return httpx2.Response(200, json=payload, headers={"x-typesafe-request-id": "mock"})


# --- dry run, journal and export ----------------------------------------------------------------


def test_a_dry_run_journals_every_attempt_before_its_result_and_completes(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    assert summary.complete and summary.stop is None
    assert (summary.dispatched, summary.results, summary.unknown_items) == (12, 12, 0)
    assert summary.unaccounted_attempts == 0 and summary.known_spend_usd is None
    events = _journal(work_dir)
    calls = [e for e in events if e["event"] == "attempt"]
    assert summary.attempts == len(calls) <= header.max_attempts
    assert summary.known_input_tokens == 200 * len(calls)
    for i, event in enumerate(events):  # each attempt is followed by its result, then the item
        if event["event"] == "attempt":
            assert events[i + 1] == events[i + 1] | {"event": "attempt_result"}
            assert events[i + 1]["attempt"] == event["attempt"]
    dispatched = [e["item_id"] for e in events if e["event"] == "dispatch"]
    assert dispatched == list(header.items)  # the frozen order
    assert events[-1]["event"] == "complete"


def test_dry_runs_load_no_settings_or_credentials_and_open_no_socket(work, monkeypatch) -> None:
    import ecommerce_search.config as config

    work_dir, eval_set = work
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-sentinel-secret-must-not-appear")

    def forbidden(*args, **kwargs):
        raise AssertionError("a dry run must not load settings")

    monkeypatch.setattr(config, "get_settings", forbidden)
    seen = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: seen.append(a) or forbidden())
    rn.run(work_dir, eval_set, _header(eval_set), rn.dry_run_factory())
    run_dir = work_dir / rn.RUNS_DIR / "r1"
    assert seen == []
    for path in run_dir.iterdir():
        if path.is_file():
            assert b"ts-sentinel-secret" not in path.read_bytes()
    assert issubclass(SyntheticTransport, httpx2.AsyncBaseTransport)
    assert not hasattr(SyntheticTransport(), "_inner")  # it has nothing to delegate to


def test_the_export_is_synthetic_and_accepted_by_the_evaluator(work, tmp_path) -> None:
    work_dir, eval_set = work
    rn.run(work_dir, eval_set, _header(eval_set), rn.dry_run_factory())
    out = tmp_path / "outcomes.jsonl"
    counts = rn.export_outcomes(work_dir, "r1", out)
    assert counts == {"exported": 12, "unknown_items_left_out": 0, "not_attempted": 0}
    assert json.loads(out.read_text(encoding="utf-8").splitlines()[0])["synthetic"] is True
    report, failed = evaluate(eval_set, work_dir / "raw", out)
    assert report["synthetic"] is True and report["outcomes"]["records"] == 12
    assert report["label_metrics"]["category"]["jev"]["pooled"]["attempted"] == 7
    assert failed == []
    with pytest.raises(FileExistsError):
        rn.export_outcomes(work_dir, "r1", out)


def test_a_completed_run_never_dispatches_again(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    before = _journal(work_dir)
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    assert summary.complete and _journal(work_dir) == before


# --- binding and header -------------------------------------------------------------------------


def test_run_parameters_are_frozen_and_the_lock_is_exclusive(work) -> None:
    work_dir, eval_set = work
    rn.run(work_dir, eval_set, _header(eval_set, max_items=3), rn.dry_run_factory())
    with pytest.raises(rn.RunnerError, match="parameters are frozen"):
        rn.run(work_dir, eval_set, _header(eval_set, max_items=4), rn.dry_run_factory())
    (work_dir / rn.RUNS_DIR / "r1" / rn.LOCK_FILE).write_text("", encoding="utf-8")
    with pytest.raises(rn.RunnerError, match="lock"):
        rn.run(work_dir, eval_set, _header(eval_set, max_items=3), rn.dry_run_factory())


def test_runs_are_bound_to_the_set_and_are_dev_only(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    assert header.set_sha256 == eval_set.set_sha256 and header.split == "dev"
    assert all(eval_set.item(i).split.value == "dev" for i in header.items)
    assert "lp-2" not in header.items  # the only test-split item
    with pytest.raises(rn.RunnerError, match="dev-only"):
        rn.run(work_dir, eval_set, header.model_copy(update={"items": ("lp-2",)}), None)
    other = header.model_copy(update={"set_sha256": "0" * 64})
    with pytest.raises(rn.RunnerError, match="does not match"):
        rn.run(work_dir, eval_set, other, rn.dry_run_factory())


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"mode": rn.RunMode.LIVE}, "live run needs"),
        ({"mode": rn.RunMode.LIVE, "send_approval_ref": "x"}, "live run needs"),
        (
            {"mode": rn.RunMode.LIVE, "send_approval_ref": "x", "model": "jev-2"} | _priced(),
            "approved model",
        ),
        ({"send_approval_ref": "owner-ok"}, "dry run records no send approval"),
        ({"smoke": True, "max_items": 6}, "smoke run has at most 5"),
        ({"max_items": 2, "max_attempts": 5}, "at most two HTTP calls"),
        (
            {"allowance": rn.Allowance(input_tokens_per_call=1, output_tokens_per_call=0)},
            "together",
        ),
    ],
)
def test_invalid_run_headers_are_refused(work, kw, message) -> None:
    _, eval_set = work
    with pytest.raises(rn.RunnerError, match=message):
        _header(eval_set, **kw)


def test_a_live_header_records_prices_allowances_and_the_send_approval(work) -> None:
    _, eval_set = work
    header = _header(
        eval_set, mode=rn.RunMode.LIVE, smoke=True, max_items=5, send_approval_ref="owner-x",
        **_priced(),
    )  # fmt: skip
    assert header.smoke and len(header.items) == 5 and header.max_attempts == 10
    assert header.reservation_usd(2) == 2 * (1000 * Decimal(2) + 10 * Decimal(8)) / Decimal(1e6)


def test_live_factory_refuses_without_a_key_or_with_another_model() -> None:
    with pytest.raises(rn.RunnerError, match="TYPESAFE_API_KEY"):
        rn.live_factory(object())


# --- caps, tripwire, allowance ------------------------------------------------------------------


def test_the_attempt_cap_counts_http_attempts_and_stops_before_overflowing(work) -> None:
    work_dir, eval_set = work
    summary = rn.run(work_dir, eval_set, _header(eval_set, max_attempts=3), rn.dry_run_factory())
    assert summary.stop == "attempt_cap" and summary.attempts <= 3
    assert summary.unknown_items == 0  # a listing that might overflow the cap is never started
    with pytest.raises(rn.RunnerError, match="blocked"):
        rn.run(work_dir, eval_set, _header(eval_set, max_attempts=3), rn.dry_run_factory())


def test_the_transport_hook_refuses_unowned_repeated_and_over_cap_requests(work, tmp_path) -> None:
    _, eval_set = work
    header = _header(eval_set, max_items=1, max_attempts=2)
    journal = rn.Journal(tmp_path / "j.jsonl", header, rn.JournalState())
    with pytest.raises(rn.AttemptRefused, match="no_dispatch"):
        journal.before("A")
    journal.current_item = header.items[0]
    assert journal.before("A") == 1
    journal.after(1, 200, (10, 1), "r-1", None)
    with pytest.raises(rn.AttemptRefused, match="repeated_call"):
        journal.before("A")  # an SDK retry or resend of the same call
    assert journal.before("B") == 2
    journal.after(2, 200, (10, 1), "r-2", None)
    journal.current_item = "other"
    with pytest.raises(rn.AttemptRefused, match="attempt_cap"):
        journal.before("A")
    assert len(journal.state.attempts) == 2
    assert [r.reason for r in journal.state.refusals] == [
        "no_dispatch",
        "repeated_call",
        "attempt_cap",
    ]


def test_call_b_is_refused_while_call_a_usage_is_unknown(work, tmp_path) -> None:
    _, eval_set = work
    header = _header(eval_set, max_items=1)
    journal = rn.Journal(tmp_path / "j.jsonl", header, rn.JournalState())
    journal.current_item = header.items[0]
    journal.before("A")
    with pytest.raises(rn.AttemptRefused, match="unknown_cost"):
        journal.before("B")  # call A has no result yet
    journal.after(1, 503, None, None, None)
    with pytest.raises(rn.AttemptRefused, match="unknown_cost"):
        journal.before("B")  # call A returned no usage


class _Recorder:
    """A scripted underlying transport that records every request it actually receives."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[str] = []
        self.headers: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.calls.append("B" if "claims" in body["state"] else "A")
        self.headers.append(dict(request.headers))
        return self.reply(request)


def _direct(eval_set, work_dir, tmp_path, recorder, *, item="shoe-1", **header_kw):
    """Decide one listing through the real provider and journaling transport, bypassing the
    runner's dispatch-level checks, so that only the per-request hook can stop call B."""
    from evals.listing_decisions.evaluate import load_raw

    from ecommerce_search.decision.listing_questions import build_listing_state
    from ecommerce_search.normalization.normalizer import normalize_listing

    header = _header(eval_set, **header_kw).model_copy(update={"items": (item,)})
    journal = rn.Journal(tmp_path / "direct.jsonl", header, rn.JournalState())
    raw = load_raw(work_dir / "raw", eval_set.item(item))
    state = build_listing_state(raw, normalize_listing(raw))
    assert state.attribute_questions  # this listing needs call B
    provider = _mock_factory(recorder)(journal)
    journal.current_item = item
    try:
        outcome = provider.decide(state)
    finally:
        provider.close()
    return journal, outcome


def test_call_b_is_not_sent_after_a_call_a_allowance_overrun(work, tmp_path) -> None:
    work_dir, eval_set = work
    recorder = _Recorder(_synthetic_reply)  # 200 input tokens per call
    journal, outcome = _direct(
        eval_set, work_dir, tmp_path, recorder, **_priced(input_allowance=150)
    )
    assert recorder.calls == ["A"]
    assert [r.reason for r in journal.state.refusals] == ["allowance_exceeded"]
    assert outcome.fallback_reason == "transport_error"
    assert (outcome.usage, outcome.input_tokens, outcome.output_tokens) == ("partial", 200, 1)


def test_call_b_is_not_sent_when_call_a_usage_is_missing(work, tmp_path) -> None:
    work_dir, eval_set = work
    recorder = _Recorder(lambda r: _synthetic_reply(r, usage=None))
    journal, outcome = _direct(eval_set, work_dir, tmp_path, recorder)
    assert recorder.calls == ["A"]
    assert journal.state.unaccounted_attempts == [1]
    assert outcome.fallback_reason is not None and outcome.usage == "none"


def test_call_b_is_not_sent_when_the_attempt_cap_is_exhausted(work, tmp_path) -> None:
    work_dir, eval_set = work
    recorder = _Recorder(_synthetic_reply)
    journal, outcome = _direct(eval_set, work_dir, tmp_path, recorder, max_items=1, max_attempts=1)
    assert recorder.calls == ["A"]
    assert [r.reason for r in journal.state.refusals] == ["attempt_cap"]
    assert (outcome.usage, outcome.input_tokens) == ("partial", 200)


def test_call_b_is_not_sent_without_a_remaining_spend_reservation(work, tmp_path) -> None:
    work_dir, eval_set = work
    recorder = _Recorder(_synthetic_reply)
    # One call reserves $0.00208; after call A ($0.000408 spent) another reservation does not fit.
    journal, outcome = _direct(eval_set, work_dir, tmp_path, recorder, **_priced(max_usd="0.0024"))
    assert recorder.calls == ["A"]
    assert [r.reason for r in journal.state.refusals] == ["spend_tripwire"]
    assert outcome.usage == "partial"


def test_a_refused_call_b_ends_the_run_with_its_stable_reason(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set, **_priced(input_allowance=150)).model_copy(
        update={"items": ("shoe-1",), "max_attempts": 2}
    )
    recorder = _Recorder(_synthetic_reply)
    summary = rn.run(work_dir, eval_set, header, _mock_factory(recorder))
    assert recorder.calls == ["A"]
    assert summary.stop == "allowance_exceeded" and summary.results == 1
    events = _events(work_dir)
    assert events == ["dispatch", "attempt", "attempt_result", "refused", "result", "stop"]
    result = next(e for e in _journal(work_dir) if e["event"] == "result")
    assert result["outcome"]["usage"] == "partial"


def test_the_production_http_wiring_over_a_mocked_transport(work, make_settings) -> None:
    """`live_factory` with a mocked underlying transport: no real endpoint, key or network."""
    work_dir, eval_set = work
    key = "ts-wiring-key-not-real"
    settings = make_settings(typesafe_api_key=key, jev_model="jev-1.13.0")

    def reply(request: httpx2.Request) -> httpx2.Response:
        response = _synthetic_reply(request)
        response.headers["x-typesafe-request-id"] = f"req-{key}"  # an echoed secret
        return response

    recorder = _Recorder(reply)
    factory, secret = rn.live_factory(settings, inner=httpx2.MockTransport(recorder))
    assert secret == key
    header = _header(
        eval_set, mode=rn.RunMode.LIVE, smoke=True, max_items=1, send_approval_ref="test-only",
        **_priced(),
    ).model_copy(update={"items": ("shoe-1",)})  # fmt: skip
    summary = rn.run(work_dir, eval_set, header, factory, secret=secret)
    assert recorder.calls == ["A", "B"]  # one request per call: no retry, no resend
    assert all(h["authorization"] == f"Bearer {key}" for h in recorder.headers)
    events = _journal(work_dir)
    assert [e["event"] for e in events][:5] == [
        "dispatch",
        "attempt",
        "attempt_result",
        "attempt",
        "attempt_result",
    ]
    assert [e["call"] for e in events if e["event"] == "attempt"] == ["A", "B"]
    usage = [
        (e["input_tokens"], e["output_tokens"]) for e in events if e["event"] == "attempt_result"
    ]
    assert usage == [(200, 1), (200, 1)] and summary.known_input_tokens == 400
    assert summary.stop == "secret_in_output" and summary.results == 0  # the result is dropped
    journal_bytes = (work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE).read_bytes()
    run_bytes = (work_dir / rn.RUNS_DIR / "r1" / rn.RUN_FILE).read_bytes()
    assert key.encode() not in journal_bytes and key.encode() not in run_bytes
    assert b"[redacted]" in journal_bytes


def test_the_production_wiring_never_retries_a_failed_call(work, make_settings) -> None:
    work_dir, eval_set = work
    settings = make_settings(typesafe_api_key="ts-wiring-key-not-real", jev_model="jev-1.13.0")
    recorder = _Recorder(lambda r: httpx2.Response(500, json={"error": "x"}))
    factory, secret = rn.live_factory(settings, inner=httpx2.MockTransport(recorder))
    header = _header(
        eval_set, mode=rn.RunMode.LIVE, smoke=True, max_items=1, send_approval_ref="test-only",
        **_priced(),
    ).model_copy(update={"items": ("shoe-1",)})  # fmt: skip
    summary = rn.run(work_dir, eval_set, header, factory, secret=secret)
    assert recorder.calls == ["A"] and summary.stop == "unknown_cost"
    assert summary.unaccounted_attempts == 1 and summary.known_spend_usd == 0


def test_the_spend_tripwire_reserves_allowances_before_each_listing(work) -> None:
    work_dir, eval_set = work
    # Each call reserves 1000 x $2 + 10 x $8 per million = $0.00208; actual use is 200/1.
    header = _header(eval_set, **_priced(max_usd="0.005"))
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    assert summary.stop == "spend_tripwire"
    assert summary.known_spend_usd is not None and summary.known_spend_usd <= Decimal("0.005")
    assert summary.unaccounted_attempts == 0


def test_usage_above_an_allowance_is_recorded_and_stops_terminally(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set, **_priced(input_allowance=150))
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    assert summary.stop == "allowance_exceeded" and summary.dispatched == 1
    assert summary.known_input_tokens >= 200  # the actual usage is recorded
    with pytest.raises(rn.RunnerError, match="terminally"):
        rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)


# --- unknown cost, crashes and resume -----------------------------------------------------------


def _fail_call_b(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    if "claims" in body["state"]:
        return httpx2.Response(503, json={"error": "unavailable"})
    return _synthetic_reply(request)


def test_a_failed_call_b_keeps_call_a_usage_and_stops_on_unknown_cost(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    summary = rn.run(work_dir, eval_set, header, _mock_factory(_fail_call_b))
    assert summary.stop == "unknown_cost" and summary.unaccounted_attempts == 1
    events = _journal(work_dir)
    result = next(e for e in events if e["event"] == "result")
    assert result["outcome"]["usage"] == "partial"
    assert result["outcome"]["input_tokens"] == 200  # call A is preserved
    known = [e for e in events if e["event"] == "attempt_result" and e["input_tokens"] is not None]
    assert len(known) >= 1 and summary.known_input_tokens == 200 * len(known)
    with pytest.raises(rn.RunnerError, match="unresolved unknown cost"):
        rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)


def test_a_deadline_cancellation_is_unaccounted_and_never_resent(work) -> None:
    work_dir, eval_set = work

    async def slow(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(2.0)
        return _synthetic_reply(request)

    class SlowTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await slow(request)

    def build(journal):
        config = JevConfig(
            api_key=SecretStr("ts-test-key-not-real"),
            model="jev-1.13.0",
            base_url="https://mock.invalid",
            attempt_timeout_seconds=5.0,
            max_retries=0,
            retry_budget_seconds=5.0,
            deadline_seconds=0.2,
            max_in_flight=1,
        )
        transport = JournalingTransport(SlowTransport(), journal)
        return JevListingDecisionProvider(config, transport=transport)

    header = _header(eval_set)
    summary = rn.run(work_dir, eval_set, header, build)
    assert summary.stop == "unknown_cost" and summary.dispatched == 1
    events = _journal(work_dir)
    assert [e["call"] for e in events if e["event"] == "attempt"] == ["A"]
    late = [e for e in events if e["event"] == "attempt_result"]
    assert all(e["input_tokens"] is None for e in late)  # cancelled: never accounted
    with pytest.raises(rn.RunnerError, match="unresolved unknown cost"):
        rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)


class _Crash:
    """A provider that journals call A and then crashes before any result."""

    def __init__(self, journal, *, attempt_first: bool):
        self.journal = journal
        self.attempt_first = attempt_first

    def decide(self, state):
        if self.attempt_first:
            self.journal.before("A")
        raise RuntimeError("simulated crash")

    def close(self):
        pass


def test_a_crash_after_call_a_was_sent_blocks_the_run_permanently(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    with pytest.raises(RuntimeError, match="simulated crash"):
        rn.run(work_dir, eval_set, header, lambda j: _Crash(j, attempt_first=True))
    # Simulate a hard kill: no stop line survived.
    path = work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert _events(work_dir) == ["dispatch", "attempt"]
    with pytest.raises(rn.RunnerError, match="unresolved unknown cost"):
        rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)


def test_a_crash_before_any_attempt_resumes_without_resending(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    with pytest.raises(RuntimeError):
        rn.run(work_dir, eval_set, header, lambda j: _Crash(j, attempt_first=False))
    with pytest.raises(rn.RunnerError, match="blocked"):
        rn.run(work_dir, eval_set, header, rn.dry_run_factory())
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)
    assert summary.complete and summary.unknown_items == 1 and summary.results == 11
    dispatched = [e["item_id"] for e in _journal(work_dir) if e["event"] == "dispatch"]
    assert dispatched == list(header.items) and len(set(dispatched)) == 12  # never resent
    assert "resume_approved" in _events(work_dir)


def test_an_interruption_is_resumable_and_keeps_the_frozen_order(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    calls = []

    def interrupting(journal):
        provider = rn.dry_run_factory()(journal)

        class Interrupt:
            def decide(self, state):
                calls.append(state)
                if len(calls) == 3:
                    raise KeyboardInterrupt
                return provider.decide(state)

            def close(self):
                provider.close()

        return Interrupt()

    with pytest.raises(KeyboardInterrupt):
        rn.run(work_dir, eval_set, header, interrupting)
    assert _events(work_dir)[-1] == "stop"
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)
    assert summary.complete and summary.dispatched == 12 and summary.unknown_items == 1
    dispatched = [e["item_id"] for e in _journal(work_dir) if e["event"] == "dispatch"]
    assert dispatched == list(header.items)


def test_a_stopping_fallback_with_known_usage_stops_and_is_resumable(work) -> None:
    work_dir, eval_set = work
    header = _header(eval_set)
    factory = _mock_factory(lambda r: _synthetic_reply(r, model="jev-9.9.9"))
    summary = rn.run(work_dir, eval_set, header, factory)
    assert summary.stop == "model_mismatch" and summary.unaccounted_attempts == 0
    summary = rn.run(work_dir, eval_set, header, rn.dry_run_factory(), resume_approved=True)
    assert summary.complete and summary.dispatched == 12


class _Returns:
    def __init__(self, make):
        self.make = make

    def decide(self, state):
        return self.make(state)

    def close(self):
        pass


def test_an_outcome_for_another_state_stops_without_writing_a_result(work) -> None:
    work_dir, eval_set = work
    wrong = ListingDecisionOutcome(provider="deterministic", state_sha256="0" * 64)
    summary = rn.run(work_dir, eval_set, _header(eval_set), lambda j: _Returns(lambda s: wrong))
    assert summary.stop == "binding_mismatch" and summary.results == 0


def test_a_secret_in_an_outcome_is_never_written(work) -> None:
    work_dir, eval_set = work

    def leaky(state):
        return ListingDecisionOutcome(
            provider="jev",
            state_sha256=state.state_sha256(),
            model="jev-1.13.0",
            latency_ms=1.0,
            request_id=FAKE_KEY,
            fallback_reason="timeout",
        )

    summary = rn.run(
        work_dir, eval_set, _header(eval_set), lambda j: _Returns(leaky), secret=FAKE_KEY
    )
    assert summary.stop == "secret_in_output" and summary.results == 0
    assert FAKE_KEY.encode() not in (work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE).read_bytes()


def test_the_cli_runs_dry_exports_and_reports_status(work, tmp_path, capsys) -> None:
    from evals.listing_decisions.cli import main

    work_dir, _ = work
    base = ["--set-dir", str(work_dir), "--run-id", "cli"]
    assert main(["run", *base, "--max-items", "4"]) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["complete"] and status["unaccounted_attempts"] == 0
    assert main(["status", *base]) == 0
    assert main(["export", *base, "--out", str(tmp_path / "o.jsonl")]) == 0
    assert main(["run", *base, "--max-items", "4", "--live"]) == 2  # no prices or approval
