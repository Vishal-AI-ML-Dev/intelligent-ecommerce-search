"""Versioned listing-normalization rule registry (Milestone 13).

`rules_digest()` is a rule/configuration digest. It covers the field contracts, the rule
registry, the M13 tables and patterns, the M2 tables M13 reuses, and a fingerprint of the
reused M2 parser *behaviour* on fixed probe inputs. A pinned test then forces an explicit
`RULES_VERSION` decision when any of these change.

It does not cover M13's own code in `fields`, `extraction` and `normalizer` (outcome
classification, currency format, qualifier handling, check and message logic). Those are
guarded only by the literal-expected behaviour-regression tests
(`tests/unit/normalization/test_listing_behaviour_guard.py`), which detect changes to the
probed behaviour and nothing else. No implementation identity is recorded.

These are deterministic engineering rules, not measured marketplace facts.

The M2 catalog alias set (`taxonomy.BRAND_LOOKUP`) is read, never extended here: it also feeds
buyer-query parsing and catalog ingestion.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from ecommerce_search.catalog import schemas as m2
from ecommerce_search.catalog import taxonomy as tx
from ecommerce_search.catalog.quality import parameters as qp

RULES_VERSION = "listing-norm-1"

# ---- keys --------------------------------------------------------------------------------

IDENTITY_KEYS = ("seller_id", "seller_listing_ref")


def canonical_key(key: str) -> str:
    """Key matching form: trimmed and casefolded. Used to match seller keys to catalog fields
    and to detect colliding keys. Identity keys and identity values are never matched this way."""
    return key.strip().casefold()


class Basis(StrEnum):
    """Where a candidate value was read from."""

    SELLER_FIELD = "seller_field"  # the seller's own structured key/value
    TITLE = "title"  # an unverified extraction from the title text
    DESCRIPTION = "description"  # an unverified extraction from the description text


TEXT_BASES = frozenset({Basis.TITLE, Basis.DESCRIPTION})


class ValueType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"
    ENUM = "enum"
    BOOLEAN = "boolean"


# ---- field contracts ---------------------------------------------------------------------

# Core catalog fields a seller listing may supply and M13 normalizes.
CORE_FIELDS = (
    "title",
    "description",
    "category",
    "subcategory",
    "brand",
    "price",
    "currency",
    "availability",
)
# Category attributes, exactly the M2 per-category attribute models.
ATTRIBUTE_FIELDS: frozenset[str] = frozenset(
    name for model in m2.SPEC_MODELS.values() for name in model.model_fields
)
CATEGORY_ATTRIBUTES: dict[tx.Category, frozenset[str]] = {
    category: frozenset(model.model_fields) for category, model in m2.SPEC_MODELS.items()
}
# Kept in raw input but never normalized: a seller cannot assert ratings, review counts or
# catalog identity/provenance, so these are flagged unsupported, not treated as facts.
UNSUPPORTED_SELLER_FIELDS = frozenset(
    {"rating", "review_count", "product_id", "source_type", "is_synthetic"}
)
# Core fields the validated catalog requires (M2 `CoreFields` without identity/provenance).
REQUIRED_FIELDS = frozenset({"title", "category", "brand", "price", "currency", "availability"})
EXPECTED_FIELDS: dict[tx.Category, tuple[str, ...]] = dict(qp.EXPECTED_FIELDS)

FIELD_VALUE_TYPES: dict[str, ValueType] = {
    "title": ValueType.TEXT,
    "description": ValueType.TEXT,
    "category": ValueType.ENUM,
    "subcategory": ValueType.TEXT,
    "brand": ValueType.TEXT,
    "price": ValueType.DECIMAL,
    "currency": ValueType.TEXT,
    "availability": ValueType.ENUM,
    "ram_gb": ValueType.INTEGER,
    "storage_gb": ValueType.INTEGER,
    "storage_type": ValueType.ENUM,
    "storage_interface": ValueType.ENUM,
    "processor": ValueType.TEXT,
    "gpu": ValueType.TEXT,
    "screen_size_inches": ValueType.DECIMAL,
    "operating_system": ValueType.TEXT,
    "weight_kg": ValueType.DECIMAL,
    "camera": ValueType.TEXT,
    "battery_mah": ValueType.INTEGER,
    "size": ValueType.DECIMAL,
    "size_system": ValueType.ENUM,
    "color": ValueType.TEXT,
    "material": ValueType.TEXT,
    "gender": ValueType.ENUM,
    "wireless": ValueType.BOOLEAN,
    "anc": ValueType.BOOLEAN,
    "battery_life_hours": ValueType.DECIMAL,
    "connectivity": ValueType.ENUM,
}
FIELD_ENUMS: dict[str, type[Enum]] = {
    "category": tx.Category,
    "availability": tx.Availability,
    "storage_type": tx.StorageType,
    "storage_interface": tx.StorageInterface,
    "size_system": tx.SizeSystem,
    "gender": tx.Gender,
    "connectivity": tx.Connectivity,
}
# Exact decimal places of a normalized decimal value (the M2 field precision).
FIELD_DECIMAL_PLACES: dict[str, int] = {
    "price": 2,
    "screen_size_inches": 1,
    "weight_kg": 2,
    "size": 1,
    "battery_life_hours": 1,
}
# Canonical unit of a normalized measure; fields not listed have no unit.
FIELD_UNITS: dict[str, str] = {
    "ram_gb": "GB",
    "storage_gb": "GB",
    "screen_size_inches": "in",
    "weight_kg": "kg",
    "battery_mah": "mAh",
    "battery_life_hours": "h",
}

# ---- M13 tables --------------------------------------------------------------------------

# Combined storage terms in a seller's storage_type value -> (medium, interface). A bare
# interface term ("nvme") is not listed: the medium is never derived from it.
STORAGE_TERMS: dict[str, tuple[tx.StorageType, tx.StorageInterface]] = {
    "nvme ssd": (tx.StorageType.SSD, tx.StorageInterface.NVME),
    "ssd nvme": (tx.StorageType.SSD, tx.StorageInterface.NVME),
    "sata ssd": (tx.StorageType.SSD, tx.StorageInterface.SATA),
    "ssd sata": (tx.StorageType.SSD, tx.StorageInterface.SATA),
}
# A bare interface term given as the seller's storage_type is recorded as uncertain: it names
# an interface, not a medium, and the medium is never derived from it.
STORAGE_TYPE_UNCERTAIN_TERMS = frozenset(str(i.value).casefold() for i in tx.StorageInterface)

# Storage terms in title/description text -> (medium or None, interface or None). A bare
# "nvme" yields an interface candidate only, never an SSD medium candidate.
TEXT_STORAGE_TERMS: dict[str, tuple[tx.StorageType | None, tx.StorageInterface | None]] = {
    "ssd": (tx.StorageType.SSD, None),
    "hdd": (tx.StorageType.HDD, None),
    "nvme": (None, tx.StorageInterface.NVME),
    **STORAGE_TERMS,
}

# Hard bounds of a normalized measure, mirroring the M2 field types: (lower, lower bound
# inclusive, upper bound inclusive). A parsed value outside them is invalid.
MEASURE_BOUNDS: dict[str, tuple[Decimal, bool, Decimal]] = {
    "price": (Decimal(0), False, qp.PRICE_MAX),
    "ram_gb": (Decimal(1), True, Decimal(qp.RAM_GB_MAX)),
    "storage_gb": (Decimal(1), True, Decimal(qp.STORAGE_GB_MAX)),
    "screen_size_inches": (Decimal(0), False, qp.SCREEN_INCHES_MAX),
    "weight_kg": (Decimal(0), False, qp.WEIGHT_KG_MAX),
    "battery_mah": (Decimal(1), True, Decimal(qp.BATTERY_MAH_MAX)),
    "battery_life_hours": (Decimal(0), False, qp.BATTERY_HOURS_MAX),
    "size": (Decimal(0), False, qp.SHOE_SIZE_MAX),
}
# Precision of the local decimal context the reused M2 measure parser runs in (Inexact
# trapped). An intake-bounded value has at most MAX_RAW_BYTES (65,536) coefficient digits and
# decimal places, and every unit multiplier is an integer of at most 4 digits, so the M2 unit
# multiplication and quantization are exact or raise; nothing is silently rounded. The global
# decimal context is not changed.
MEASURE_DECIMAL_PREC = 2 * 65_536 + 64


def within_bounds(field: str, number: Decimal) -> bool:
    lower, inclusive, upper = MEASURE_BOUNDS[field]
    return (number >= lower if inclusive else number > lower) and number <= upper


# Title/description patterns (Python `re`, no flags; letters are matched ASCII-case-
# insensitively through scoped `(?ai:...)` groups, digits through `[0-9]`, separators are
# literal spaces). Every quantifier is bounded and nothing is nested, so a scan is linear in
# the (intake-bounded) text length. A qualifier such as "up to" makes the match uncertain.
_QUALIFIER = (
    r"(?:(?P<qualifier>(?ai:up[ ]?to|upgrad(?:e)?able[ ]to|expandable(?:[ ]up)?[ ]to"
    r"|max(?:imum)?|supports?))[ ]{1,2})?"
)
TEXT_PATTERNS: dict[str, str] = {
    "title.ram_pattern": (
        r"(?<![\w.,/])" + _QUALIFIER + r"(?P<measure>[1-9][0-9]{0,3}[ ]{0,2}(?ai:GB))"
        r"[ ]{1,2}(?ai:RAM)(?!\w)"
    ),
    "title.storage_pattern": (
        r"(?<![\w.,/])" + _QUALIFIER + r"(?P<measure>[1-9][0-9]{0,4}(?:\.[0-9]{1,2})?[ ]{0,2}"
        r"(?ai:GB|TB))[ ]{1,2}(?P<term>(?ai:NVMe[ ]SSD|SSD[ ]NVMe|SATA[ ]SSD|SSD[ ]SATA"
        r"|NVMe|SSD|HDD))(?!\w)"
    ),
}


@dataclass(frozen=True)
class Contradiction:
    """Normalized `field` values that contradict normalized `related_field` values (any
    basis). These mirror the M2 attribute-model validators; nothing else is a contradiction."""

    field: str
    values: frozenset[str]
    related_field: str
    related_values: frozenset[str]
    reason: str

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "values": sorted(self.values),
            "related_field": self.related_field,
            "related_values": sorted(self.related_values),
            "reason": self.reason,
        }


CONTRADICTIONS: tuple[Contradiction, ...] = (
    Contradiction(
        "storage_interface",
        frozenset({tx.StorageInterface.NVME.value}),
        "storage_type",
        frozenset(m.value for m in tx.StorageType) - {tx.StorageType.SSD.value},
        "NVME requires an SSD medium",
    ),
    Contradiction(
        "connectivity",
        frozenset({tx.Connectivity.WIRED.value}),
        "wireless",
        frozenset({"true"}),
        "wired connectivity contradicts wireless=true",
    ),
    Contradiction(
        "connectivity",
        frozenset({tx.Connectivity.BLUETOOTH.value, tx.Connectivity.WIRELESS_2_4GHZ.value}),
        "wireless",
        frozenset({"false"}),
        "wireless connectivity contradicts wireless=false",
    ),
)

# ---- rule registry -----------------------------------------------------------------------

MEASURE_FIELDS = {
    "measure.price": "price",
    "measure.ram": "ram_gb",
    "measure.capacity": "storage_gb",
    "measure.screen": "screen_size_inches",
    "measure.weight": "weight_kg",
    "measure.battery_mah": "battery_mah",
    "measure.battery_hours": "battery_life_hours",
    "measure.shoe_size": "size",
}
MEASURE_RULES = {field: rule_id for rule_id, field in MEASURE_FIELDS.items()}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    fields: frozenset[str]
    bases: frozenset[Basis]
    description: str
    reuses: str | None = None  # the Milestone 2 code or table the rule delegates to
    # True when the source key may differ from the target field (a combined term).
    cross_field: bool = False

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "fields": sorted(self.fields),
            "bases": sorted(b.value for b in self.bases),
            "description": self.description,
            "reuses": self.reuses,
            "cross_field": self.cross_field,
        }


_SELLER = frozenset({Basis.SELLER_FIELD})

RULES: tuple[Rule, ...] = (
    Rule(
        "text.whitespace",
        frozenset(
            {
                "title",
                "description",
                "processor",
                "gpu",
                "operating_system",
                "camera",
                "color",
                "material",
            }
        ),
        _SELLER,
        "collapse whitespace only; the text (including model names) is otherwise unchanged",
        "catalog.schemas.clean_text",
    ),
    Rule(
        "brand.dictionary",
        frozenset({"brand"}),
        _SELLER,
        "known brand or alias (whitespace-collapsed, casefolded) -> canonical spelling; "
        "an unknown brand is unsupported and kept as given",
        "catalog.taxonomy.BRAND_LOOKUP",
    ),
    Rule(
        "subcategory.casefold",
        frozenset({"subcategory"}),
        _SELLER,
        "collapse whitespace and casefold",
        "catalog.schemas.clean_text",
    ),
    Rule(
        "currency.upper",
        frozenset({"currency"}),
        _SELLER,
        "collapse whitespace and uppercase; must be three letters A-Z",
        "catalog.schemas.clean_text",
    ),
    Rule(
        "enum.casefold",
        frozenset(FIELD_ENUMS),
        _SELLER,
        "case- and whitespace-insensitive match against the closed taxonomy value set",
        "catalog.schemas.enum_parser",
    ),
    Rule(
        "storage.term",
        frozenset({"storage_type", "storage_interface"}),
        _SELLER,
        "combined storage term in storage_type -> medium and interface (STORAGE_TERMS); a "
        "bare interface term in storage_type is uncertain (STORAGE_TYPE_UNCERTAIN_TERMS)",
        None,
        cross_field=True,
    ),
    *(
        Rule(
            rule_id,
            frozenset({field}),
            _SELLER,
            f"number or <number><unit> -> {field} in its canonical unit and precision; "
            "unknown units and excess precision are not normalized",
            "catalog.schemas.parse_decimal_measure",
        )
        for rule_id, field in MEASURE_FIELDS.items()
    ),
    Rule(
        "boolean.strict",
        frozenset({"wireless", "anc"}),
        _SELLER,
        "JSON true/false only; strings such as 'yes' are not normalized",
        "pydantic.StrictBool (catalog.schemas)",
    ),
    Rule(
        "title.ram_pattern",
        frozenset({"ram_gb"}),
        TEXT_BASES,
        "explicit '<N> GB RAM' text (TEXT_PATTERNS) -> unverified ram_gb candidate; "
        "a qualifier such as 'up to' makes it uncertain",
        None,
    ),
    Rule(
        "title.storage_pattern",
        frozenset({"storage_gb", "storage_type", "storage_interface"}),
        TEXT_BASES,
        "explicit '<N> GB|TB <storage term>' text (TEXT_PATTERNS, TEXT_STORAGE_TERMS) -> "
        "unverified storage candidates; a bare NVMe term never implies SSD; a qualifier "
        "such as 'up to' makes them uncertain",
        None,
    ),
)
RULES_BY_ID: dict[str, Rule] = {rule.rule_id: rule for rule in RULES}

# ---- Milestone 2 behaviour fingerprint ---------------------------------------------------

# (units, decimal places, currency prefixes allowed) per reused measure rule.
MEASURE_PARSERS: dict[str, tuple[Mapping[str, Decimal], int, bool]] = {
    "measure.price": (m2.NO_UNITS, 2, True),
    "measure.ram": (m2.RAM_UNITS, 0, False),
    "measure.capacity": (m2.CAPACITY_UNITS, 0, False),
    "measure.screen": (m2.SCREEN_UNITS, 1, False),
    "measure.weight": (m2.WEIGHT_UNITS, 2, False),
    "measure.battery_mah": (m2.BATTERY_UNITS, 0, False),
    "measure.battery_hours": (m2.HOUR_UNITS, 1, False),
    "measure.shoe_size": (m2.NO_UNITS, 1, False),
}
_MEASURE_PROBES: dict[str, tuple[Any, ...]] = {
    "measure.price": ("₹40,000", "Rs. 40000", "INR 40000.5", 33999, "40k", "40000.123"),
    "measure.ram": ("16GB", " 16 gb ", "16", 8, "16 GiB", "16.5GB"),
    "measure.capacity": ("1TB", "0.5 TB", "1,024GB", "512gb", "1 TB SSD", "1.5TB"),
    "measure.screen": ('15.6"', "14 inch", "13.3 inches", "14.05in", Decimal("14")),
    "measure.weight": ("1.5kg", "1.234 kg", "1500 g", Decimal("1.5")),
    "measure.battery_mah": ("5000mAh", "5000 MAH", "5,000 mah"),
    "measure.battery_hours": ("30 hrs", "7.5h", "30 min"),
    "measure.shoe_size": ("9", "9.5", "UK 9", True),
}
_TEXT_PROBES = ("  HP   Pavilion \t 14 ", " x ", "   ", "Ryzen 7")
_ENUM_PROBES: tuple[tuple[type[Enum], str], ...] = (
    (tx.Category, " LAPTOP "),
    (tx.StorageType, "ssd"),
    (tx.StorageType, "solid state"),
    (tx.StorageInterface, "NVMe"),
    (tx.Connectivity, "Bluetooth"),
)


def _probe(fn: Callable[[], Any]) -> str | None:
    try:
        result = fn()
    except ValueError:
        return "error"
    if isinstance(result, Enum):
        return str(result.value)
    return None if result is None else str(result)


def m2_behaviour_fingerprint() -> dict:
    """Outputs of the reused M2 parsers on fixed probe inputs ("error" when M2 rejects)."""
    measures = {}
    for rule_id, probes in _MEASURE_PROBES.items():
        units, dp, prefixes = MEASURE_PARSERS[rule_id]
        measures[rule_id] = [
            [
                repr(value),
                _probe(
                    lambda v=value, w=rule_id, u=units, d=dp, p=prefixes: m2.parse_decimal_measure(
                        v, what=w, units=u, dp=d, prefixes=p
                    )
                ),
            ]
            for value in probes
        ]
    enums = [
        [
            cls.__name__,
            value,
            _probe(lambda c=cls, v=value: m2.enum_parser(c, nullable=False).func(v)),
        ]
        for cls, value in _ENUM_PROBES
    ]
    text = [[repr(value), _probe(lambda v=value: m2.clean_text(v))] for value in _TEXT_PROBES]
    return {"measures": measures, "enums": enums, "clean_text": text}


# ---- registry dump and digest ------------------------------------------------------------


def _units(units: Mapping[str, Decimal]) -> dict[str, str]:
    return {unit: str(multiplier) for unit, multiplier in sorted(units.items())}


def rules_as_dict() -> dict:
    """Every table and rule that decides a normalized listing value, plus the M2 fingerprint."""
    return {
        "rules_version": RULES_VERSION,
        "taxonomy_version": tx.TAXONOMY_VERSION,
        "m2_transform_version": tx.TRANSFORM_VERSION,
        "m2_quality_rules_version": qp.RULES_VERSION,
        "identity_keys": list(IDENTITY_KEYS),
        "key_matching": "strip + casefold, exact catalog field names only, no key aliases",
        "core_fields": list(CORE_FIELDS),
        "required_fields": sorted(REQUIRED_FIELDS),
        "unsupported_seller_fields": sorted(UNSUPPORTED_SELLER_FIELDS),
        "category_attributes": {c.value: sorted(f) for c, f in CATEGORY_ATTRIBUTES.items()},
        "expected_fields": {c.value: list(f) for c, f in EXPECTED_FIELDS.items()},
        "field_value_types": {f: t.value for f, t in sorted(FIELD_VALUE_TYPES.items())},
        "field_enums": {f: [str(m.value) for m in e] for f, e in sorted(FIELD_ENUMS.items())},
        "field_decimal_places": dict(sorted(FIELD_DECIMAL_PLACES.items())),
        "field_units": dict(sorted(FIELD_UNITS.items())),
        "subcategories": {c.value: sorted(s) for c, s in tx.SUBCATEGORIES.items()},
        "brand_lookup": dict(sorted(tx.BRAND_LOOKUP.items())),
        "brand_categories": {
            b: sorted(c.value for c in cats) for b, cats in sorted(tx.BRAND_CATEGORIES.items())
        },
        "m2_units": {
            "ram": _units(m2.RAM_UNITS),
            "capacity": _units(m2.CAPACITY_UNITS),
            "screen": _units(m2.SCREEN_UNITS),
            "weight": _units(m2.WEIGHT_UNITS),
            "battery": _units(m2.BATTERY_UNITS),
            "hours": _units(m2.HOUR_UNITS),
        },
        "m2_hard_bounds": qp.parameters_as_dict()["hard_bounds"],
        "storage_terms": {
            term: [medium.value, interface.value]
            for term, (medium, interface) in sorted(STORAGE_TERMS.items())
        },
        "storage_type_uncertain_terms": sorted(STORAGE_TYPE_UNCERTAIN_TERMS),
        "text_storage_terms": {
            term: [None if m is None else m.value, None if i is None else i.value]
            for term, (m, i) in sorted(TEXT_STORAGE_TERMS.items())
        },
        "text_patterns": dict(sorted(TEXT_PATTERNS.items())),
        "text_extraction": "title and description only, exact raw text, code-point spans, "
        "never the category; each pattern scanned left to right without overlap",
        "measure_bounds": {
            f: [str(lo), "inclusive" if inc else "exclusive", str(hi)]
            for f, (lo, inc, hi) in sorted(MEASURE_BOUNDS.items())
        },
        "measure_arithmetic": {
            "decimal_prec": MEASURE_DECIMAL_PREC,
            "traps": ["DivisionByZero", "Inexact", "InvalidOperation", "Overflow"],
            "outcomes": "normalized: exact value at the field precision within bounds; "
            "invalid: wrong JSON type, more decimal places than the field allows (including "
            "non-integral results of a unit conversion), outside the hard bounds, or not "
            "representable; unsupported: unknown unit or unrecognised text",
        },
        "empty_value": "null, or a string with no non-whitespace characters (str.split)",
        "contradictions": [c.as_dict() for c in CONTRADICTIONS],
        "rules": [rule.as_dict() for rule in RULES],
        "m2_behaviour": m2_behaviour_fingerprint(),
    }


def canonical_json(value: Any) -> bytes:
    """The one canonical JSON encoding used for digests: sorted keys, no insignificant
    whitespace, UTF-8 without ASCII escaping."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def rules_digest() -> str:
    return hashlib.sha256(canonical_json(rules_as_dict())).hexdigest()
