"""Offline `qd-report-1` report: intent metrics, raw-probability diagnostics, latency, cost.

Queries and labels are the shared SYNTHETIC TEST FIXTURES and every Jev outcome is fabricated
for software tests. Golds: dq-001 laptop/none, dq-002 laptop/gaming, dq-003 headphones/travel,
dq-004 none/none, dq-005 ambiguous laptop,phone / multi student,lightweight, dq-006 unclear.
`outcome()` gives the top label probability 0.9 and every other label 0.1 / (labels - 1).
"""

import json
from decimal import Decimal

import pytest
from evals.query_decisions.category_metrics import intent_metrics, jev_intent_predictor
from evals.query_decisions.cli import main
from evals.query_decisions.contracts import CATEGORY_LABELS
from evals.query_decisions.partitions import batch_records
from evals.query_decisions.reporting import (
    REPORT_VERSION,
    build_report,
    category_probability_diagnostics,
)
from query_decision_support import QUERY_COUNT, T0, header, outcome, run

from ecommerce_search.decision.provider import CategoryProposal

# Every outcome valid. Category: dq-002 wrong (phone). Intent: dq-002 wrong (office), dq-003
# missed (none), dq-004 wrong commit (gaming), dq-005 one of the stated intents (student).
VALID_SCRIPT = [
    outcome("laptop", "none"),
    outcome("phone", "office"),
    outcome("headphones", "none"),
    outcome("none", "gaming"),
    outcome("phone", "student"),
    outcome("laptop", "none"),
]
# dq-001 falls back, dq-002 raises (unknown outcome, run stops), dq-003..dq-006 not attempted.
BROKEN_SCRIPT = [outcome(fallback="timeout"), RuntimeError("provider failed")]


def records(sealed_dir, script, run_header=None):
    run(sealed_dir, script, run_header)
    return batch_records(sealed_dir, "r1")


def test_intent_metrics_keep_every_gold_kind_apart(sealed_dir):
    metrics = intent_metrics(records(sealed_dir, VALID_SCRIPT), jev_intent_predictor)
    assert metrics["unclear_excluded"] == 1
    assert metrics["single_intent"] == {
        "n": 2,
        "correct": 0,
        "wrong": 1,
        "predicted_none": 1,
        "abstain": 0,
        "accuracy": "0.000000",
        "coverage": "0.500000",
    }
    assert metrics["gold_none"] == {"n": 2, "correct_none": 1, "wrong_commit": 1, "abstain": 0}
    assert metrics["multi"] == {
        "n": 1,
        "in_set": 1,
        "out_of_set": 0,
        "predicted_none": 0,
        "abstain": 0,
    }
    assert metrics["ambiguous"]["n"] == 0
    rows = metrics["confusion_matrix"]["rows"]
    assert rows["none"]["none"] == 1 and rows["none"]["gaming"] == 1  # none is a decision
    assert rows["travel"]["none"] == 1 and rows["travel"]["abstain"] == 0


def test_abstentions_and_fallbacks_are_not_none_decisions(sealed_dir):
    metrics = intent_metrics(records(sealed_dir, BROKEN_SCRIPT), jev_intent_predictor)
    assert metrics["gold_none"] == {"n": 2, "correct_none": 0, "wrong_commit": 0, "abstain": 2}
    assert metrics["confusion_matrix"]["rows"]["none"]["abstain"] == 2


def test_brier_and_reliability_use_raw_probabilities_with_stated_denominators(sealed_dir):
    diagnostics = category_probability_diagnostics(records(sealed_dir, VALID_SCRIPT))
    assert diagnostics["probability_kind"] == "raw provider probability; not calibrated confidence"
    assert diagnostics["eligible"] == 4  # the ambiguous and unclear golds are excluded
    assert diagnostics["excluded"] == {"ambiguous_gold": 1, "unclear_gold": 1}
    # Three correct items score 0.01 + 4 * 0.025^2 = 0.0125; the wrong one scores 1.7625.
    assert diagnostics["brier_score"] == "0.450000"
    reliability = diagnostics["reliability"]
    assert reliability["n"] == 4 and reliability["ece"] == "0.150000"
    (row,) = [r for r in reliability["rows"] if r["n"]]
    assert row == {
        "interval": "(0.800000, 0.900000]",
        "n": 4,
        "mean_raw_probability": "0.900000",
        "accuracy": "0.750000",
        "gap": "0.150000",
    }


def test_a_tied_top_counts_for_brier_but_not_for_reliability(sealed_dir):
    tied = {label: 0.0 for label in CATEGORY_LABELS} | {"laptop": 0.5, "phone": 0.5}
    tie = outcome().model_copy(
        update={
            "category": CategoryProposal(
                label=None,
                raw_choice="laptop",
                top_labels=("laptop", "phone"),
                provider_probabilities=tied,
                provider_confidence=0.375,
            )
        }
    )
    diagnostics = category_probability_diagnostics(records(sealed_dir, [tie, *VALID_SCRIPT[1:]]))
    assert diagnostics["eligible"] == 4
    assert diagnostics["excluded"]["tied_top_for_reliability"] == 1
    assert diagnostics["reliability"]["n"] == 3


def test_no_score_is_invented_without_usable_probabilities(sealed_dir):
    report_records = records(sealed_dir, BROKEN_SCRIPT)
    diagnostics = category_probability_diagnostics(report_records)
    assert diagnostics["eligible"] == 0
    assert diagnostics["brier_score"] is None and diagnostics["reliability"]["ece"] is None
    assert diagnostics["excluded"] == {
        "ambiguous_gold": 1,
        "fallback": 1,
        "not_attempted": 2,
        "unclear_gold": 1,
        "unknown_outcome": 1,
    }
    with pytest.raises(ValueError):
        category_probability_diagnostics(report_records, bins=0)


def test_run_summary_and_latency_report_missing_outcomes_and_fallbacks_apart(sealed_dir):
    run(sealed_dir, BROKEN_SCRIPT)
    report = build_report(sealed_dir, "r1")
    assert report["run"] == {
        "attempted": 2,
        "results": 1,
        "valid_results": 0,
        "fallbacks": {"timeout": 1},
        "unknown_outcome": 1,
        "not_attempted": 4,
        "run_complete": False,
    }
    latency = report["latency"]
    assert latency["all_results_ms"]["n"] == 1 and latency["valid_results_ms"] == {"n": 0}
    assert latency["missing_timing"] == {"unknown_outcome": 1, "not_attempted": 4}


def test_latency_quantiles_use_the_nearest_rank_method(sealed_dir):
    script = [
        outcome().model_copy(update={"latency_ms": float(ms)}) for ms in (60, 10, 50, 20, 40, 30)
    ]
    run(sealed_dir, script)
    latency = build_report(sealed_dir, "r1")["latency"]
    assert "ceil(q * n)" in latency["quantile_method"]
    assert latency["valid_results_ms"] == {
        "n": 6,
        "min": "10.000000",
        "max": "60.000000",
        "mean": "35.000000",
        "p50": "30.000000",
        "p90": "60.000000",
        "p95": "60.000000",
        "p99": "60.000000",
    }


def test_cost_is_an_estimate_per_1000_attempts_with_unknown_usage_disclosed(sealed_dir):
    priced = header(
        sealed_dir,
        price_usd_per_million_input=Decimal("1000"),
        price_usd_per_million_output=Decimal("0"),
        price_checked_on=T0.date(),
    )
    run(sealed_dir, [*VALID_SCRIPT[:4], outcome(fallback="timeout"), outcome()], priced)
    cost = build_report(sealed_dir, "r1")["usage_and_cost"]
    assert cost["kind"].endswith("not billing data")
    assert (cost["attempted"], cost["attempts_with_returned_usage"]) == (QUERY_COUNT, 5)
    assert cost["attempts_with_unknown_usage"] == 1
    assert cost["input_tokens"] == 500 and cost["estimated_usd_returned_usage"] == "0.500000"
    assert cost["estimated_usd_per_1000_attempts_lower"] == "83.333333"  # unknown counted at 0
    assert cost["estimated_usd_per_1000_attempts_with_usage"] == "100.000000"
    assert cost["price_checked_on"] == T0.date().isoformat()


def test_a_run_without_prices_has_no_cost_estimate(sealed_dir):
    run(sealed_dir, VALID_SCRIPT)
    cost = build_report(sealed_dir, "r1")["usage_and_cost"]
    assert cost["estimated_usd_returned_usage"] is None
    assert cost["note"] == "no prices recorded for this run; no cost estimate"


def test_the_report_is_reproducible_and_exposed_through_the_cli(sealed_dir, capsys):
    args = ["--work-dir", str(sealed_dir)]
    assert main([*args, "run", "--run-id", "dry-1", "--max-attempts", str(QUERY_COUNT)]) == 0
    capsys.readouterr()
    out = sealed_dir / "report.json"
    assert main([*args, "report", "--run-id", "dry-1", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["report_version"] == REPORT_VERSION == "qd-report-1"
    assert report["synthetic"] is True and report["mode"] == "dry_run"
    assert report == build_report(sealed_dir, "dry-1")  # same inputs, same report
    assert main([*args, "report", "--run-id", "dry-1", "--out", str(out)]) == 2  # no overwrite
    evaluated = sealed_dir / "evaluate.json"
    assert main([*args, "evaluate", "--run-id", "dry-1", "--out", str(evaluated)]) == 0
    assert json.loads(evaluated.read_text(encoding="utf-8"))["report_version"] == (
        "qd-diagnostic-2"
    )
    assert main([*args, "report", "--run-id", "no-such-run"]) == 2
