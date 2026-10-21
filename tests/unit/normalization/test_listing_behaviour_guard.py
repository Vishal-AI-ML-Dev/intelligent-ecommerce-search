"""M13 behaviour-regression guard for code-only rules.

`rules_digest()` covers rule tables and configuration, not M13's own code in `fields`,
`extraction` and `normalizer`. This guard pins the behaviour of that code on representative
synthetic listings with literal expected outcomes (written from the contracts, not produced by
the code under test). It is not part of `rules_digest()` or `output_sha256()` and is not
circular.

It detects a code change only if the change alters one of these probed outcomes; it cannot
detect every implementation change. A failure here means: decide whether the change is
intended and, after M13-3 persists outputs, bump `RULES_VERSION`.
"""

import json

import pytest

from ecommerce_search.catalog import schemas as m2
from ecommerce_search.normalization import fields, normalizer
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.models import Outcome
from ecommerce_search.normalization.normalizer import normalize_listing

S, T = "seller_field", "title"
N, UNS, UC, INV = "normalized", "unsupported", "uncertain", "invalid"
UNCHECKED = "attribute, subcategory, brand-category and expected-field checks were not performed"

# (id, seller fields, expected candidates (all), finding kinds checked, expected findings of
# those kinds). Candidate: (field, basis, raw_text, span_start, outcome, value).
# Finding: (kind, field, related_field, source_key, message).
GUARD = [
    ("currency-ok", {"currency": " usd "}, {("currency", S, " usd ", None, N, "USD")}, (), set()),
    ("currency-symbol", {"currency": "₹"}, {("currency", S, "₹", None, INV, None)}, (), set()),
    (
        "currency-word",
        {"currency": "rupees"},
        {("currency", S, "rupees", None, INV, None)},
        (),
        set(),
    ),
    ("currency-number", {"currency": 840}, {("currency", S, "840", None, INV, None)}, (), set()),
    (
        "failure-outcomes",
        {
            "ram_gb": "16 GiB",
            "storage_gb": "16.5GB",
            "battery_mah": "200000",
            "wireless": "yes",
            "anc": 1,
            "storage_type": "SATA",
            "availability": "In Stock",
            "brand": "Zebronics",
            "gender": 1,
        },
        {
            ("ram_gb", S, "16 GiB", None, UNS, None),
            ("storage_gb", S, "16.5GB", None, INV, None),
            ("battery_mah", S, "200000", None, INV, None),
            ("wireless", S, "yes", None, UNS, None),
            ("anc", S, "1", None, INV, None),
            ("storage_type", S, "SATA", None, UC, None),
            ("availability", S, "In Stock", None, UNS, None),
            ("brand", S, "Zebronics", None, UNS, None),
            ("gender", S, "1", None, INV, None),
        },
        (),
        set(),
    ),
    (
        "precision",
        {"screen_size_inches": "15.600000000000000000000000000001", "price": "₹40,000.00"},
        {
            ("screen_size_inches", S, "15.600000000000000000000000000001", None, INV, None),
            ("price", S, "₹40,000.00", None, N, "40000.00"),
        },
        (),
        set(),
    ),
    (
        "qualifiers",
        {"title": "upgradeable to 32GB RAM; max 2TB SSD; 8GB RAM"},
        {
            (
                "title",
                S,
                "upgradeable to 32GB RAM; max 2TB SSD; 8GB RAM",
                None,
                N,
                "upgradeable to 32GB RAM; max 2TB SSD; 8GB RAM",
            ),
            ("ram_gb", T, "upgradeable to 32GB RAM", 0, UC, None),
            ("storage_gb", T, "max 2TB SSD", 25, UC, None),
            ("storage_type", T, "max 2TB SSD", 25, UC, None),
            ("ram_gb", T, "8GB RAM", 38, N, "8"),
        },
        ("value_conflict",),
        set(),
    ),
    (
        "contradictions",
        {
            "storage_type": "HDD",
            "title": "1TB NVMe",
            "connectivity": "bluetooth",
            "wireless": False,
        },
        {
            ("storage_type", S, "HDD", None, N, "HDD"),
            ("title", S, "1TB NVMe", None, N, "1TB NVMe"),
            ("storage_gb", T, "1TB NVMe", 0, N, "1024"),
            ("storage_interface", T, "1TB NVMe", 0, N, "NVME"),
            ("connectivity", S, "bluetooth", None, N, "bluetooth"),
            ("wireless", S, "false", None, N, "false"),
        },
        ("cross_field_contradiction",),
        {
            (
                "cross_field_contradiction",
                "storage_interface",
                "storage_type",
                None,
                "storage_interface=NVME (title) and storage_type=HDD (seller_field): "
                "NVME requires an SSD medium",
            ),
            (
                "cross_field_contradiction",
                "connectivity",
                "wireless",
                None,
                "connectivity=bluetooth (seller_field) and wireless=false (seller_field): "
                "wireless connectivity contradicts wireless=false",
            ),
        },
    ),
    (
        "conflict-empty-unchecked",
        {"ram_gb": 16, "title": "8GB RAM", "category": None},
        {
            ("ram_gb", S, "16", None, N, "16"),
            ("title", S, "8GB RAM", None, N, "8GB RAM"),
            ("ram_gb", T, "8GB RAM", 0, N, "8"),
        },
        ("value_conflict", "empty_value", "compatibility_unchecked", "missing_required"),
        {
            (
                "value_conflict",
                "ram_gb",
                None,
                None,
                "2 different normalized values: 16 (seller_field); 8 (title)",
            ),
            (
                "empty_value",
                "category",
                None,
                "category",
                "key is present but its value is null; not a valid value",
            ),
            (
                "compatibility_unchecked",
                None,
                None,
                None,
                f"category is null or blank; {UNCHECKED}",
            ),
            ("missing_required", "brand", None, None, "key is absent"),
            ("missing_required", "price", None, None, "key is absent"),
            ("missing_required", "currency", None, None, "key is absent"),
            ("missing_required", "availability", None, None, "key is absent"),
        },
    ),
    (
        "unsupported-keys",
        {"rating": 4, "product_id": "p", "foo": 1},
        set(),
        ("unsupported_attribute",),
        {
            (
                "unsupported_attribute",
                None,
                None,
                "rating",
                "seller-supplied rating is kept in the raw input only; not a verified catalog fact",
            ),
            (
                "unsupported_attribute",
                None,
                None,
                "product_id",
                "catalog product_id cannot be asserted by a seller; kept in the raw input only",
            ),
            (
                "unsupported_attribute",
                None,
                None,
                "foo",
                "unknown key; kept in the raw input only and not normalized",
            ),
        },
    ),
    (
        "category-checks",
        {"category": "phone", "subcategory": "Over-Ear", "brand": "nike", "ram_gb": 8, "size": 9},
        {
            ("category", S, "phone", None, N, "phone"),
            ("subcategory", S, "Over-Ear", None, N, "over-ear"),
            ("brand", S, "nike", None, N, "Nike"),
            ("ram_gb", S, "8", None, N, "8"),
            ("size", S, "9", None, N, "9.0"),
        },
        (
            "incompatible_attribute",
            "incompatible_subcategory",
            "brand_category_mismatch",
            "missing_expected",
            "compatibility_unchecked",
        ),
        {
            (
                "incompatible_attribute",
                "size",
                None,
                None,
                "size is not an attribute of category phone",
            ),
            (
                "incompatible_subcategory",
                "subcategory",
                None,
                None,
                "'over-ear' is not a phone subcategory; allowed: smartphone",
            ),
            (
                "brand_category_mismatch",
                "brand",
                None,
                None,
                "Nike is not expected in category phone",
            ),
            ("missing_expected", "storage_gb", None, None, "key is absent; expected for phone"),
            ("missing_expected", "battery_mah", None, None, "key is absent; expected for phone"),
            (
                "missing_expected",
                "screen_size_inches",
                None,
                None,
                "key is absent; expected for phone",
            ),
            (
                "missing_expected",
                "operating_system",
                None,
                None,
                "key is absent; expected for phone",
            ),
        },
    ),
]


def mismatches() -> list[str]:
    problems = []
    for case_id, fields_in, expected_candidates, kinds, expected_findings in GUARD:
        data = {"seller_id": "s", "seller_listing_ref": "r"} | fields_in
        raw = parse_raw_listing(json.dumps(data, ensure_ascii=False).encode())
        result = normalize_listing(raw)
        result.verify_evidence(raw)
        candidates = {
            (c.field, c.basis.value, c.raw_text, c.span_start, c.outcome.value, c.normalized_value)
            for c in result.candidates
        }
        findings = {
            (f.kind.value, f.field, f.related_field, f.source_key, f.message)
            for f in result.findings
            if f.kind.value in kinds
        }
        if candidates != expected_candidates:
            problems.append(
                f"{case_id}: candidates {sorted(candidates ^ expected_candidates, key=str)}"
            )
        if findings != expected_findings:
            problems.append(f"{case_id}: findings {sorted(findings ^ expected_findings, key=str)}")
    return problems


def test_code_only_behaviour_matches_the_guard():
    assert mismatches() == []


# ---- the guard detects representative code-only changes the rules digest cannot see -------


def _inexact_measure(field, value):  # the pre-fix behaviour: M2 in the default context
    units, dp, prefixes = nr.MEASURE_PARSERS[nr.MEASURE_RULES[field]]
    try:
        number = m2.parse_decimal_measure(value, what=field, units=units, dp=dp, prefixes=prefixes)
    except (ValueError, ArithmeticError):
        return Outcome.INVALID, None
    return Outcome.NORMALIZED, str(number)


def _lenient_currency(original):
    def normalize(field, key, value):
        if field == "currency" and isinstance(value, str):
            return [
                fields.make_candidate(
                    field, key, value, "currency.upper", Outcome.NORMALIZED, value.strip().upper()
                )
            ]
        return original(field, key, value)

    return normalize


@pytest.mark.parametrize(
    "mutate",
    [
        lambda mp: mp.setattr(fields, "measure_outcome", _inexact_measure),
        lambda mp: mp.setattr(fields, "_enum", lambda field, text: (Outcome.UNSUPPORTED, None)),
        lambda mp: mp.setattr(
            normalizer, "normalize_seller_field", _lenient_currency(fields.normalize_seller_field)
        ),
        lambda mp: mp.setattr(normalizer, "_described", lambda value, bases: value),
        lambda mp: mp.setattr(normalizer, "_UNCHECKED", "checks skipped"),
    ],
    ids=["inexact-measure", "enum-outcome", "lenient-currency", "message-format", "unchecked-text"],
)
def test_guard_detects_code_only_changes_that_leave_the_rules_digest_unchanged(monkeypatch, mutate):
    digest = nr.rules_digest()
    mutate(monkeypatch)
    assert nr.rules_digest() == digest  # the rule/configuration digest is blind to these
    assert mismatches()
