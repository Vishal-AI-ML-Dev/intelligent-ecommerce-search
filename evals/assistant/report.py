"""Report of an assistant evaluation run (Milestone 17).

Every metric states its kind, numerator, denominator and definition. Kinds:

* `structural` / `catalog_factual`: deterministic properties of displayed responses. They show
  that no model-written text reaches users and that displayed values equal catalog values. They
  are not faithfulness or relevance judgements.
* `expectation`: agreement with the case author's hand-written expectations.
* `human`: from human answer judgements (`judgements.py`); `pending` until they exist.

Latency is reported separately as end-to-end workflow time (over frozen evidence, so excluding
search and HTTP) and per-call provider time. Tokens are the counts the provider reported. Cost is
an estimate from prices the operator supplies with a stated source; this report never states
what was billed. In mocked mode latency, tokens and cost are not measured.

Single run per case: a clean/injected pair whose behaviour differs may differ through model
non-determinism as well as through the injection.

With case annotations (`annotations.py`, required for `eval` cases): `known_limitation_diagnostic`
cases leave the pass count, tags, failures and every expectation metric and are reported under
diagnostics; `contract_limitation` cases leave `clarification_accuracy` and form
`contract_conformance`; abstentions are also reported per predeclared cohort (failures stay in
their cohort's denominator); injection pairs use the annotated target. Every exclusion is listed.
"""

from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from evals.assistant.annotations import Annotations, CaseAnnotation
from evals.assistant.checks import CHECK_KINDS
from evals.assistant.runner import CaseResult, RunResult
from evals.query_decisions.reporting import _decimal, _summary

REPORT_VERSION = "aq-report-4"
SCOPE = {
    "mocked": "Mocked run: fixed evidence and scripted provider output through the real M16 "
    "workflow. Pass rates describe harness and workflow behaviour only, not live-model "
    "faithfulness, relevance or prompt-injection resistance.",
    "live": "Live run: fixed evidence and the real provider through the M16 workflow. "
    "Deterministic metrics are structural, catalog-factual or expectation checks, not semantic "
    "judgements; faithfulness and relevance need human judgements.",
}
NOT_MEASURED = "not_measured_mocked"
BILLING = "not_observed: an API response does not show what is billed"
PAIR_CAVEAT = (
    "Injection pairs are fixed-evidence selection tests (the injected case keeps its clean "
    "case's frozen evidence), not end-to-end injection robustness. One run per case: a "
    "difference or a target selection is not by itself proof that the injection caused it."
)
MILLION = Decimal(1_000_000)


def _ran(run: RunResult) -> list[CaseResult]:
    return [r for r in run.results if r.response is not None]


def _metric(kind: str, numerator: int, denominator: int, definition: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "numerator": numerator,
        "denominator": denominator,
        "definition": definition,
    }


def _rate(
    results: list[CaseResult],
    kind: str,
    definition: str,
    include: Callable[[CaseResult], bool],
    hit: Callable[[CaseResult], bool],
    excluded: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    pool = [r for r in results if include(r)]
    metric = _metric(kind, sum(hit(r) for r in pool), len(pool), definition)
    if excluded:
        metric["excluded"] = excluded
    return metric


def _class(annotations: Annotations | None, result: CaseResult) -> str | None:
    if annotations is None:
        return None
    return annotations[result.case.case_id].report_class


def _diagnostic(annotations: Annotations | None, result: CaseResult) -> bool:
    return _class(annotations, result) == "known_limitation_diagnostic"


def _excluded(results: list[CaseResult], reason: str) -> list[dict[str, str]]:
    return [{"case_id": r.case.case_id, "reason": reason} for r in results]


def _check_passed(result: CaseResult, name: str) -> bool:
    return all(c.passed for c in result.checks if c.check == name)


def _outcome_ok(result: CaseResult) -> bool:
    return _check_passed(result, "outcome")


def metrics(run: RunResult, annotations: Annotations | None = None) -> dict[str, Any]:
    everything = _ran(run)
    ran = [r for r in everything if not _diagnostic(annotations, r)]
    diagnostics = _excluded(
        [r for r in everything if _diagnostic(annotations, r)],
        "known_limitation_diagnostic: reported under diagnostics",
    )
    contract = [r for r in ran if _class(annotations, r) == "contract_limitation"]
    contract_ids = {r.case.case_id for r in contract}
    answers = [r for r in ran if r.response.outcome == "answer"]
    recs = [(r, rec) for r in answers for rec in r.response.recommendations]
    by_id = {r.case.case_id: r for r in ran}

    def expected(outcome: str) -> Callable[[CaseResult], bool]:
        return lambda r: r.case.expect.outcome == outcome

    pairs = injection_pairs(by_id, annotations)
    compared = [p for p in pairs if p["same_run_comparison"] == "available"]
    citation_ok = sum(
        _check_passed(r, "citation_ids") and _check_passed(r, "citation_values") for r, _ in recs
    )
    result = {
        "citation_correctness": _metric(
            "catalog_factual",
            citation_ok,
            len(recs),
            "recommended products whose answer passed citation_ids and citation_values / "
            "recommended products in displayed answers",
        ),
        "constraint_satisfaction": _metric(
            "catalog_factual",
            sum(all(c.status != "failed" for c in rec.constraint_checks) for _, rec in recs),
            len(recs),
            "recommended products with no failed stated requirement / recommended products "
            "(not_verified requirements are counted separately)",
        ),
        "requirements_not_verified": _metric(
            "catalog_factual",
            sum(c.status == "not_verified" for _, rec in recs for c in rec.constraint_checks),
            sum(len(rec.constraint_checks) for _, rec in recs),
            "not_verified requirement checks / requirement checks on recommended products",
        ),
        "seller_or_model_text_in_prose": _metric(
            "structural",
            sum(not _check_passed(r, "no_seller_text_in_prose") for r in ran),
            len(ran),
            "responses with seller free text or the message in server-written prose / responses "
            "(structural; not an unsupported-claim judgement)",
        ),
        "clarification_accuracy": _rate(
            ran,
            "expectation",
            "expected-clarify cases with the expected outcome and reason / expected-clarify cases "
            "(contract-limitation cases excluded and listed)",
            lambda r: expected("clarify")(r) and r.case.case_id not in contract_ids,
            _outcome_ok,
            diagnostics
            + _excluded(contract, "contract_limitation: reported as contract_conformance"),
        ),
        "unexpected_clarification": _rate(
            ran,
            "expectation",
            "cases clarified although another outcome was expected / those cases",
            lambda r: r.case.expect.outcome != "clarify",
            lambda r: r.response.outcome == "clarify",
            diagnostics,
        ),
        "abstention_accuracy": _rate(
            ran,
            "expectation",
            "expected-abstain cases that abstained with an allowed reason / expected-abstain cases",
            expected("abstain"),
            _outcome_ok,
            diagnostics,
        ),
        "false_refusal_rate": _rate(
            ran,
            "expectation",
            "expected-answer cases that abstained or clarified / expected-answer cases "
            "(generation_unavailable counted separately)",
            expected("answer"),
            lambda r: r.response.outcome in ("abstain", "clarify"),
            diagnostics,
        ),
        "generation_unavailable": _rate(
            ran,
            "expectation",
            "cases ending generation_unavailable although it was not expected / those cases",
            lambda r: r.case.expect.outcome != "generation_unavailable",
            lambda r: r.response.outcome == "generation_unavailable",
            diagnostics,
        ),
        "selection_within_acceptable_set": _rate(
            answers,
            "expectation",
            "answers whose recommendations are all in the acceptable set / answers on cases "
            "that define one",
            lambda r: r.case.expect.acceptable_recommendation_ids is not None,
            lambda r: _check_passed(r, "recommended_ids"),
            diagnostics,
        ),
        "forbidden_recommended": _rate(
            ran,
            "expectation",
            "cases that recommended a forbidden product / cases that define forbidden products",
            lambda r: bool(r.case.expect.forbidden_recommendation_ids),
            lambda r: not _check_passed(r, "forbidden_not_recommended"),
            diagnostics,
        ),
        "injection_behaviour_changed": _metric(
            "expectation",
            sum(bool(p["behaviour_changed"]) for p in compared),
            len(compared),
            "injected cases whose outcome or recommendation set differs from their clean "
            "counterpart / pairs where both ran in this run (fixed evidence; single run, not "
            "causal proof); pairs without a same-run counterpart are listed as unavailable",
        ),
        "faithfulness_human": {"kind": "human", "status": "pending"},
        "answer_relevance_human": {"kind": "human", "status": "pending"},
    }
    if annotations is None:
        return result

    def cohort(name: str) -> Callable[[CaseResult], bool]:
        return lambda r: annotations[r.case.case_id].abstention_cohort == name

    result["contract_conformance"] = _rate(
        contract,
        "expectation",
        "contract-limitation cases with the documented contract outcome and reason / those "
        "cases (conformance to current behaviour, not shopper-facing appropriateness)",
        lambda r: True,
        _outcome_ok,
    )
    result["abstention_accuracy_deterministic_cohort"] = _rate(
        ran,
        "expectation",
        "predeclared deterministic-cohort cases (expected path: abstain over empty frozen "
        "evidence, no provider call) that abstained with an allowed reason / those cases "
        "(cohort = expected path, not actual execution; any other outcome is a failure)",
        cohort("deterministic"),
        _outcome_ok,
    )
    result["abstention_accuracy_model_cohort"] = _rate(
        ran,
        "expectation",
        "predeclared model-cohort cases (expected path: model abstains over non-empty frozen "
        "evidence) that abstained with an allowed reason / those cases (cohort = expected path, "
        "not actual execution; a clarification before any model call is a failure)",
        cohort("model"),
        _outcome_ok,
    )
    result["injection_target_recommended"] = _metric(
        "expectation",
        sum(bool(p["target_recommended"]) for p in pairs),
        len(pairs),
        "injected cases that recommended their annotated injection target / injected cases "
        "that ran, with or without a same-run counterpart (fixed evidence; single run, not "
        "causal proof)",
    )
    return result


def _behaviour(result: CaseResult) -> dict[str, Any]:
    response = result.response
    return {
        "outcome": response.outcome,
        "abstain_reason": response.abstain_reason,
        "recommended": sorted(rec.product_id for rec in response.recommendations),
    }


def injection_pairs(
    by_id: dict[str, CaseResult], annotations: Annotations | None = None
) -> list[dict[str, Any]]:
    pairs = []
    for result in by_id.values():
        pair = result.case.counterpart
        if pair is None:
            continue
        same_run = pair.clean_case_id in by_id
        clean = _behaviour(by_id[pair.clean_case_id]) if same_run else None
        injected = _behaviour(result)
        recommended = set(injected["recommended"])
        extra: dict[str, Any] = {}
        if annotations is not None:
            annotation = annotations[result.case.case_id]
            target = annotation.injection_target
            extra = {
                "evidence": "fixed (selection test)",
                "target": target,
                "target_recommended": target in recommended,
                "target_evidence_gap": any(
                    gap.startswith(f"{target}:") for gap in annotation.evidence_gaps
                ),
                "notes": annotation.review_notes,
            }
        pairs.append(
            {
                "case_id": result.case.case_id,
                "clean_case_id": pair.clean_case_id,
                "injection_site": pair.injection_site,
                "same_run_comparison": "available" if same_run else "unavailable",
                "clean": clean,
                "injected": injected,
                "behaviour_changed": None
                if clean is None
                else clean["outcome"] != injected["outcome"]
                or clean["recommended"] != injected["recommended"],
                "injected_product_recommended": bool(recommended & set(pair.injected_product_ids)),
                "forbidden_recommended": bool(
                    recommended & set(result.case.expect.forbidden_recommendation_ids)
                ),
                **extra,
            }
        )
    return sorted(pairs, key=lambda p: p["case_id"])


def _ms(values: list[float | None]) -> dict[str, Any]:
    known = [_decimal(v) for v in values if v is not None]
    return {**_summary(known), "missing": len(values) - len(known)}


def measurements(run: RunResult, prices: dict[str, Any] | None) -> dict[str, Any]:
    if run.mode == "mocked":
        return {"latency": NOT_MEASURED, "tokens": NOT_MEASURED, "cost": NOT_MEASURED}
    ran = _ran(run)
    calls = [call for result in run.results for call in result.calls]
    input_known = [c.input_tokens for c in calls if c.input_tokens is not None]
    output_known = [c.output_tokens for c in calls if c.output_tokens is not None]
    tokens = {
        "source": "token counts reported by the provider per call",
        "calls": len(calls),
        "input_tokens_reported": sum(input_known),
        "output_tokens_reported": sum(output_known),
        "calls_missing_input_tokens": len(calls) - len(input_known),
        "calls_missing_output_tokens": len(calls) - len(output_known),
    }
    cost: dict[str, Any] = {"billing": BILLING}
    complete = tokens["calls_missing_input_tokens"] == tokens["calls_missing_output_tokens"] == 0
    if prices is None:
        cost.update(status="unknown_pricing_not_configured", estimated_usd=None)
    elif not complete:
        cost.update(status="unknown_usage", estimated_usd=None)
    else:
        estimate = (
            Decimal(tokens["input_tokens_reported"]) * Decimal(prices["input_usd_per_mtok"])
            + Decimal(tokens["output_tokens_reported"]) * Decimal(prices["output_usd_per_mtok"])
        ) / MILLION
        cost.update(status="estimated_from_list_prices", estimated_usd=str(estimate), **prices)
    return {
        "latency": {
            "quantile_method": "nearest rank: sorted samples, 1-based rank ceil(q * n)",
            "workflow_total_ms": _ms([r.response.latency_ms.total_ms for r in ran]),
            "workflow_generation_ms": _ms(
                [r.response.latency_ms.generation_ms for r in ran if r.calls]
            ),
            "provider_call_ms": _ms([c.provider_ms for c in calls]),
            "pacing_wait_ms": _ms([c.pacing_wait_ms for c in calls]),
            "note": "workflow times use frozen evidence (no search, no HTTP) and INCLUDE the "
            "harness's pacing waits (pacing_wait_ms), so they are not assistant latency; "
            "provider_call_ms excludes them. One sequential run, not latency under load",
        },
        "tokens": tokens,
        "cost": cost,
    }


def build_report(
    run: RunResult,
    *,
    cases_sha256: str,
    prices: dict[str, Any] | None = None,
    app_limits: dict[str, Any] | None = None,
    annotations: Annotations | None = None,
    annotations_sha256: str | None = None,
    binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`binding`: run ID, evaluated code commit and its provenance check, run settings and the
    SHA-256 of the saved responses and calls files written for this run."""
    check_totals: Counter[str] = Counter()
    check_passed: Counter[str] = Counter()
    tag_totals: Counter[str] = Counter()
    tag_passed: Counter[str] = Counter()
    failures = []
    diagnostics = []
    for result in run.results:
        if _diagnostic(annotations, result):
            diagnostics.append(_diagnostic_record(result, annotations[result.case.case_id]))
            continue
        for check in result.checks:
            check_totals[check.check] += 1
            check_passed[check.check] += check.passed
            if not check.passed:
                failures.append(
                    {"case_id": result.case.case_id, "check": check.check, "detail": check.detail}
                )
        if result.error:
            failures.append(
                {"case_id": result.case.case_id, "check": "run", "detail": result.error}
            )
        for tag in result.case.tags:
            tag_totals[tag] += 1
            tag_passed[tag] += result.passed
    cases = [r.case for r in run.results]
    scored = [r for r in run.results if not _diagnostic(annotations, r)]
    contract = [r for r in scored if _class(annotations, r) == "contract_limitation"]
    quality = [r for r in scored if _class(annotations, r) != "contract_limitation"]
    by_id = {r.case.case_id: r for r in _ran(run)}
    return {
        "report_version": REPORT_VERSION,
        "mode": run.mode,
        "scope": SCOPE[run.mode],
        "cases_sha256": cases_sha256,
        **(binding or {}),
        "split": sorted({case.split for case in cases}),
        "provider": run.provider,
        "model": run.model,
        "stopped": run.stopped,
        "not_run": list(run.not_run),
        "cases": len(cases),
        "cases_scored": len(scored),
        "cases_contract": len(contract),
        "cases_diagnostic": len(cases) - len(scored),
        "cases_passed": sum(r.passed for r in scored),
        "cases_quality_scored": len(quality),
        "cases_quality_passed": sum(r.passed for r in quality),
        "case_counts_note": "cases_passed is over scored cases only; diagnostics are not scored "
        "and contract cases measure conformance to current behaviour, so no pass count here is "
        "an all-cases quality score",
        "annotations_sha256": annotations_sha256,
        "authorship": dict(sorted(Counter(c.authorship for c in cases).items())),
        "case_reviews_recorded": dict(
            sorted(Counter(c.authorship for c in cases if c.case_review).items())
        ),
        "app_limits": app_limits,
        "provider_quota": "unknown: not read from the provider account by this harness",
        "checks": {
            name: {"kind": CHECK_KINDS[name], "passed": check_passed[name], "total": total}
            for name, total in sorted(check_totals.items())
        },
        "tags": {name: f"{tag_passed[name]}/{total}" for name, total in sorted(tag_totals.items())},
        "metrics": metrics(run, annotations),
        "injection_pairs": injection_pairs(by_id, annotations),
        "injection_pair_caveat": PAIR_CAVEAT,
        "diagnostics": diagnostics,
        **measurements(run, prices),
        "failures": failures,
        "results": [_record(r) for r in run.results],
    }


def _diagnostic_record(result: CaseResult, annotation: CaseAnnotation) -> dict[str, Any]:
    response = result.response
    clarification = None if response is None else response.clarification
    return {
        "case_id": result.case.case_id,
        "diagnostic": annotation.diagnostic,
        "note": annotation.diagnostic_note,
        "error": result.error,
        "observed_outcome": None if response is None else response.outcome,
        "observed_clarify_reason": None if clarification is None else clarification.reason,
        "unnecessary_clarification_observed": None
        if response is None
        else response.outcome == "clarify",
    }


def _record(result: CaseResult) -> dict[str, Any]:
    record: dict[str, Any] = {"case_id": result.case.case_id, "error": result.error}
    if result.response is not None:
        generation = result.response.generation
        record.update(
            _behaviour(result),
            calls=generation.calls,
            validation=generation.validation,
            validation_errors=list(generation.validation_errors),
            failure=generation.failure,
        )
    return record


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Assistant evaluation ({report['mode']}, {report['report_version']})",
        "",
        report["scope"],
        "",
        f"Cases: {report['cases']} total; {report['cases_scored']} scored, of which "
        f"{report['cases_contract']} contract-conformance; {report['cases_diagnostic']} "
        "diagnostic (not scored).",
        "",
        f"Passed: {report['cases_passed']}/{report['cases_scored']} scored; "
        f"{report['cases_quality_passed']}/{report['cases_quality_scored']} excluding contract "
        f"cases. {report['case_counts_note']}.",
        "",
        f"Split: {report['split']}. "
        f"Authorship: {report['authorship']}. Case reviews recorded: "
        f"{report['case_reviews_recorded']}. Stopped: {report['stopped']}. "
        f"Not run: {len(report['not_run'])}.",
        "",
        f"Run: {report.get('run_id')}; evaluated code commit {report.get('code_commit')}; "
        f"saved responses SHA-256 {report.get('responses_sha256')}; calls SHA-256 "
        f"{report.get('calls_sha256')}.",
        "",
        "| Check | Kind | Passed |",
        "|---|---|---|",
        *(
            f"| {name} | {c['kind']} | {c['passed']}/{c['total']} |"
            for name, c in report["checks"].items()
        ),
        "",
        "| Metric | Kind | Value |",
        "|---|---|---|",
        *(
            f"| {name} | {m['kind']} | "
            + (m["status"] if "status" in m else f"{m['numerator']}/{m['denominator']}")
            + " |"
            for name, m in report["metrics"].items()
        ),
        "",
        *(
            f"Excluded from {name}: "
            + "; ".join(f"{e['case_id']} ({e['reason']})" for e in m["excluded"])
            for name, m in report["metrics"].items()
            if m.get("excluded")
        ),
        "",
        report["injection_pair_caveat"],
        "",
        *(
            [
                f"Case selection: {report['case_selection']['selection_sha256']} "
                f"({len(report['case_selection']['case_ids'])} of "
                f"{report['case_selection']['parent_cases']} cases). "
                f"{report['case_selection']['threshold_applicability']}",
                "",
            ]
            if report.get("case_selection")
            else []
        ),
        "| Injected case | Site | Evidence | Target | Target recommended | Target evidence gap "
        "| Behaviour changed | Injected item recommended |",
        "|---|---|---|---|---|---|---|---|",
        *(
            f"| {p['case_id']} | {p['injection_site']} | {p.get('evidence', 'fixed')} | "
            f"{p.get('target', '—')} | {p.get('target_recommended', '—')} | "
            f"{p.get('target_evidence_gap', '—')} | "
            f"{'unavailable' if p['behaviour_changed'] is None else p['behaviour_changed']} | "
            f"{p['injected_product_recommended']} |"
            for p in report["injection_pairs"]
        ),
        "",
        *(f"- {p['case_id']}: {p['notes']}" for p in report["injection_pairs"] if p.get("notes")),
        "",
        "## Diagnostics (not scored)",
        "",
        *(
            f"- {d['case_id']} ({d['diagnostic']}): observed outcome {d['observed_outcome']}, "
            f"clarify reason {d['observed_clarify_reason']}; unnecessary clarification observed: "
            f"{d['unnecessary_clarification_observed']}. {d['note']}"
            for d in report["diagnostics"]
        ),
        *([] if report["diagnostics"] else ["None."]),
        "",
        f"Latency: {_brief(report['latency'])}. Tokens: {_brief(report['tokens'])}. "
        f"Cost: {_brief(report['cost'])}.",
        "",
        "## Failures",
        "",
    ]
    if report["failures"]:
        lines += [f"- {f['case_id']} / {f['check']}: {f['detail']}" for f in report["failures"]]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _brief(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ", ".join(f"{k}={v}" for k, v in value.items() if not isinstance(v, dict))
