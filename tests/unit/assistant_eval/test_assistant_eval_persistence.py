"""Milestone 17: saved API responses (`ar-1`), per-call telemetry, run binding and provenance,
prompt per-case persistence, human-review coverage, and run 001 staying unchanged.

Every provider here is scripted or an in-process fake; no request leaves the process.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from evals.assistant import __main__ as cli
from evals.assistant.annotations import (
    AnnotationApproval,
    AnnotationSet,
    CaseAnnotation,
    derived_cohort,
    eligibility_sha256,
)
from evals.assistant.contracts import case_sha256, file_sha256, load_cases
from evals.assistant.judgements import AnswerJudgement, summarize_judgements
from evals.assistant.persist import classify_status, load_responses
from evals.assistant.runner import RecordingProvider

from ecommerce_search.assistant.prompt import INSTRUCTIONS
from ecommerce_search.generation.provider import GenerationResult, GroundedDraft

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "evals/assistant/fixtures/synthetic-v1/cases.jsonl"
RUN_001 = ROOT / "evals/results/m17/abe-v1-live-001"
REVIEW = {"reviewer_handle": "ann-1", "reviewed_on": "2026-10-23", "decision": "approved"}
CAPTURE = {"search_version": "v3_reranked", "top_k": 5, "captured_at": "2026-10-23T10:00:00Z"}
CLEAN = {"code_commit": "c" * 40, "tracked_modified": [], "untracked_code": []}
PLACEHOLDER = "test-placeholder-key-never-sent"


def fixture(case_id):
    records = (json.loads(line) for line in FIXTURES.read_text("utf-8").splitlines())
    return next(r for r in records if r["case_id"] == case_id)


def eval_record(case_id):
    return {
        **fixture(case_id),
        "split": "eval",
        "authorship": "ai_authored",
        "author": "claude-code",
        "case_review": REVIEW,
        "evidence_source": "captured_v3",
        "evidence_capture": CAPTURE,
        "scripted": [],
    }


def eval_files(tmp_path, case_ids):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(eval_record(i)) + "\n" for i in case_ids), "utf-8")
    cases = load_cases(path)
    annotations = []
    for case in cases:
        annotation = CaseAnnotation(
            case_id=case.case_id,
            case_sha256=case_sha256(case),
            abstention_cohort=derived_cohort(case),
        )
        if annotation.abstention_cohort is not None:
            approval = AnnotationApproval(
                case_id=case.case_id,
                reviewer_handle="ann-1",
                reviewed_on="2026-10-23",
                eligibility_sha256=eligibility_sha256(annotation),
                recorded_from="test",
            )
            annotation = annotation.model_copy(update={"approval": approval})
        annotations.append(annotation)
    aset = AnnotationSet(
        annotation_version="aa-1", cases_sha256=file_sha256(path), annotations=annotations
    )
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(aset.model_dump_json(), "utf-8")
    return path, annotations_path


def settings():
    return SimpleNamespace(
        assistant_generation_model="fake-model",
        assistant_generation_timeout_seconds=20.0,
        assistant_generation_deadline_seconds=30.0,
        assistant_generation_max_output_tokens=1024,
        assistant_evidence_k=5,
        gemini_api_key=PLACEHOLDER,
    )


def answer(*ids):
    return GenerationResult(
        draft=GroundedDraft(
            outcome="answer",
            selected=tuple({"product_id": i, "relevant_facts": ("price",)} for i in ids),
        ),
        failure=None,
        input_tokens=500,
        output_tokens=40,
    )


class Fake:
    provider, model = "fake-live", "fake-model"

    def __init__(self, *results):
        self.results = list(results)

    def generate(self, request):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def live(tmp_path, case_ids, provider, run_id="test-run-002", out=None):
    cases, annotations = eval_files(tmp_path, case_ids)
    out = out or tmp_path / "out"
    argv = [
        "live",
        "--run-id",
        run_id,
        "--cases",
        str(cases),
        "--expected-sha256",
        file_sha256(cases),
        "--annotations",
        str(annotations),
        "--expected-annotations-sha256",
        file_sha256(annotations),
        "--max-requests",
        "10",
        "--min-interval-seconds",
        "0",
        "--out",
        str(out),
        "--confirm-live",
    ]
    code = cli.main(argv, lambda s: provider, lambda: CLEAN, settings)
    return code, out, cases


def test_live_saves_complete_responses_in_returned_order_bound_to_the_run(tmp_path):
    ids = ["grounded-dell-under-budget", "clarify-no-product-need"]
    code, out, cases_path = live(tmp_path, ids, Fake(answer("SYN-LAP-002", "SYN-LAP-001")))
    assert code == 1  # the fixture's own expectations differ; persistence is what is tested
    records = load_responses(out / "responses.jsonl")
    assert [r["case_id"] for r in records] == ids
    first = records[0]
    assert first["format"] == "ar-1" and first["run_id"] == "test-run-002"
    assert first["code_commit"] == "c" * 40
    assert first["cases_sha256"] == file_sha256(cases_path)
    assert first["case_sha256"] == case_sha256(load_cases(cases_path)[0])
    response = first["response"]
    assert [r["product_id"] for r in response["recommendations"]] == ["SYN-LAP-002", "SYN-LAP-001"]
    rec = response["recommendations"][0]
    assert rec["explanation"] and rec["facts"] and "constraint_checks" in rec
    assert response["notice"] and response["summary"] and response["evidence"]
    assert records[1]["response"]["outcome"] == "clarify"
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["run_id"] == "test-run-002" and report["code_commit"] == "c" * 40
    assert report["responses_sha256"] == file_sha256(out / "responses.jsonl")
    assert report["calls_sha256"] == file_sha256(out / "calls.jsonl")
    assert report["run_settings"]["gemini_api_key_configured"] is True
    for name in ("responses.jsonl", "calls.jsonl", "report.json", "report.md", "review-sheet.md"):
        data = (out / name).read_bytes()
        assert b"\r" not in data
        assert PLACEHOLDER.encode() not in data
        assert INSTRUCTIONS[:80].encode() not in data
    sheet = (out / "review-sheet.md").read_text("utf-8")
    assert "**Saved API responses**" in sheet and "not verified browser output" in sheet
    assert sheet.index("SYN-LAP-002") < sheet.index("SYN-LAP-001")


def test_calls_record_start_time_and_pacing_wait(tmp_path):
    code, out, _ = live(tmp_path, ["grounded-dell-under-budget"], Fake(answer("SYN-LAP-001")))
    (call,) = [json.loads(line) for line in (out / "calls.jsonl").read_text("utf-8").splitlines()]
    assert call["run_id"] == "test-run-002" and call["case_id"] == "grounded-dell-under-budget"
    assert call["seq"] == 1 and call["started_utc"] and call["pacing_wait_ms"] == 0.0
    waits = []
    recorder = RecordingProvider(
        Fake(answer("SYN-LAP-001"), answer("SYN-LAP-001")),
        min_interval_seconds=10,
        sleep=waits.append,
        clock=iter([0.0, 4.0, 10.0]).__next__,
    )
    recorder.generate(None)
    recorder.generate(None)
    assert waits == [6.0] and recorder.calls[1].pacing_wait_ms == 6000.0


def test_earlier_responses_survive_a_stop_and_a_crash(tmp_path):
    ids = ["grounded-dell-under-budget", "listing-clean-counterpart"]
    stopped = GenerationResult(
        draft=None, failure="rate_limited", input_tokens=None, output_tokens=None
    )
    code, out, _ = live(tmp_path, ids, Fake(answer("SYN-LAP-001"), stopped))
    records = load_responses(out / "responses.jsonl")
    assert [r["case_id"] for r in records] == ids
    assert records[1]["response"]["outcome"] == "generation_unavailable"
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["stopped"] == "provider_failure:rate_limited"
    crash_dir = tmp_path / "crash"
    crash_dir.mkdir()
    with pytest.raises(RuntimeError):
        live(crash_dir, ids, Fake(answer("SYN-LAP-001"), RuntimeError("boom")))
    saved = load_responses(crash_dir / "out" / "responses.jsonl")
    assert [r["case_id"] for r in saved] == ["grounded-dell-under-budget"]


def test_runs_are_never_merged_and_dirty_code_is_refused(tmp_path, capsys):
    out = tmp_path / "existing"
    out.mkdir()
    (out / "responses.jsonl").write_text("{}\n", "utf-8")
    code, _, _ = live(tmp_path, ["grounded-dell-under-budget"], Fake(), out=out)
    assert code == 2 and "never merged" in capsys.readouterr().err
    cases, annotations = eval_files(tmp_path, ["grounded-dell-under-budget"])
    dirty = {"code_commit": "c" * 40, "tracked_modified": ["evals/x.py"], "untracked_code": []}
    argv = [
        "live", "--run-id", "test-run-002", "--cases", str(cases),
        "--expected-sha256", file_sha256(cases), "--annotations", str(annotations),
        "--expected-annotations-sha256", file_sha256(annotations), "--max-requests", "2",
        "--min-interval-seconds", "0", "--out", str(tmp_path / "new"), "--confirm-live",
    ]  # fmt: skip
    assert cli.main(argv, lambda s: Fake(), lambda: dirty, settings) == 2
    assert "evaluation code differs" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["live", "--run-id", "Bad ID"])


def test_status_classification_flags_tracked_changes_and_importable_untracked_files():
    lines = [
        " M evals/assistant/report.py",
        "?? evals/assistant/new_module.py",
        "!! evals/assistant/ignored_plugin.py",
        "?? evals/results/m17/run/report.json",
        "!! evals/assistant/__pycache__/x.cpython-312.pyc",
        "A  tests/unit/new_test.py",
    ]
    assert classify_status(lines) == {
        "tracked_modified": ["evals/assistant/report.py", "tests/unit/new_test.py"],
        "untracked_code": ["evals/assistant/ignored_plugin.py", "evals/assistant/new_module.py"],
    }


def test_mocked_runs_with_out_also_persist(tmp_path):
    out = tmp_path / "mocked"
    assert cli.main(["mocked", "--out", str(out), "--run-id", "mocked-001"],
                    provenance_check=lambda: CLEAN) == 0  # fmt: skip
    records = load_responses(out / "responses.jsonl")
    assert len(records) == 20 and all(r["run_id"] == "mocked-001" for r in records)


def judgement(case_id, products=(), outcome="yes", misleading="no"):
    return AnswerJudgement.model_validate(
        {
            "schema_version": "aj-1",
            "report_sha256": "a" * 64,
            "case_id": case_id,
            "reviewer_handle": "ann-1",
            "judged_on": "2026-10-23",
            "outcome_appropriate": outcome,
            "products": [{"product_id": p, "fit": f} for p, f in products],
            "misleading": misleading,
        }
    )


def test_coverage_reports_missing_unsure_and_product_level_counts():
    report = {
        "results": [
            {"case_id": "a", "error": None, "recommended": ["P1", "P2"]},
            {"case_id": "b", "error": None, "recommended": ["P3"]},
            {"case_id": "c", "error": None, "recommended": []},
            {"case_id": "d", "error": "cap", "recommended": []},
        ],
        "diagnostics": [{"case_id": "c"}],
        "not_run": ["e"],
    }
    judged = (judgement("a", (("P1", "fits"), ("P2", "unsure")), misleading="unsure"),)
    coverage = summarize_judgements(judged, report)["coverage"]
    assert coverage["responses_scored"] == {"recorded": 2, "judged": 1, "missing": ["b"]}
    assert coverage["responses_diagnostic"] == {"recorded": 1, "judged": 0, "missing": ["c"]}
    assert coverage["not_recorded"] == ["d", "e"]
    assert coverage["products"] == {
        "recorded": 3,
        "judged": 2,
        "unsure": 1,
        "in_missing_judgements": 1,
    }
    assert coverage["unsure"] == {"outcome_appropriate": 0, "misleading": 1, "product_fit": 1}


def test_run_001_artifacts_are_unchanged():
    readme = (RUN_001 / "README.md").read_text("utf-8")
    for name in ("report.json", "report.md", "calls.jsonl", "calls.raw.jsonl", "run_script.py"):
        digest = hashlib.sha256((RUN_001 / name).read_bytes()).hexdigest()
        assert f"`{name}` | `{digest}`" in readme
