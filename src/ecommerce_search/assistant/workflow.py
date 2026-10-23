"""The bounded assistant workflow (Milestone 16). A plain, fixed sequence; no agent loop:

understand (deterministic) -> clarify? -> V3 retrieval -> no evidence? abstain
-> provider off? no generated answer -> generate (call 1) -> validate
-> failed and time left? repair (call 2, the only retry) -> validate -> answer or abstain.

At most two generation calls per request. A provider failure is never turned into an answer.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ecommerce_search.assistant.clarify import decide_clarification
from ecommerce_search.assistant.explain import render_summary
from ecommerce_search.assistant.models import (
    AbstainReason,
    AssistantLatency,
    AssistantResponse,
    Clarification,
    CostStatus,
    EvidenceProduct,
    GenerationInfo,
    GenerationStatus,
    Recommendation,
    ValidationStatus,
    Versions,
)
from ecommerce_search.assistant.prompt import INSTRUCTIONS, build_user_content
from ecommerce_search.assistant.retrieval import CatalogRetriever, Retrieval
from ecommerce_search.assistant.validate import validate_draft
from ecommerce_search.generation.provider import (
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
)
from ecommerce_search.query_understanding.parser import parse_normalized_query

MILLION = Decimal(1_000_000)
# Reasons to skip generation before any provider request (decided by the API layer).
GenerationBlock = Literal["region_restricted", "region_unknown", "personal_data_suspected"]
# Quota or usage limits (provider 429 or the local guard): shown as "usage limit reached".
USAGE_LIMIT_FAILURES = frozenset(
    {"rate_limited", "local_concurrency_limit", "local_rate_limit", "local_daily_cap"}
)

NOTICES = {
    "answer": "The assistant selected products from the retrieved catalog results. Every "
    "explanation is built from catalog data; the selection itself is not verified as the best "
    "fit.",
    "clarify": "More detail is needed before searching.",
    "no_matching_products": "No catalog products matched this request, so no answer was generated.",
    "model_abstained": "The assistant found no retrieved product that fits this request.",
    "validation_failed": "A generated answer was produced but failed the grounding checks, so "
    "it is not shown.",
    "disabled": "No generated answer is available: answer generation is turned off on this "
    "server. The products below are ordinary search results, not a recommendation.",
    "failed": "No generated answer is available: answer generation failed. The products below "
    "are ordinary search results, not a recommendation.",
    "region_restricted": "No generated answer is available for your location: AI generation is "
    "offered only in eligible regions. The products below are ordinary search results, not a "
    "recommendation.",
    "region_unknown": "No generated answer is available: your region could not be confirmed as "
    "eligible for AI generation. The products below are ordinary search results, not a "
    "recommendation.",
    "personal_data_suspected": "No generated answer was requested: your message may contain "
    "personal information (such as an email address or phone number), which is never sent to "
    "the AI provider. Please remove it and ask again. The products below are ordinary search "
    "results, not a recommendation.",
    "limited": "No generated answer is available right now: the generation usage limit was "
    "reached. The products below are ordinary search results, not a recommendation.",
}


@dataclass(frozen=True)
class AssistantLimits:
    evidence_k: int
    max_output_tokens: int
    deadline_seconds: float
    input_usd_per_mtok: Decimal | None = None
    output_usd_per_mtok: Decimal | None = None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


@dataclass
class _Run:
    """Mutable bookkeeping of one request's generation calls."""

    calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    generation_ms: float | None = None

    def record(self, result: GenerationResult) -> None:
        self.calls += 1
        self.input_tokens = _add(self.input_tokens, result.input_tokens)
        self.output_tokens = _add(self.output_tokens, result.output_tokens)


def _add(total: int | None, value: int | None) -> int | None:
    # One unknown count makes the total unknown.
    return None if total is None or value is None else total + value


def _cost(run: _Run, limits: AssistantLimits) -> tuple[Decimal | None, CostStatus]:
    if run.calls == 0:
        return None, "not_applicable"
    if run.input_tokens is None or run.output_tokens is None:
        return None, "unknown_usage"
    if limits.input_usd_per_mtok is None or limits.output_usd_per_mtok is None:
        return None, "unknown_pricing_not_configured"
    cost = (
        Decimal(run.input_tokens) * limits.input_usd_per_mtok
        + Decimal(run.output_tokens) * limits.output_usd_per_mtok
    ) / MILLION
    return cost, "estimated"


def answer(
    message: str,
    *,
    retriever: CatalogRetriever,
    generator: GenerationProvider | None,
    limits: AssistantLimits,
    generation_block: GenerationBlock | None = None,
) -> AssistantResponse:
    """`message` is already normalized (`normalize_query`). Raises `RetrievalUnavailable`.

    `generation_block` (decided by the caller before any provider request: country eligibility,
    personal-data screen) skips generation with an honest notice and plain search results."""
    started = time.perf_counter()
    understanding = parse_normalized_query(message)
    understanding_ms = _ms(started)
    run = _Run()

    def respond(
        outcome: str,
        notice_key: str,
        *,
        retrieval: Retrieval | None = None,
        clarification: Clarification | None = None,
        abstain_reason: AbstainReason | None = None,
        summary: str | None = None,
        recommendations: tuple[Recommendation, ...] = (),
        status: GenerationStatus = "not_needed",
        failure: str | None = None,
        validation: ValidationStatus = "not_run",
        validation_errors: tuple[str, ...] = (),
    ) -> AssistantResponse:
        cost, cost_status = _cost(run, limits)
        evidence: tuple[EvidenceProduct, ...] = () if retrieval is None else retrieval.products
        return AssistantResponse(
            outcome=outcome,
            notice=NOTICES[notice_key],
            clarification=clarification,
            abstain_reason=abstain_reason,
            summary=summary,
            recommendations=recommendations,
            evidence=evidence,
            generation=GenerationInfo(
                status=status,
                failure=failure,
                provider=None if generator is None else generator.provider,
                model=None if generator is None else generator.model,
                calls=run.calls,
                validation=validation,
                validation_errors=validation_errors,
                input_tokens=run.input_tokens if run.calls else None,
                output_tokens=run.output_tokens if run.calls else None,
                estimated_cost_usd=cost,
                cost_status=cost_status,
            ),
            versions=Versions(retriever=None if retrieval is None else retrieval.search_version),
            latency_ms=AssistantLatency(
                understanding_ms=understanding_ms,
                retrieval_ms=None if retrieval is None else retrieval.retrieval_ms,
                generation_ms=run.generation_ms,
                total_ms=_ms(started),
            ),
        )

    clarification = decide_clarification(understanding)
    if clarification is not None:
        return respond("clarify", "clarify", clarification=clarification)

    retrieval = retriever.retrieve(message, limits.evidence_k)
    if not retrieval.products:
        return respond(
            "abstain",
            "no_matching_products",
            retrieval=retrieval,
            abstain_reason="no_matching_products",
        )
    if generator is None:
        return respond("generation_unavailable", "disabled", retrieval=retrieval, status="disabled")
    if generation_block is not None:
        return respond(
            "generation_unavailable",
            generation_block,
            retrieval=retrieval,
            status="skipped",
            failure=generation_block,
        )

    generation_started = time.perf_counter()
    repair_codes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    for attempt in range(2):
        result = generator.generate(
            GenerationRequest(
                instructions=INSTRUCTIONS,
                user_content=build_user_content(message, retrieval.products, repair_codes),
                max_output_tokens=limits.max_output_tokens,
            )
        )
        run.record(result)
        run.generation_ms = _ms(generation_started)
        if result.draft is None:
            return respond(
                "generation_unavailable",
                "limited" if result.failure in USAGE_LIMIT_FAILURES else "failed",
                retrieval=retrieval,
                status="failed",
                failure=result.failure,
                validation="failed" if attempt else "not_run",
                validation_errors=errors,
            )
        errors, recommendations = validate_draft(result.draft, retrieval.products, understanding)
        if not errors:
            validation: ValidationStatus = "repaired" if attempt else "passed"
            if result.draft.outcome == "abstain":
                return respond(
                    "abstain",
                    "model_abstained",
                    retrieval=retrieval,
                    abstain_reason="model_abstained",
                    status="used",
                    validation=validation,
                )
            return respond(
                "answer",
                "answer",
                retrieval=retrieval,
                summary=render_summary(len(recommendations), len(retrieval.products)),
                recommendations=recommendations,
                status="used",
                validation=validation,
            )
        if time.perf_counter() - started >= limits.deadline_seconds:
            break
        repair_codes = errors
    return respond(
        "abstain",
        "validation_failed",
        retrieval=retrieval,
        abstain_reason="validation_failed",
        status="used",
        validation="failed",
        validation_errors=errors,
    )
