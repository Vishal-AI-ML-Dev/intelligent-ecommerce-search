"""`GET/POST /search/reranked` (Milestone 8, V3) with a fake embedder, a fake reranker, an
instrumented fake session and fake filtered source reads (no database, no model, no network).

The fake filtered source reads are the same kind as the V2 unit tests use: they return only
products of a small in-test catalog that satisfy the `FilterSpec` they receive. The fake session
answers the candidate-record statements from the same catalog's raw records, so the real
`read_reranked_sources`, record rebuild, `rd-1` documents, score validation and head reranking
run. The fake reranker (tests/fake_reranker.py) only exercises plumbing; nothing here measures
the quality of any model. Hand-written expectations use explicit per-product scores.
"""

import hashlib
import io
import json
import logging
import math
import os
import subprocess
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

from ecommerce_search.api import app as app_module
from ecommerce_search.api import dense as dense_api
from ecommerce_search.api import reranked as reranked_api
from ecommerce_search.api import search as search_api
from ecommerce_search.api.app import create_app
from ecommerce_search.api.dependencies import (
    get_db_session,
    get_decision_provider,
    get_embedder,
    get_reranker,
)
from ecommerce_search.api.observability import build_log_handler
from ecommerce_search.api.schemas import RerankedSearchResponse
from ecommerce_search.catalog.mapping import spec_columns
from ecommerce_search.catalog.schemas import CORE_KEYS, parse_raw
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.config import Settings
from ecommerce_search.decision import DeterministicDecisionProvider
from ecommerce_search.embeddings.provider import EmbedderUnavailable
from ecommerce_search.embeddings.sentence_transformers_provider import (
    SentenceTransformerEmbedder,
)
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.reranking import cross_encoder_provider
from ecommerce_search.reranking.cross_encoder_provider import CrossEncoderReranker
from ecommerce_search.reranking.documents import RerankDocumentError, build_rerank_document
from ecommerce_search.reranking.provider import RerankerUnavailable
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2
from ecommerce_search.search import filtered as filtered_search
from ecommerce_search.search import hybrid as hybrid_search
from ecommerce_search.search import reranked as reranked_search
from ecommerce_search.search.dense import DenseHit, DenseResult
from ecommerce_search.search.hybrid import SNAPSHOT_SQL
from ecommerce_search.search.lexical import LexicalHit, LexicalResult
from fake_embedder import FakeEmbedder
from fake_reranker import FakeReranker, overlap_score

SENSITIVE = "host=internal-db.example password=hunter2 SELECT * FROM products D:\\secret\\path"
FIXED_503 = {"detail": "search unavailable"}
SECRETS = ("hunter2", "internal-db", "SELECT", "secret\\path", "Traceback")
PATH = "/search/reranked"

# ---- in-test catalog (valid raw records) ---------------------------------------------------------

_CORE = {
    "seller_id": "test-seller",
    "description": "Test product.",
    "subcategory": None,
    "currency": "INR",
    "rating": Decimal("4.2"),
    "review_count": 3,
    "availability": "in_stock",
    "source_type": "synthetic",
    "is_synthetic": True,
}


def _raw(pid, category, brand, price, title, **spec):
    return {
        **_CORE,
        "product_id": pid,
        "title": title,
        "category": category,
        "brand": brand,
        "price": Decimal(price),
        **spec,
    }


LAPTOP_SSD = {"storage_type": "SSD"}
CATALOG = (
    _raw(
        "L1",
        "laptop",
        "HP",
        "33999.00",
        "HP Pavilion Laptop",
        ram_gb=8,
        storage_gb=256,
        storage_interface="SATA",
        **LAPTOP_SSD,
    ),
    _raw(
        "L2",
        "laptop",
        "Dell",
        "55999.00",
        "Dell Inspiron Gaming Laptop",
        ram_gb=16,
        storage_gb=512,
        storage_interface="NVME",
        **LAPTOP_SSD,
    ),
    _raw(
        "L3",
        "laptop",
        "HP",
        "28999.00",
        "HP Stream Laptop",
        ram_gb=8,
        storage_gb=1024,
        storage_type="HDD",
    ),
    _raw(
        "L4",
        "laptop",
        "HP",
        "38999.00",
        "HP Victus Gaming Laptop",
        ram_gb=8,
        storage_gb=256,
        storage_interface="NVME",
        **LAPTOP_SSD,
    ),
    _raw("P1", "phone", "Samsung", "26999.00", "Samsung Galaxy Phone", ram_gb=8, storage_gb=256),
    _raw("P2", "phone", "Apple", "79999.00", "Apple iPhone", ram_gb=6, storage_gb=128),
    _raw("S1", "shoes", "Nike", "4299.00", "Nike Revolution Running Shoes"),
    _raw(
        "H1",
        "headphones",
        "Sony",
        "1999.00",
        "Sony Wireless Headphones",
        wireless=True,
        connectivity="bluetooth",
    ),
)
BY_ID = {raw["product_id"]: raw for raw in CATALOG}
TITLE_TO_ID = {raw["title"]: raw["product_id"] for raw in CATALOG}
NOT_LEXICAL = {"H1"}  # the fake lexical source never matches it, so overlap < union


def _plain(value):
    return value.value if hasattr(value, "value") else value


def satisfies(raw: dict, filters: FilterSpec) -> bool:
    """fp-1 eligibility over the in-test catalog (independent of the production SQL)."""
    checks = {
        "category": lambda v: raw["category"] == v,
        "brand": lambda v: raw["brand"] == v,
        "ram_gb": lambda v: raw.get("ram_gb") == v,
        "storage_gb": lambda v: raw.get("storage_gb") == v,
        "storage_type": lambda v: raw.get("storage_type") == v,
        "storage_interface": lambda v: raw.get("storage_interface") == v,
        "min_price": lambda v: raw["price"] >= v,
        "max_price": lambda v: raw["price"] <= v,
    }
    return all(
        check(_plain(getattr(filters, name)))
        for name, check in checks.items()
        if getattr(filters, name) is not None
    )


def _display(raw: dict) -> dict:
    return {
        "product_id": raw["product_id"],
        "title": raw["title"],
        "brand": raw["brand"],
        "category": raw["category"],
        "subcategory": None,
        "description": raw["description"],
        "price": raw["price"],
        "currency": "INR",
        "rating": raw["rating"],
        "review_count": 3,
        "availability": "in_stock",
    }


def lexical_hits_for(filters: FilterSpec, limit: int) -> list[LexicalHit]:
    eligible = [r for r in CATALOG if satisfies(r, filters) and r["product_id"] not in NOT_LEXICAL]
    return [
        LexicalHit(rank=n, lexical_score=1.0 / n, **_display(r))
        for n, r in enumerate(eligible[:limit], start=1)
    ]


def dense_hits_for(filters: FilterSpec, limit: int) -> list[DenseHit]:
    eligible = [r for r in reversed(CATALOG) if satisfies(r, filters)]
    return [
        DenseHit(rank=n, dense_score=1.0 - n / 100, **_display(r))
        for n, r in enumerate(eligible[:limit], start=1)
    ]


def product_row(raw: dict) -> SimpleNamespace:
    return SimpleNamespace(**{name: raw.get(name) for name in CORE_KEYS})


def spec_row(raw: dict) -> SimpleNamespace:
    values = {name: raw.get(name) for name in spec_columns(Category(raw["category"]))}
    return SimpleNamespace(product_id=raw["product_id"], category=raw["category"], **values)


def document_of(pid: str) -> str:
    """The `rd-1` text of an in-test product, built independently of the route."""
    return build_rerank_document(parse_raw(dict(BY_ID[pid])))


# ---- fakes ---------------------------------------------------------------------------------------

STATEMENT_NAMES = {
    id(SNAPSHOT_SQL): "snapshot",
    id(reranked_search.CANDIDATE_PRODUCTS_SQL): "products",
    **{id(stmt): f"spec:{cat.value}" for cat, stmt in reranked_search.CANDIDATE_SPEC_SQL.items()},
}


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class FakeSession:
    """Transaction events, the snapshot statement and the candidate-record statements."""

    def __init__(self, events: list, errors: dict | None = None, products=None) -> None:
        self.events = events
        self.errors = errors or {}
        self.products = products  # optional callable(ids) -> product rows
        self._open = False
        self.closed = False

    def in_transaction(self) -> bool:
        return self._open

    @contextmanager
    def begin(self):
        assert not self._open, "nested transaction"
        self.events.append("begin")
        self._open = True
        try:
            yield self
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")
        finally:
            self._open = False

    def execute(self, statement, params=None):
        assert self._open, "statement outside the transaction"
        name = STATEMENT_NAMES.get(id(statement), "other")
        self.events.append(("execute", name))
        if name in self.errors:
            raise self.errors[name]
        if name in ("snapshot", "other"):
            return None
        ids = params["product_ids"]
        if name == "products":
            if self.products is not None:
                return _Result(self.products(ids))
            return _Result(product_row(BY_ID[pid]) for pid in ids)
        category = name.split(":", 1)[1]
        return _Result(spec_row(BY_ID[pid]) for pid in ids if BY_ID[pid]["category"] == category)

    def connection(self, *args, **kwargs):
        raise AssertionError("the reranked route never asks the session for a connection")

    def close(self) -> None:
        self.closed = True
        self.events.append("close")


class RecordingEmbedder(FakeEmbedder):
    def __init__(self, events: list, sessions: list) -> None:
        super().__init__()
        self.events = events
        self.sessions = sessions

    def _in_tx(self) -> bool:
        return any(session.in_transaction() for session in self.sessions)

    def load(self):
        self.events.append(("load", self._in_tx()))
        return super().load()

    def embed_query(self, text):
        self.events.append(("embed", self._in_tx()))
        return super().embed_query(text)


class RecordingReranker(FakeReranker):
    """Records every reranker call and whether a transaction was open at that moment."""

    def __init__(self, events: list, sessions: list, spec=MS_MARCO_MINILM_L6_V2) -> None:
        super().__init__(spec)
        self.events = events
        self.sessions = sessions

    def _in_tx(self) -> bool:
        return any(session.in_transaction() for session in self.sessions)

    def load(self):
        self.events.append(("rr-load", self._in_tx()))
        return super().load()

    def count_pair_tokens(self, query, documents):
        self.events.append(("rr-count", self._in_tx()))
        return super().count_pair_tokens(query, documents)

    def score(self, query, documents):
        self.events.append(("rr-score", self._in_tx()))
        return super().score(query, documents)


def scores_by_id(mapping: dict) -> object:
    """A `score_output` that returns `mapping[product id]` for each document (by its title)."""

    def output(query, texts):
        return [mapping[TITLE_TO_ID[text.split("\n", 1)[0]]] for text in texts]

    return output


@pytest.fixture
def events():
    return []


@pytest.fixture
def client_for(make_settings, monkeypatch, events):
    def _make(
        embedder="recording",
        reranker="recording",
        provider=None,
        errors=None,
        products=None,
        lexical_error=None,
        dense_error=None,
        **settings,
    ):
        sessions: list = []
        fake = RecordingEmbedder(events, sessions) if embedder == "recording" else embedder
        rr = RecordingReranker(events, sessions) if reranker == "recording" else reranker

        def fake_lexical(session, query, filters, limit):
            assert session.in_transaction() and type(filters) is FilterSpec
            events.append(("lexical", query, filters, limit))
            if lexical_error is not None:
                raise lexical_error
            return LexicalResult(
                tsquery="'x'", hits=lexical_hits_for(filters, limit), lexical_ms=1.5
            )

        def fake_dense(session, vector, spec, filters, limit):
            assert session.in_transaction() and type(filters) is FilterSpec
            events.append(("dense", len(vector), filters, limit))
            if dense_error is not None:
                raise dense_error
            return DenseResult(hits=dense_hits_for(filters, limit), vector_ms=2.5)

        monkeypatch.setattr(filtered_search, "filtered_lexical_search", fake_lexical)
        monkeypatch.setattr(filtered_search, "filtered_dense_search", fake_dense)

        def session_dependency():
            session = FakeSession(events, errors, products)
            sessions.append(session)
            try:
                yield session
            finally:
                session.close()

        app = create_app(make_settings(**settings))
        app.dependency_overrides[get_db_session] = session_dependency
        app.dependency_overrides[get_embedder] = lambda: fake
        app.dependency_overrides[get_reranker] = lambda: rr
        if provider is not None:
            app.dependency_overrides[get_decision_provider] = lambda: provider
        client = TestClient(app)
        client.fake = fake
        client.reranker = rr
        client.sessions = sessions
        return client

    return _make


def names(events: list) -> list[str]:
    out = []
    for event in events:
        if isinstance(event, str):
            out.append(event)
        elif event[0] == "execute":
            out.append(f"execute:{event[1]}")
        else:
            out.append(event[0])
    return out


def call(client, method: str, query: str, top_k: int | None = None, path: str = PATH):
    if method == "get":
        params = {"q": query} | ({} if top_k is None else {"top_k": top_k})
        return client.get(path, params=params)
    body = {"query": query} | ({} if top_k is None else {"top_k": top_k})
    return client.post(path, json=body)


def without_latency(payload: dict) -> dict:
    payload = dict(payload)
    payload.pop("latency_ms")
    return payload


SPIED = (
    "normalize_query",
    "understand_normalized_query",
    "derive_filters",
    "encode_query",
    "read_reranked_sources",
    "read_filtered_sources",
    "fuse_rrf",
    "build_rerank_documents",
    "score_documents",
    "rerank_head",
)
RETRIEVAL_SIDE = (
    "encode_query",
    "read_reranked_sources",
    "read_filtered_sources",
    "fuse_rrf",
    "build_rerank_documents",
    "score_documents",
    "rerank_head",
)


def install_spies(monkeypatch, events: list, targets=SPIED) -> list:
    records: list = []
    for name in targets:
        original = getattr(reranked_api, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            events.append(f"call:{_name}")
            result = _original(*args, **kwargs)
            records.append((_name, args, kwargs, result))
            return result

        monkeypatch.setattr(reranked_api, name, spy)
    return records


def by_name(records: list, name: str) -> list:
    return [record for record in records if record[0] == name]


V3_RESULT_KEYS = ("rrf_rank", "rerank_rank", "rerank_score")


def v2_view(v3: dict) -> dict:
    """A V3 body without its V3-only additions (search version, reranker block, V3 latency and
    per-result rank fields): what must equal the V2 body for the same request."""
    body = without_latency(v3)
    body.pop("search_version")
    body.pop("reranker")
    body["results"] = [
        {key: value for key, value in result.items() if key not in V3_RESULT_KEYS}
        for result in body["results"]
    ]
    return body


def v2_body(v2: dict) -> dict:
    body = without_latency(v2)
    body.pop("search_version")
    return body


def ids(body: dict) -> list[str]:
    return [result["product_id"] for result in body["results"]]


QUERIES = {
    "no-filters": "comfortable gift",
    "category": "gaming laptop",
    "brand": "hp",
    "combined": "hp laptop 8gb ram 256gb ssd under 40k",
    "conflict-storage": "nvme hdd laptop",
    "conflict-brand": "hp dell laptop",
    "ambiguity-ram": "laptop 8gb 16gb ram",
    "informational-sasta": "sasta phone",
    "zero-results": "nike laptop",
    "punctuation": "!!!",
}
QUERY_PARAMS = pytest.mark.parametrize("query", QUERIES.values(), ids=QUERIES.keys())


# ---- 1. success families: GET/POST parity, V2 filters, applied reranking ------------------------


@QUERY_PARAMS
@pytest.mark.parametrize("enabled", [True, False])
def test_get_and_post_agree_and_filters_match_v2(client_for, query, enabled):
    with client_for(reranker_enabled=enabled) as client:
        got = call(client, "get", query, 50)
        posted = call(client, "post", query, 50)
        v2 = call(client, "get", query, 50, path="/search/filtered")
    assert got.status_code == posted.status_code == v2.status_code == 200
    assert without_latency(got.json()) == without_latency(posted.json())
    body, v2_json = got.json(), v2.json()
    RerankedSearchResponse.model_validate(body)
    assert body["search_version"] == "v3_reranked"
    for key in ("filter_policy", "applied_filters", "ignored_constraints", "query_understanding"):
        assert body[key] == v2_json[key], key
    for key in ("fused_count", "candidate_count", "lexical_hit_count", "dense_hit_count"):
        assert body[key] == v2_json[key], key
    assert sorted(ids(body)) == sorted(ids(v2_json))  # same candidate set


def test_filters_conflicts_ambiguities_are_reported_as_in_v2(client_for):
    with client_for() as client:
        bodies = {key: call(client, "get", q).json() for key, q in QUERIES.items()}
    assert bodies["category"]["applied_filters"] == [
        {"field": "category", "operator": "eq", "value": "laptop"}
    ]
    assert bodies["category"]["ignored_constraints"] == [
        {"field": "semantic_intent", "reason": "informational_only"}
    ]
    assert {"field": "brand", "reason": "conflict"} in bodies["conflict-brand"][
        "ignored_constraints"
    ]
    assert {"field": "ram_gb", "reason": "ambiguous_family"} in bodies["ambiguity-ram"][
        "ignored_constraints"
    ]
    assert bodies["zero-results"]["results"] == [] and bodies["zero-results"]["applied_filters"]


# "gaming laptop": category=laptop. Lexical L1, L2, L3, L4; dense L4, L3, L2, L1 (rrf_k 100):
# L1 = L4 = 1/101 + 1/104 > L2 = L3 = 1/102 + 1/103, ties by id -> RRF order L1, L4, L2, L3.
RRF_ORDER = ["L1", "L4", "L2", "L3"]
HAND_SCORES = {"L1": 0.5, "L2": 3.0, "L3": -1.0, "L4": 3.0}


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("depth", "expected_ids", "expected_rrf", "expected_rerank", "expected_scores"),
    [
        # all four scored: 3.0 (rrf 2), 3.0 (rrf 3), 0.5, -1.0
        (20, ["L4", "L2", "L1", "L3"], [2, 3, 1, 4], [1, 2, 3, 4], [3.0, 3.0, 0.5, -1.0]),
        (4, ["L4", "L2", "L1", "L3"], [2, 3, 1, 4], [1, 2, 3, 4], [3.0, 3.0, 0.5, -1.0]),
        # head L1, L4 scored and swapped; tail L2, L3 unchanged
        (2, ["L4", "L1", "L2", "L3"], [2, 1, 3, 4], [1, 2, None, None], [3.0, 0.5, None, None]),
        # head of one: unchanged order, only the first is scored
        (1, ["L1", "L4", "L2", "L3"], [1, 2, 3, 4], [1, None, None, None], [0.5, None, None, None]),
    ],
)
def test_hand_written_rerank_orders(
    client_for, method, depth, expected_ids, expected_rrf, expected_rerank, expected_scores
):
    with client_for(search_rerank_depth=depth) as client:
        client.reranker.score_output = scores_by_id(HAND_SCORES)
        v2 = call(client, "get", "gaming laptop", path="/search/filtered").json()
        response = call(client, method, "gaming laptop")
    assert ids(v2) == RRF_ORDER
    assert response.status_code == 200
    body = response.json()
    assert ids(body) == expected_ids
    assert [r["rank"] for r in body["results"]] == [1, 2, 3, 4]
    assert [r["rrf_rank"] for r in body["results"]] == expected_rrf
    assert [r["rerank_rank"] for r in body["results"]] == expected_rerank
    assert [r["rerank_score"] for r in body["results"]] == expected_scores
    assert body["reranker"] == {
        "enabled": True,
        "status": "applied",
        "model_id": MS_MARCO_MINILM_L6_V2.model_id,
        "model_revision": MS_MARCO_MINILM_L6_V2.revision,
        "document_version": "rd-1",
        "max_length": 512,
        "rerank_depth": depth,
        "reranked_count": min(depth, 4),
        "score_type": "logit",
    }
    # RRF and source ranks/scores travel unchanged with each product.
    v2_by_id = {r["product_id"]: r for r in v2["results"]}
    for result in body["results"]:
        expected = v2_by_id[result["product_id"]]
        for key in expected:
            if key != "rank":
                assert result[key] == expected[key], key
        assert ids(v2)[result["rrf_rank"] - 1] == result["product_id"]


def test_default_fake_scores_follow_the_rd1_documents(client_for):
    with client_for() as client:
        body = call(client, "get", "gaming laptop").json()
        (count_call,) = client.reranker.count_calls
        (score_call,) = client.reranker.score_calls
    texts = [document_of(pid) for pid in RRF_ORDER]
    assert count_call == ("gaming laptop", texts) and score_call == ("gaming laptop", texts)
    expected = sorted(
        (
            (overlap_score("gaming laptop", text), rank, pid)
            for rank, (pid, text) in enumerate(zip(RRF_ORDER, texts, strict=True), start=1)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    assert ids(body) == [pid for _score, _rank, pid in expected]
    assert [r["rerank_score"] for r in body["results"]] == [s for s, _r, _p in expected]


@pytest.mark.parametrize("method", ["get", "post"])
def test_equal_scores_keep_rrf_order(client_for, method):
    with client_for() as client:
        client.reranker.score_output = lambda q, texts: [1.25] * len(texts)
        v2 = call(client, "get", "comfortable gift", 50, path="/search/filtered").json()
        body = call(client, method, "comfortable gift", 50).json()
    assert ids(body) == ids(v2) and len(ids(body)) == len(CATALOG)
    n = len(CATALOG)
    assert [r["rrf_rank"] for r in body["results"]] == list(range(1, n + 1))
    assert [r["rerank_rank"] for r in body["results"]] == list(range(1, n + 1))
    assert all(r["rerank_score"] == 1.25 for r in body["results"])


MATRIX_SCORES = {
    "L1": 2.0,
    "L2": -0.5,
    "L3": 2.0,
    "L4": 7.5,
    "P1": 0.0,
    "P2": -3.0,
    "S1": 7.5,
    "H1": 1.0,
}


@pytest.mark.parametrize("depth", [1, 3, 8, 20])
@pytest.mark.parametrize("top_k", [1, 3, 5, 8, 20])
def test_depth_and_top_k_matrix(client_for, depth, top_k):
    with client_for(search_rerank_depth=depth) as client:
        client.reranker.score_output = scores_by_id(MATRIX_SCORES)
        rrf = ids(call(client, "get", "comfortable gift", 50, path="/search/filtered").json())
        body = call(client, "post", "comfortable gift", top_k).json()
    size = min(depth, len(rrf))
    head = sorted(
        ((MATRIX_SCORES[pid], rank, pid) for rank, pid in enumerate(rrf[:size], start=1)),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    expected = [(pid, rank, n, score) for n, (score, rank, pid) in enumerate(head, start=1)]
    expected += [(pid, rank, None, None) for rank, pid in enumerate(rrf[size:], start=size + 1)]
    expected = expected[:top_k]
    got = [
        (r["product_id"], r["rrf_rank"], r["rerank_rank"], r["rerank_score"])
        for r in body["results"]
    ]
    assert got == expected
    assert body["result_count"] == len(expected) == min(top_k, len(rrf))
    assert body["candidate_count"] == len(rrf) and body["top_k"] == top_k
    assert body["reranker"]["reranked_count"] == size
    assert body["reranker"]["rerank_depth"] == depth


def test_top_k_above_depth_exposes_a_null_scored_tail(client_for):
    with client_for(search_rerank_depth=2) as client:
        body = call(client, "get", "comfortable gift", 6).json()
    tail = body["results"][2:]
    assert len(tail) == 4
    assert all(r["rerank_rank"] is None and r["rerank_score"] is None for r in tail)
    assert [r["rrf_rank"] for r in tail] == [3, 4, 5, 6]
    assert all(r["rrf_score"] > 0 for r in body["results"])


# ---- 2. disabled, zero candidates, punctuation ----------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("query", ["gaming laptop", "comfortable gift", "hp dell laptop"])
def test_disabled_mode_is_the_exact_v2_path_and_order(
    client_for, events, monkeypatch, method, query
):
    with client_for(reranker_enabled=False) as client:
        v2 = call(client, method, query, 50, path="/search/filtered").json()
        events.clear()
        records = install_spies(monkeypatch, events)
        response = call(client, method, query, 50)
    assert response.status_code == 200
    body = response.json()
    assert v2_view(body) == v2_body(v2)
    assert body["reranker"] == {
        "enabled": False,
        "status": "disabled",
        "model_id": MS_MARCO_MINILM_L6_V2.model_id,
        "model_revision": MS_MARCO_MINILM_L6_V2.revision,
        "document_version": "rd-1",
        "max_length": 512,
        "rerank_depth": 20,
        "reranked_count": 0,
        "score_type": "logit",
    }
    n = len(body["results"])
    assert [r["rrf_rank"] for r in body["results"]] == list(range(1, n + 1))
    assert all(r["rerank_rank"] is None and r["rerank_score"] is None for r in body["results"])
    assert len(by_name(records, "read_filtered_sources")) == 1
    for name in ("read_reranked_sources", "build_rerank_documents", "score_documents"):
        assert by_name(records, name) == [], name
    assert by_name(records, "rerank_head") == []
    assert "execute:products" not in names(events)
    assert not any(name.startswith("rr-") for name in names(events))
    reranker = client.reranker
    assert reranker.load_calls == 0 and not reranker.count_calls and not reranker.score_calls
    latency = body["latency_ms"]
    for key in ("document_read_ms", "reranker_load_ms", "rerank_document_ms", "reranking_ms"):
        assert latency[key] is None, key
    assert latency["rrf_ms"] is not None and latency["lexical_ms"] == 1.5


def test_disabled_mode_needs_no_reranker_at_all(client_for):
    with client_for(reranker_enabled=False, reranker=None) as client:
        response = call(client, "get", "gaming laptop")
    assert response.status_code == 200 and response.json()["reranker"]["status"] == "disabled"


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("enabled", [True, False])
def test_zero_candidates_is_a_200_without_record_read_or_reranker(
    client_for, events, monkeypatch, method, enabled
):
    with client_for(reranker_enabled=enabled) as client:
        records = install_spies(monkeypatch, events)
        response = call(client, method, "nike laptop")
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [] and body["candidate_count"] == 0 and body["result_count"] == 0
    assert body["applied_filters"] == [
        {"field": "category", "operator": "eq", "value": "laptop"},
        {"field": "brand", "operator": "eq", "value": "Nike"},
    ]
    assert body["reranker"]["status"] == ("skipped_no_candidates" if enabled else "disabled")
    assert body["reranker"]["reranked_count"] == 0
    assert "execute:products" not in names(events)
    assert not any(name.startswith("rr-") for name in names(events))
    assert len(by_name(records, "fuse_rrf")) == 1
    for name in ("build_rerank_documents", "score_documents", "rerank_head"):
        assert by_name(records, name) == [], name
    latency = body["latency_ms"]
    for key in ("document_read_ms", "reranker_load_ms", "rerank_document_ms", "reranking_ms"):
        assert latency[key] is None, key
    assert latency["rrf_ms"] is not None


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("query", ["!!!", "-", "...  ???", "\u2014"])
def test_punctuation_runs_no_model_database_or_reranker(
    client_for, events, monkeypatch, method, enabled, query
):
    with client_for(reranker_enabled=enabled) as client:
        records = install_spies(monkeypatch, events)
        response = call(client, method, query)
    assert response.status_code == 200
    assert names(events) == [
        "call:normalize_query",
        "call:understand_normalized_query",
        "call:derive_filters",
        "close",
    ]
    assert {name for name, *_ in records} == {
        "normalize_query",
        "understand_normalized_query",
        "derive_filters",
    }
    assert client.fake.load_calls == 0 and client.reranker.load_calls == 0
    body = response.json()
    assert body["results"] == [] and body["tsquery"] is None
    assert body["dense_status"] == "skipped_no_searchable_text"
    assert body["reranker"]["status"] == "skipped_no_searchable_text"
    assert body["reranker"]["enabled"] is enabled and body["reranker"]["reranked_count"] == 0
    latency = body["latency_ms"]
    executed = ("total_ms", "query_understanding_ms", "filter_translation_ms")
    assert all(value is None for key, value in latency.items() if key not in executed)


def test_latency_stages_are_reported_only_when_they_ran(client_for):
    with client_for() as client:
        first = call(client, "get", "gaming laptop").json()["latency_ms"]
        second = call(client, "post", "gaming laptop").json()["latency_ms"]
    for latency in (first, second):
        for key in ("document_read_ms", "rerank_document_ms", "reranking_ms", "rrf_ms"):
            assert latency[key] is not None and latency[key] >= 0, key
        assert latency["total_ms"] >= latency["query_understanding_ms"]
    assert first["reranker_load_ms"] == 1.0  # this request loaded the (fake) model
    assert second["reranker_load_ms"] is None  # already loaded
    assert set(first) == {
        "lexical_ms",
        "model_load_ms",
        "query_embedding_ms",
        "vector_ms",
        "rrf_ms",
        "query_understanding_ms",
        "filter_translation_ms",
        "document_read_ms",
        "reranker_load_ms",
        "rerank_document_ms",
        "reranking_ms",
        "total_ms",
    }


# ---- 3. call order, cardinality and inputs -------------------------------------------------------


MODEL_EVENTS = ("load", "embed", "rr-load", "rr-count", "rr-score")


def test_exact_call_order_enabled(client_for, events, monkeypatch):
    with client_for() as client:
        install_spies(monkeypatch, events)
        assert call(client, "get", "gaming laptop").status_code == 200
    assert names(events) == [
        "call:normalize_query",
        "call:understand_normalized_query",
        "call:derive_filters",
        "call:encode_query",
        "load",
        "load",  # count_tokens
        "embed",
        "load",  # inside embed_query
        "call:read_reranked_sources",
        "begin",
        "execute:snapshot",
        "lexical",
        "dense",
        "execute:products",
        "execute:spec:laptop",
        "commit",
        "call:fuse_rrf",
        "rr-load",
        "call:build_rerank_documents",
        "call:score_documents",
        "rr-count",
        "rr-score",
        "call:rerank_head",
        "close",
    ]
    # No transaction is open while either model loads or runs.
    model_events = [e for e in events if isinstance(e, tuple) and e[0] in MODEL_EVENTS]
    assert len(model_events) == 7 and all(e[1] is False for e in model_events)


def test_exact_call_order_disabled(client_for, events, monkeypatch):
    with client_for(reranker_enabled=False) as client:
        install_spies(monkeypatch, events)
        assert call(client, "post", "gaming laptop").status_code == 200
    assert names(events) == [
        "call:normalize_query",
        "call:understand_normalized_query",
        "call:derive_filters",
        "call:encode_query",
        "load",
        "load",
        "embed",
        "load",
        "call:read_filtered_sources",
        "begin",
        "execute:snapshot",
        "lexical",
        "dense",
        "commit",
        "call:fuse_rrf",
        "close",
    ]


@pytest.mark.parametrize("method", ["get", "post"])
def test_each_stage_runs_once_with_the_unchanged_query(
    client_for, events, monkeypatch, make_settings, method
):
    stub = DeterministicDecisionProvider()
    calls: list = []
    original = stub.understand
    stub.understand = lambda q, d: calls.append(q) or original(q, d)
    settings = make_settings(search_rerank_depth=3)
    with client_for(provider=stub, search_rerank_depth=3) as client:
        records = install_spies(monkeypatch, events)
        assert call(client, method, "  gaming   laptop ").status_code == 200
    query = "gaming laptop"
    counts = {name: len(by_name(records, name)) for name in SPIED}
    assert counts == dict.fromkeys(SPIED, 1) | {"read_filtered_sources": 0}
    assert calls == [query]
    assert by_name(records, "normalize_query")[0][1][0] == "  gaming   laptop "
    (encode,) = by_name(records, "encode_query")
    assert encode[1][2] == query and client.fake.encoded == [query]
    (derive,) = by_name(records, "derive_filters")
    (read,) = by_name(records, "read_reranked_sources")
    args = read[1]
    assert read[2] == {} and len(args) == 7
    assert args[0] is client.sessions[0] and args[1] == query
    assert args[2] == FakeEmbedder().embed_query(query)
    assert args[3] == settings.embedding_spec() and args[4] is derive[3].spec
    assert args[5:] == (settings.search_lexical_k, settings.search_dense_k)
    sources = read[3]
    (fuse,) = by_name(records, "fuse_rrf")
    assert fuse[1][0] is sources.lexical.hits and fuse[1][1] is sources.dense.hits
    assert fuse[1][2:] == (settings.search_rrf_k, settings.search_candidate_k)
    fused = fuse[3]
    (build,) = by_name(records, "build_rerank_documents")
    assert build[1][0] == [candidate.product_id for candidate in fused[:3]]
    assert build[1][1] is sources.records
    (score,) = by_name(records, "score_documents")
    assert score[1][0] is client.reranker and score[1][1] == query
    assert score[1][2] == [document_of(pid) for pid in build[1][0]]
    (head,) = by_name(records, "rerank_head")
    assert head[1][0] is fused and head[1][1] == score[3] and head[1][2] == 3
    assert client.reranker.load_calls == 1
    assert [c[0] for c in client.reranker.count_calls] == [query]
    assert [c[0] for c in client.reranker.score_calls] == [query]
    assert len(client.reranker.score_calls[0][1]) == 3  # only the head is scored


def test_enabled_candidates_equal_v2_before_reranking(client_for):
    with client_for() as client:
        client.reranker.score_output = scores_by_id(MATRIX_SCORES)
        v2 = call(client, "get", "comfortable gift", 50, path="/search/filtered").json()
        v3 = call(client, "get", "comfortable gift", 50).json()
    by_rrf = sorted(v3["results"], key=lambda r: r["rrf_rank"])
    stripped = [{k: v for k, v in r.items() if k not in (*V3_RESULT_KEYS, "rank")} for r in by_rrf]
    assert stripped == [{k: v for k, v in r.items() if k != "rank"} for r in v2["results"]]
    for key in ("lexical_hit_count", "dense_hit_count", "overlap_count", "fused_count"):
        assert v3[key] == v2[key]
    assert v3["fusion"] == v2["fusion"] and v3["tsquery"] == v2["tsquery"]
    assert ids(v3) != ids(v2)  # the scores really reordered the head


# ---- 4. failures ---------------------------------------------------------------------------------


def _app_records(caplog):
    return [record for record in caplog.records if record.name.startswith("ecommerce_")]


def _assert_fixed_503(response, caplog, expected_log: str):
    assert response.status_code == 503 and response.json() == FIXED_503
    app_records = _app_records(caplog)
    errors = [r for r in app_records if r.levelno >= logging.WARNING]
    assert [r.getMessage() for r in errors] == [expected_log]
    assert [r.name for r in errors] == [reranked_api.__name__]
    assert all(r.exc_info is None and r.exc_text is None for r in app_records)
    app_log = "\n".join(r.getMessage() for r in app_records)
    for secret in (*SECRETS, "RuntimeError", "ValueError", "gaming laptop", "Pavilion"):
        assert secret not in response.text and secret not in app_log


def _assert_no_fallback(events, records):
    # One source read, no V2 read, no retry and nothing after the failure that returns results.
    assert names(events).count("begin") == 1
    assert by_name(records, "read_filtered_sources") == []
    assert len(by_name(records, "read_reranked_sources")) <= 1


RERANKER_HINT = (
    "the reranker model snapshot is not present: run "
    "`python -m ecommerce_search.search model-fetch --kind reranker` (see ADR-007)"
)


def _log_for(reason: str) -> str:
    message = f"reranked search unavailable: reranker {reason}"
    return f"{message} ({RERANKER_HINT})" if reason == "snapshot_missing" else message


LOAD_REASONS = ["snapshot_missing", "load_failed", "config_mismatch"]


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("reason", LOAD_REASONS)
def test_reranker_load_failures_are_a_fixed_503_after_the_transaction(
    client_for, events, caplog, monkeypatch, method, reason
):
    with caplog.at_level(logging.DEBUG), client_for() as client:
        client.reranker.failures["load"] = RerankerUnavailable(reason)
        records = install_spies(monkeypatch, events)
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, _log_for(reason))
    assert names(events)[names(events).index("commit") :] == [
        "commit",
        "call:fuse_rrf",
        "rr-load",
        "close",
    ]
    _assert_no_fallback(events, records)
    assert not client.reranker.count_calls and not client.reranker.score_calls


def _token_output(value):
    return lambda q, texts: value(texts)


SCORING_FAILURES = {
    "input_too_long": ("token_output", lambda texts: [513] * len(texts)),
    "invalid_output tokens": ("token_output", lambda texts: ["x"] * len(texts)),
    "invalid_output short tokens": ("token_output", lambda texts: [5]),
    "invalid_output nan": ("score_output", lambda texts: [math.nan] * len(texts)),
    "invalid_output inf": ("score_output", lambda texts: [math.inf] * len(texts)),
    "invalid_output length": ("score_output", lambda texts: [1.0] * (len(texts) + 1)),
    "invalid_output strings": ("score_output", lambda texts: ["1.0"] * len(texts)),
    "invalid_output bools": ("score_output", lambda texts: [True] * len(texts)),
    "invalid_output scalar": ("score_output", lambda texts: 1.0),
    "invalid_output nested": ("score_output", lambda texts: [[1.0]] * len(texts)),
}


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(("attribute", "output"), SCORING_FAILURES.values(), ids=SCORING_FAILURES)
def test_malformed_or_overlength_reranker_output_is_a_fixed_503(
    client_for, events, caplog, monkeypatch, method, attribute, output
):
    reason = "input_too_long" if attribute == "token_output" and output([1])[0] == 513 else ""
    reason = reason or "invalid_output"
    with caplog.at_level(logging.DEBUG), client_for() as client:
        setattr(client.reranker, attribute, _token_output(output))
        records = install_spies(monkeypatch, events)
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, _log_for(reason))
    _assert_no_fallback(events, records)
    assert by_name(records, "rerank_head") == []
    scored = attribute == "score_output"
    assert len(client.reranker.score_calls) == (1 if scored else 0)  # never scored twice


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        ("count", "score_failed"),
        ("score", "score_failed"),
        ("count", "invalid_output"),
        ("score", "input_too_long"),
    ],
)
def test_reranker_scoring_failures_are_a_fixed_503_without_retry(
    client_for, events, caplog, monkeypatch, method, stage, reason
):
    with caplog.at_level(logging.DEBUG), client_for() as client:
        client.reranker.failures[stage] = RerankerUnavailable(reason)
        records = install_spies(monkeypatch, events)
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, _log_for(reason))
    _assert_no_fallback(events, records)
    assert len(client.reranker.count_calls) == 1
    assert len(client.reranker.score_calls) == (1 if stage == "score" else 0)


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_reranker_is_snapshot_missing_after_the_transaction(
    client_for, events, caplog, method
):
    with caplog.at_level(logging.DEBUG), client_for(reranker=None) as client:
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, _log_for("snapshot_missing"))
    assert names(events)[-3:] == ["execute:spec:laptop", "commit", "close"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_reranker_for_another_model_is_a_config_mismatch_before_loading(
    client_for, events, caplog, method
):
    other = FakeReranker()  # the test-local FAKE_SPEC, not the configured model
    with caplog.at_level(logging.DEBUG), client_for(reranker=other) as client:
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, _log_for("config_mismatch"))
    assert other.load_calls == 0 and not other.score_calls


def _products_missing(pid):
    return lambda ids: [product_row(BY_ID[p]) for p in ids if p != pid]


def _products_invalid(pid):
    def rows(ids):
        out = []
        for p in ids:
            raw = dict(BY_ID[p])
            if p == pid:
                raw["price"] = Decimal("-1")
                raw["title"] = SENSITIVE
            out.append(product_row(raw))
        return out

    return rows


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("products", [_products_missing("L2"), _products_invalid("L3")])
def test_unreadable_candidate_records_are_document_build_failed_before_the_reranker(
    client_for, events, caplog, monkeypatch, method, products
):
    with caplog.at_level(logging.DEBUG), client_for(products=products) as client:
        records = install_spies(monkeypatch, events)
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, "reranked search unavailable: document_build_failed")
    assert names(events)[-2:] == ["commit", "close"]  # the snapshot ended; nothing after it
    assert by_name(records, "fuse_rrf") == [] and client.reranker.load_calls == 0
    _assert_no_fallback(events, records)


@pytest.mark.parametrize("method", ["get", "post"])
def test_document_build_failure_after_loading_is_a_fixed_503(
    client_for, events, caplog, monkeypatch, method
):
    def broken(product_ids, records):
        raise RerankDocumentError

    with caplog.at_level(logging.DEBUG), client_for() as client:
        monkeypatch.setattr(reranked_api, "build_rerank_documents", broken)
        records = install_spies(monkeypatch, events, ("score_documents", "read_filtered_sources"))
        response = call(client, method, "gaming laptop")
    _assert_fixed_503(response, caplog, "reranked search unavailable: document_build_failed")
    assert names(events)[-3:] == ["commit", "rr-load", "close"]
    assert records == [] and not client.reranker.score_calls


def _operational():
    return OperationalError(SENSITIVE, {"p": "hunter2"}, Exception(SENSITIVE))


def _missing_table():
    return ProgrammingError(SENSITIVE, {}, type("E", (Exception,), {"sqlstate": "42P01"})())


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("failure", "logged", "tail"),
    [
        ({"errors": {"snapshot": _operational()}}, "database_error", []),
        ({"lexical_error": _operational()}, "database_error", ["lexical"]),
        ({"lexical_error": _missing_table()}, "table_missing", ["lexical"]),
        ({"dense_error": _operational()}, "database_error", ["lexical", "dense"]),
        (
            {"errors": {"products": _operational()}},
            "database_error",
            ["lexical", "dense", "execute:products"],
        ),
        (
            {"errors": {"spec:laptop": _missing_table()}},
            "table_missing",
            ["lexical", "dense", "execute:products", "execute:spec:laptop"],
        ),
    ],
)
def test_database_failures_roll_back_release_and_never_retry_or_fall_back(
    client_for, events, caplog, monkeypatch, method, failure, logged, tail
):
    with caplog.at_level(logging.DEBUG), client_for(**failure) as client:
        records = install_spies(monkeypatch, events, RETRIEVAL_SIDE)
        response = call(client, method, "hunter2 gaming laptop")
    assert response.status_code == 503 and response.json() == FIXED_503
    app_log = "\n".join(r.getMessage() for r in _app_records(caplog))
    for secret in SECRETS:
        assert secret not in response.text and secret not in app_log
    errors = [r.getMessage() for r in _app_records(caplog) if r.levelno >= logging.WARNING]
    assert len(errors) == 1 and errors[0].startswith(f"reranked search unavailable: {logged}")
    assert names(events)[names(events).index("call:read_reranked_sources") :] == [
        "call:read_reranked_sources",
        "begin",
        "execute:snapshot",
        *tail,
        "rollback",
        "close",
    ]
    assert [name for name, *_ in records] == ["encode_query"]  # one attempt, no fusion
    assert client.reranker.load_calls == 0
    assert not client.sessions[0].in_transaction() and client.sessions[0].closed


class RaisingProvider:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error or RuntimeError(SENSITIVE)
        self.calls = 0

    def understand(self, query, deterministic_result):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("stage", ["provider", "policy"])
def test_understanding_and_policy_failures_happen_before_any_model_or_database_work(
    client_for, events, caplog, monkeypatch, method, stage
):
    provider = RaisingProvider() if stage == "provider" else None
    with caplog.at_level(logging.DEBUG), client_for(provider=provider) as client:
        if stage == "policy":

            def broken(decision):
                raise RuntimeError(SENSITIVE)

            monkeypatch.setattr(reranked_api, "derive_filters", broken)
        records = install_spies(monkeypatch, events, RETRIEVAL_SIDE)
        response = call(client, method, "hunter2 gaming laptop")
    reason = "query_understanding_failed" if stage == "provider" else "filter_policy_failed"
    assert response.status_code == 503 and response.json() == FIXED_503
    errors = [r.getMessage() for r in _app_records(caplog) if r.levelno >= logging.WARNING]
    assert errors == [f"reranked search unavailable: {reason}"]
    app_log = "\n".join(r.getMessage() for r in _app_records(caplog))
    for secret in SECRETS:
        assert secret not in response.text and secret not in app_log
    assert records == [] and events == ["close"]
    assert client.fake.load_calls == 0 and client.reranker.load_calls == 0


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "reason",
    ["snapshot_missing", "load_failed", "dimension_mismatch", "config_mismatch", "encode_failed"],
)
def test_embedding_failures_are_a_fixed_503_before_any_transaction(
    client_for, events, caplog, monkeypatch, method, reason
):
    fake = FakeEmbedder()
    fake.fail_after_batches = 0
    fake.failure = EmbedderUnavailable(reason)
    with caplog.at_level(logging.ERROR), client_for(embedder=fake) as client:
        install_spies(monkeypatch, events, ("derive_filters", "read_reranked_sources"))
        response = call(client, method, "gaming laptop")
    assert response.status_code == 503 and response.json() == FIXED_503
    errors = [r.getMessage() for r in _app_records(caplog) if r.levelno >= logging.WARNING]
    assert len(errors) == 1 and errors[0].startswith(f"reranked search unavailable: {reason}")
    assert events == ["call:derive_filters", "close"]
    assert client.reranker.load_calls == 0


@pytest.mark.parametrize("method", ["get", "post"])
def test_no_embedding_models_directory_is_a_fixed_503(client_for, events, caplog, method):
    with caplog.at_level(logging.ERROR), client_for(embedder=None) as client:
        response = call(client, method, "gaming laptop")
    assert response.status_code == 503 and response.json() == FIXED_503
    assert "snapshot_missing" in caplog.text and "model-fetch" in caplog.text
    assert "--kind reranker" not in caplog.text  # the embedding model's hint, not the reranker's
    assert events == ["close"]


def _assert_generic_500(response, caplog, *secrets: str) -> None:
    # A programming error is not disguised as the service-unavailable 503 (as in V1/V2).
    assert response.status_code == 500 and response.text == "Internal Server Error"
    app_records = _app_records(caplog)
    assert not [r for r in app_records if r.levelno >= logging.WARNING]
    app_log = "\n".join(r.getMessage() for r in app_records)
    for secret in (*SECRETS, *secrets):
        assert secret not in response.text and secret not in app_log


def _raise_runtime(*args, **kwargs):
    raise RuntimeError(SENSITIVE)


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("stage", ["embedder", "record reader", "reranker score", "reranker load"])
def test_unexpected_errors_are_a_generic_500_without_fallback(
    client_for, events, caplog, monkeypatch, method, stage
):
    embedder = "recording"
    if stage == "embedder":
        embedder = FakeEmbedder()
        embedder.fail_after_batches = 0
        embedder.failure = RuntimeError(SENSITIVE)
    with caplog.at_level(logging.DEBUG), client_for(embedder=embedder) as c:
        if stage == "record reader":
            monkeypatch.setattr(reranked_search, "_read_candidate_rows", _raise_runtime)
        elif stage == "reranker score":
            c.reranker.failures["score"] = RuntimeError(SENSITIVE)
        elif stage == "reranker load":
            c.reranker.failures["load"] = RuntimeError(SENSITIVE)
        records = install_spies(monkeypatch, events, RETRIEVAL_SIDE)
        response = call(TestClient(c.app, raise_server_exceptions=False), method, "gaming laptop")
    _assert_generic_500(response, caplog, "RuntimeError")
    assert by_name(records, "read_filtered_sources") == []
    assert names(events).count("begin") <= 1
    if stage == "record reader":
        assert names(events)[-2:] == ["rollback", "close"]
    assert not c.sessions[0].in_transaction() and c.sessions[0].closed


@pytest.mark.parametrize("method", ["get", "post"])
def test_malformed_source_list_is_a_generic_500_after_the_transaction(
    client_for, events, caplog, monkeypatch, method
):
    def duplicated_dense(session, vector, spec, filters, limit):
        events.append(("dense", len(vector), filters, limit))
        hit = dense_hits_for(filters, 1)[0]
        return DenseResult(hits=[hit, DenseHit(**{**hit.__dict__, "rank": 2})], vector_ms=0.0)

    with caplog.at_level(logging.DEBUG), client_for() as c:
        monkeypatch.setattr(filtered_search, "filtered_dense_search", duplicated_dense)
        install_spies(monkeypatch, events, RETRIEVAL_SIDE)
        response = call(TestClient(c.app, raise_server_exceptions=False), method, "gaming laptop")
    _assert_generic_500(response, caplog, "unique", "ValueError")
    assert names(events)[names(events).index("commit") :] == ["commit", "call:fuse_rrf", "close"]
    assert c.reranker.load_calls == 0


class Boom(BaseException):
    pass


@pytest.mark.parametrize(
    "stage",
    ["provider", "derive", "encode", "read", "records", "fuse", "rr-load", "rr-score"],
)
def test_base_exceptions_propagate(make_settings, events, monkeypatch, stage):
    session = FakeSession(events)
    provider = DeterministicDecisionProvider()
    embedder = FakeEmbedder()
    reranker = FakeReranker(MS_MARCO_MINILM_L6_V2)

    def boom(*args, **kwargs):
        raise Boom

    monkeypatch.setattr(
        filtered_search,
        "filtered_lexical_search",
        lambda s, q, f, k: LexicalResult(tsquery="'x'", hits=lexical_hits_for(f, k), lexical_ms=0),
    )
    monkeypatch.setattr(
        filtered_search,
        "filtered_dense_search",
        lambda s, v, sp, f, k: DenseResult(hits=dense_hits_for(f, k), vector_ms=0.0),
    )
    if stage == "provider":
        provider = RaisingProvider(Boom())
    elif stage == "derive":
        monkeypatch.setattr(reranked_api, "derive_filters", boom)
    elif stage == "encode":
        embedder.fail_after_batches = 0
        embedder.failure = Boom()
    elif stage == "read":
        monkeypatch.setattr(filtered_search, "filtered_lexical_search", boom)
    elif stage == "records":
        monkeypatch.setattr(reranked_search, "_read_candidate_rows", boom)
    elif stage == "fuse":
        monkeypatch.setattr(reranked_api, "fuse_rrf", boom)
    elif stage == "rr-load":
        reranker.failures["load"] = Boom()
    else:
        reranker.failures["score"] = Boom()
    with pytest.raises(Boom):
        reranked_api.run_reranked_search(
            session,
            make_settings(),
            embedder,
            reranker,
            provider,
            "gaming laptop",
            None,
            ("query", "q"),
            ("query", "top_k"),
        )
    assert not session.in_transaction()
    if stage in ("read", "records"):
        assert events[-1] == "rollback"


@pytest.mark.parametrize(
    "params",
    [
        {"q": ""},
        {"q": "   "},
        {"q": "shoes\x00"},
        {"q": "a\u200bb"},
        {"q": "x" * 201},
        {"q": "shoes", "top_k": 0},
        {"q": "shoes", "top_k": 51},
        {"q": "shoes", "top_k": "many"},
    ],
)
def test_validation_422s_happen_before_any_work(client_for, events, params):
    provider = RaisingProvider()
    with client_for(provider=provider) as client:
        got = client.get(PATH, params=params)
        body = {"query": params["q"]} | ({"top_k": params["top_k"]} if "top_k" in params else {})
        posted = client.post(PATH, json=body)
        v2 = client.get("/search/filtered", params=params)
    assert got.status_code == posted.status_code == v2.status_code == 422
    assert provider.calls == 0 and client.fake.load_calls == 0 and client.reranker.load_calls == 0
    assert "begin" not in events


@pytest.mark.parametrize(
    "body",
    [
        {"query": "shoes", "rerank": False},
        {"query": "shoes", "rerank_depth": 5},
        {"query": "shoes", "filters": {}},
        {"top_k": 5},
        ["shoes"],
    ],
)
def test_post_request_is_closed(client_for, events, body):
    with client_for() as client:
        response = client.post(PATH, json=body)
    assert response.status_code == 422 and "begin" not in events
    assert client.reranker.load_calls == 0


def test_get_ignores_unknown_parameters_and_cannot_switch_reranking(client_for):
    with client_for() as client:
        plain = call(client, "get", "gaming laptop").json()
        extra = client.get(PATH, params={"q": "gaming laptop", "rerank": "false"}).json()
    assert without_latency(extra) == without_latency(plain)
    assert extra["reranker"]["status"] == "applied"


def test_top_k_upper_bound_is_candidate_k(client_for):
    with client_for(search_candidate_k=5, search_rerank_depth=3, search_default_top_k=5) as c:
        ok = call(c, "get", "comfortable gift", 5)
        too_many = call(c, "post", "comfortable gift", 6)
    assert ok.status_code == 200 and ok.json()["result_count"] == 5
    assert too_many.status_code == 422


# ---- 5. OpenAPI contract and frozen baseline ----------------------------------------------------

FORBIDDEN_KEYS = ("jev", "confidence", "fallback", "quality", "probability", "listing")


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


def test_openapi_documents_the_v3_contract(make_settings):
    schema = create_app(make_settings()).openapi()
    operations = schema["paths"][PATH]
    assert set(operations) == {"get", "post"}
    for operation in operations.values():
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok == {"$ref": "#/components/schemas/RerankedSearchResponse"}
        unavailable = operation["responses"]["503"]["content"]["application/json"]["schema"]
        assert unavailable == {"$ref": "#/components/schemas/SearchUnavailable"}
    body = operations["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/RerankedSearchRequest"}
    components = schema["components"]["schemas"]
    request = components["RerankedSearchRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "top_k"}
    response = components["RerankedSearchResponse"]
    filtered = components["FilteredSearchResponse"]
    assert set(response["properties"]) == set(filtered["properties"]) | {"reranker"}
    assert response["properties"]["search_version"] == {
        "type": "string",
        "const": "v3_reranked",
        "title": "Search Version",
    }
    block = components["RerankerBlock"]
    assert block["additionalProperties"] is False
    assert set(block["properties"]) == {
        "enabled",
        "status",
        "model_id",
        "model_revision",
        "document_version",
        "max_length",
        "rerank_depth",
        "reranked_count",
        "score_type",
    }
    assert block["properties"]["status"]["enum"] == [
        "applied",
        "disabled",
        "skipped_no_candidates",
        "skipped_no_searchable_text",
    ]
    result = components["RerankedSearchResult"]
    hybrid = components["HybridSearchResult"]
    assert set(result["properties"]) == set(hybrid["properties"]) | set(V3_RESULT_KEYS)
    for key in hybrid["properties"]:
        assert result["properties"][key] == hybrid["properties"][key], key
    latency = components["RerankedSearchLatency"]
    assert set(latency["properties"]) == set(components["FilteredSearchLatency"]["properties"]) | {
        "document_read_ms",
        "reranker_load_ms",
        "rerank_document_ms",
        "reranking_ms",
    }
    assert "filtering_ms" not in latency["properties"]
    for name in (
        "RerankedSearchRequest",
        "RerankedSearchResponse",
        "RerankedSearchResult",
        "RerankedSearchLatency",
        "RerankerBlock",
    ):
        for key in _keys(components[name]["properties"]):
            assert not any(word in key.lower() for word in FORBIDDEN_KEYS), (name, key)


FROZEN_PATHS = ("/health", "/search", "/search/dense", "/search/hybrid", "/search/filtered")
# sha256 of the canonical JSON of the five frozen operations plus every component they reference
# (transitively), exported from HEAD 957f7ed before any Milestone 8 Session E change, with the
# same procedure as `frozen_openapi` below.
HEAD_FROZEN_DIGEST = "3f776a588dbfab595369e18d9b8a50bca6ac8d3184054e7cbf8b05acd9d68934"


def _refs(node, out: set) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                out.add(value.rsplit("/", 1)[-1])
            else:
                _refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _refs(value, out)


def frozen_openapi(schema: dict) -> dict:
    paths = {path: schema["paths"][path] for path in FROZEN_PATHS}
    components = schema["components"]["schemas"]
    seen: set = set()
    todo: set = set()
    _refs(paths, todo)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        found: set = set()
        _refs(components[name], found)
        todo |= found - seen
    return {"paths": paths, "components": {name: components[name] for name in sorted(seen)}}


def test_frozen_operations_and_components_are_byte_identical_to_head(make_settings):
    schema = create_app(make_settings()).openapi()
    frozen = frozen_openapi(schema)
    blob = json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(blob.encode()).hexdigest() == HEAD_FROZEN_DIGEST
    # + experimental V4 and the M16 assistant
    assert set(schema["paths"]) == {
        *FROZEN_PATHS,
        PATH,
        "/search/routed",
        "/assistant/answer",
        "/assistant/status",
    }
    v3_only = {
        "RerankedSearchRequest",
        "RerankedSearchResponse",
        "RerankedSearchResult",
        "RerankedSearchLatency",
        "RerankerBlock",
    }
    assert not v3_only & set(frozen["components"])


LEGACY_KEYS = {
    "/search": {
        "query",
        "search_version",
        "document_version",
        "top_k",
        "result_count",
        "results",
        "tsquery",
        "applied_filters",
        "latency_ms",
    },
    "/search/dense": {
        "query",
        "search_version",
        "embedding_model_id",
        "embedding_model_revision",
        "embedding_dimension",
        "embedding_text_version",
        "distance_metric",
        "top_k",
        "result_count",
        "results",
        "applied_filters",
        "latency_ms",
    },
}
LEGACY_KEYS["/search/hybrid"] = {
    *LEGACY_KEYS["/search"],
    "embedding_model_id",
    "embedding_model_revision",
    "embedding_dimension",
    "embedding_text_version",
    "distance_metric",
    "fusion",
    "dense_status",
    "lexical_hit_count",
    "dense_hit_count",
    "overlap_count",
    "fused_count",
    "candidate_count",
    "query_understanding",
}
LEGACY_KEYS["/search/filtered"] = LEGACY_KEYS["/search/hybrid"] | {
    "filter_policy",
    "ignored_constraints",
}
LEGACY_RESULT_KEYS = {
    "rank",
    "product_id",
    "title",
    "brand",
    "category",
    "subcategory",
    "description",
    "price",
    "currency",
    "rating",
    "review_count",
    "availability",
}
RESULT_KEYS = {
    "/search": LEGACY_RESULT_KEYS | {"lexical_score"},
    "/search/dense": LEGACY_RESULT_KEYS | {"dense_score"},
    "/search/hybrid": LEGACY_RESULT_KEYS
    | {"rrf_score", "lexical_rank", "lexical_score", "dense_rank", "dense_score"},
}
RESULT_KEYS["/search/filtered"] = RESULT_KEYS["/search/hybrid"]
UNFILTERED = FilterSpec()


def _install_legacy_sources(monkeypatch) -> None:
    def lexical(session, query, limit):
        return LexicalResult(
            tsquery="'x'", hits=lexical_hits_for(UNFILTERED, limit), lexical_ms=1.5
        )

    def dense(session, vector, spec, limit):
        return DenseResult(hits=dense_hits_for(UNFILTERED, limit), vector_ms=2.5)

    monkeypatch.setattr(search_api, "lexical_search", lexical)
    monkeypatch.setattr(dense_api, "dense_search", dense)
    monkeypatch.setattr(hybrid_search, "lexical_search", lexical)
    monkeypatch.setattr(hybrid_search, "dense_search", dense)


def _legacy_responses(client) -> dict:
    out = {}
    for path in LEGACY_KEYS:
        for method in ("get", "post"):
            response = call(client, method, "hp laptop 8gb ram", 5, path=path)
            assert response.status_code == 200, (path, method)
            body = response.json()
            assert set(body) == LEGACY_KEYS[path], path
            assert body["results"] and all(set(r) == RESULT_KEYS[path] for r in body["results"])
            out[path, method] = without_latency(body)
    return out


def _raising_v3_stubs(monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("V3 code must not run for V0/dense/V1/V2 requests")

    monkeypatch.setattr(reranked_search, "read_reranked_sources", broken)
    monkeypatch.setattr(reranked_search, "_read_candidate_rows", broken)
    monkeypatch.setattr(reranked_search, "build_candidate_records", broken)
    monkeypatch.setattr(reranked_api, "read_reranked_sources", broken)
    monkeypatch.setattr(reranked_api, "build_rerank_documents", broken)
    monkeypatch.setattr(reranked_api, "score_documents", broken)
    monkeypatch.setattr(reranked_api, "rerank_head", broken)


def test_existing_endpoints_never_touch_the_reranker_or_record_reader(
    client_for, events, monkeypatch
):
    with client_for() as client:
        _install_legacy_sources(monkeypatch)
        baseline = _legacy_responses(client)
        _raising_v3_stubs(monkeypatch)
        stubbed = _legacy_responses(client)
        v3 = call(TestClient(client.app, raise_server_exceptions=False), "get", "gaming laptop")
    assert stubbed == baseline
    reranker = client.reranker
    # Only the V3 request below reached (and failed in) V3 code; the reranker was never used.
    assert reranker.load_calls == 0 and not reranker.count_calls and not reranker.score_calls
    assert "execute:products" not in names(events)
    assert v3.status_code == 500  # the stubs really are installed for the V3 route


# ---- 6. dependency, app lifecycle and imports -----------------------------------------------------


def test_create_app_owns_one_unloaded_reranker_built_from_settings(make_settings, monkeypatch):
    built: list = []

    class CountingReranker(CrossEncoderReranker):
        def __init__(self, *args, **kwargs):
            built.append((args, kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_module, "CrossEncoderReranker", CountingReranker)
    settings = make_settings(reranker_batch_size=7)
    app = create_app(settings)
    reranker = app.state.reranker
    assert isinstance(reranker, CrossEncoderReranker) and not reranker.loaded
    assert built == [((MS_MARCO_MINILM_L6_V2, settings.resolved_models_dir()), {"batch_size": 7})]
    request = SimpleNamespace(app=app)
    assert get_reranker(request) is reranker and get_reranker(request) is reranker
    with TestClient(app):  # the lifespan creates the embedder as before, never the reranker
        assert app.state.reranker is reranker and not reranker.loaded
        assert isinstance(app.state.embedder, SentenceTransformerEmbedder)
        assert not app.state.embedder.loaded
        assert isinstance(app.state.decision_provider, DeterministicDecisionProvider)
    assert len(built) == 1
    assert create_app(settings).state.reranker is not reranker  # one per app


def test_create_app_reads_no_model_file(make_settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no model file may be read while creating the app")

    monkeypatch.setattr(cross_encoder_provider, "verify_manifest", forbidden)
    monkeypatch.setattr(cross_encoder_provider, "snapshot_config_problems", forbidden)
    monkeypatch.setattr(cross_encoder_provider, "_read_json", forbidden)
    for method in ("open", "read_text", "read_bytes", "iterdir", "glob", "rglob"):
        monkeypatch.setattr(Path, method, forbidden)
    app = create_app(make_settings(reranker_enabled=True))
    assert app.state.reranker is not None and not app.state.reranker.loaded


def test_no_models_directory_means_no_reranker(make_settings, monkeypatch):
    monkeypatch.setattr(Settings, "resolved_models_dir", lambda self: None)
    app = create_app(make_settings())
    assert app.state.reranker is None
    assert get_reranker(SimpleNamespace(app=app)) is None


_LAZY_PROBE = """
import sys
import ecommerce_search.search.reranked
import ecommerce_search.api.reranked
import ecommerce_search.api.app
from ecommerce_search.api.app import create_app
from ecommerce_search.config import Settings
app = create_app(Settings(_env_file=None, postgres_password="x", app_env="test"))
assert app.state.reranker is not None and not app.state.reranker.loaded
heavy = ("torch", "transformers", "sentence_transformers", "huggingface_hub", "hf_xet",
         "tokenizers", "safetensors")
print(",".join(sorted(name for name in sys.modules if name.split(".")[0] in heavy)))
"""


def test_importing_the_v3_modules_and_creating_the_app_load_no_model_library():
    done = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-c", _LAZY_PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("POSTGRES_", "DB_", "SEARCH_", "RERANKER_", "EMBEDDING_"))
        },
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""


# ---- Milestone 24A: one query-free summary per reranked search --------------------------

M24A_MARKER = "zqx7privatemarker"
SUMMARY_KEYS = {
    "event",
    "request_id",
    "reranker_status",
    "reranker_enabled",
    "dense_status",
    "top_k",
    "candidate_count",
    "reranked_count",
    "result_count",
    "latency_ms",
}


@pytest.fixture
def formatted_app_logs(caplog):
    caplog.set_level(logging.INFO)
    stream = io.StringIO()
    handler = build_log_handler(stream)
    handler.addFilter(logging.Filter("ecommerce_search"))  # not the test client's httpx logger
    logging.getLogger().addHandler(handler)
    yield stream.getvalue
    logging.getLogger().removeHandler(handler)


def json_events(text: str, event: str) -> list[dict]:
    marker = f'"event":"{event}"'
    return [json.loads(line[line.find("{") :]) for line in text.splitlines() if marker in line]


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("query", "enabled", "status"),
    [
        (f"gaming laptop {M24A_MARKER}", True, "applied"),
        (f"gaming laptop {M24A_MARKER}", False, "disabled"),
        (f"nike laptop {M24A_MARKER}", True, "skipped_no_candidates"),
        ("!!!", True, "skipped_no_searchable_text"),
    ],
)
def test_reranked_summary_is_logged_once_without_query_text(
    client_for, formatted_app_logs, method, query, enabled, status
):
    with client_for(reranker_enabled=enabled) as client:
        if method == "get":
            response = client.get(
                PATH, params={"q": query}, headers={"X-Request-ID": "m24a-req-01"}
            )
        else:
            response = client.post(
                PATH, json={"query": query}, headers={"X-Request-ID": "m24a-req-01"}
            )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "m24a-req-01"
    body = response.json()
    text = formatted_app_logs()
    (summary,) = json_events(text, "reranked_search_completed")
    assert set(summary) == SUMMARY_KEYS
    assert summary["request_id"] == "m24a-req-01"
    assert summary["reranker_status"] == body["reranker"]["status"] == status
    assert summary["reranker_enabled"] is enabled
    assert summary["latency_ms"] == body["latency_ms"]
    for key in ("dense_status", "top_k", "candidate_count", "result_count"):
        assert summary[key] == body[key], key
    assert summary["reranked_count"] == body["reranker"]["reranked_count"]
    (completed,) = json_events(text, "request_completed")
    assert (completed["route"], completed["status"]) == (PATH, 200)
    assert M24A_MARKER not in text and "laptop" not in text and "!!!" not in text
