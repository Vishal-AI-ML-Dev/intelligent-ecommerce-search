"""Source invocation for the pool, with a fake in-process client (no database, no model).
Every response body here is SYNTHETIC."""

import socket

import pytest
from evals.contracts.constants import POOL_SOURCE_TOP_K
from evals.contracts.models import SOURCE_ORDER, SourceSystem
from evals.runners import pool_sources as ps

PINS = {
    "embedding_model_id": "sentence-transformers/all-MiniLM-L6-v2",
    "embedding_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "reranker_model_id": "cross-encoder/ms-marco-MiniLM-L6-v2",
    "reranker_revision": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
}
VERSION = {
    "/search": "v0_lexical",
    "/search/dense": "dense_only",
    "/search/hybrid": "v1_hybrid",
    "/search/filtered": "v2_filtered",
    "/search/reranked": "v3_reranked",
}
SCORE = {
    "/search": "lexical_score",
    "/search/dense": "dense_score",
    "/search/hybrid": "rrf_score",
    "/search/filtered": "rrf_score",
    "/search/reranked": "rerank_score",
}


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeClient:
    """Records every call and answers with a contract-shaped SYNTHETIC body."""

    def __init__(self, app: str, calls: list, *, depth: int = 20, status: int = 200, **tweak):
        self.app, self.calls, self.depth, self.status, self.tweak = app, calls, depth, status, tweak

    def get(self, path: str, params: dict) -> FakeResponse:
        self.calls.append((self.app, path, dict(params)))
        n = 3
        body = {
            "search_version": VERSION[path],
            "top_k": params["top_k"],
            "result_count": n,
            "results": [
                {"rank": i + 1, "product_id": f"SYN-LAP-000{i + 1}", SCORE[path]: 1.5 - i}
                for i in range(n)
            ],
            "applied_filters": [],
        }
        if path != "/search":
            body["embedding_model_id"] = PINS["embedding_model_id"]
            body["embedding_model_revision"] = PINS["embedding_revision"]
        if path in ("/search/hybrid", "/search/filtered", "/search/reranked"):
            body["query_understanding"] = {"provider_version": "qu-1"}
            body["dense_status"] = "used"
        if path in ("/search/filtered", "/search/reranked"):
            body["filter_policy"] = {"version": "fp-1"}
            body["applied_filters"] = [{"field": "category", "value": "laptop"}]
            body["ignored_constraints"] = []
        if path == "/search/reranked":
            body["reranker"] = {
                "enabled": True,
                "status": "applied",
                "model_id": PINS["reranker_model_id"],
                "model_revision": PINS["reranker_revision"],
                "rerank_depth": self.depth,
                "reranked_count": n,
            }
        body.update(self.tweak)
        return FakeResponse(self.status, body)


def _clients(calls, **overrides):
    clients = {
        "base": FakeClient("base", calls),
        "d10": FakeClient("d10", calls, depth=10),
        "d20": FakeClient("d20", calls, depth=20),
    }
    clients.update(overrides)
    return clients


def test_every_source_is_invoked_with_top_k_20():
    calls: list = []
    fetched = ps.fetch_sources(_clients(calls), [("gq-p01", "headphones")], PINS)
    assert [f.run.system for f in fetched] == list(SOURCE_ORDER)
    assert calls == [
        ("base", "/search", {"q": "headphones", "top_k": 20}),
        ("base", "/search/dense", {"q": "headphones", "top_k": 20}),
        ("base", "/search/hybrid", {"q": "headphones", "top_k": 20}),
        ("base", "/search/filtered", {"q": "headphones", "top_k": 20}),
        ("d10", "/search/reranked", {"q": "headphones", "top_k": 20}),
        ("d20", "/search/reranked", {"q": "headphones", "top_k": 20}),
    ]
    assert POOL_SOURCE_TOP_K == 20
    by_system = {f.run.system: f for f in fetched}
    assert by_system[SourceSystem.V3_RERANKED_D10].run.reranker.depth == 10
    assert by_system[SourceSystem.V3_RERANKED_D20].run.reranker.depth == 20
    assert by_system[SourceSystem.V0_LEXICAL].run.reranker is None
    assert by_system[SourceSystem.V2_FILTERED].run.applied_filters == (
        '{"field":"category","value":"laptop"}',
    )
    assert by_system[SourceSystem.V2_FILTERED].run.parser_version == "qu-1"
    assert by_system[SourceSystem.V0_LEXICAL].scores == ("1.5", "0.5", "-0.5")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base": FakeClient("base", [], status=503)}, "status 503"),
        ({"base": FakeClient("base", [], top_k=10)}, "top_k"),
        ({"d10": FakeClient("d10", [], depth=20)}, "reranker mode"),
        ({"base": FakeClient("base", [], search_version="other")}, "search_version"),
        ({"base": FakeClient("base", [], embedding_model_revision="x")}, "embedding model"),
        ({"base": FakeClient("base", [], result_count=7)}, "inconsistent"),
    ],
)
def test_a_failed_or_foreign_response_aborts_the_run(overrides, message):
    with pytest.raises(ps.SourceError, match=message):
        ps.fetch_sources(_clients([], **overrides), [("gq-p01", "headphones")], PINS)


def test_non_finite_scores_are_refused():
    with pytest.raises(ps.SourceError, match="finite"):
        ps._score(float("nan"), "x")
    assert ps._score(None, "x") is None
    assert ps._score(1e-05, "x") == "1e-05"


class _Settings:
    def __init__(self, **overrides) -> None:
        for name, value in ps.APPROVED_SETTINGS.items():
            setattr(self, name, overrides.get(name, value))


def test_only_the_approved_settings_are_accepted():
    names = [v.name for v in ps.check_settings(_Settings())]
    assert names == sorted(names) and "embedding_models_dir" not in names
    with pytest.raises(ps.SourceError, match="search_rrf_k"):
        ps.check_settings(_Settings(search_rrf_k=60))


def test_scratch_database_names_are_enforced():
    name = ps.new_scratch_name()
    assert ps.assert_scratch_name(name, "ecommerce_search") == name
    for bad in ("ecommerce_search", "ecommerce_search_bench_xyz", "postgres"):
        with pytest.raises(ps.SourceError):
            ps.assert_scratch_name(bad, "ecommerce_search")


def test_a_dirty_runtime_tree_is_refused():
    def dirty_git(*args):
        return " M src/ecommerce_search/api/app.py" if args[0] == "status" else "a" * 40

    with pytest.raises(ps.SourceError, match="runtime paths differ"):
        ps.runtime_trees(dirty_git)

    def clean_git(*args):
        return "" if args[0] == "status" else "b" * 40

    trees = ps.runtime_trees(clean_git)
    assert [t.name for t in trees] == sorted(ps.RUNTIME_PATHS)


def test_the_network_guard_refuses_outbound_hosts(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    for name in ps.OFFLINE_ENV:
        monkeypatch.setenv(name, "0")
    ps.install_network_guard()
    with pytest.raises(OSError, match="network access is disabled"):
        socket.getaddrinfo("huggingface.co", 443)
    with pytest.raises(OSError, match="network access is disabled"):
        socket.create_connection(("192.0.2.1", 443))
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
