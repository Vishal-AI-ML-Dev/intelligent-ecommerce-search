"""AC-01, AC-04 and AC-05 (Pydantic side): `return-data-v1` record contracts. Hermetic.

Every record is a SYNTHETIC test fixture (`tests/returns_samples.py`).
"""

import json

import pytest
from pydantic import ValidationError

from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from returns_contract_sets import (
    CONTRACT_FIELDS,
    DATASET_FIELDS,
    ENUM_VALUES,
    FORBIDDEN_FIELD_WORDS,
)
from returns_samples import DATASET, RECORDS

MODELS = rc.ENTITY_MODELS


def parse(entity: str, **overrides):
    return MODELS[rc.Entity(entity)].model_validate_json(
        json.dumps(RECORDS[entity] | overrides, ensure_ascii=False)
    )


def error_types(entity: str, **overrides) -> list[str]:
    with pytest.raises(ValidationError) as exc:
        parse(entity, **overrides)
    return [e["type"] for e in exc.value.errors()]


# ---- entity set, field allowlists and enums ------------------------------------------------


def test_the_eleven_entities_are_the_contract_entities():
    assert {e.value for e in rc.Entity} == set(CONTRACT_FIELDS)
    assert set(MODELS) == set(rc.Entity)
    assert all(model.ENTITY is entity for entity, model in MODELS.items())


@pytest.mark.parametrize("entity", sorted(CONTRACT_FIELDS))
def test_each_model_has_exactly_the_contract_fields(entity):
    assert set(MODELS[rc.Entity(entity)].model_fields) == CONTRACT_FIELDS[entity]


def test_the_dataset_descriptor_has_exactly_the_contract_fields():
    assert set(rc.DatasetDescriptor.model_fields) == DATASET_FIELDS


def test_no_model_field_is_a_decision_outcome_or_hypothesis():
    for model in (*MODELS.values(), rc.DatasetDescriptor):
        for name in model.model_fields:
            assert not set(name.split("_")) & FORBIDDEN_FIELD_WORDS, (model.__name__, name)


@pytest.mark.parametrize(
    ("enum_cls", "key"),
    [
        (rc.ShipmentStatus, "shipment_status"),
        (rc.DeliveryEventType, "delivery_event_type"),
        (rc.DeliveryEventSource, "delivery_event_source"),
        (rc.ReturnStatus, "return_status"),
        (rc.ReasonCode, "reason_code"),
        (rc.InspectionCondition, "inspection_condition"),
        (rc.ComplaintChannel, "complaint_channel"),
        (rc.ComplaintTopic, "complaint_topic"),
        (rc.CorrectionFocusArea, "focus_area"),
        (rc.CorrectionCaseStatus, "correction_case_status"),
        (rc.EvidenceType, "evidence_type"),
        (rc.Currency, "currency"),
        (rc.SourceType, "source_type"),
    ],
)
def test_enums_equal_the_frozen_contract_values(enum_cls, key):
    assert {m.value for m in enum_cls} == ENUM_VALUES[key]


def test_every_evidence_type_targets_a_stored_entity():
    assert set(rc.EVIDENCE_ENTITIES) == set(rc.EvidenceType)
    assert rc.Entity.CORRECTION_CASE_EVIDENCE not in rc.EVIDENCE_ENTITIES.values()


def test_customer_statements_and_inspection_observations_are_separate_records():
    statement_fields = set().union(
        CONTRACT_FIELDS["return_comments"],
        CONTRACT_FIELDS["support_complaints"],
        CONTRACT_FIELDS["customer_reviews"],
    )
    assert "condition" not in statement_fields and "inspector_ref" not in statement_fields
    assert "customer_reason_code" not in CONTRACT_FIELDS["inspection_observations"]
    assert not {"text"} & CONTRACT_FIELDS["inspection_observations"]


# ---- valid records, canonical form and provenance ------------------------------------------


@pytest.mark.parametrize("entity", sorted(RECORDS))
def test_every_sample_record_validates_and_round_trips(entity):
    record = parse(entity)
    canonical = record.canonical()
    assert set(canonical) == CONTRACT_FIELDS[entity]  # explicit nulls are present
    assert canonical["is_synthetic"] is True
    again = MODELS[rc.Entity(entity)].model_validate_json(json.dumps(canonical))
    assert again.record_sha256() == record.record_sha256()


def test_canonical_form_of_an_order_item():
    canonical = parse("order_items").canonical()
    assert canonical["unit_price"] == "2499.00" and canonical["quantity"] == 1


def test_absent_optional_values_hash_like_explicit_nulls():
    raw = dict(RECORDS["inspection_observations"])
    del raw["note"]
    absent = rc.InspectionObservation.model_validate_json(json.dumps(raw))
    assert absent.canonical()["note"] is None
    assert absent.record_sha256() == parse("inspection_observations").record_sha256()


def test_offset_spelling_does_not_change_the_hash():
    a = parse("orders", ordered_at="2026-01-05T10:00:00+05:30")
    b = parse("orders", ordered_at="2026-01-05T04:30:00.000000Z")
    assert a.record_sha256() == b.record_sha256()
    assert a.canonical()["ordered_at"] == "2026-01-05T04:30:00.000000Z"


def test_the_hash_covers_exactly_the_canonical_contract_fields():
    record = parse("returns")
    assert record.record_sha256() == cf.record_sha256("returns", record.canonical())


def test_csv_style_scalars_hash_like_the_json_form():
    json_form = parse("order_items")
    csv_row = {k: ("" if v is None else str(v)) for k, v in RECORDS["order_items"].items()}
    csv_row["quantity"] = cf.parse_csv_integer(csv_row["quantity"])
    csv_row["is_synthetic"] = cf.parse_csv_boolean(csv_row["is_synthetic"].lower())
    csv_form = rc.OrderItem.model_validate(csv_row)
    assert csv_form.record_sha256() == json_form.record_sha256()


def test_source_record_ids():
    assert parse("orders").source_record_id() == "SYN-ORD-00001"
    assert parse("correction_case_evidence").source_record_id() == (
        "SYN-CASE-00001|return|SYN-RET-00001"
    )


def test_the_longest_evidence_key_is_152_characters():
    longest = rc.CorrectionCaseEvidence(
        case_id="C" * 64,
        evidence_type="inspection_observation",
        evidence_id="E" * 64,
        is_synthetic=True,
    )
    assert len(longest.source_record_id()) == rc.EVIDENCE_KEY_MAX == 152


def test_dataset_descriptor_and_hash():
    descriptor = rc.DatasetDescriptor.model_validate_json(json.dumps(DATASET))
    assert descriptor.canonical()["authored_on"] == "2026-10-24"
    assert descriptor.descriptor_sha256() == cf.descriptor_sha256(descriptor.canonical())
    changed = rc.DatasetDescriptor.model_validate_json(json.dumps(DATASET | {"notes": "x"}))
    assert changed.descriptor_sha256() != descriptor.descriptor_sha256()


def test_records_are_immutable():
    record = parse("orders")
    with pytest.raises(ValidationError):
        record.order_id = "SYN-ORD-2"


# ---- synthetic only ------------------------------------------------------------------------


@pytest.mark.parametrize("entity", sorted(RECORDS))
def test_non_synthetic_records_are_rejected_as_not_synthetic(entity):
    assert error_types(entity, is_synthetic=False) == ["not_synthetic"]


@pytest.mark.parametrize("entity", sorted(RECORDS))
def test_is_synthetic_is_required_and_strict(entity):
    raw = {k: v for k, v in RECORDS[entity].items() if k != "is_synthetic"}
    with pytest.raises(ValidationError) as exc:
        MODELS[rc.Entity(entity)].model_validate_json(json.dumps(raw))
    assert [e["type"] for e in exc.value.errors()] == ["missing"]
    assert error_types(entity, is_synthetic="true") == ["bool_type"]


def test_non_synthetic_datasets_are_rejected():
    for override in ({"is_synthetic": False}, {"source_type": "public_dataset"}):
        with pytest.raises(ValidationError) as exc:
            rc.DatasetDescriptor.model_validate_json(json.dumps(DATASET | override))
        assert [e["type"] for e in exc.value.errors()] == ["not_synthetic"]


# ---- field rules ---------------------------------------------------------------------------


def test_unknown_fields_are_rejected():
    assert error_types("orders", sku="SYN-SHO-0001") == ["extra_forbidden"]


def test_integers_must_be_json_integers():
    assert error_types("order_items", quantity=1.0) == ["int_type"]
    assert error_types("order_items", quantity="1") == ["int_type"]
    assert error_types("order_items", quantity=True) == ["int_type"]
    assert error_types("order_items", quantity=0) == ["out_of_range"]
    assert error_types("order_items", quantity=11) == ["out_of_range"]
    assert parse("order_items", quantity=10).quantity == 10
    assert error_types("returns", quantity=0) == ["out_of_range"]
    assert error_types("customer_reviews", rating=6) == ["out_of_range"]


def test_money_must_be_a_canonical_decimal_string():
    assert error_types("order_items", unit_price=2499) == ["type"]
    assert error_types("order_items", unit_price=2499.0) == ["type"]
    assert error_types("order_items", unit_price="2499.001") == ["format"]


def test_timestamps_need_an_offset():
    assert error_types("orders", ordered_at="2026-01-05T10:00:00") == ["format"]
    assert error_types("orders", ordered_at="2026-01-05T10:00:00.1234567Z") == ["format"]


@pytest.mark.parametrize(
    "value",
    ["", "x" * 65, "has space", "pipe|inside", "-leading", "SYN-ORD-1\n", "ünïcode", "a/b"],
)
def test_record_ids_follow_the_id_pattern(value):
    assert error_types("orders", order_id=value) == ["format"]


def test_record_ids_allow_the_documented_alphabet_up_to_64():
    assert parse("orders", order_id="A" + "b9._:-" * 10 + "xyz").order_id
    assert len(parse("orders", order_id="A" * 64).order_id) == 64


@pytest.mark.parametrize(
    "value", ["SYN-CUST-0001", "SYN-CUST-000001", "syn-cust-00001", "SYN-USER-00001", "x"]
)
def test_actor_references_are_synthetic_actor_ids(value):
    assert error_types("orders", customer_ref=value) == ["format"]


def test_actor_reference_kinds():
    for ref in ("SYN-CUST-00001", "SYN-INSP-12345", "SYN-STAFF-99999"):
        assert parse("orders", customer_ref=ref).customer_ref == ref


def test_enum_values_are_exact():
    assert error_types("returns", status="Received") == ["enum"]
    assert error_types("returns", customer_reason_code="size_too_small") == ["enum"]
    assert error_types("orders", currency="USD") == ["enum"]


def test_free_text_limits_count_code_points():
    devanagari = "जू" * 1000  # 2,000 code points, more than 2,000 bytes
    assert parse("return_comments", text=devanagari).text == devanagari
    assert error_types("return_comments", text=devanagari + "x") == ["too_long"]
    assert error_types("return_comments", text="") == ["required"]
    assert error_types("return_comments", text=" \t\n　") == ["required"]
    assert parse("inspection_observations", note="n" * 1000).note
    assert error_types("inspection_observations", note="n" * 1001) == ["too_long"]
    assert parse("customer_reviews", text="t" * 2000).text
    assert error_types("customer_reviews", text="t" * 2001) == ["too_long"]


def test_text_is_never_trimmed_or_normalized():
    text = "  Café́ \r\n"
    assert parse("return_comments", text=text).text == text


def test_dataset_text_limits():
    def fails(**override) -> list[str]:
        with pytest.raises(ValidationError) as exc:
            rc.DatasetDescriptor.model_validate_json(json.dumps(DATASET | override))
        return [e["type"] for e in exc.value.errors()]

    assert fails(source_name="s" * 201) == ["too_long"]
    assert fails(rights_note="r" * 2001) == ["too_long"]
    assert fails(dataset_id="Synthetic") == ["format"]
    assert fails(dataset_id="d" * 49) == ["format"]
    assert fails(dataset_version=0) == ["out_of_range"]
    assert fails(dataset_version=1_000_000_000) == ["out_of_range"]
    assert fails(dataset_version="1") == ["int_type"]


# ---- row-local consistency -----------------------------------------------------------------


def test_shipment_delivery_fields():
    assert error_types("shipments", delivered_at=None) == ["required"]
    assert error_types("shipments", status="in_transit") == ["format"]
    assert parse("shipments", status="in_transit", delivered_at=None).delivered_at is None
    assert error_types("shipments", shipped_at="2026-01-09T00:00:00+05:30") == ["temporal_order"]


def test_return_receipt_fields():
    assert error_types("returns", received_at=None) == ["required"]
    assert error_types("returns", status="cancelled") == ["format"]
    assert parse("returns", status="cancelled", received_at=None).received_at is None
    assert parse("returns", status="closed", received_at=None).received_at is None
    assert error_types("returns", received_at="2026-01-09T00:00:00+05:30") == ["temporal_order"]


def test_a_complaint_needs_at_least_one_subject():
    assert error_types(
        "support_complaints", order_id=None, order_item_id=None, product_id=None
    ) == ["required"]
    assert parse("support_complaints", order_id=None, order_item_id=None).product_id


def test_correction_cases_are_opened_only():
    assert error_types("correction_cases", status="closed") == ["enum"]


def test_snapshot_triple_formats():
    assert error_types("order_items", catalog_dataset_version="01") == ["format"]
    assert error_types("order_items", catalog_dataset_version=1) == ["string_type"]
    assert error_types("order_items", catalog_raw_sha256="C" * 64) == ["format"]


# ---- privacy of errors ---------------------------------------------------------------------


def test_validation_errors_never_echo_the_input():
    value = "SYN-CUST-1 person@example.com"
    with pytest.raises(ValidationError) as exc:
        parse("orders", customer_ref=value)
    assert value not in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        parse("return_comments", text="x" * 2001)
    assert "xxxx" not in str(exc.value)
