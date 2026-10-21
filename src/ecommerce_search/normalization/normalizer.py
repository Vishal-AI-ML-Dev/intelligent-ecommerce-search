"""Pure listing normalization and checks (Milestone 13).

`normalize_listing(raw)` turns one validated `RawListing` into a `ListingNormalization`:

1. every supported seller key -> seller_field candidates (`fields`); a supported key with
   null or a blank string -> `empty_value` (supplied, but not a valid value, and not missing);
2. identity keys are skipped; every other key (including seller rating/review_count) ->
   `unsupported_attribute`, kept in the raw input only;
3. a string title/description -> unverified text candidates (`extraction`);
4. checks: `value_conflict` for every field with two or more different normalized values,
   `cross_field_contradiction` from `rules.CONTRADICTIONS`, `missing_required` for absent
   keys, and, only when the seller's category normalized, `incompatible_attribute`,
   `incompatible_subcategory`, `brand_category_mismatch` and `missing_expected`. Otherwise one
   `compatibility_unchecked` finding says those checks were not performed.

There is no winning value, the category is never inferred, and nothing is invented. The
function performs no I/O and needs no settings, database, network or model. Expected listing
value problems are recorded, never raised; a wrong argument type raises `TypeError`.
"""

from collections import defaultdict

from ecommerce_search.catalog import taxonomy as tx
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.extraction import extract_text_candidates
from ecommerce_search.normalization.fields import normalize_seller_field
from ecommerce_search.normalization.intake import RawListing
from ecommerce_search.normalization.models import (
    AttributeCandidate,
    Finding,
    FindingKind,
    ListingNormalization,
    Outcome,
    is_empty_value,
)
from ecommerce_search.normalization.rules import Basis

_UNCHECKED = "attribute, subcategory, brand-category and expected-field checks were not performed"


def _unsupported_message(field: str) -> str:
    if field in ("rating", "review_count"):
        return f"seller-supplied {field} is kept in the raw input only; not a verified catalog fact"
    if field in nr.UNSUPPORTED_SELLER_FIELDS:
        return f"catalog {field} cannot be asserted by a seller; kept in the raw input only"
    return "unknown key; kept in the raw input only and not normalized"


def _values(candidates: list[AttributeCandidate]) -> dict[str, dict[str, set[str]]]:
    """field -> normalized value -> bases it was read from."""
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for c in candidates:
        if c.outcome is Outcome.NORMALIZED:
            out[c.field][c.normalized_value].add(c.basis.value)
    return out


def _described(value: str, bases: set[str]) -> str:
    return f"{value} ({', '.join(sorted(bases))})"


def _checks(
    candidates: list[AttributeCandidate], empty_fields: set[str], present_fields: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    values = _values(candidates)

    for field, by_value in values.items():
        if len(by_value) > 1:
            described = "; ".join(_described(v, b) for v, b in sorted(by_value.items()))
            findings.append(
                Finding(
                    kind=FindingKind.VALUE_CONFLICT,
                    field=field,
                    message=f"{len(by_value)} different normalized values: {described}",
                )
            )

    for rule in nr.CONTRADICTIONS:
        left = values.get(rule.field, {})
        right = values.get(rule.related_field, {})
        for a in sorted(rule.values & left.keys()):
            for b in sorted(rule.related_values & right.keys()):
                findings.append(
                    Finding(
                        kind=FindingKind.CROSS_FIELD_CONTRADICTION,
                        field=rule.field,
                        related_field=rule.related_field,
                        message=f"{rule.field}={_described(a, left[a])} and "
                        f"{rule.related_field}={_described(b, right[b])}: {rule.reason}",
                    )
                )

    supplied = {c.field for c in candidates if c.basis is Basis.SELLER_FIELD} | empty_fields
    for field in sorted(nr.REQUIRED_FIELDS - supplied):
        findings.append(
            Finding(kind=FindingKind.MISSING_REQUIRED, field=field, message="key is absent")
        )

    category = [c for c in candidates if c.field == "category" and c.basis is Basis.SELLER_FIELD]
    if not category or category[0].outcome is not Outcome.NORMALIZED:
        if "category" in empty_fields:
            state = "category is null or blank"
        elif "category" not in present_fields:
            state = "category is absent"
        elif category[0].outcome is Outcome.INVALID:
            state = "category is not a string"
        else:
            state = "category is not in the closed taxonomy"
        findings.append(
            Finding(kind=FindingKind.COMPATIBILITY_UNCHECKED, message=f"{state}; {_UNCHECKED}")
        )
        return findings

    cat = tx.Category(category[0].normalized_value)
    for field in sorted((supplied & nr.ATTRIBUTE_FIELDS) - nr.CATEGORY_ATTRIBUTES[cat]):
        findings.append(
            Finding(
                kind=FindingKind.INCOMPATIBLE_ATTRIBUTE,
                field=field,
                message=f"{field} is not an attribute of category {cat.value}",
            )
        )
    for sub in sorted(values.get("subcategory", {})):
        if sub not in tx.SUBCATEGORIES[cat]:
            allowed = ", ".join(sorted(tx.SUBCATEGORIES[cat]))
            findings.append(
                Finding(
                    kind=FindingKind.INCOMPATIBLE_SUBCATEGORY,
                    field="subcategory",
                    message=f"{sub!r} is not a {cat.value} subcategory; allowed: {allowed}",
                )
            )
    for brand in sorted(values.get("brand", {})):
        if cat not in tx.BRAND_CATEGORIES[brand]:
            findings.append(
                Finding(
                    kind=FindingKind.BRAND_CATEGORY_MISMATCH,
                    field="brand",
                    message=f"{brand} is not expected in category {cat.value}",
                )
            )
    for field in nr.EXPECTED_FIELDS[cat]:
        if field not in supplied:
            findings.append(
                Finding(
                    kind=FindingKind.MISSING_EXPECTED,
                    field=field,
                    message=f"key is absent; expected for {cat.value}",
                )
            )
    return findings


def normalize_listing(raw: RawListing) -> ListingNormalization:
    if not isinstance(raw, RawListing):
        raise TypeError(f"expected RawListing, got {type(raw).__name__}")
    candidates: list[AttributeCandidate] = []
    findings: list[Finding] = []
    present: dict[str, str] = {}  # supported field -> exact raw key
    empty_fields: set[str] = set()

    for key, value in raw.fields:
        field = nr.canonical_key(key)
        if field in nr.IDENTITY_KEYS:
            continue
        if field not in nr.FIELD_VALUE_TYPES:
            findings.append(
                Finding(
                    kind=FindingKind.UNSUPPORTED_ATTRIBUTE,
                    source_key=key,
                    message=_unsupported_message(field),
                )
            )
            continue
        present[field] = key
        if is_empty_value(value):
            empty_fields.add(field)
            what = "null" if value is None else "a blank string"
            findings.append(
                Finding(
                    kind=FindingKind.EMPTY_VALUE,
                    field=field,
                    source_key=key,
                    message=f"key is present but its value is {what}; not a valid value",
                )
            )
            continue
        candidates.extend(normalize_seller_field(field, key, value))

    for basis in sorted(nr.TEXT_BASES):
        key = present.get(basis.value)
        if key is None:
            continue
        text = raw.value(key)
        if isinstance(text, str) and not is_empty_value(text):
            candidates.extend(extract_text_candidates(basis, key, text))

    findings.extend(_checks(candidates, empty_fields, set(present)))
    return ListingNormalization(
        rules_version=nr.RULES_VERSION,
        rules_digest=nr.rules_digest(),
        taxonomy_version=tx.TAXONOMY_VERSION,
        candidates=tuple(candidates),
        findings=tuple(findings),
    )
