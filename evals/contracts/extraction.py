"""What the frozen deterministic parser (qu-1) and filter policy (fp-1) do with each gold item.

Two facts are kept apart and never folded into one boolean:

* `fp1_filterable` (on the gold constraint): fp-1 *could* enforce this field once supplied;
* `status` / `fp1_applied` (here): what qu-1 actually extracted from *this exact query*, and
  whether fp-1 actually applied a filter on the field.

For example `bose headphones under 15000` has an fp-1-filterable price constraint, but qu-1
does not parse `under 15000`, so the price extraction is `not_extracted` and not applied.
Fields qu-1 has no slot for (processor, subcategory, gender, ANC, ...) and product
(series/model) intents are `no_parser_field`: they are never presented as parser output.

The records are generated: they depend only on the gold records and the frozen qu-1/lexicon/fp-1
code, carry no timestamp or handle, and regenerate byte for byte. Only qu-1's values for the
gold items are recorded; extra parser output is not.
"""

from collections.abc import Iterable, Sequence
from decimal import Decimal

from ecommerce_search.decision.provider import (
    DeterministicDecisionProvider,
    understand_normalized_query,
)
from ecommerce_search.filtering.policy import FilterField, FilterOperator, derive_filters
from ecommerce_search.query_understanding.models import QueryUnderstanding
from evals.contracts.models import (
    ExtractionStatus,
    ExtractionTarget,
    GoldConstraint,
    GoldField,
    ProductIntent,
    QueryExtraction,
    QueryRecord,
    canonical_gold_value,
)

# Gold fields qu-1 has no slot for.
NO_PARSER_FIELDS = frozenset(
    {
        GoldField.PROCESSOR,
        GoldField.SUBCATEGORY,
        GoldField.GENDER,
        GoldField.ANC,
        GoldField.WIRELESS,
        GoldField.CONNECTIVITY,
    }
)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(getattr(value, "value", value))


def _parsed(u: QueryUnderstanding, field: GoldField, operator: FilterOperator) -> str | None:
    values: dict[GoldField, object] = {
        GoldField.CATEGORY: u.category,
        GoldField.BRAND: u.brand,
        GoldField.RAM_GB: u.ram_gb,
        GoldField.STORAGE_GB: u.storage_gb,
        GoldField.STORAGE_TYPE: u.storage_type,
        GoldField.STORAGE_INTERFACE: u.attributes.storage_interface,
        GoldField.SEMANTIC_INTENT: u.semantic_intent,
        GoldField.PRICE_PREFERENCE: u.attributes.price_preference,
    }
    if field is GoldField.PRICE:
        return _text(u.max_price if operator is FilterOperator.LTE else u.min_price)
    return _text(values[field])


def _filter_field(field: GoldField, operator: FilterOperator) -> FilterField | None:
    if field is GoldField.PRICE:
        return FilterField.MAX_PRICE if operator is FilterOperator.LTE else FilterField.MIN_PRICE
    try:
        return FilterField(field.value)
    except ValueError:
        return None


def derive_query_extraction(
    queries: Sequence[QueryRecord],
    constraints: Iterable[GoldConstraint],
    intents: Iterable[ProductIntent],
) -> list[QueryExtraction]:
    """One record per gold constraint and per product intent, in canonical order."""
    provider = DeterministicDecisionProvider()
    texts = {q.query_id: q.text for q in queries}
    analysed: dict[str, tuple[QueryUnderstanding, frozenset[FilterField]]] = {}
    for query_id, text in texts.items():
        decision = understand_normalized_query(text, provider)
        applied = frozenset(f.field for f in derive_filters(decision).applied_filters)
        analysed[query_id] = (decision.understanding, applied)

    records: list[QueryExtraction] = []
    for c in constraints:
        understanding, applied = analysed[c.query_id]
        gold_value = canonical_gold_value(c.value)
        if c.field in NO_PARSER_FIELDS:
            status, parsed = ExtractionStatus.NO_PARSER_FIELD, None
        else:
            parsed = _parsed(understanding, c.field, c.operator)
            if parsed is None:
                status = ExtractionStatus.NOT_EXTRACTED
            elif parsed == gold_value:
                status = ExtractionStatus.EXTRACTED
            else:
                status = ExtractionStatus.EXTRACTED_OTHER_VALUE
        filter_field = _filter_field(c.field, c.operator)
        records.append(
            QueryExtraction(
                query_id=c.query_id,
                target=ExtractionTarget.GOLD_CONSTRAINT,
                field=c.field.value,
                operator=c.operator,
                gold_value=gold_value,
                status=status,
                parsed_value=parsed,
                fp1_filterable=c.fp1_filterable,
                fp1_applied=filter_field is not None and filter_field in applied,
            )
        )
    for intent in intents:
        records.append(
            QueryExtraction(
                query_id=intent.query_id,
                target=ExtractionTarget.PRODUCT_INTENT,
                field=intent.kind.value,
                operator=None,
                gold_value=intent.value,
                status=ExtractionStatus.NO_PARSER_FIELD,
                parsed_value=None,
                fp1_filterable=False,
                fp1_applied=False,
            )
        )
    return sorted(records, key=lambda r: r.sort_key())
