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


INDEPENDENCE_NOTE = (
    "AI-assisted, owner-endorsed judgements are not independent human validation; "
    "'not stated' provenance is not 'unaided'. One reviewer: no agreement is claimed."
)
VIEWS = {
    "scored_excluding_contract": ("scored",),
    "scored_including_contract": ("scored", "contract"),
    "contract": ("contract",),
    "diagnostic": ("diagnostic",),
}


def _provenance_group(text: str | None) -> str:
    if text is None:
        return "not_recorded"
    if text.startswith("AI-assisted"):
        return "ai_assisted_owner_endorsed"
    if text.startswith("not stated"):
        return "not_stated"
    return "other"


def _rate(good: int, bad: int, unsure: int, definition: str) -> dict[str, Any]:
    return {
        "numerator": good,
        "denominator": good + bad,
        "unsure": unsure,
        "definition": definition,
    }


def human_review_report(
    judgements: tuple[AnswerJudgement, ...],
    report: dict[str, Any],
    classes: dict[str, str],
    provenance: dict[str, str] | None = None,
    pending: dict[str, Any] | None = None,
    planned: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Human-review coverage and rates per case class for one recorded run.

    `classes` maps every case in the run's case file to `scored`, `contract` or `diagnostic`
    (from the approved annotations). Every processed case appears exactly once: judged, missing
    or without a saved response; every unrun case is listed. `planned` gives the predeclared
    population per class (case-file counts) so any difference is stated, never hidden. No
    threshold is evaluated here, and an incomplete run never gets a verdict.
    """
    by_case = {j.case_id: j for j in judgements}
    processed = [r["case_id"] for r in report["results"]]
    if len(set(processed)) != len(processed):
        raise ValueError("a case appears more than once in the report")
    unknown = set(processed) - set(classes)
    if unknown:
        raise ValueError(f"no class for processed cases {sorted(unknown)}")
    saved = {r["case_id"]: r.get("recommended", []) for r in report["results"] if not r["error"]}
    provenance = provenance or {}
    entries = (pending or {}).get("entries", [])
    complete = report["stopped"] is None and not report["not_run"]

    def view(names: tuple[str, ...]) -> dict[str, Any]:
        ids = [c for c in processed if classes[c] in names]
        judged = [by_case[c] for c in ids if c in by_case]
        outcome = Counter(j.outcome_appropriate for j in judged)
        misleading = Counter(j.misleading for j in judged)
        fits = Counter(p.fit for j in judged for p in j.products)
        missing = [c for c in ids if c in saved and c not in by_case]
        groups = Counter(_provenance_group(provenance.get(j.case_id)) for j in judged)
        planned_n = None if planned is None else sum(planned.get(n, 0) for n in names)
        return {
            "planned_cases": planned_n,
            "processed": len(ids),
            "saved_responses": sum(c in saved for c in ids),
            "judged": len(judged),
            "missing_judgements": missing,
            "no_saved_response": [c for c in ids if c not in saved],
            "outcome_appropriate": _rate(
                outcome["yes"], outcome["no"], outcome["unsure"], "judged yes / judged yes or no"
            ),
            "not_misleading": _rate(
                misleading["no"],
                misleading["yes"],
                misleading["unsure"],
                "judged not misleading / judged yes or no",
            ),
            "products": {
                "recorded": sum(len(saved.get(c, [])) for c in ids),
                "fits": fits["fits"],
                "partly_fits": fits["partly_fits"],
                "does_not_fit": fits["does_not_fit"],
                "unsure": fits["unsure"],
                "in_missing_judgements": sum(len(saved[c]) for c in missing),
                "fits_rate": _rate(
                    fits["fits"],
                    fits["partly_fits"] + fits["does_not_fit"],
                    fits["unsure"],
                    "products judged fits / products judged fits, partly or does not fit",
                ),
            },
            "provenance": dict(sorted(groups.items())),
        }

    views = {name: view(names) for name, names in VIEWS.items()}
    accounted = sum(
        views[n]["processed"] for n in ("scored_excluding_contract", "contract", "diagnostic")
    )
    if accounted != len(processed):
        raise ValueError("processed cases are not partitioned exactly once")
    return {
        "report_sha256_bound": next(iter(by_case.values())).report_sha256 if by_case else None,
        "run_status": "complete" if complete else "incomplete",
        "stopped": report["stopped"],
        "not_run": list(report["not_run"]),
        "processed_cases": len(processed),
        "verdict": "none: human thresholds are not evaluated by this summary"
        + ("" if complete else "; run incomplete"),
        "population_note": "Rates cover processed cases with saved responses only; the "
        "predeclared populations (planned_cases) are the case-file counts. Differences come "
        "from unrun cases and missing judgements, which are listed; populations are not "
        "changed after seeing results.",
        "views": views,
        "partial_entries": {
            "active": [e["case_id"] for e in entries if "completed" not in e],
            "history_completed": [e["case_id"] for e in entries if "completed" in e],
        },
        "independence": INDEPENDENCE_NOTE,
    }


def render_human_review(summary: dict[str, Any]) -> str:
    lines = [
        f"# Human review: {summary['run_status']} run",
        "",
        f"Processed cases: {summary['processed_cases']}; not run: {len(summary['not_run'])}. "
        f"Verdict: {summary['verdict']}.",
        "",
        summary["independence"],
        "",
        summary["population_note"],
        "",
        "| View | Planned | Processed | Judged | Missing | Outcome appropriate | Not misleading "
        "| Products: fits / partly / does not / unsure / in missing | Provenance |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, v in summary["views"].items():
        o, m, p = v["outcome_appropriate"], v["not_misleading"], v["products"]
        lines.append(
            f"| {name} | {v['planned_cases']} | {v['processed']} | {v['judged']} | "
            f"{len(v['missing_judgements'])} | {o['numerator']}/{o['denominator']} "
            f"(unsure {o['unsure']}) | {m['numerator']}/{m['denominator']} (unsure "
            f"{m['unsure']}) | {p['fits']} / {p['partly_fits']} / {p['does_not_fit']} / "
            f"{p['unsure']} / {p['in_missing_judgements']} of {p['recorded']} | "
            f"{', '.join(f'{k} {n}' for k, n in v['provenance'].items()) or '—'} |"
        )
    missing = [c for v in summary["views"].values() for c in v["missing_judgements"]]
    active = ", ".join(summary["partial_entries"]["active"]) or "none"
    history = ", ".join(summary["partial_entries"]["history_completed"]) or "none"
    lines += [
        "",
        f"Missing judgements: {', '.join(sorted(set(missing))) or 'none'}. "
        f"Active partial entries: {active}; completed (history only): {history}.",
        "",
        f"Not run: {', '.join(summary['not_run']) or 'none'}.",
    ]
    return "\n".join(lines) + "\n"
