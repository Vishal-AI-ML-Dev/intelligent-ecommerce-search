"""Deterministic constraint eligibility: does a catalog product satisfy a query's hard gold
constraints? Pure and offline; it reads only the gold records and the read-only catalog index.

Per constraint the status is:

* `satisfied` / `violated`: the product has the attribute and it does / does not match;
* `unknown`: the attribute exists for the product's category but its value is missing;
* `not_applicable`: the attribute cannot exist for the product (for example `ram_gb` on shoes,
  or a shoe subcategory on a laptop). Like an fp-1 spec filter on a missing spec row, a hard
  constraint the product cannot have counts as a violation overall (`models.overall_status`).

Matching is literal and exact: brand and category equality, RAM/storage integer equality,
storage medium and interface compared separately (`storage_interface=NVME` never implies or
requires a `storage_type` constraint), processor string equality, inclusive price bounds.

Only hard constraints take part. Soft constraints, semantic intent, price preference and
product (series/model) intents never do. Eligibility is a catalog fact derived from the gold
records; it is never a relevance label and creates none.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ecommerce_search.catalog.taxonomy import SUBCATEGORIES
from ecommerce_search.filtering.policy import FilterOperator
from evals.contracts.canonical import ContractError
from evals.contracts.catalog import CatalogIndex, CatalogProduct
from evals.contracts.models import (
    FIELD_CATEGORIES,
    SOFT_ONLY_FIELDS,
    ConstraintCheck,
    ConstraintStatus,
    EligibilityRecord,
    GoldConstraint,
    GoldField,
    Hardness,
    OverallStatus,
    overall_status,
)

# Gold fields read from the product's category-specific spec.
_SPEC_FIELDS = frozenset(
    {
        GoldField.RAM_GB,
        GoldField.STORAGE_GB,
        GoldField.STORAGE_TYPE,
        GoldField.STORAGE_INTERFACE,
        GoldField.PROCESSOR,
        GoldField.GENDER,
        GoldField.ANC,
        GoldField.WIRELESS,
        GoldField.CONNECTIVITY,
    }
)


def _match(matches: bool) -> ConstraintStatus:
    return ConstraintStatus.SATISFIED if matches else ConstraintStatus.VIOLATED


def check_constraint(constraint: GoldConstraint, product: CatalogProduct) -> ConstraintStatus:
    """The status of one hard gold constraint for one product."""
    if constraint.hardness is not Hardness.HARD or constraint.field in SOFT_ONLY_FIELDS:
        raise ContractError("only hard gold constraints take part in eligibility")
    field, value = constraint.field, constraint.value
    if field is GoldField.CATEGORY:
        return _match(product.category.value == value)
    if field is GoldField.BRAND:
        return _match(product.brand == value)
    if field is GoldField.PRICE:
        bound = constraint.price_value()
        if constraint.operator is FilterOperator.LTE:
            return _match(product.price <= bound)
        return _match(product.price >= bound)
    if field is GoldField.SUBCATEGORY:
        if value not in SUBCATEGORIES[product.category]:
            return ConstraintStatus.NOT_APPLICABLE
        if product.subcategory is None:
            return ConstraintStatus.UNKNOWN
        return _match(product.subcategory == value)
    if field in _SPEC_FIELDS:
        allowed = FIELD_CATEGORIES.get(field)
        if allowed is not None and product.category not in allowed:
            return ConstraintStatus.NOT_APPLICABLE
        if field.value not in product.spec:
            return ConstraintStatus.NOT_APPLICABLE
        actual = product.spec[field.value]
        if actual is None:
            return ConstraintStatus.UNKNOWN
        return _match(actual == value and type(actual) is type(value))
    raise ContractError(f"no eligibility rule for {field.value}")  # pragma: no cover


def hard_constraints(query_id: str, constraints: Iterable[GoldConstraint]) -> list[GoldConstraint]:
    """The query's hard gold constraints, in canonical (field, operator) order."""
    hard = [c for c in constraints if c.query_id == query_id and c.hardness is Hardness.HARD]
    return sorted(hard, key=lambda c: (c.field.value, c.operator.value))


def evaluate(
    query_id: str, constraints: Iterable[GoldConstraint], product: CatalogProduct
) -> EligibilityRecord:
    """The eligibility record of one (query, product) pair."""
    checks = tuple(
        ConstraintCheck(
            field=c.field,
            operator=c.operator,
            fp1_filterable=c.fp1_filterable,
            status=check_constraint(c, product),
        )
        for c in hard_constraints(query_id, constraints)
    )
    return EligibilityRecord(
        query_id=query_id,
        product_id=product.product_id,
        checks=checks,
        hard_overall=overall_status([c.status for c in checks]),
        fp1_overall=overall_status([c.status for c in checks if c.fp1_filterable]),
    )


@dataclass(frozen=True)
class EligibilitySummary:
    """Catalog-wide eligibility of one query. Unknown and not-applicable evaluations are counted
    separately, never folded into satisfied or violated."""

    query_id: str
    hard_constraint_count: int
    satisfied: tuple[str, ...]  # product ids satisfying every hard constraint
    unknown: tuple[str, ...]  # no violation, at least one missing value
    violated_count: int  # products with at least one violated or not-applicable check
    not_applicable_count: int  # products with at least one not-applicable check
    unknown_check_count: int  # individual unknown checks over the whole catalog


def summarize(
    query_id: str, constraints: Sequence[GoldConstraint], catalog: CatalogIndex
) -> EligibilitySummary:
    hard = hard_constraints(query_id, constraints)
    satisfied: list[str] = []
    unknown: list[str] = []
    violated = not_applicable = unknown_checks = 0
    for product_id in sorted(catalog.products):
        record = evaluate(query_id, hard, catalog.products[product_id])
        statuses = [c.status for c in record.checks]
        unknown_checks += statuses.count(ConstraintStatus.UNKNOWN)
        not_applicable += ConstraintStatus.NOT_APPLICABLE in statuses
        if record.hard_overall is OverallStatus.SATISFIED:
            satisfied.append(product_id)
        elif record.hard_overall is OverallStatus.UNKNOWN:
            unknown.append(product_id)
        elif record.hard_overall is OverallStatus.VIOLATED:
            violated += 1
    return EligibilitySummary(
        query_id=query_id,
        hard_constraint_count=len(hard),
        satisfied=tuple(satisfied),
        unknown=tuple(unknown),
        violated_count=violated,
        not_applicable_count=not_applicable,
        unknown_check_count=unknown_checks,
    )
