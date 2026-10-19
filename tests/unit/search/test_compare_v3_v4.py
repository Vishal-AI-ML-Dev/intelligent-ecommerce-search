"""Offline V3-versus-V4 comparison runner (fake client and fabricated outcomes; no database,
model, network or Jev call). Product ids and labels are software-test fixtures."""

import pytest
from evals.contracts.constants import Label
from evals.runners.compare_v3_v4 import (
    ComparisonError,
    ComparisonQuery,
    RecordedJevProvider,
    compare_query,
    summarize,
)
from test_routed_api import outcome

from ecommerce_search.query_understanding import parse_normalized_query


class _Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


def _body(ids, categories=None, status="skipped_deterministic_category", baseline=None):
    categories = categories or ["laptop"] * len(ids)
    baseline = baseline or [True] * len(ids)
    return {
        "results": [
            {"product_id": pid, "category": cat, "in_baseline_sources": base}
            for pid, cat, base in zip(ids, categories, baseline, strict=True)
        ],
        "jev_routing": {
            "status": status,
            "routed_category": None,
            "raw_top_category_probability": None,
        },
    }


class FakeClient:
    def __init__(self, v3, v4):
        self.bodies = {"/search/reranked": v3, "/search/routed": v4}
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params["q"]))
        return _Response(self.bodies[path])


def test_the_recorded_provider_replays_and_refuses_unrecorded_queries():
    recorded = outcome("phone")
    provider = RecordedJevProvider({"gaming": recorded})
    result = provider.understand("gaming", parse_normalized_query("gaming"))
    assert result.jev == recorded and result.provider == "jev"
    with pytest.raises(ComparisonError):
        provider.understand("travel", parse_normalized_query("travel"))


def test_a_query_v4_would_ask_about_without_a_recorded_outcome_is_not_compared():
    client = FakeClient(_body([]), _body([]))
    query = ComparisonQuery(key="golden:q1", text="something for travel", kind="judged", labels={})
    row = compare_query(client, query, 20, 200)
    assert row["status"] == "not_compared" and client.calls == []


def test_judged_rows_score_both_systems_and_count_unjudged_v4_results():
    labels = {"A": Label.RELEVANT, "B": Label.NOT_RELEVANT, "C": Label.RELEVANT}
    client = FakeClient(
        _body(["B", "A"]),
        _body(["A", "X"], status="routed", baseline=[True, False]),
    )
    query = ComparisonQuery(
        key="golden:q1", text="gaming", kind="judged", outcome=outcome(), labels=labels
    )
    row = compare_query(client, query, 20, 200)
    assert row["ranking_changed"] is True
    assert (row["entered_top_k"], row["left_top_k"]) == (["X"], ["B"])
    assert row["branch_only_in_v4"] == 1
    assert (float(row["v3"]["unjudged@10"]), float(row["v4"]["unjudged@10"])) == (0, 1)
    assert row["ndcg@10_outcome"] == "v4_win"


def test_diagnostic_rows_compare_gold_category_counts_not_relevance():
    client = FakeClient(
        _body(["L1", "P1"], ["laptop", "phone"]),
        _body(["P1", "P2"], ["phone", "phone"], status="routed"),
    )
    query = ComparisonQuery(
        key="b:dq-001",
        text="gaming",
        kind="diagnostic_only",
        outcome=outcome(),
        gold_category="phone",
    )
    row = compare_query(client, query, 20, 200)
    assert row["gold_category_in_top10"] == {"v3": 1, "v4": 2}
    assert "v3" not in row and "ndcg@10_outcome" not in row
    summary = summarize([row])
    assert summary["diagnostic_only"]["v4_vs_v3_gold_category_items_in_top10"] == {"more": 1}
    assert summary["judged"]["queries"] == 0
