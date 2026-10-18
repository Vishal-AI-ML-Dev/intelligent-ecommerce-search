"""`qd-1` schema: queries, gold kinds and the `jq-1` label sets (synthetic fixtures only)."""

import json

import pytest
from evals.query_decisions.contracts import (
    CATEGORY_LABELS,
    INTENT_LABELS,
    NONE_LABEL,
    CategoryGold,
    GoldKind,
    IntentGold,
    QueryDecisionError,
    load_queries,
    queries_sha256,
)
from evals.query_decisions.labeling import parse_gold
from pydantic import ValidationError
from query_decision_support import fixture_query_line

from ecommerce_search.decision import jev_provider


def test_label_sets_are_exactly_the_unchanged_jq1_question_set():
    assert jev_provider.JEV_PROMPT_VERSION == "jq-1"
    assert NONE_LABEL == jev_provider.NONE_LABEL
    assert tuple(jev_provider._CATEGORY_CRITERIA) == CATEGORY_LABELS
    assert tuple(jev_provider._INTENT_CRITERIA) == INTENT_LABELS


def test_queries_load_sorted_and_hash_ignores_formatting():
    lines = [fixture_query_line("dq-002", "b query"), fixture_query_line("dq-001", "a query")]
    queries = load_queries(("\n".join(lines) + "\n").encode())
    assert [q.query_id for q in queries] == ["dq-001", "dq-002"]
    spaced = [json.dumps(json.loads(line), indent=None, separators=(", ", ": ")) for line in lines]
    assert queries_sha256(load_queries("\n".join(spaced).encode())) == queries_sha256(queries)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n",
        ("﻿" + fixture_query_line("dq-001", "a")).encode(),
        (fixture_query_line("dq-001", "a") + "\n" + fixture_query_line("dq-001", "b")).encode(),
        fixture_query_line("dq-001", " padded ").encode(),
        fixture_query_line("dq-001", "two  spaces").encode(),
        fixture_query_line("q-1", "bad id").encode(),
        b'{"query_id": "dq-001", "query_id": "dq-002"}',
    ],
)
def test_invalid_query_files_are_refused(data):
    with pytest.raises(QueryDecisionError):
        load_queries(data)


def test_human_authored_queries_need_an_author_and_synthetic_ones_none():
    record = json.loads(fixture_query_line("dq-001", "a"))
    record["source"] = "human_authored"
    with pytest.raises(QueryDecisionError):
        load_queries(json.dumps(record).encode())
    record = json.loads(fixture_query_line("dq-001", "a"))
    record["author_handle"] = "ann-1"
    with pytest.raises(QueryDecisionError):
        load_queries(json.dumps(record).encode())


def test_none_is_a_label_not_an_abstention():
    gold = parse_gold("none", CategoryGold)
    assert (gold.kind, gold.label, gold.candidates) == (GoldKind.LABEL, "none", ())


def test_candidates_are_stored_in_label_set_order():
    gold = parse_gold("ambiguous:phone,laptop", CategoryGold)
    assert gold.candidates == ("laptop", "phone")
    multi = parse_gold("multi:lightweight,student", IntentGold)
    assert (multi.kind, multi.candidates) == (GoldKind.MULTI, ("student", "lightweight"))


@pytest.mark.parametrize(
    ("text", "gold_type"),
    [
        ("multi:laptop,phone", CategoryGold),  # multi is intent only
        ("ambiguous:laptop", CategoryGold),  # fewer than two candidates
        ("ambiguous:laptop,none", CategoryGold),  # none is never a candidate
        ("ambiguous:laptop,laptop", CategoryGold),
        ("tablet", CategoryGold),
        ("gaming", CategoryGold),
        ("laptop", IntentGold),
        ("maybe:gaming,office", IntentGold),
    ],
)
def test_invalid_gold_answers_are_refused(text, gold_type):
    with pytest.raises((ValueError, ValidationError)):
        parse_gold(text, gold_type)


def test_unclear_carries_nothing():
    assert parse_gold("unclear", IntentGold).model_dump(mode="json") == {
        "kind": "unclear",
        "label": None,
        "candidates": [],
    }
    with pytest.raises(ValidationError):
        IntentGold(kind=GoldKind.UNCLEAR, label="gaming")
