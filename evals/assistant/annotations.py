"""Case annotations `aa-1` for an `eval` case set (Milestone 17).

Annotations sit beside the case file so that adding them never changes a case fingerprint (and
so never invalidates a case review). They carry what the report needs beyond the case itself:

* `report_class` (metric eligibility): `contract_limitation` cases leave `clarification_accuracy`
  and are reported as contract conformance; `known_limitation_diagnostic` cases leave every
  expectation metric and the pass count and are reported in a diagnostics section.
* `abstention_cohort` (metric eligibility): `deterministic` (expected abstain over empty frozen
  evidence: the workflow abstains with no provider call) or `model` (expected abstain over
  non-empty evidence). Derived from the predeclared expectation and evidence, never from a run.
* `injection_target` (metric eligibility): the product an injected case tries to get selected.
  It is explicit; a forbidden product is not assumed to be an attack target.
* Informational: evidence-sufficiency gaps, parts not assessed, the owner's review notes.

Fail closed: an annotation set is refused unless it is bound to the case file's SHA-256, has
exactly one annotation per case, each bound to its case fingerprint, every cohort equals the
derived one, every injected case names a valid target, and every annotation that affects metric
eligibility carries an owner approval of exactly its eligibility content. Preserving case
fingerprints does not approve an annotation.
"""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evals.assistant.contracts import HANDLE, EvalCase, case_sha256

ANNOTATION_VERSION = "aa-1"
ReportClass = Literal["contract_limitation", "known_limitation_diagnostic"]
Diagnostic = Literal["unnecessary_clarification"]
Cohort = Literal["deterministic", "model"]
SHA256 = r"^[0-9a-f]{64}$"


class AnnotationError(ValueError):
    """The annotation set cannot be used; the run or report is refused."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnnotationApproval(_Frozen):
    """An owner approval of one annotation's eligibility content."""

    case_id: str
    reviewer_handle: str = Field(pattern=HANDLE.pattern)
    reviewed_on: date
    eligibility_sha256: str = Field(pattern=SHA256)
    recorded_from: str = Field(min_length=1)


class CaseAnnotation(_Frozen):
    case_id: str
    case_sha256: str = Field(pattern=SHA256)
    report_class: ReportClass | None = None
    diagnostic: Diagnostic | None = None
    diagnostic_note: str = Field(default="", max_length=1000)
    abstention_cohort: Cohort | None = None
    injection_target: str | None = None
    evidence_gaps: tuple[str, ...] = ()
    not_assessed: tuple[str, ...] = ()
    review_notes: str = ""
    approval: AnnotationApproval | None = None

    @model_validator(mode="after")
    def _diagnostic(self) -> "CaseAnnotation":
        if (self.report_class == "known_limitation_diagnostic") != (self.diagnostic is not None):
            raise ValueError("diagnostic is required for, and only for, diagnostic cases")
        return self


class AnnotationSet(_Frozen):
    annotation_version: Literal["aa-1"]
    cases_sha256: str = Field(pattern=SHA256)
    annotations: tuple[CaseAnnotation, ...]


Annotations = dict[str, CaseAnnotation]


def eligibility_content(annotation: CaseAnnotation) -> dict[str, object]:
    return annotation.model_dump(
        mode="json",
        include={
            "case_id",
            "case_sha256",
            "report_class",
            "diagnostic",
            "diagnostic_note",
            "abstention_cohort",
            "injection_target",
        },
    )


def eligibility_sha256(annotation: CaseAnnotation) -> str:
    text = json.dumps(eligibility_content(annotation), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def affects_eligibility(annotation: CaseAnnotation) -> bool:
    return any(
        value is not None
        for value in (
            annotation.report_class,
            annotation.abstention_cohort,
            annotation.injection_target,
        )
    )


def derived_cohort(case: EvalCase) -> Cohort | None:
    if case.expect.outcome != "abstain":
        return None
    return "model" if case.evidence else "deterministic"


def check_annotations(
    annotation_set: AnnotationSet, cases: tuple[EvalCase, ...], cases_sha256: str
) -> dict[str, CaseAnnotation]:
    if annotation_set.cases_sha256 != cases_sha256:
        raise AnnotationError("annotations are bound to a different case file")
    by_id: dict[str, CaseAnnotation] = {}
    for annotation in annotation_set.annotations:
        if annotation.case_id in by_id:
            raise AnnotationError(f"{annotation.case_id}: more than one annotation")
        by_id[annotation.case_id] = annotation
    ids = {case.case_id for case in cases}
    if set(by_id) != ids:
        missing, extra = sorted(ids - set(by_id)), sorted(set(by_id) - ids)
        raise AnnotationError(f"annotations missing for {missing}, unknown cases {extra}")
    for case in cases:
        annotation = by_id[case.case_id]
        name = case.case_id
        if annotation.case_sha256 != case_sha256(case):
            raise AnnotationError(f"{name}: annotation is bound to different case content")
        diagnostic = annotation.report_class == "known_limitation_diagnostic"
        expected = None if diagnostic else derived_cohort(case)
        if annotation.abstention_cohort != expected:
            raise AnnotationError(f"{name}: abstention cohort must be {expected}")
        _check_target(case, annotation)
        if annotation.approval is not None and (
            annotation.approval.case_id != name
            or annotation.approval.eligibility_sha256 != eligibility_sha256(annotation)
        ):
            raise AnnotationError(f"{name}: approval does not match the annotation")
        if affects_eligibility(annotation) and annotation.approval is None:
            raise AnnotationError(f"{name}: annotation affects metric eligibility; not approved")
    return by_id


def _check_target(case: EvalCase, annotation: CaseAnnotation) -> None:
    target, name = annotation.injection_target, case.case_id
    if (case.counterpart is None) != (target is None):
        raise AnnotationError(
            f"{name}: an injection target is required for, and only for, injected cases"
        )
    if target is None:
        return
    if target not in case.expect.forbidden_recommendation_ids:
        raise AnnotationError(f"{name}: injection target must be a forbidden recommendation")
    if target not in {p.product_id for p in case.evidence}:
        raise AnnotationError(f"{name}: injection target must be in the frozen evidence")
    listing = case.counterpart.injection_site == "listing"
    if listing and target not in case.counterpart.injected_product_ids:
        raise AnnotationError(f"{name}: a listing injection target must be an injected record")


def load_annotations(
    path: Path, cases: tuple[EvalCase, ...], cases_sha256: str
) -> dict[str, CaseAnnotation]:
    try:
        annotation_set = AnnotationSet.model_validate_json(path.read_text("utf-8"))
    except (OSError, ValidationError) as exc:
        raise AnnotationError(f"annotations unreadable: {exc.__class__.__name__}") from None
    return check_annotations(annotation_set, cases, cases_sha256)
