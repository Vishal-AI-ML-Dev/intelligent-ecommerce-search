"""Deterministic checks of one assistant response against an `aq-2` case (Milestone 17).

Each check reads the response and the case (its hand-written expectations and raw catalog
records) only. None calls the assistant's own validation or rendering code, so a check cannot
pass merely because the code under test agrees with itself.

Recommendations and displayed evidence are checked separately: a product may have to stay
visible as a search result while never being recommended.

Each check has a kind (`CHECK_KINDS`): `structural` (shape and bounds of the response),
`catalog_factual` (displayed values equal the raw catalog records) or `expectation` (agreement
with the case author's hand-written expectations). None of them is a semantic judgement: they do
not prove faithfulness or relevance, which need human judgements (`judgements.py`). Passing on
mocked provider output describes harness and workflow behaviour only.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ecommerce_search.assistant.models import AssistantResponse
from ecommerce_search.generation.provider import MAX_SELECTED_PRODUCTS
from evals.assistant.contracts import EvalCase

# Seller-written text shorter than this is not searched for (brands and short words recur).
MIN_LEAK_LENGTH = 12


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str = ""


def _outcome(case: EvalCase, response: AssistantResponse) -> str:
    expect = case.expect
    problems = []
    if response.outcome != expect.outcome:
        problems.append(f"outcome {response.outcome} != {expect.outcome}")
    actual_clarify = None if response.clarification is None else response.clarification.reason
    if actual_clarify != expect.clarify_reason:
        problems.append(f"clarify_reason {actual_clarify} != {expect.clarify_reason}")
    if expect.abstain_reasons and response.abstain_reason not in expect.abstain_reasons:
        problems.append(f"abstain_reason {response.abstain_reason} not in {expect.abstain_reasons}")
    if not expect.abstain_reasons and response.abstain_reason is not None:
        problems.append(f"unexpected abstain_reason {response.abstain_reason}")
    return "; ".join(problems)


def _recommended_ids(case: EvalCase, response: AssistantResponse) -> str:
    actual = [rec.product_id for rec in response.recommendations]
    if len(actual) > MAX_SELECTED_PRODUCTS or len(set(actual)) != len(actual):
        return f"invalid recommendation list {actual}"
    if response.outcome != "answer" and actual:
        return f"recommendations {actual} on outcome {response.outcome}"
    expected = case.expect.recommended_ids
    if expected is not None and sorted(actual) != sorted(expected):
        return f"recommended {sorted(actual)} != expected {sorted(expected)}"
    acceptable = case.expect.acceptable_recommendation_ids
    if acceptable is not None and not set(actual) <= set(acceptable):
        return f"recommended {sorted(set(actual) - set(acceptable))} outside the acceptable set"
    return ""


def _citation_ids(case: EvalCase, response: AssistantResponse) -> str:
    retrieved = {product.product_id for product in case.evidence}
    unknown = [
        rec.product_id for rec in response.recommendations if rec.product_id not in retrieved
    ]
    return f"recommended IDs not retrieved: {unknown}" if unknown else ""


def _citation_values(case: EvalCase, response: AssistantResponse) -> str:
    records = {product.product_id: product for product in case.evidence}
    problems = []
    for rec in response.recommendations:
        record = records.get(rec.product_id)
        if record is None:
            continue  # reported by citation_ids
        for fact in rec.facts:
            raw = getattr(record, fact.field)
            if raw is None or fact.value != str(raw):
                problems.append(f"{rec.product_id}.{fact.field}={fact.value!r} (catalog {raw!r})")
    return "; ".join(problems)


def _forbidden_not_recommended(case: EvalCase, response: AssistantResponse) -> str:
    forbidden = set(case.expect.forbidden_recommendation_ids)
    hit = sorted(rec.product_id for rec in response.recommendations if rec.product_id in forbidden)
    return f"forbidden products recommended: {hit}" if hit else ""


def _evidence_retained(case: EvalCase, response: AssistantResponse) -> str:
    shown = {product.product_id for product in response.evidence}
    missing = sorted(set(case.expect.required_evidence_ids) - shown)
    return f"required evidence missing: {missing}" if missing else ""


def _evidence_unaltered(case: EvalCase, response: AssistantResponse) -> str:
    records = {product.product_id: product for product in case.evidence}
    changed = sorted(
        product.product_id
        for product in response.evidence
        if records.get(product.product_id) != product
    )
    return f"displayed evidence differs from the catalog records: {changed}" if changed else ""


def _constraint_statuses(case: EvalCase, response: AssistantResponse) -> str:
    by_id = {rec.product_id: rec for rec in response.recommendations}
    problems = []
    for product_id, expected in case.expect.constraint_statuses.items():
        rec = by_id.get(product_id)
        if rec is None:
            problems.append(f"{product_id} not recommended")
            continue
        actual = {check.field: check.status for check in rec.constraint_checks}
        if actual != expected:
            problems.append(f"{product_id}: {actual} != {expected}")
    for rec in response.recommendations:
        statuses = [check.status for check in rec.constraint_checks]
        if "failed" in statuses:
            problems.append(f"{rec.product_id} recommended with a failed requirement")
        all_passed = bool(statuses) and all(status == "passed" for status in statuses)
        if (rec.verification == "all_verified") != all_passed:
            problems.append(f"{rec.product_id} verification {rec.verification} vs {statuses}")
    return "; ".join(problems)


def _no_seller_text(case: EvalCase, response: AssistantResponse) -> str:
    """Seller free text (title, subcategory, description) and the shopper message never appear in
    server-written prose: summary, notice, clarification question or explanations."""
    prose = [response.notice, response.summary or ""]
    prose += [rec.explanation for rec in response.recommendations]
    question = "" if response.clarification is None else response.clarification.question
    prose.append(question)
    leaks = []
    for product in case.evidence:
        for field in ("title", "subcategory", "description"):
            text = getattr(product, field)
            if text and len(text) >= MIN_LEAK_LENGTH and any(text in part for part in prose):
                leaks.append(f"{product.product_id}.{field}")
    if len(case.message) >= MIN_LEAK_LENGTH and case.message in question:
        leaks.append("message echoed in clarification")
    if response.outcome != "answer" and response.summary is not None:
        leaks.append(f"summary on outcome {response.outcome}")
    return f"seller or shopper text in server prose: {leaks}" if leaks else ""


def _bounded_calls(case: EvalCase, response: AssistantResponse) -> str:
    calls = response.generation.calls
    if calls > case.expect.max_generation_calls or calls > 2:
        return f"{calls} generation calls (max {case.expect.max_generation_calls})"
    if case.generation == "off" and calls:
        return f"{calls} generation calls with generation off"
    return ""


def _versions_recorded(case: EvalCase, response: AssistantResponse) -> str:
    versions = response.versions
    if not (versions.prompt and versions.policy and versions.evidence):
        return "prompt, policy or evidence version missing"
    if response.outcome != "clarify" and not versions.retriever:
        return "retriever version missing after retrieval"
    return ""


CHECKS: dict[str, Callable[[EvalCase, AssistantResponse], str]] = {
    "outcome": _outcome,
    "recommended_ids": _recommended_ids,
    "citation_ids": _citation_ids,
    "citation_values": _citation_values,
    "forbidden_not_recommended": _forbidden_not_recommended,
    "evidence_retained": _evidence_retained,
    "evidence_unaltered": _evidence_unaltered,
    "constraint_statuses": _constraint_statuses,
    "no_seller_text_in_prose": _no_seller_text,
    "bounded_calls": _bounded_calls,
    "versions_recorded": _versions_recorded,
}


CHECK_KINDS: dict[str, str] = {
    "outcome": "expectation",
    "recommended_ids": "expectation",
    "citation_ids": "catalog_factual",
    "citation_values": "catalog_factual",
    "forbidden_not_recommended": "expectation",
    "evidence_retained": "expectation",
    "evidence_unaltered": "catalog_factual",
    "constraint_statuses": "expectation",
    "no_seller_text_in_prose": "structural",
    "bounded_calls": "structural",
    "versions_recorded": "structural",
    "script_consumed": "structural",
}


def run_checks(case: EvalCase, response: AssistantResponse) -> tuple[CheckResult, ...]:
    results = []
    for name, check in CHECKS.items():
        detail = check(case, response)
        results.append(CheckResult(check=name, passed=not detail, detail=detail))
    return tuple(results)
