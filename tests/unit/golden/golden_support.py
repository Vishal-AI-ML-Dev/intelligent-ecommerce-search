"""Builders for Golden Dataset contract tests.

Every record built here is a SYNTHETIC TEST FIXTURE: hand-made to exercise the contracts. None
of it is a real query definition, gold constraint, pool, provenance or human label.
"""

from datetime import UTC, datetime, timedelta

from evals.contracts.cards import card_sha256
from evals.contracts.constants import Label, Relation
from evals.contracts.models import (
    FP1_GOLD_FIELDS,
    SOURCE_ORDER,
    CompletionStatus,
    Confidence,
    ConstraintCheck,
    ConstraintStatus,
    EligibilityRecord,
    ExpectedEligibility,
    GoldConstraint,
    GoldField,
    Hardness,
    IntentFamily,
    Judgement,
    LanguageStyle,
    NamedValue,
    Partition,
    PoolItem,
    PoolProvenance,
    PoolReport,
    ProvenanceEntry,
    QueryPoolSummary,
    QueryRecord,
    Round,
    RunContext,
    SourceRun,
    SystemCount,
    overall_status,
    run_id_for,
)
from evals.contracts.validation import blind_order_key

from ecommerce_search.filtering.policy import FilterOperator

T0 = datetime(2026, 10, 10, 12, 0, 0, 123456, tzinfo=UTC)
HANDLE = "ann-1"
RUBRIC = "rubric-1"

DEFAULT_RELATION = {
    Label.RELEVANT: Relation.SATISFIES_INTENT,
    Label.BORDERLINE: Relation.SUBSTITUTE,
    Label.NOT_RELEVANT: Relation.OFF_TARGET,
}


def query(
    query_id: str = "gq-p01",
    text: str = "fixture query",
    *,
    family: IntentFamily = IntentFamily.BROAD_CATEGORY,
    partition: Partition | None = None,
    seen: bool = False,
    interpretations: tuple[str, ...] = (),
    style: LanguageStyle = LanguageStyle.ENGLISH,
) -> QueryRecord:
    if partition is None:
        partition = Partition.TEST if query_id.startswith("gq-t") else Partition.PILOT_DEV
    return QueryRecord(
        query_id=query_id,
        text=text,
        language_style=style,
        intent_family=family,
        interpretations=interpretations,
        expected_eligibility=ExpectedEligibility.UNKNOWN,
        seen_in_development=seen,
        partition=partition,
    )


def gold(
    query_id: str,
    field: GoldField,
    value,
    *,
    operator: FilterOperator = FilterOperator.EQ,
    hardness: Hardness = Hardness.HARD,
) -> GoldConstraint:
    return GoldConstraint(
        query_id=query_id,
        field=field,
        operator=operator,
        value=value,
        hardness=hardness,
        fp1_filterable=field in FP1_GOLD_FIELDS,
        annotator_handle=HANDLE,
        recorded_at=T0,
    )


def pool_for(query_id: str, product_ids: list[str], catalog) -> list[PoolItem]:
    ordered = sorted(product_ids, key=lambda p: blind_order_key(query_id, p))
    return [
        PoolItem(
            query_id=query_id,
            product_id=product_id,
            product_content_sha256=catalog.products[product_id].content_sha256,
            card_sha256=card_sha256(catalog.products[product_id]),
            blind_position=position,
        )
        for position, product_id in enumerate(ordered, start=1)
    ]


def judgement(
    query_id: str,
    product_id: str,
    label: Label = Label.RELEVANT,
    *,
    round_: Round = Round.PRIMARY,
    relation: Relation | None = None,
    confidence: Confidence = Confidence.HIGH,
    rationale: str | None = None,
    at: datetime = T0,
    handle: str = HANDLE,
    **extra,
) -> Judgement:
    if rationale is None and (label is Label.BORDERLINE or confidence is Confidence.LOW):
        rationale = "Fixture rationale."
    return Judgement(
        query_id=query_id,
        product_id=product_id,
        round=round_,
        label=label,
        relation=relation or DEFAULT_RELATION[label],
        confidence=confidence,
        rationale=rationale,
        annotator_handle=handle,
        annotated_at=at,
        rubric_version=RUBRIC,
        **extra,
    )


def eligibility(
    query_id: str, product_id: str, checks: list[tuple[GoldField, FilterOperator, ConstraintStatus]]
) -> EligibilityRecord:
    built = sorted(
        (
            ConstraintCheck(
                field=f, operator=op, fp1_filterable=f in FP1_GOLD_FIELDS, status=status
            )
            for f, op, status in checks
        ),
        key=lambda c: (c.field.value, c.operator.value),
    )
    return EligibilityRecord(
        query_id=query_id,
        product_id=product_id,
        checks=tuple(built),
        hard_overall=overall_status([c.status for c in built]),
        fp1_overall=overall_status([c.status for c in built if c.fp1_filterable]),
    )


def after_delay(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def run_context(**overrides) -> RunContext:
    """A SYNTHETIC run context (fixture values, not a real run)."""
    fields = dict(
        queries_manifest_sha256="1" * 64,
        dataset_id="synthetic-seed",
        dataset_version="1",
        dataset_checksum_sha256="2c1c5fa43eec5f7346df40c17e57c07f6594ab1833c5d9d4c862e6d2e8bbbbc8",
        pool_policy="pool-1",
        blind_order="blind-1",
        source_top_k=20,
        settings=(NamedValue(name="search_rrf_k", value="100"),),
        model_pins=(NamedValue(name="embedding_model_id", value="fixture"),),
        component_versions=(NamedValue(name="parser", value="qu-1"),),
        library_versions=(NamedValue(name="torch", value="fixture"),),
        runtime_trees=(NamedValue(name="src", value="0" * 40),),
    )
    fields.update(overrides)
    return RunContext(**fields)


def provenance_bundle(entries: list[ProvenanceEntry], context: RunContext | None = None):
    """A SYNTHETIC provenance bundle whose source runs are derived from the entries' hits
    (every (query, system) must have contiguous ranks 1..n)."""
    context = context or run_context()
    ranked: dict[tuple[str, object], dict[int, str]] = {}
    for entry in entries:
        for hit in entry.sources:
            ranked.setdefault((entry.query_id, hit.system), {})[hit.rank] = entry.product_id
    runs = []
    for (query_id, system), by_rank in ranked.items():
        assert sorted(by_rank) == list(range(1, len(by_rank) + 1))
        runs.append(
            SourceRun(
                query_id=query_id,
                system=system,
                endpoint="/search",
                search_version="v0_lexical",
                top_k=context.source_top_k,
                product_ids=tuple(by_rank[r] for r in sorted(by_rank)),
                reranker=None
                if system.value not in ("v3_reranked_d10", "v3_reranked_d20")
                else {"enabled": True, "depth": 10, "status": "applied", "reranked_count": 1},
            )
        )
    return PoolProvenance(
        run_id=run_id_for(context),
        run_context=context,
        source_runs=tuple(sorted(runs, key=lambda r: r.sort_key())),
        entries=tuple(sorted(entries, key=lambda e: e.sort_key())),
    )


def pool_report(pool: list[PoolItem]) -> PoolReport:
    """A SYNTHETIC aggregate report consistent with `pool` (no hard constraints, no sources)."""
    sizes: dict[str, int] = {}
    for item in pool:
        sizes[item.query_id] = sizes.get(item.query_id, 0) + 1
    summaries = tuple(
        QueryPoolSummary(
            query_id=query_id,
            hard_constraint_count=0,
            eligible_count=None,
            unknown_count=None,
            completion=CompletionStatus.NO_HARD_CONSTRAINTS,
            completion_added=0,
            source_result_counts=tuple(SystemCount(system=s, count=0) for s in SOURCE_ORDER),
            union_size=size,
            negatives_same_category=0,
            negatives_other_category=0,
            negative_shortfall=4,
            pool_size=size,
            within_cap=size <= 60,
        )
        for query_id, size in sorted(sizes.items())
    )
    return PoolReport(
        pool_policy="pool-1",
        source_top_k=20,
        max_pool_size=60,
        completion_cap=25,
        negatives_per_query=4,
        queries=summaries,
        total_pool_items=len(pool),
        all_within_cap=all(s.within_cap for s in summaries),
    )
