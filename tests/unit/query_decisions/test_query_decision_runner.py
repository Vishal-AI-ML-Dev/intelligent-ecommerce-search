"""Budgeted diagnostic runner: accounting, stop conditions, resume, redaction (all mocked).

Queries and labels are synthetic test fixtures. Scripted providers return fabricated
`JevOutcome`s for software testing only; the dry-run test uses the real provider over an
in-memory transport. Nothing touches the network.
"""

import json
import threading
from decimal import Decimal

import pytest
from evals.query_decisions.contracts import QueryDecisionError
from evals.query_decisions.labeling import seal_labels
from evals.query_decisions.runner import (
    JOURNAL_FILE,
    RUNS_DIR,
    RunMode,
    dry_run_provider,
    read_journal,
    run_diagnostic,
)
from query_decision_support import (
    FIXTURE_ROWS,
    QUERY_COUNT,
    T0,
    fixture_query_line,
    header,
    outcome,
    run,
    write_fixture_labels,
)

from ecommerce_search.decision import jev_provider

SECRET = "ts-fixture-secret-not-real"  # noqa: S105 - test value


def events(work_dir, run_id="r1"):
    path = work_dir / RUNS_DIR / run_id / JOURNAL_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_query_is_dispatched_once_and_journalled_before_its_result(sealed_dir):
    summary, provider = run(sealed_dir, [outcome() for _ in range(QUERY_COUNT)])
    assert summary.complete and summary.stopped is None
    assert (summary.attempts, summary.results, provider.calls) == (QUERY_COUNT, QUERY_COUNT, 6)
    assert provider.closed
    kinds = [event["event"] for event in events(sealed_dir)]
    assert kinds == ["dispatch", "result"] * QUERY_COUNT + ["complete"]
    assert summary.outcomes == {"valid": QUERY_COUNT}


def test_header_fixes_retries_zero_and_one_call_in_flight(sealed_dir):
    run_header = header(sealed_dir)
    assert (run_header.max_retries, run_header.max_in_flight) == (0, 1)
    with pytest.raises(ValueError):
        header(sealed_dir, max_retries=1)
    with pytest.raises(ValueError):
        header(sealed_dir, max_in_flight=2)


def test_journal_holds_no_query_text(sealed_dir):
    run(sealed_dir, [outcome() for _ in range(QUERY_COUNT)])
    journal = (sealed_dir / RUNS_DIR / "r1" / JOURNAL_FILE).read_text(encoding="utf-8")
    for _, text, _, _ in FIXTURE_ROWS:
        assert text not in journal


def test_the_cap_may_not_exceed_the_number_of_queries(sealed_dir):
    with pytest.raises(QueryDecisionError, match="no resends"):
        run(sealed_dir, [], header(sealed_dir, max_attempts=QUERY_COUNT + 1))


def test_attempt_cap_stops_and_blocks_until_approved_and_never_resends(sealed_dir):
    capped = header(sealed_dir, max_attempts=2)
    summary, provider = run(sealed_dir, [outcome(), outcome()], capped)
    assert (summary.stopped, summary.attempts, provider.calls) == ("attempt_cap", 2, 2)
    with pytest.raises(QueryDecisionError, match="blocked"):
        run(sealed_dir, [], capped)
    summary, provider = run(sealed_dir, [], capped, resume_approved=True)
    assert (summary.stopped, summary.attempts, provider.calls) == ("attempt_cap", 2, 0)


def test_a_restart_with_other_settings_is_refused(sealed_dir):
    run(sealed_dir, [outcome()], header(sealed_dir, max_attempts=1))
    with pytest.raises(QueryDecisionError, match="different settings"):
        run(sealed_dir, [], header(sealed_dir, max_attempts=2), resume_approved=True)


@pytest.mark.parametrize(
    "reason", ["auth_error", "model_mismatch", "request_rejected", "saturated", "provider_closed"]
)
def test_hard_fallbacks_stop_at_once(sealed_dir, reason):
    summary, provider = run(sealed_dir, [outcome(), outcome(fallback=reason), outcome()])
    assert (summary.stopped, provider.calls, summary.results) == (reason, 2, 2)


def test_three_consecutive_transient_fallbacks_stop(sealed_dir):
    script = [
        outcome(fallback="timeout"),
        outcome(),
        outcome(fallback="rate_limited"),
        outcome(fallback="server_error"),
        outcome(fallback="deadline_exceeded"),
        outcome(),
    ]
    summary, provider = run(sealed_dir, script)
    assert (summary.stopped, provider.calls) == ("consecutive_transient", 5)


def test_unexpected_exception_and_missing_outcome_leave_an_unknown_attempt(sealed_dir):
    summary, _ = run(sealed_dir, [outcome(), RuntimeError("echo of a request")])
    assert (summary.stopped, summary.unresolved) == ("unexpected_exception", 1)
    stop = events(sealed_dir)[-1]
    assert stop == {
        "event": "stop",
        "reason": "unexpected_exception",
        "at": stop["at"],
        "detail": "RuntimeError",
    }
    summary, provider = run(sealed_dir, [outcome()] * 4, resume_approved=True)
    assert summary.complete and provider.calls == 4
    assert (summary.attempts, summary.unresolved, summary.not_attempted) == (6, 1, 0)


def test_a_crash_after_dispatch_blocks_and_is_never_resent(sealed_dir):
    run_header = header(sealed_dir, max_attempts=1)
    run(sealed_dir, [outcome()], run_header)
    journal = sealed_dir / RUNS_DIR / "r1" / JOURNAL_FILE
    lines = journal.read_text(encoding="utf-8").splitlines()
    journal.write_text(lines[0] + "\n", encoding="utf-8")  # simulate a kill after the dispatch
    _, state = read_journal(journal)
    assert state.blocked_by == "an attempt has no recorded outcome"
    with pytest.raises(QueryDecisionError, match="blocked"):
        run(sealed_dir, [], run_header)


def test_the_key_in_a_result_drops_the_result_and_stops(sealed_dir):
    leaky = outcome().model_copy(update={"request_id": SECRET})
    summary, _ = run(sealed_dir, [outcome(), leaky], secret=SECRET)
    assert (summary.stopped, summary.results, summary.unresolved) == ("secret_in_output", 1, 1)
    journal = (sealed_dir / RUNS_DIR / "r1" / JOURNAL_FILE).read_text(encoding="utf-8")
    assert SECRET not in journal


def test_spend_tripwire_uses_returned_tokens_and_supplied_prices(sealed_dir):
    priced = header(
        sealed_dir,
        price_usd_per_million_input=Decimal("1000"),
        price_usd_per_million_output=Decimal("0"),
        price_checked_on=T0.date(),
        max_estimated_usd=Decimal("0.2"),
    )
    summary, provider = run(sealed_dir, [outcome(tokens=100)] * QUERY_COUNT, priced)
    assert (summary.stopped, provider.calls) == ("spend_tripwire", 2)
    assert summary.estimated_usd == Decimal("0.2")


def test_live_mode_refuses_synthetic_fixtures_and_missing_prices(sealed_dir):
    with pytest.raises(QueryDecisionError, match="synthetic"):
        run(sealed_dir, [], header(sealed_dir, mode=RunMode.LIVE), secret=SECRET)
    assert not (sealed_dir / RUNS_DIR).exists()


def test_invalid_response_rate_stops_after_twenty_results(tmp_path):
    rows = [(f"dq-{i:03d}", f"fixture query {i}", "laptop", "none") for i in range(1, 25)]
    (tmp_path / "queries.jsonl").write_text(
        "\n".join(fixture_query_line(q, t) for q, t, _, _ in rows) + "\n", encoding="utf-8"
    )
    write_fixture_labels(tmp_path, rows)
    seal_labels(tmp_path)
    script = [outcome(fallback="invalid_response")] + [outcome()] * 23
    summary, provider = run(tmp_path, script, header(tmp_path, max_attempts=24))
    # Not judged before 20 results; at 20, one invalid response is 5 % > 2 %.
    assert (summary.stopped, provider.calls) == ("invalid_response_rate", 20)


def test_dry_run_uses_the_real_provider_offline_with_retries_off(sealed_dir, monkeypatch):
    built = []
    real_init = jev_provider.JevDecisionProvider.__init__

    def spy(self, config, *, transport=None):
        built.append((config.max_retries, config.max_in_flight, transport is not None))
        real_init(self, config, transport=transport)

    monkeypatch.setattr(jev_provider.JevDecisionProvider, "__init__", spy)
    summary = run_diagnostic(sealed_dir, header(sealed_dir), dry_run_provider, say=lambda _: None)
    assert built == [(0, 1, True)]
    assert summary.complete and summary.outcomes == {"valid": QUERY_COUNT}
    assert summary.estimated_usd is None
    assert [t for t in threading.enumerate() if t.name == jev_provider.LOOP_THREAD_NAME] == []


def test_cli_dry_run_then_evaluate_without_network_or_settings(sealed_dir, capsys):
    from evals.query_decisions.cli import main

    args = ["--work-dir", str(sealed_dir)]
    assert main([*args, "run", "--run-id", "dry-1", "--max-attempts", str(QUERY_COUNT)]) == 0
    assert "(dry_run): complete" in capsys.readouterr().out
    out = sealed_dir / "report.json"
    assert main([*args, "evaluate", "--run-id", "dry-1", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["synthetic"] is True and report["mode"] == "dry_run"
    assert report["run"]["input_tokens"] == 0
    assert (
        main([*args, "evaluate", "--run-id", "dry-1", "--out", str(out)]) != 0
    )  # never overwrites


# --- live-mode construction (fake settings and spies only; no .env, key, client or network) ----

LIVE_FAKE_KEY = "ts-fake-live-key-not-real-91c4"  # noqa: S105 - test value


def test_live_provider_factory_forces_retries_off_and_one_call_in_flight(
    make_settings, monkeypatch
):
    from evals.query_decisions.runner import live_provider_factory

    settings = make_settings(
        typesafe_api_key=LIVE_FAKE_KEY,
        jev_max_retries=2,
        jev_max_in_flight=8,
        jev_attempt_timeout_seconds=0.75,
        jev_retry_budget_seconds=2.5,
        jev_deadline_seconds=2.75,
    )
    assert settings.jev_enabled is False  # live diagnostic runs do not need JEV_ENABLED
    before = settings.model_dump()
    built = []

    class SpyProvider:
        def __init__(self, config, *, transport=None):
            built.append((config, transport))

    monkeypatch.setattr(jev_provider, "JevDecisionProvider", SpyProvider)
    factory, model, key = live_provider_factory(settings)
    assert built == []  # nothing is built until the runner asks for it
    factory()

    [(config, transport)] = built
    assert transport is None
    assert (config.max_retries, config.max_in_flight) == (0, 1)
    assert (config.model, config.base_url) == (settings.jev_model, settings.jev_base_url)
    assert (
        config.attempt_timeout_seconds,
        config.retry_budget_seconds,
        config.deadline_seconds,
    ) == (0.75, 2.5, 2.75)
    assert config.api_key.get_secret_value() == LIVE_FAKE_KEY
    assert (model, key) == (settings.jev_model, LIVE_FAKE_KEY)
    assert settings.model_dump() == before
    assert (settings.jev_max_retries, settings.jev_max_in_flight) == (2, 8)


@pytest.mark.parametrize("missing_key", [True, False])
def test_live_provider_factory_refuses_no_key_and_non_settings(make_settings, missing_key):
    from evals.query_decisions.runner import live_provider_factory

    settings = make_settings() if missing_key else object()
    with pytest.raises(QueryDecisionError):
        live_provider_factory(settings)


LIVE_PRICES = [
    "--price-input-usd-per-million",
    "0.042",
    "--price-output-usd-per-million",
    "0",
    "--price-checked-on",
    "2026-10-18",
    "--max-estimated-usd",
    "0.05",
]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([], "prices"),
        (LIVE_PRICES[:6], "prices"),
        (LIVE_PRICES, "synthetic"),
    ],
)
def test_rejected_live_cli_input_never_builds_settings_or_a_provider(
    sealed_dir, monkeypatch, capsys, extra, message
):
    from evals.query_decisions import cli

    import ecommerce_search.config

    def refuse(*args, **kwargs):
        raise AssertionError("must not be constructed for rejected input")

    monkeypatch.setattr(ecommerce_search.config, "Settings", refuse)
    monkeypatch.setattr(cli, "live_provider_factory", refuse)
    monkeypatch.setattr(jev_provider, "JevDecisionProvider", refuse)

    argv = ["--work-dir", str(sealed_dir), "run", "--run-id", "live-1", "--max-attempts", "1"]
    assert cli.main([*argv, "--live", *extra]) == 2
    assert message in capsys.readouterr().err
    assert not (sealed_dir / RUNS_DIR).exists()


def test_live_provider_factory_refuses_an_unapproved_model(make_settings, monkeypatch):
    from evals.query_decisions.runner import live_provider_factory

    built = []
    monkeypatch.setattr(jev_provider, "JevDecisionProvider", lambda *a, **k: built.append(a))
    settings = make_settings(typesafe_api_key=LIVE_FAKE_KEY, jev_model="jev-1.14.0")
    with pytest.raises(QueryDecisionError, match="approved model jev-1.13.0"):
        live_provider_factory(settings)
    assert built == []


def test_a_live_header_with_an_unapproved_model_is_refused_before_any_record(sealed_dir):
    live = header(
        sealed_dir,
        mode=RunMode.LIVE,
        model="jev-1.14.0",
        price_usd_per_million_input=Decimal("0.042"),
        price_usd_per_million_output=Decimal("0"),
        price_checked_on=T0.date(),
        max_estimated_usd=Decimal("0.05"),
    )

    def never():
        raise AssertionError("no provider may be built")

    with pytest.raises(QueryDecisionError, match="approved model"):
        run_diagnostic(sealed_dir, live, never, secret=SECRET, say=lambda _: None)
    assert not (sealed_dir / RUNS_DIR).exists()


def test_cli_reports_a_jev_configuration_error_cleanly_with_no_dispatch(
    sealed_dir, monkeypatch, capsys
):
    from evals.query_decisions import cli

    from ecommerce_search.decision.jev_provider import JevConfigurationError

    def failing_factory():
        raise JevConfigurationError()

    monkeypatch.setattr(cli, "dry_run_provider", failing_factory)
    argv = ["--work-dir", str(sealed_dir), "run", "--run-id", "cfg-1", "--max-attempts", "1"]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == "error: jev configuration invalid\n"
    assert "Traceback" not in captured.out + captured.err
    assert not (sealed_dir / RUNS_DIR / "cfg-1" / JOURNAL_FILE).exists()


def test_cli_evaluate_refuses_an_unknown_run_cleanly(sealed_dir, capsys):
    from evals.query_decisions import cli

    argv = ["--work-dir", str(sealed_dir), "evaluate", "--run-id", "no-such-run"]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == "error: no such run in the work directory\n"
    assert captured.out == ""
