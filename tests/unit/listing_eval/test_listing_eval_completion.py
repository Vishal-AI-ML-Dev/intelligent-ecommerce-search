"""Remaining-only completion runs and safe combination (mocked; no live call): selection
excludes every dispatched item, binding to the source journal, stop-on-first-invalid,
duplicate rejection, journal-verified lineage, legacy compatibility and combined quality counts."""

import json

import pytest
from evals.listing_decisions import combine as cb
from evals.listing_decisions import runner as rn
from evals.listing_decisions.contracts import EvalSetError
from evals.listing_decisions.evaluate import evaluate
from test_listing_eval_runner import (  # noqa: F401 - loopback_only is an autouse fixture
    _at,
    _big_set,
    _header,
    _journal,
    _scripted,
    loopback_only,
)


def _source_run(tmp_path, count=30, invalid=(3, 5)):
    """A source run that stops with `invalid_response_rate` after 20 results (2 invalid)."""
    work_dir, eval_set, order = _big_set(tmp_path / "big", count)
    header = _header(eval_set, max_items=count)
    factory = _scripted(_at(order, *invalid))
    assert rn.run(work_dir, eval_set, header, factory).stop == "invalid_response_rate"
    return work_dir, eval_set, order, factory


def _completion_header(work_dir, eval_set, max_items=10, **kw):
    excluded, items = rn.exclusions(work_dir, eval_set, ("r1",))
    return rn.make_header(
        eval_set,
        run_id="c1",
        mode=rn.RunMode.DRY_RUN,
        max_items=max_items,
        excluded_runs=excluded,
        excluded_items=items,
        **kw,
    )


def _source_files(work_dir):
    run_dir = work_dir / rn.RUNS_DIR / "r1"
    return {p.name: p.read_bytes() for p in run_dir.iterdir()}


# --- selection and binding ----------------------------------------------------------------------


def test_the_selection_is_the_frozen_order_minus_every_dispatched_item(tmp_path) -> None:
    work_dir, eval_set, _, _ = _source_run(tmp_path)
    state = rn.read_journal(work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE)
    header = _completion_header(work_dir, eval_set)
    frozen = rn.frozen_items(eval_set, 30)
    assert header.items == tuple(i for i in frozen if i not in state.dispatched)
    assert len(header.items) == 10 and not set(header.items) & set(state.dispatched)
    assert header.excluded_runs[0].run_id == "r1" and header.excluded_runs[0].dispatched == 20


def test_unknown_and_failed_items_are_excluded_too(tmp_path) -> None:
    work_dir, eval_set, order = _big_set(tmp_path / "big", 30)
    header = _header(eval_set, max_items=30)
    factory = _scripted(_at(order, 3, 5))

    def crash_on_the_fourth(journal):
        provider = factory(journal)

        class Crash:
            calls = 0

            def decide(self, state):
                Crash.calls += 1
                if Crash.calls == 4:
                    raise RuntimeError("simulated crash")
                return provider.decide(state)

            def close(self):
                provider.close()

        return Crash()

    with pytest.raises(RuntimeError):
        rn.run(work_dir, eval_set, header, crash_on_the_fourth)
    state = rn.read_journal(work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE)
    assert len(state.unknown_items) == 1 and state.invalid_responses == 1
    completion = _completion_header(work_dir, eval_set, max_items=30)
    assert len(completion.items) == 26  # 30 minus 4 dispatched: 2 valid, 1 invalid, 1 unknown
    assert not set(completion.items) & set(state.dispatched)


def test_a_completion_run_never_touches_the_source_run(tmp_path) -> None:
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    before = _source_files(work_dir)
    header = _completion_header(work_dir, eval_set)
    summary = rn.run(work_dir, eval_set, header, factory)
    assert summary.complete and summary.dispatched == 10
    assert _source_files(work_dir) == before  # header, journal and artifacts unchanged
    recorded = rn.RunHeader.model_validate_json(
        (work_dir / rn.RUNS_DIR / "c1" / rn.RUN_FILE).read_bytes()
    )
    assert recorded.items == header.items and recorded.excluded_runs == header.excluded_runs


def test_a_changed_or_locked_source_journal_is_refused(tmp_path) -> None:
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    header = _completion_header(work_dir, eval_set)
    journal = work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE
    journal.write_bytes(journal.read_bytes() + b"")  # unchanged bytes are fine
    with open(journal, "ab") as handle:
        handle.write(b'{"event":"resume_approved"}\n')  # any change after selection
    with pytest.raises(rn.RunnerError, match="journal changed since selection"):
        rn.run(work_dir, eval_set, header, factory)
    (work_dir / rn.RUNS_DIR / "r1" / rn.LOCK_FILE).write_text("", encoding="utf-8")
    with pytest.raises(rn.RunnerError, match="in progress"):
        rn.exclusions(work_dir, eval_set, ("r1",))


def test_an_excluded_run_bound_to_another_set_is_refused(tmp_path) -> None:
    work_dir, eval_set, _, _ = _source_run(tmp_path)
    path = work_dir / rn.RUNS_DIR / "r1" / rn.RUN_FILE
    header = rn.RunHeader.model_validate_json(path.read_bytes())
    path.write_text(header.model_copy(update={"set_sha256": "0" * 64}).model_dump_json())
    with pytest.raises(rn.RunnerError, match="another set"):
        rn.exclusions(work_dir, eval_set, ("r1",))


def test_a_header_selecting_an_already_dispatched_item_is_refused(tmp_path) -> None:
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    header = _completion_header(work_dir, eval_set)
    state = rn.read_journal(work_dir / rn.RUNS_DIR / "r1" / rn.JOURNAL_FILE)
    forged = header.model_copy(update={"items": (next(iter(state.dispatched)),)})
    with pytest.raises(rn.RunnerError, match="already dispatched"):
        rn.run(work_dir, eval_set, forged, factory)


# --- stop on first invalid ----------------------------------------------------------------------


def test_stop_on_first_invalid_stops_terminally_without_the_20_result_minimum(tmp_path) -> None:
    work_dir, eval_set, order, _ = _source_run(tmp_path)
    header = _completion_header(work_dir, eval_set, stop_on_first_invalid=True)
    second = int(header.items[1].split("-")[-1])
    factory = _scripted(_at(order, 3, 5) | {second})
    summary = rn.run(work_dir, eval_set, header, factory)
    assert summary.stop == "first_invalid_response" and summary.results == 2
    assert summary.invalid_responses == 1
    with pytest.raises(rn.RunnerError, match="terminally"):
        rn.run(work_dir, eval_set, header, factory, resume_approved=True)


def test_without_the_option_a_short_run_continues_through_invalid_responses(tmp_path) -> None:
    work_dir, eval_set, order, _ = _source_run(tmp_path)
    header = _completion_header(work_dir, eval_set)
    assert header.stop_on_first_invalid is False  # the default is unchanged
    numbers = {int(i.split("-")[-1]) for i in header.items[:3]}
    summary = rn.run(work_dir, eval_set, header, _scripted(_at(order, 3, 5) | numbers))
    assert summary.complete and summary.invalid_responses == 3  # under 20 results


# --- combine ------------------------------------------------------------------------------------


def _completed(tmp_path):
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    rn.run(work_dir, eval_set, _completion_header(work_dir, eval_set), factory)
    return work_dir, eval_set


def test_combine_keeps_every_result_and_fallback_with_lineage(tmp_path) -> None:
    work_dir, eval_set = _completed(tmp_path)
    out = tmp_path / "combined.jsonl"
    assert cb.combine(work_dir, eval_set, ("r1", "c1"), out) == {
        "records": 30,
        "sources": ["r1", "c1"],
    }
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [s["run_id"] for s in lines[0]["sources"]] == ["r1", "c1"]
    assert [s["results"] for s in lines[0]["sources"]] == [20, 10]
    assert all(r["source_run"]["run_id"] in ("r1", "c1") for r in lines[1:])
    invalid = [r for r in lines[1:] if r["outcome"]["fallback_reason"] == "invalid_response"]
    assert len(invalid) == 2 and {r["source_run"]["run_id"] for r in invalid} == {"r1"}
    cb.verify_lineage(work_dir, eval_set, out)
    report, failed = evaluate(eval_set, work_dir / "raw", out, lineage_verified=True)
    assert failed == [] and report["outcomes"]["lineage"] == {"present": True, "verified": True}
    quality = report["run_quality"]
    assert (quality["results"], quality["invalid_responses"], quality["breached"]) == (30, 2, True)
    assert quality["by_source_run"]["r1"]["invalid_responses"] == 2
    assert quality["by_source_run"]["c1"] == {
        "results": 10,
        "invalid_responses": 0,
        "rate": "0",
        "breached": False,  # under 20 results the rule cannot fire on its own
    }


def test_the_combined_quality_rule_matches_hand_computed_counts(tmp_path) -> None:
    work_dir, eval_set, order = _big_set(tmp_path / "big", 60)
    header = _header(eval_set, max_items=60)
    factory = _scripted(_at(order, 2))  # 1/20 breached, recovers to 1/60
    assert rn.run(work_dir, eval_set, header, factory).stop == "invalid_response_rate"
    excluded, items = rn.exclusions(work_dir, eval_set, ("r1",))
    completion = rn.make_header(
        eval_set, run_id="c1", mode=rn.RunMode.DRY_RUN, max_items=60,
        excluded_runs=excluded, excluded_items=items,
    )  # fmt: skip
    assert rn.run(work_dir, eval_set, completion, factory).complete
    out = tmp_path / "c.jsonl"
    cb.combine(work_dir, eval_set, ("r1", "c1"), out)
    quality = evaluate(eval_set, work_dir / "raw", out)[0]["run_quality"]
    assert (quality["results"], quality["invalid_responses"], quality["breached"]) == (60, 1, False)


def test_combine_rejects_items_dispatched_in_two_runs(tmp_path) -> None:
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    overlap = _header(eval_set, max_items=5).model_copy(update={"run_id": "o1"})
    rn.run(work_dir, eval_set, overlap, factory)  # dispatches items r1 already dispatched
    with pytest.raises(EvalSetError, match="dispatched in both"):
        cb.combine(work_dir, eval_set, ("r1", "o1"), tmp_path / "x.jsonl")
    with pytest.raises(EvalSetError, match="once"):
        cb.combine(work_dir, eval_set, ("r1", "r1"), tmp_path / "y.jsonl")


def test_combine_rejects_differently_bound_runs(tmp_path) -> None:
    work_dir, eval_set, _, factory = _source_run(tmp_path)
    header = _completion_header(work_dir, eval_set)
    other = header.model_copy(update={"model": "jev-1.14.0"})  # a dry run may name any model
    path = work_dir / rn.RUNS_DIR / "c1"
    path.mkdir(parents=True)
    (path / rn.RUN_FILE).write_text(other.model_dump_json(), encoding="utf-8")
    (path / rn.JOURNAL_FILE).write_text("", encoding="utf-8")
    with pytest.raises(EvalSetError, match="bound differently: model"):
        cb.combine(work_dir, eval_set, ("r1", "c1"), tmp_path / "z.jsonl")


@pytest.mark.parametrize("tamper", ["outcome", "relabel", "drop", "header"])
def test_lineage_is_verified_against_the_journals_not_the_labels(tmp_path, tamper) -> None:
    work_dir, eval_set = _completed(tmp_path)
    out = tmp_path / "combined.jsonl"
    cb.combine(work_dir, eval_set, ("r1", "c1"), out)
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    c1 = next(r for r in lines[1:] if r["source_run"]["run_id"] == "c1")
    if tamper == "outcome":
        c1["outcome"]["latency_ms"] = 999.0
    elif tamper == "relabel":
        r1_hash = lines[0]["sources"][0]["journal_sha256"]
        c1["source_run"] = {"run_id": "r1", "journal_sha256": r1_hash}
        lines[0]["sources"][0]["results"] += 1
        lines[0]["sources"][1]["results"] -= 1
    elif tamper == "drop":
        lines.remove(c1)
        lines[0]["sources"][1]["results"] -= 1
    else:
        lines[0]["sources"][1]["journal_sha256"] = "0" * 64
        for r in lines[1:]:
            if r["source_run"]["run_id"] == "c1":
                r["source_run"]["journal_sha256"] = "0" * 64
    out.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    with pytest.raises(EvalSetError):
        cb.verify_lineage(work_dir, eval_set, out)


def test_lineage_must_be_complete_and_consistent_to_be_read(tmp_path) -> None:
    work_dir, eval_set = _completed(tmp_path)
    out = tmp_path / "combined.jsonl"
    cb.combine(work_dir, eval_set, ("r1", "c1"), out)
    lines = out.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    del record["source_run"]
    lines[1] = json.dumps(record)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(EvalSetError, match="every record"):
        evaluate(eval_set, work_dir / "raw", out)


def test_legacy_single_run_exports_are_still_accepted_without_lineage(tmp_path) -> None:
    work_dir, eval_set, _, _ = _source_run(tmp_path)
    out = tmp_path / "legacy.jsonl"
    rn.export_outcomes(work_dir, "r1", out)
    report, _ = evaluate(eval_set, work_dir / "raw", out)
    assert report["outcomes"]["lineage"] == {"present": False, "verified": False}
    assert report["run_quality"]["results"] == 20 and report["run_quality"]["breached"]
    assert "by_source_run" not in report["run_quality"]
    with pytest.raises(EvalSetError, match="no lineage"):
        cb.verify_lineage(work_dir, eval_set, out)


def test_the_cli_combines_and_verifies(tmp_path) -> None:
    from evals.listing_decisions.cli import main

    work_dir, _ = _completed(tmp_path)
    out = tmp_path / "cli.jsonl"
    assert (
        main(
            ["combine", "--set-dir", str(work_dir), "--run", "r1", "--run", "c1", "--out", str(out)]
        )
        == 0
    )
    report = tmp_path / "report.json"
    argv = [
        "evaluate", "--set", str(work_dir / "set.json"), "--raw-dir", str(work_dir / "raw"),
        "--outcomes", str(out), "--out", str(report), "--verify-lineage",
    ]  # fmt: skip
    assert main(argv) == 0
    assert json.loads(report.read_bytes())["outcomes"]["lineage"]["verified"] is True
    assert (
        main(
            [
                "combine",
                "--set-dir",
                str(work_dir),
                "--run",
                "r1",
                "--run",
                "r1",
                "--out",
                str(tmp_path / "d.jsonl"),
            ]
        )
        == 2
    )
    assert _journal(work_dir, "c1")[-1]["event"] == "complete"


@pytest.mark.parametrize("content", [b"", b"{not json\n", b'{"schema_version": "x"}\n'])
def test_verify_lineage_refuses_unreadable_files_cleanly(tmp_path, content) -> None:
    work_dir, eval_set = _completed(tmp_path)
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(content)
    with pytest.raises(EvalSetError):
        cb.verify_lineage(work_dir, eval_set, bad)


def test_combine_refuses_a_journal_item_that_is_not_in_the_set(tmp_path) -> None:
    work_dir, eval_set = _completed(tmp_path)
    path = work_dir / rn.RUNS_DIR / "c1" / rn.JOURNAL_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if json.loads(line)["event"] == "dispatch")
    event = json.loads(lines[first])
    old = event["item_id"]
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, "sp:not-in-set"), encoding="utf-8"
    )
    with pytest.raises(EvalSetError, match="not a dev item of the set"):
        cb.combine(work_dir, eval_set, ("r1", "c1"), tmp_path / "x.jsonl")
