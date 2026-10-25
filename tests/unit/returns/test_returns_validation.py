"""AC-06, AC-08 (format equivalence) and AC-10 (domain): Phase A steps 3-4. Hermetic.

Structural invalidity is rejected; conflicting evidence (contracts §3.1) is accepted with zero
errors; anything that needs stored rows is deferred, never reported here. Every record is a
SYNTHETIC fixture.
"""

import pytest

from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns import validation as va
from returns_samples import DATASET, RECORDS
from returns_uploads import CHECKED_AT, codes, csv_parts, json_parts, sample_records, validate

E = rc.Entity


def with_changes(entity: str, index: int = 0, **changes) -> dict[str, list[dict]]:
    records = sample_records()
    records[entity][index] = records[entity][index] | changes
    return records


def errors_of(records, **kwargs) -> list[tuple]:
    report = validate(json_parts(records, **kwargs)).report
    return [(e.entity, e.row, e.field, e.code) for e in report.errors]


# ---- helpers derived from the contracts ----------------------------------------------------


def test_id_fields_and_csv_kinds():
    screened = {e.value: set(va.id_fields(m)) for e, m in rc.ENTITY_MODELS.items()}
    assert screened["shipments"] == {"shipment_id", "order_id", "carrier_ref"}
    assert screened["order_items"] == {"order_item_id", "order_id", "product_id"}
    assert screened["correction_case_evidence"] == {"case_id", "evidence_id"}
    kinds = {}
    for model in rc.ENTITY_MODELS.values():
        kinds.update(va.csv_kinds(model))
    assert kinds == {"quantity": int, "rating": int, "is_synthetic": bool}


# ---- valid uploads and format equivalence --------------------------------------------------


def test_json_and_csv_forms_give_identical_records_and_hashes():
    json_result, csv_result = validate(json_parts()), validate(csv_parts())
    assert json_result.report.error_count == csv_result.report.error_count == 0
    assert json_result.dataset == csv_result.dataset == rc.DatasetDescriptor.model_validate(DATASET)

    def hashes(result):
        return sorted(
            (e.value, v.record.source_record_id(), v.record.record_sha256())
            for e, records in result.records.items()
            for v in records
        )

    assert hashes(json_result) == hashes(csv_result)
    assert cf.records_sha256(hashes(json_result)) == cf.records_sha256(hashes(csv_result))
    assert [v.row for v in csv_result.records[E.ORDERS]] == [1]
    assert csv_result.records[E.ORDERS][0].file == "orders.csv"


def test_free_text_is_redacted_while_the_record_hash_covers_the_raw_text():
    text = "Mail buyer@example.com or call 00000 00000 0, pin 012345"
    result = validate(json_parts(with_changes("return_comments", text=text)))
    (comment,) = result.records[E.RETURN_COMMENTS]
    assert comment.redaction.text == "Mail [EMAIL] or call [NUMBER], pin [PIN]"
    assert comment.redaction.counts == {"email": 1, "upi": 0, "number": 1, "pin": 1}
    assert comment.record.text == text
    raw = comment.record.canonical()
    assert raw["text"] == text
    assert comment.record.record_sha256() == cf.record_sha256("return_comments", raw)
    redacted = raw | {"text": comment.redaction.text}
    assert comment.record.record_sha256() != cf.record_sha256("return_comments", redacted)
    assert result.records[E.ORDERS][0].redaction is None
    assert result.records[E.INSPECTION_OBSERVATIONS][0].redaction is None  # note is null


def test_references_outside_the_bundle_are_deferred_not_errors():
    records = {
        "returns": [RECORDS["returns"] | {"order_item_id": "SYN-OI-99999"}],
        "inspection_observations": [RECORDS["inspection_observations"]],
        "correction_case_evidence": [
            RECORDS["correction_case_evidence"] | {"case_id": "SYN-CASE-99999"}
        ],
        "shipments": [RECORDS["shipments"] | {"order_id": "SYN-ORD-99999"}],
    }
    report = validate(json_parts(records)).report
    assert report.error_count == 0
    assert report.phases_completed == er.PHASES
    assert "stored_references" in report.deferred_checks
    assert "catalog_references" in report.deferred_checks
    assert "catalog_references" not in report.phases_completed


def test_promised_delivery_may_be_later_than_the_check_time():
    records = with_changes("shipments", promised_delivery_by="2027-01-01T00:00:00Z")
    assert errors_of(records) == []


# ---- step 3: field rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "changes", "field", "code"),
    [
        ("orders", {"currency": "USD"}, "currency", "enum"),
        ("orders", {"customer_ref": "CUST-1"}, "customer_ref", "format"),
        ("orders", {"order_id": "-bad"}, "order_id", "format"),
        ("orders", {"ordered_at": "2026-01-05 10:00:00"}, "ordered_at", "format"),
        ("orders", {"ordered_at": None}, "ordered_at", "required"),
        ("orders", {"is_synthetic": False}, "is_synthetic", "not_synthetic"),
        ("orders", {"extra": 1}, "extra", "unknown_column"),
        ("orders", {"extra": None}, "extra", "unknown_column"),
        ("order_items", {"unit_price": 2499.0}, "unit_price", "type"),
        ("order_items", {"quantity": 1.0}, "quantity", "type"),
        ("order_items", {"quantity": 11}, "quantity", "out_of_range"),
        ("order_items", {"quantity": True}, "quantity", "type"),
        ("customer_reviews", {"rating": 0}, "rating", "out_of_range"),
        ("return_comments", {"text": " \n "}, "text", "required"),
        ("return_comments", {"text": "x" * (rc.TEXT_MAX + 1)}, "text", "too_long"),
        ("inspection_observations", {"note": "x" * (rc.NOTE_MAX + 1)}, "note", "too_long"),
        ("correction_cases", {"status": "closed"}, "status", "enum"),
    ],
)
def test_field_rules(entity, changes, field, code):
    assert errors_of(with_changes(entity, **changes)) == [(entity, 1, field, code)]


def test_text_and_note_at_their_limits_are_accepted():
    records = with_changes("return_comments", text="x" * rc.TEXT_MAX)
    records["inspection_observations"][0]["note"] = "é" * rc.NOTE_MAX
    assert errors_of(records) == []


@pytest.mark.parametrize(
    ("entity", "changes", "code"),
    [
        ("shipments", {"delivered_at": None}, "required"),
        ("shipments", {"status": "in_transit"}, "format"),
        ("shipments", {"shipped_at": "2026-01-09T09:00:00+05:30"}, "temporal_order"),
        ("returns", {"status": "cancelled"}, "format"),
        ("returns", {"received_at": "2026-01-09T00:00:00+05:30"}, "temporal_order"),
        (
            "support_complaints",
            {"order_id": None, "order_item_id": None, "product_id": None},
            "required",
        ),
    ],
)
def test_row_local_status_rules_use_the_recorded_mapping(entity, changes, code):
    assert errors_of(with_changes(entity, **changes)) == [(entity, 1, None, code)]


def test_dataset_rules():
    assert errors_of(None, dataset=DATASET | {"source_type": "retailer"}) == [
        (None, None, "source_type", "not_synthetic")
    ]
    assert errors_of(None, dataset=DATASET | {"is_synthetic": False}) == [
        (None, None, "is_synthetic", "not_synthetic")
    ]
    assert errors_of(None, dataset=DATASET | {"source_name": "x" * 201}) == [
        (None, None, "source_name", "too_long")
    ]


@pytest.mark.parametrize(
    ("entity", "changes", "field"),
    [
        ("orders", {"order_id": "SYN-ORD-012345"}, "order_id"),
        ("shipments", {"carrier_ref": "carrier-0000000000"}, "carrier_ref"),
        ("order_items", {"product_id": "SYN-012345"}, "product_id"),
        ("correction_case_evidence", {"evidence_id": "R.054321"}, "evidence_id"),
    ],
)
def test_identifiers_matching_a_redaction_pattern_look_personal(entity, changes, field):
    assert (entity, 1, field, "looks_personal") in errors_of(with_changes(entity, **changes))


@pytest.mark.parametrize("field", ["source_name", "source_description", "rights_note", "notes"])
def test_dataset_text_matching_a_pattern_looks_personal(field):
    errors = errors_of(None, dataset=DATASET | {field: "contact buyer@example.com"})
    assert errors == [(None, None, field, "looks_personal")]


def test_csv_integer_and_boolean_forms():
    def csv_errors(entity, **changes):
        report = validate(csv_parts(with_changes(entity, **changes))).report
        return [(e.field, e.code, e.line) for e in report.errors]

    assert csv_errors("order_items", quantity="1.0") == [("quantity", "format", 2)]
    assert csv_errors("order_items", quantity="01") == [("quantity", "format", 2)]
    assert csv_errors("orders", is_synthetic="TRUE") == [("is_synthetic", "format", 2)]
    assert csv_errors("orders", is_synthetic=None) == [("is_synthetic", "required", 2)]
    assert csv_errors("orders", is_synthetic=False) == [("is_synthetic", "not_synthetic", 2)]
    assert csv_errors("customer_reviews", text=None) == []  # empty optional field is null


def test_a_json_row_that_is_not_an_object():
    report = validate(json_parts(sample_records(orders=[RECORDS["orders"], 7]))).report
    assert [(e.row, e.code, e.pointer) for e in report.errors] == [(2, "type", "/orders/1")]


# ---- step 4: structural rules (§3.1) -------------------------------------------------------

BEFORE_ORDER = "2026-01-05T09:00:00+05:30"


@pytest.mark.parametrize(
    ("entity", "changes", "field", "code"),
    [
        ("shipments", {"shipped_at": BEFORE_ORDER}, "shipped_at", "temporal_order"),
        (
            "shipments",
            {"shipped_at": None, "delivered_at": BEFORE_ORDER},
            "delivered_at",
            "temporal_order",
        ),
        ("delivery_events", {"occurred_at": BEFORE_ORDER}, "occurred_at", "temporal_order"),
        (
            "returns",
            {"requested_at": BEFORE_ORDER, "received_at": None, "status": "closed"},
            "requested_at",
            "temporal_order",
        ),
        ("return_comments", {"submitted_at": BEFORE_ORDER}, "submitted_at", "temporal_order"),
        (
            "inspection_observations",
            {"observed_at": "2026-01-10T10:00:00+05:30"},
            "observed_at",
            "temporal_order",
        ),
        ("customer_reviews", {"submitted_at": BEFORE_ORDER}, "submitted_at", "temporal_order"),
        ("support_complaints", {"opened_at": BEFORE_ORDER}, "opened_at", "temporal_order"),
        ("correction_cases", {"opened_at": "2026-10-25T12:00:01Z"}, "opened_at", "temporal_order"),
        (
            "customer_reviews",
            {"product_id": "SYN-SHO-0002"},
            "product_id",
            "inconsistent_reference",
        ),
        (
            "support_complaints",
            {"product_id": "SYN-SHO-0002"},
            "product_id",
            "inconsistent_reference",
        ),
        ("support_complaints", {"order_id": "SYN-ORD-00002"}, "order_id", "inconsistent_reference"),
        ("correction_cases", {"product_id": "SYN-SHO-0002"}, None, None),  # evidence row fails
    ],
)
def test_structural_rules_inside_the_bundle(entity, changes, field, code):
    errors = errors_of(with_changes(entity, **changes))
    if code is None:
        assert errors == [("correction_case_evidence", 1, "evidence_id", "inconsistent_reference")]
    else:
        assert errors == [(entity, 1, field, code)]


def test_import_time_bound_is_inclusive():
    records = with_changes("correction_cases", opened_at="2026-10-25T12:00:00Z")
    assert errors_of(records) == []
    assert CHECKED_AT.isoformat() == "2026-10-25T12:00:00+00:00"


def test_inspection_only_on_a_received_or_closed_return():
    for status in ("requested", "in_transit_to_warehouse"):
        records = with_changes("returns", status=status, received_at=None)
        assert errors_of(records) == [
            ("inspection_observations", 1, "return_id", "inconsistent_reference")
        ]
    assert errors_of(with_changes("returns", status="closed")) == []


def test_duplicates_and_second_shipment():
    records = sample_records()
    records["orders"].append(records["orders"][0] | {"customer_ref": "SYN-CUST-00002"})
    records["shipments"].append(records["shipments"][0] | {"shipment_id": "SYN-SHP-00002"})
    records["correction_case_evidence"].append(dict(records["correction_case_evidence"][0]))
    assert errors_of(records) == [  # bundle.json: entity, then row
        ("correction_case_evidence", 2, None, "duplicate_in_bundle"),
        ("orders", 2, "order_id", "duplicate_in_bundle"),
        ("shipments", 2, "order_id", "duplicate_shipment"),
    ]


def test_quantity_rule_counts_non_cancelled_returns_only():
    second = RECORDS["returns"] | {
        "return_id": "SYN-RET-00002",
        "status": "requested",
        "received_at": None,
    }
    over = sample_records(returns=[RECORDS["returns"], second])
    assert errors_of(over) == [("returns", 2, "quantity", "quantity_exceeded")]
    cancelled = RECORDS["returns"] | {"status": "cancelled", "received_at": None}
    ok = sample_records(returns=[cancelled, second])
    ok["inspection_observations"] = []
    assert errors_of(ok) == []


def test_quantity_over_the_maximum_item_quantity_is_definite_without_the_item():
    base = RECORDS["returns"] | {
        "order_item_id": "SYN-OI-99999",
        "status": "requested",
        "received_at": None,
    }
    records = {
        "returns": [
            base | {"return_id": "SYN-RET-00001", "quantity": 6},
            base | {"return_id": "SYN-RET-00002", "quantity": 4},
        ]
    }
    assert errors_of(records) == []
    records["returns"].append(base | {"return_id": "SYN-RET-00003", "quantity": 1})
    assert errors_of(records) == [("returns", 3, "quantity", "quantity_exceeded")]


def test_evidence_product_through_each_resolvable_kind():
    cases = {
        "return": "SYN-RET-00001",
        "return_comment": "SYN-RCM-00001",
        "inspection_observation": "SYN-INS-00001",
        "customer_review": "SYN-REV-00001",
        "support_complaint": "SYN-CMP-00001",
        "shipment": "SYN-SHP-00001",
        "delivery_event": "SYN-DEV-00001",
    }
    links = [
        {"case_id": "SYN-CASE-00001", "evidence_type": t, "evidence_id": i, "is_synthetic": True}
        for t, i in cases.items()
    ]
    assert errors_of(sample_records(correction_case_evidence=links)) == []
    records = sample_records(correction_case_evidence=links)
    records["correction_cases"][0]["product_id"] = "SYN-SHO-0002"
    flagged = {(row, code) for _, row, _, code in errors_of(records)}
    # Shipments and delivery events relate through an order whose stored items may contain the
    # product: never definite inside the bundle.
    assert flagged == {(r, "inconsistent_reference") for r in range(1, 6)}


# ---- conflicting evidence is accepted (§3.1) -----------------------------------------------


def test_conflicting_evidence_is_accepted_with_zero_errors():
    records = sample_records()
    records["returns"][0]["customer_reason_code"] = "NO_LONGER_NEEDED"
    records["return_comments"][0]["text"] = "It arrived damaged and broken."
    records["inspection_observations"][0]["condition"] = "as_new"
    records["inspection_observations"].append(
        records["inspection_observations"][0]
        | {
            "observation_id": "SYN-INS-00002",
            "condition": "damaged",
            "inspector_ref": "SYN-INSP-00002",
        }
    )
    records["delivery_events"].append(
        records["delivery_events"][0]
        | {"event_id": "SYN-DEV-00002", "event_type": "damage_reported_by_carrier"}
    )
    records["customer_reviews"][0] |= {"rating": 5, "text": "Great shoe, love it."}
    records["support_complaints"][0] |= {
        "customer_topic": "delivery",
        "text": "Item was never received.",
    }
    result = validate(json_parts(records))
    assert result.report.error_count == 0
    assert result.report.error_count_is_lower_bound is False
    assert len(result.records[E.INSPECTION_OBSERVATIONS]) == 2


# ---- reporting: order, determinism, truncation ---------------------------------------------


def duplicate_orders(errors: int) -> dict[str, list[dict]]:
    return {"orders": [RECORDS["orders"]] * (errors + 1)}


@pytest.mark.parametrize(
    ("errors", "count", "lower_bound", "truncated", "returned"),
    [
        (199, 199, False, False, 199),
        (200, 200, False, False, 200),
        (201, 201, False, True, 200),
        (1000, 1000, False, True, 200),
        (1001, 1000, True, True, 200),
    ],
)
def test_error_count_and_truncation(errors, count, lower_bound, truncated, returned):
    report = validate(json_parts(duplicate_orders(errors))).report
    assert report.error_count == count
    assert report.error_count_is_lower_bound is lower_bound
    assert report.errors_truncated is truncated
    assert len(report.errors) == returned
    assert [e.row for e in report.errors] == list(range(2, returned + 2))
    assert report.phases_completed == er.PHASES


def test_collection_stops_and_skips_step_4_when_step_3_fills_it():
    rows = [
        RECORDS["orders"] | {"order_id": f"SYN-ORD-{i:05d}", "currency": "USD"} for i in range(1001)
    ]
    report = validate(json_parts({"orders": rows})).report
    assert (report.error_count, report.error_count_is_lower_bound) == (1000, True)
    assert report.phases_completed == er.PHASES[:3]


def test_step_3_errors_make_the_count_a_lower_bound():
    report = validate(json_parts(with_changes("orders", currency="USD"))).report
    assert report.error_count == 1 and report.error_count_is_lower_bound is True


def test_errors_are_ordered_by_file_row_field_and_code_and_are_stable():
    records = sample_records()
    records["orders"][0] |= {"currency": "USD", "customer_ref": "x"}
    records["returns"][0] |= {"quantity": 0}
    records["shipments"][0] |= {"status": "lost"}
    first = validate(csv_parts(records)).report
    assert [(e.file, e.row, e.field, e.code) for e in first.errors] == [
        ("orders.csv", 1, "currency", "enum"),
        ("orders.csv", 1, "customer_ref", "format"),
        ("returns.csv", 1, "quantity", "out_of_range"),
        ("shipments.csv", 1, "status", "enum"),
    ]
    assert validate(csv_parts(records)).report == first


def test_records_are_returned_only_when_there_are_no_errors():
    result = validate(json_parts(with_changes("orders", currency="USD")))
    assert result.records == {} and result.dataset is None
    assert [f.name for f in result.files] == ["bundle.json"]


def test_checked_at_must_be_aware():
    from datetime import datetime

    with pytest.raises(TypeError):
        va.validate_upload(json_parts(), checked_at=datetime(2026, 10, 25))
    assert codes(validate(json_parts())) == []
