"""M14-6 preparation: adapters and label mapping, grouping and selection, `corrupt-1`, and the
`prepare` pipeline, on hand-written synthetic CSV rows only."""

import json

import pytest
from evals.listing_decisions import adapters, corrupt, selection
from evals.listing_decisions.adapters import Adapted, Excluded
from evals.listing_decisions.contracts import EvalSetError, load_set
from evals.listing_decisions.corruption import evidence_preserved
from evals.listing_decisions.evaluate import evaluate
from evals.listing_decisions.prepare import prepare, raw_bytes
from prepare_support import (
    FLIPKART_ROWS,
    LAPTOP_ROWS,
    PHONE_ROWS,
    phone,
    specs,
    tree,
    write_sources,
)

from ecommerce_search.decision.listing_questions import build_listing_state, model_inputs
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing

_BY_PID = {r["pid"]: r for r in FLIPKART_ROWS}


def _flipkart(pid: str) -> Adapted | Excluded:
    return adapters.adapt_flipkart(_BY_PID[pid])


def _labels(result: Adapted) -> dict[str, tuple]:
    return {lbl["task"]: (lbl["value"], lbl.get("scoring")) for lbl in result.labels}


# --- adapters and label mapping (la-1 / lm-1) ------------------------------------------------


def test_a_flipkart_casual_shoe_maps_documented_fields_and_labels() -> None:
    result = _flipkart("SHOE1")
    assert isinstance(result, Adapted) and result.stratum == "shoes:casual"
    assert result.fields == {
        "seller_id": "flipkart-sample",
        "seller_listing_ref": "SHOE1",
        "title": "Nike Men's Casual Loafers",
        "description": "Brown leather loafers by Nike.",
        "brand": "Nike",
        "price": "999",
        "category": "shoes",
        "subcategory": "casual",
        "color": "Brown",
        "material": "Leather",
        "gender": "Men",
    }
    assert _labels(result) == {"category": ("shoes", None), "subcategory": ("casual", "exact")}
    assert all(lbl["label_kind"] == "mapped" for lbl in result.labels)


def test_no_label_source_or_missing_claim_is_ever_filled_in() -> None:
    for pid in ("SHOE1", "SHOE4", "SHOE6", "HP1", "HP3", "ACC1"):
        fields = _flipkart(pid).fields
        assert not {"product_category_tree", "Occasion", "Headset Design", "Headphone Type"} & set(
            fields
        )
        assert "availability" not in fields and "currency" not in fields
    assert "brand" not in _flipkart("SHOE4").fields  # blank source brand stays missing


@pytest.mark.parametrize(
    ("pid", "stratum", "labels"),
    [
        ("SHOE4", "shoes:other", {"category": ("shoes", None)}),  # Casual leaf, Party occasion
        (
            "SHOE5",
            "shoes:formal",
            {"category": ("shoes", None), "subcategory": ("formal", "exact")},
        ),
        (
            "SHOE6",
            "shoes:sports",
            {
                "category": ("shoes", None),
                "subcategory": ("sports", "excluded_ambiguous_mapping"),
            },
        ),
        ("SHOE7", "shoes:other", {"category": ("shoes", None)}),  # wedges: footwear, no subcat
        (
            "HP1",
            "headphones:in-ear",
            {"category": ("headphones", None), "subcategory": ("in-ear", "exact")},
        ),
        ("HP2", "headphones:unlabelled", {"category": ("headphones", None)}),  # Over the Head only
        (
            "HP3",
            "headphones:over-ear",
            {"category": ("headphones", None), "subcategory": ("over-ear", "exact")},
        ),
        ("HP4", "headphones:unlabelled", {"category": ("headphones", None)}),  # conflicting specs
        ("ACC1", "none:phone_accessory", {"category": ("none", None)}),
        ("ACC3", "none:laptop_accessory", {"category": ("none", None)}),
    ],
)
def test_lm1_label_mapping_cases(pid, stratum, labels) -> None:
    result = _flipkart(pid)
    assert (result.stratum, _labels(result)) == (stratum, labels)


@pytest.mark.parametrize(
    ("pid", "reason"),
    [
        ("SHOE8", "uncertain_or_multiple_categories"),
        ("ACC2", "accessory_path_without_accessory_title_word"),
        ("CL1", "out_of_scope"),
        ("MOB1", "phone_from_another_source"),
    ],
)
def test_flipkart_exclusions_have_fixed_reasons(pid, reason) -> None:
    assert _flipkart(pid) == Excluded("flipkart", pid, reason)


def test_wireless_maps_exact_values_only_and_none_items_carry_no_category() -> None:
    assert _flipkart("HP1").fields["wireless"] is True
    assert _flipkart("HP2").fields["wireless"] is False
    assert "wireless" not in _flipkart("HP4").fields and _flipkart("HP4").omitted == ("wireless",)
    assert "category" not in _flipkart("ACC1").fields


def test_spec_text_is_parsed_by_regex_with_escapes_removed() -> None:
    text = specs(Color='Navy \\"Blue\\"')
    assert adapters.specs_of(text) == {"Color": 'Navy "Blue"'}
    assert adapters.specs_of("nil") == adapters.specs_of("{broken") == {}
    assert adapters.path_of(tree("A", "B")) == ["A", "B"]


def test_smartphone_adapter_transformations_and_exclusions() -> None:
    first = adapters.adapt_smartphone(PHONE_ROWS[0], 1)
    assert first.fields == {
        "seller_id": "smartphones-kaggle",
        "seller_listing_ref": "row-1",
        "title": "Samsung Galaxy A54 5G",
        "price": "₹19,999",
        "category": "phone",
        "processor": "Snapdragon 695",
        "operating_system": "Android v12",
    }
    assert first.labels[0]["label_kind"] == "source" and first.stratum == "phone"
    second = adapters.adapt_smartphone(PHONE_ROWS[1], 2)
    assert "processor" not in second.fields and second.omitted == ("processor",)
    assert adapters.adapt_smartphone(PHONE_ROWS[2], 3).reason == "non_phone_or_display_ge_7in"
    assert adapters.adapt_smartphone(PHONE_ROWS[3], 4).reason == "column_shape_failed"


@pytest.mark.parametrize(
    ("generation", "processor"),
    [
        ("5th Gen AMD Ryzen 5 5600H", "AMD Ryzen 5 5600H"),
        ("12th Gen Intel Core i5 1235U", "Intel Core i5 1235U"),
        ("11th Gen Intel Core i7 1165G7", "Intel Core i7 1165G7"),
        ("Apple M2 Pro", "Apple M2 Pro"),
        ("Intel Celeron  N4500", "Intel Celeron N4500"),
        ("7th Gen AMD Ryzen 5  7535HS​", "AMD Ryzen 5 7535HS"),
        ("Intel Core Ultra 7 Series 1 155H", None),
        ("Octa Core", None),
    ],
)
def test_laptop_processor_only_from_an_explicit_processor_model(generation, processor) -> None:
    result = adapters.adapt_laptop(LAPTOP_ROWS[0] | {"Generation": generation})
    assert result.fields.get("processor") == processor


@pytest.mark.parametrize(
    ("ssd", "storage_type"),
    [
        ("512 GB SSD", "SSD"),
        ("1 TB Hard Disk", "HDD"),
        ("", None),
        ("512 GB SSD + 1 TB HDD", None),
        ("4 GB NVIDIA GeForce RTX 3050", None),
    ],
)
def test_laptop_storage_type_is_the_medium_only(ssd, storage_type) -> None:
    result = adapters.adapt_laptop(LAPTOP_ROWS[0] | {"SSD": ssd})
    assert result.fields.get("storage_type") == storage_type
    assert "512" not in str(result.fields.get("storage_type"))


def test_laptop_rows_keep_hdd_and_blank_storage_and_exclude_invalid_shapes() -> None:
    hdd, blank = adapters.adapt_laptop(LAPTOP_ROWS[1]), adapters.adapt_laptop(LAPTOP_ROWS[2])
    assert hdd.fields["storage_type"] == "HDD" and "storage_type" not in blank.fields
    assert adapters.adapt_laptop(LAPTOP_ROWS[3]).reason == "title_rule_not_a_laptop"
    assert adapters.adapt_laptop(LAPTOP_ROWS[4]).reason == "column_shape_failed"


# --- grouping, selection, split (lg-1) -------------------------------------------------------


def _row(row_id: str, *identities: str, stratum: str = "phone") -> Adapted:
    return Adapted("smartphones", row_id, {}, (), stratum, identities)


def test_records_sharing_any_identity_share_one_group_transitively() -> None:
    rows = [_row("a", "x", "y"), _row("b", "y", "z"), _row("c", "z"), _row("d", "w")]
    groups = selection.group_keys(rows)
    assert (
        groups[("smartphones", "a")] == groups[("smartphones", "b")] == groups[("smartphones", "c")]
    )
    assert groups[("smartphones", "d")] != groups[("smartphones", "a")]


def test_different_listing_ids_with_one_normalized_title_share_a_group() -> None:
    kept, _ = selection.dedupe_variants(
        [r for r in (_flipkart(p) for p in ("SHOE1", "SHOE2", "SHOE3")) if isinstance(r, Adapted)]
    )
    groups = selection.group_keys(kept)
    assert [r.source_row_id for r in kept] == ["SHOE1", "SHOE3"]
    assert groups[("flipkart", "SHOE1")] == groups[("flipkart", "SHOE3")]


def test_variants_of_one_listing_keep_the_lowest_pid() -> None:
    kept, excluded = selection.dedupe_variants([_flipkart("SHOE2"), _flipkart("SHOE1")])
    assert [r.source_row_id for r in kept] == ["SHOE1"]
    assert excluded == [Excluded("flipkart", "SHOE2", "duplicate_listing_variant")]


def test_caps_are_upper_bounds_and_short_strata_are_reported_not_filled() -> None:
    rows = [_row(f"p{i}") for i in range(5)] + [_row("s1", stratum="shoes:casual")]
    chosen, counts = selection.select(rows, "seed")
    assert counts["phone"] == {"eligible": 5, "cap": 120, "selected": 5, "shortfall": 115}
    assert counts["shoes:casual"]["selected"] == 1 and counts["shoes:formal"]["eligible"] == 0
    assert len(chosen) == 6
    assert selection.select(rows, "seed")[0] == chosen


def test_selection_order_and_split_are_seeded_and_group_level() -> None:
    rows = [_row(f"p{i}") for i in range(200)]
    first, _ = selection.select(rows, "seed-a")
    second, _ = selection.select(rows, "seed-b")
    assert [r.source_row_id for r in first] != [r.source_row_id for r in second]
    assert selection.split_of("g", "seed-a") == selection.split_of("g", "seed-a")
    assert {selection.split_of(f"g{i}", "s") for i in range(20)} == {"dev", "test"}


def test_injection_texts_add_no_numbers_and_preserve_m13_evidence() -> None:
    assert not any(ch.isdigit() for text in corrupt.INJECTION_TEXTS for ch in text)
    shoe = _flipkart("SHOE1").fields
    clean = normalize_listing(parse_raw_listing(raw_bytes(shoe)))
    for text in corrupt.INJECTION_TEXTS:
        injected = shoe | {"description": f"{shoe['description']} {text}"}
        assert evidence_preserved(clean, normalize_listing(parse_raw_listing(raw_bytes(injected))))


# --- prepare end to end ----------------------------------------------------------------------


@pytest.fixture
def prepared(tmp_path):
    inputs, sources = write_sources(tmp_path / "src")
    out = tmp_path / "out"
    manifest = prepare(inputs, out, sources=sources, synthetic_fixture=True)
    return inputs, sources, out, manifest


def test_prepare_writes_a_valid_set_raw_files_and_counts(prepared) -> None:
    _, _, out, manifest = prepared
    eval_set = load_set(out / "set.json")
    assert eval_set.set_sha256 == manifest["set_sha256"]
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "raw", "set.json"]
    assert len(list((out / "raw").iterdir())) == len(eval_set.items)
    excluded = manifest["sources"]["flipkart"]["excluded_by_reason"]
    assert excluded == {
        "accessory_path_without_accessory_title_word": 1,
        "duplicate_listing_variant": 1,
        "out_of_scope": 1,
        "phone_from_another_source": 1,
        "uncertain_or_multiple_categories": 1,
    }
    assert manifest["sources"]["smartphones"]["excluded_by_reason"] == {
        "column_shape_failed": 1,
        "non_phone_or_display_ge_7in": 1,
    }
    assert manifest["sources"]["laptop_price"]["excluded_by_reason"] == {
        "column_shape_failed": 1,
        "title_rule_not_a_laptop": 1,
    }
    assert manifest["sources"]["laptop_price"]["omitted_fields_in_selected"] == {
        "processor": 1,
        "storage_type": 1,
    }
    assert manifest["strata"]["shoes:casual"] == {
        "eligible": 2,
        "cap": 40,
        "selected": 2,
        "shortfall": 38,
    }
    # flipkart 17 rows - 5 excluded = 12; smartphones 5 - 2 = 3; laptops 5 - 2 = 3 (no cap binds)
    assert manifest["clean_items"] == 18


def test_every_corruption_count_is_an_upper_bound_with_reported_shortfall(prepared) -> None:
    _, _, out, manifest = prepared
    eval_set = load_set(out / "set.json")
    twins = [i.twin_of for i in eval_set.items if i.twin_of is not None]
    assert len(twins) == len(set(twins)) == manifest["corrupted_items"]  # one per clean item
    for by_category in manifest["corruption"].values():
        for counts in by_category.values():
            assert counts["generated"] <= min(counts["requested"], counts["eligible"])
            assert counts["shortfall"] == counts["requested"] - counts["generated"]
    assert manifest["corruption"]["instruction_injection"]["none"]["shortfall"] > 0
    for item in eval_set.items:
        if item.twin_of is not None:
            twin = eval_set.item(item.twin_of)
            assert (twin.split, twin.group_key) == (item.split, item.group_key)


def test_groups_never_cross_splits_and_split_counts_are_reported(prepared) -> None:
    _, _, out, manifest = prepared
    eval_set = load_set(out / "set.json")
    by_group = {}
    for item in eval_set.items:
        assert by_group.setdefault(item.group_key, item.split) is item.split
    clean = sum(v for v in manifest["split_counts_clean"].values())
    assert clean == manifest["clean_items"]


def test_prepared_listings_never_show_labels_or_label_sources_to_the_model(prepared) -> None:
    _, _, out, _ = prepared
    eval_set = load_set(out / "set.json")
    for item in eval_set.items:
        data = (out / "raw" / f"{item.raw_sha256}.json").read_bytes()
        fields = json.loads(data)
        assert not {"product_category_tree", "Occasion", "Headset Design"} & set(fields)
        raw = parse_raw_listing(data)
        inputs = model_inputs(build_listing_state(raw, normalize_listing(raw)))
        assert set(inputs["listing_text"]) == {"title", "description"}
        claims = inputs["listing_claims"]["claims"].values()
        assert not {"category", "subcategory"} & {c["field"] for c in claims}


def test_prepare_is_deterministic(prepared, tmp_path) -> None:
    inputs, sources, out, manifest = prepared
    again = prepare(inputs, tmp_path / "again", sources=sources, synthetic_fixture=True)
    assert again == manifest
    assert (tmp_path / "again" / "set.json").read_bytes() == (out / "set.json").read_bytes()


def test_prepare_refuses_changed_inputs_and_an_existing_output(prepared, tmp_path) -> None:
    inputs, sources, out, _ = prepared
    with pytest.raises(EvalSetError, match="already exists"):
        prepare(inputs, out, sources=sources)
    inputs["smartphones"].write_text("model\n", encoding="utf-8")
    target = tmp_path / "never"
    with pytest.raises(EvalSetError, match="pinned SHA-256"):
        prepare(inputs, target, sources=sources)
    assert not target.exists()


def test_the_default_pins_are_the_m14_0_checksums_and_refuse_synthetic_files(tmp_path) -> None:
    inputs, _ = write_sources(tmp_path / "src")
    with pytest.raises(EvalSetError, match="pinned SHA-256"):
        prepare(inputs, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_the_prepared_dev_split_evaluates_deterministically(prepared) -> None:
    _, _, out, _ = prepared
    report, failed = evaluate(load_set(out / "set.json"), out / "raw")
    assert report["synthetic"] is True and report["split"]["evaluated"] == "dev"
    missing = report["descriptive"]["missing_required_fields"]["by_source"]
    for body in missing.values():
        assert body["by_field"]["availability"]["numerator"] == body["items"]  # never fabricated
    assert set(report["descriptive"]["deterministic_routes"]["routes"]) == {"manual_review"}
    injection = report["corruption"]["instruction_injection"]["deterministic"]["non_degradation"]
    assert injection["status"] in ("non_informative_ceiling_limited", "not_checked")
    assert injection["informative"] == 0
    assert failed == []


def test_a_phone_with_the_same_family_lands_in_the_same_group(tmp_path) -> None:
    phones = [phone("Samsung Galaxy A54 5G"), phone("Samsung Galaxy S23 5G")]
    inputs, sources = write_sources(tmp_path / "src", phones=phones)
    prepare(inputs, tmp_path / "out", sources=sources, synthetic_fixture=True)
    items = [
        i for i in load_set(tmp_path / "out" / "set.json").items if i.source_id == "smartphones"
    ]
    assert len({i.group_key for i in items if i.twin_of is None}) == 1
