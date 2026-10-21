"""M13-2 checks: value conflicts, cross-field contradictions, category compatibility,
unknown category and expected fields. Synthetic listings only."""

import json

import pytest

from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.models import FindingKind
from ecommerce_search.normalization.normalizer import normalize_listing

K = FindingKind


def run(fields: dict):
    base = {"seller_id": "s", "seller_listing_ref": "r"}
    raw = parse_raw_listing(json.dumps(base | fields, ensure_ascii=False).encode())
    result = normalize_listing(raw)
    result.verify_evidence(raw)
    return result


def of(result, kind):
    return [f for f in result.findings if f.kind is kind]


# ---- conflicts ---------------------------------------------------------------------------


def test_structured_vs_title_conflict_keeps_both_values_without_a_winner():
    result = run({"ram_gb": "16GB", "title": "Laptop 8GB RAM"})
    (conflict,) = of(result, K.VALUE_CONFLICT)
    assert conflict.field == "ram_gb"
    assert conflict.message == "2 different normalized values: 16 (seller_field); 8 (title)"
    values = {c.normalized_value for c in result.candidates if c.field == "ram_gb"}
    assert values == {"16", "8"}


def test_agreeing_structured_and_text_values_are_not_a_conflict():
    result = run({"ram_gb": 16, "title": "16GB RAM", "description": "16 GB RAM"})
    assert not of(result, K.VALUE_CONFLICT)
    assert len([c for c in result.candidates if c.field == "ram_gb"]) == 3


def test_repeated_text_with_different_values_is_a_conflict():
    result = run({"title": "8GB RAM laptop", "description": "Now with 16GB RAM. 8GB RAM base."})
    (conflict,) = of(result, K.VALUE_CONFLICT)
    assert (
        conflict.message
        == "2 different normalized values: 16 (description); 8 (description, title)"
    )


def test_uncertain_text_does_not_create_a_conflict():
    result = run({"ram_gb": 16, "description": "upgradable to 32GB RAM"})
    assert not of(result, K.VALUE_CONFLICT)


# ---- contradictions ----------------------------------------------------------------------


def test_nvme_with_hdd_is_a_contradiction_across_structured_and_text():
    result = run({"storage_type": "HDD", "title": "1TB NVMe"})
    (finding,) = of(result, K.CROSS_FIELD_CONTRADICTION)
    assert (finding.field, finding.related_field) == ("storage_interface", "storage_type")
    assert finding.message == (
        "storage_interface=NVME (title) and storage_type=HDD (seller_field): "
        "NVME requires an SSD medium"
    )


def test_nvme_without_a_medium_is_not_a_contradiction_and_implies_nothing():
    result = run({"storage_interface": "nvme"})
    assert not of(result, K.CROSS_FIELD_CONTRADICTION)
    assert not [c for c in result.candidates if c.field == "storage_type"]


def test_sata_with_hdd_is_not_a_contradiction():
    assert not of(
        run({"storage_type": "HDD", "storage_interface": "SATA"}), K.CROSS_FIELD_CONTRADICTION
    )


@pytest.mark.parametrize(
    ("connectivity", "wireless", "contradicts"),
    [
        ("wired", True, True),
        ("wired", False, False),
        ("bluetooth", False, True),
        ("wireless_2_4ghz", False, True),
        ("bluetooth", True, False),
        ("usb", True, False),
    ],
)
def test_wired_wireless_contradictions(connectivity, wireless, contradicts):
    result = run({"category": "headphones", "connectivity": connectivity, "wireless": wireless})
    findings = of(result, K.CROSS_FIELD_CONTRADICTION)
    assert bool(findings) is contradicts
    if contradicts:
        assert (findings[0].field, findings[0].related_field) == ("connectivity", "wireless")


def test_wireless_contradiction_is_not_derived_from_unsupported_strings():
    assert not of(run({"connectivity": "wired", "wireless": "yes"}), K.CROSS_FIELD_CONTRADICTION)


# ---- category compatibility --------------------------------------------------------------


def test_category_incompatible_attributes_are_flagged_for_supplied_keys():
    result = run(
        {"category": "shoes", "ram_gb": "8GB", "storage_type": "NVMe SSD", "wireless": None}
    )
    flagged = {f.field for f in of(result, K.INCOMPATIBLE_ATTRIBUTE)}
    assert flagged == {"ram_gb", "storage_type", "storage_interface", "wireless"}


def test_text_candidates_are_not_flagged_as_incompatible_attributes():
    result = run({"category": "shoes", "title": "Running shoe 8GB RAM"})
    assert not of(result, K.INCOMPATIBLE_ATTRIBUTE)


def test_subcategory_and_brand_must_fit_the_category():
    result = run({"category": "phone", "subcategory": "Over-Ear", "brand": "nike"})
    (sub,) = of(result, K.INCOMPATIBLE_SUBCATEGORY)
    assert sub.message == "'over-ear' is not a phone subcategory; allowed: smartphone"
    (brand,) = of(result, K.BRAND_CATEGORY_MISMATCH)
    assert brand.message == "Nike is not expected in category phone"
    assert not of(
        run({"category": "phone", "subcategory": "smartphone", "brand": "Samsung"}),
        K.INCOMPATIBLE_SUBCATEGORY,
    )


def test_missing_expected_fields_follow_the_category():
    result = run({"category": "headphones", "wireless": True})
    assert [f.field for f in of(result, K.MISSING_EXPECTED)] == ["connectivity"]
    result = run({"category": "headphones", "wireless": True, "connectivity": None})
    assert not of(result, K.MISSING_EXPECTED)  # present-but-null is not absent
    assert [f.field for f in of(result, K.EMPTY_VALUE)] == ["connectivity"]


@pytest.mark.parametrize(
    ("category", "state"),
    [
        ({}, "category is absent"),
        ({"category": None}, "category is null or blank"),
        ({"category": "  "}, "category is null or blank"),
        ({"category": "tablet"}, "category is not in the closed taxonomy"),
        ({"category": 3}, "category is not a string"),
    ],
)
def test_unknown_category_makes_compatibility_explicitly_unchecked(category, state):
    result = run(
        category | {"ram_gb": 8, "color": "red", "subcategory": "nonsense", "brand": "Nike"}
    )
    (unchecked,) = of(result, K.COMPATIBILITY_UNCHECKED)
    assert unchecked.message.startswith(state + "; ")
    assert "not performed" in unchecked.message
    for kind in (
        K.INCOMPATIBLE_ATTRIBUTE,
        K.INCOMPATIBLE_SUBCATEGORY,
        K.BRAND_CATEGORY_MISMATCH,
        K.MISSING_EXPECTED,
    ):
        assert not of(result, kind)


def test_known_category_is_not_reported_unchecked():
    assert not of(run({"category": "laptop"}), K.COMPATIBILITY_UNCHECKED)
