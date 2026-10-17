"""The compact-input mode of the blind annotation CLI on a temporary frozen pool. Every entry
typed here is a SYNTHETIC TEST INPUT, never a human relevance label; the real working directory
is never touched."""

import itertools
import re
from datetime import timedelta

import pytest
from evals.annotation import cli
from evals.annotation.session import (
    BINDING_FILE,
    WorkStore,
    hard_unknown,
    hard_violation,
    load_pool,
)
from evals.contracts.constants import Label, Relation
from evals.contracts.models import Confidence
from pool_support import LABEL_T0, frozen_pool

NOW = LABEL_T0 + timedelta(days=1)
_TICKS = itertools.count()


@pytest.fixture(scope="module")
def pool(tmp_path_factory, catalog):
    return frozen_pool(tmp_path_factory.mktemp("annotation_compact"), catalog)


@pytest.fixture(scope="module")
def context(pool, catalog):
    root, target, _, _ = pool
    return load_pool(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(context, tmp_path):
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
            raise EOFError
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.said.append(text)


def _run(context, store, answers, *, compact=True):
    script = Script(answers)
    saved = cli.annotate(
        context,
        store,
        annotator="ann-1",
        ask=script.ask,
        say=script.say,
        now=lambda: NOW + timedelta(seconds=next(_TICKS)),
        compact=compact,
    )
    return saved, script


def _stored(store):
    return store.load(now=NOW + timedelta(days=1))


def _index(context, predicate):
    return next(i for i, item in enumerate(context.items) if predicate(context, item))


def _safe(context, item):
    return not hard_violation(context, item) and not hard_unknown(context, item)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("r1hnn!", (Label.RELEVANT, Relation.EXACT_MATCH, Confidence.HIGH, False, False)),
        ("r2mnn!", (Label.RELEVANT, Relation.SATISFIES_INTENT, Confidence.MEDIUM, False, False)),
        ("b1hyn!", (Label.BORDERLINE, Relation.PARTIAL, Confidence.HIGH, True, False)),
        ("b2lny!", (Label.BORDERLINE, Relation.SUBSTITUTE, Confidence.LOW, False, True)),
        ("n1mnn!", (Label.NOT_RELEVANT, Relation.COMPLEMENT, Confidence.MEDIUM, False, False)),
        ("n2hnn!", (Label.NOT_RELEVANT, Relation.OFF_TARGET, Confidence.HIGH, False, False)),
        ("n3lyy!", (Label.NOT_RELEVANT, Relation.OUT_OF_CATALOG, Confidence.LOW, True, True)),
        ("  N2HNN!  ", (Label.NOT_RELEVANT, Relation.OFF_TARGET, Confidence.HIGH, False, False)),
    ],
)
def test_parse_maps_every_field(text, expected):
    entry = cli.parse_compact(text)
    got = (
        entry.label,
        entry.relation,
        entry.confidence,
        entry.needs_adjudication,
        entry.catalog_issue,
    )
    assert got == expected
    assert entry.rationale is None


def test_parse_takes_the_rationale_after_the_bar():
    entry = cli.parse_compact("b1mnn! | Partially matches the stated intent")
    assert entry.label is Label.BORDERLINE and entry.relation is Relation.PARTIAL
    assert entry.rationale == "Partially matches the stated intent"
    assert cli.parse_compact("n2hnn! | a | b").rationale == "a | b"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "r",
        "r1hn!",
        "r1hnnn!",
        "x1hnn!",
        "r1xnn!",
        "r1hxn!",
        "r1hnx!",
        "r 1hnn!",
        "!r1hnn",
        "r1hnn!!",
        "rr1hnn!",
        "r1hnn! |",
        "r1hnn! |   ",
        "| rationale only",
    ],
)
def test_malformed_entries_are_refused(text):
    with pytest.raises(cli.CompactEntryError):
        cli.parse_compact(text)


@pytest.mark.parametrize("text", ["r0hnn!", "r3hnn!", "b3hnn!", "b0mnn!", "n4hnn!", "n9hnn!"])
def test_relations_the_label_does_not_allow_are_refused(text):
    with pytest.raises(cli.CompactEntryError, match="not allowed for"):
        cli.parse_compact(text)


@pytest.mark.parametrize("text", ["r1hnn", "n2hnn | rationale", "b1mnn"])
def test_the_trailing_bang_is_mandatory(text):
    with pytest.raises(cli.CompactEntryError, match="not confirmed"):
        cli.parse_compact(text)


def test_refused_entries_save_nothing_and_the_pair_is_asked_again(context, store):
    index = _index(context, _safe)
    preceding = ["n2hnn!"] * index
    answers = [*preceding, "r1hnn", "r3hnn!", "zzz", "r1hnn!", "q"]
    saved, script = _run(context, store, answers)
    assert saved == index + 1
    assert sum(1 for line in script.said if line.startswith("not saved")) == 3
    item = context.items[index]
    (match,) = [j for j in _stored(store) if j.product_id == item.product_id]
    assert (match.label, match.relation) == (Label.RELEVANT, Relation.EXACT_MATCH)


def test_one_prompt_per_pair_and_no_separate_save_prompt(context, store):
    saved, script = _run(context, store, ["n2hnn!", "n3lyy! | Not sold here.", "q"])
    assert saved == 2
    assert len(script.prompts) == 3
    assert all(p == cli.COMPACT_PROMPT for p in script.prompts)
    first, second = sorted(_stored(store), key=lambda j: j.annotated_at)
    assert (first.relation, first.needs_adjudication, first.catalog_issue) == (
        Relation.OFF_TARGET,
        False,
        False,
    )
    assert (second.relation, second.confidence, second.rationale) == (
        Relation.OUT_OF_CATALOG,
        Confidence.LOW,
        "Not sold here.",
    )
    assert second.needs_adjudication and second.catalog_issue
    assert sum(1 for line in script.said if line.startswith("saved: ")) == 2


def test_borderline_and_low_confidence_require_a_rationale(context, store):
    answers = [
        "b1mnn!",
        "b1mnn! | Partially matches the stated intent",
        "n2lnn!",
        "n2lnn! | x",
        "q",
    ]
    saved, script = _run(context, store, answers)
    assert saved == 2
    assert sum(1 for line in script.said if "rationale is required" in line) == 2
    rationales = sorted(j.rationale for j in _stored(store))
    assert rationales == ["Partially matches the stated intent", "x"]


def test_relevant_with_an_unknown_hard_attribute_requires_a_rationale(context, store, monkeypatch):
    # The synthetic pool has no relevant-eligible pair with an unknown hard attribute, so the
    # rule's input is forced for one pair; the rule itself is the one both modes use.
    index = _index(context, lambda c, i: not hard_violation(c, i))
    target = context.items[index]
    monkeypatch.setattr(cli, "hard_unknown", lambda c, i: i == target)
    answers = [*["n2hnn!"] * index, "r2hnn!", "r2hnn! | Stated intent met.", "q"]
    saved, script = _run(context, store, answers)
    assert saved == index + 1
    assert sum(1 for line in script.said if "rationale is required" in line) == 1
    (match,) = [j for j in _stored(store) if j.product_id == target.product_id]
    assert match.label is Label.RELEVANT and match.rationale == "Stated intent met."


def test_an_optional_rationale_is_kept(context, store):
    index = _index(context, _safe)
    saved, _ = _run(context, store, [*["n2hnn!"] * index, "r1hnn! | Exact model.", "q"])
    assert saved == index + 1
    item = context.items[index]
    (match,) = [j for j in _stored(store) if j.product_id == item.product_id]
    assert match.rationale == "Exact model."


def test_relevant_is_refused_for_a_hard_violation(context, store):
    index = _index(context, hard_violation)
    answers = [*["n2hnn!"] * index, "r1hnn! | x", "n2hnn!", "q"]
    saved, script = _run(context, store, answers)
    assert saved == index + 1
    assert any("relevant is not allowed" in line for line in script.said)
    assert all(j.label is Label.NOT_RELEVANT for j in _stored(store))


def test_q_quits_without_saving_the_open_pair_and_resume_continues(context, store):
    saved, script = _run(context, store, ["n2hnn!", "n2hnn!", "q"])
    assert saved == 2
    assert script.said[-1] == "stopped; 2 judgement(s) saved this session"
    assert len(_stored(store)) == 2
    saved, script = _run(context, store, ["Q"])
    assert saved == 0
    assert script.said[0].startswith(f"2 of {len(context.items)} pairs judged")
    assert any(context.items[2].product_id in line for line in script.said)
    assert len(_stored(store)) == 2


def test_end_of_input_quits_like_q(context, store):
    saved, script = _run(context, store, ["n2hnn!"])
    assert saved == 1
    assert script.said[-1] == cli.INPUT_ENDED_MESSAGE
    assert not store.lock_path.exists()


def test_each_confirmed_entry_is_saved_atomically_before_the_next_prompt(context, store):
    seen: list[int] = []

    class Watching(Script):
        def ask(self, prompt):
            seen.append(len(_stored(store)))
            return super().ask(prompt)

    script = Watching(["n2hnn!", "n1mnn!", "q"])
    cli.annotate(
        context,
        store,
        annotator="ann-1",
        ask=script.ask,
        say=script.say,
        now=lambda: NOW + timedelta(seconds=next(_TICKS)),
        compact=True,
    )
    assert seen == [0, 1, 2]
    assert sorted(p.name for p in store.directory.iterdir()) == [
        BINDING_FILE,
        "primary_judgements.jsonl",
    ]


def test_help_and_codes_show_no_suggestion(context, store):
    saved, script = _run(context, store, ["?", "q"])
    assert saved == 0
    assert script.said.count(cli.compact_codes()) == 2  # session start and `?`
    codes = cli.compact_codes()
    for line in (
        "relation after r: 1 exact_match, 2 satisfies_intent",
        "relation after b: 1 partial, 2 substitute",
        "relation after n: 1 complement, 2 off_target, 3 out_of_catalog",
    ):
        assert line in codes
    shown = "\n".join(script.said + script.prompts).lower()
    words = set(re.findall(r"[a-z0-9_]+", shown))
    for forbidden in ("rank", "score", "suggest", "propos", "default", "eligib", "seller"):
        assert not any(word.startswith(forbidden) for word in words), forbidden


def test_the_flag_is_opt_in_and_the_default_mode_is_unchanged(context, store):
    parser = cli.build_parser()
    assert parser.parse_args(["annotate", "--annotator", "ann-1"]).compact is False
    assert parser.parse_args(["annotate", "--annotator", "ann-1", "--compact"]).compact is True
    saved, script = _run(context, store, ["q"], compact=False)
    assert saved == 0
    assert script.prompts[0].startswith("label [r]elevant")
    assert cli.compact_codes() not in script.said
