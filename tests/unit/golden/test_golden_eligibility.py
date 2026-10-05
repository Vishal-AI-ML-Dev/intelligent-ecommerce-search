"""Deterministic constraint eligibility (Session C1). Uses the committed seed catalog plus
SYNTHETIC hand-made products for missing-value cases; no relevance label is involved."""

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.eligibility import check_constraint, evaluate, hard_constraints, summarize
from evals.contracts.models import (
    ConstraintStatus,
    EligibilityRecord,
    GoldField,
    Hardness,
    OverallStatus,
)
from golden_support import gold

from ecommerce_search.filtering.policy import FilterOperator

S, V, U, NA = (
    ConstraintStatus.SATISFIED,
    ConstraintStatus.VIOLATED,
    ConstraintStatus.UNKNOWN,
    ConstraintStatus.NOT_APPLICABLE,
)
LTE, GTE = FilterOperator.LTE, FilterOperator.GTE


def _first(catalog, **facts):
    for product_id in sorted(catalog.products):
        product = catalog.products[product_id]
        spec = dict(product.spec)
        if all(
            getattr(product, k, None) == v
            if k in ("category", "brand", "subcategory")
            else spec.get(k) == v
            for k, v in facts.items()
        ):
            return product
    raise AssertionError(f"no catalog product with {facts}")


def _status(field, value, product, operator=FilterOperator.EQ):
    return check_constraint(gold("gq-p01", field, value, operator=operator), product)


def test_category_and_brand_are_exact(catalog):
    lap = catalog.products["SYN-LAP-0003"]
    assert _status(GoldField.CATEGORY, "laptop", lap) is S
    assert _status(GoldField.CATEGORY, "phone", lap) is V
    assert _status(GoldField.BRAND, "Lenovo", lap) is S
    assert _status(GoldField.BRAND, "Dell", lap) is V


def test_price_bounds_are_inclusive(catalog):
    lap = catalog.products["SYN-LAP-0003"]
    price = f"{lap.price:.2f}"
    assert _status(GoldField.PRICE, price, lap, LTE) is S
    assert _status(GoldField.PRICE, price, lap, GTE) is S
    below = f"{lap.price - Decimal('0.01'):.2f}"
    above = f"{lap.price + Decimal('0.01'):.2f}"
    assert _status(GoldField.PRICE, below, lap, LTE) is V
    assert _status(GoldField.PRICE, above, lap, GTE) is V


def test_spec_fields_satisfied_violated_and_not_applicable(catalog):
    lap = catalog.products["SYN-LAP-0003"]  # AMD Ryzen 7, 16 GB, 1 TB SSD
    shoe = _first(catalog, category="shoes")
    assert _status(GoldField.PROCESSOR, "AMD Ryzen 7", lap) is S
    assert _status(GoldField.PROCESSOR, "Intel Core i5", lap) is V
    assert _status(GoldField.RAM_GB, lap.spec["ram_gb"], lap) is S
    assert _status(GoldField.RAM_GB, 64, lap) is V
    assert _status(GoldField.RAM_GB, 8, shoe) is NA
    assert _status(GoldField.ANC, True, lap) is NA
    assert _status(GoldField.GENDER, "women", lap) is NA


def test_nvme_is_the_storage_interface_field_and_implies_nothing(catalog):
    nvme = _first(catalog, category="laptop", storage_interface="NVME")
    sata_ssd = _first(catalog, category="laptop", storage_interface="SATA", storage_type="SSD")
    assert _status(GoldField.STORAGE_INTERFACE, "NVME", nvme) is S
    assert _status(GoldField.STORAGE_INTERFACE, "NVME", sata_ssd) is V
    record = evaluate("gq-p08", [gold("gq-p08", GoldField.STORAGE_INTERFACE, "NVME")], sata_ssd)
    assert [c.field for c in record.checks] == [GoldField.STORAGE_INTERFACE]


def test_unisex_does_not_satisfy_a_strict_women_constraint(catalog):
    unisex = _first(catalog, category="shoes", gender="unisex")
    women = _first(catalog, category="shoes", gender="women")
    assert _status(GoldField.GENDER, "women", unisex) is V
    assert _status(GoldField.GENDER, "women", women) is S


def test_missing_values_are_unknown_never_satisfied(catalog):
    # SYNTHETIC products: the committed seed has no missing value for these fields.
    lap = catalog.products["SYN-LAP-0003"]
    no_ram = replace(lap, spec=MappingProxyType({**lap.spec, "ram_gb": None}))
    no_sub = replace(lap, subcategory=None)
    assert _status(GoldField.RAM_GB, 16, no_ram) is U
    assert _status(GoldField.SUBCATEGORY, "business", no_sub) is U
    record = evaluate(
        "gq-p01",
        [gold("gq-p01", GoldField.CATEGORY, "laptop"), gold("gq-p01", GoldField.RAM_GB, 16)],
        no_ram,
    )
    assert record.hard_overall is OverallStatus.UNKNOWN
    assert record.fp1_overall is OverallStatus.UNKNOWN


def test_subcategory_of_another_category_is_not_applicable(catalog):
    lap = catalog.products["SYN-LAP-0003"]
    running = _first(catalog, category="shoes", subcategory="running")
    assert _status(GoldField.SUBCATEGORY, "running", lap) is NA
    assert _status(GoldField.SUBCATEGORY, "running", running) is S
    assert _status(GoldField.SUBCATEGORY, "casual", running) is V


def test_overall_status_and_fp1_status_are_separate(catalog):
    lap = catalog.products["SYN-LAP-0003"]
    constraints = [
        gold("gq-p04", GoldField.CATEGORY, "laptop"),
        gold("gq-p04", GoldField.PROCESSOR, "Intel Core i5"),
    ]
    record = evaluate("gq-p04", constraints, lap)
    assert record.hard_overall is OverallStatus.VIOLATED
    assert record.fp1_overall is OverallStatus.SATISFIED  # processor is not an fp-1 field
    shoe = _first(catalog, category="shoes")
    on_shoe = evaluate("gq-p01", [gold("gq-p01", GoldField.RAM_GB, 8)], shoe)
    assert on_shoe.checks[0].status is NA and on_shoe.hard_overall is OverallStatus.VIOLATED
    assert evaluate("gq-p20", [], lap).hard_overall is OverallStatus.NO_CONSTRAINTS


def test_soft_signals_never_take_part(catalog):
    lap = catalog.products["SYN-LAP-0003"]
    soft = [
        gold("gq-p11", GoldField.CATEGORY, "laptop"),
        gold("gq-p11", GoldField.SEMANTIC_INTENT, "gaming", hardness=Hardness.SOFT),
        gold("gq-p11", GoldField.BRAND, "Dell", hardness=Hardness.SOFT),
    ]
    assert [c.field for c in hard_constraints("gq-p11", soft)] == [GoldField.CATEGORY]
    record = evaluate("gq-p11", soft, lap)
    assert [c.field for c in record.checks] == [GoldField.CATEGORY]
    assert record.hard_overall is OverallStatus.SATISFIED
    with pytest.raises(ContractError, match="only hard"):
        check_constraint(soft[1], lap)
    with pytest.raises(ContractError, match="only hard"):
        check_constraint(soft[2], lap)


def test_eligibility_records_carry_no_relevance_label():
    fields = set(EligibilityRecord.model_fields)
    assert not fields & {"label", "relation", "confidence", "relevant", "annotator_handle"}


def test_summary_counts_unknown_and_not_applicable_separately(catalog):
    p12 = [
        gold("gq-p12", GoldField.CATEGORY, "shoes"),
        gold("gq-p12", GoldField.SUBCATEGORY, "running"),
        gold("gq-p12", GoldField.GENDER, "women"),
    ]
    summary = summarize("gq-p12", p12, catalog)
    assert len(summary.satisfied) == 6
    assert summary.unknown == ()
    assert summary.violated_count == len(catalog.products) - 6
    assert summary.not_applicable_count == len(catalog.products) - 50  # every non-shoe product
    assert summary.unknown_check_count == 0


def test_p04_has_exactly_one_hard_eligible_product(catalog):
    p04 = [
        gold("gq-p04", GoldField.CATEGORY, "laptop"),
        gold("gq-p04", GoldField.BRAND, "Lenovo"),
        gold("gq-p04", GoldField.PROCESSOR, "AMD Ryzen 7"),
    ]
    assert summarize("gq-p04", p04, catalog).satisfied == ("SYN-LAP-0003",)
    lumen = [p for p in catalog.products.values() if "Lumen" in p.title]
    assert len(lumen) == 4  # the other three Lumen laptops are not hard-eligible


def test_zero_eligible_and_constraint_free_queries(catalog):
    p19 = [gold("gq-p19", GoldField.CATEGORY, "laptop"), gold("gq-p19", GoldField.RAM_GB, 64)]
    assert summarize("gq-p19", p19, catalog).satisfied == ()
    none = summarize("gq-p20", [], catalog)
    assert none.hard_constraint_count == 0 and none.satisfied == () and none.unknown == ()
    assert none.violated_count == 0
