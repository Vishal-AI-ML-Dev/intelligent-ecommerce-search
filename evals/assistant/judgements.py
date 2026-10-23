"""Human answer-judgement format `aj-1` (Milestone 17); rubric in `docs/m17-answer-rubric.md`.

One judgement is one reviewer's verdict on one case of one recorded run, bound to that run's
report by its SHA-256. Judgements are entered by a reviewer; no code writes them, and AI is never
a reviewer. `reviewer_handle` (`ann-<n>`) records provenance only: it is pseudonymous and does
not prove that a human made the judgement. With one reviewer, no agreement is claimed.

Semantic metrics (faithfulness, answer relevance, outcome appropriateness) come only from these
judgements. `unsure` verdicts are excluded from denominators and counted.
"""

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.assistant.contracts import HANDLE

SCHEMA_VERSION = "aj-1"
Verdict = Literal["yes", "no", "unsure"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProductJudgement(_Frozen):
    product_id: str
    fit: Literal["fits", "partly_fits", "does_not_fit", "unsure"]


class AnswerJudgement(_Frozen):
    schema_version: Literal["aj-1"]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str
    reviewer_handle: str = Field(pattern=HANDLE.pattern)
    judged_on: date
    outcome_appropriate: Verdict
    # Exactly the products the run recommended for this case (empty when none).
    products: tuple[ProductJudgement, ...] = ()
    # Whether the displayed answer (explanations, summary, notice) would mislead the shopper.
    misleading: Verdict
    notes: str = Field(default="", max_length=500)


def load_judgements(
    path: Path, report: dict[str, Any], report_sha256: str
) -> tuple[AnswerJudgement, ...]:
    recommended = {
        r["case_id"]: sorted(r.get("recommended", [])) for r in report["results"] if not r["error"]
    }
    judgements = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        judgement = AnswerJudgement.model_validate(json.loads(line))
        if judgement.report_sha256 != report_sha256:
            raise ValueError(f"line {number}: judgement is for a different report")
        if judgement.case_id not in recommended:
            raise ValueError(f"line {number}: {judgement.case_id} has no recorded response")
        judged = sorted(p.product_id for p in judgement.products)
        if judged != recommended[judgement.case_id]:
            raise ValueError(f"line {number}: products must be exactly the recommended ones")
        key = (judgement.case_id, judgement.reviewer_handle)
        if key in seen:
            raise ValueError(f"line {number}: duplicate judgement for {key}")
        seen.add(key)
        judgements.append(judgement)
    return tuple(judgements)


def _verdict_rate(values: list[str], good: str, definition: str) -> dict[str, Any]:
    counts = Counter(values)
    return {
        "kind": "human",
        "numerator": counts[good],
        "denominator": counts["yes"] + counts["no"],
        "unsure": counts["unsure"],
        "definition": definition,
    }


def coverage(judgements: tuple[AnswerJudgement, ...], report: dict[str, Any]) -> dict[str, Any]:
    """How much of the recorded run was judged. Nothing missing or unsure is discarded: missing
    responses are listed, unsure verdicts counted, and diagnostics reported separately."""
    diagnostics = {d["case_id"] for d in report.get("diagnostics", [])}
    recorded = {r["case_id"]: r.get("recommended", []) for r in report["results"] if not r["error"]}
    judged = {j.case_id for j in judgements}

    def responses(ids: set[str]) -> dict[str, Any]:
        return {
            "recorded": len(ids),
            "judged": len(ids & judged),
            "missing": sorted(ids - judged),
        }

    products_recorded = sum(len(recorded[i]) for i in recorded if i not in diagnostics)
    product_verdicts = Counter(
        p.fit for j in judgements if j.case_id not in diagnostics for p in j.products
    )
    return {
        "responses_scored": responses(set(recorded) - diagnostics),
        "responses_diagnostic": responses(set(recorded) & diagnostics),
        "not_recorded": sorted(r["case_id"] for r in report["results"] if r["error"])
        + list(report.get("not_run", [])),
        "products": {
            "recorded": products_recorded,
            "judged": sum(product_verdicts.values()),
            "unsure": product_verdicts["unsure"],
            "in_missing_judgements": sum(
                len(recorded[i]) for i in set(recorded) - judged - diagnostics
            ),
        },
        "unsure": {
            "outcome_appropriate": sum(j.outcome_appropriate == "unsure" for j in judgements),
            "misleading": sum(j.misleading == "unsure" for j in judgements),
            "product_fit": product_verdicts["unsure"],
        },
    }


def summarize_judgements(
    judgements: tuple[AnswerJudgement, ...], report: dict[str, Any] | None = None
) -> dict[str, Any]:
    fits = Counter(p.fit for j in judgements for p in j.products)
    reviewers = sorted({j.reviewer_handle for j in judgements})
    summary: dict[str, Any] = {} if report is None else {"coverage": coverage(judgements, report)}
    return summary | {
        "reviewers": reviewers,
        "agreement": "not claimed: one reviewer" if len(reviewers) == 1 else "not computed",
        "judgements": len(judgements),
        "answer_relevance_human": {
            "kind": "human",
            "numerator": fits["fits"],
            "denominator": fits["fits"] + fits["partly_fits"] + fits["does_not_fit"],
            "partly_fits": fits["partly_fits"],
            "unsure": fits["unsure"],
            "definition": "recommended products judged `fits` / judged products (unsure excluded)",
        },
        "faithfulness_human": _verdict_rate(
            [j.misleading for j in judgements],
            "no",
            "responses judged not misleading / responses judged yes or no",
        ),
        "outcome_appropriate_human": _verdict_rate(
            [j.outcome_appropriate for j in judgements],
            "yes",
            "responses whose outcome was judged appropriate / responses judged yes or no",
        ),
    }
