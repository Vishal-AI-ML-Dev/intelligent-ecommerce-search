"""M13-1 intake contract: exact raw bytes, explicit rejection reasons, identity contract.

All inputs are synthetic test fixtures; they establish contract behaviour only.
"""

import hashlib

import pytest

from ecommerce_search.normalization.intake import (
    IDENTITY_MAX_LENGTH,
    MAX_RAW_BYTES,
    IntakeReason,
    JsonNumber,
    ListingIdentity,
    ListingIntakeError,
    parse_raw_listing,
    raw_sha256,
    raw_value_text,
)

ID = '"seller_id":"s-1","seller_listing_ref":"L-1"'


def _raw(body: str = "") -> bytes:
    return ("{" + ID + ("," + body if body else "") + "}").encode("utf-8")


def _reason(data: bytes) -> IntakeReason:
    with pytest.raises(ListingIntakeError) as info:
        parse_raw_listing(data)
    return info.value.reason


def test_raw_bytes_are_kept_exactly_and_hashed_without_decoding():
    data = b'  {"seller_id" : "s-1",\t"seller_listing_ref":"L-1", "title":"HP  \\u00e9 \xc3\xa9"}  '
    listing = parse_raw_listing(data)
    assert listing.raw_bytes is data
    assert listing.raw_sha256 == hashlib.sha256(data).hexdigest() == raw_sha256(data)
    assert listing.identity == ListingIdentity(seller_id="s-1", seller_listing_ref="L-1")
    assert listing.value("title") == "HP  é é"


def test_fields_keep_source_order_and_number_literals():
    listing = parse_raw_listing(_raw('"ram":16,"screen":14.0,"w":1e3,"neg":-0,"ok":true,"n":null'))
    assert [k for k, _ in listing.fields] == [
        "seller_id",
        "seller_listing_ref",
        "ram",
        "screen",
        "w",
        "neg",
        "ok",
        "n",
    ]
    assert listing.value("screen") == JsonNumber("14.0")
    assert [raw_value_text(listing.value(k)) for k in ("ram", "screen", "w", "neg", "ok", "n")] == [
        "16",
        "14.0",
        "1e3",
        "-0",
        "true",
        None,
    ]
    assert listing.value("screen").as_decimal().as_tuple().exponent == -1


def test_key_order_changes_the_raw_hash():
    a = parse_raw_listing(_raw('"a":"1","b":"2"'))
    b = parse_raw_listing(_raw('"b":"2","a":"1"'))
    assert a.raw_sha256 != b.raw_sha256


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", IntakeReason.EMPTY),
        (b"\xef\xbb\xbf" + _raw(), IntakeReason.BYTE_ORDER_MARK),
        (b'{"seller_id":"s","seller_listing_ref":"r","t":"\xff"}', IntakeReason.NOT_UTF8),
        (b"\xed\xa0\x80", IntakeReason.NOT_UTF8),  # UTF-8-encoded surrogate
        (b"{", IntakeReason.INVALID_JSON),
        (b"   ", IntakeReason.INVALID_JSON),
        (_raw() + b"{}", IntakeReason.INVALID_JSON),
        (b"[1]", IntakeReason.NOT_AN_OBJECT),
        (b'"x"', IntakeReason.NOT_AN_OBJECT),
        (_raw('"title":"a","title":"b"'), IntakeReason.DUPLICATE_KEY),
        (_raw('"RAM_GB":"8"," ram_gb ":"16"'), IntakeReason.COLLIDING_KEYS),
        (_raw('"Seller_ID":"x"'), IntakeReason.COLLIDING_KEYS),
        (_raw('" ":"x"'), IntakeReason.BLANK_KEY),
        (_raw('"spec":{"ram":8}'), IntakeReason.NON_SCALAR_VALUE),
        (_raw('"tags":["a"]'), IntakeReason.NON_SCALAR_VALUE),
        (_raw('"p":NaN'), IntakeReason.NON_FINITE_NUMBER),
        (_raw('"p":-Infinity'), IntakeReason.NON_FINITE_NUMBER),
        (_raw('"t":"\\ud800"'), IntakeReason.LONE_SURROGATE),
        (_raw('"\\udc00":"x"'), IntakeReason.LONE_SURROGATE),
        (b'{"seller_id":"s-1"}', IntakeReason.MISSING_IDENTITY),
        (b'{"Seller_Id":"s-1","seller_listing_ref":"L"}', IntakeReason.MISSING_IDENTITY),
    ],
)
def test_invalid_intake_is_rejected_with_an_explicit_reason(data, reason):
    assert _reason(data) is reason


def test_nested_duplicate_keys_are_rejected_too():
    assert _reason(_raw('"spec":{"a":1,"a":2}')) is IntakeReason.DUPLICATE_KEY


def test_size_boundary_is_inclusive():
    base = _raw('"d":""')
    exact = base[:-2] + b"x" * (MAX_RAW_BYTES - len(base)) + b'"}'
    assert len(exact) == MAX_RAW_BYTES
    parse_raw_listing(exact)
    over = base[:-2] + b"x" * (MAX_RAW_BYTES - len(base) + 1) + b'"}'
    assert _reason(over) is IntakeReason.TOO_LARGE


def test_deep_nesting_is_an_explicit_intake_rejection():
    deep = b'{"seller_id":"s","seller_listing_ref":"r","x":' + b"[" * 30000 + b"]" * 30000 + b"}"
    assert len(deep) <= MAX_RAW_BYTES
    assert _reason(deep) in (IntakeReason.INVALID_JSON, IntakeReason.NON_SCALAR_VALUE)


@pytest.mark.parametrize(
    "value",
    ['""', '" s-1"', '"s-1 "', '"s\\u0000"', '"s\\u200b1"', "1", "true", "null"]
    + ['"' + "a" * (IDENTITY_MAX_LENGTH + 1) + '"'],
)
def test_identity_values_are_bounded_strings_validated_as_given(value):
    data = ('{"seller_id":' + value + ',"seller_listing_ref":"L-1"}').encode()
    assert _reason(data) is IntakeReason.INVALID_IDENTITY


def test_identity_values_are_not_trimmed_or_casefolded():
    a = parse_raw_listing(b'{"seller_id":"Seller A","seller_listing_ref":"Ref-1"}')
    b = parse_raw_listing(b'{"seller_id":"seller a","seller_listing_ref":"ref-1"}')
    assert a.identity.seller_id == "Seller A"
    assert a.identity != b.identity
    long = "é" * IDENTITY_MAX_LENGTH
    parse_raw_listing(('{"seller_id":"' + long + '","seller_listing_ref":"r"}').encode())


def test_programming_errors_are_not_converted_to_intake_errors():
    with pytest.raises(TypeError):
        parse_raw_listing('{"seller_id":"s"}')  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        raw_sha256("text")  # type: ignore[arg-type]


def test_value_problems_are_not_intake_problems():
    # Unknown keys, odd units and rating are accepted at intake; normalization records them.
    listing = parse_raw_listing(_raw('"ram_gb":"16 GiB","rating":4.9,"colour":"red","price":"40k"'))
    assert listing.value("rating") == JsonNumber("4.9")
    assert listing.has_key("colour")
