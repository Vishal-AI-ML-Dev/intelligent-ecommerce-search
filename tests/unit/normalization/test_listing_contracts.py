"""M13-1 output contracts: candidates, findings, conflict invariants, evidence and output hash.

Candidates here are hand-built synthetic fixtures; no normalization algorithm exists yet.
"""

import pytest
from pydantic import ValidationError

from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.models import (
    AttributeCandidate,
    EvidenceMismatch,
    Finding,
    FindingKind,
    ListingNormalization,
    Outcome,
)
from ecommerce_search.normalization.rules import Basis, ValueType

SELLER, TITLE = Basis.SELLER_FIELD, Basis.TITLE
N, K = Outcome.NORMALIZED, FindingKind


def ram(value="16", raw="16 gb", key="ram_gb", **kw) -> AttributeCandidate:
    fields = {
        "field": "ram_gb",
        "basis": SELLER,
        "source_key": key,
        "raw_text": raw,
        "outcome": N,
        "normalized_value": value,
        "value_type": ValueType.INTEGER,
        "unit": "GB",
        "rule_id": "measure.ram",
    }
    return AttributeCandidate(**(fields | kw))


def title_ram(value="8", start=10, raw="8GB RAM") -> AttributeCandidate:
    return AttributeCandidate(
        field="ram_gb",
        basis=TITLE,
        source_key="title",
        raw_text=raw,
        span_start=start,
        span_end=start + len(raw),
        outcome=N,
        normalized_value=value,
        value_type=ValueType.INTEGER,
        unit="GB",
        rule_id="title.ram_pattern",
    )


def norm(candidates=(), findings=()) -> ListingNormalization:
    return ListingNormalization(
        rules_version=nr.RULES_VERSION,
        rules_digest="0" * 64,
        taxonomy_version="1",
        candidates=tuple(candidates),
        findings=tuple(findings),
    )


def conflict(field="ram_gb") -> Finding:
    return Finding(kind=K.VALUE_CONFLICT, field=field, message="different values")


# ---- candidates --------------------------------------------------------------------------


def test_contracts_are_frozen_and_forbid_extra_fields():
    candidate = ram()
    with pytest.raises(ValidationError):
        candidate.normalized_value = "8"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ram(resolved="16")


@pytest.mark.parametrize(
    "overrides",
    [
        {"field": "colour"},
        {"rule_id": "measure.unknown"},
        {"rule_id": "measure.capacity"},  # rule not for ram_gb
        {"rule_id": "title.ram_pattern"},  # text rule on a seller field
        {"source_key": "storage_gb"},  # key does not match field
        {"span_start": 0, "span_end": 5},  # seller field with span
        {"normalized_value": None},
        {"value_type": ValueType.DECIMAL},
        {"unit": None},
        {"unit": "MB"},
        {"normalized_value": "016"},
        {"normalized_value": "16.0"},
        {"normalized_value": "-16"},
        {"raw_text": ""},
        {"source_key": ""},
    ],
)
def test_invalid_candidates_are_rejected(overrides):
    with pytest.raises(ValidationError):
        ram(**overrides)


def test_seller_key_matching_is_trim_casefold_but_evidence_keeps_the_exact_key():
    candidate = ram(key=" RAM_GB ")
    assert candidate.source_key == " RAM_GB "


@pytest.mark.parametrize("outcome", [Outcome.UNSUPPORTED, Outcome.UNCERTAIN, Outcome.INVALID])
def test_non_normalized_outcomes_keep_raw_evidence_only(outcome):
    candidate = ram(
        raw="16 GiB", outcome=outcome, normalized_value=None, value_type=None, unit=None
    )
    assert candidate.raw_text == "16 GiB"
    with pytest.raises(ValidationError):
        ram(outcome=outcome)  # still carries a normalized value


@pytest.mark.parametrize(
    ("field", "rule", "vtype", "unit", "good", "bad"),
    [
        (
            "price",
            "measure.price",
            ValueType.DECIMAL,
            None,
            "40000.00",
            ["40000", "40000.0", "4E+4"],
        ),
        ("screen_size_inches", "measure.screen", ValueType.DECIMAL, "in", "14.0", ["14", "NaN"]),
        ("storage_type", "enum.casefold", ValueType.ENUM, None, "SSD", ["ssd", "NVME"]),
        ("wireless", "boolean.strict", ValueType.BOOLEAN, None, "true", ["True", "yes"]),
        (
            "processor",
            "text.whitespace",
            ValueType.TEXT,
            None,
            "Intel Core i5",
            ["Intel  Core", " x"],
        ),
    ],
)
def test_normalized_values_must_be_canonical_for_their_field(field, rule, vtype, unit, good, bad):
    def make(value):
        return AttributeCandidate(
            field=field,
            basis=SELLER,
            source_key=field,
            raw_text="raw",
            outcome=N,
            normalized_value=value,
            value_type=vtype,
            unit=unit,
            rule_id=rule,
        )

    make(good)
    for value in bad:
        with pytest.raises(ValidationError):
            make(value)


def test_storage_term_rule_may_target_the_related_field():
    AttributeCandidate(
        field="storage_interface",
        basis=SELLER,
        source_key="storage_type",
        raw_text="NVMe SSD",
        outcome=N,
        normalized_value="NVME",
        value_type=ValueType.ENUM,
        rule_id="storage.term",
    )
    with pytest.raises(ValidationError):  # other rules may not cross fields
        AttributeCandidate(
            field="storage_interface",
            basis=SELLER,
            source_key="storage_type",
            raw_text="NVME",
            outcome=N,
            normalized_value="NVME",
            value_type=ValueType.ENUM,
            rule_id="enum.casefold",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"span_start": None, "span_end": None},
        {"span_start": 5, "span_end": 5},
        {"span_start": -1, "span_end": 6},
        {"span_start": 0, "span_end": 3},  # length differs from raw_text
        {"source_key": "description"},
    ],
)
def test_text_candidates_need_a_consistent_span_on_their_text_field(overrides):
    fields = title_ram().model_dump() | overrides
    with pytest.raises(ValidationError):
        AttributeCandidate(**fields)


# ---- findings ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": K.VALUE_CONFLICT},
        {"kind": K.VALUE_CONFLICT, "field": "colour"},
        {"kind": K.CROSS_FIELD_CONTRADICTION, "field": "storage_interface"},
        {"kind": K.CROSS_FIELD_CONTRADICTION, "field": "wireless", "related_field": "wireless"},
        {"kind": K.VALUE_CONFLICT, "field": "ram_gb", "related_field": "storage_gb"},
        {"kind": K.UNSUPPORTED_ATTRIBUTE},
        {"kind": K.UNSUPPORTED_ATTRIBUTE, "source_key": "rating", "field": "ram_gb"},
        {"kind": K.UNSUPPORTED_ATTRIBUTE, "source_key": "seller_id"},
        {"kind": K.UNSUPPORTED_ATTRIBUTE, "source_key": " RAM_GB"},  # supported field
        {"kind": K.COMPATIBILITY_UNCHECKED, "field": "ram_gb"},
        {"kind": K.INCOMPATIBLE_ATTRIBUTE, "field": "price"},
        {"kind": K.INCOMPATIBLE_SUBCATEGORY, "field": "category"},
        {"kind": K.BRAND_CATEGORY_MISMATCH, "field": "title"},
        {"kind": K.MISSING_REQUIRED, "field": "description"},
        {"kind": K.MISSING_EXPECTED, "field": "price"},
        {"kind": K.MISSING_REQUIRED, "field": "title", "source_key": "title"},
        {"kind": K.MISSING_REQUIRED, "field": "title", "message": ""},
        {"kind": K.EMPTY_VALUE, "field": "ram_gb"},  # no source key
        {"kind": K.EMPTY_VALUE, "field": "ram_gb", "source_key": "storage_gb"},
        {"kind": K.EMPTY_VALUE, "source_key": "rating"},
    ],
)
def test_invalid_findings_are_rejected(fields):
    with pytest.raises(ValidationError):
        Finding(**({"message": "m"} | fields))


def test_valid_findings():
    Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="rating", message="seller rating")
    Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="review_count", message="seller count")
    Finding(kind=K.COMPATIBILITY_UNCHECKED, message="category unknown")
    Finding(
        kind=K.CROSS_FIELD_CONTRADICTION,
        field="storage_interface",
        related_field="storage_type",
        message="NVME with HDD",
    )
    Finding(kind=K.INCOMPATIBLE_ATTRIBUTE, field="ram_gb", message="ram_gb on shoes")
    Finding(kind=K.MISSING_EXPECTED, field="battery_mah", message="missing")
    Finding(kind=K.EMPTY_VALUE, field="ram_gb", source_key=" RAM_GB", message="null")


# ---- normalization invariants ------------------------------------------------------------


def test_conflicting_values_require_a_conflict_finding_and_keep_both_candidates():
    with pytest.raises(ValidationError, match="value_conflict"):
        norm([ram("16"), title_ram("8")])
    result = norm([ram("16"), title_ram("8")], [conflict()])
    assert {c.normalized_value for c in result.candidates} == {"16", "8"}
    assert "resolved" not in ListingNormalization.model_fields


def test_conflict_finding_without_different_values_is_rejected():
    with pytest.raises(ValidationError, match="value_conflict"):
        norm([ram("16"), title_ram("16")], [conflict()])
    with pytest.raises(ValidationError, match="value_conflict"):
        norm([], [conflict()])


def test_non_normalized_candidates_do_not_create_conflicts():
    uncertain = ram(
        raw="16 GiB", outcome=Outcome.UNSUPPORTED, normalized_value=None, value_type=None, unit=None
    )
    norm([uncertain, title_ram("8")])


def test_missing_cannot_be_reported_for_a_supplied_field():
    missing = Finding(kind=K.MISSING_EXPECTED, field="ram_gb", message="missing")
    with pytest.raises(ValidationError, match="missing"):
        norm([ram()], [missing])
    norm([title_ram()], [missing])  # a title extraction is not a seller-supplied value


def test_an_empty_value_counts_as_supplied_and_cannot_be_reported_missing():
    empty = Finding(kind=K.EMPTY_VALUE, field="ram_gb", source_key="ram_gb", message="null")
    missing = Finding(kind=K.MISSING_EXPECTED, field="ram_gb", message="missing")
    norm([], [empty])
    with pytest.raises(ValidationError, match="missing"):
        norm([], [empty, missing])


def test_empty_value_evidence_must_be_null_or_blank():
    raw = parse_raw_listing(
        b'{"seller_id":"s","seller_listing_ref":"r","ram_gb":null,"color":" \\t","gpu":"x"}'
    )

    def empty(field):
        return norm([], [Finding(kind=K.EMPTY_VALUE, field=field, source_key=field, message="m")])

    empty("ram_gb").verify_evidence(raw)
    empty("color").verify_evidence(raw)
    with pytest.raises(EvidenceMismatch):
        empty("gpu").verify_evidence(raw)
    with pytest.raises(EvidenceMismatch):
        empty("gpu").verify_evidence(
            parse_raw_listing(b'{"seller_id":"s","seller_listing_ref":"r"}')
        )


def test_duplicates_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        norm([ram(), ram()])
    rating = Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="rating", message="m")
    with pytest.raises(ValidationError, match="duplicate"):
        norm([], [rating, rating])


# ---- output hash -------------------------------------------------------------------------


def test_output_hash_is_order_independent_and_covers_exactly_the_model_fields():
    rating = Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="rating", message="m")
    review = Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="review_count", message="m")
    a = norm([ram("16"), title_ram("8")], [conflict(), rating, review])
    b = norm([title_ram("8"), ram("16")], [review, rating, conflict()])
    assert a == b
    assert a.output_sha256() == b.output_sha256()
    assert set(ListingNormalization.model_fields) == {
        "rules_version",
        "rules_digest",
        "taxonomy_version",
        "candidates",
        "findings",
    }
    assert a.canonical_json() == nr.canonical_json(a.model_dump(mode="json"))


@pytest.mark.parametrize(
    "change",
    [
        {"rules_version": "listing-norm-2"},
        {"rules_digest": "1" * 64},
        {"taxonomy_version": "2"},
        {"candidates": (ram("8"),)},
        {"findings": (Finding(kind=K.COMPATIBILITY_UNCHECKED, message="m"),)},
    ],
)
def test_output_hash_changes_with_any_included_field(change):
    base = norm([ram("16")])
    changed = ListingNormalization(**(dict(base) | change))
    assert changed.output_sha256() != base.output_sha256()


def test_key_order_equivalent_raw_inputs_share_the_output_hash_but_not_the_raw_hash():
    raw_a = parse_raw_listing(b'{"seller_id":"s","seller_listing_ref":"r","ram_gb":"16 gb","x":1}')
    raw_b = parse_raw_listing(b'{"x":1,"ram_gb":"16 gb","seller_listing_ref":"r","seller_id":"s"}')
    out = norm([ram("16")])
    out.verify_evidence(raw_a)
    out.verify_evidence(raw_b)
    assert raw_a.raw_sha256 != raw_b.raw_sha256
    assert out.output_sha256() == norm([ram("16")]).output_sha256()


def test_output_hash_is_utf8_without_ascii_escaping():
    candidate = AttributeCandidate(
        field="color",
        basis=SELLER,
        source_key="color",
        raw_text="Bleu  clair é",
        outcome=N,
        normalized_value="Bleu clair é",
        value_type=ValueType.TEXT,
        rule_id="text.whitespace",
    )
    assert "é".encode() in norm([candidate]).canonical_json()


# ---- evidence ----------------------------------------------------------------------------


def test_evidence_quotes_the_raw_listing_exactly():
    raw = parse_raw_listing(
        '{"seller_id":"s","seller_listing_ref":"r","title":"Zénith 14 8GB RAM laptop",'
        '"RAM_GB":16,"rating":4.5}'.encode()
    )
    start = "Zénith 14 ".__len__()
    rating = Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="rating", message="m")
    good = norm(
        [ram("16", raw="16", key="RAM_GB"), title_ram("8", start=start)], [conflict(), rating]
    )
    good.verify_evidence(raw)

    for bad in (
        norm([ram("16", raw="16 ", key="RAM_GB")]),  # raw_text differs from the literal
        norm([ram("16", raw="16", key="ram_gb")]),  # exact key differs
        norm([title_ram("8", start=start + 1)]),  # span off by one
        norm([], [Finding(kind=K.UNSUPPORTED_ATTRIBUTE, source_key="colour", message="m")]),
    ):
        with pytest.raises(EvidenceMismatch):
            bad.verify_evidence(raw)
