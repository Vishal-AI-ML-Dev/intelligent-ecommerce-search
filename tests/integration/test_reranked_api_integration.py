"""`/search/reranked` (Milestone 8, V3) end to end against real PostgreSQL scratch databases (fake
embedder and fake reranker only; the development database is never touched).

Independent evidence:

* Expected filter specs come from the predeclared Milestone 7 truth table
  (`filter_support.TRUTH_TABLE`); expected ignored constraints and the applied-filter JSON are
  written here by hand from the fp-1 rules.
* Eligibility is the catalog oracle of `filter_support` (plain SELECTs + Python predicates).
* Source lists are compared with direct calls of the committed filtered lexical / dense reads,
  fusion with an exact-fraction RRF oracle and reranking with a sort written here over fake
  scores that are predeclared per product id (a hash), never produced by the route.
* `rd-1` documents are compared with the text built from the committed seed file.

The fake reranker exercises plumbing only; nothing here measures the quality of any model.
"""

import hashlib
import json
import logging
from decimal import Decimal
from fractions import Fraction

import pytest
from catalog_support import SEED
from dense_support import embed_fake
from fastapi.testclient import TestClient
from filter_support import (
    MISSING_EMBEDDING,
    NULL_RAM_LAPTOP,
    NULL_STORAGE_TYPE_LAPTOP,
    STALE_EMBEDDING,
    TRUTH_TABLE,
    catalog_facts,
    oracle_eligible,
    satisfies,
)
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from ecommerce_search.api import reranked as reranked_api
from ecommerce_search.api.app import create_app
from ecommerce_search.api.dependencies import get_decision_provider, get_embedder, get_reranker
from ecommerce_search.api.schemas import RerankedSearchResponse
from ecommerce_search.catalog.schemas import parse_raw
from ecommerce_search.decision import DecisionResult
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.query_understanding import QueryUnderstanding
from ecommerce_search.reranking.documents import build_rerank_document
from ecommerce_search.reranking.provider import RerankerUnavailable
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2
from ecommerce_search.search import reranked as reranked_search
from ecommerce_search.search.filtered import filtered_dense_search, filtered_lexical_search
from fake_embedder import FakeEmbedder
from fake_reranker import FakeReranker

pytestmark = pytest.mark.integration

ROWS = {row.id: row for row in TRUTH_TABLE}
PATH = "/search/reranked"
FIXED_503 = {"detail": "search unavailable"}


def _ignored(*pairs: tuple[str, str]) -> list[dict]:
    return [{"field": field, "reason": reason} for field, reason in pairs]


# truth-table row id -> expected ignored constraints, written by hand from fp-1 (the same
# committed expectations as the V2 integration tests).
QUERIES = {
    "english": ("cat-brand", []),
    "hinglish-intent": (
        "informational-intent",
        _ignored(("semantic_intent", "informational_only")),
    ),
    "hinglish-sasta": ("informational-price", _ignored(("price_preference", "informational_only"))),
    "hinglish-impossible-price": ("exact-price-none", []),
    "numeric-ram": ("ram-only", []),
    "numeric-storage": ("storage-ssd", []),
    "numeric-nvme": ("nvme", []),
    "numeric-min-price": ("min-price", []),
    "numeric-max-price": ("max-price", []),
    "combined": ("full", []),
    "combined-ram": ("cat-brand-ram", []),
    "conflict-storage": (
        "conflict-storage",
        _ignored(("storage_type", "conflict"), ("storage_interface", "conflict")),
    ),
    "conflict-brand": ("conflict-brand", _ignored(("brand", "conflict"))),
    "conflict-price": (
        "conflict-price",
        _ignored(("min_price", "conflict"), ("max_price", "conflict")),
    ),
    "ambiguity": ("ambiguous-ram", _ignored(("ram_gb", "ambiguous_family"))),
    "impossible-brand": ("impossible-brand", []),
    "impossible-phone-ssd": ("impossible-phone-ssd", []),
    "punctuation": ("punctuation", []),
}
QUERY_PARAMS = pytest.mark.parametrize("label", QUERIES, ids=QUERIES)
FIELD_ORDER = (
    "category",
    "brand",
    "ram_gb",
    "storage_gb",
    "storage_type",
    "storage_interface",
    "min_price",
    "max_price",
)
OPERATORS = {"min_price": "gte", "max_price": "lte"}
V3_STAGES = ("document_read_ms", "reranker_load_ms", "rerank_document_ms", "reranking_ms")


def _plain(value):
    return value.value if hasattr(value, "value") else value


def expected_applied(spec: FilterSpec) -> list[dict]:
    out = []
    for name in FIELD_ORDER:
        value = getattr(spec, name)
        if value is None:
            continue
        rendered = f"{value:.2f}" if isinstance(value, Decimal) else str(_plain(value))
        out.append({"field": name, "operator": OPERATORS.get(name, "eq"), "value": rendered})
    return out


def rrf_oracle(lexical: list[str], dense: list[str], rrf_k: int) -> list[tuple[str, Fraction]]:
    scores: dict[str, Fraction] = {}
    for ranking in (lexical, dense):
        for position, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, Fraction(0)) + Fraction(1, rrf_k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def fake_score(product_id: str) -> float:
    """Predeclared fake logit per product: nine values, so equal scores (ties) are common."""
    return float(int(hashlib.sha256(product_id.encode()).hexdigest()[:8], 16) % 9 - 4)


def rerank_oracle(
    rrf_ids: list[str], depth: int
) -> list[tuple[str, int, int | None, float | None]]:
    """(product id, rrf rank, rerank rank, score): head by score desc then RRF rank; tail kept."""
    size = min(depth, len(rrf_ids))
    head = sorted(
        ((fake_score(pid), rank, pid) for rank, pid in enumerate(rrf_ids[:size], start=1)),
        key=lambda item: (-item[0], item[1]),
    )
    out = [(pid, rank, n, score) for n, (score, rank, pid) in enumerate(head, start=1)]
    return out + [(pid, rank, None, None) for rank, pid in enumerate(rrf_ids[size:], size + 1)]


# ------------------------------------------------------------------------------ catalog state

NULLED = {
    NULL_RAM_LAPTOP: {"ram_gb": None},
    NULL_STORAGE_TYPE_LAPTOP: {"storage_type": None, "storage_interface": None},
}


def _seed_documents() -> dict[str, str]:
    """rd-1 text of every product, built from the committed seed lines (plus the NULL changes)."""
    documents = {}
    for line in SEED.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            record = parse_raw({**raw, **NULLED.get(raw["product_id"], {})})
            documents[record.product_id] = build_rerank_document(record)
    return documents


SEED_DOCUMENTS = _seed_documents()
DOCUMENT_TO_ID = {document: pid for pid, document in SEED_DOCUMENTS.items()}
assert len(DOCUMENT_TO_ID) == len(SEED_DOCUMENTS) == 240  # every document is distinct


def apply_valid_mutations(engine) -> None:
    """Stale / missing embeddings and NULL spec values; every product keeps a valid record."""
    stale_sha = hashlib.sha256(b"m8-session-f-stale-embedding").hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE laptop_specs SET ram_gb = NULL WHERE product_id = :p"),
            {"p": NULL_RAM_LAPTOP},
        )
        conn.execute(
            text(
                "UPDATE laptop_specs SET storage_type = NULL, storage_interface = NULL "
                "WHERE product_id = :p"
            ),
            {"p": NULL_STORAGE_TYPE_LAPTOP},
        )
        conn.execute(
            text("UPDATE product_embeddings SET source_content_sha256 = :s WHERE product_id = :p"),
            {"s": stale_sha, "p": STALE_EMBEDDING},
        )
        conn.execute(
            text("DELETE FROM product_embeddings WHERE product_id = :p"), {"p": MISSING_EMBEDDING}
        )


@pytest.fixture(scope="module")
def world(writable_seeded_engine):
    engine = writable_seeded_engine
    assert engine.url.database.startswith("ecommerce_search_test_")
    embed_fake(engine)
    apply_valid_mutations(engine)
    return {"engine": engine, "facts": catalog_facts(engine)}


class ScriptedReranker(FakeReranker):
    """The fake reranker with the pinned spec, predeclared scores per product and a record of
    the pool's checked-out connections at every call."""

    def __init__(self) -> None:
        super().__init__(MS_MARCO_MINILM_L6_V2)
        self.engine = None
        self.checked_out: list[tuple[str, int]] = []
        self.score_output = lambda query, texts: [fake_score(DOCUMENT_TO_ID[t]) for t in texts]

    def _note(self, stage: str) -> None:
        self.checked_out.append((stage, self.engine.pool.checkedout()))

    def load(self):
        self._note("load")
        return super().load()

    def count_pair_tokens(self, query, documents):
        self._note("count")
        return super().count_pair_tokens(query, documents)

    def score(self, query, documents):
        self._note("score")
        return super().score(query, documents)


class RecordingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.engine = None
        self.checked_out: list[int] = []

    def embed_query(self, text):
        self.checked_out.append(self.engine.pool.checkedout())
        return super().embed_query(text)


def make_client(settings, engine, *, reranker=None, provider=None, **overrides) -> TestClient:
    scratch = settings.model_copy(update={"postgres_db": engine.url.database, **overrides})
    assert scratch.postgres_db != settings.postgres_db  # never the development database
    assert scratch.postgres_db.startswith("ecommerce_search_test_")
    app = create_app(scratch)
    embedder = RecordingEmbedder()
    reranker = reranker or ScriptedReranker()
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_reranker] = lambda: reranker
    if provider is not None:
        app.dependency_overrides[get_decision_provider] = lambda: provider
    client = TestClient(app)
    client.embedder, client.reranker = embedder, reranker
    return client


def attach(client) -> list:
    """Wire the recorders to the app engine; return its classified statement log."""
    engine = client.app.state.engine
    client.embedder.engine = engine
    if isinstance(client.reranker, ScriptedReranker):
        client.reranker.engine = engine
    log: list = []

    def before(conn, cursor, statement, params, context, many):
        flat = " ".join(statement.split())
        if "p.seller_id" in flat:
            log.append("records")
        elif "_specs s WHERE s.product_id = ANY" in flat:
            log.append("spec-records")
        else:
            log.append(flat[:30])

    event.listen(engine, "begin", lambda conn: log.append("BEGIN"))
    event.listen(engine, "commit", lambda conn: log.append("COMMIT"))
    event.listen(engine, "rollback", lambda conn: log.append("ROLLBACK"))
    event.listen(engine, "before_cursor_execute", before)
    return log


def spy(monkeypatch, module, name: str) -> list:
    calls: list = []
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return calls


def call(client, method: str, query: str, top_k: int | None = None, path: str = PATH):
    if method == "get":
        return client.get(path, params={"q": query} | ({} if top_k is None else {"top_k": top_k}))
    return client.post(path, json={"query": query} | ({} if top_k is None else {"top_k": top_k}))


def without_latency(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "latency_ms"}


def direct_sources(engine, query: str, spec: FilterSpec, settings) -> tuple[list, list]:
    vector = FakeEmbedder().embed_query(query)
    with Session(engine) as session:
        lexical = filtered_lexical_search(session, query, spec, settings.search_lexical_k).hits
        dense = filtered_dense_search(
            session, vector, settings.embedding_spec(), spec, settings.search_dense_k
        ).hits
    return lexical, dense


# ------------------------------------------------------------------------- oracle agreement


@QUERY_PARAMS
@pytest.mark.parametrize("depth", [20, 5])
def test_reranked_search_matches_the_independent_oracles(
    settings, world, monkeypatch, label, depth
):
    row_id, ignored = QUERIES[label]
    row = ROWS[row_id]
    assert row.parsed
    with make_client(settings, world["engine"], search_rerank_depth=depth) as client:
        log = attach(client)
        app_settings = client.app.state.settings
        v3_reads = spy(monkeypatch, reranked_api, "read_reranked_sources")
        v2_reads = spy(monkeypatch, reranked_api, "read_filtered_sources")
        got = call(client, "get", row.query, 50)
        posted = call(client, "post", row.query, 50)
        assert client.app.state.engine.pool.checkedout() == 0
        log.clear()
        v2 = call(client, "get", row.query, 50, path="/search/filtered")
    assert got.status_code == posted.status_code == v2.status_code == 200
    a = without_latency(got.json())
    assert a == without_latency(posted.json())  # GET/POST parity
    v2_json = v2.json()
    body = RerankedSearchResponse.model_validate(got.json())
    assert body.search_version == "v3_reranked" and body.filter_policy.version == "fp-1"
    assert a["applied_filters"] == expected_applied(row.spec) == v2_json["applied_filters"]
    assert a["ignored_constraints"] == ignored == v2_json["ignored_constraints"]
    assert a["query_understanding"] == v2_json["query_understanding"]
    assert a["query"] == row.query
    reranker = client.reranker
    latency = got.json()["latency_ms"]
    if row.query == "!!!":
        assert v3_reads == [] and v2_reads == [] and client.embedder.load_calls == 0
        assert a["results"] == [] and a["reranker"]["status"] == "skipped_no_searchable_text"
        assert reranker.load_calls == 0 and not reranker.score_calls
        assert all(latency[key] is None for key in V3_STAGES)
        return
    assert len(v3_reads) == 2 and v2_reads == []  # one enabled read per request, no V2 retry
    for args in v3_reads:
        assert args[1] == row.query and args[4] == row.spec
        assert args[5:] == (app_settings.search_lexical_k, app_settings.search_dense_k)
    # No connection was checked out while any model loaded or ran.
    assert all(n == 0 for n in client.embedder.checked_out)
    assert all(n == 0 for _stage, n in reranker.checked_out)
    # Direct, committed filtered reads are the source oracle; RRF is recomputed exactly.
    lexical, dense = direct_sources(world["engine"], row.query, row.spec, app_settings)
    lexical_ids = [hit.product_id for hit in lexical]
    dense_ids = [hit.product_id for hit in dense]
    assert a["lexical_hit_count"] == len(lexical) == v2_json["lexical_hit_count"]
    assert a["dense_hit_count"] == len(dense) == v2_json["dense_hit_count"]
    assert a["overlap_count"] == len(set(lexical_ids) & set(dense_ids))
    assert a["fused_count"] == len(set(lexical_ids) | set(dense_ids)) == v2_json["fused_count"]
    fused = rrf_oracle(lexical_ids, dense_ids, a["fusion"]["rrf_k"])
    fused = fused[: app_settings.search_candidate_k]
    assert a["candidate_count"] == len(fused) == v2_json["candidate_count"]
    rrf_ids = [pid for pid, _score in fused]
    assert [r["product_id"] for r in v2_json["results"]] == rrf_ids  # V2 is the RRF order
    expected = rerank_oracle(rrf_ids, depth)
    got_rows = [
        (r["product_id"], r["rrf_rank"], r["rerank_rank"], r["rerank_score"]) for r in a["results"]
    ]
    assert got_rows == expected
    assert sorted(r["product_id"] for r in a["results"]) == sorted(rrf_ids)  # a permutation
    assert [r["rank"] for r in a["results"]] == list(range(1, len(expected) + 1))
    by_lexical = {hit.product_id: hit for hit in lexical}
    by_dense = {hit.product_id: hit for hit in dense}
    rrf_scores = dict(fused)
    for result in a["results"]:
        pid = result["product_id"]
        assert satisfies(world["facts"][pid], row.spec), pid  # independent catalog predicate
        assert result["rrf_score"] == float(rrf_scores[pid])
        lex, den = by_lexical.get(pid), by_dense.get(pid)
        assert result["lexical_rank"] == (lex and lex.rank)
        assert result["lexical_score"] == (lex and lex.lexical_score)
        assert result["dense_rank"] == (den and den.rank)
        assert result["dense_score"] == (den and den.dense_score)
        source = lex or den
        assert result["title"] == source.title and result["price"] == f"{source.price:.2f}"
    size = min(depth, len(rrf_ids))
    block = a["reranker"]
    assert block == {
        "enabled": True,
        "status": "applied" if rrf_ids else "skipped_no_candidates",
        "model_id": "cross-encoder/ms-marco-MiniLM-L6-v2",
        "model_revision": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
        "document_version": "rd-1",
        "max_length": 512,
        "rerank_depth": depth,
        "reranked_count": size,
        "score_type": "logit",
    }
    if rrf_ids:
        # Each request scored the head once with the seed-built rd-1 documents, nothing else.
        head_documents = [SEED_DOCUMENTS[pid] for pid in rrf_ids[:size]]
        assert reranker.score_calls == [(row.query, head_documents)] * 2
        assert reranker.count_calls == [(row.query, head_documents)] * 2
        assert reranker.load_calls == 2
        assert all(latency[key] is not None for key in ("document_read_ms", "rerank_document_ms"))
        assert latency["reranker_load_ms"] == 1.0  # the first request loaded the fake model
    else:
        # Valid filters, no eligible product: 200, filters kept, no record read, no reranker.
        assert lexical == [] and dense == []
        assert a["results"] == [] and a["applied_filters"] == expected_applied(row.spec)
        assert reranker.load_calls == 0 and not reranker.score_calls
        assert all(latency[key] is None for key in V3_STAGES)
    if row.expect == "empty":  # predeclared: no product satisfies the filters
        assert oracle_eligible(world["facts"], row.spec) == set() and a["results"] == []
    elif row.id != "full":  # "full": its one eligible product has a stale embedding
        assert a["results"] and a["dense_hit_count"] > 0  # non-vacuous comparison


@pytest.mark.parametrize("label", ["english", "combined-ram", "conflict-brand", "hinglish-intent"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_disabled_reranking_is_exactly_v2(settings, world, monkeypatch, label, method):
    row = ROWS[QUERIES[label][0]]
    with make_client(settings, world["engine"], reranker_enabled=False) as client:
        log = attach(client)
        v3_reads = spy(monkeypatch, reranked_api, "read_reranked_sources")
        records = spy(monkeypatch, reranked_search, "_read_candidate_rows")
        v3 = call(client, method, row.query, 50)
        v2 = call(client, method, row.query, 50, path="/search/filtered")
    assert v3.status_code == v2.status_code == 200
    body, v2_body = v3.json(), v2.json()
    assert body["results"]  # non-vacuous
    assert v3_reads == [] and records == [] and "records" not in log
    assert log.count("BEGIN") == 2 and log.count("COMMIT") == 2  # one transaction each
    reranker = client.reranker
    assert reranker.load_calls == 0 and not reranker.count_calls and not reranker.score_calls
    assert body["reranker"]["status"] == "disabled" and body["reranker"]["reranked_count"] == 0
    assert all(body["latency_ms"][key] is None for key in V3_STAGES)
    stripped = without_latency(body)
    stripped.pop("reranker")
    for payload in (stripped, v2_body):
        payload.pop("search_version")
        payload.pop("latency_ms", None)
    n = len(stripped["results"])
    assert [r.pop("rrf_rank") for r in stripped["results"]] == list(range(1, n + 1))
    assert all(r.pop("rerank_rank") is None for r in stripped["results"])
    assert all(r.pop("rerank_score") is None for r in stripped["results"])
    assert stripped == v2_body


@pytest.mark.parametrize("query", ["nike laptop", "phone ssd", "from 20k ke andar"])
def test_zero_matches_read_no_records_and_never_touch_the_reranker(settings, world, query):
    with make_client(settings, world["engine"]) as client:
        assert client.get("/health").status_code == 200  # warm-up: dialect initialization
        log = attach(client)
        response = call(client, "get", query)
    body = response.json()
    assert response.status_code == 200 and body["results"] == [] and body["applied_filters"]
    assert body["reranker"]["status"] == "skipped_no_candidates"
    assert body["reranker"]["reranked_count"] == 0
    assert all(body["latency_ms"][key] is None for key in V3_STAGES)
    # One transaction: snapshot, tsquery, filtered lexical, filtered dense; no record read.
    assert log[0] == "BEGIN" and log[-1] == "COMMIT" and len(log) == 6
    assert log[1].startswith("SET TRANSACTION ISOLATION")
    assert "records" not in log and "spec-records" not in log
    assert client.reranker.load_calls == 0
    assert "fallback" not in response.text.lower()


def test_top_k_above_depth_exposes_only_a_null_scored_tail(settings, world):
    with make_client(settings, world["engine"], search_rerank_depth=3) as client:
        attach(client)
        body = call(client, "get", "comfortable gift", 12).json()
    assert body["result_count"] == 12 and body["reranker"]["reranked_count"] == 3
    head, tail = body["results"][:3], body["results"][3:]
    assert sorted(r["rrf_rank"] for r in head) == [1, 2, 3]
    assert [r["rerank_rank"] for r in head] == [1, 2, 3]
    assert all(r["rerank_score"] is not None for r in head)
    assert [r["rrf_rank"] for r in tail] == list(range(4, 13))
    assert all(r["rerank_rank"] is None and r["rerank_score"] is None for r in tail)


def test_repeated_requests_are_deterministic(settings, world):
    with make_client(settings, world["engine"]) as client:
        attach(client)
        bodies = [
            without_latency(call(client, "get", "hp laptop 8gb ram").json()) for _ in range(3)
        ]
    assert bodies[0]["results"] and bodies[0] == bodies[1] == bodies[2]


# ------------------------------------------------------------------------- failures


class UnevidencedProvider:
    """A well-formed decision whose brand has no matched-term evidence: fp-1 refuses it."""

    def understand(self, query, deterministic_result):
        return DecisionResult(understanding=QueryUnderstanding(raw_query=query, brand="HP"))


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def api_log():
    # Alembic's fileConfig replaces the root handlers, so the route logger gets its own.
    records = _Records()
    route_logger = logging.getLogger(reranked_api.__name__)
    route_logger.addHandler(records)
    yield records
    route_logger.removeHandler(records)


def pool_events(client) -> list:
    events: list = []
    pool = client.app.state.engine.pool
    event.listen(pool, "checkout", lambda *args: events.append("out"))
    event.listen(pool, "checkin", lambda *args: events.append("in"))
    return events


@pytest.mark.parametrize("failure", ["provider", "derive"])
def test_policy_failure_checks_out_no_connection(settings, world, monkeypatch, api_log, failure):
    provider = UnevidencedProvider() if failure == "provider" else None
    if failure == "derive":

        def broken(decision):
            raise RuntimeError("policy failure with secret detail")

        monkeypatch.setattr(reranked_api, "derive_filters", broken)
    with make_client(settings, world["engine"], provider=provider) as client:
        attach(client)
        checkouts = pool_events(client)
        got = call(client, "get", "hp laptop")
        posted = call(client, "post", "hp laptop")
        assert client.app.state.engine.pool.checkedout() == 0
    for response in (got, posted):
        assert response.status_code == 503 and response.json() == FIXED_503
    assert checkouts == []  # no engine checkout at all
    assert client.embedder.load_calls == 0 and client.reranker.load_calls == 0
    assert api_log.messages == ["reranked search unavailable: filter_policy_failed"] * 2
    assert not any("secret" in m or "hp laptop" in m for m in api_log.messages)


def test_record_database_failure_rolls_back_releases_and_never_retries(
    settings, world, monkeypatch, api_log
):
    broken = text(
        "SELECT product_id FROM no_such_table WHERE product_id = ANY(CAST(:product_ids AS text[]))"
    )
    monkeypatch.setattr(reranked_search, "CANDIDATE_PRODUCTS_SQL", broken)
    with make_client(settings, world["engine"]) as client:
        assert client.get("/health").status_code == 200  # warm-up: dialect initialization
        log = attach(client)
        v3_reads = spy(monkeypatch, reranked_api, "read_reranked_sources")
        v2_reads = spy(monkeypatch, reranked_api, "read_filtered_sources")
        fusions = spy(monkeypatch, reranked_api, "fuse_rrf")
        pool = pool_events(client)
        response = call(client, "get", "hp laptop")
        assert client.app.state.engine.pool.checkedout() == 0
    assert response.status_code == 503 and response.json() == FIXED_503
    assert "no_such_table" not in response.text
    assert len(v3_reads) == 1 and v2_reads == [] and fusions == []  # one attempt, no fallback
    assert log[0] == "BEGIN" and log[-1] == "ROLLBACK" and "COMMIT" not in log
    assert log.count("BEGIN") == 1 and "records" not in log
    assert pool == ["out", "in"]  # one checkout, released
    assert client.reranker.load_calls == 0
    assert len(api_log.messages) == 1
    assert api_log.messages[0].startswith("reranked search unavailable: table_missing")
    assert "SELECT" not in api_log.messages[0] and "hp laptop" not in api_log.messages[0]


def test_a_missing_spec_row_is_document_build_failed_and_v2_still_serves(
    settings, world, monkeypatch, api_log
):
    engine = world["engine"]
    query = "hp laptop"
    victim = "SYN-LAP-0057"  # an HP laptop in this query's source union
    with engine.begin() as conn:
        saved = (
            conn.execute(text("SELECT * FROM laptop_specs WHERE product_id = :p"), {"p": victim})
            .mappings()
            .one()
        )
        conn.execute(text("DELETE FROM laptop_specs WHERE product_id = :p"), {"p": victim})
    try:
        with make_client(settings, engine) as client:
            assert client.get("/health").status_code == 200
            log = attach(client)
            v2_reads = spy(monkeypatch, reranked_api, "read_filtered_sources")
            fusions = spy(monkeypatch, reranked_api, "fuse_rrf")
            pool = pool_events(client)
            response = call(client, "post", query, 50)
            v2 = call(client, "post", query, 50, path="/search/filtered")
            assert client.app.state.engine.pool.checkedout() == 0
    finally:
        with engine.begin() as conn:
            columns = ", ".join(saved)
            values = ", ".join(f":{key}" for key in saved)
            conn.execute(
                text(f"INSERT INTO laptop_specs ({columns}) VALUES ({values})"),  # noqa: S608
                dict(saved),
            )
    assert response.status_code == 503 and response.json() == FIXED_503
    assert v2.status_code == 200 and victim in {r["product_id"] for r in v2.json()["results"]}
    assert api_log.messages == ["reranked search unavailable: document_build_failed"]
    # The V3 snapshot committed normally; the failure came after it, before fusion or reranker.
    v3_part = log[: log.index("COMMIT") + 1]
    assert v3_part[0] == "BEGIN" and "records" in v3_part and "ROLLBACK" not in log
    assert v2_reads == [] and fusions == [] and client.reranker.load_calls == 0
    assert pool == ["out", "in", "out", "in"]  # V3 then the separate V2 request
    assert catalog_facts(engine) == world["facts"]  # restored


@pytest.mark.parametrize(
    ("stage", "reason"),
    [("load", "load_failed"), ("count", "score_failed"), ("score", "score_failed")],
)
def test_reranker_failure_happens_after_the_connection_is_released(
    settings, world, monkeypatch, api_log, stage, reason
):
    reranker = ScriptedReranker()
    reranker.failures[stage] = RerankerUnavailable(reason)
    with make_client(settings, world["engine"], reranker=reranker) as client:
        assert client.get("/health").status_code == 200
        log = attach(client)
        v3_reads = spy(monkeypatch, reranked_api, "read_reranked_sources")
        v2_reads = spy(monkeypatch, reranked_api, "read_filtered_sources")
        pool = pool_events(client)
        response = call(client, "get", "hp laptop")
    assert response.status_code == 503 and response.json() == FIXED_503
    assert api_log.messages == [f"reranked search unavailable: reranker {reason}"]
    assert log[0] == "BEGIN" and log[-1] == "COMMIT" and log.count("BEGIN") == 1
    assert "records" in log and "ROLLBACK" not in log
    assert pool == ["out", "in"]
    assert len(v3_reads) == 1 and v2_reads == []  # no V2 order returned instead, no retry
    assert reranker.checked_out and all(n == 0 for _stage, n in reranker.checked_out)
    assert len(reranker.score_calls) == (1 if stage == "score" else 0)
