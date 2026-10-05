"""Deterministic candidate-pool assembly under pool policy `pool-1` (M9 Session C2).

Pure and offline. Given every source's ranked response, the frozen gold constraints and the
read-only catalog index, it builds, per query:

1. the **source union**: every product any source returned (each source was called with
   `top_k = POOL_SOURCE_TOP_K = POOL_DEPTH`), deduplicated by product id;
2. **eligibility completion** (all-or-nothing): only for a query with at least one hard
   constraint and 1..`ELIGIBILITY_COMPLETION_CAP` products satisfying every hard constraint;
   every satisfied product not yet pooled is added. Zero eligible, more than the cap, or no hard
   constraint (out of catalog): nothing is added and nothing is truncated;
3. **deterministic negatives**: `NEGATIVES_PER_QUERY` products not yet pooled, ordered by
   `sha256("pool-1|query_id|product_id")`, two per tier:
   * same category: in the query's hard gold category and violating another hard constraint;
   * other category: outside the hard gold category (every product when the query has no hard
     category).
   A query with hard constraints only takes products whose hard status is `violated` (never
   satisfied, never unknown). A query without hard constraints takes ordinary catalog products
   as out-of-catalog probes. A short tier is filled from the other tier; what cannot be found is
   reported as a shortfall, never invented;
4. **blind order** `sha256("blind-1|query_id|product_id")`, independent of any source order;
5. the **pool cap** is checked by the caller (`validation.check_pool_cap`); nothing is trimmed.

The result is independent of input order: every input is re-keyed and sorted. No relevance
label is read or produced; eligibility is a catalog fact, not relevance.
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ecommerce_search.catalog.taxonomy import Category
from evals.contracts.canonical import ContractError, encode_json_object, encode_jsonl
from evals.contracts.cards import card_sha256
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import (
    ELIGIBILITY_COMPLETION_CAP,
    MAX_POOL_SIZE_PER_QUERY,
    NEGATIVES_PER_QUERY,
    NEGATIVES_PER_TIER,
    POOL_POLICY_VERSION,
)
from evals.contracts.eligibility import evaluate, hard_constraints
from evals.contracts.models import (
    SOURCE_ORDER,
    CompletionStatus,
    EligibilityRecord,
    GoldConstraint,
    GoldField,
    InclusionReason,
    OverallStatus,
    PoolItem,
    PoolProvenance,
    PoolReport,
    ProvenanceEntry,
    QueryPoolSummary,
    QueryRecord,
    RunContext,
    SourceHit,
    SourceRun,
    SourceSystem,
    SystemCount,
    run_id_for,
)
from evals.contracts.validation import blind_order_key


@dataclass(frozen=True)
class FetchedSource:
    """One source's response to one query: the private run record plus one score string (or
    None) per returned product, in rank order."""

    run: SourceRun
    scores: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if len(self.scores) != len(self.run.product_ids):
            raise ContractError(
                f"{self.run.query_id}/{self.run.system.value}: one score per returned product"
            )


@dataclass(frozen=True)
class PoolBuild:
    pool: tuple[PoolItem, ...]
    eligibility: tuple[EligibilityRecord, ...]
    provenance: PoolProvenance
    report: PoolReport

    def pool_bytes(self) -> bytes:
        return encode_jsonl(self.pool)

    def eligibility_bytes(self) -> bytes:
        return encode_jsonl(self.eligibility)

    def report_bytes(self) -> bytes:
        return encode_json_object(self.report)

    def provenance_bytes(self) -> bytes:
        return encode_json_object(self.provenance)


def negative_order_key(query_id: str, product_id: str) -> str:
    text = f"{POOL_POLICY_VERSION}|{query_id}|{product_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hard_category(hard: Sequence[GoldConstraint]) -> Category | None:
    for constraint in hard:
        if constraint.field is GoldField.CATEGORY:
            return Category(str(constraint.value))
    return None


@dataclass(frozen=True)
class NegativeSelection:
    same_category: tuple[str, ...]
    other_category: tuple[str, ...]
    shortfall: int


def select_negatives(
    query_id: str,
    hard: Sequence[GoldConstraint],
    statuses: Mapping[str, OverallStatus],
    pooled: Iterable[str],
    catalog: CatalogIndex,
) -> NegativeSelection:
    """The deterministic negatives of one query (see the module docstring)."""
    excluded = set(pooled)
    wanted = OverallStatus.VIOLATED if hard else OverallStatus.NO_CONSTRAINTS
    category = _hard_category(hard)
    same: list[str] = []
    other: list[str] = []
    for product_id, status in statuses.items():
        if product_id in excluded or status is not wanted:
            continue
        product = catalog.products[product_id]
        (same if category is not None and product.category is category else other).append(
            product_id
        )
    same.sort(key=lambda p: negative_order_key(query_id, p))
    other.sort(key=lambda p: negative_order_key(query_id, p))
    n_same = min(NEGATIVES_PER_TIER, len(same))
    n_other = min(NEGATIVES_PER_TIER, len(other))
    missing = NEGATIVES_PER_QUERY - n_same - n_other
    extra = min(missing, len(other) - n_other)
    n_other, missing = n_other + extra, missing - extra
    extra = min(missing, len(same) - n_same)
    n_same, missing = n_same + extra, missing - extra
    return NegativeSelection(tuple(same[:n_same]), tuple(other[:n_other]), missing)


def _index_sources(
    queries: Sequence[QueryRecord], sources: Iterable[FetchedSource], catalog: CatalogIndex
) -> dict[tuple[str, SourceSystem], FetchedSource]:
    query_ids = {q.query_id for q in queries}
    indexed: dict[tuple[str, SourceSystem], FetchedSource] = {}
    for source in sources:
        key = (source.run.query_id, source.run.system)
        if source.run.query_id not in query_ids:
            raise ContractError(f"a source response for unknown query {source.run.query_id!r}")
        if key in indexed:
            raise ContractError(f"two responses for {key[0]}/{key[1].value}")
        unknown = [p for p in source.run.product_ids if p not in catalog.products]
        if unknown:
            raise ContractError(f"{key[0]}/{key[1].value}: unknown products {unknown[:3]!r}")
        indexed[key] = source
    missing = [
        f"{q}/{s.value}" for q in sorted(query_ids) for s in SOURCE_ORDER if (q, s) not in indexed
    ]
    if missing:
        raise ContractError(f"missing source responses: {missing[:6]!r}")
    return indexed


def assemble_pool(
    queries: Sequence[QueryRecord],
    constraints: Sequence[GoldConstraint],
    catalog: CatalogIndex,
    sources: Iterable[FetchedSource],
    run_context: RunContext,
) -> PoolBuild:
    """Build the public pool, its eligibility records, the private provenance and the public
    aggregate report. Raises `ContractError` on any missing, duplicated or foreign input; it
    never trims a pool (the cap is checked separately)."""
    indexed = _index_sources(queries, sources, catalog)
    pool: list[PoolItem] = []
    eligibility: list[EligibilityRecord] = []
    entries: list[ProvenanceEntry] = []
    summaries: list[QueryPoolSummary] = []
    for query_id in sorted(q.query_id for q in queries):
        hard = hard_constraints(query_id, constraints)
        records = {
            product_id: evaluate(query_id, hard, catalog.products[product_id])
            for product_id in sorted(catalog.products)
        }
        statuses = {p: r.hard_overall for p, r in records.items()}
        hits: dict[str, list[SourceHit]] = {}
        for system in SOURCE_ORDER:
            source = indexed[(query_id, system)]
            for rank, (product_id, score) in enumerate(
                zip(source.run.product_ids, source.scores, strict=True), start=1
            ):
                hits.setdefault(product_id, []).append(
                    SourceHit(system=system, rank=rank, score=score)
                )
        union = set(hits)
        satisfied = sorted(p for p, s in statuses.items() if s is OverallStatus.SATISFIED)
        if not hard:
            completion = CompletionStatus.NO_HARD_CONSTRAINTS
        elif not satisfied:
            completion = CompletionStatus.ZERO_ELIGIBLE
        elif len(satisfied) > ELIGIBILITY_COMPLETION_CAP:
            completion = CompletionStatus.OVER_COMPLETION_CAP
        else:
            completion = CompletionStatus.APPLIED
        added = sorted(set(satisfied) - union) if completion is CompletionStatus.APPLIED else []
        negatives = select_negatives(query_id, hard, statuses, union | set(added), catalog)
        reasons: dict[str, InclusionReason] = dict.fromkeys(union, InclusionReason.SYSTEM_UNION)
        reasons.update(dict.fromkeys(added, InclusionReason.ELIGIBILITY_COMPLETION))
        reasons.update(
            dict.fromkeys(negatives.same_category, InclusionReason.NEGATIVE_SAME_CATEGORY)
        )
        reasons.update(
            dict.fromkeys(negatives.other_category, InclusionReason.NEGATIVE_OTHER_CATEGORY)
        )
        ordered = sorted(reasons, key=lambda p: blind_order_key(query_id, p))
        for position, product_id in enumerate(ordered, start=1):
            product = catalog.products[product_id]
            pool.append(
                PoolItem(
                    query_id=query_id,
                    product_id=product_id,
                    product_content_sha256=product.content_sha256,
                    card_sha256=card_sha256(product),
                    blind_position=position,
                )
            )
            eligibility.append(records[product_id])
            entries.append(
                ProvenanceEntry(
                    query_id=query_id,
                    product_id=product_id,
                    added_by=reasons[product_id],
                    sources=tuple(hits.get(product_id, ())),
                )
            )
        unknown = sum(1 for s in statuses.values() if s is OverallStatus.UNKNOWN)
        summaries.append(
            QueryPoolSummary(
                query_id=query_id,
                hard_constraint_count=len(hard),
                eligible_count=len(satisfied) if hard else None,
                unknown_count=unknown if hard else None,
                completion=completion,
                completion_added=len(added),
                source_result_counts=tuple(
                    SystemCount(system=s, count=len(indexed[(query_id, s)].run.product_ids))
                    for s in SOURCE_ORDER
                ),
                union_size=len(union),
                negatives_same_category=len(negatives.same_category),
                negatives_other_category=len(negatives.other_category),
                negative_shortfall=negatives.shortfall,
                pool_size=len(ordered),
                within_cap=len(ordered) <= MAX_POOL_SIZE_PER_QUERY,
            )
        )
    runs = sorted((s.run for s in indexed.values()), key=lambda r: r.sort_key())
    provenance = PoolProvenance(
        run_id=run_id_for(run_context),
        run_context=run_context,
        source_runs=tuple(runs),
        entries=tuple(sorted(entries, key=lambda e: e.sort_key())),
    )
    report = PoolReport(
        pool_policy=POOL_POLICY_VERSION,
        source_top_k=run_context.source_top_k,
        max_pool_size=MAX_POOL_SIZE_PER_QUERY,
        completion_cap=ELIGIBILITY_COMPLETION_CAP,
        negatives_per_query=NEGATIVES_PER_QUERY,
        queries=tuple(summaries),
        total_pool_items=len(pool),
        all_within_cap=all(s.within_cap for s in summaries),
    )
    return PoolBuild(
        pool=tuple(sorted(pool, key=lambda i: i.sort_key())),
        eligibility=tuple(sorted(eligibility, key=lambda e: e.sort_key())),
        provenance=provenance,
        report=report,
    )


def sources_from_provenance(provenance: PoolProvenance) -> list[FetchedSource]:
    """Recover every source response (ranked ids and scores) from a provenance bundle, so a
    frozen pool can be re-assembled offline and compared byte for byte."""
    scores: dict[tuple[str, SourceSystem, str], str | None] = {}
    for entry in provenance.entries:
        for hit in entry.sources:
            scores[(entry.query_id, hit.system, entry.product_id)] = hit.score
    return [
        FetchedSource(
            run=run,
            scores=tuple(scores[(run.query_id, run.system, p)] for p in run.product_ids),
        )
        for run in provenance.source_runs
    ]
