"""M14-4 corruption reporting: structural pair checks, route transitions, attributable detection
and the instruction-injection non-degradation check."""

import copy

import pytest
from evals.listing_decisions.contracts import EvalSetError, load_set
from evals.listing_decisions.evaluate import evaluate
from listing_eval_support import (
    CORRUPT_COLOR,
    CORRUPT_INJECTION,
    ITEMS,
    SHOE_CASUAL,
    corruption,
    default_outcomes,
    outcome,
    write_fixture,
    write_outcomes,
)


def _run(tmp_path, items=None, outcomes=None):
    set_path, raw_dir = write_fixture(tmp_path, items)
    path = write_outcomes(
        tmp_path / "outcomes.jsonl", set_path, default_outcomes() if outcomes is None else outcomes
    )
    return evaluate(load_set(set_path), raw_dir, path)


def test_category_swap_counts_only_the_attributable_jev_signal(tmp_path) -> None:
    report, failed = _run(tmp_path)
    swap = report["corruption"]["category_swap"]
    deterministic, jev = swap["deterministic"], swap["jev"]
    # The swap triggers several unrelated M13 manual-review findings; none counts as detection.
    assert deterministic["transitions"] == {"accept_with_warning->manual_review": 1}
    assert deterministic["attributable_detection"]["value"] is None
    assert (
        "no deterministic reason is attributable"
        in deterministic["attributable_detection"]["reason"]
    )
    assert jev["attributable_detection"] == {
        "jev.category_disagrees_with_seller": {
            "detected": 1,
            "absent": 0,
            "present_in_clean_twin": 0,
            "not_assessable": 0,
        }
    }
    assert (jev["raised"], jev["unchanged"], jev["lowered"], jev["clean_manual_review"]) == (
        1,
        0,
        0,
        0,
    )
    assert failed == []


def test_attribute_swap_is_detected_by_the_contradicted_claim_only(tmp_path) -> None:
    report, _ = _run(tmp_path)
    swap = report["corruption"]["attribute_swap"]
    assert swap["deterministic"]["attributable_detection"]["m13.value_conflict"]["absent"] == 1
    assert swap["deterministic"]["transitions"] == {"accept_with_warning->accept_with_warning": 1}
    jev = swap["jev"]["attributable_detection"]
    assert jev["jev.claim_contradicted"]["detected"] == 1
    assert jev["m13.value_conflict"]["absent"] == 1


def test_a_signal_already_present_in_the_clean_twin_is_not_attributable(tmp_path) -> None:
    contradicted = {"color": ("contradicted",)}
    outcomes = default_outcomes() | {
        "shoe-1": outcome(SHOE_CASUAL, support=contradicted),
        "shoe-1-c2": outcome(CORRUPT_COLOR, support=contradicted),
        "shoe-1-c3": outcome(CORRUPT_INJECTION, support=contradicted),
    }
    report, failed = _run(tmp_path, outcomes=outcomes)
    jev = report["corruption"]["attribute_swap"]["jev"]
    assert jev["attributable_detection"]["jev.claim_contradicted"] == {
        "detected": 0,
        "absent": 0,
        "present_in_clean_twin": 1,
        "not_assessable": 0,
    }
    assert jev["clean_manual_review"] == 1
    assert failed == []


def test_a_lowered_route_is_recorded_but_never_fails_a_non_injection_rule(tmp_path) -> None:
    contradicted = {"material": ("contradicted",)}
    outcomes = default_outcomes() | {
        "shoe-1": outcome(SHOE_CASUAL, support=contradicted),  # manual_review
        "shoe-1-c2": outcome(CORRUPT_COLOR),  # accept_with_warning: lowered
        "shoe-1-c3": outcome(CORRUPT_INJECTION, support=contradicted),  # unchanged
    }
    report, failed = _run(tmp_path, outcomes=outcomes)
    jev = report["corruption"]["attribute_swap"]["jev"]
    assert (jev["lowered"], jev["transitions"]) == (
        1,
        {"manual_review->accept_with_warning": 1},
    )
    assert "non_degradation" not in jev
    assert failed == []


def test_out_of_set_subcategory_needs_its_own_finding_and_jev_pairs_need_both_outcomes(
    tmp_path,
) -> None:
    report, _ = _run(tmp_path)
    oos = report["corruption"]["out_of_set_subcategory"]
    detection = oos["deterministic"]["attributable_detection"]
    assert detection["m13.incompatible_subcategory"]["detected"] == 1
    assert detection["m13.candidate_unsupported"]["absent"] == 1
    assert (oos["jev"]["pairs"], oos["jev"]["evaluated"], oos["jev"]["not_evaluated"]) == (1, 0, 1)


def test_instruction_injection_is_a_non_degradation_check_only(tmp_path) -> None:
    report, failed = _run(tmp_path)
    for arm in ("deterministic", "jev"):
        body = report["corruption"]["instruction_injection"][arm]
        assert body["attributable_detection"]["value"] is None
        assert "not evidence that the injection was detected" in body["non_degradation"]["note"]
        # shoe-1-c3 keeps its evidence; lp-1-c1 adds a RAM claim, so it is not checked.
        assert body["non_degradation"]["evidence_preserved"] == 1
        assert body["non_degradation"]["evidence_not_preserved"] == 1
        assert body["non_degradation"]["status"] == "passed"
    assert report["corruption"]["instruction_injection"]["deterministic"]["transitions"] == {
        "accept_with_warning->accept_with_warning": 1,
        "accept_with_warning->manual_review": 1,
    }
    assert failed == []


def test_a_lowered_route_with_preserved_evidence_fails_the_injection_check(tmp_path) -> None:
    outcomes = default_outcomes() | {
        "shoe-1": outcome(SHOE_CASUAL, support={"color": ("contradicted",)}),  # manual_review
        "shoe-1-c3": outcome(CORRUPT_INJECTION),  # accept_with_warning: lowered
    }
    report, failed = _run(tmp_path, outcomes=outcomes)
    body = report["corruption"]["instruction_injection"]["jev"]["non_degradation"]
    assert (body["lowered_with_evidence_preserved"], body["status"]) == (1, "failed")
    assert failed == ["instruction_injection.jev.non_degradation"]


def test_a_lowered_route_without_preserved_evidence_is_not_a_failure(tmp_path) -> None:
    from listing_eval_support import CORRUPT_INJECTION_EVIDENCE, LAPTOP

    outcomes = default_outcomes() | {
        "lp-1": outcome(LAPTOP, ("phone",), ("smartphone",)),
        "lp-1-c1": outcome(CORRUPT_INJECTION_EVIDENCE, ("laptop",), ("none",)),
    }
    report, failed = _run(tmp_path, outcomes=outcomes)
    body = report["corruption"]["instruction_injection"]["jev"]
    assert body["non_degradation"]["evidence_not_preserved"] == 1
    assert failed == []


def _with(item_id: str, fields: dict, ref: dict, twin: str) -> dict:
    items = copy.deepcopy(ITEMS)
    _, source, group, split, _, _, _ = ITEMS[twin]
    items[item_id] = (fields, source, group, split, [], twin, ref)
    return items


_PHONE = ITEMS["ph-1"][0]
_SHOE_FORMAL = ITEMS["shoe-3"][0]


@pytest.mark.parametrize(
    "items",
    [
        _with(
            "shoe-1-c2",
            CORRUPT_COLOR | {"material": "Mesh"},
            corruption("attribute_swap", "color", "Brown"),
            "shoe-1",
        ),
        _with("shoe-1-c2", CORRUPT_COLOR, corruption("attribute_swap", "color", "Red"), "shoe-1"),
        _with(
            "ph-1-c1",
            _PHONE | {"seller_listing_ref": "ph-1-c1", "brand": "Samsung"},
            corruption("attribute_swap", "brand", "Xiaomi"),
            "ph-1",
        ),
        _with(
            "shoe-3-c1",
            _SHOE_FORMAL | {"seller_listing_ref": "x", "subcategory": "casual"},
            corruption("out_of_set_subcategory", "subcategory", "formal"),
            "shoe-3",
        ),
        _with(
            "shoe-1-c3",
            SHOE_CASUAL | {"seller_listing_ref": "x", "description": "Replaced text."},
            corruption("instruction_injection", "description", SHOE_CASUAL["description"]),
            "shoe-1",
        ),
        _with("shoe-1-c1", CORRUPT_COLOR, corruption("category_swap", "color", "Brown"), "shoe-1"),
    ],
    ids=[
        "two-keys-changed",
        "wrong-original-value",
        "original-not-in-text",
        "subcategory-inside-the-taxonomy",
        "injection-does-not-append",
        "category-swap-of-another-key",
    ],
)
def test_pairs_that_break_their_rule_are_refused(tmp_path, request, items) -> None:
    expected = {
        "two-keys-changed": "exactly its target key",
        "wrong-original-value": "original_value is not the twin's value",
        "original-not-in-text": "original value in the text",
        "subcategory-inside-the-taxonomy": "outside the taxonomy",
        "injection-does-not-append": "appends to the description",
        "category-swap-of-another-key": "changes the seller category",
    }[request.node.callspec.id]
    with pytest.raises(EvalSetError, match=expected):
        _run(tmp_path, items=items, outcomes={})


def test_the_original_not_in_text_case_is_a_real_attribute_swap_otherwise(tmp_path) -> None:
    """Guard for the parametrized case above: the phone's brand is absent from its own text."""
    assert "xiaomi" not in _PHONE["title"].casefold() and "description" not in _PHONE
