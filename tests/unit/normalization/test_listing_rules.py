"""M13-1 rule registry: field contracts, versioning, and coverage of reused M2 behaviour."""

from decimal import Decimal

import pytest

from ecommerce_search.catalog import schemas as m2
from ecommerce_search.catalog import taxonomy as tx
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.rules import Basis, ValueType

# Changing any rule table, any reused M2 table or the output of a reused M2 parser on the
# probe inputs changes this digest. Update it only together with an explicit RULES_VERSION
# decision recorded in docs/m13-progress.md (unreleased until M13-4; nothing is persisted yet).
PINNED_RULES_VERSION = "listing-norm-1"
# M13-2 (unreleased listing-norm-1): text patterns, text storage terms, uncertain storage_type
# terms, measure bounds, precision probe, empty-value rule and contradictions were added
# (ca0a679f...776e). M13-2 corrective: the 12-place precision probe was replaced by the exact
# measure-arithmetic entry (MEASURE_DECIMAL_PREC, traps, outcome classes).
PINNED_RULES_DIGEST = "3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db"


def test_rules_version_and_digest_are_pinned():
    assert nr.RULES_VERSION == PINNED_RULES_VERSION
    assert nr.rules_digest() == PINNED_RULES_DIGEST


def test_digest_is_repeatable():
    assert nr.rules_digest() == nr.rules_digest()
    assert nr.canonical_json(nr.rules_as_dict()) == nr.canonical_json(nr.rules_as_dict())


# ---- field contracts ---------------------------------------------------------------------


def test_listing_fields_are_the_m2_core_fields_plus_the_m2_attributes():
    assert set(nr.FIELD_VALUE_TYPES) == set(nr.CORE_FIELDS) | nr.ATTRIBUTE_FIELDS
    assert set(nr.CORE_FIELDS) <= set(m2.CORE_KEYS)
    assert {
        c: frozenset(m.model_fields) for c, m in m2.SPEC_MODELS.items()
    } == nr.CATEGORY_ATTRIBUTES
    covered = set(nr.CORE_FIELDS) | nr.UNSUPPORTED_SELLER_FIELDS | set(nr.IDENTITY_KEYS)
    assert covered == set(m2.CORE_KEYS) | {"seller_listing_ref"}


def test_unsupported_and_identity_keys_are_never_listing_fields():
    assert not nr.UNSUPPORTED_SELLER_FIELDS & set(nr.FIELD_VALUE_TYPES)
    assert not set(nr.IDENTITY_KEYS) & set(nr.FIELD_VALUE_TYPES)
    assert {"rating", "review_count"} <= nr.UNSUPPORTED_SELLER_FIELDS


def test_required_fields_are_the_m2_required_core_fields():
    required = {name for name, f in m2.CoreFields.model_fields.items() if f.is_required()}
    assert required - {"product_id", "source_type", "is_synthetic"} == nr.REQUIRED_FIELDS


def test_type_tables_are_consistent():
    types = nr.FIELD_VALUE_TYPES
    assert {f for f, t in types.items() if t is ValueType.ENUM} == set(nr.FIELD_ENUMS)
    assert {f for f, t in types.items() if t is ValueType.DECIMAL} == set(nr.FIELD_DECIMAL_PLACES)
    assert set(nr.FIELD_UNITS) <= set(types)
    assert all(types[f] in (ValueType.INTEGER, ValueType.DECIMAL) for f in nr.FIELD_UNITS)


def test_registry_covers_every_field_from_seller_input():
    assert len(nr.RULES_BY_ID) == len(nr.RULES)
    for rule in nr.RULES:
        assert rule.fields <= set(nr.FIELD_VALUE_TYPES), rule.rule_id
        assert rule.bases
    seller = {f for r in nr.RULES if Basis.SELLER_FIELD in r.bases for f in r.fields}
    assert seller == set(nr.FIELD_VALUE_TYPES)
    text_rules = [r for r in nr.RULES if r.bases & nr.TEXT_BASES]
    assert all(r.bases == nr.TEXT_BASES for r in text_rules)
    assert [r.rule_id for r in nr.RULES if r.cross_field] == ["storage.term"]


def test_storage_terms_never_derive_the_medium_from_a_bare_interface():
    assert all(" " in term for term in nr.STORAGE_TERMS)
    assert "nvme" not in nr.STORAGE_TERMS
    assert all(m is tx.StorageType.SSD for m, _ in nr.STORAGE_TERMS.values())
    assert nr.TEXT_STORAGE_TERMS["nvme"] == (None, tx.StorageInterface.NVME)
    assert {"nvme", "sata"} == nr.STORAGE_TYPE_UNCERTAIN_TERMS


def test_contradictions_mirror_the_m2_attribute_validators():
    nvme = nr.CONTRADICTIONS[0]
    assert (nvme.field, nvme.related_field) == ("storage_interface", "storage_type")
    assert nvme.related_values == {m.value for m in tx.StorageType} - {"SSD"}
    for rule in nr.CONTRADICTIONS:
        allowed = {str(m.value) for m in nr.FIELD_ENUMS.get(rule.field, ())} or {"true", "false"}
        assert rule.values <= allowed
    assert {(c.field, c.related_field) for c in nr.CONTRADICTIONS} == {
        ("storage_interface", "storage_type"),
        ("connectivity", "wireless"),
    }


def test_measure_precision_keeps_every_intake_bounded_value_exact():
    from ecommerce_search.normalization.intake import MAX_RAW_BYTES

    # coefficient digits + decimal places (each <= MAX_RAW_BYTES) + a 4-digit unit multiplier
    assert nr.MEASURE_DECIMAL_PREC >= 2 * MAX_RAW_BYTES + 4
    assert all(
        m == m.to_integral_value() and m < 10_000
        for u in nr.MEASURE_PARSERS.values()
        for m in u[0].values()
    )


def test_measure_bounds_match_the_m2_field_types():
    for rule_id, field in nr.MEASURE_FIELDS.items():
        assert nr.MEASURE_RULES[field] == rule_id
    assert set(nr.MEASURE_BOUNDS) == set(nr.MEASURE_RULES)
    assert not nr.within_bounds("price", Decimal(0))
    assert nr.within_bounds("ram_gb", Decimal(1))
    assert not nr.within_bounds("ram_gb", Decimal(4097))


def test_m13_does_not_extend_the_shared_m2_brand_aliases():
    expected = {b.casefold(): b for b in tx.BRAND_CATEGORIES} | {
        "hewlett-packard": "HP",
        "one plus": "OnePlus",
    }
    assert expected == tx.BRAND_LOOKUP
    assert nr.rules_as_dict()["brand_lookup"] == dict(sorted(expected.items()))


# ---- reused M2 behaviour -----------------------------------------------------------------

# Explicit regression table for the M2 parsers M13 reuses (also embedded in the digest).
M2_MEASURES = {
    "measure.price": [
        ("₹40,000", "40000.00"),
        ("Rs. 40000", "40000.00"),
        ("INR 40000.5", "40000.50"),
        (33999, "33999.00"),
        ("40k", "error"),
        ("40000.123", "error"),
    ],
    "measure.ram": [
        ("16GB", "16"),
        (" 16 gb ", "16"),
        ("16", "16"),
        (8, "8"),
        ("16 GiB", "error"),
        ("16.5GB", "error"),
    ],
    "measure.capacity": [
        ("1TB", "1024"),
        ("0.5 TB", "512"),
        ("1,024GB", "1024"),
        ("512gb", "512"),
        ("1 TB SSD", "error"),
        ("1.5TB", "1536"),
    ],
    "measure.screen": [
        ('15.6"', "15.6"),
        ("14 inch", "14.0"),
        ("13.3 inches", "13.3"),
        ("14.05in", "error"),
        (Decimal("14"), "14.0"),
    ],
    "measure.weight": [
        ("1.5kg", "1.50"),
        ("1.234 kg", "error"),
        ("1500 g", "error"),
        (Decimal("1.5"), "1.50"),
    ],
    "measure.battery_mah": [("5000mAh", "5000"), ("5000 MAH", "5000"), ("5,000 mah", "5000")],
    "measure.battery_hours": [("30 hrs", "30.0"), ("7.5h", "7.5"), ("30 min", "error")],
    "measure.shoe_size": [("9", "9.0"), ("9.5", "9.5"), ("UK 9", "error"), (True, "error")],
}


def test_reused_m2_measure_parsing_is_unchanged():
    fingerprint = nr.m2_behaviour_fingerprint()["measures"]
    assert fingerprint == {
        rule: [[repr(value), out] for value, out in cases] for rule, cases in M2_MEASURES.items()
    }


def test_reused_m2_enum_and_text_behaviour_is_unchanged():
    fingerprint = nr.m2_behaviour_fingerprint()
    assert fingerprint["enums"] == [
        ["Category", " LAPTOP ", "laptop"],
        ["StorageType", "ssd", "SSD"],
        ["StorageType", "solid state", "error"],
        ["StorageInterface", "NVMe", "NVME"],
        ["Connectivity", "Bluetooth", "bluetooth"],
    ]
    assert fingerprint["clean_text"] == [
        [repr("  HP   Pavilion \t 14 "), "HP Pavilion 14"],
        [repr(" x "), "x"],
        [repr("   "), None],
        [repr("Ryzen 7"), "Ryzen 7"],
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda mp: mp.setitem(m2.CAPACITY_UNITS, "pb", Decimal(1024 * 1024)),
        lambda mp: mp.setitem(tx.BRAND_LOOKUP, "hp inc", "HP"),
        lambda mp: mp.setitem(nr.STORAGE_TERMS, "nvme", nr.STORAGE_TERMS["nvme ssd"]),
        lambda mp: mp.setattr(m2, "clean_text", lambda value, what="text": value),
        lambda mp: mp.setattr(
            m2, "parse_decimal_measure", lambda value, **kw: Decimal(0).quantize(Decimal(1))
        ),
        lambda mp: mp.setattr(tx, "TAXONOMY_VERSION", "2"),
        lambda mp: mp.setitem(nr.TEXT_PATTERNS, "title.ram_pattern", r"[0-9]+GB"),
        lambda mp: mp.setitem(nr.TEXT_STORAGE_TERMS, "sata", (None, tx.StorageInterface.SATA)),
        lambda mp: mp.setitem(nr.MEASURE_BOUNDS, "ram_gb", (Decimal(1), True, Decimal(64))),
        lambda mp: mp.setattr(nr, "CONTRADICTIONS", nr.CONTRADICTIONS[:1]),
    ],
    ids=[
        "m2-unit-table",
        "m2-brand-alias",
        "m13-storage-term",
        "m2-clean-text-code",
        "m2-measure-code",
        "taxonomy-version",
        "m13-text-pattern",
        "m13-text-storage-term",
        "m13-measure-bound",
        "m13-contradiction",
    ],
)
def test_digest_detects_changes_to_reused_m2_behaviour_and_m13_tables(monkeypatch, mutate):
    before = nr.rules_digest()
    mutate(monkeypatch)
    assert nr.rules_digest() != before
