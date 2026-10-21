"""M13-2 structured seller-field normalization, empty/missing/unsupported handling and
determinism. All listings are synthetic; they establish specified behaviour only."""

import json
import random

import pytest

from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.models import FindingKind, Outcome
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.rules import Basis

K, OUT = FindingKind, Outcome
IDENTITY = {"seller_id": "s-1", "seller_listing_ref": "ref-1"}


def raw_of(fields: dict | str):
    if isinstance(fields, dict):
        fields = json.dumps(IDENTITY | fields, ensure_ascii=False)
    return parse_raw_listing(fields.encode("utf-8"))


def run(fields: dict | str):
    raw = raw_of(fields)
    result = normalize_listing(raw)
    result.verify_evidence(raw)
    return result


def seller(result, field):
    return [c for c in result.candidates if c.field == field and c.basis is Basis.SELLER_FIELD]


def only(result, field):
    (candidate,) = seller(result, field)
    return candidate


def kinds(result, kind):
    return [f for f in result.findings if f.kind is kind]


# ---- supported units, vocabularies and brands --------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("ram_gb", "16GB", "16"),
        ("ram_gb", " 16 gb ", "16"),
        ("ram_gb", 8, "8"),
        ("storage_gb", "1TB", "1024"),
        ("storage_gb", "0.5 TB", "512"),
        ("storage_gb", "1,024GB", "1024"),
        ("screen_size_inches", '15.6"', "15.6"),
        ("screen_size_inches", "14 inch", "14.0"),
        ("weight_kg", "1.5kg", "1.50"),
        ("battery_mah", "5,000 mAh", "5000"),
        ("battery_life_hours", "30 hrs", "30.0"),
        ("size", 9, "9.0"),
        ("price", "₹40,000", "40000.00"),
        ("price", "Rs. 40000", "40000.00"),
        ("price", 33999.5, "33999.50"),
    ],
)
def test_supported_units_normalize_with_the_m2_parsers(field, value, expected):
    candidate = only(run({field: value}), field)
    assert (candidate.outcome, candidate.normalized_value) == (OUT.NORMALIZED, expected)
    assert candidate.unit == nr.FIELD_UNITS.get(field)
    assert candidate.raw_text == (value if isinstance(value, str) else json.dumps(value))


@pytest.mark.parametrize(
    ("field", "value", "outcome"),
    [
        ("ram_gb", "16 GiB", OUT.UNSUPPORTED),  # unknown unit
        ("ram_gb", "sixteen", OUT.UNSUPPORTED),
        ("ram_gb", "₹16", OUT.UNSUPPORTED),  # currency marker outside price
        ("ram_gb", "16.5GB", OUT.INVALID),  # excess precision
        ("ram_gb", 16.5, OUT.INVALID),
        ("ram_gb", "8192GB", OUT.INVALID),  # above the M2 hard bound
        ("ram_gb", 0, OUT.INVALID),
        ("ram_gb", True, OUT.INVALID),
        ("ram_gb", "1e999999999", OUT.UNSUPPORTED),
        ("price", 0, OUT.INVALID),
        ("price", "40k", OUT.UNSUPPORTED),
        ("price", "40000.123", OUT.INVALID),
        ("weight_kg", "1500 g", OUT.UNSUPPORTED),
        ("storage_gb", "1 TB SSD", OUT.UNSUPPORTED),
    ],
)
def test_unsupported_and_invalid_measures_keep_raw_evidence_only(field, value, outcome):
    candidate = only(run({field: value}), field)
    assert candidate.outcome is outcome
    assert candidate.normalized_value is None


def test_huge_json_number_literal_is_invalid_not_an_error():
    candidate = only(
        run('{"seller_id":"s","seller_listing_ref":"r","ram_gb":1e999999999}'), "ram_gb"
    )
    assert (candidate.outcome, candidate.raw_text) == (OUT.INVALID, "1e999999999")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("hp", "HP"), ("  Hewlett-Packard ", "HP"), ("one   plus", "OnePlus"), ("BOAT", "boAt")],
)
def test_known_brands_and_m2_aliases_take_the_canonical_spelling(value, expected):
    candidate = only(run({"brand": value}), "brand")
    assert (candidate.outcome, candidate.normalized_value) == (OUT.NORMALIZED, expected)
    assert candidate.raw_text == value


def test_unknown_brand_is_unsupported_and_kept_as_given():
    result = run({"brand": "Zebronics", "category": "headphones"})
    candidate = only(result, "brand")
    assert (candidate.outcome, candidate.raw_text) == (OUT.UNSUPPORTED, "Zebronics")
    assert not kinds(result, K.BRAND_CATEGORY_MISMATCH)
    assert not [f for f in kinds(result, K.MISSING_REQUIRED) if f.field == "brand"]


@pytest.mark.parametrize(
    ("field", "value", "outcome", "expected"),
    [
        ("category", " LAPTOP ", OUT.NORMALIZED, "laptop"),
        ("category", "notebook", OUT.UNSUPPORTED, None),
        ("availability", "IN_STOCK", OUT.NORMALIZED, "in_stock"),
        ("availability", "In Stock", OUT.UNSUPPORTED, None),  # no alias for the closed set
        ("connectivity", "Bluetooth", OUT.NORMALIZED, "bluetooth"),
        ("size_system", "uk", OUT.NORMALIZED, "UK"),
        ("gender", 1, OUT.INVALID, None),
        ("currency", " inr ", OUT.NORMALIZED, "INR"),
        ("currency", "₹", OUT.INVALID, None),
        ("currency", "rupees", OUT.INVALID, None),
        ("subcategory", "  Over-Ear ", OUT.NORMALIZED, "over-ear"),
        ("wireless", True, OUT.NORMALIZED, "true"),
        ("anc", False, OUT.NORMALIZED, "false"),
        ("wireless", "yes", OUT.UNSUPPORTED, None),
        ("wireless", 1, OUT.INVALID, None),
        ("processor", 5, OUT.INVALID, None),
    ],
)
def test_closed_vocabularies_and_strict_types(field, value, outcome, expected):
    candidate = only(run({field: value}), field)
    assert (candidate.outcome, candidate.normalized_value) == (outcome, expected)


def test_text_fields_collapse_whitespace_and_never_rewrite_model_names():
    result = run(
        {
            "title": "  hewlett-packard   Pavilion\t14-dv2xxx  ",
            "processor": "intel CORE  i5-1235U",
            "gpu": "RTX 4050",
        }
    )
    assert only(result, "title").normalized_value == "hewlett-packard Pavilion 14-dv2xxx"
    assert only(result, "processor").normalized_value == "intel CORE i5-1235U"
    assert only(result, "gpu").normalized_value == "RTX 4050"
    assert not seller(result, "brand")  # the brand is never read from the title


# ---- storage representations (approved NVME semantics) -----------------------------------


@pytest.mark.parametrize("term", ["NVMe SSD", "ssd  nvme", "SATA SSD"])
def test_combined_storage_terms_yield_medium_and_interface(term):
    result = run({"storage_type": term})
    assert only(result, "storage_type").normalized_value == "SSD"
    interface = only(result, "storage_interface")
    assert interface.source_key == "storage_type"
    assert interface.rule_id == "storage.term"
    assert interface.normalized_value == ("SATA" if "SATA" in term else "NVME")


@pytest.mark.parametrize("term", ["NVMe", "sata"])
def test_bare_interface_term_in_storage_type_is_uncertain_and_implies_no_medium(term):
    result = run({"storage_type": term})
    assert only(result, "storage_type").outcome is OUT.UNCERTAIN
    assert not seller(result, "storage_interface")


def test_storage_term_and_seller_interface_can_conflict():
    result = run({"storage_type": "NVMe SSD", "storage_interface": "SATA"})
    assert {c.normalized_value for c in seller(result, "storage_interface")} == {"NVME", "SATA"}
    assert [f.field for f in kinds(result, K.VALUE_CONFLICT)] == ["storage_interface"]


# ---- missing, null, blank, unsupported ---------------------------------------------------


def test_absent_required_keys_are_missing():
    result = run({})
    assert {f.field for f in kinds(result, K.MISSING_REQUIRED)} == nr.REQUIRED_FIELDS
    assert not result.candidates


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_present_null_or_blank_is_an_empty_value_not_missing(value):
    result = run({"title": value, "ram_gb": value, "category": "laptop"})
    empty = kinds(result, K.EMPTY_VALUE)
    assert {(f.field, f.source_key) for f in empty} == {("title", "title"), ("ram_gb", "ram_gb")}
    reported_missing = {f.field for f in result.findings if f.kind.value.startswith("missing")}
    assert not {"title", "ram_gb"} & reported_missing
    assert not seller(result, "title") and not seller(result, "ram_gb")
    assert not [c for c in result.candidates if c.basis is Basis.TITLE]


def test_invalid_supplied_value_is_not_reported_missing():
    result = run({"price": "free", "category": "laptop", "ram_gb": "lots"})
    assert only(result, "price").outcome is OUT.UNSUPPORTED
    reported_missing = {f.field for f in result.findings if f.kind.value.startswith("missing")}
    assert not {"price", "ram_gb"} & reported_missing


def test_seller_rating_review_count_and_unknown_keys_are_unsupported():
    result = run(
        {"rating": 4.9, "Review_Count": 10000, "product_id": "P1", "colour": "red", "x": None}
    )
    unsupported = {f.source_key: f.message for f in kinds(result, K.UNSUPPORTED_ATTRIBUTE)}
    assert set(unsupported) == {"rating", "Review_Count", "product_id", "colour", "x"}
    assert "not a verified catalog fact" in unsupported["rating"]
    assert "not a verified catalog fact" in unsupported["Review_Count"]
    assert not result.candidates


def test_identity_keys_are_not_attributes_and_seller_keys_match_trim_casefold():
    result = run({" RAM_GB ": "16GB"})
    candidate = only(result, "ram_gb")
    assert candidate.source_key == " RAM_GB "
    assert not kinds(result, K.UNSUPPORTED_ATTRIBUTE)


def test_wrong_argument_type_is_a_programming_error():
    with pytest.raises(TypeError):
        normalize_listing(b"{}")  # type: ignore[arg-type]


# ---- determinism -------------------------------------------------------------------------

FULL = {
    "title": "Zénith 14 — 16GB RAM 512GB NVMe SSD",
    "description": "Thin laptop. 16GB RAM.",
    "category": "laptop",
    "subcategory": "ultrabook",
    "brand": "lenovo",
    "price": "₹54,990",
    "currency": "inr",
    "availability": "in_stock",
    "ram_gb": "8 GB",
    "storage_gb": "512GB",
    "storage_type": "NVMe SSD",
    "processor": "Ryzen 7 7840U",
    "rating": 4.4,
    "unknown_key": "x",
}


def test_shuffled_key_order_gives_the_same_output_but_a_different_raw_hash():
    rng = random.Random(13)  # noqa: S311 - deterministic test shuffle, not security
    items = list((IDENTITY | FULL).items())
    base = raw_of(FULL)
    outputs = set()
    for _ in range(5):
        rng.shuffle(items)
        shuffled = parse_raw_listing(json.dumps(dict(items), ensure_ascii=False).encode())
        result = normalize_listing(shuffled)
        result.verify_evidence(shuffled)
        outputs.add(result.output_sha256())
        if shuffled.raw_bytes != base.raw_bytes:
            assert shuffled.raw_sha256 != base.raw_sha256
    assert outputs == {normalize_listing(base).output_sha256()}


def test_repeated_runs_are_identical_and_canonically_ordered():
    a, b = run(FULL), run(FULL)
    assert a == b
    assert a.canonical_json() == b.canonical_json()
    assert list(a.candidates) == sorted(a.candidates, key=lambda c: c.sort_key())
    assert list(a.findings) == sorted(a.findings, key=lambda f: f.sort_key())
    assert a.rules_digest == nr.rules_digest()
    assert a.rules_version == nr.RULES_VERSION


# Regression pin, produced by this implementation (not an independent oracle): any change to
# the rules, the digest or the output for this synthetic listing changes it. M13-2 corrective:
# changed from ac4d8471...24d80 only because the embedded rules_digest changed (substituting the
# old digest reproduces the old hash; candidates and findings are unchanged).
PINNED_FULL_OUTPUT_SHA256 = "30640df1fd36215f6099a60e17ed7a657933bad2dbb852686b7e7b8e1844d04a"


def test_output_hash_for_a_fixed_listing_is_pinned():
    assert run(FULL).output_sha256() == PINNED_FULL_OUTPUT_SHA256


def test_raw_input_is_preserved_unchanged():
    raw = raw_of(FULL)
    before = raw.raw_bytes
    normalize_listing(raw)
    assert raw.raw_bytes == before
    assert json.loads(raw.raw_bytes) == IDENTITY | FULL
