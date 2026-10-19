"""Gate-tooling CLI (`partition`, `sweep`, `freeze`, `accept`) over synthetic fixtures (mocked).

Every query and label is a SYNTHETIC TEST FIXTURE and every Jev outcome is fabricated for
software tests. Runs use scripted providers with the real clock, so evaluation dispatches come
after the threshold is frozen. Thresholds and criteria are arbitrary test values, not approved
parameters.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from evals.query_decisions.cli import main
from evals.query_decisions.labeling import seal_labels
from evals.query_decisions.runner import run_diagnostic
from query_decision_support import (
    FIXTURE_ROWS,
    T0,
    ScriptedProvider,
    header,
    outcome,
    write_fixture_labels,
    write_fixture_queries,
)

CAL_ROWS = [
    ("dq-001", "garden hose 20m", "none", "none"),
    ("dq-002", "camera tripod stand", "none", "none"),
]
EVAL_SCRIPT = [
    outcome("laptop", "none"),
    outcome("laptop", "gaming"),
    outcome("headphones", "travel"),  # dq-003: correct fill
    outcome("none", "none"),  # dq-004: Jev none fills nothing
    outcome("phone", "none"),  # dq-005: commit inside the ambiguous candidates
    outcome("laptop", "none"),  # dq-006: unclear gold, excluded
]
CRITERIA = [
    "--confidence-level",
    "0.95",
    "--wrong-commit-definition",
    "lenient",
    "--max-wrong-commit-upper-bound",
    "1",
    "--min-coverage",
    "0.5",
    "--min-accepted-fills",
    "1",
    "--max-unknown-or-fallback-rate",
    "0",
    "--max-unclear-rate",
    "0.3",
]


def sealed_batch(path: Path, rows) -> Path:
    path.mkdir()
    write_fixture_queries(path, rows)
    write_fixture_labels(path, rows)
    seal_labels(path, now=lambda: T0 - timedelta(days=1))
    return path


def scripted_run(work_dir: Path, outcomes) -> None:
    provider = ScriptedProvider(work_dir, "r1", outcomes)
    run_diagnostic(
        work_dir,
        header(work_dir, max_attempts=len(outcomes)),
        lambda: provider,
        now=lambda: datetime.now(UTC),
        say=lambda _: None,
    )


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return (
        root,
        sealed_batch(tmp_path / "eval", FIXTURE_ROWS),
        sealed_batch(tmp_path / "cal", CAL_ROWS),
    )


def cli(*argv) -> int:
    return main([str(arg) for arg in argv])


def create_partitions(root, eval_dir, cal_dir):
    assert (
        cli(
            "partition",
            "create",
            "--root",
            root,
            "--id",
            "cal-1",
            "--role",
            "calibration",
            "--screening",
            "parser_abstains_on_category",
            "--source",
            cal_dir,
            "dq-001,dq-002",
        )
        == 0
    )
    assert (
        cli(
            "partition",
            "create",
            "--root",
            root,
            "--id",
            "eval-1",
            "--role",
            "evaluation",
            "--screening",
            "parser_abstains_on_category",
            "--source",
            eval_dir,
            "dq-003,dq-004,dq-005,dq-006",
        )
        == 0
    )


def freeze(root, threshold_id="t-1", *extra):
    return cli(
        "freeze",
        "--root",
        root,
        "--id",
        threshold_id,
        "--calibration",
        "cal-1",
        "--threshold",
        "0.5",
        "--model",
        "jev-1.13.0",
        *CRITERIA,
        *extra,
    )


def test_a_complete_synthetic_workflow(dirs, capsys):
    root, eval_dir, cal_dir = dirs
    create_partitions(root, eval_dir, cal_dir)
    assert cli("partition", "show", "--root", root, "--id", "eval-1") == 0
    assert "eval-1 (evaluation): 4 members" in capsys.readouterr().out
    assert (
        cli(
            "partition", "check", "--root", root, "--calibration", "cal-1", "--evaluation", "eval-1"
        )
        == 0
    )

    scripted_run(cal_dir, [outcome("none"), outcome("phone")])
    sweep = root / "sweep.json"
    assert (
        cli(
            "sweep",
            "--root",
            root,
            "--calibration",
            "cal-1",
            "--batch",
            cal_dir,
            "r1",
            "--threshold",
            "0.95",
            "--threshold",
            "0.5",
            "--out",
            sweep,
        )
        == 0
    )
    rows = json.loads(sweep.read_text(encoding="utf-8"))["sweep"]
    assert [(r["threshold"], r["accepted_fills"], r["wrong_commits_strict"]) for r in rows] == [
        ("0.5", 1, 1),
        ("0.95", 0, 0),
    ]
    capsys.readouterr()
    assert (
        cli(
            "sweep",
            "--root",
            root,
            "--calibration",
            "eval-1",
            "--batch",
            eval_dir,
            "r1",
            "--threshold",
            "0.5",
        )
        == 2
    )
    assert "never on evaluation data" in capsys.readouterr().err

    assert freeze(root, "t-1", "--selection-note", "test value") == 0
    assert freeze(root) == 2  # write-once
    assert freeze(root, "t-2") == 0  # a second threshold, also frozen before the evaluation run
    scripted_run(eval_dir, EVAL_SCRIPT)  # dispatched after both thresholds were frozen
    capsys.readouterr()
    assert (
        cli(
            "accept",
            "--root",
            root,
            "--threshold",
            "t-1",
            "--evaluation",
            "eval-1",
            "--batch",
            eval_dir,
            "r1",
        )
        == 0
    )
    assert "acceptance pass (recorded)" in capsys.readouterr().out
    report = json.loads((root / "acceptance" / "t-1.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "pass" and report["synthetic"] is True
    assert len(list((root / "consumption").iterdir())) == 1

    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert (
        cli(
            "accept",
            "--root",
            root,
            "--threshold",
            "t-1",
            "--evaluation",
            "eval-1",
            "--batch",
            eval_dir,
            "r1",
        )
        == 0
    )
    assert "identical repeat; nothing written" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before

    assert (
        cli(
            "accept",
            "--root",
            root,
            "--threshold",
            "t-2",
            "--evaluation",
            "eval-1",
            "--batch",
            eval_dir,
            "r1",
        )
        == 2
    )
    assert "already consumed by a different confirmation" in capsys.readouterr().err


def test_freeze_requires_every_acceptance_criterion(dirs):
    root, eval_dir, cal_dir = dirs
    create_partitions(root, eval_dir, cal_dir)
    without_coverage = [a for a in CRITERIA if a not in ("--min-coverage", "0.5")]
    with pytest.raises(SystemExit) as exited:
        cli(
            "freeze",
            "--root",
            root,
            "--id",
            "t-1",
            "--calibration",
            "cal-1",
            "--threshold",
            "0.5",
            "--model",
            "jev-1.13.0",
            *without_coverage,
        )
    assert exited.value.code == 2
    assert not (root / "thresholds").exists()
    assert (
        cli(
            "freeze",
            "--root",
            root,
            "--id",
            "t-1",
            "--calibration",
            "eval-1",
            "--threshold",
            "0.5",
            "--model",
            "jev-1.13.0",
            *CRITERIA,
        )
        == 2
    )


def test_partitions_are_write_once_and_overlap_is_refused(dirs, capsys):
    root, eval_dir, cal_dir = dirs
    create_partitions(root, eval_dir, cal_dir)
    assert (
        cli(
            "partition",
            "create",
            "--root",
            root,
            "--id",
            "eval-1",
            "--role",
            "evaluation",
            "--screening",
            "none",
            "--source",
            eval_dir,
            "dq-001",
        )
        == 2
    )
    assert (
        cli(
            "partition",
            "create",
            "--root",
            root,
            "--id",
            "cal-2",
            "--role",
            "calibration",
            "--screening",
            "none",
            "--source",
            eval_dir,
            "dq-003",
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli(
            "partition", "check", "--root", root, "--calibration", "cal-2", "--evaluation", "eval-1"
        )
        == 2
    )
    assert "partitions overlap" in capsys.readouterr().err


def test_gate_commands_need_an_explicit_root(dirs):
    for argv in (
        ["partition", "show", "--id", "x"],
        ["accept", "--threshold", "t", "--evaluation", "e", "--batch", "d", "r"],
    ):
        with pytest.raises(SystemExit) as exited:
            cli(*argv)
        assert exited.value.code == 2
