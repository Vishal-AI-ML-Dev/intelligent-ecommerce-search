"""M18-03B, without a database: the Phase B rules (`check_stored`) over a stored-record view,
the `ImportReport` shape, the dataset-version lock key, the importer label, and the paths of the
import service that must not touch the database. Every record is SYNTHETIC."""

import copy
import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ecommerce_search.config import Settings
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.labels import is_valid_importer_label
from ecommerce_search.returns.service import (
    EntityCounts,
    ImportReport,
    commit_import,
    import_lock_key,
    validate_import,
)
from ecommerce_search.returns.validation import check_stored
from returns_samples import RECORDS
from returns_uploads import CHECKED_AT, json_parts, sample_records, validate

E = rc.Entity


def rec(entity: str, **over) -> dict:
    return copy.deepcopy(RECORDS[entity]) | over


def records_of(records: dict[str, list[dict]]):
    result = validate(json_parts(records))
    assert result.report.errors == (), result.report.errors
    return result.records


def stored_view(records: dict[str, list[dict]] | None = None):
    """Stored records by entity and ID; contract models carry every attribute the rules read."""
    validated = records_of(sample_records() if records is None else records)
    stored = {e: {} for e in E}
    for entity, rows in validated.items():
        stored[entity] = {v.record.source_record_id(): v.record for v in rows}
    return stored


def phase_b(bundle: dict[str, list[dict]], stored=None) -> list[tuple[str, str, str | None]]:
    errors = check_stored(
        records_of(bundle), stored_view() if stored is None else stored, checked_at=CHECKED_AT
    )
    return sorted((e.entity, e.code, e.field) for e in errors)


# ---- references ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "over", "field"),
    [
        ("order_items", {"order_item_id": "SYN-OI-9", "order_id": "SYN-ORD-9"}, "order_id"),
        ("shipments", {"shipment_id": "SYN-SHP-9", "order_id": "SYN-ORD-9"}, "order_id"),
        ("delivery_events", {"event_id": "SYN-DEV-9", "shipment_id": "SYN-SHP-9"}, "shipment_id"),
        ("returns", {"return_id": "SYN-RET-9", "order_item_id": "SYN-OI-9"}, "order_item_id"),
        ("return_comments", {"comment_id": "SYN-RCM-9", "return_id": "SYN-RET-9"}, "return_id"),
        (
            "inspection_observations",
            {"observation_id": "SYN-INS-9", "return_id": "SYN-RET-9"},
            "return_id",
        ),
        (
            "customer_reviews",
            {"review_id": "SYN-REV-9", "order_item_id": "SYN-OI-9"},
            "order_item_id",
        ),
        (
            "support_complaints",
            {"complaint_id": "SYN-CMP-9", "order_id": "SYN-ORD-9", "order_item_id": None},
            "order_id",
        ),
        (
            "support_complaints",
            {"complaint_id": "SYN-CMP-9", "order_id": None, "order_item_id": "SYN-OI-9"},
            "order_item_id",
        ),
        ("correction_case_evidence", {"case_id": "SYN-CASE-9"}, "case_id"),
        ("correction_case_evidence", {"evidence_id": "SYN-RET-9"}, "evidence_id"),
    ],
)
def test_a_reference_found_neither_in_the_bundle_nor_stored_is_unknown(entity, over, field):
    assert phase_b({entity: [rec(entity, **over)]}) == [(entity, "unknown_reference", field)]


def test_references_resolve_against_stored_records_and_the_bundle():
    bundle = {
        "returns": [rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)],
        "return_comments": [rec("return_comments", comment_id="SYN-RCM-2", return_id="SYN-RET-2")],
    }
    item = rec("order_items", quantity=2)
    stored = stored_view(sample_records(order_items=[item]))
    assert phase_b(bundle, stored) == []


def test_an_evidence_target_is_looked_up_in_the_table_of_its_type():
    # SYN-RET-00001 exists, but not as a shipment.
    ev = rec("correction_case_evidence", evidence_type="shipment")
    assert phase_b({"correction_case_evidence": [ev]}) == [
        ("correction_case_evidence", "unknown_reference", "evidence_id")
    ]


# ---- shipments and quantities --------------------------------------------------------------


def test_a_second_shipment_for_a_stored_order_is_a_duplicate():
    shipment = rec("shipments", shipment_id="SYN-SHP-2")
    assert phase_b({"shipments": [shipment]}) == [("shipments", "duplicate_shipment", "order_id")]


def test_repeating_the_stored_shipment_is_not_a_duplicate():
    assert phase_b({"shipments": [rec("shipments")]}) == []


def test_stored_non_cancelled_quantity_counts_towards_the_item_quantity():
    ret = rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)
    assert phase_b({"returns": [ret]}) == [("returns", "quantity_exceeded", "quantity")]


def test_a_cancelled_stored_return_does_not_count_and_a_repeated_one_is_not_counted_twice():
    cancelled = rec("returns", status="cancelled", received_at=None)
    stored = stored_view(
        sample_records(returns=[cancelled], return_comments=[], inspection_observations=[])
        | {"correction_case_evidence": []}
    )
    new = rec("returns", return_id="SYN-RET-2", status="requested", received_at=None)
    assert phase_b({"returns": [new]}, stored) == []  # T8: cancelled plus new is allowed
    assert phase_b({"returns": [rec("returns")]}) == []  # the stored return, unchanged


def test_quantity_uses_the_stored_item_quantity_not_the_phase_a_maximum():
    stored = stored_view(sample_records(order_items=[rec("order_items", quantity=2)]))
    two = [
        rec("returns", return_id=f"SYN-RET-{n}", status="requested", received_at=None)
        for n in (2, 3)
    ]
    assert phase_b({"returns": two}, stored) == [("returns", "quantity_exceeded", "quantity")]


# ---- time order and inspections -------------------------------------------------------------


def test_time_rules_use_stored_parents():
    early = "2026-01-01T00:00:00+05:30"  # before the stored order
    bundle = {
        "shipments": [],
        "returns": [
            rec("returns", return_id="SYN-RET-2", requested_at=early, status="cancelled")
            | {"received_at": None}
        ],
        "inspection_observations": [
            rec("inspection_observations", observation_id="SYN-INS-2", observed_at=early)
        ],
    }
    assert phase_b(bundle) == [
        ("inspection_observations", "temporal_order", "observed_at"),
        ("returns", "temporal_order", "requested_at"),
    ]


def test_an_inspection_on_a_stored_return_that_was_not_received_is_rejected():
    requested = rec("returns", status="requested", received_at=None)
    stored = stored_view(
        sample_records(returns=[requested], inspection_observations=[])
        | {"correction_case_evidence": []}
    )
    obs = rec("inspection_observations", observation_id="SYN-INS-2")
    assert phase_b({"inspection_observations": [obs]}, stored) == [
        ("inspection_observations", "inconsistent_reference", "return_id")
    ]


# ---- product consistency and evidence relations --------------------------------------------


def test_a_review_must_name_the_product_of_its_stored_item():
    review = rec("customer_reviews", review_id="SYN-REV-2", product_id="SYN-LAP-0001")
    assert phase_b({"customer_reviews": [review]}) == [
        ("customer_reviews", "inconsistent_reference", "product_id")
    ]


def test_an_order_and_product_complaint_without_an_item_needs_the_product_in_the_order():
    def complaint(product):
        return rec(
            "support_complaints", complaint_id="SYN-CMP-2", order_item_id=None, product_id=product
        )

    assert phase_b({"support_complaints": [complaint("SYN-SHO-0001")]}) == []
    assert phase_b({"support_complaints": [complaint("SYN-LAP-0001")]}) == [
        ("support_complaints", "inconsistent_reference", "product_id")
    ]


@pytest.mark.parametrize("kind", ["shipment", "delivery_event"])
def test_shipment_evidence_relates_through_the_stored_items_of_its_order(kind):
    target = "SYN-SHP-00001" if kind == "shipment" else "SYN-DEV-00001"
    ev = rec("correction_case_evidence", evidence_type=kind, evidence_id=target)
    assert phase_b({"correction_case_evidence": [ev]}) == []
    other_case = rec("correction_cases", case_id="SYN-CASE-2", product_id="SYN-LAP-0001")
    ev = ev | {"case_id": "SYN-CASE-2"}
    assert phase_b({"correction_cases": [other_case], "correction_case_evidence": [ev]}) == [
        ("correction_case_evidence", "inconsistent_reference", "evidence_id")
    ]


def test_an_order_only_complaint_relates_only_when_its_order_contains_the_product():
    order_only = rec(
        "support_complaints", complaint_id="SYN-CMP-2", order_item_id=None, product_id=None
    )
    ev = rec("correction_case_evidence", evidence_type="support_complaint", evidence_id="SYN-CMP-2")
    assert phase_b({"support_complaints": [order_only], "correction_case_evidence": [ev]}) == []
    other_case = rec("correction_cases", case_id="SYN-CASE-2", product_id="SYN-LAP-0001")
    ev = ev | {"case_id": "SYN-CASE-2"}
    bundle = {
        "support_complaints": [order_only],
        "correction_cases": [other_case],
        "correction_case_evidence": [ev],
    }
    assert phase_b(bundle) == [
        ("correction_case_evidence", "inconsistent_reference", "evidence_id")
    ]


def test_phase_a_still_defers_shipment_and_order_only_evidence():
    case = rec("correction_cases", product_id="SYN-LAP-0001")
    ev = rec("correction_case_evidence", evidence_type="shipment", evidence_id="SYN-SHP-00001")
    result = validate(
        json_parts(sample_records(correction_cases=[case], correction_case_evidence=[ev]))
    )
    assert result.report.errors == ()


def test_conflicting_evidence_against_stored_records_is_accepted():
    # A "damaged" comment and a carrier damage event next to a stored `as_new` inspection.
    bundle = {
        "return_comments": [
            rec("return_comments", comment_id="SYN-RCM-2", text="Arrived damaged, box crushed.")
        ],
        "delivery_events": [
            rec("delivery_events", event_id="SYN-DEV-2", event_type="damage_reported_by_carrier")
        ],
        "customer_reviews": [rec("customer_reviews", review_id="SYN-REV-2", rating=5)],
    }
    assert phase_b(bundle) == []


def test_stored_records_need_only_the_attributes_the_rules_read():
    stored = {e: {} for e in E}
    stored[E.ORDERS]["SYN-ORD-00001"] = SimpleNamespace(
        order_id="SYN-ORD-00001", ordered_at=rc.Order.model_validate(RECORDS["orders"]).ordered_at
    )
    item = rec("order_items")
    assert phase_b({"order_items": [item]}, stored) == []


# ---- report, lock key, label ---------------------------------------------------------------


def test_the_import_report_has_exactly_the_contract_fields():
    assert set(ImportReport.model_fields) == {
        "contract_version",
        "mode",
        "status",
        "bundle_sha256",
        "batch_id",
        "checked_at",
        "counts",
        "phases_completed",
        "error_count",
        "error_count_is_lower_bound",
        "errors_truncated",
        "errors",
    }
    assert set(EntityCounts.model_fields) == {"received", "new", "unchanged"}


def test_the_lock_key_is_deterministic_and_separates_dataset_versions():
    key = import_lock_key("synthetic-returns", 1)
    assert key == import_lock_key("synthetic-returns", 1)
    assert -(2**63) <= key < 2**63
    assert len({key, import_lock_key("synthetic-returns", 2), import_lock_key("other", 1)}) == 3


@pytest.mark.parametrize("label", ["cli", "deploy-2026-10:a", "local.importer_1"])
def test_valid_importer_labels(label):
    assert is_valid_importer_label(label)


@pytest.mark.parametrize(
    "label",
    ["", " cli", "a" * 65, "user@example.com", "importer 1", "9000000001", "560001", None, 7],
)
def test_invalid_importer_labels(label):
    assert not is_valid_importer_label(label)


FAKE_PW = "test-password-not-real"


def _settings(**values):
    return Settings(postgres_password=FAKE_PW, **values)


def test_the_label_setting_is_optional_validated_and_never_echoed():
    assert _settings().returns_importer_label is None
    assert _settings(returns_importer_label="").returns_importer_label is None
    assert _settings(returns_importer_label="deploy-1").returns_importer_label == "deploy-1"
    with pytest.raises(ValidationError) as caught:
        _settings(returns_importer_label="ops@example.com")
    assert [e["loc"] for e in caught.value.errors()] == [("returns_importer_label",)]
    assert "ops@example.com" not in str(caught.value)


# ---- paths that never reach the database ----------------------------------------------------


class NoDatabase:
    """An engine stand-in that fails the test if anything uses it."""

    def __getattr__(self, name):
        raise AssertionError(f"the database was used ({name})")


def test_a_phase_a_failure_is_reported_without_touching_the_database():
    bad = sample_records(orders=[rec("orders", currency="USD")])
    for report in (
        validate_import(NoDatabase(), json_parts(bad), checked_at=CHECKED_AT),
        commit_import(
            NoDatabase(), json_parts(bad), imported_by_label="cli", checked_at=CHECKED_AT
        ),
    ):
        assert report.status == "invalid"
        assert [e.code for e in report.errors] == ["enum"]
        assert report.phases_completed == ("envelope", "parsing", "fields", "bundle_structure")
        assert report.error_count_is_lower_bound is True
        assert report.batch_id is None
        assert report.counts["orders"] == EntityCounts(received=1)


@pytest.mark.parametrize("label", ["", "user@example.com", None])
def test_commit_refuses_an_invalid_label_before_anything_else(label):
    with pytest.raises(ValueError, match="imported_by_label"):
        commit_import(NoDatabase(), json_parts(), imported_by_label=label)


def test_the_report_serializes_checked_at_in_canonical_form():
    bad = sample_records(orders=[rec("orders", currency="USD")])
    report = validate_import(NoDatabase(), json_parts(bad), checked_at=CHECKED_AT)
    assert report.model_dump(mode="json")["checked_at"] == "2026-10-25T12:00:00.000000Z"
    assert report.mode == "validate"


# ---- AC-13 (5): the declared service surface ------------------------------------------------

SERVICE_SURFACE = {
    "EntityCounts",
    "ImportReport",
    "ImportRoleError",
    "ImportStorageError",
    "commit_import",
    "import_lock_key",
    "require_importer_role",
    "validate_import",
}
RETURNS_MODULES = (
    "bundle",
    "canonical",
    "cli",
    "contracts",
    "errors",
    "grants",
    "labels",
    "redaction",
    "service",
    "validation",
    "versions",
)
# No route or code path refunds, orders, bans, sanctions or infers fraud or responsibility.
FORBIDDEN_NAME = re.compile(
    r"refund|ban(?![a-z])|sanction|approv|reject|fraud|penal|suspend|blacklist|blame|responsib",
    re.IGNORECASE,
)


def _public_callables(module) -> set[str]:
    return {
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == module.__name__
    }


def test_the_service_surface_equals_the_declared_set_exactly():
    from ecommerce_search.returns import service

    assert set(service.__all__) == SERVICE_SURFACE
    assert len(service.__all__) == len(SERVICE_SURFACE)
    assert _public_callables(service) == SERVICE_SURFACE


def test_the_cli_reaches_the_domain_only_through_the_two_entry_points():
    from ecommerce_search.returns import cli

    used = {name for name in SERVICE_SURFACE if hasattr(cli, name)}
    assert used == {
        "ImportReport",
        "ImportRoleError",
        "ImportStorageError",
        "commit_import",
        "validate_import",
    }


def test_no_returns_module_exposes_a_decision_or_sanction_operation():
    import importlib

    for name in RETURNS_MODULES:
        module = importlib.import_module(f"ecommerce_search.returns.{name}")
        offending = sorted(n for n in _public_callables(module) if FORBIDDEN_NAME.search(n))
        assert offending == [], name
