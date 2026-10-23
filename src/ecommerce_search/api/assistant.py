"""`POST /assistant/answer` (Milestone 16): the grounded shopping assistant's first slice.

Single turn. Retrieval is the existing V3 reranked search (`run_reranked_search`), adapted to the
assistant's `CatalogRetriever` interface here, so the assistant domain has no HTTP dependency and
no retrieval of its own. Generation is optional and off by default; see
`ecommerce_search.assistant.workflow` for the bounded flow.

Logs carry fixed codes, counts and timings only: never the message, catalog evidence, explanations,
prompt, response text or any key.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ecommerce_search.api.dependencies import (
    get_db_session,
    get_decision_provider,
    get_embedder,
    get_generation_provider,
    get_reranker,
    get_settings_dep,
)
from ecommerce_search.api.observability import log_event
from ecommerce_search.api.reranked import run_reranked_search
from ecommerce_search.assistant.models import (
    AssistantRequest,
    AssistantResponse,
    AssistantUnavailable,
    EvidenceProduct,
)
from ecommerce_search.assistant.retrieval import CatalogRetriever, Retrieval, RetrievalUnavailable
from ecommerce_search.assistant.workflow import AssistantLimits, answer
from ecommerce_search.config import Settings
from ecommerce_search.decision import DecisionProvider
from ecommerce_search.embeddings.provider import Embedder
from ecommerce_search.generation import GenerationProvider
from ecommerce_search.reranking.documents import RerankDocumentError
from ecommerce_search.reranking.provider import RerankerProvider
from ecommerce_search.search.query import QueryError, normalize_query
from ecommerce_search.search.reranked import read_candidate_records

logger = logging.getLogger(__name__)

router = APIRouter()

UNAVAILABLE_DETAIL = "assistant unavailable"
RESPONSES = {
    422: {"description": "Invalid message text (standard validation error body)."},
    503: {
        "model": AssistantUnavailable,
        "description": "The underlying search is unavailable. The body is fixed and carries no "
        "internal details. Generation failures are not 503s: they return "
        "`generation_unavailable`.",
    },
}
DESCRIPTION = (
    "Grounded shopping assistant (Milestone 16, first slice; single turn). Deterministic query "
    "understanding decides whether to ask a clarification question. Otherwise the existing V3 "
    "reranked search retrieves catalog records, and the optional generator may only select "
    "retrieved product IDs and name the catalog fields that support them; it writes no text. "
    "Deterministic checks reject unknown IDs, cited fields the record does not give, and "
    "explicit-constraint violations on catalog fields; one repair attempt is allowed, then the "
    "assistant abstains. Every explanation and the summary are rendered by the server from "
    "catalog values (policy asst-policy-3); no model text is returned. This controls displayed "
    "claims only: it does not show that the selection is the best fit. With generation off "
    "(the default) or failing, `outcome` is `generation_unavailable` and the products are plain "
    "search results."
)


SPEC_FIELDS = ("ram_gb", "storage_gb", "storage_type", "storage_interface")


def _spec_value(record: object, field: str) -> int | str | None:
    value = getattr(getattr(record, "spec", None), field, None)
    return None if value is None else value if isinstance(value, int) else str(value)


class RerankedSearchRetriever:
    """Adapts the V3 search entry point to the assistant's retriever interface, then reads the
    typed catalog spec fields (RAM, storage) of exactly the retrieved products. That read is a
    by-ID catalog lookup in its own read-only snapshot, not a second search."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        embedder: Embedder | None,
        reranker: RerankerProvider | None,
        provider: DecisionProvider,
    ) -> None:
        self._args = (session, settings, embedder, reranker, provider)

    def retrieve(self, query: str, top_k: int) -> Retrieval:
        started = time.perf_counter()
        try:
            response = run_reranked_search(
                *self._args, query, top_k, ("body", "message"), ("body", "message")
            )
        except HTTPException as exc:
            # Search has already logged its fixed reason code.
            raise RetrievalUnavailable from exc
        session = self._args[0]
        try:
            records = read_candidate_records(session, [r.product_id for r in response.results])
        except (SQLAlchemyError, RerankDocumentError):
            logger.error("assistant catalog facts unavailable: catalog_read_failed")
            raise RetrievalUnavailable from None
        products = tuple(
            EvidenceProduct(
                search_rank=result.rank,
                product_id=result.product_id,
                title=result.title,
                brand=result.brand,
                category=result.category,
                subcategory=result.subcategory,
                description=result.description,
                price=result.price,
                currency=result.currency,
                rating=result.rating,
                review_count=result.review_count,
                availability=result.availability,
                **{
                    field: _spec_value(records.get(result.product_id), field)
                    for field in SPEC_FIELDS
                },
            )
            for result in response.results
        )
        return Retrieval(
            search_version=response.search_version,
            products=products,
            retrieval_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def get_assistant_retriever(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    reranker: Annotated[RerankerProvider | None, Depends(get_reranker)],
    provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> CatalogRetriever:
    return RerankedSearchRetriever(session, settings, embedder, reranker, provider)


def limits_from(settings: Settings) -> AssistantLimits:
    return AssistantLimits(
        evidence_k=min(settings.assistant_evidence_k, settings.search_candidate_k),
        max_output_tokens=settings.assistant_generation_max_output_tokens,
        deadline_seconds=settings.assistant_generation_deadline_seconds,
        input_usd_per_mtok=settings.assistant_input_usd_per_mtok,
        output_usd_per_mtok=settings.assistant_output_usd_per_mtok,
    )


@router.post(
    "/assistant/answer",
    response_model=AssistantResponse,
    responses=RESPONSES,
    summary="Grounded shopping assistant: clarify, retrieve (V3), optional bounded generation",
    description=DESCRIPTION,
)
def assistant_answer(
    body: AssistantRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    retriever: Annotated[CatalogRetriever, Depends(get_assistant_retriever)],
    generator: Annotated[GenerationProvider | None, Depends(get_generation_provider)],
) -> AssistantResponse:
    try:
        message = normalize_query(body.message, settings.search_max_query_length)
    except QueryError as exc:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body", "message"), "msg": str(exc)}]
        ) from None
    try:
        response = answer(
            message, retriever=retriever, generator=generator, limits=limits_from(settings)
        )
    except RetrievalUnavailable:
        log_event(logger, "assistant_answer_unavailable", reason="search_unavailable")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL) from None
    generation = response.generation
    log_event(
        logger,
        "assistant_answer_completed",
        outcome=response.outcome,
        clarify_reason=None if response.clarification is None else response.clarification.reason,
        abstain_reason=response.abstain_reason,
        generation_status=generation.status,
        generation_failure=generation.failure,
        generation_calls=generation.calls,
        validation=generation.validation,
        validation_errors=list(generation.validation_errors),
        evidence_count=len(response.evidence),
        recommendation_count=len(response.recommendations),
        input_tokens=generation.input_tokens,
        output_tokens=generation.output_tokens,
        cost_status=generation.cost_status,
        latency_ms=response.latency_ms.model_dump(),
    )
    return response
