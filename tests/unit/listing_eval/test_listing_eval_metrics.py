"""M14-4 label-based metrics on hand-computed synthetic cases, explicit denominators, null
reasons, the deterministic baseline and the measurability registry."""

import pytest
from evals.listing_decisions.contracts import load_set
from evals.listing_decisions.evaluate import evaluate
from evals.listing_decisions.measurability import UNMEASURED
from evals.listing_decisions.metrics import NO_ELIGIBLE, NO_PREDICTIONS, ratio
from listing_eval_support import SHOE_SPORTS, default_outcomes, outcome, write_outcomes


def _report(fixture_set, outcomes_path=None):
    set_path, raw_dir, default = fixture_set
    report, failed = evaluate(load_set(set_path), raw_dir, outcomes_path or default)
    return report


def _value(r: dict) -> str | None:
    return r["value"]


def test_ratio_gives_null_with_a_reason_for_a_zero_denominator() -> None:
    assert ratio(0, 0, "why") == {"numerator": 0, "denominator": 0, "value": None, "reason": "why"}
    assert ratio(2, 3, "why")["value"] == "0.6667"


def test_category_pooled_counts_and_denominators_are_hand_computed(fixture_set) -> None:
    pooled = _report(fixture_set)["label_metrics"]["category"]["jev"]["pooled"]
    # eligible: acc-1, hp-1, lp-1, ph-1, shoe-1, shoe-2, shoe-3 (clean dev items with labels)
    assert (pooled["eligible"], pooled["attempted"], pooled["not_attempted"]) == (7, 7, 0)
    assert pooled["abstentions"] == {
        "total": 2,
        "tie": 1,
        "fallback": 1,
        "fallback_by_reason": {"timeout": 1},
    }
    assert (pooled["predictions"], pooled["correct"], pooled["wrong"]) == (5, 3, 1)
    assert pooled["predicted_none"] == 1  # hp-1: `none` for a headphones label
    assert pooled["overall_accuracy"] == ratio(3, 7, NO_ELIGIBLE)
    assert _value(pooled["overall_accuracy"]) == "0.4286"
    assert _value(pooled["answered_accuracy"]) == "0.6000"
    assert _value(pooled["coverage"]) == "0.7143"
    assert pooled["warning"].startswith("pooled over sources")
    assert pooled["confusion"]["shoes"] == {"shoes": 2, "tie": 1}
    assert pooled["confusion"]["phone"] == {"fallback": 1}
    laptop = pooled["per_label"]["laptop"]
    assert laptop["precision"]["value"] is None and laptop["precision"]["reason"]
    assert laptop["recall"]["value"] == "0.0000"
    assert laptop["f1"] == {"value": None, "reason": laptop["f1"]["reason"]}


def test_category_per_source_and_null_answered_accuracy(fixture_set) -> None:
    by_source = _report(fixture_set)["label_metrics"]["category"]["jev"]["by_source"]
    assert sorted(by_source) == ["flipkart", "laptop_price", "smartphones"]
    flipkart = by_source["flipkart"]
    assert (flipkart["eligible"], flipkart["correct"], flipkart["predicted_none"]) == (5, 3, 1)
    phones = by_source["smartphones"]  # one item, a fallback: no prediction at all
    assert (phones["eligible"], phones["predictions"]) == (1, 0)
    assert phones["answered_accuracy"] == ratio(0, 0, NO_PREDICTIONS)
    assert phones["overall_accuracy"]["value"] == "0.0000"


def test_subcategory_excludes_the_ambiguous_sports_label_for_every_prediction(fixture_set) -> None:
    pooled = _report(fixture_set)["label_metrics"]["subcategory"]["jev"]["pooled"]
    assert pooled["excluded_ambiguous_mapping"] == 1  # shoe-2
    assert (pooled["eligible"], pooled["predictions"], pooled["correct"], pooled["wrong"]) == (
        3,
        3,
        2,
        1,
    )
    assert _value(pooled["overall_accuracy"]) == "0.6667"
    assert "sports" not in pooled["confusion"]


@pytest.mark.parametrize("prediction", [("sports",), ("running",), ("casual",), ("none",)])
def test_the_sports_exclusion_does_not_depend_on_the_prediction(
    fixture_set, tmp_path, prediction
) -> None:
    set_path, _, _ = fixture_set
    outcomes = default_outcomes() | {"shoe-2": outcome(SHOE_SPORTS, ("shoes",), prediction)}
    path = write_outcomes(tmp_path / f"o-{prediction[0]}.jsonl", set_path, outcomes)
    baseline = _report(fixture_set)["label_metrics"]["subcategory"]["jev"]
    assert _report(fixture_set, path)["label_metrics"]["subcategory"]["jev"] == baseline


def test_the_deterministic_baseline_is_coverage_zero_and_not_a_predictor(fixture_set) -> None:
    baseline = _report(fixture_set)["label_metrics"]["category"]["deterministic_baseline"]
    assert baseline["proposes"] is False and baseline["predictions"] == 0
    assert baseline["eligible"] == 7
    assert baseline["coverage"] == ratio(0, 7, NO_ELIGIBLE)
    assert baseline["coverage"]["value"] == "0.0000"
    assert baseline["overall_accuracy"]["value"] is None
    assert "not an equivalent predictor" in baseline["overall_accuracy"]["reason"]


def test_without_outcomes_jev_metrics_are_null_with_a_reason(fixture_set) -> None:
    set_path, raw_dir, _ = fixture_set
    report, _ = evaluate(load_set(set_path), raw_dir, None)
    for task in ("category", "subcategory"):
        jev = report["label_metrics"][task]["jev"]
        assert jev == {"value": None, "reason": "no recorded Jev outcomes were supplied"}
    assert report["outcomes"] is None


def test_unmeasured_metrics_are_null_with_reasons_never_zero(fixture_set) -> None:
    unmeasured = _report(fixture_set)["measurability"]["unmeasured"]
    assert set(unmeasured) == set(UNMEASURED)
    assert {
        "route_correctness",
        "unsafe_auto_accept_rate",
        "unnecessary_review_rate",
        "ambiguity",
        "attribute_text_support",
        "calibration",
        "subcategory.coarse_sports_running",
    } <= set(unmeasured)
    for entry in unmeasured.values():
        assert entry["value"] is None and entry["reason"]


def test_descriptive_counts_are_separate_from_label_metrics(fixture_set) -> None:
    report = _report(fixture_set)
    descriptive = report["descriptive"]
    assert "no expected route" in descriptive["note"]
    assert descriptive["deterministic_routes"]["n"] == 7  # clean dev items
    assert descriptive["deterministic_routes"]["routes"] == {
        "accept_with_warning": 6,
        "manual_review": 1,  # acc-1: missing_required category
    }
    assert descriptive["jev_items_without_outcome"] == 0
    assert descriptive["jev_abstention_counts"] == {
        "category.none": 2,
        "category.tie": 1,
        "fallback.timeout": 1,
        "subcategory.none": 1,
    }
    assert "auto_accept" not in descriptive["jev_routes"]["routes"]
    titles = descriptive["title_word_disclosure"]["by_source"]
    assert titles["laptop_price"] == {"labelled_items": 1, "title_contains_category_word": 0}
    assert "accuracy" not in str(descriptive)
