"""AC-01: `return-data-v1` canonical forms and hashes (contracts §4.6–4.7). Hermetic."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import versions

# ---- versions ------------------------------------------------------------------------------


def test_contract_versions_are_the_frozen_identifiers():
    assert versions.CONTRACT_VERSION == "return-data-v1"
    assert versions.REASON_CODES_VERSION == "return-reason-codes-1"
    assert versions.REDACTION_VERSION == "redaction-1"
    assert versions.TRANSFORMATION_VERSION == "returns-import-1"


# ---- timestamps ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("2026-01-05T10:00:00+05:30", "2026-01-05T04:30:00.000000Z"),
        ("2026-01-05T04:30:00Z", "2026-01-05T04:30:00.000000Z"),
        ("2026-01-05t04:30:00z", "2026-01-05T04:30:00.000000Z"),
        ("2026-01-05T04:30:00.5Z", "2026-01-05T04:30:00.500000Z"),
        ("2026-01-05T04:30:00.123456Z", "2026-01-05T04:30:00.123456Z"),
        ("2026-01-04T23:30:00-05:00", "2026-01-05T04:30:00.000000Z"),
        ("2026-01-01T02:00:00+05:30", "2025-12-31T20:30:00.000000Z"),  # date changes in UTC
        ("2026-01-05T04:30:00-00:00", "2026-01-05T04:30:00.000000Z"),
    ],
)
def test_timestamps_canonicalize_to_utc_with_six_fraction_digits(raw, canonical):
    assert cf.format_timestamp(cf.parse_timestamp(raw)) == canonical


def test_equal_instants_in_different_offsets_are_equal():
    a = cf.parse_timestamp("2026-01-05T10:00:00.000001+05:30")
    b = cf.parse_timestamp("2026-01-05T04:30:00.000001Z")
    assert a == b and a.tzinfo is UTC
    assert cf.format_timestamp(a) == cf.format_timestamp(b)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-01-05T04:30:00.1234567Z",  # more than 6 fractional digits
        "2026-01-05T04:30:00",  # no offset
        "2026-01-05 04:30:00Z",  # space separator
        "2026-01-05T04:30Z",  # no seconds
        "2026-01-05T04:30:60Z",  # leap second
        "2026-02-30T04:30:00Z",  # no such date
        "2026-01-05T24:00:00Z",
        "2026-01-05T04:30:00+24:00",
        "2026-01-05T04:30:00+05:60",
        "2026-01-05T04:30:00+0530",
        "2026-01-05T04:30:00.Z",
        "２０２６-01-05T04:30:00Z",  # non-ASCII digits
        "2026-01-05T04:30:00Z\n",
        " 2026-01-05T04:30:00Z",
        "",
    ],
)
def test_malformed_timestamps_are_rejected_as_format(raw):
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_timestamp(raw)
    assert exc.value.code == "format"


@pytest.mark.parametrize("raw", [1767587400, 1767587400.0, True, None])
def test_non_string_timestamps_are_rejected_as_type(raw):
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_timestamp(raw)
    assert exc.value.code == "type"


def test_naive_datetimes_are_rejected():
    with pytest.raises(cf.CanonicalFormError):
        cf.parse_timestamp(datetime(2026, 1, 5, 4, 30))
    with pytest.raises(cf.CanonicalFormError):
        cf.format_timestamp(datetime(2026, 1, 5, 4, 30))


def test_early_years_keep_four_digits():
    assert cf.format_timestamp(cf.parse_timestamp("0999-01-05T04:30:00Z")).startswith("0999-")


def test_an_instant_before_year_one_in_utc_is_rejected():
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_timestamp("0001-01-01T00:30:00+05:30")
    assert exc.value.code == "format"


# ---- dates ---------------------------------------------------------------------------------


def test_dates():
    assert cf.format_date(cf.parse_date("2026-10-24")) == "2026-10-24"
    for raw in ("2026-1-24", "2026-02-30", "2026-10-24T00:00:00Z", "20261024"):
        with pytest.raises(cf.CanonicalFormError):
            cf.parse_date(raw)
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_date(20261024)
    assert exc.value.code == "type"


# ---- money ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("33999", "33999.00"),
        ("33999.0", "33999.00"),
        ("33999.00", "33999.00"),
        ("0", "0.00"),
        ("0.5", "0.50"),
        ("99999999.99", "99999999.99"),
    ],
)
def test_money_canonicalizes_to_two_fraction_digits(raw, canonical):
    assert cf.format_money(cf.parse_money(raw)) == canonical


@pytest.mark.parametrize(
    "raw", ["01", "1.234", "-1", "+1", "1e3", "1.", ".5", "100000000", " 1", "1,000", "NaN", ""]
)
def test_non_canonical_money_text_is_rejected(raw):
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_money(raw)
    assert exc.value.code == "format"


@pytest.mark.parametrize("raw", [33999, 33999.0, True, None])
def test_json_numbers_are_rejected_for_money(raw):
    with pytest.raises(cf.CanonicalFormError) as exc:
        cf.parse_money(raw)
    assert exc.value.code == "type"


def test_decimal_values_are_accepted_only_with_at_most_two_places():
    assert cf.parse_money(Decimal("12.5")) == Decimal("12.50")
    with pytest.raises(cf.CanonicalFormError):
        cf.parse_money(Decimal("12.345"))


# ---- CSV scalars ---------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "value"), [("0", 0), ("7", 7), ("10", 10)])
def test_csv_integers(raw, value):
    assert cf.parse_csv_integer(raw) == value


@pytest.mark.parametrize("raw", ["01", "1.0", "+1", "-1", "", " 1", "1 ", "١"])
def test_non_canonical_csv_integers_are_rejected(raw):
    with pytest.raises(cf.CanonicalFormError):
        cf.parse_csv_integer(raw)


def test_csv_booleans():
    assert cf.parse_csv_boolean("true") is True
    assert cf.parse_csv_boolean("false") is False
    for raw in ("True", "1", "yes", "", "true "):
        with pytest.raises(cf.CanonicalFormError):
            cf.parse_csv_boolean(raw)


# ---- hashes --------------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_keeps_non_ascii():
    text = cf.canonical_json({"b": None, "a": "जूता", "c": [1, True]})
    assert text == '{"a":"जूता","b":null,"c":[1,true]}'


def test_record_sha256_is_the_documented_formula():
    record = {"order_id": "SYN-ORD-1", "note": None}
    expected = hashlib.sha256(
        b'return-data-v1\norders\n{"note":null,"order_id":"SYN-ORD-1"}'
    ).hexdigest()
    assert cf.record_sha256("orders", record) == expected
    assert cf.record_sha256("returns", record) != expected  # entity is part of the hash


def test_record_sha256_ignores_key_order_but_not_values():
    a = {"x": "1", "y": None}
    b = {"y": None, "x": "1"}
    assert cf.record_sha256("orders", a) == cf.record_sha256("orders", b)
    assert cf.record_sha256("orders", a) != cf.record_sha256("orders", {"x": "1"})


def test_descriptor_sha256_uses_the_dataset_entity_name():
    block = {"dataset_id": "d"}
    assert cf.descriptor_sha256(block) == cf.record_sha256("dataset", block)


def test_bundle_sha256_is_sorted_by_canonical_name():
    files = {"orders.csv": "a" * 64, "manifest.json": "b" * 64}
    expected = hashlib.sha256(
        f"return-data-v1\nmanifest.json\0{'b' * 64}\norders.csv\0{'a' * 64}\n".encode()
    ).hexdigest()
    assert cf.bundle_sha256(files) == expected
    assert cf.bundle_sha256(dict(reversed(files.items()))) == expected
    assert cf.bundle_sha256({"bundle.json": "a" * 64}) != cf.bundle_sha256({"x.csv": "a" * 64})


def test_records_sha256_is_order_independent_and_sorted_by_entity_then_bytes():
    rows = [
        ("returns", "b", "1" * 64),
        ("orders", "Z", "2" * 64),
        ("orders", "a", "3" * 64),
    ]
    expected = hashlib.sha256(
        (f"orders\tZ\t{'2' * 64}\norders\ta\t{'3' * 64}\nreturns\tb\t{'1' * 64}\n").encode()
    ).hexdigest()
    assert cf.records_sha256(rows) == expected
    assert cf.records_sha256(reversed(rows)) == expected


def test_a_json_number_and_a_json_string_form_are_not_interchangeable():
    # JSON form of a hashed record keeps money as a string and integers as integers.
    record = json.loads(cf.canonical_json({"unit_price": "1.00", "quantity": 1}))
    assert record == {"unit_price": "1.00", "quantity": 1}
