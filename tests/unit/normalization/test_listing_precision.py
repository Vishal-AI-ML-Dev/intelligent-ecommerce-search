"""M13-2 corrective: exact decimal handling for every M13 measure path.

The reused M2 parser multiplies by the unit in the current decimal context; under the default
28-digit context a long decimal is rounded before M2's precision check. M13 runs the parser in a
local exact context instead. Expected values below are literal, written from the field
contracts (precision, integrality, bounds, canonical format), not computed by production code.
"""

import decimal

import pytest

from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing

N, INV, UNS = "normalized", "invalid", "unsupported"
PREFIX = '{"seller_id":"s","seller_listing_ref":"r",'


def seller(field: str, json_value: str) -> tuple[str, str | None]:
    """Outcome and value of the seller candidate for `"field": <json_value>` (raw JSON text)."""
    raw = parse_raw_listing(f'{PREFIX}"{field}":{json_value}}}'.encode())
    result = normalize_listing(raw)
    result.verify_evidence(raw)
    (c,) = [c for c in result.candidates if c.field == field and c.basis.value == "seller_field"]
    return c.outcome.value, c.normalized_value


CASES = [
    # the two failing independent probes
    ("screen_size_inches", '"15.600000000000000000000000000001"', INV, None),
    ("ram_gb", "16.00000000000000000000000000001", INV, None),
    # nearby long decimals, string and JSON number, valid and invalid
    ("ram_gb", '"16.00000000000000000000000000001GB"', INV, None),
    ("ram_gb", '"16.000000000000000000000000000000GB"', N, "16"),
    ("ram_gb", "16.000000000000000000000000000000", N, "16"),
    ("screen_size_inches", '"15.600000000000000000000000000000"', N, "15.6"),
    ("screen_size_inches", '"15.65"', INV, None),
    ("weight_kg", '"1.50000000000000000000000000000kg"', N, "1.50"),
    ("weight_kg", '"1.505kg"', INV, None),
    ("weight_kg", '"1.500000000000000000000000000000001 kg"', INV, None),
    ("size", '"9.50000000000000000000000000000001"', INV, None),
    ("size", "9.50", N, "9.5"),
    ("battery_life_hours", '"7.500000000000000000000000000000h"', N, "7.5"),
    ("battery_mah", '"5000.0000000000000000000000000001 mAh"', INV, None),
    # unit multiplication (TB -> GB is x1024; the result must be integral GB)
    ("storage_gb", '"0.5TB"', N, "512"),
    ("storage_gb", '"1.5TB"', N, "1536"),
    ("storage_gb", '"0.0009765625TB"', N, "1"),
    ("storage_gb", '"0.00048828125TB"', INV, None),
    ("storage_gb", '"0.5000000000000000000000000000001TB"', INV, None),
    ("storage_gb", '"1.00000000000000000000000000000TB"', N, "1024"),
    ("storage_gb", '"64TB"', N, "65536"),
    ("storage_gb", '"64.0009765625TB"', INV, None),  # 65537 GB, above the hard bound
    # price: 2 decimal places, > 0, <= 9999999999.99, currency prefixes allowed
    ("price", '"40000.000000000000000000000000000001"', INV, None),
    ("price", '"₹40,000.00000000000000000000000000"', N, "40000.00"),
    ("price", '"9999999999.99"', N, "9999999999.99"),
    ("price", '"9999999999.990000000000000000000001"', INV, None),
    ("price", '"10000000000"', INV, None),
    ("price", '"0.01"', N, "0.01"),
    ("price", '"0.001"', INV, None),
    ("price", '"0.00"', INV, None),
    ("price", "1e2", N, "100.00"),
    ("price", "1E-2", N, "0.01"),
    ("price", "1e-3", INV, None),
    # unrepresentable or extreme exponents are invalid, never an error or a rounded value
    ("price", "1e999999999", INV, None),
    ("price", "1e-999999999", INV, None),
    ("price", "1e100000", INV, None),
    # booleans are not numbers
    ("price", "true", INV, None),
    ("ram_gb", "false", INV, None),
    # unknown units and unrecognised text keep the unsupported outcome
    ("ram_gb", '"16.00000000000000000000000000001 GiB"', UNS, None),
    ("price", '"forty"', UNS, None),
]


@pytest.mark.parametrize(("field", "json_value", "outcome", "value"), CASES)
def test_measures_are_exact_before_precision_and_bounds(field, json_value, outcome, value):
    assert seller(field, json_value) == (outcome, value)


def test_long_digit_strings_are_recognised_and_out_of_bounds():
    assert seller("ram_gb", '"' + "9" * 60_000 + 'GB"') == (INV, None)
    assert seller("price", '"' + "1" * 60_000 + '"') == (INV, None)


def test_text_extraction_uses_the_same_exact_measures():
    raw = parse_raw_listing((PREFIX + '"title":"1.50TB SSD and 16GB RAM"}').encode())
    values = {
        (c.field, c.outcome.value, c.normalized_value)
        for c in normalize_listing(raw).candidates
        if c.basis.value == "title"
    }
    assert values == {
        ("storage_gb", N, "1536"),
        ("storage_type", N, "SSD"),
        ("ram_gb", N, "16"),
    }


def test_the_global_decimal_context_is_unchanged():
    before = decimal.getcontext().copy()
    seller("price", '"40000.000000000000000000000000000001"')
    after = decimal.getcontext()
    assert (after.prec, after.traps, after.Emax, after.Emin) == (
        before.prec,
        before.traps,
        before.Emax,
        before.Emin,
    )
