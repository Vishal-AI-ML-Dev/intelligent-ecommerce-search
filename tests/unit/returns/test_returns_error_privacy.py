"""AC-10 (domain): privacy-safe errors (contracts §4.8, §5.1). Hermetic.

Hostile personal-data-like values (fixture-allowlist forms, contracts §5.3) are placed on every
surface Phase A reports on: client file names, JSON keys, CSV header names, manifest entries,
identifiers, enum values, actor references, free text, dataset text, the contract version and
syntax errors. None may appear in a report or a log record. This checks the listed surfaces
only; it is not a claim of comprehensive personal-data detection.
"""

import hashlib
import json
import logging

import pytest

from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns import redaction as rd
from ecommerce_search.returns.bundle import Part
from returns_contract_sets import CONTRACT_FIELDS, DATASET_FIELDS
from returns_samples import DATASET
from returns_uploads import csv_parts, json_bytes, json_parts, sample_records, validate

EMAIL = "buyer@example.com"
PHONE = "+91 00000 00000"
PIN = "012345"
UPI = "buyer@okexample"
SECRETS = (EMAIL, "buyer@", "00000 00000", PIN, "okexample", "Expecting", "pydantic", "Traceback")
HOSTILE = f"{EMAIL} {PHONE} {PIN} {UPI}"


def record_change(entity: str, **changes) -> list[Part]:
    records = sample_records()
    records[entity][0] |= changes
    return json_parts(records)


def csv_header_with_hostile_name() -> list[Part]:
    parts = csv_parts()
    orders = parts[[p.name for p in parts].index("orders.csv")]
    data = orders.data.replace(b'"currency"', json.dumps(HOSTILE).encode(), 1)
    return [Part(p.name, data if p.name == "orders.csv" else p.data) for p in parts]


def manifest_with_hostile_name() -> list[Part]:
    data = b"order_id\n"
    entry = {
        "name": f"{EMAIL}.csv",
        "entity": EMAIL,
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": PIN,
    }
    manifest = {"contract_version": "return-data-v1", "dataset": DATASET, "files": [entry]}
    return [Part("manifest.json", json_bytes(manifest)), Part(f"{EMAIL}.csv", data)]


CASES = {
    "client file name": lambda: [*json_parts(), Part(f"{HOSTILE}.json", b"{}")],
    "top-level JSON key": lambda: json_parts(**{HOSTILE: 1}),
    "record JSON key": lambda: record_change("orders", **{HOSTILE: 1}),
    "duplicate JSON key": lambda: [Part("bundle.json", f'{{"{EMAIL}": 1, "{EMAIL}": 2}}'.encode())],
    "CSV header": csv_header_with_hostile_name,
    "manifest entry": manifest_with_hostile_name,
    "contract version": lambda: json_parts(contract_version=HOSTILE),
    "JSON syntax": lambda: [Part("bundle.json", f'{{"a": {HOSTILE}}}'.encode())],
    "CSV syntax": lambda: [
        Part(
            "manifest.json",
            json_bytes(
                {
                    "contract_version": "return-data-v1",
                    "dataset": DATASET,
                    "files": [
                        {
                            "name": "orders.csv",
                            "entity": "orders",
                            "sha256": hashlib.sha256(f'x"{HOSTILE}'.encode()).hexdigest(),
                            "rows": 0,
                        }
                    ],
                }
            ),
        ),
        Part("orders.csv", f'x"{HOSTILE}'.encode()),
    ],
    "record ID format": lambda: record_change("orders", order_id=f"SYN {HOSTILE}"),
    "record ID pattern": lambda: record_change("orders", order_id=f"SYN-ORD-{PIN}"),
    "carrier ref": lambda: record_change("shipments", carrier_ref="carrier-0000000000"),
    "enum value": lambda: record_change("orders", currency=HOSTILE),
    "timestamp": lambda: record_change("orders", ordered_at=HOSTILE),
    "money": lambda: record_change("order_items", unit_price=HOSTILE),
    "integer": lambda: record_change("order_items", quantity=HOSTILE),
    "actor reference": lambda: record_change("orders", customer_ref=HOSTILE),
    "free text too long": lambda: record_change("return_comments", text=HOSTILE + "x" * 2000),
    "free text wrong type": lambda: record_change("return_comments", text=[HOSTILE]),
    "note too long": lambda: record_change("inspection_observations", note=HOSTILE * 40),
    "dataset text": lambda: json_parts(dataset=DATASET | {"source_name": HOSTILE}),
    "dataset id": lambda: json_parts(dataset=DATASET | {"dataset_id": HOSTILE}),
    "dataset type": lambda: json_parts(dataset=DATASET | {"dataset_version": HOSTILE}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_no_hostile_value_is_echoed_in_reports_or_logs(case, caplog):
    caplog.set_level(logging.DEBUG)
    report = validate(CASES[case]()).report
    assert report.error_count > 0, case
    text = report.model_dump_json()
    for secret in SECRETS:
        assert secret not in text, (case, secret)
    for error in report.errors:
        assert error.message == er.MESSAGES[error.code]
        assert (
            error.field in (None, er.UNRECOGNIZED)
            or error.field in er.KNOWN_KEYS
            or error.field.isidentifier()
        )
        assert (
            error.file in {"bundle.json", "manifest.json"}
            or error.file.endswith(".csv")
            or error.file.startswith("unexpected part #")
        )
        if error.value_preview is not None:
            assert len(error.value_preview) <= rd.PREVIEW_MAX
            assert not rd.matches_any(error.value_preview)
    for record in caplog.records:
        for secret in SECRETS:
            assert secret not in record.getMessage()


def test_validation_emits_no_log_records(caplog):
    caplog.set_level(logging.DEBUG)
    for build in CASES.values():
        validate(build())
    assert [r for r in caplog.records if r.name.startswith("ecommerce_search")] == []


def test_enum_preview_is_redacted_and_bounded():
    (error,) = validate(record_change("orders", currency=f"{EMAIL} {'z' * 200}")).report.errors
    assert (error.code, error.value_preview) == ("enum", "[EMAIL] " + "z" * 72)
    (error,) = validate(record_change("orders", currency="usd")).report.errors
    assert error.value_preview == "usd"


@pytest.mark.parametrize(
    ("entity", "changes"),
    [
        ("orders", {"customer_ref": "SYN-CUST-1"}),
        ("return_comments", {"text": "x" * 2001}),
        ("inspection_observations", {"inspector_ref": "inspector"}),
        ("correction_cases", {"opened_by_ref": "staff"}),
        ("orders", {"order_id": "SYN-ORD-012345"}),
    ],
)
def test_no_preview_for_free_text_actor_references_or_personal_looking_ids(entity, changes):
    (error,) = validate(record_change(entity, **changes)).report.errors
    assert error.value_preview is None


def test_no_preview_for_dataset_text():
    errors = validate(json_parts(dataset=DATASET | {"license_identifier": "x" * 201})).report.errors
    assert [(e.field, e.code, e.value_preview) for e in errors] == [
        ("license_identifier", "too_long", None)
    ]


def test_every_code_has_a_fixed_value_free_template():
    contract_codes = {
        "required",
        "type",
        "format",
        "enum",
        "too_long",
        "out_of_range",
        "unknown_column",
        "missing_column",
        "duplicate_in_bundle",
        "unknown_reference",
        "unknown_product",
        "snapshot_mismatch",
        "inconsistent_reference",
        "temporal_order",
        "quantity_exceeded",
        "duplicate_shipment",
        "conflict_existing",
        "not_synthetic",
        "looks_personal",
        "encoding",
        "contract_version",
        "dataset_mismatch",
        "missing_file",
        "unexpected_file",
        "checksum_mismatch",
        "row_count_mismatch",
        "bundle_too_large",
        "file_too_large",
        "too_many_files",
        "too_many_rows",
        "too_many_columns",
        "field_too_large",
    }  # contracts §4.8 plus the row and file limit codes of §4.4
    assert contract_codes == er.ROW_ERROR_CODES
    for message in er.MESSAGES.values():
        assert "{" not in message and not rd.matches_any(message)


def test_known_keys_are_exactly_the_fixed_contract_names():
    envelope = {"contract_version", "dataset", "files", "name", "entity", "sha256", "rows"}
    fields = set().union(*CONTRACT_FIELDS.values())
    expected = envelope | DATASET_FIELDS | set(CONTRACT_FIELDS) | fields
    assert expected == er.KNOWN_KEYS


def test_safe_key_and_pointer():
    assert er.safe_key("catalog_raw_sha256") == "catalog_raw_sha256"  # a contract name
    assert er.safe_key("some_key") == "some_key"
    for hostile in ("Some_Key", "key1", EMAIL, "a" * 41, "", None, 3):
        assert er.safe_key(hostile) == er.UNRECOGNIZED
    assert er.pointer("orders", 0, EMAIL) == "/orders/0/<unrecognized>"


def test_records_keep_raw_text_only_in_memory_and_expose_the_redacted_form():
    records = sample_records()
    records["return_comments"][0]["text"] = f"Reach me at {EMAIL}"
    (comment,) = validate(csv_parts(records)).records[rc.Entity.RETURN_COMMENTS]
    assert comment.redaction.text == "Reach me at [EMAIL]"
