"""M18-03C: the synthetic release files and their generator, without a database.

Covers AC-14 (deterministic generator, release checksums, `scenarios.json` refused as an import
part) and the contracts §8 dataset design: counts, catalog links, references, scenario
coverage and the text overlays. `scenarios.json` is read only to check coverage; its entries
are generator intent, not labels.
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ecommerce_search.ingestion.loader import load_catalog
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.redaction import matches_any
from ecommerce_search.returns.validation import check_stored, validate_upload
from returns_release import (
    CSV_DIR,
    RELEASE,
    ROOT,
    bundle,
    csv_parts,
    generator,
    json_parts,
    provenance,
    scenarios,
)

CHECKED_AT = datetime(2026, 10, 26, tzinfo=UTC)
E = rc.Entity
EXPECTED_COUNTS = {
    "orders": 600,
    "order_items": 900,
    "shipments": 600,
    "delivery_events": 2365,
    "returns": 180,
    "return_comments": 161,
    "inspection_observations": 145,
    "customer_reviews": 150,
    "support_complaints": 60,
    "correction_cases": 20,
    "correction_case_evidence": 132,
}
APPROXIMATE = {  # contracts §8 "about" sizes. The 15% band is an M18-03C test choice, not an
    # approved contract rule; the exact generated values are pinned above.
    "delivery_events": 2400,
    "return_comments": 150,
    "inspection_observations": 140,
    "correction_case_evidence": 120,
}
CATEGORY_INTENTS = {
    "sizing/fit",
    "expectation mismatch",
    "colour/image mismatch",
    "incorrect specification",
    "product-quality defect",
    "damaged product",
    "wrong item delivered",
    "logistics delay",
    "manufacturing defect",
    "change of mind",
    "suspected abuse (investigation only)",
    "insufficient evidence",
}
SHAPES = {"consistent", "missing_evidence", "conflicting", "noisy_text"}
PREFIXES = {
    "orders": ("order_id", "SYN-ORD-"),
    "order_items": ("order_item_id", "SYN-OI-"),
    "shipments": ("shipment_id", "SYN-SHP-"),
    "delivery_events": ("event_id", "SYN-DEV-"),
    "returns": ("return_id", "SYN-RET-"),
    "return_comments": ("comment_id", "SYN-RCM-"),
    "inspection_observations": ("observation_id", "SYN-INS-"),
    "customer_reviews": ("review_id", "SYN-REV-"),
    "support_complaints": ("complaint_id", "SYN-CMP-"),
    "correction_cases": ("case_id", "SYN-CASE-"),
}
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def rendered() -> dict[str, bytes]:
    return generator().render()


@pytest.fixture(scope="module")
def data() -> dict:
    return bundle()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return scenarios()


@pytest.fixture(scope="module")
def catalog() -> dict:
    lines = load_catalog(ROOT / "data" / "seed" / "catalog_seed_v1.jsonl").lines
    return {line.record.product_id: line for line in lines}


@pytest.fixture(scope="module")
def json_result():
    result = validate_upload(json_parts(), checked_at=CHECKED_AT)
    assert result.report.error_count == 0
    return result


@pytest.fixture(scope="module")
def csv_result():
    result = validate_upload(csv_parts(), checked_at=CHECKED_AT)
    assert result.report.error_count == 0
    return result


def by_id(data: dict, entity: str) -> dict[str, dict]:
    key = PREFIXES[entity][0]
    return {r[key]: r for r in data[entity]}


def driver_returns(manifest: dict) -> dict[str, list[str]]:
    """scenario_id -> return IDs, for the three return drivers of §8."""
    drivers = ("category_evidence_shape", "sku_hotspot", "plain_control")
    return {
        s["scenario_id"]: s["record_ids"].get("returns", [])
        for s in manifest["scenarios"]
        if s["family"] in drivers
    }


# ---- reproducibility and checksums (AC-14) -----------------------------------------------


def test_generation_is_byte_identical_and_equals_the_release_files(rendered):
    assert generator().render() == rendered
    on_disk = {p.relative_to(RELEASE).as_posix() for p in RELEASE.rglob("*") if p.is_file()}
    assert on_disk == set(rendered)
    for path, data in rendered.items():
        assert (RELEASE / path).read_bytes() == data, path


def test_check_mode_reproduces_every_checksum(capsys):
    assert generator().main(["--check"]) == 0
    assert provenance()["records_sha256"] in capsys.readouterr().out


def test_the_release_files_use_lf_line_ends_only():
    for path in RELEASE.rglob("*"):
        if path.is_file():
            assert b"\r" not in path.read_bytes(), path.name
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "data/seed/returns_v1/** text eol=lf" in attributes


def test_provenance_records_every_release_checksum(json_result, csv_result):
    prov = provenance()
    files = {p.relative_to(RELEASE).as_posix() for p in RELEASE.rglob("*") if p.is_file()}
    assert set(prov["files"]) == files - {"provenance.json"}
    for path, sha in prov["files"].items():
        assert hashlib.sha256((RELEASE / path).read_bytes()).hexdigest() == sha, path
    assert prov["bundle_sha256"] == {
        "json": json_result.report.bundle_sha256,
        "csv": csv_result.report.bundle_sha256,
    }
    assert prov["descriptor_sha256"] == json_result.dataset.descriptor_sha256()
    assert prov["record_counts"] == EXPECTED_COUNTS
    assert prov["total_records"] == sum(EXPECTED_COUNTS.values()) == 5313
    assert prov["is_synthetic"] is True
    assert prov["label_status"] == "generator_intent_not_label"


def _content(result) -> dict[str, list[tuple[str, str]]]:
    return {
        e.value: sorted(
            (v.record.source_record_id(), v.record.record_sha256()) for v in result.records[e]
        )
        for e in E
    }


def test_json_and_csv_forms_are_canonically_equivalent(json_result, csv_result):
    assert _content(json_result) == _content(csv_result)
    assert json_result.dataset == csv_result.dataset
    expected = provenance()["records_sha256"]
    for result in (json_result, csv_result):
        lines = [(e, rid, sha) for e, rows in _content(result).items() for rid, sha in rows]
        assert cf.records_sha256(lines) == expected
    assert json_result.report.bundle_sha256 != csv_result.report.bundle_sha256


def test_the_release_is_within_the_import_limits(json_result, csv_result):
    assert len(json_parts()[0].data) < 2 * 1024 * 1024  # contracts §8: under 2 MiB
    for result in (json_result, csv_result):
        assert sum(f.size_bytes for f in result.files) <= 8 * 1024 * 1024
        assert all(f.size_bytes <= 5 * 1024 * 1024 for f in result.files)
    assert max(EXPECTED_COUNTS.values()) <= 5000 and sum(EXPECTED_COUNTS.values()) <= 10_000


# ---- references and structure ------------------------------------------------------------


def test_every_reference_resolves_and_every_structural_rule_holds(json_result):
    """With nothing stored, `check_stored` sees the whole release: references, time order,
    one shipment per order, the quantity rule and every evidence-to-product relation."""
    empty = {e: {} for e in E}
    assert check_stored(json_result.records, empty, checked_at=CHECKED_AT) == []


def test_record_counts_follow_the_approved_design(data):
    counts = {e.value: len(data[e.value]) for e in E}
    assert counts == EXPECTED_COUNTS
    for entity, about in APPROXIMATE.items():
        assert abs(counts[entity] - about) <= about * 0.15, entity


def test_every_record_is_marked_synthetic_and_uses_the_synthetic_prefixes(data):
    descriptor = data["dataset"]
    assert descriptor["source_type"] == "synthetic" and descriptor["is_synthetic"] is True
    assert (descriptor["dataset_id"], descriptor["dataset_version"]) == ("synthetic-returns", 1)
    assert descriptor["generator"] == "scripts/generate_returns_dataset.py@1"
    for e in E:
        assert all(r["is_synthetic"] is True for r in data[e.value]), e
    for entity, (key, prefix) in PREFIXES.items():
        assert all(r[key].startswith(prefix) for r in data[entity]), entity


def test_catalog_links_use_actual_products_raw_hashes_and_prices(data, catalog):
    for entity in ("order_items", "correction_cases"):
        for r in data[entity]:
            line = catalog[r["product_id"]]
            assert (r["catalog_dataset_id"], r["catalog_dataset_version"]) == (
                "synthetic-seed",
                "1",
            )
            assert r["catalog_raw_sha256"] == line.raw_sha256
    for r in data["order_items"]:
        assert r["unit_price"] == cf.format_money(catalog[r["product_id"]].record.price)
    for entity in ("customer_reviews", "support_complaints"):
        assert {r["product_id"] for r in data[entity] if r["product_id"]} <= set(catalog)
    items_per_product = Counter(r["product_id"] for r in data["order_items"])
    assert set(items_per_product) == set(catalog)
    assert min(items_per_product.values()) >= 2


def test_orders_customers_and_shipments(data):
    orders = data["orders"]
    assert len({o["customer_ref"] for o in orders}) == 400
    sizes = Counter(r["order_id"] for r in data["order_items"])
    assert set(sizes) == {o["order_id"] for o in orders}
    assert set(sizes.values()) == {1, 2, 3}
    assert sorted(s["order_id"] for s in data["shipments"]) == sorted(o["order_id"] for o in orders)
    assert {s["carrier_ref"] for s in data["shipments"]} == {
        "synthetic-carrier-a",
        "synthetic-carrier-b",
        "synthetic-carrier-c",
    }


def test_every_timestamp_is_inside_the_synthetic_window_at_ist(data):
    start = datetime(2026, 1, 5, tzinfo=IST)
    end = datetime(2026, 7, 1, tzinfo=IST)
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30")
    for e in E:
        model = rc.ENTITY_MODELS[e]
        for r in data[e.value]:
            for name in model.model_fields:
                value = r.get(name)
                if isinstance(value, str) and stamp.fullmatch(value):
                    assert start <= datetime.fromisoformat(value) < end, (e, name)


# ---- scenario coverage (contracts §8) ----------------------------------------------------


def test_every_return_belongs_to_exactly_one_driver_scenario(data, manifest):
    drivers = driver_returns(manifest)
    owners = Counter(rid for ids in drivers.values() for rid in ids)
    assert set(owners) == set(by_id(data, "returns"))
    assert set(owners.values()) == {1}
    families = Counter()
    for s in manifest["scenarios"]:
        if s["scenario_id"] in drivers:
            families[s["family"]] += len(s["record_ids"]["returns"])
    assert families == {"category_evidence_shape": 96, "sku_hotspot": 48, "plain_control": 36}


def test_category_scenarios_cover_12_categories_by_4_shapes_by_2_instances(manifest):
    cells = Counter(
        (s["generator_intent"], s["evidence_shape"])
        for s in manifest["scenarios"]
        if s["family"] == "category_evidence_shape"
    )
    assert set(cells) == {(c, s) for c in CATEGORY_INTENTS for s in SHAPES}
    assert set(cells.values()) == {2}


def test_scenario_entries_are_generator_intent_and_reference_existing_records(data, manifest):
    assert manifest["label_status"] == "generator_intent_not_label"
    ids = {
        e.value: {rc.ENTITY_MODELS[e].model_validate(r).source_record_id() for r in data[e.value]}
        for e in E
    }
    for s in manifest["scenarios"]:
        assert set(s) == {
            "scenario_id",
            "family",
            "generator_intent",
            "evidence_shape",
            "record_ids",
            "listing_truth",
            "label_status",
        }
        assert s["listing_truth"] == "unknown"
        assert s["label_status"] == "generator_intent_not_label"
        for entity, record_ids in s["record_ids"].items():
            assert set(record_ids) <= ids[entity], (s["scenario_id"], entity)


def _products_of_returns(data, return_ids):
    items = by_id(data, "order_items")
    returns = by_id(data, "returns")
    return [items[returns[r]["order_item_id"]]["product_id"] for r in return_ids]


def _intent_returns(manifest, intent):
    return [
        rid
        for s in manifest["scenarios"]
        if s["family"] in ("category_evidence_shape", "sku_hotspot")
        and s["generator_intent"] == intent
        for rid in s["record_ids"]["returns"]
    ]


@pytest.mark.parametrize(
    ("intent", "allowed"),
    [
        ("sizing/fit", {"SHO"}),
        ("incorrect specification", {"LAP", "PHN"}),
        ("colour/image mismatch", {"SHO", "HDP", "PHN"}),
    ],
)
def test_category_applicability(data, manifest, intent, allowed):
    products = _products_of_returns(data, _intent_returns(manifest, intent))
    assert products and {p.split("-")[1] for p in products} <= allowed


def test_six_sku_hotspots_of_eight_returns_each_on_products_with_20_items(data, manifest):
    hotspots = [s for s in manifest["scenarios"] if s["family"] == "sku_hotspot"]
    assert len(hotspots) == 6
    items_per_product = Counter(r["product_id"] for r in data["order_items"])
    kinds = Counter()
    for s in hotspots:
        products = set(_products_of_returns(data, s["record_ids"]["returns"]))
        assert len(s["record_ids"]["returns"]) == 8 and len(products) == 1
        (product,) = products
        assert items_per_product[product] >= 20
        assert len(s["record_ids"]["correction_cases"]) == 1
        kinds[(product.split("-")[1], s["generator_intent"])] += 1
    assert kinds == {
        ("SHO", "sizing/fit"): 2,
        ("LAP", "incorrect specification"): 1,
        ("HDP", "colour/image mismatch"): 1,
        ("PHN", "product-quality defect"): 1,
        ("HDP", "damaged product"): 1,
    }


def _evidence_by_return(data):
    comments, inspections = defaultdict(list), defaultdict(list)
    for c in data["return_comments"]:
        comments[c["return_id"]].append(c)
    for i in data["inspection_observations"]:
        inspections[i["return_id"]].append(i)
    return comments, inspections


def test_suspected_abuse_is_only_inspection_evidence_with_one_distinct_customer_each(
    data, manifest
):
    rids = _intent_returns(manifest, "suspected abuse (investigation only)")
    assert len(rids) == 8
    comments, inspections = _evidence_by_return(data)
    items = by_id(data, "order_items")
    returns = by_id(data, "returns")
    orders = by_id(data, "orders")
    customers = []
    for rid in rids:
        conditions = {i["condition"] for i in inspections[rid]}
        assert "item_differs_from_order" in conditions
        customers.append(orders[items[returns[rid]["order_item_id"]]["order_id"]]["customer_ref"])
    assert len(set(customers)) == 8
    orders_per_customer = Counter(o["customer_ref"] for o in data["orders"])
    assert all(orders_per_customer[c] == 1 for c in customers)
    complaints = {c["order_id"] for c in data["support_complaints"]}
    assert not {items[returns[r]["order_item_id"]]["order_id"] for r in rids} & complaints


def test_insufficient_evidence_returns_have_no_reason_comment_or_inspection(data, manifest):
    rids = _intent_returns(manifest, "insufficient evidence")
    assert len(rids) == 8
    comments, inspections = _evidence_by_return(data)
    returns = by_id(data, "returns")
    for rid in rids:
        assert returns[rid]["customer_reason_code"] == "NOT_PROVIDED"
        assert comments[rid] == [] and inspections[rid] == []


def test_cancelled_returns_exercise_the_quantity_rule(data):
    returns = data["returns"]
    cancelled = [r for r in returns if r["status"] == "cancelled"]
    assert len(cancelled) >= 12
    per_item = defaultdict(list)
    for r in returns:
        per_item[r["order_item_id"]].append(r["status"])
    chains = [s for s in per_item.values() if "cancelled" in s and len(s) > 1]
    assert len(chains) >= 6 and all(any(x != "cancelled" for x in s) for s in chains)
    items = by_id(data, "order_items")
    for item_id, statuses in per_item.items():
        live = sum(1 for s in statuses if s != "cancelled")
        assert live <= items[item_id]["quantity"]
    assert any(sum(s != "cancelled" for s in v) == 2 for v in per_item.values())


def _overlay(manifest, family):
    (s,) = [s for s in manifest["scenarios"] if s["scenario_id"] == family]
    assert s["family"] == "text_overlay"
    return s["record_ids"]["return_comments"]


def test_text_overlays_meet_the_minimums_on_existing_returns(data, manifest):
    comments = by_id(data, "return_comments")
    drivers = {rid for ids in driver_returns(manifest).values() for rid in ids}
    texts = {}
    for family, minimum in (
        ("overlay-allowlisted-personal-data", 10),
        ("overlay-prompt-injection", 6),
        ("overlay-markup", 4),
        ("overlay-near-limit", 4),
        ("overlay-devanagari", 4),
    ):
        ids = _overlay(manifest, family)
        assert len(ids) >= minimum, family
        assert {comments[c]["return_id"] for c in ids} <= drivers  # no extra returns
        texts[family] = [comments[c]["text"] for c in ids]
    assert all(matches_any(t) for t in texts["overlay-allowlisted-personal-data"])
    assert all("<" in t or "](" in t for t in texts["overlay-markup"])
    assert all(1990 <= len(t) <= 2000 for t in texts["overlay-near-limit"])
    assert sum(len(t) == 2000 for t in texts["overlay-near-limit"]) >= 2
    assert all(re.search("[ऀ-ॿ]", t) for t in texts["overlay-devanagari"])


# ---- scenarios stay separate from imported records ---------------------------------------


def test_scenario_intent_never_appears_in_imported_records(data, manifest):
    """Records carry no scenario ID, intent or label field or text. Only the dataset block's
    provenance notes name `scenarios.json`, to say that it is never imported."""
    scenario_ids = {s["scenario_id"] for s in manifest["scenarios"]}
    records = json.dumps({e.value: data[e.value] for e in E}, ensure_ascii=False)
    for token in ("scenario", "generator_intent", "label_status", "listing_truth", "intent"):
        assert token not in records
    for scenario_id in scenario_ids:
        assert scenario_id not in records
    for path in CSV_DIR.glob("*.csv"):
        assert b"scenario" not in path.read_bytes()


def test_scenarios_json_is_outside_both_import_directories():
    assert not (RELEASE / "bundle" / "scenarios.json").exists()
    assert not (CSV_DIR / "scenarios.json").exists()
    assert sorted(p.name for p in CSV_DIR.iterdir()) == sorted(
        ["manifest.json", *(f"{e.value}.csv" for e in E)]
    )


@pytest.mark.parametrize("form", ["json", "csv"])
def test_scenarios_json_is_refused_as_an_import_part(form):
    parts = json_parts() if form == "json" else csv_parts()
    parts.append(Part("scenarios.json", (RELEASE / "scenarios.json").read_bytes()))
    result = validate_upload(parts, checked_at=CHECKED_AT)
    report = result.report
    expected = [(f"unexpected part #{len(parts)}", "unexpected_file")]
    if form == "csv":  # a 13th part is also over the 12-file limit
        expected.insert(0, ("manifest.json", "too_many_files"))
    assert [(e.file, e.code) for e in report.errors] == expected
    assert result.records == {} and report.bundle_sha256 is None
    assert "scenarios" not in report.model_dump_json()


def test_scenarios_json_is_not_an_import_shape(manifest):
    assert "contract_version" not in manifest
    assert not set(manifest) & {e.value for e in E}
    assert json.loads(json.dumps(manifest)) == manifest
