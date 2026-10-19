"""Offline V3-versus-V4 comparison with recorded Jev outcomes (Milestone 12, experimental V4).

Both systems run in one process through the real application (`create_app`, routes
`/search/reranked` and `/search/routed`) against the configured catalog database and local
models, with the same settings, queries, filters and V3 configuration. V4's Jev provider is
replaced by `RecordedJevProvider`, which replays outcomes recorded by the M12 runner
(`runs/<run-id>/journal.jsonl`) and never makes a call; V4 Jev routing is forced on for that
replay only. No key is read for Jev and nothing is written except the exclusive `--out` file.

Query sets:

* `--batch DIR RUN_ID` (repeatable): a sealed M12 batch and one recorded run. These queries have
  category labels but **no relevance judgements**: their rows are `diagnostic_only`. The
  diagnostic compares how many top-K results fall in the sealed single-category gold; that is
  category agreement, not relevance.
* `--golden`: the M9/M10 human-reviewed pilot/dev queries with their frozen final judgements
  (`judged`). Queries on which V4 would ask Jev have no recorded outcome; they are reported as
  `not_compared` (never replayed with a made-up outcome). Judged metrics are `eval-1`
  (`evals/metrics/retrieval.py`) at K = 10, strict; a V4 result without a judgement counts as
  not relevant and is counted as unjudged.

Per query: the routing status, the V3 and V4 top-K ids, whether they differ, the ids that
entered and left the top K, and (judged) NDCG@10 and precision@10 for both with win/loss/tie.
A rank change alone is never reported as an improvement.

Usage:
  uv run --offline python -m evals.runners.compare_v3_v4 \\
      --batch data/processed/m12_diagnostic live-001 \\
      --batch data/processed/m12_calibration_cal1 cal1-live-001 --golden --out FILE
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecommerce_search.api.dense import has_searchable_text
from ecommerce_search.decision.provider import DecisionResult, JevOutcome
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.search.query import normalize_query
from ecommerce_search.search.routed import pre_call_status
from evals.contracts.manifest import Role
from evals.contracts.models import QueryRecord
from evals.metrics.retrieval import query_metrics

COMPARISON_VERSION = "v3v4-cmp-1"
DEFAULT_TOP_K = 20
JUDGED_K = 10


class ComparisonError(Exception):
    pass


@dataclass(frozen=True)
class ComparisonQuery:
    key: str  # unique across sets: "<set>:<query id>"
    text: str
    kind: str  # "judged" or "diagnostic_only"
    outcome: JevOutcome | None = None  # the recorded Jev outcome, if any
    gold_category: str | None = None  # sealed single-category gold (diagnostic only)
    labels: Mapping[str, Any] | None = None  # judged: product id -> final label


class RecordedJevProvider:
    """Replays recorded outcomes by normalized query text; refuses anything unrecorded."""

    def __init__(self, outcomes: Mapping[str, JevOutcome]) -> None:
        self._outcomes = dict(outcomes)
        self.calls: list[str] = []

    def understand(self, query: str, deterministic_result) -> DecisionResult:
        self.calls.append(query)
        if query not in self._outcomes:
            raise ComparisonError("no recorded outcome for a query that V4 asked about")
        return DecisionResult(
            provider="jev",
            provider_version="jq-1",
            understanding=deterministic_result,
            jev=self._outcomes[query],
        )


def batch_queries(work_dir: Path, run_id: str) -> list[ComparisonQuery]:
    from evals.query_decisions.contracts import GoldKind
    from evals.query_decisions.labeling import load_sealed
    from evals.query_decisions.runner import JOURNAL_FILE, RUNS_DIR, read_journal

    seal, queries, labels = load_sealed(work_dir)
    _events, state = read_journal(work_dir / RUNS_DIR / run_id / JOURNAL_FILE)
    gold = {label.query_id: label.category for label in labels}
    name = f"{work_dir.name}/{run_id}"
    out = []
    for query in queries:
        category = gold[query.query_id]
        out.append(
            ComparisonQuery(
                key=f"{name}:{query.query_id}",
                text=query.text,
                kind="diagnostic_only",
                outcome=state.results.get(query.query_id),
                gold_category=category.label if category.kind is GoldKind.LABEL else None,
            )
        )
    return out


def golden_queries() -> list[ComparisonQuery]:
    from evals.contracts.canonical import decode_jsonl
    from evals.runners import build_pool
    from evals.runners.evaluate_human_pilot_dev import load_inputs

    inputs = load_inputs(build_pool.DEFAULT_GOLDEN_DIR)
    pilot = {p.query_id for p in inputs.partitions}
    labels: dict[str, dict[str, Any]] = {}
    for judgement in inputs.finals:
        labels.setdefault(judgement.query_id, {})[judgement.product_id] = judgement.label
    return [
        ComparisonQuery(
            key=f"golden:{q.query_id}", text=q.text, kind="judged", labels=labels[q.query_id]
        )
        for q in decode_jsonl(inputs.files[Role.QUERIES], QueryRecord)
        if q.query_id in pilot
    ]


def _ids(body: Mapping[str, Any]) -> list[str]:
    return [result["product_id"] for result in body["results"]]


def _judged(query: ComparisonQuery, ranked: Sequence[str]) -> dict[str, Any]:
    metrics = query_metrics(query.key, list(ranked), query.labels or {}, None)
    wanted = {
        ("ndcg", JUDGED_K, "none"),
        ("precision", JUDGED_K, "strict"),
        ("unjudged", JUDGED_K, "none"),
    }
    return {
        f"{v.metric}@{v.k}" + ("" if v.definition == "none" else f"_{v.definition}"): v.value
        for v in metrics.values
        if (v.metric, v.k, v.definition) in wanted
    }


def compare_query(client, query: ComparisonQuery, top_k: int, max_query_length: int) -> dict:
    text = normalize_query(query.text, max_query_length)
    row: dict[str, Any] = {"query": query.key, "kind": query.kind}
    asks_jev = (
        pre_call_status(
            parse_normalized_query(text), enabled=True, searchable=has_searchable_text(text)
        )
        is None
    )
    if asks_jev and query.outcome is None:
        row["status"] = "not_compared"
        row["reason"] = "V4 would ask Jev and no recorded outcome exists"
        return row
    v3 = client.get("/search/reranked", params={"q": text, "top_k": top_k})
    v4 = client.get("/search/routed", params={"q": text, "top_k": top_k})
    if v3.status_code != 200 or v4.status_code != 200:
        raise ComparisonError(f"{query.key}: V3 {v3.status_code}, V4 {v4.status_code}")
    v3_body, v4_body = v3.json(), v4.json()
    v3_ids, v4_ids = _ids(v3_body), _ids(v4_body)
    routing = v4_body["jev_routing"]
    row.update(
        status=routing["status"],
        routed_category=routing["routed_category"],
        raw_top_category_probability=routing["raw_top_category_probability"],
        v3_ids=v3_ids,
        v4_ids=v4_ids,
        ranking_changed=v3_ids != v4_ids,
        entered_top_k=[pid for pid in v4_ids if pid not in v3_ids],
        left_top_k=[pid for pid in v3_ids if pid not in v4_ids],
        branch_only_in_v4=sum(not r["in_baseline_sources"] for r in v4_body["results"]),
    )
    if query.kind == "judged":
        row["v3"] = _judged(query, v3_ids)
        row["v4"] = _judged(query, v4_ids)
        a, b = row["v3"]["ndcg@10"], row["v4"]["ndcg@10"]
        row["ndcg@10_outcome"] = (
            "undefined"
            if a is None or b is None
            else "tie"
            if a == b
            else "v4_win"
            if float(b) > float(a)
            else "v4_loss"
        )
    elif query.gold_category not in (None, "none"):
        categories = {
            "v3": [r["category"] for r in v3_body["results"]][:JUDGED_K],
            "v4": [r["category"] for r in v4_body["results"]][:JUDGED_K],
        }
        row["gold_category"] = query.gold_category
        row["gold_category_in_top10"] = {
            name: sum(c == query.gold_category for c in values)
            for name, values in categories.items()
        }
    return row


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def count(kind: str) -> dict[str, Any]:
        mine = [r for r in rows if r["kind"] == kind]
        compared = [r for r in mine if r["status"] != "not_compared"]
        out: dict[str, Any] = {
            "queries": len(mine),
            "not_compared": len(mine) - len(compared),
            "routing_status": dict(sorted(Counter(r["status"] for r in mine).items())),
            "ranking_changed": sum(r["ranking_changed"] for r in compared),
        }
        if kind == "judged":
            out["ndcg@10_outcomes"] = dict(
                sorted(Counter(r["ndcg@10_outcome"] for r in compared).items())
            )
            out["v4_unjudged_in_top10"] = sum(
                int(float(r["v4"]["unjudged@10"] or 0)) for r in compared
            )
        else:
            diag = [r for r in compared if "gold_category_in_top10" in r]
            delta = Counter(
                "more"
                if r["gold_category_in_top10"]["v4"] > r["gold_category_in_top10"]["v3"]
                else "fewer"
                if r["gold_category_in_top10"]["v4"] < r["gold_category_in_top10"]["v3"]
                else "equal"
                for r in diag
            )
            out["single_category_gold_queries"] = len(diag)
            out["v4_vs_v3_gold_category_items_in_top10"] = dict(sorted(delta.items()))
        return out

    return {"judged": count("judged"), "diagnostic_only": count("diagnostic_only")}


def run(batches: Sequence[tuple[Path, str]], golden: bool, top_k: int) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from ecommerce_search.api.app import create_app
    from ecommerce_search.api.dependencies import get_v4_jev_provider
    from ecommerce_search.config import Settings

    settings = Settings(search_v4_jev_enabled=False)  # replay only: no live V4 provider
    queries: list[ComparisonQuery] = []
    for work_dir, run_id in batches:
        queries += batch_queries(work_dir, run_id)
    if golden:
        queries += golden_queries()
    if not queries:
        raise ComparisonError("no queries: give --batch and/or --golden")
    texts = [normalize_query(q.text, settings.search_max_query_length) for q in queries]
    recorded = {t: q.outcome for t, q in zip(texts, queries, strict=True) if q.outcome}
    provider = RecordedJevProvider(recorded)
    app = create_app(settings)
    app.dependency_overrides[get_v4_jev_provider] = lambda: lambda: provider
    with TestClient(app) as client:
        rows = [compare_query(client, q, top_k, settings.search_max_query_length) for q in queries]
    return {
        "comparison_version": COMPARISON_VERSION,
        "experimental": True,
        "note": "rank changes are not relevance improvements; diagnostic_only rows have no "
        "relevance judgements; Jev outcomes are replayed, not new calls",
        "top_k": top_k,
        "configuration": {
            "rrf_k": settings.search_rrf_k,
            "lexical_k": settings.search_lexical_k,
            "dense_k": settings.search_dense_k,
            "candidate_k": settings.search_candidate_k,
            "rerank_depth": settings.search_rerank_depth,
            "reranker_enabled": settings.reranker_enabled,
            "reranker_model": f"{settings.reranker_model_id}@{settings.reranker_model_revision}",
            "embedding_model": f"{settings.embedding_model_id}@{settings.embedding_model_revision}",
        },
        "sources": [{"batch": str(d), "run_id": r} for d, r in batches]
        + ([{"golden": "pilot_dev final judgements"}] if golden else []),
        "replayed_jev_calls": len(provider.calls),
        "summary": summarize(rows),
        "per_query": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.compare_v3_v4")
    parser.add_argument("--batch", action="append", nargs=2, metavar=("DIR", "RUN_ID"), default=[])
    parser.add_argument("--golden", action="store_true")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run([(Path(d), r) for d, r in args.batch], args.golden, args.top_k)
    except (ComparisonError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out is None:
        print(text, end="")
    else:
        with open(args.out, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(json.dumps(report["summary"], indent=2, sort_keys=True))
        print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
