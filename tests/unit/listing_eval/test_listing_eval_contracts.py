"""M14-4 `le-1` set contract: provenance, label kinds, measurability limits and set binding."""

import copy
import json

import pytest
from evals.listing_decisions.contracts import (
    EvalSet,
    EvalSetError,
    LabelKind,
    TaskLabel,
    dump_set,
    load_set,
)
from evals.listing_decisions.measurability import MEASURED_CATEGORY_LABELS
from listing_eval_support import ITEMS, label, set_data, state_of, write_fixture
from pydantic import ValidationError

from ecommerce_search.decision.listing_questions import model_inputs


def _validate(data: dict) -> EvalSet:
    return EvalSet.model_validate_json(dump_set(data))


def test_the_fixture_set_loads_with_its_provenance(tmp_path) -> None:
    set_path, _ = write_fixture(tmp_path)
    eval_set = load_set(set_path)
    assert eval_set.schema_version == "le-1" and eval_set.synthetic_fixture is True
    source = eval_set.source("flipkart")
    assert source.rights_caveat and source.licence_declared_by and source.files[0].sha256
    assert {lbl.label_kind for i in eval_set.items for lbl in i.labels} == {"mapped", "source"}


def test_label_kinds_have_no_human_reviewed_kind() -> None:
    assert [k.value for k in LabelKind] == ["source", "mapped", "synthetic_corruption"]
    with pytest.raises(ValidationError):
        TaskLabel.model_validate(label("category", "shoes", "synthetic_corruption"))
    with pytest.raises(ValidationError):
        TaskLabel.model_validate(label("category", "shoes", "human"))


def test_measured_category_labels_are_the_jl1_category_labels() -> None:
    from ecommerce_search.decision.listing_models import CATEGORY_LABELS

    assert MEASURED_CATEGORY_LABELS == CATEGORY_LABELS


@pytest.mark.parametrize(
    "body",
    [
        label("category", "shoes", "mapped") | {"mapping_version": None},
        label("category", "shoes", "source") | {"mapping_version": "fixture-map-1"},
        label("category", "tablet"),
        label("category", "shoes") | {"scoring": "exact"},
        label("category", "shoes") | {"origin_field": ""},
        label("subcategory", "running"),
        label("subcategory", "business"),
        label("subcategory", "smartphone"),
        label("subcategory", "casual") | {"scoring": None},
        label("subcategory", "casual", scoring="excluded_ambiguous_mapping"),
    ],
    ids=[
        "mapped-without-version",
        "source-with-version",
        "unknown-category",
        "category-with-scoring",
        "no-origin-field",
        "running-has-no-source",
        "laptop-subcategory-unmeasured",
        "phone-subcategory-unmeasured",
        "subcategory-without-scoring",
        "only-sports-is-ambiguous",
    ],
)
def test_invalid_labels_are_refused(body) -> None:
    with pytest.raises(ValidationError):
        TaskLabel.model_validate(body)


def _items_with(item_id: str, **changes) -> dict:
    items = copy.deepcopy(ITEMS)
    fields, source, group, split, labels, twin, corrupt = items[item_id]
    values = {
        "fields": fields,
        "source": source,
        "group": group,
        "split": split,
        "labels": labels,
        "twin": twin,
        "corrupt": corrupt,
    } | changes
    items[item_id] = tuple(values.values())
    return items


@pytest.mark.parametrize(
    "items",
    [
        _items_with("shoe-1-c1", split="test"),
        _items_with("shoe-1-c1", group="g-other"),
        _items_with("shoe-1-c1", labels=[label("category", "shoes")]),
        _items_with("shoe-1-c1", twin="shoe-1-c2"),
        _items_with("shoe-1-c1", twin="missing"),
        _items_with("shoe-1", labels=[label("category", "shoes"), label("category", "phone")]),
        _items_with("lp-2", group="g-lp-1"),
        _items_with("shoe-1", source="unknown_source"),
    ],
    ids=[
        "twin-in-another-split",
        "twin-in-another-group",
        "corrupted-item-with-labels",
        "twin-is-corrupted",
        "unknown-twin",
        "two-labels-for-one-task",
        "group-crosses-splits",
        "unknown-source",
    ],
)
def test_invalid_sets_are_refused(items) -> None:
    data = set_data(items)
    if any(row["source_id"] == "unknown_source" for row in data["items"]):
        data["sources"] = [s for s in data["sources"] if s["source_id"] != "unknown_source"]
    with pytest.raises(ValidationError):
        _validate(data)


def test_a_mapped_label_must_name_its_sources_mapping_version() -> None:
    items = _items_with(
        "shoe-1", labels=[label("category", "shoes") | {"mapping_version": "other-map"}]
    )
    with pytest.raises(ValidationError):
        _validate(set_data(items))


def test_an_edited_set_fails_its_digest(tmp_path) -> None:
    set_path, _ = write_fixture(tmp_path)
    data = json.loads(set_path.read_bytes())
    data["items"][0]["source_row_id"] = "edited"
    set_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvalSetError):
        load_set(set_path)


def test_set_bytes_are_canonical_and_reproducible() -> None:
    assert dump_set(set_data()) == dump_set(set_data())


def test_model_inputs_never_contain_seller_category_fields_or_labels() -> None:
    eval_set = _validate(set_data())
    for item in eval_set.items:
        fields = ITEMS[item.item_id][0]
        inputs = model_inputs(state_of(fields))
        assert set(inputs["listing_text"]) == {"title", "description"}
        assert set(inputs["listing_claims"]) == {"title", "description", "claims"}
        claim_fields = {c["field"] for c in inputs["listing_claims"]["claims"].values()}
        assert not {"category", "subcategory"} & claim_fields
        sent = json.dumps(inputs, ensure_ascii=False).casefold()
        text = f"{fields.get('title', '')} {fields.get('description', '')}".casefold()
        for lbl in item.labels:
            for hidden in (lbl.value, lbl.origin_field, lbl.mapping_version):
                if hidden and hidden.casefold() not in text:
                    assert hidden.casefold() not in sent, (item.item_id, hidden)
        for seller in ("category", "subcategory"):
            value = fields.get(seller)
            if value and value.casefold() not in text:
                assert value.casefold() not in sent, (item.item_id, seller)
    # the check is not vacuous: this listing's labels are absent from its own text
    assert "shoes" not in json.dumps(model_inputs(state_of(ITEMS["shoe-2"][0]))).casefold()
