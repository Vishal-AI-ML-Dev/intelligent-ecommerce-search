"""Milestone 17: `--only` subset runs. The whole frozen file and its annotations are validated
first; the selection runs in file order and is recorded; missing same-run counterparts are
reported as unavailable without dropping target checks; subset reports apply no full-dataset
threshold; full runs behave as before.

Every provider is scripted or a fake; nothing leaves the process.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from evals.assistant import __main__ as cli
from evals.assistant.annotations import (
    AnnotationApproval,
    AnnotationSet,
    CaseAnnotation,
    derived_cohort,
    eligibility_sha256,
)
from evals.assistant.contracts import case_sha256, file_sha256, load_cases
from evals.assistant.judgements import AnswerJudgement
from evals.assistant.persist import load_responses
from evals.assistant.report import build_report
from evals.assistant.runner import run_cases

FIXTURES = Path(__file__).parents[3] / "evals/assistant/fixtures/synthetic-v1/cases.jsonl"
CLEAN = {"code_commit": "c" * 40, "tracked_modified": [], "untracked_code": []}
INJECTED = "listing-injection-ignored-stays-visible"
CLEAN_CASE = "listing-clean-counterpart"


def select(tmp_path, *ids, name="only.txt"):
    path = tmp_path / name
    path.write_text("".join(f"{i}\n" for i in ids), "utf-8")
    return path


def mocked(tmp_path, only, out="out"):
    argv = ["mocked", "--only", str(only), "--out", str(tmp_path / out), "--run-id", "subset-01"]
    return cli.main(argv, provenance_check=lambda: CLEAN)


def test_selection_runs_in_file_order_and_is_recorded(tmp_path):
    only = select(tmp_path, "model-abstains", "clarify-no-product-need")  # reverse of file order
    assert mocked(tmp_path, only) == 0
    out = tmp_path / "out"
    report = json.loads((out / "report.json").read_text("utf-8"))
    selection = report["case_selection"]
    assert selection["case_ids"] == ["clarify-no-product-need", "model-abstains"]
    assert selection["selection_sha256"] == file_sha256(only)
    assert selection["parent_cases"] == 20 and selection["parent_cases_sha256"] == file_sha256(
        FIXTURES
    )
    assert [r["case_id"] for r in report["results"]] == selection["case_ids"]
    records = load_responses(out / "responses.jsonl")
    assert all(r["selection_sha256"] == file_sha256(only) for r in records)
    assert all(r["cases_sha256"] == file_sha256(FIXTURES) for r in records)
    markdown = (out / "report.md").read_text("utf-8")
    assert "not converted or applied to this subset" in markdown
    assert "no full-dataset verdict" in markdown


def test_empty_unknown_and_duplicate_selections_are_refused(tmp_path, capsys):
    for ids, message in (
        ((), "empty"),
        (("model-abstains", "model-abstains"), "more than once"),
        (("model-abstains", "no-such-case"), "unknown cases"),
    ):
        only = select(tmp_path, *ids, name=f"only-{len(ids)}-{message[:4]}.txt")
        assert mocked(tmp_path, only, out=f"out-{message[:4]}") == 2
        assert message in capsys.readouterr().err


def test_an_injected_case_selected_alone_keeps_its_target_check(tmp_path):
    only = select(tmp_path, INJECTED)  # its clean counterpart is validated but not run
    mocked(tmp_path, only)
    report = json.loads((tmp_path / "out" / "report.json").read_text("utf-8"))
    (pair,) = report["injection_pairs"]
    assert pair["same_run_comparison"] == "unavailable"
    assert pair["clean"] is None and pair["behaviour_changed"] is None
    changed = report["metrics"]["injection_behaviour_changed"]
    assert (changed["numerator"], changed["denominator"]) == (0, 0)
    assert "unavailable" in (tmp_path / "out" / "report.md").read_text("utf-8")
    # with annotations the target check still counts the injected case
    cases = load_cases(FIXTURES)
    run = run_cases(tuple(c for c in cases if c.case_id == INJECTED))
    annotations = {
        c.case_id: CaseAnnotation(
            case_id=c.case_id,
            case_sha256=case_sha256(c),
            abstention_cohort=derived_cohort(c),
            injection_target="SYN-LAP-012" if c.case_id == INJECTED else None,
        )
        for c in cases
    }
    full = build_report(run, cases_sha256="0" * 64, annotations=annotations)
    target = full["metrics"]["injection_target_recommended"]
    assert target["denominator"] == 1 and full["injection_pairs"][0]["target"] == "SYN-LAP-012"


def test_full_runs_are_unchanged(tmp_path):
    out = tmp_path / "full"
    assert cli.main(["mocked", "--out", str(out)], provenance_check=lambda: CLEAN) == 0
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["case_selection"] is None and report["cases"] == 20
    assert len(report["injection_pairs"]) == 3
    assert all(p["same_run_comparison"] == "available" for p in report["injection_pairs"])
    assert report["metrics"]["injection_behaviour_changed"]["denominator"] == 3
    assert all("selection_sha256" not in r for r in load_responses(out / "responses.jsonl"))
    assert "not converted or applied" not in (out / "report.md").read_text("utf-8")


def eval_files(tmp_path, case_ids):
    """Approved test annotations for a small eval file (test data, not owner approvals)."""
    records = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in FIXTURES.read_text("utf-8").splitlines()
    }
    review = {"reviewer_handle": "ann-1", "reviewed_on": "2026-10-23", "decision": "approved"}
    capture = {"search_version": "v3", "top_k": 5, "captured_at": "2026-10-23T10:00:00Z"}
    lines = []
    for case_id in case_ids:
        source = "captured_v3_injected" if case_id == INJECTED else "captured_v3"
        record = records[case_id] | {
            "split": "eval", "authorship": "ai_authored", "author": "claude-code",
            "case_review": review, "evidence_source": source, "evidence_capture": capture,
            "scripted": [],
        }  # fmt: skip
        lines.append(json.dumps(record))
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("\n".join(lines) + "\n", "utf-8")
    cases = load_cases(cases_path)
    annotations = []
    for case in cases:
        annotation = CaseAnnotation(
            case_id=case.case_id,
            case_sha256=case_sha256(case),
            abstention_cohort=derived_cohort(case),
            injection_target="SYN-LAP-012" if case.case_id == INJECTED else None,
        )
        if annotation.injection_target or annotation.abstention_cohort:
            annotation = annotation.model_copy(
                update={
                    "approval": AnnotationApproval(
                        case_id=case.case_id,
                        reviewer_handle="ann-1",
                        reviewed_on="2026-10-23",
                        eligibility_sha256=eligibility_sha256(annotation),
                        recorded_from="test",
                    )
                }
            )
        annotations.append(annotation)
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(
        AnnotationSet(
            annotation_version="aa-1",
            cases_sha256=file_sha256(cases_path),
            annotations=annotations,
        ).model_dump_json(),
        "utf-8",
    )
    return cases_path, annotations_path


def test_live_dry_run_validates_the_whole_file_then_reports_the_selection(tmp_path, capsys):
    cases, annotations = eval_files(tmp_path, ["grounded-dell-under-budget", CLEAN_CASE, INJECTED])
    only = select(tmp_path, INJECTED)
    argv = [
        "live", "--run-id", "subset-02", "--cases", str(cases),
        "--expected-sha256", file_sha256(cases), "--annotations", str(annotations),
        "--expected-annotations-sha256", file_sha256(annotations), "--only", str(only),
        "--max-requests", "2", "--min-interval-seconds", "10", "--out", str(tmp_path / "o"),
    ]  # fmt: skip
    settings = SimpleNamespace(
        assistant_generation_model="m", assistant_generation_timeout_seconds=20.0,
        assistant_generation_deadline_seconds=30.0, assistant_generation_max_output_tokens=1024,
        assistant_evidence_k=5, gemini_api_key=None,
    )  # fmt: skip
    assert cli.main(argv, lambda s: None, lambda: CLEAN, lambda: settings) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["cases"] == 1 and plan["case_selection"]["case_ids"] == [INJECTED]
    assert plan["case_selection"]["selection_sha256"] == file_sha256(only)
    assert not (tmp_path / "o").exists()
    # a wrong annotation hash is still refused although the selection itself is valid
    wrong = [*argv]
    wrong[wrong.index("--expected-annotations-sha256") + 1] = "0" * 64
    assert cli.main(wrong, lambda s: None, lambda: CLEAN, lambda: settings) == 2


def test_human_review_uses_the_selected_population(tmp_path, capsys):
    cases, annotations = eval_files(tmp_path, ["grounded-dell-under-budget", CLEAN_CASE, INJECTED])
    report = {
        "stopped": None,
        "not_run": [],
        "cases_sha256": file_sha256(cases),
        "annotations_sha256": file_sha256(annotations),
        "case_selection": {"case_ids": [INJECTED]},
        "results": [{"case_id": INJECTED, "error": None, "recommended": []}],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), "utf-8")
    judgement = AnswerJudgement.model_validate(
        {
            "schema_version": "aj-1", "report_sha256": file_sha256(report_path),
            "case_id": INJECTED, "reviewer_handle": "ann-1", "judged_on": "2026-10-23",
            "outcome_appropriate": "yes", "products": [], "misleading": "no",
        }
    )  # fmt: skip
    judgements = tmp_path / "j.jsonl"
    judgements.write_text(judgement.model_dump_json() + "\n", "utf-8")
    argv = ["judgements", "--report", str(report_path), "--judgements", str(judgements),
            "--annotations", str(annotations)]  # fmt: skip
    assert cli.main(argv) == 0
    views = json.loads(capsys.readouterr().out)["views"]
    assert views["scored_excluding_contract"]["planned_cases"] == 1
    assert views["scored_excluding_contract"]["processed"] == 1
