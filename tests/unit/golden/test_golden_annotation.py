"""The blind annotation CLI on a temporary frozen pool. Every label typed here is a SYNTHETIC
TEST INPUT, never a human relevance label; the real working directory is never touched."""

import itertools
import re
import shutil
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest
from evals.annotation import cli
from evals.annotation.session import (
    BINDING_FILE,
    AnnotationError,
    WorkStore,
    hard_violation,
    load_pool,
    pending,
)
from evals.contracts.canonical import ContractError, decode_jsonl, encode_json_object
from evals.contracts.cards import HIDDEN_FIELDS, card, render_card
from evals.contracts.constants import Label, Relation
from evals.contracts.models import Judgement
from pool_support import LABEL_T0, frozen_pool, synthetic_primaries

NOW = LABEL_T0 + timedelta(days=1)
_TICKS = itertools.count()


@pytest.fixture(scope="module")
def pool(tmp_path_factory, catalog):
    return frozen_pool(tmp_path_factory.mktemp("annotation"), catalog)


@pytest.fixture(scope="module")
def context(pool, catalog):
    root, target, _, _ = pool
    return load_pool(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(context, tmp_path):
    """An initialized work store whose binding was created at NOW."""
    work = WorkStore(context, tmp_path / "work")
    work.initialize(now=NOW)
    return work


class Script:
    """Answers prompts in order; records every prompt and every printed line."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.said.append(text)


NOT_RELEVANT_OFF_TARGET = ["n", "2", "h", "", "", "", "y"]  # label, relation, ..., save


def _run(context, store, answers, annotator="ann-1"):
    script = Script(answers)
    saved = cli.annotate(
        context,
        store,
        annotator=annotator,
        ask=script.ask,
        say=script.say,
        now=lambda: NOW + timedelta(seconds=next(_TICKS)),
    )
    return saved, script


def test_the_pool_is_read_without_the_private_provenance(pool, catalog, tmp_path):
    root, target, private, _ = pool
    hidden = tmp_path / "moved"
    shutil.move(private, hidden)
    try:
        context = load_pool(target, allowed_root=root, catalog=catalog)
    finally:
        shutil.move(hidden, private)
    assert len(context.items) == sum(1 for _ in context.items)
    assert [(i.query_id, i.blind_position) for i in context.items] == sorted(
        (i.query_id, i.blind_position) for i in context.items
    )


def test_one_pair_at_a_time_with_resume(context, store):
    saved, script = _run(context, store, [*NOT_RELEVANT_OFF_TARGET * 2, "q"])
    assert saved == 2
    stored = store.load(now=NOW + timedelta(days=1))
    assert [(j.query_id, j.product_id) for j in stored] == sorted(
        (i.query_id, i.product_id) for i in context.items[:2]
    )
    assert all(j.label is Label.NOT_RELEVANT and j.relation is Relation.OFF_TARGET for j in stored)
    third = context.items[2]
    saved, script = _run(context, store, ["q"])
    assert saved == 0
    assert any(third.product_id in line for line in script.said)
    assert script.said[0].startswith(f"2 of {len(context.items)} pairs judged")


def test_nothing_is_prefilled_or_suggested(context, store):
    saved, script = _run(context, store, ["", "x", *NOT_RELEVANT_OFF_TARGET, "q"])
    assert saved == 1
    assert script.prompts[0].startswith("label") and script.prompts[1].startswith("label")
    shown = "\n".join(script.said + script.prompts).lower()
    words = set(re.findall(r"[a-z0-9_]+", shown))
    for forbidden in (
        "rank",
        "score",
        "v0_lexical",
        "system_union",
        "negative",
        "completion",
        "seller",
        "availability",
        "rating",
        "review",
        "suggest",
        "eligib",
    ):
        assert not any(word.startswith(forbidden) for word in words), forbidden


def test_relevant_is_refused_for_a_hard_violation(context, store):
    index = next(i for i, item in enumerate(context.items) if hard_violation(context, item))
    preceding = NOT_RELEVANT_OFF_TARGET * index
    answers = [*preceding, "r", *NOT_RELEVANT_OFF_TARGET, "q"]
    saved, script = _run(context, store, answers)
    assert saved == index + 1
    assert any("relevant is not allowed" in line for line in script.said)
    last = store.load(now=NOW + timedelta(days=1))
    item = context.items[index]
    match = [j for j in last if (j.query_id, j.product_id) == (item.query_id, item.product_id)]
    assert match[0].label is Label.NOT_RELEVANT


def test_borderline_requires_a_rationale_and_a_valid_relation(context, store):
    answers = ["b", "9", "1", "m", "", "Close substitute.", "", "", "y", "q"]
    saved, script = _run(context, store, answers)
    assert saved == 1
    assert sum(1 for p in script.prompts if p.startswith("relation")) == 2
    assert any("rationale is required" in line for line in script.said)
    (judgement,) = store.load(now=NOW + timedelta(days=1))
    assert judgement.label is Label.BORDERLINE and judgement.rationale == "Close substitute."
    assert judgement.relation in (Relation.PARTIAL, Relation.SUBSTITUTE)


def test_a_discarded_judgement_is_asked_again(context, store):
    answers = ["n", "2", "h", "", "", "", "n", *NOT_RELEVANT_OFF_TARGET, "q"]
    saved, script = _run(context, store, answers)
    assert saved == 1
    assert any("discarded" in line for line in script.said)


def test_only_ann_1_annotates(context, store):
    with pytest.raises(AnnotationError, match="only 'ann-1'"):
        _run(context, store, ["q"], annotator="ann-2")


def _judgement(item, **overrides):
    (j,) = synthetic_primaries([item], start=NOW)
    return j.model_copy(update=overrides)


def test_duplicate_foreign_unknown_and_out_of_window_records_are_refused(context, store):
    item = context.items[0]
    good = _judgement(item)
    store.save([good], now=NOW + timedelta(hours=1))
    with pytest.raises(ContractError):
        store.save([good, good], now=NOW + timedelta(hours=1))
    foreign = Judgement.model_validate({**good.model_dump(), "annotator_handle": "ann-2"})
    with pytest.raises(AnnotationError, match="foreign handle"):
        store.save([foreign], now=NOW + timedelta(hours=1))
    unknown = Judgement.model_validate({**good.model_dump(), "product_id": "SYN-XXX-9999"})
    with pytest.raises(AnnotationError, match="not a pair"):
        store.save([unknown], now=NOW + timedelta(hours=1))
    early = _judgement(item, annotated_at=LABEL_T0 - timedelta(days=30))
    with pytest.raises(AnnotationError, match="window"):
        store.save([early], now=NOW + timedelta(hours=1))
    future = _judgement(item, annotated_at=NOW + timedelta(days=5))
    with pytest.raises(AnnotationError, match="window"):
        store.save([future], now=NOW + timedelta(hours=1))
    wrong_rubric = _judgement(item, rubric_version="rubric-2")
    with pytest.raises(AnnotationError, match="rubric"):
        store.save([wrong_rubric], now=NOW + timedelta(hours=1))
    # Refused saves never replaced the stored file.
    assert store.load(now=NOW + timedelta(days=1)) == [good]


def test_a_tampered_working_file_is_refused_on_resume(context, store):
    store.save([_judgement(context.items[0])], now=NOW + timedelta(hours=1))
    text = store.path.read_text(encoding="utf-8").replace('"ann-1"', '"ann-7"')
    store.path.write_text(text, encoding="utf-8", newline="\n")
    with pytest.raises(AnnotationError, match="foreign handle"):
        store.load(now=NOW + timedelta(days=1))


def test_a_working_file_bound_to_another_pool_is_refused(context, store):
    store.save([_judgement(context.items[0])], now=NOW + timedelta(hours=1))
    binding = store.read_binding(now=NOW + timedelta(days=1))
    other = binding.model_copy(update={"pool_manifest_sha256": "f" * 64})
    (store.directory / BINDING_FILE).write_bytes(encode_json_object(other))
    with pytest.raises(AnnotationError, match="another pool"):
        store.load(now=NOW + timedelta(days=1))


def test_saves_are_atomic_and_canonical(context, store):
    judgements = synthetic_primaries(context.items[:5], start=NOW)
    store.save(reversed(judgements), now=NOW + timedelta(hours=1))
    assert sorted(p.name for p in store.directory.iterdir()) == [
        BINDING_FILE,
        "primary_judgements.jsonl",
    ]
    assert decode_jsonl(store.path.read_bytes(), Judgement) == sorted(
        judgements, key=lambda j: j.sort_key()
    )
    assert len(pending(context, judgements)) == len(context.items) - 5


def test_a_modified_product_or_sealed_round_blocks_annotation(pool, catalog):
    root, target, _, _ = pool
    context = load_pool(target, allowed_root=root, catalog=catalog)
    product_id = context.items[0].product_id
    product = catalog.products[product_id]
    products = dict(catalog.products)
    products[product_id] = replace(product, price=product.price + 1)
    mutated = replace(catalog, products=MappingProxyType(products))
    with pytest.raises(ContractError, match="hash differs"):
        load_pool(target, allowed_root=root, catalog=mutated)
    sealed = target / "manifests" / "3-primary_sealed.json"
    sealed.write_bytes(b"{}\n")
    try:
        with pytest.raises(AnnotationError, match="already sealed"):
            load_pool(target, allowed_root=root, catalog=catalog)
    finally:
        sealed.unlink()


def test_the_card_shows_only_approved_fields(catalog):
    for product in catalog.products.values():
        projection = card(product)
        assert set(projection) == {
            "attributes",
            "brand",
            "card_version",
            "category",
            "currency",
            "description",
            "price",
            "product_id",
            "subcategory",
            "title",
        }
        assert not HIDDEN_FIELDS & set(projection["attributes"])
        words = set(re.findall(r"[a-z0-9_]+", render_card(product).lower()))
        for hidden in ("seller", "availability", "rating", "review"):
            assert not any(word.startswith(hidden) for word in words), hidden
    missing = next(p for p in catalog.products.values() if None in p.spec.values())
    assert "not stated" in render_card(missing)
