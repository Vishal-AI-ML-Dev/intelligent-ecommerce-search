"""`GET /search/reranked` and `POST /search/reranked` (Milestone 8, V3). One shared path.

V3 = V2 + cross-encoder reranking of the head of the fused list. Order of work: validate; run
deterministic query understanding once; translate it into fp-1 filters once; then, only for a
query with a letter or digit, load the embedding model and encode the unchanged normalized query
(no database connection is used), then read the sources in one REPEATABLE READ READ ONLY
transaction, which ends (and releases its connection) before fusion and before any reranker
work. Fusion is the unchanged V1 Reciprocal Rank Fusion, so the candidate list, its counts and
every source and RRF rank and score equal V2.

- `RERANKER_ENABLED=false` is a configured mode, not a fallback: the exact V2 source read runs,
  no catalog record is read and the reranker is never touched; the V2 order is returned.
- Enabled: the same snapshot also reads the catalog records of the source-ID union. After the
  transaction, the reranker is loaded, `rd-1` documents are built for the first
  `min(search_rerank_depth, candidate_count)` fused candidates only, pair lengths are checked
  without truncation, the head is scored once and reordered deterministically (score desc, then
  RRF rank, then product id). The unscored tail keeps its RRF order. No candidates is a 200.

An enabled reranker that fails is a fixed generic 503: the V2 order is never returned instead
and nothing is retried. The server log records only a fixed reason code (plus a fixed operator
hint where one exists), never query or document text, SQL, paths, credentials, exception text or
a traceback. Existing routes are untouched by this module.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ecommerce_search.api.dense import HINTS, encode_query, has_searchable_text
from ecommerce_search.api.dependencies import (
    get_db_session,
    get_decision_provider,
    get_embedder,
    get_reranker,
    get_settings_dep,
)
from ecommerce_search.api.schemas import (
    FilteredQueryUnderstandingBlock,
    FilterPolicy,
    HybridFusion,
    RerankedSearchLatency,
    RerankedSearchRequest,
    RerankedSearchResponse,
    RerankedSearchResult,
    RerankerBlock,
    SearchUnavailable,
)
from ecommerce_search.config import Settings
from ecommerce_search.decision import DecisionProvider, understand_normalized_query
from ecommerce_search.embeddings.provider import Embedder, EmbedderUnavailable
from ecommerce_search.embeddings.text import EMBEDDING_TEXT_VERSION
from ecommerce_search.filtering import FILTER_POLICY_VERSION, FilterDerivation, derive_filters
from ecommerce_search.reranking.documents import (
    RERANK_DOCUMENT_VERSION,
    RerankDocumentError,
    build_rerank_documents,
)
from ecommerce_search.reranking.provider import (
    RerankerProvider,
    RerankerUnavailable,
    score_documents,
)
from ecommerce_search.reranking.ranking import RerankedCandidate, head_size, rerank_head
from ecommerce_search.search.dense import DISTANCE_METRIC
from ecommerce_search.search.documents import DOCUMENT_VERSION
from ecommerce_search.search.filtered import read_filtered_sources
from ecommerce_search.search.hybrid import FUSION_METHOD, RRF_K_STATUS, fuse_rrf
from ecommerce_search.search.query import QueryError, normalize_query
from ecommerce_search.search.reranked import read_reranked_sources

logger = logging.getLogger(__name__)

router = APIRouter()

RERANKED_SEARCH_VERSION = "v3_reranked"
UNAVAILABLE_DETAIL = "search unavailable"
# Fixed operator hints for reranker failures, logged server-side only (never in a response).
RERANKER_HINTS = {
    "snapshot_missing": "the reranker model snapshot is not present: run "
    "`python -m ecommerce_search.search model-fetch --kind reranker` (see ADR-007)",
}
RESPONSES = {
    422: {"description": "Invalid query text or top_k (standard validation error body)."},
    503: {
        "model": SearchUnavailable,
        "description": "Query understanding or filter translation failed, the embedding model, "
        "the search index or the search database is unavailable, the product record of a "
        "retrieved product cannot be read or validated, or the enabled reranker cannot load or "
        "score. The body is fixed and "
        "carries no internal details. The unreranked order is never returned instead.",
    },
}
DESCRIPTION = (
    "V3 reranked retrieval (Milestone 8): V2 filtered hybrid retrieval, then a local "
    "cross-encoder rescores the head of the fused list. Query understanding, filters, source "
    "retrieval and Reciprocal Rank Fusion are exactly those of `/search/filtered`. When "
    "reranking is enabled, the first `reranker.rerank_depth` fused candidates are scored "
    "against the query and reordered by `rerank_score` descending (ties keep RRF order); "
    "candidates beyond that depth follow unchanged in RRF order with a null `rerank_score`, so "
    "a `top_k` above the depth exposes an unscored tail. `rrf_rank` is each result's position "
    "in the fused list. Rerank scores are raw logits: uncalibrated and not probabilities. The "
    "reranker model is a provisional pin, not a quality selection. When reranking is disabled "
    "by configuration (`reranker.status = disabled`), the V2 order is returned. When the "
    "enabled reranker fails, the response is a 503; the V2 order is never silently returned. "
    "Queries without any letter or digit return no results without running any model or the "
    "database."
)


def _invalid(location: tuple[str, str], message: str) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": location, "msg": message}])


def _unavailable(reason: str) -> HTTPException:
    hint = HINTS.get(reason)
    if hint:
        logger.error("reranked search unavailable: %s (%s)", reason, hint)
    else:
        logger.error("reranked search unavailable: %s", reason)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL)


def _reranker_unavailable(reason: str) -> HTTPException:
    hint = RERANKER_HINTS.get(reason)
    if hint:
        logger.error("reranked search unavailable: reranker %s (%s)", reason, hint)
    else:
        logger.error("reranked search unavailable: reranker %s", reason)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _reranked_search(
    session: Session,
    settings: Settings,
    embedder: Embedder | None,
    reranker: RerankerProvider | None,
    provider: DecisionProvider,
    raw_query: str,
    top_k: int | None,
    query_location: tuple[str, str],
    top_k_location: tuple[str, str],
) -> RerankedSearchResponse:
    started = time.perf_counter()
    try:
        query = normalize_query(raw_query, settings.search_max_query_length)
    except QueryError as exc:
        raise _invalid(query_location, str(exc)) from None
    limit = settings.search_default_top_k if top_k is None else top_k
    if not 1 <= limit <= settings.search_candidate_k:
        raise _invalid(top_k_location, f"top_k must be between 1 and {settings.search_candidate_k}")
    understanding_started = time.perf_counter()
    try:
        decision = understand_normalized_query(query, provider)
    except Exception:
        raise _unavailable("query_understanding_failed") from None
    understanding_ms = _elapsed_ms(understanding_started)
    translation_started = time.perf_counter()
    try:
        derivation = derive_filters(decision)
        if not isinstance(derivation, FilterDerivation):
            raise TypeError
    except Exception:
        raise _unavailable("filter_policy_failed") from None
    translation_ms = _elapsed_ms(translation_started)
    spec = settings.embedding_spec()
    reranker_spec = settings.reranker_spec()
    enabled = settings.reranker_enabled
    depth = settings.search_rerank_depth
    searchable = has_searchable_text(query)
    tsquery = lexical_ms = load_ms = embed_ms = vector_ms = rrf_ms = None
    document_read_ms = reranker_load_ms = rerank_document_ms = reranking_ms = None
    lexical_hits: list = []
    dense_hits: list = []
    ranked: list[RerankedCandidate] = []
    reranked_count = 0
    if not searchable:
        reranker_status = "skipped_no_searchable_text"
    else:
        if embedder is None:
            raise _unavailable("snapshot_missing")
        # Model work first: no database connection is checked out until it is done.
        try:
            vector, load_ms, embed_ms = encode_query(embedder, spec, query, query_location)
        except EmbedderUnavailable as exc:
            raise _unavailable(exc.reason) from None
        records = None
        try:
            if enabled:
                sources = read_reranked_sources(
                    session,
                    query,
                    vector,
                    spec,
                    derivation.spec,
                    settings.search_lexical_k,
                    settings.search_dense_k,
                )
                lexical, dense = sources.lexical, sources.dense
                records, document_read_ms = sources.records, sources.document_read_ms
            else:
                lexical, dense = read_filtered_sources(
                    session,
                    query,
                    vector,
                    spec,
                    derivation.spec,
                    settings.search_lexical_k,
                    settings.search_dense_k,
                )
        except SQLAlchemyError as exc:
            missing = getattr(getattr(exc, "orig", None), "sqlstate", None) == "42P01"
            raise _unavailable("table_missing" if missing else "database_error") from None
        except RerankDocumentError:
            raise _unavailable(RerankDocumentError.reason) from None
        # The read-only transaction has ended and the connection is released.
        tsquery, lexical_ms, vector_ms = lexical.tsquery, lexical.lexical_ms, dense.vector_ms
        lexical_hits, dense_hits = lexical.hits, dense.hits
        rrf_started = time.perf_counter()
        fused = fuse_rrf(
            lexical_hits, dense_hits, settings.search_rrf_k, settings.search_candidate_k
        )
        rrf_ms = _elapsed_ms(rrf_started)
        if not enabled or not fused:
            reranker_status = "disabled" if not enabled else "skipped_no_candidates"
            ranked = [
                RerankedCandidate(
                    candidate=candidate, rrf_rank=position, rerank_rank=None, rerank_score=None
                )
                for position, candidate in enumerate(fused, start=1)
            ]
        else:
            if reranker is None:
                raise _reranker_unavailable("snapshot_missing")
            if reranker.spec != reranker_spec:
                raise _reranker_unavailable("config_mismatch")
            try:
                reranker_load_ms = reranker.load()
            except RerankerUnavailable as exc:
                raise _reranker_unavailable(exc.reason) from None
            size = head_size(len(fused), depth)
            documents_started = time.perf_counter()
            try:
                documents = build_rerank_documents(
                    [candidate.product_id for candidate in fused[:size]], records
                )
            except RerankDocumentError:
                raise _unavailable(RerankDocumentError.reason) from None
            rerank_document_ms = _elapsed_ms(documents_started)
            reranking_started = time.perf_counter()
            try:
                scores = score_documents(reranker, query, [doc.text for doc in documents])
                ranked = rerank_head(fused, scores, depth)
            except RerankerUnavailable as exc:
                raise _reranker_unavailable(exc.reason) from None
            reranking_ms = _elapsed_ms(reranking_started)
            reranker_status = "applied"
            reranked_count = size
    rows = {hit.product_id: hit for hit in dense_hits} | {
        hit.product_id: hit for hit in lexical_hits
    }
    results = []
    for position, item in enumerate(ranked[:limit], start=1):
        candidate = item.candidate
        row = rows[candidate.product_id]  # lexical display fields win; same snapshot either way
        results.append(
            RerankedSearchResult(
                rank=position,
                rrf_score=float(candidate.rrf_score),
                lexical_rank=candidate.lexical_rank,
                lexical_score=candidate.lexical_score,
                dense_rank=candidate.dense_rank,
                dense_score=candidate.dense_score,
                product_id=row.product_id,
                title=row.title,
                brand=row.brand,
                category=row.category,
                subcategory=row.subcategory,
                description=row.description,
                price=row.price,
                currency=row.currency,
                rating=None if row.rating is None else float(row.rating),
                review_count=row.review_count,
                availability=row.availability,
                rrf_rank=item.rrf_rank,
                rerank_rank=item.rerank_rank,
                rerank_score=item.rerank_score,
            )
        )
    lexical_ids = {hit.product_id for hit in lexical_hits}
    dense_ids = {hit.product_id for hit in dense_hits}
    total_ms = _elapsed_ms(started)
    return RerankedSearchResponse(
        query=query,
        search_version=RERANKED_SEARCH_VERSION,
        document_version=DOCUMENT_VERSION,
        tsquery=tsquery,
        embedding_model_id=spec.model_id,
        embedding_model_revision=spec.revision,
        embedding_dimension=spec.dimension,
        embedding_text_version=EMBEDDING_TEXT_VERSION,
        distance_metric=DISTANCE_METRIC,
        fusion=HybridFusion(
            method=FUSION_METHOD,
            rrf_k=settings.search_rrf_k,
            rrf_k_status=RRF_K_STATUS,
            lexical_k=settings.search_lexical_k,
            dense_k=settings.search_dense_k,
            candidate_k=settings.search_candidate_k,
        ),
        dense_status="used" if searchable else "skipped_no_searchable_text",
        lexical_hit_count=len(lexical_hits),
        dense_hit_count=len(dense_hits),
        overlap_count=len(lexical_ids & dense_ids),
        fused_count=len(lexical_ids | dense_ids),
        candidate_count=len(ranked),
        top_k=limit,
        result_count=len(results),
        results=results,
        filter_policy=FilterPolicy(version=FILTER_POLICY_VERSION),
        applied_filters=list(derivation.applied_filters),
        ignored_constraints=list(derivation.ignored_constraints),
        query_understanding=FilteredQueryUnderstandingBlock(
            usage="filter_source",
            provider=decision.provider,
            provider_version=decision.provider_version,
            understanding=decision.understanding,
        ),
        reranker=RerankerBlock(
            enabled=enabled,
            status=reranker_status,
            model_id=reranker_spec.model_id,
            model_revision=reranker_spec.revision,
            document_version=RERANK_DOCUMENT_VERSION,
            max_length=reranker_spec.max_length,
            rerank_depth=depth,
            reranked_count=reranked_count,
            score_type=reranker_spec.score_type,
        ),
        latency_ms=RerankedSearchLatency(
            lexical_ms=lexical_ms,
            model_load_ms=load_ms,
            query_embedding_ms=embed_ms,
            vector_ms=vector_ms,
            rrf_ms=rrf_ms,
            query_understanding_ms=understanding_ms,
            filter_translation_ms=translation_ms,
            document_read_ms=document_read_ms,
            reranker_load_ms=reranker_load_ms,
            rerank_document_ms=rerank_document_ms,
            reranking_ms=reranking_ms,
            total_ms=total_ms,
        ),
    )


@router.get(
    "/search/reranked",
    response_model=RerankedSearchResponse,
    responses=RESPONSES,
    summary="Reranked search: V2 filtered hybrid + cross-encoder reranking (V3, Milestone 8)",
    description=DESCRIPTION,
)
def reranked_search_get(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    reranker: Annotated[RerankerProvider | None, Depends(get_reranker)],
    provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
    q: Annotated[str, Query(description="Search text.")],
    top_k: Annotated[
        int | None,
        Query(ge=1, description="Maximum results. Default and upper bound come from settings."),
    ] = None,
) -> RerankedSearchResponse:
    return _reranked_search(
        session,
        settings,
        embedder,
        reranker,
        provider,
        q,
        top_k,
        ("query", "q"),
        ("query", "top_k"),
    )


@router.post(
    "/search/reranked",
    response_model=RerankedSearchResponse,
    responses=RESPONSES,
    summary="Reranked search: V2 filtered hybrid + cross-encoder reranking (V3, Milestone 8)",
    description=DESCRIPTION,
)
def reranked_search_post(
    body: RerankedSearchRequest,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    reranker: Annotated[RerankerProvider | None, Depends(get_reranker)],
    provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> RerankedSearchResponse:
    return _reranked_search(
        session,
        settings,
        embedder,
        reranker,
        provider,
        body.query,
        body.top_k,
        ("body", "query"),
        ("body", "top_k"),
    )
