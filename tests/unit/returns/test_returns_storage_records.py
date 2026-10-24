"""AC-01 / AC-05 (ORM side): M18 table metadata against the frozen contract sets. Hermetic.

The database side (introspection of a migrated scratch database) is in
`tests/integration/test_returns_schema.py`.
"""

import re

import pytest

import ecommerce_search.models  # noqa: F401
from ecommerce_search.db.base import Base
from ecommerce_search.models import returns as mr
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import grants
from returns_contract_sets import (
    FORBIDDEN_FIELD_WORDS,
    FORBIDDEN_WORD_EXCEPTIONS,
    M18_TABLES,
    STORAGE_COLUMNS,
)


def _check(table: str, suffix: str) -> str:
    return next(
        str(c.sqltext)
        for c in Base.metadata.tables[table].constraints
        if str(getattr(c, "name", "")) == f"ck_{table}_{suffix}"
    )


def test_m18_tables_are_registered():
    assert set(mr.RETURN_TABLES) == M18_TABLES
    assert set(Base.metadata.tables) >= M18_TABLES
    assert set(grants.M18_TABLES) == M18_TABLES


@pytest.mark.parametrize("table", sorted(STORAGE_COLUMNS))
def test_each_table_has_exactly_the_contract_columns(table):
    assert {c.name for c in Base.metadata.tables[table].columns} == STORAGE_COLUMNS[table]


def test_no_column_is_a_decision_outcome_or_hypothesis():
    for table in M18_TABLES:
        for column in Base.metadata.tables[table].columns:
            if column.name in FORBIDDEN_WORD_EXCEPTIONS:
                continue
            assert not set(column.name.split("_")) & FORBIDDEN_FIELD_WORDS, (table, column.name)


def test_raw_bytes_exist_only_in_the_restricted_contents_table():
    binary = {
        (t.name, c.name)
        for t in Base.metadata.tables.values()
        for c in t.columns
        if t.name in M18_TABLES and c.type.python_type is bytes
    }
    assert binary == {("return_import_file_contents", "raw_bytes")}


@pytest.mark.parametrize(
    ("table", "suffix", "enum_cls"),
    [
        ("return_datasets", "source_type_valid", rc.SourceType),
        ("orders", "currency_valid", rc.Currency),
        ("shipments", "status_valid", rc.ShipmentStatus),
        ("delivery_events", "event_type_valid", rc.DeliveryEventType),
        ("delivery_events", "source_valid", rc.DeliveryEventSource),
        ("returns", "status_valid", rc.ReturnStatus),
        ("returns", "customer_reason_code_valid", rc.ReasonCode),
        ("inspection_observations", "condition_valid", rc.InspectionCondition),
        ("support_complaints", "channel_valid", rc.ComplaintChannel),
        ("support_complaints", "customer_topic_valid", rc.ComplaintTopic),
        ("correction_cases", "focus_area_valid", rc.CorrectionFocusArea),
        ("correction_cases", "status_valid", rc.CorrectionCaseStatus),
        ("correction_case_evidence", "evidence_type_valid", rc.EvidenceType),
        ("return_import_files", "entity_valid", rc.Entity),
    ],
)
def test_check_constraints_use_the_contract_values(table, suffix, enum_cls):
    assert set(re.findall(r"'([^']+)'", _check(table, suffix))) == {m.value for m in enum_cls}


def test_file_names_are_the_canonical_allowlist():
    names = set(re.findall(r"'([^']+)'", _check("return_import_files", "name_valid")))
    assert names == {"bundle.json", "manifest.json"} | {f"{e.value}.csv" for e in rc.Entity}


def test_record_id_checks_use_the_contract_patterns():
    assert _check("orders", "order_id_format") == (
        "order_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'"
    )
    assert _check("orders", "customer_ref_format") == (
        "customer_ref ~ '^SYN-(CUST|INSP|STAFF)-[0-9]{5}$'"
    )
    assert _check("return_datasets", "dataset_id_format") == (
        "dataset_id ~ '^[a-z0-9][a-z0-9-]{0,47}$'"
    )


def test_evidence_key_length_is_152_and_every_other_key_is_64():
    for entity in rc.Entity:
        expected = 152 if entity is rc.Entity.CORRECTION_CASE_EVIDENCE else 64
        assert _check(entity.value, "source_record_id_length") == (
            f"char_length(source_record_id) BETWEEN 1 AND {expected}"
        )


def test_batch_counts_cover_every_entity():
    for entity in rc.Entity:
        assert f"{entity.value}_new" in _check("return_import_batches", "new_total_positive")
