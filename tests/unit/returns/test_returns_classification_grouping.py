"""M19 Part 2: rule-signature grouping, stored-row shapes and the `record` CLI, without a
database. Database behaviour is covered by the integration module. Inputs are hand-written
synthetic checks, not an evaluation set."""

import json

import pytest

from ecommerce_search.returns.classification import cli, store
from ecommerce_search.returns.classification.baseline import classify
from ecommerce_search.returns.classification.contracts import ClassificationInput
from ecommerce_search.returns.classification.grouping import (
    GroupItem,
    group_items,
    signature,
)

DEFECT = ("product_quality_defect", "manufacturing_defect")


def item(
    cid,
    product="SYN-SHO-0001",
    candidates=("sizing_fit",),
    rules=(),
    ordinal=0,
    tier="medium",
    contradicted=False,
    reasons=(),
):
    return GroupItem(
        cid,
        product,
        ordinal,
        candidates,
        rules,
        tier=tier,
        contradicted=contradicted,
        review_reasons=reasons,
    )


# ---- signatures ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        (("code:SIZE_TOO_SMALL",), "sizing_fit:too_small"),
        (("text:sizing_small:too tight",), "sizing_fit:too_small"),
        (("code:SIZE_TOO_LARGE", "text:sizing_large:too big"), "sizing_fit:too_large"),
        (("code:SIZE_TOO_SMALL", "text:sizing_large:too big"), "sizing_fit:mixed"),
        (("text:sizing_unspecified:fit nahi",), "sizing_fit:unspecified"),
    ],
)
def test_sizing_signatures_keep_the_direction(rules, expected):
    assert signature(item(1, rules=rules)) == expected


def test_an_unresolved_defect_keeps_both_candidates_in_its_signature():
    assert signature(item(1, candidates=DEFECT)) == "product_quality_defect|manufacturing_defect"


def test_other_categories_use_the_candidate_set_only():
    assert signature(item(1, candidates=("damaged_product",), rules=("code:X",))) == (
        "damaged_product"
    )


# ---- grouping ------------------------------------------------------------------------------


def test_groups_are_per_sku_and_per_signature_and_flag_only_large_groups():
    items = [
        item(1, rules=("code:SIZE_TOO_SMALL",)),
        item(2, rules=("code:SIZE_TOO_SMALL",)),
        item(3, rules=("code:SIZE_TOO_SMALL",)),
        item(4, rules=("code:SIZE_TOO_LARGE",)),
        item(5, product="SYN-SHO-0002", rules=("code:SIZE_TOO_SMALL",)),
    ]
    groups = group_items(items, min_pattern_size=3)
    summary = [(g.product_id, g.signature, g.member_count, g.is_pattern) for g in groups]
    assert summary == [
        ("SYN-SHO-0001", "sizing_fit:too_large", 1, False),
        ("SYN-SHO-0001", "sizing_fit:too_small", 3, True),
        ("SYN-SHO-0002", "sizing_fit:too_small", 1, False),
    ]


def test_grouping_does_not_depend_on_input_order():
    items = [item(i, rules=("code:SIZE_TOO_SMALL",)) for i in range(5)]
    assert group_items(items, 3) == group_items(list(reversed(items)), 3)


def test_a_return_with_two_predictions_joins_two_groups():
    items = [
        item(1, candidates=("damaged_product",), ordinal=0),
        item(1, candidates=("logistics_delay",), ordinal=1),
    ]
    assert [g.signature for g in group_items(items, 2)] == ["damaged_product", "logistics_delay"]


@pytest.mark.parametrize(
    ("kwargs", "qualifies"),
    [
        ({"tier": "high"}, True),
        ({"tier": "medium"}, True),
        ({"tier": "medium", "reasons": ("unsupported_distinction", "low_confidence")}, True),
        ({"tier": "low"}, False),
        ({"tier": "medium", "contradicted": True}, False),
        ({"tier": "high", "reasons": ("conflicting_statements",)}, False),
        ({"tier": "high", "reasons": ("statement_inspection_conflict",)}, False),
        ({"tier": "high", "reasons": ("conflicting_inspections",)}, False),
    ],
)
def test_which_members_may_count_towards_a_pattern(kwargs, qualifies):
    assert item(1, **kwargs).qualifies is qualifies


def test_low_tier_and_conflicting_members_never_make_a_pattern():
    rules = ("code:SIZE_TOO_SMALL",)
    weak = [
        item(1, rules=rules, tier="low"),
        item(2, rules=rules, tier="medium", contradicted=True),
        item(3, rules=rules, tier="high", reasons=("conflicting_statements",)),
        item(4, rules=rules, tier="high"),
        item(5, rules=rules, tier="medium"),
    ]
    (group,) = group_items(weak, 3)
    assert (group.member_count, group.qualifying_member_count, group.is_pattern) == (5, 2, False)
    (group,) = group_items([*weak, item(6, rules=rules, tier="high")], 3)
    assert (group.member_count, group.qualifying_member_count, group.is_pattern) == (6, 3, True)


def test_every_member_is_kept_even_when_it_does_not_qualify():
    (group,) = group_items([item(i, tier="low") for i in range(4)], 3)
    assert group.member_count == 4 and not group.is_pattern


def test_threshold_must_be_at_least_two():
    with pytest.raises(ValueError):
        group_items([], 1)


def test_one_return_cannot_appear_twice_in_one_group():
    with pytest.raises(ValueError):
        group_items([item(1, ordinal=0), item(1, ordinal=1)], 2)


# ---- stored-row shapes ---------------------------------------------------------------------


def _result():
    return classify(
        ClassificationInput(
            return_id="r1",
            customer_reason_code="ARRIVED_DAMAGED",
            customer_statements=[
                {
                    "evidence_type": "return_comment",
                    "evidence_id": "c1",
                    "text": "It is not damaged, just too tight",
                }
            ],
            inspection_observations=[{"observation_id": "o1", "condition": "as_new"}],
        )
    )


def test_evidence_rows_keep_kinds_ordinals_and_spans_but_not_text():
    rows = store._evidence_rows(_result())
    assert [r["ordinal"] for r in rows] == list(range(len(rows)))
    kinds = [(r["kind"], r["prediction_ordinal"]) for r in rows]
    assert ("supporting", 0) in kinds and ("contradicting", 1) in kinds
    assert ("inspection_finding", None) in kinds and ("suppressed_match", None) in kinds
    for row in rows:
        assert "matched_text" not in row
        assert (row["field"] == "text") == (row["span_start"] is not None)
        assert (row["kind"] == "inspection_finding") == (row["effect"] is not None)


def test_hashes_are_canonical():
    assert store._sha({"b": 1, "a": [1, 2]}) == store._sha({"a": [1, 2], "b": 1})
    assert store._sha({"a": 1}) != store._sha({"a": 2})


# ---- record CLI ----------------------------------------------------------------------------


class _Report:
    def __init__(self, **data):
        self.data = data
        self.run_id = data.get("run_id", 0)

    def model_dump(self, mode):
        return self.data


def test_record_prints_both_reports(monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_PASSWORD", "cli-test-password-not-real")
    calls = []
    monkeypatch.setattr(
        store,
        "record_classification_run",
        lambda engine, ds, ver: (
            calls.append(("run", ds, ver)) or _Report(run_id=7, status="recorded")
        ),
    )
    monkeypatch.setattr(
        store,
        "record_group_run",
        lambda engine, run_id, size: (
            calls.append(("groups", run_id, size)) or _Report(status="recorded")
        ),
    )
    code = cli.main(["record", "--dataset-id", "synthetic-returns", "--dataset-version", "1"])
    assert code == cli.EXIT_OK
    assert calls == [("run", "synthetic-returns", 1), ("groups", 7, 3)]
    out = json.loads(capsys.readouterr().out)
    assert out["classification"]["run_id"] == 7 and out["groups"]["status"] == "recorded"


def test_record_errors_are_fixed_messages(monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_PASSWORD", "cli-test-password-not-real")

    def refuse(engine, ds, ver):
        raise store.StoreRoleError("connect as the least-privilege classifier role")

    monkeypatch.setattr(store, "record_classification_run", refuse)
    code = cli.main(["record", "--dataset-id", "SECRETDS", "--dataset-version", "1"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_ERROR
    assert "least-privilege classifier role" in err
    assert "SECRETDS" not in err and "cli-test-password-not-real" not in err


def test_record_requires_settings_and_a_valid_threshold(capsys):
    assert cli.main(["record", "--dataset-id", "d", "--dataset-version", "1"]) == cli.EXIT_ERROR
    assert "settings are incomplete" in capsys.readouterr().err
    args = ["record", "--dataset-id", "d", "--dataset-version", "1", "--min-pattern-size", "1"]
    assert cli.main(args) == cli.EXIT_ERROR
