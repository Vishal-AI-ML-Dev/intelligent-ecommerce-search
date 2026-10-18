"""`qd-2` query sources: human-authored, AI-authored and synthetic test fixtures.

Every record here is software-test data. The `ai_authored` records are built in memory only to
exercise validation; they are not the real diagnostic queries and are never written to disk.
Seals are built in memory with `make_seal`. Nothing touches the network, `.env` or a key.
"""

import json
from decimal import Decimal

import pytest
from evals.contracts.canonical import encode_json_object
from evals.query_decisions import cli, runner
from evals.query_decisions.contracts import (
    DecisionQuery,
    QueryDecisionError,
    QuerySource,
    load_queries,
    source_counts,
)
from evals.query_decisions.evaluate import evaluate
from evals.query_decisions.labeling import SEAL_FILE, load_sealed
from evals.query_decisions.runner import RUNS_DIR, RunMode, live_refusal, run_diagnostic
from pydantic import ValidationError
from query_decision_support import (
    T0,
    fixture_query_line,
    header,
    make_seal,
    outcome,
    run,
)

import ecommerce_search.config
from ecommerce_search.decision import jev_provider

AI_SEAL = make_seal(ai=10)
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


def _record(**overrides) -> dict:
    record = json.loads(fixture_query_line("dq-001", "fixture text"))
    record.update(overrides)
    return record


def _load(record: dict):
    return load_queries(json.dumps(record).encode())


# --- schema ------------------------------------------------------------------------------------


def test_ai_authored_queries_need_a_tool_and_no_handle():
    (query,) = _load(_record(source="ai_authored", author_tool="notion-ai"))
    assert (query.source, query.author_tool, query.author_handle) == (
        QuerySource.AI_AUTHORED,
        "notion-ai",
        None,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "ai_authored"},  # no tool
        {"source": "ai_authored", "author_tool": "notion-ai", "author_handle": "ann-1"},
        {"source": "ai_authored", "author_tool": "ann-1"},  # a handle is not a tool
        {"source": "ai_authored", "author_tool": "Notion AI"},
        {"source": "human_authored", "author_handle": "ann-1", "author_tool": "notion-ai"},
        {"source": "human_authored"},  # no handle
        {"author_tool": "notion-ai"},  # a synthetic fixture has neither
        {"author_handle": "ann-1"},
    ],
)
def test_source_specific_author_fields_are_enforced(overrides):
    with pytest.raises(QueryDecisionError):
        _load(_record(**overrides))


def test_unsupported_schema_versions_are_refused_clearly():
    with pytest.raises(QueryDecisionError, match="unsupported schema_version; .* reads qd-2 only"):
        _load(_record(schema_version="qd-1"))
    with pytest.raises(QueryDecisionError, match="unsupported schema_version"):
        load_queries(b'["not", "an", "object"]\n')


def test_source_counts_list_every_source():
    queries = (
        DecisionQuery.model_validate_json(json.dumps(_record()), strict=True),
        DecisionQuery.model_validate_json(
            json.dumps(_record(query_id="dq-002", source="ai_authored", author_tool="notion-ai")),
            strict=True,
        ),
    )
    assert source_counts(queries) == {
        QuerySource.HUMAN_AUTHORED: 0,
        QuerySource.AI_AUTHORED: 1,
        QuerySource.SYNTHETIC_TEST_FIXTURE: 1,
    }


def test_seal_counts_must_add_up_and_synthetic_means_fixtures_only():
    assert make_seal(ai=10).synthetic is False
    assert make_seal(ai=9, fixtures=1).synthetic is True
    with pytest.raises(ValidationError):
        make_seal(ai=10, query_count=11)
    with pytest.raises(ValidationError):
        make_seal(ai=10, synthetic=True)
    with pytest.raises(ValidationError):
        make_seal(fixtures=2, synthetic=False)


# --- sealing -----------------------------------------------------------------------------------


def test_the_seal_records_source_counts_of_the_sealed_queries(sealed_dir):
    seal, _, _ = load_sealed(sealed_dir)
    assert (
        seal.human_authored_queries,
        seal.ai_authored_queries,
        seal.synthetic_fixture_queries,
        seal.synthetic,
    ) == (0, 0, 6, True)


def test_a_seal_whose_counts_disagree_with_the_queries_is_refused(sealed_dir):
    seal, _, _ = load_sealed(sealed_dir)
    forged = seal.model_copy(update={"human_authored_queries": 1, "synthetic_fixture_queries": 5})
    (sealed_dir / SEAL_FILE).write_bytes(encode_json_object(forged))
    with pytest.raises(QueryDecisionError, match="source counts"):
        load_sealed(sealed_dir)


# --- live approval ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seal", "prices", "approved", "expected"),
    [
        (AI_SEAL, False, True, "prices"),
        (AI_SEAL, True, False, "--approve-ai-authored"),
        (AI_SEAL, True, True, None),
        (make_seal(ai=9, fixtures=1), True, True, "synthetic"),
        (make_seal(fixtures=6), True, True, "synthetic"),
        (make_seal(human=10), True, True, "no AI-authored queries"),
        (make_seal(human=10), True, False, None),
    ],
)
def test_live_refusal_rules(seal, prices, approved, expected):
    refusal = live_refusal(seal, prices_complete=prices, ai_authored_approved=approved)
    if expected is None:
        assert refusal is None
    else:
        assert refusal is not None and expected in refusal


class SettingsReached(Exception):
    """Raised by the patched Settings: the CLI got past every precondition."""


def _guard_construction(monkeypatch, settings_error=AssertionError):
    def settings(*args, **kwargs):
        raise settings_error("Settings constructed")

    def never(*args, **kwargs):
        raise AssertionError("must not be constructed")

    monkeypatch.setattr(ecommerce_search.config, "Settings", settings)
    monkeypatch.setattr(cli, "live_provider_factory", never)
    monkeypatch.setattr(jev_provider, "JevDecisionProvider", never)


def _live_argv(tmp_path, *extra):
    return [
        "--work-dir",
        str(tmp_path),
        "run",
        "--run-id",
        "live-1",
        "--max-attempts",
        "1",
        "--live",
        *LIVE_PRICES,
        *extra,
    ]


def test_cli_refuses_ai_authored_live_without_approval_before_settings(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "load_sealed", lambda work_dir: (AI_SEAL, (), ()))
    _guard_construction(monkeypatch)
    assert cli.main(_live_argv(tmp_path)) == 2
    assert "--approve-ai-authored" in capsys.readouterr().err
    assert not (tmp_path / RUNS_DIR).exists()


def test_cli_with_approval_gets_past_the_preconditions_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_sealed", lambda work_dir: (AI_SEAL, (), ()))
    _guard_construction(monkeypatch, settings_error=SettingsReached)
    with pytest.raises(SettingsReached):
        cli.main(_live_argv(tmp_path, "--approve-ai-authored"))
    assert not (tmp_path / RUNS_DIR).exists()


def test_cli_refuses_a_fixture_batch_live_even_with_the_approval(sealed_dir, monkeypatch, capsys):
    _guard_construction(monkeypatch)
    assert cli.main(_live_argv(sealed_dir, "--approve-ai-authored")) == 2
    assert "synthetic" in capsys.readouterr().err
    assert not (sealed_dir / RUNS_DIR).exists()


def _live_header(sealed_dir, **overrides):
    return header(
        sealed_dir,
        mode=RunMode.LIVE,
        price_usd_per_million_input=Decimal("0.042"),
        price_usd_per_million_output=Decimal("0"),
        price_checked_on=T0.date(),
        max_estimated_usd=Decimal("0.05"),
        **overrides,
    )


def test_runner_refuses_ai_authored_live_without_recorded_approval(sealed_dir, monkeypatch):
    real_seal, _, _ = load_sealed(sealed_dir)
    ai_seal = make_seal(
        ai=6,
        queries_sha256=real_seal.queries_sha256,
        labels_sha256=real_seal.labels_sha256,
    )
    monkeypatch.setattr(runner, "load_sealed", lambda work_dir: (ai_seal, (), ()))

    def never():
        raise AssertionError("no provider may be built")

    with pytest.raises(QueryDecisionError, match="--approve-ai-authored"):
        run_diagnostic(sealed_dir, _live_header(sealed_dir), never, secret="k", say=print)  # noqa: S106 - fake value
    assert not (sealed_dir / RUNS_DIR).exists()


def test_the_approval_is_refused_for_dry_runs_and_recorded_in_the_header(sealed_dir):
    with pytest.raises(QueryDecisionError, match="live runs only"):
        run(sealed_dir, [], header(sealed_dir, ai_authored_approved=True))
    assert not (sealed_dir / RUNS_DIR).exists()
    assert _live_header(sealed_dir, ai_authored_approved=True).ai_authored_approved is True
    assert header(sealed_dir).ai_authored_approved is False


# --- evaluator ----------------------------------------------------------------------------------


def test_report_carries_source_counts_and_development_only_notes(sealed_dir):
    run(sealed_dir, [outcome() for _ in range(6)])
    report = evaluate(sealed_dir, "r1")
    assert report["query_sources"] == {
        "human_authored": 0,
        "ai_authored": 0,
        "synthetic_test_fixture": 6,
    }
    assert report["ai_author_tools"] == []
    assert report["evidence_notes"][0] == "development diagnostic only; never held-out evidence"
    assert any("synthetic test fixtures" in note for note in report["evidence_notes"])
    assert {row["source"] for row in report["per_query"]} == {"synthetic_test_fixture"}


def test_ai_authored_batches_get_a_development_only_note():
    from evals.query_decisions.evaluate import _evidence_notes

    notes = _evidence_notes(make_seal(human=30, ai=10))
    assert notes == [
        "development diagnostic only; never held-out evidence",
        "10 of 40 queries are AI-authored (development-only; labels are human annotations; "
        "not held-out evidence)",
    ]


# --- end to end on temporary files --------------------------------------------------------------


def test_ai_authored_batch_end_to_end_seal_dry_run_and_report(tmp_path, monkeypatch):
    """Temporary software-test data only. The `ai_authored` source value here exercises the
    schema: these texts are test fixtures, the labels are scripted test labels (not human
    annotation), and the outputs come from the mocked dry-run transport. None of it is
    diagnostic evidence."""
    from evals.query_decisions.labeling import QUERIES_FILE, seal_labels
    from evals.query_decisions.runner import dry_run_provider
    from query_decision_support import FIXTURE_ROWS, write_fixture_labels

    def no_settings(*args, **kwargs):
        raise AssertionError("live Settings must not be constructed")

    monkeypatch.setattr(ecommerce_search.config, "Settings", no_settings)
    lines = [
        json.dumps(
            _record(query_id=query_id, text=text, source="ai_authored", author_tool="notion-ai")
        )
        for query_id, text, _, _ in FIXTURE_ROWS
    ]
    (tmp_path / QUERIES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_fixture_labels(tmp_path)

    seal = seal_labels(tmp_path)
    assert (seal.ai_authored_queries, seal.human_authored_queries) == (6, 0)
    assert (seal.synthetic_fixture_queries, seal.synthetic) == (0, False)
    _, queries, _ = load_sealed(tmp_path)
    assert {q.author_tool for q in queries} == {"notion-ai"}

    summary = run_diagnostic(
        tmp_path, header(tmp_path, run_id="dry-ai"), dry_run_provider, say=lambda _: None
    )
    assert summary.complete and summary.outcomes == {"valid": 6}

    report = evaluate(tmp_path, "dry-ai")
    assert report["query_sources"] == {
        "human_authored": 0,
        "ai_authored": 6,
        "synthetic_test_fixture": 0,
    }
    assert report["ai_author_tools"] == ["notion-ai"]
    assert {row["source"] for row in report["per_query"]} == {"ai_authored"}
    assert report["synthetic"] is True  # the dry run's responses are synthetic
    assert report["synthetic_reason"] == "dry run: fixed synthetic responses, not Jev output"
    assert report["evidence_scope"] == "development diagnostic; not held-out evidence"
    assert report["evidence_notes"] == [
        "development diagnostic only; never held-out evidence",
        "6 of 6 queries are AI-authored (development-only; labels are human annotations; "
        "not held-out evidence)",
    ]
    assert report["run"]["input_tokens"] == 0
