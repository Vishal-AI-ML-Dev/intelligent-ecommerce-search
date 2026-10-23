"""Milestone 17: eval-split rules, counterparts, the live runner (with a fake provider only),
the live CLI's refusals and dry run, and the human-judgement format.

No test sends a provider request: every "live" run here uses an in-process fake.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from evals.assistant import __main__ as cli
from evals.assistant.__main__ import DEFAULT_CASES, live_refusal
from evals.assistant.annotations import (
    AnnotationApproval,
    AnnotationSet,
    CaseAnnotation,
    derived_cohort,
    eligibility_sha256,
)
from evals.assistant.contracts import EvalCase, case_sha256, file_sha256, load_cases
from evals.assistant.judgements import load_judgements, summarize_judgements
from evals.assistant.report import BILLING, build_report
from evals.assistant.runner import run_live
from pydantic import ValidationError

from ecommerce_search.generation.provider import GenerationResult, GroundedDraft

FIXTURE_LINES = DEFAULT_CASES.read_text("utf-8").splitlines()
REVIEW = {"reviewer_handle": "ann-1", "reviewed_on": "2026-10-23", "decision": "approved"}
CAPTURE = {"search_version": "v3_reranked", "top_k": 10, "captured_at": "2026-10-23T10:00:00Z"}


def fixture(case_id):
    records = (json.loads(line) for line in FIXTURE_LINES)
    return next(r for r in records if r["case_id"] == case_id)


def as_eval(record, **update):
    record = {
        **record,
        "split": "eval",
        "authorship": "ai_authored",
        "author": "claude-code",
        "case_review": REVIEW,
        "evidence_source": "captured_v3",
        "evidence_capture": CAPTURE,
        "scripted": [],
    }
    record.update(update)
    return record


def write(tmp_path, records):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    return path


EVAL_RECORDS = [
    as_eval(fixture("grounded-dell-under-budget")),
    as_eval(fixture("clarify-no-product-need")),
    as_eval(fixture("listing-clean-counterpart")),
    as_eval(
        fixture("listing-injection-ignored-stays-visible"), evidence_source="captured_v3_injected"
    ),
]


class FakeLive:
    provider = "fake-live"
    model = "fake-model"

    def __init__(self, *results):
        self.results = list(results)
        self.sent = 0

    def generate(self, request):
        self.sent += 1
        return self.results.pop(0)


def draft(*ids):
    return GenerationResult(
        draft=GroundedDraft(
            outcome="answer",
            selected=tuple({"product_id": i, "relevant_facts": ("price",)} for i in ids),
        ),
        failure=None,
        input_tokens=900,
        output_tokens=80,
    )


def failure(code):
    return GenerationResult(draft=None, failure=code, input_tokens=None, output_tokens=None)


# ---- split, evidence and counterpart rules ------------------------------------------------------


def test_eval_cases_refuse_fixture_authorship_and_synthetic_evidence():
    with pytest.raises(ValidationError):
        EvalCase.model_validate(
            as_eval(
                fixture("model-abstains"),
                authorship="synthetic_test_fixture",
                author="x",
                case_review=None,
            )
        )
    with pytest.raises(ValidationError):
        EvalCase.model_validate(
            as_eval(fixture("model-abstains"), evidence_source="synthetic", evidence_capture=None)
        )
    with pytest.raises(ValidationError):  # captured evidence must say how it was captured
        EvalCase.model_validate(as_eval(fixture("model-abstains"), evidence_capture=None))


def test_abstain_and_clarify_expectations_must_name_reasons():
    record = fixture("model-abstains")
    with pytest.raises(ValidationError):
        EvalCase.model_validate({**record, "expect": {**record["expect"], "abstain_reasons": []}})


def test_a_case_file_holds_one_split(tmp_path):
    path = write(tmp_path, [fixture("model-abstains"), as_eval(fixture("clarify-no-product-need"))])
    with pytest.raises(ValueError, match="one split"):
        load_cases(path)


def test_eval_counterparts_load(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS))
    assert cases[3].counterpart.clean_case_id == "listing-clean-counterpart"


def test_a_listing_injection_may_change_free_text_only(tmp_path):
    injected = as_eval(
        fixture("listing-injection-ignored-stays-visible"), evidence_source="captured_v3_injected"
    )
    injected["evidence"][1] = {**injected["evidence"][1], "price": "1.00"}
    with pytest.raises(ValueError, match="free text only"):
        load_cases(write(tmp_path, [*EVAL_RECORDS[:3], injected]))


def test_a_message_injection_must_keep_the_evidence(tmp_path):
    injected = fixture("message-injection-followed-is-rejected")
    injected["evidence"] = injected["evidence"][:1]
    clean = fixture("message-clean-counterpart")
    with pytest.raises(ValueError, match="keep the evidence"):
        load_cases(write(tmp_path, [clean, injected]))


def test_an_injected_case_needs_its_clean_counterpart(tmp_path):
    with pytest.raises(ValueError, match="clean counterpart"):
        load_cases(write(tmp_path, [fixture("message-injection-followed-is-rejected")]))


# ---- live runner with a fake provider -----------------------------------------------------------


def test_live_run_reports_workflow_and_provider_latency_tokens_and_cost_separately(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS))
    run = run_live(
        cases,
        FakeLive(draft("SYN-LAP-001"), draft("SYN-LAP-001"), draft("SYN-LAP-001")),
        max_requests=10,
        min_interval_seconds=0,
    )
    prices = {"input_usd_per_mtok": "0.10", "output_usd_per_mtok": "0.40", "price_source": "test"}
    report = build_report(
        run, cases_sha256="a" * 64, prices=prices, app_limits={"max_requests": 10}
    )
    assert report["mode"] == "live" and report["stopped"] is None
    latency = report["latency"]
    assert latency["provider_call_ms"]["n"] == 3  # clarify case: no call
    assert latency["workflow_total_ms"]["n"] == 4
    assert report["tokens"]["input_tokens_reported"] == 2700
    assert report["cost"]["estimated_usd"] == str(
        (Decimal(2700) * Decimal("0.10") + Decimal(240) * Decimal("0.40")) / Decimal(1_000_000)
    )
    assert report["cost"]["billing"] == BILLING
    assert report["provider_quota"].startswith("unknown")
    pair = report["injection_pairs"][0]
    assert pair["behaviour_changed"] is False and pair["injected_product_recommended"] is False


def test_changed_behaviour_is_reported_even_without_selecting_the_injected_item(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS))
    # The clean case answers SYN-LAP-001; the injected case adds the injected record.
    live = FakeLive(draft("SYN-LAP-001"), draft("SYN-LAP-001"), draft("SYN-LAP-001", "SYN-LAP-012"))
    report = build_report(
        run_live(cases, live, max_requests=10, min_interval_seconds=0), cases_sha256="a" * 64
    )
    pair = report["injection_pairs"][0]
    assert pair["behaviour_changed"] is True and pair["injected_product_recommended"] is True
    assert report["metrics"]["injection_behaviour_changed"]["numerator"] == 1


def test_missing_prices_or_tokens_leave_cost_unknown(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS[:1]))
    unknown = GenerationResult(
        draft=draft("SYN-LAP-001").draft, failure=None, input_tokens=None, output_tokens=5
    )
    run = run_live(cases, FakeLive(unknown), max_requests=5, min_interval_seconds=0)
    prices = {"input_usd_per_mtok": "1", "output_usd_per_mtok": "1", "price_source": "test"}
    assert (
        build_report(run, cases_sha256="a" * 64, prices=prices)["cost"]["status"] == "unknown_usage"
    )
    assert (
        build_report(run, cases_sha256="a" * 64)["cost"]["status"]
        == "unknown_pricing_not_configured"
    )


def test_request_cap_stops_before_sending_and_lists_cases_not_run(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS))
    live = FakeLive(draft("SYN-LAP-999"), draft("SYN-LAP-999"))  # forces a repair
    run = run_live(cases, live, max_requests=1, min_interval_seconds=0)
    assert live.sent == 1 and run.stopped == "request_cap_reached"
    assert run.results[0].error and len(run.not_run) == 3


@pytest.mark.parametrize("code", ["rate_limited", "auth_error", "server_error"])
def test_quota_or_access_failure_stops_the_run(tmp_path, code):
    cases = load_cases(write(tmp_path, EVAL_RECORDS))
    live = FakeLive(failure(code))
    run = run_live(cases, live, max_requests=10, min_interval_seconds=0)
    assert live.sent == 1 and run.stopped == f"provider_failure:{code}"
    assert run.results[0].response.outcome == "generation_unavailable"
    assert run.not_run == (
        "clarify-no-product-need",
        "listing-clean-counterpart",
        "listing-injection-ignored-stays-visible",
    )


def test_minimum_interval_is_enforced_between_requests(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS[2:]))
    waits = []
    run_live(
        cases,
        FakeLive(draft("SYN-LAP-001"), draft("SYN-LAP-001")),
        max_requests=5,
        min_interval_seconds=30,
        sleep=waits.append,
    )
    assert len(waits) == 1 and 29 < waits[0] <= 30


# ---- live CLI gates (no provider is ever built in these tests) ----------------------------------


def _refusing_factory(settings):
    raise AssertionError("no provider may be built")


CLEAN = {"code_commit": "c" * 40, "tracked_modified": [], "untracked_code": []}


def _clean():
    return CLEAN


def _settings():
    """Stand-in for Settings in hermetic tests; the key is a placeholder, never sent."""
    return SimpleNamespace(
        assistant_generation_model="test-model",
        assistant_generation_timeout_seconds=20.0,
        assistant_generation_deadline_seconds=30.0,
        assistant_generation_max_output_tokens=1024,
        assistant_evidence_k=5,
        gemini_api_key=None,
    )


def test_live_refuses_dev_cases_unreviewed_cases_and_unapproved_files(tmp_path):
    dev = load_cases(DEFAULT_CASES)
    assert live_refusal(dev, "x", "x") == "live runs use eval cases only"
    unreviewed = load_cases(write(tmp_path, [as_eval(fixture("model-abstains"), case_review=None)]))
    assert "case review" in live_refusal(unreviewed, "x", "x")
    assert "SHA-256" in live_refusal(unreviewed, "x", "y")


def write_annotations(tmp_path, path, approved=True):
    """Test-built `aa-1` annotations (approvals here are test data, not owner approvals)."""
    cases = load_cases(path)
    annotations = []
    for case in cases:
        target = None if case.counterpart is None else case.counterpart.injected_product_ids[0]
        annotation = CaseAnnotation(
            case_id=case.case_id,
            case_sha256=case_sha256(case),
            abstention_cohort=derived_cohort(case),
            injection_target=target,
        )
        if approved and target is not None:
            approval = AnnotationApproval(
                case_id=case.case_id,
                reviewer_handle="ann-1",
                reviewed_on="2026-10-23",
                eligibility_sha256=eligibility_sha256(annotation),
                recorded_from="test",
            )
            annotation = annotation.model_copy(update={"approval": approval})
        annotations.append(annotation)
    out = tmp_path / "annotations.json"
    aset = AnnotationSet(
        annotation_version="aa-1", cases_sha256=file_sha256(path), annotations=annotations
    )
    out.write_text(aset.model_dump_json(), "utf-8")
    return out


def test_live_dry_run_and_refusals_send_nothing(tmp_path, capsys):
    path = write(tmp_path, EVAL_RECORDS)
    annotations = write_annotations(tmp_path, path)
    base = [
        "live",
        "--run-id",
        "test-run-001",
        "--cases",
        str(path),
        "--annotations",
        str(annotations),
        "--expected-annotations-sha256",
        file_sha256(annotations),
        "--max-requests",
        "4",
        "--min-interval-seconds",
        "10",
        "--out",
        str(tmp_path / "out"),
    ]
    assert (
        cli.main(
            [*base, "--expected-sha256", "0" * 64, "--confirm-live"],
            _refusing_factory,
            _clean,
            _settings,
        )
        == 2
    )
    assert (
        cli.main(
            [*base, "--expected-sha256", file_sha256(path)], _refusing_factory, _clean, _settings
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True and plan["max_requests"] == 4
    priced = [
        *base,
        "--expected-sha256",
        file_sha256(path),
        "--price-source",
        "x",
        "--confirm-live",
    ]
    assert cli.main(priced, _refusing_factory, _clean, _settings) == 2


def test_live_refuses_mismatched_or_unapproved_annotations(tmp_path, capsys):
    path = write(tmp_path, EVAL_RECORDS)
    approved = write_annotations(tmp_path, path)
    sha = file_sha256(approved)
    base = [
        "live",
        "--run-id",
        "test-run-001",
        "--cases",
        str(path),
        "--expected-sha256",
        file_sha256(path),
    ]
    limits = ["--max-requests", "4", "--min-interval-seconds", "10", "--out", str(tmp_path)]
    wrong_sha = [*base, "--annotations", str(approved), "--expected-annotations-sha256", "0" * 64]
    assert cli.main([*wrong_sha, *limits], _refusing_factory, _clean, _settings) == 2
    unapproved = write_annotations(tmp_path, path, approved=False)
    pending = [
        *base,
        "--annotations",
        str(unapproved),
        "--expected-annotations-sha256",
        file_sha256(unapproved),
    ]
    assert cli.main([*pending, *limits], _refusing_factory, _clean, _settings) == 2
    assert "not approved" in capsys.readouterr().err
    assert sha != file_sha256(unapproved)


# ---- human judgements ---------------------------------------------------------------------------


def _report(tmp_path):
    cases = load_cases(write(tmp_path, EVAL_RECORDS[:2]))
    run = run_live(cases, FakeLive(draft("SYN-LAP-001")), max_requests=5, min_interval_seconds=0)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(build_report(run, cases_sha256="a" * 64)), "utf-8")
    return path


def _judgement(sha, **update):
    record = {
        "schema_version": "aj-1",
        "report_sha256": sha,
        "case_id": "grounded-dell-under-budget",
        "reviewer_handle": "ann-1",
        "judged_on": "2026-10-24",
        "outcome_appropriate": "yes",
        "products": [{"product_id": "SYN-LAP-001", "fit": "fits"}],
        "misleading": "no",
    }
    record.update(update)
    return record


def test_judgements_bind_to_the_report_and_its_recommendations(tmp_path):
    report_path = _report(tmp_path)
    report, sha = json.loads(report_path.read_text("utf-8")), file_sha256(report_path)
    path = tmp_path / "judgements.jsonl"
    for bad, message in [
        (_judgement("b" * 64), "different report"),
        (_judgement(sha, products=[]), "exactly the recommended"),
        (_judgement(sha, case_id="nope"), "no recorded response"),
    ]:
        path.write_text(json.dumps(bad) + "\n", "utf-8")
        with pytest.raises(ValueError, match=message):
            load_judgements(path, report, sha)
    path.write_text(json.dumps(_judgement(sha, reviewer_handle="claude")) + "\n", "utf-8")
    with pytest.raises(ValidationError):
        load_judgements(path, report, sha)


def test_judgement_summary_excludes_unsure_and_claims_no_agreement(tmp_path):
    report_path = _report(tmp_path)
    report, sha = json.loads(report_path.read_text("utf-8")), file_sha256(report_path)
    path = tmp_path / "judgements.jsonl"
    rows = [
        _judgement(sha),
        _judgement(sha, case_id="clarify-no-product-need", products=[], misleading="unsure"),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    summary = summarize_judgements(load_judgements(path, report, sha))
    assert summary["agreement"] == "not claimed: one reviewer"
    assert summary["faithfulness_human"]["denominator"] == 1
    assert summary["faithfulness_human"]["unsure"] == 1
    assert summary["answer_relevance_human"]["numerator"] == 1
