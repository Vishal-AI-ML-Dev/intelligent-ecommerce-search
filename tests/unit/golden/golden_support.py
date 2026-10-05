"""Builders for Golden Dataset contract tests.

Every record built here is a SYNTHETIC TEST FIXTURE: hand-made to exercise the contracts. None
of it is a real query definition, gold constraint, pool, provenance or human label.
"""

from datetime import UTC, datetime, timedelta

from evals.contracts.constants import Label, Relation
from evals.contracts.models import (
    FP1_GOLD_FIELDS,
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
    Partition,
    PoolItem,
    QueryRecord,
    Round,
    overall_status,
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
