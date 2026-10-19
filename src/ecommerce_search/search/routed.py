"""Experimental V4 Jev retrieval routing (Milestone 12, routing rule `jr-1`).

V4 = V3 plus one optional, additional candidate branch scoped by Jev's category. It is
experimental: no confidence threshold has been validated (the M12 calibration found none), so
there is no gate band and Jev's probability is recorded, never used to decide anything.

Rule `jr-1` (`routing_status`, `routing_category`):

* Jev is asked only when V4 Jev routing is enabled, the query has a letter or digit, and the
  deterministic parser gives no category and reports no category conflict. A conflicting
  category is never resolved by a model (`docs/spec.md` §7 rule 3).
* Routing is used only when Jev returns a valid outcome whose unique top category label is not
  `none` (`CategoryProposal.label`). A fallback, a tie or `none` leaves V4 equal to V3.
* The branch filters are the unchanged fp-1 filters of the deterministic parse plus Jev's
  category (`routed_filters`): every deterministic constraint is kept. Jev's category never
  filters the combined result: the V3 sources are read unchanged, with the V3 filters.
* The four ranked lists (V3 lexical and dense, branch lexical and dense) are fused with the
  unchanged RRF formula, equal weights and the configured `rrf_k` and `candidate_k`
  (`fuse_routed`). A product found by both the V3 sources and the branch sums its terms, so the
  branch acts as a soft boost. With an empty branch the fused list equals V3's exactly.
* The fused list is then reranked by the existing reranker exactly as in V3.

`read_routed_sources` runs all four source reads (and the record read for the union when the
reranker is enabled) in one REPEATABLE READ READ ONLY transaction, through the same filtered
lexical and dense functions as V2/V3.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Literal

from sqlalchemy.orm import Session

from ecommerce_search.catalog.schemas import CatalogRecord
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.decision.provider import JevOutcome
from ecommerce_search.embeddings.spec import EmbeddingModelSpec
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.query_understanding.models import QueryUnderstanding, UnderstandingField
from ecommerce_search.search import filtered, reranked
from ecommerce_search.search.dense import DenseHit, DenseResult
from ecommerce_search.search.hybrid import SNAPSHOT_SQL
from ecommerce_search.search.lexical import LexicalHit, LexicalResult

ROUTING_VERSION: Literal["jr-1"] = "jr-1"

RoutingStatus = Literal[
    "disabled",
    "skipped_no_searchable_text",
    "skipped_deterministic_category",
    "skipped_category_conflict",
    "jev_fallback",
    "jev_no_category",
    "routed",
]


def pre_call_status(
    understanding: QueryUnderstanding, *, enabled: bool, searchable: bool
) -> RoutingStatus | None:
    """Why Jev is not asked, or None when it is asked (one call)."""
    if not enabled:
        return "disabled"
    if not searchable:
        return "skipped_no_searchable_text"
    if understanding.category is not None:
        return "skipped_deterministic_category"
    if any(c.field is UnderstandingField.CATEGORY for c in understanding.conflicts):
        return "skipped_category_conflict"
    return None


def routing_category(outcome: JevOutcome) -> tuple[RoutingStatus, Category | None]:
    """The status after a call and the category that scopes the branch (if any)."""
    if outcome.fallback_reason is not None or outcome.category is None:
        return "jev_fallback", None
    label = outcome.category.label  # set only for a unique top label that is not `none`
    if label is None:
        return "jev_no_category", None
    return "routed", Category(label)


def top_category_probability(outcome: JevOutcome | None) -> float | None:
    """The raw provider probability of the unique top category label (recorded only)."""
    if outcome is None or outcome.category is None or len(outcome.category.top_labels) != 1:
        return None
    return outcome.category.provider_probabilities[outcome.category.top_labels[0]]


def routed_filters(base: FilterSpec, category: Category) -> FilterSpec:
    """The deterministic fp-1 filters plus Jev's category (only when no category is set)."""
    if not isinstance(base, FilterSpec) or base.category is not None:
        raise ValueError("the branch is only built when the deterministic filters set no category")
    return FilterSpec(**{**dict(base), "category": category})


@dataclass(frozen=True)
class RoutedCandidate:
    """One fused V4 candidate. The V3-source ranks and scores keep V3's meaning; the branch
    ranks say where the product was found in the Jev-scoped branch (None when it was not)."""

    product_id: str
    rrf_score: Fraction
    lexical_rank: int | None
    lexical_score: float | None
    dense_rank: int | None
    dense_score: float | None
    routed_lexical_rank: int | None
    routed_dense_rank: int | None

    @property
    def in_baseline_sources(self) -> bool:
        return self.lexical_rank is not None or self.dense_rank is not None


def _check_source(name: str, hits: Sequence[LexicalHit] | Sequence[DenseHit]) -> None:
    if [hit.rank for hit in hits] != list(range(1, len(hits) + 1)):
        raise ValueError(f"{name} ranks must be contiguous 1..n in list order")
    if len({hit.product_id for hit in hits}) != len(hits):
        raise ValueError(f"{name} product ids must be unique")


def fuse_routed(
    lexical_hits: Sequence[LexicalHit],
    dense_hits: Sequence[DenseHit],
    routed_lexical_hits: Sequence[LexicalHit],
    routed_dense_hits: Sequence[DenseHit],
    rrf_k: int,
    candidate_k: int,
) -> list[RoutedCandidate]:
    """RRF over the four lists with equal weights (pure; same order rule as `fuse_rrf`)."""
    if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
        raise ValueError("rrf_k must be a positive integer")
    if isinstance(candidate_k, bool) or not isinstance(candidate_k, int) or candidate_k < 1:
        raise ValueError("candidate_k must be a positive integer")
    sources = {
        "lexical": lexical_hits,
        "dense": dense_hits,
        "routed lexical": routed_lexical_hits,
        "routed dense": routed_dense_hits,
    }
    for name, hits in sources.items():
        _check_source(name, hits)
    by_source = [{hit.product_id: hit for hit in hits} for hits in sources.values()]
    lexical, dense, routed_lexical, routed_dense = by_source
    fused = []
    for product_id in set().union(*by_source):
        score = Fraction(0)
        for hits in by_source:
            hit = hits.get(product_id)
            if hit is not None:
                score += Fraction(1, rrf_k + hit.rank)
        lex, den = lexical.get(product_id), dense.get(product_id)
        r_lex, r_den = routed_lexical.get(product_id), routed_dense.get(product_id)
        fused.append(
            RoutedCandidate(
                product_id=product_id,
                rrf_score=score,
                lexical_rank=None if lex is None else lex.rank,
                lexical_score=None if lex is None else lex.lexical_score,
                dense_rank=None if den is None else den.rank,
                dense_score=None if den is None else den.dense_score,
                routed_lexical_rank=None if r_lex is None else r_lex.rank,
                routed_dense_rank=None if r_den is None else r_den.rank,
            )
        )
    fused.sort(key=lambda candidate: (-candidate.rrf_score, candidate.product_id))
    return fused[:candidate_k]


@dataclass(frozen=True)
class RoutedSources:
    lexical: LexicalResult
    dense: DenseResult
    routed_lexical: LexicalResult
    routed_dense: DenseResult
    # Records of every product in any of the four lists; empty unless records were requested.
    records: Mapping[str, CatalogRecord]
    document_read_ms: float | None


def read_routed_sources(
    session: Session,
    query: str,
    query_vector: Sequence[float],
    spec: EmbeddingModelSpec,
    filters: FilterSpec,
    branch_filters: FilterSpec,
    lexical_k: int,
    dense_k: int,
    *,
    with_records: bool,
) -> RoutedSources:
    """The V3 source reads, the branch reads and (optionally) the union's records, in one
    read-only snapshot. Errors behave as in `read_reranked_sources`."""
    if session.in_transaction():
        raise RuntimeError("read_routed_sources needs a session without an open transaction")
    filtered.active_fields(filters)
    filtered.active_fields(branch_filters)
    read_ms = 0.0
    product_ids: list[str] = []
    products: list = []
    specs: dict = {}
    with session.begin():
        session.execute(SNAPSHOT_SQL)  # must be the first statement of the transaction
        lexical = filtered.filtered_lexical_search(session, query, filters, lexical_k)
        dense = filtered.filtered_dense_search(session, query_vector, spec, filters, dense_k)
        routed_lexical = filtered.filtered_lexical_search(session, query, branch_filters, lexical_k)
        routed_dense = filtered.filtered_dense_search(
            session, query_vector, spec, branch_filters, dense_k
        )
        product_ids = sorted(
            {hit.product_id for result in (lexical, routed_lexical) for hit in result.hits}
            | {hit.product_id for result in (dense, routed_dense) for hit in result.hits}
        )
        if with_records and product_ids:
            read_started = time.perf_counter()
            products, specs = reranked._read_candidate_rows(session, product_ids)
            read_ms = time.perf_counter() - read_started
    if not (with_records and product_ids):
        return RoutedSources(
            lexical, dense, routed_lexical, routed_dense, MappingProxyType({}), None
        )
    build_started = time.perf_counter()
    records = reranked.build_candidate_records(product_ids, products, specs)
    read_ms += time.perf_counter() - build_started
    return RoutedSources(
        lexical,
        dense,
        routed_lexical,
        routed_dense,
        MappingProxyType(records),
        round(read_ms * 1000, 3),
    )
