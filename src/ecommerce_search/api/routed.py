"""`GET /search/routed` and `POST /search/routed`: experimental V4 (Milestone 12, rule `jr-1`).

V4 = V3 + Jev retrieval routing. Everything V3 does is unchanged: deterministic query
understanding, fp-1 filters, the V3 lexical and dense sources, RRF and the reranker. V4 adds one
step, before any model or database work: when `SEARCH_V4_JEV_ENABLED=true`, the query has a
letter or digit, and the deterministic parser gives no category and reports no category conflict,
the existing M11 `JevDecisionProvider` is asked once (pinned `jev-1.13.0`, retries 0, one call
in flight, bounded by the configured deadline). If Jev returns a unique top category that is not
`none`, one extra branch reads the same sources with the deterministic filters plus that
category, and the four lists are fused with RRF before the existing reranker runs
(`search/routed.py`). Jev's category never filters the combined result.

No confidence threshold has been validated, so nothing is gated: the raw probability is only
reported. Every other outcome (routing off, Jev not asked, a handled Jev fallback, `none` or a
tie) runs exactly the V3 path and returns the V3 ranking, with `jev_routing` saying why.

Failures:
- Known Jev failures return the V3 baseline with `jev_routing.status = jev_fallback` and a fixed
  `fallback_reason`: every runtime call failure the provider handles (timeout, transport, auth,
  rate limit, server, rejected or invalid response, model mismatch, deadline, saturated, closed)
  and a provider that cannot be obtained (`configuration_invalid`, `sdk_unavailable`,
  `provider_closed`).
- A provider result that is not a valid Jev `DecisionResult` for this query is the fixed 503
  (`jev_provider_error`), as is every V3 failure.
- Any other exception is a programming error and is not caught here.
Nothing logs or returns the key, headers or query text. The provider is built lazily by the
first request that needs it, so server start-up never calls Jev, and it is closed when the
application stops.
"""

import logging
import threading
import time
from collections.abc import Callable
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
    get_v4_jev_provider,
)
from ecommerce_search.api.reranked import RERANKER_HINTS
from ecommerce_search.api.schemas import (
    FilteredQueryUnderstandingBlock,
    FilterPolicy,
    HybridFusion,
    JevRoutingBlock,
    RerankerBlock,
    RoutedSearchLatency,
    RoutedSearchRequest,
    RoutedSearchResponse,
    RoutedSearchResult,
    SearchUnavailable,
)
from ecommerce_search.config import Settings
from ecommerce_search.decision import DecisionProvider, understand_normalized_query
from ecommerce_search.decision.jev_provider import (
    JevConfig,
    JevConfigurationError,
    JevDecisionProvider,
)
from ecommerce_search.decision.provider import DecisionResult, JevOutcome
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
from ecommerce_search.search.routed import (
    ROUTING_VERSION,
    fuse_routed,
    pre_call_status,
    read_routed_sources,
    routed_filters,
    routing_category,
    top_category_probability,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ROUTED_SEARCH_VERSION = "v4_jev_routed_experimental"
UNAVAILABLE_DETAIL = "search unavailable"
RESPONSES = {
    422: {"description": "Invalid query text or top_k (standard validation error body)."},
    503: {
        "model": SearchUnavailable,
        "description": "Any V3 failure, or a Jev provider result that is not a valid Jev "
        "decision for the query. The body is fixed and carries no internal details. Known Jev "
        "failures (call fallbacks, and a provider that cannot be configured, imported or is "
        "closed) are not errors: they return the V3 ranking.",
    },
}
DESCRIPTION = (
    "EXPERIMENTAL V4 (Milestone 12, rule `jr-1`): V3 reranked retrieval plus Jev retrieval "
    "routing. Off unless `SEARCH_V4_JEV_ENABLED=true`, in which case the route makes live, paid "
    "Jev calls. Only when the deterministic parser gives no category is Jev asked once for a "
    "category; a unique top category other than `none` adds one candidate branch filtered by "
    "the deterministic filters plus that category. The V3 and branch candidates are fused with "
    "RRF and reranked as in V3; Jev's category never filters the combined result. No confidence "
    "threshold has been validated: the raw probability is reported, never used. In every other "
    "case the V3 ranking is returned, and `jev_routing.status` says why. Not a relevance claim."
)


class JevProviderClosed(Exception):
    """The application is shutting down; the V4 Jev provider is no longer available."""


# Known reasons why the V4 Jev provider cannot be obtained. Each returns the V3 baseline with
# `jev_routing.status = jev_fallback` and this fixed `fallback_reason`; nothing else is caught.
PROVIDER_UNAVAILABLE_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (JevConfigurationError, "configuration_invalid"),
    (ImportError, "sdk_unavailable"),
    (JevProviderClosed, "provider_closed"),
)


class LazyJevProvider:
    """Builds the V4 Jev provider on first use (never at start-up) and closes it once."""

    def __init__(self, config: JevConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._provider: JevDecisionProvider | None = None
        self._closed = False

    def get(self) -> DecisionProvider:
        with self._lock:
            if self._closed:
                raise JevProviderClosed()
            if self._provider is None:
                self._provider = JevDecisionProvider(self._config)
            return self._provider

    def close(self) -> None:
        with self._lock:
            self._closed = True
            provider, self._provider = self._provider, None
        if provider is not None:
            provider.close()


def _invalid(location: tuple[str, str], message: str) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": location, "msg": message}])


def _unavailable(reason: str) -> HTTPException:
    hint = HINTS.get(reason)
    if hint:
        logger.error("routed search unavailable: %s (%s)", reason, hint)
    else:
        logger.error("routed search unavailable: %s", reason)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL)


def _reranker_unavailable(reason: str) -> HTTPException:
    hint = RERANKER_HINTS.get(reason)
    if hint:
        logger.error("routed search unavailable: reranker %s (%s)", reason, hint)
    else:
        logger.error("routed search unavailable: reranker %s", reason)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _jev_provider(
    jev: Callable[[], DecisionProvider],
) -> tuple[DecisionProvider | None, str | None]:
    """The provider, or None with a fixed reason for a known setup failure (V3 is returned)."""
    try:
        return jev(), None
    except Exception as error:
        for kind, reason in PROVIDER_UNAVAILABLE_REASONS:
            if isinstance(error, kind):
                logger.warning("routed search: jev unavailable (%s); returning V3", reason)
                return None, reason
        raise  # anything else is a programming error


def _ask_jev(provider: DecisionProvider, query: str, decision: DecisionResult) -> JevOutcome:
    # Runtime call failures are fallback outcomes inside the provider. An exception escaping it
    # is a programming error and propagates; only a malformed result is the fixed 503.
    result = provider.understand(query, decision.understanding)
    if (
        not isinstance(result, DecisionResult)
        or result.provider != "jev"
        or result.jev is None
        or result.understanding != decision.understanding
    ):
        raise _unavailable("jev_provider_error")
    return result.jev


def _routed_search(
    session: Session,
    settings: Settings,
    embedder: Embedder | None,
    reranker: RerankerProvider | None,
    provider: DecisionProvider,
    jev: Callable[[], DecisionProvider] | None,
    raw_query: str,
    top_k: int | None,
    query_location: tuple[str, str],
    top_k_location: tuple[str, str],
) -> RoutedSearchResponse:
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
    searchable = has_searchable_text(query)

    # Jev routing (jr-1): at most one call, before any model or database work.
    routing_status = pre_call_status(
        decision.understanding, enabled=jev is not None, searchable=searchable
    )
    outcome: JevOutcome | None = None
    setup_reason: str | None = None
    category = None
    jev_ms = None
    if routing_status is None and jev is not None:  # pre_call_status is None only when enabled
        jev_provider, setup_reason = _jev_provider(jev)
        if jev_provider is None:
            routing_status = "jev_fallback"
        else:
            jev_started = time.perf_counter()
            outcome = _ask_jev(jev_provider, query, decision)
            jev_ms = _elapsed_ms(jev_started)
            routing_status, category = routing_category(outcome)
    branch = None if category is None else routed_filters(derivation.spec, category)

    spec = settings.embedding_spec()
    reranker_spec = settings.reranker_spec()
    enabled = settings.reranker_enabled
    depth = settings.search_rerank_depth
    tsquery = lexical_ms = load_ms = embed_ms = vector_ms = rrf_ms = None
    document_read_ms = reranker_load_ms = rerank_document_ms = reranking_ms = None
    lexical_hits: list = []
    dense_hits: list = []
    routed_lexical_hits: list = []
    routed_dense_hits: list = []
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
            if branch is not None:
                sources = read_routed_sources(
                    session,
                    query,
                    vector,
                    spec,
                    derivation.spec,
                    branch,
                    settings.search_lexical_k,
                    settings.search_dense_k,
                    with_records=enabled,
                )
                lexical, dense = sources.lexical, sources.dense
                routed_lexical_hits = sources.routed_lexical.hits
                routed_dense_hits = sources.routed_dense.hits
                if enabled:
                    records, document_read_ms = sources.records, sources.document_read_ms
            elif enabled:
                v3_sources = read_reranked_sources(
                    session,
                    query,
                    vector,
                    spec,
                    derivation.spec,
                    settings.search_lexical_k,
                    settings.search_dense_k,
                )
                lexical, dense = v3_sources.lexical, v3_sources.dense
                records, document_read_ms = v3_sources.records, v3_sources.document_read_ms
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
        if branch is not None:
            fused: list = fuse_routed(
                lexical_hits,
                dense_hits,
                routed_lexical_hits,
                routed_dense_hits,
                settings.search_rrf_k,
                settings.search_candidate_k,
            )
        else:
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
    rows = (
        {hit.product_id: hit for hit in routed_dense_hits}
        | {hit.product_id: hit for hit in routed_lexical_hits}
        | {hit.product_id: hit for hit in dense_hits}
        | {hit.product_id: hit for hit in lexical_hits}
    )  # the V3 sources' display fields win; same snapshot either way
    results = []
    for position, item in enumerate(ranked[:limit], start=1):
        candidate = item.candidate
        row = rows[candidate.product_id]
        results.append(
            RoutedSearchResult(
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
                routed_lexical_rank=getattr(candidate, "routed_lexical_rank", None),
                routed_dense_rank=getattr(candidate, "routed_dense_rank", None),
                in_baseline_sources=(
                    candidate.lexical_rank is not None or candidate.dense_rank is not None
                ),
            )
        )
    lexical_ids = {hit.product_id for hit in lexical_hits}
    dense_ids = {hit.product_id for hit in dense_hits}
    routed_ids = {hit.product_id for hit in routed_lexical_hits} | {
        hit.product_id for hit in routed_dense_hits
    }
    routing_used = branch is not None
    total_ms = _elapsed_ms(started)
    return RoutedSearchResponse(
        query=query,
        search_version=ROUTED_SEARCH_VERSION,
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
        fused_count=len(lexical_ids | dense_ids | routed_ids),
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
        latency_ms=RoutedSearchLatency(
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
            jev_decision_ms=jev_ms,
        ),
        jev_routing=JevRoutingBlock(
            experimental=True,
            routing_version=ROUTING_VERSION,
            gate="none_validated",
            status=routing_status,
            jev_called=outcome is not None,
            routing_used=routing_used,
            routed_category=None if category is None else category.value,
            fallback_reason=setup_reason if outcome is None else outcome.fallback_reason,
            model=None if outcome is None else outcome.model,
            jev_latency_ms=None if outcome is None else outcome.latency_ms,
            raw_top_category_probability=top_category_probability(outcome),
            routed_filters=[] if branch is None else list(branch.applied_filters()),
            routed_lexical_hit_count=len(routed_lexical_hits) if routing_used else None,
            routed_dense_hit_count=len(routed_dense_hits) if routing_used else None,
        ),
    )


@router.get(
    "/search/routed",
    response_model=RoutedSearchResponse,
    responses=RESPONSES,
    summary="EXPERIMENTAL Jev-routed search: V3 + Jev category retrieval branch (V4, M12)",
    description=DESCRIPTION,
)
def routed_search_get(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    reranker: Annotated[RerankerProvider | None, Depends(get_reranker)],
    provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
    jev: Annotated[Callable[[], DecisionProvider] | None, Depends(get_v4_jev_provider)],
    q: Annotated[str, Query(description="Search text.")],
    top_k: Annotated[
        int | None,
        Query(ge=1, description="Maximum results. Default and upper bound come from settings."),
    ] = None,
) -> RoutedSearchResponse:
    return _routed_search(
        session,
        settings,
        embedder,
        reranker,
        provider,
        jev,
        q,
        top_k,
        ("query", "q"),
        ("query", "top_k"),
    )


@router.post(
    "/search/routed",
    response_model=RoutedSearchResponse,
    responses=RESPONSES,
    summary="EXPERIMENTAL Jev-routed search: V3 + Jev category retrieval branch (V4, M12)",
    description=DESCRIPTION,
)
def routed_search_post(
    body: RoutedSearchRequest,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    reranker: Annotated[RerankerProvider | None, Depends(get_reranker)],
    provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
    jev: Annotated[Callable[[], DecisionProvider] | None, Depends(get_v4_jev_provider)],
) -> RoutedSearchResponse:
    return _routed_search(
        session,
        settings,
        embedder,
        reranker,
        provider,
        jev,
        body.query,
        body.top_k,
        ("body", "query"),
        ("body", "top_k"),
    )
