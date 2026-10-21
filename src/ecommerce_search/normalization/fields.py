"""Structured seller-field normalization (Milestone 13).

Each supported seller key yields one or more `AttributeCandidate`s with `basis=seller_field`.
Values are read as data only and normalized by the registered rules, reusing the M2 parsers
and tables; nothing is guessed, defaulted or rewritten beyond the rule:

* text fields collapse whitespace only (model and series names are otherwise unchanged);
* brand: a known brand or M2 alias -> canonical spelling, an unknown brand is unsupported;
* closed vocabularies match case- and whitespace-insensitively; other terms are unsupported;
* measures use the M2 unit tables and precision, then the M2 hard bounds; the M2 parser runs
  in a local exact decimal context so no long decimal is rounded before its precision check;
* storage_type accepts the combined M13 storage terms; a bare interface term is uncertain;
* booleans must be JSON true/false.

A wrong JSON type, too many decimal places (including a non-integral unit conversion), a value
outside the hard bounds or an unrepresentable number is invalid; an unknown unit or
unrecognised text is unsupported. Null and blank values are not handled here (see
`models.is_empty_value`).
"""

import decimal
from decimal import Decimal

from ecommerce_search.catalog import schemas as m2
from ecommerce_search.catalog import taxonomy as tx
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import JsonNumber, RawValue, raw_value_text
from ecommerce_search.normalization.models import AttributeCandidate, Outcome
from ecommerce_search.normalization.rules import Basis

_TEXT_RULE = nr.RULES_BY_ID["text.whitespace"]
_BOOLEAN_RULE = nr.RULES_BY_ID["boolean.strict"]


def make_candidate(
    field: str,
    key: str,
    raw_text: str,
    rule_id: str,
    outcome: Outcome,
    value: str | None = None,
    *,
    basis: Basis = Basis.SELLER_FIELD,
    span: tuple[int, int] | None = None,
) -> AttributeCandidate:
    normalized = outcome is Outcome.NORMALIZED
    return AttributeCandidate(
        field=field,
        basis=basis,
        source_key=key,
        raw_text=raw_text,
        span_start=None if span is None else span[0],
        span_end=None if span is None else span[1],
        outcome=outcome,
        normalized_value=value if normalized else None,
        value_type=nr.FIELD_VALUE_TYPES[field] if normalized else None,
        unit=nr.FIELD_UNITS.get(field) if normalized else None,
        rule_id=rule_id,
    )


def _exact_context() -> decimal.Context:
    return decimal.Context(
        prec=nr.MEASURE_DECIMAL_PREC,
        traps=[
            decimal.DivisionByZero,
            decimal.Inexact,
            decimal.InvalidOperation,
            decimal.Overflow,
        ],
    )


def _parse_exact(field: str, value: str | Decimal, dp: int) -> Decimal:
    """The reused M2 parser in a local, exact decimal context: the M2 unit multiplication and
    quantization either keep the exact value or raise, so a long decimal is never rounded
    before M2's precision check. The global decimal context is untouched."""
    units, _, prefixes = nr.MEASURE_PARSERS[nr.MEASURE_RULES[field]]
    with decimal.localcontext(_exact_context()):
        return m2.parse_decimal_measure(value, what=field, units=units, dp=dp, prefixes=prefixes)


def measure_outcome(field: str, value: str | Decimal) -> tuple[Outcome, str | None]:
    """Normalize one measure with the reused M2 parser, exact arithmetic, the field precision
    and the hard bounds."""
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        return Outcome.INVALID, None
    dp = nr.MEASURE_PARSERS[nr.MEASURE_RULES[field]][1]
    try:
        number = _parse_exact(field, value, dp)
    except (ValueError, ArithmeticError):
        if isinstance(value, Decimal):
            # A JSON number is always recognised: it has too many decimal places for the
            # field (also after the unit conversion) or cannot be represented.
            return Outcome.INVALID, None
        try:
            # A string has at most len(value) decimal places, so this precision accepts every
            # recognised number; failing here means an unknown unit or unrecognised text.
            _parse_exact(field, value, len(value))
        except (ValueError, ArithmeticError):
            return Outcome.UNSUPPORTED, None
        return Outcome.INVALID, None  # recognised, with more decimal places than allowed
    if not nr.within_bounds(field, number):
        return Outcome.INVALID, None
    return Outcome.NORMALIZED, str(number)


def _enum(field: str, text: str) -> tuple[Outcome, str | None]:
    try:
        member = m2.enum_parser(nr.FIELD_ENUMS[field], nullable=False).func(text)
    except ValueError:
        return Outcome.UNSUPPORTED, None
    return Outcome.NORMALIZED, str(member.value)


def normalize_seller_field(field: str, key: str, value: RawValue) -> list[AttributeCandidate]:
    """Candidates for one supported seller key whose value is not null or blank."""
    raw_text = raw_value_text(value)
    if raw_text is None:
        raise ValueError("null values are empty values, not candidates")

    def one(rule_id: str, outcome: Outcome, normalized: str | None = None) -> list:
        return [make_candidate(field, key, raw_text, rule_id, outcome, normalized)]

    if field in nr.MEASURE_RULES:
        rule_id = nr.MEASURE_RULES[field]
        if isinstance(value, JsonNumber):
            return one(rule_id, *measure_outcome(field, value.as_decimal()))
        if isinstance(value, str):
            return one(rule_id, *measure_outcome(field, value))
        return one(rule_id, Outcome.INVALID)
    if field in _BOOLEAN_RULE.fields:
        if isinstance(value, bool):
            return one(_BOOLEAN_RULE.rule_id, Outcome.NORMALIZED, raw_text)
        outcome = Outcome.UNSUPPORTED if isinstance(value, str) else Outcome.INVALID
        return one(_BOOLEAN_RULE.rule_id, outcome)

    if not isinstance(value, str):  # every remaining field is a string field
        rule_id = next(r.rule_id for r in nr.RULES if field in r.fields)
        return one(rule_id, Outcome.INVALID)
    cleaned = m2.clean_text(value)
    folded = cleaned.casefold()

    if field == "brand":
        brand = tx.BRAND_LOOKUP.get(folded)
        if brand is None:
            return one("brand.dictionary", Outcome.UNSUPPORTED)
        return one("brand.dictionary", Outcome.NORMALIZED, brand)
    if field == "subcategory":
        return one("subcategory.casefold", Outcome.NORMALIZED, folded)
    if field == "currency":
        if len(cleaned) == 3 and cleaned.isascii() and cleaned.isalpha():
            return one("currency.upper", Outcome.NORMALIZED, cleaned.upper())
        return one("currency.upper", Outcome.INVALID)
    if field == "storage_type":
        if folded in nr.STORAGE_TERMS:
            medium, interface = nr.STORAGE_TERMS[folded]
            return [
                make_candidate(
                    "storage_type", key, raw_text, "storage.term", Outcome.NORMALIZED, medium.value
                ),
                make_candidate(
                    "storage_interface",
                    key,
                    raw_text,
                    "storage.term",
                    Outcome.NORMALIZED,
                    interface.value,
                ),
            ]
        if folded in nr.STORAGE_TYPE_UNCERTAIN_TERMS:
            return one("storage.term", Outcome.UNCERTAIN)
    if field in nr.FIELD_ENUMS:
        return one("enum.casefold", *_enum(field, value))
    if field in _TEXT_RULE.fields:
        return one(_TEXT_RULE.rule_id, Outcome.NORMALIZED, cleaned)
    raise AssertionError(f"no seller rule for {field}")  # the registry covers every field
