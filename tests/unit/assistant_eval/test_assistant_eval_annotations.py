"""Milestone 17: case annotations `aa-1` (fail-closed loading, approvals bound to eligibility
content) and the annotation-aware report (diagnostics, contract conformance, predeclared
abstention cohorts, explicit injection targets).

Approvals here are test data built by the test, not owner approvals. No provider is called.
"""

import json
from pathlib import Path

import pytest
from evals.assistant import __main__ as cli
from evals.assistant.annotations import (
    AnnotationApproval,
    AnnotationError,
    AnnotationSet,
    CaseAnnotation,
    affects_eligibility,
    check_annotations,
    derived_cohort,
    eligibility_sha256,
    load_annotations,
)
from evals.assistant.contracts import case_sha256, file_sha256, load_cases
from evals.assistant.report import PAIR_CAVEAT, build_report, render_markdown
from evals.assistant.runner import run_cases

FIXTURES = Path(__file__).parents[3] / "evals/assistant/fixtures/synthetic-v1/cases.jsonl"
DRAFT_DIR = Path(__file__).parents[3] / "evals/assistant/drafts/abe-v1"
SUBSET = (
    "clarify-no-product-need",
    "clarify-bare-capacity",
    "abstain-no-matching-products",
    "model-abstains",
    "grounded-dell-under-budget",
    "listing-clean-counterpart",
    "listing-injection-ignored-stays-visible",
)
TARGETS = {"listing-injection-ignored-stays-visible": "SYN-LAP-012"}


def subset(tmp_path, update=None):
    records = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in FIXTURES.read_text("utf-8").splitlines()
    }
    chosen = [records[i] for i in SUBSET]
    for record in chosen:
        record["expect"].update((update or {}).get(record["case_id"], {}))
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in chosen), "utf-8")
    return load_cases(path), file_sha256(path)


def approve(annotation):
    if not affects_eligibility(annotation):
        return annotation
    approval = AnnotationApproval(
        case_id=annotation.case_id,
        reviewer_handle="ann-1",
        reviewed_on="2026-10-23",
        eligibility_sha256=eligibility_sha256(annotation),
        recorded_from="test",
    )
    return annotation.model_copy(update={"approval": approval})


def annotation_set(cases, sha, classes=None, approved=True):
    annotations = []
    for case in cases:
        fields = (classes or {}).get(case.case_id, {})
        diagnostic = fields.get("report_class") == "known_limitation_diagnostic"
        annotation = CaseAnnotation(
            case_id=case.case_id,
            case_sha256=case_sha256(case),
            abstention_cohort=None if diagnostic else derived_cohort(case),
            injection_target=TARGETS.get(case.case_id),
            **fields,
        )
        annotations.append(approve(annotation) if approved else annotation)
    return AnnotationSet(annotation_version="aa-1", cases_sha256=sha, annotations=annotations)


def replace(aset, case_id, **update):
    return aset.model_copy(
        update={
            "annotations": tuple(
                a.model_copy(update=update) if a.case_id == case_id else a for a in aset.annotations
            )
        }
    )


def put(aset, annotation):
    return aset.model_copy(
        update={
            "annotations": tuple(
                annotation if a.case_id == annotation.case_id else a for a in aset.annotations
            )
        }
    )


def get(aset, case_id):
    return next(a for a in aset.annotations if a.case_id == case_id)


def test_a_complete_approved_set_loads(tmp_path):
    cases, sha = subset(tmp_path)
    by_id = check_annotations(annotation_set(cases, sha), cases, sha)
    assert by_id["abstain-no-matching-products"].abstention_cohort == "deterministic"
    assert by_id["model-abstains"].abstention_cohort == "model"
    assert by_id["grounded-dell-under-budget"].abstention_cohort is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda a, c, s: a.model_copy(update={"cases_sha256": "0" * 64}), "different case file"),
        (
            lambda a, c, s: a.model_copy(update={"annotations": a.annotations[1:]}),
            "annotations missing",
        ),
        (
            lambda a, c, s: replace(a, "model-abstains", case_sha256="0" * 64),
            "different case content",
        ),
        (
            lambda a, c, s: replace(
                a, "listing-injection-ignored-stays-visible", injection_target=None, approval=None
            ),
            "injection target is required",
        ),
        (
            lambda a, c, s: replace(
                a, "grounded-dell-under-budget", report_class=None, injection_target="SYN-LAP-001"
            ),
            "required for, and only for",
        ),
    ],
)
def test_structural_mismatches_fail_closed(tmp_path, mutate, message):
    cases, sha = subset(tmp_path)
    with pytest.raises(AnnotationError, match=message):
        check_annotations(mutate(annotation_set(cases, sha), cases, sha), cases, sha)


def test_cohorts_come_from_the_predeclared_expectation_and_evidence(tmp_path):
    cases, sha = subset(tmp_path)
    aset = annotation_set(cases, sha)
    wrong = get(aset, "model-abstains").model_copy(
        update={"abstention_cohort": "deterministic", "approval": None}
    )
    with pytest.raises(AnnotationError, match="abstention cohort must be model"):
        check_annotations(put(aset, approve(wrong)), cases, sha)


def test_a_target_must_be_explicit_forbidden_in_evidence_and_injected(tmp_path):
    cases, sha = subset(tmp_path)
    aset = annotation_set(cases, sha)
    case_id = "listing-injection-ignored-stays-visible"
    # SYN-LAP-999 is forbidden but not in the evidence; SYN-LAP-011 is in the evidence but not
    # forbidden. Neither is assumed to be the target.
    for target, message in (
        ("SYN-LAP-999", "in the frozen evidence"),
        ("SYN-LAP-011", "must be a forbidden recommendation"),
    ):
        changed = get(aset, case_id).model_copy(
            update={"injection_target": target, "approval": None}
        )
        with pytest.raises(AnnotationError, match=message):
            check_annotations(put(aset, approve(changed)), cases, sha)


def test_eligibility_annotations_need_a_matching_approval(tmp_path):
    cases, sha = subset(tmp_path)
    with pytest.raises(AnnotationError, match="not approved"):
        check_annotations(annotation_set(cases, sha, approved=False), cases, sha)
    aset = annotation_set(cases, sha)
    # model-abstains carries an approval of its cohort; changing its class makes it stale
    stale = get(aset, "model-abstains").model_copy(update={"report_class": "contract_limitation"})
    with pytest.raises(AnnotationError, match="approval does not match"):
        check_annotations(put(aset, stale), cases, sha)
    # informational fields do not need approval and do not invalidate one
    noted = get(aset, "model-abstains").model_copy(update={"review_notes": "note"})
    check_annotations(put(aset, noted), cases, sha)


def test_unreadable_annotations_fail_closed(tmp_path):
    cases, sha = subset(tmp_path)
    with pytest.raises(AnnotationError, match="unreadable"):
        load_annotations(tmp_path / "missing.json", cases, sha)


def report_for(tmp_path, update=None):
    cases, sha = subset(tmp_path, update)
    classes = {
        "clarify-bare-capacity": {"report_class": "contract_limitation"},
        "grounded-dell-under-budget": {
            "report_class": "known_limitation_diagnostic",
            "diagnostic": "unnecessary_clarification",
            "diagnostic_note": "test diagnostic",
        },
    }
    aset = annotation_set(cases, sha, classes)
    annotations = check_annotations(aset, cases, sha)
    return build_report(run_cases(cases), cases_sha256=sha, annotations=annotations)


def test_report_separates_diagnostics_and_contract_cases_and_lists_exclusions(tmp_path):
    report = report_for(tmp_path)
    assert report["cases"] == 7 and report["cases_scored"] == 6
    assert report["cases_contract"] == 1 and report["cases_diagnostic"] == 1
    assert report["cases_passed"] == 6
    assert (report["cases_quality_passed"], report["cases_quality_scored"]) == (5, 5)
    header = render_markdown(report)
    assert "Cases: 7 total; 6 scored, of which 1 contract-conformance; 1 diagnostic" in header
    assert "Passed: 6/6 scored; 5/5 excluding contract cases." in header
    (diagnostic,) = report["diagnostics"]
    assert diagnostic["case_id"] == "grounded-dell-under-budget"
    assert diagnostic["observed_outcome"] == "answer"
    assert diagnostic["unnecessary_clarification_observed"] is False
    metrics = report["metrics"]
    assert (
        metrics["clarification_accuracy"]["numerator"],
        metrics["clarification_accuracy"]["denominator"],
    ) == (1, 1)
    excluded = {e["case_id"] for e in metrics["clarification_accuracy"]["excluded"]}
    assert excluded == {"clarify-bare-capacity", "grounded-dell-under-budget"}
    assert (
        metrics["contract_conformance"]["numerator"],
        metrics["contract_conformance"]["denominator"],
    ) == (1, 1)
    assert all("grounded-dell-under-budget" not in f["case_id"] for f in report["failures"])
    markdown = render_markdown(report)
    assert PAIR_CAVEAT in markdown and "## Diagnostics (not scored)" in markdown


def test_cohort_failures_stay_in_their_denominator(tmp_path):
    # model-abstains abstains with model_abstained; expecting only no_matching_products fails it.
    report = report_for(tmp_path, {"model-abstains": {"abstain_reasons": ["no_matching_products"]}})
    metrics = report["metrics"]
    model = metrics["abstention_accuracy_model_cohort"]
    deterministic = metrics["abstention_accuracy_deterministic_cohort"]
    assert (model["numerator"], model["denominator"]) == (0, 1)
    assert (deterministic["numerator"], deterministic["denominator"]) == (1, 1)
    assert metrics["abstention_accuracy"]["denominator"] == 2


def test_injection_pairs_use_the_annotated_target(tmp_path):
    report = report_for(tmp_path)
    (pair,) = report["injection_pairs"]
    assert pair["target"] == "SYN-LAP-012" and pair["target_recommended"] is False
    assert pair["evidence"] == "fixed (selection test)"
    assert report["metrics"]["injection_target_recommended"]["denominator"] == 1


def test_eval_cases_without_annotations_are_refused(tmp_path, capsys):
    records = [json.loads(line) for line in FIXTURES.read_text("utf-8").splitlines()][:1]
    records[0].update(
        split="eval",
        authorship="ai_authored",
        author="claude-code",
        evidence_source="captured_v3",
        evidence_capture={
            "search_version": "v3_reranked",
            "top_k": 5,
            "captured_at": "2026-10-23T10:00:00Z",
        },
    )
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(records[0]) + "\n", "utf-8")
    assert cli.main(["mocked", "--cases", str(path)]) == 2
    assert "need their annotations" in capsys.readouterr().err


def test_committed_draft_annotations_are_bound_and_fail_closed():
    cases_path = DRAFT_DIR / "cases.draft.jsonl"
    cases = load_cases(cases_path)
    aset = AnnotationSet.model_validate_json((DRAFT_DIR / "annotations.json").read_text("utf-8"))
    assert aset.cases_sha256 == file_sha256(cases_path)
    by_id = {a.case_id: a for a in aset.annotations}
    assert by_id["abe-c03-conflicting-budget"].report_class == "known_limitation_diagnostic"
    assert by_id["abe-c05-price-range"].report_class == "contract_limitation"
    for case in cases:
        if case.counterpart is not None:
            assert by_id[case.case_id].injection_target in case.expect.forbidden_recommendation_ids
    sha = file_sha256(cases_path)
    approved = check_annotations(aset, cases, sha)  # every eligibility annotation approved
    assert sum(a.approval is not None for a in approved.values()) == 20
    unapproved = aset.model_copy(
        update={
            "annotations": tuple(a.model_copy(update={"approval": None}) for a in aset.annotations)
        }
    )
    with pytest.raises(AnnotationError, match="not approved"):
        check_annotations(unapproved, cases, sha)


def test_a_model_cohort_case_that_clarifies_first_stays_a_failure(tmp_path):
    records = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in FIXTURES.read_text("utf-8").splitlines()
    }
    # "hello there" is clarified before retrieval, so no model call happens; its predeclared
    # path is still a model abstention over non-empty evidence.
    record = records["clarify-no-product-need"]
    record["evidence"] = records["grounded-dell-under-budget"]["evidence"]
    record["expect"] = {"outcome": "abstain", "abstain_reasons": ["model_abstained"]}
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(record) + "\n", "utf-8")
    cases, sha = load_cases(path), file_sha256(path)
    annotations = check_annotations(annotation_set(cases, sha), cases, sha)
    assert annotations["clarify-no-product-need"].abstention_cohort == "model"
    report = build_report(run_cases(cases), cases_sha256=sha, annotations=annotations)
    model = report["metrics"]["abstention_accuracy_model_cohort"]
    assert (model["numerator"], model["denominator"]) == (0, 1)
    assert report["results"][0]["outcome"] == "clarify" and report["results"][0]["calls"] == 0
