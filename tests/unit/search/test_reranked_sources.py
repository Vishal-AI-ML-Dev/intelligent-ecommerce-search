"""`search/reranked.py` (Milestone 8, V3): the reranked source read and candidate records, with an
instrumented fake session and fake filtered source reads (no database, no model).

The fake session answers the candidate-record statements from an in-test set of raw catalog
records; the expected records are built independently with `parse_raw` on those raw records.
"""

import re
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from ecommerce_search.catalog.mapping import spec_columns
from ecommerce_search.catalog.schemas import CORE_KEYS, parse_raw
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.embeddings.spec import ALL_MINILM_L6_V2
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.models.catalog import SPEC_TABLES
from ecommerce_search.reranking.documents import RerankDocumentError
from ecommerce_search.search import filtered as filtered_search
from ecommerce_search.search import reranked as reranked_search
from ecommerce_search.search.dense import DenseHit, DenseResult
from ecommerce_search.search.hybrid import SNAPSHOT_SQL
from ecommerce_search.search.lexical import LexicalHit, LexicalResult

SENSITIVE = "host=internal-db.example password=hunter2 SELECT * FROM products D:\\secret\\path"
SNAPSHOT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"

# ---- in-test raw catalog -------------------------------------------------------------------------

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
RAW = {
    "L1": {
        **_CORE,
        "product_id": "L1",
        "title": "HP Laptop 8GB RAM 256GB SSD",
        "category": "laptop",
        "brand": "HP",
        "price": Decimal("33999.00"),
        "ram_gb": 8,
        "storage_gb": 256,
        "storage_type": "SSD",
        "storage_interface": "SATA",
    },
    "L2": {
        **_CORE,
        "product_id": "L2",
        "title": "Dell Gaming Laptop 16GB RAM",
        "category": "laptop",
        "brand": "Dell",
        "price": Decimal("55999.00"),
        "ram_gb": 16,
        "storage_gb": 512,
        "storage_type": "SSD",
        "storage_interface": "NVME",
    },
    "P1": {
        **_CORE,
        "product_id": "P1",
        "title": "Samsung Phone 8GB RAM",
        "category": "phone",
        "brand": "Samsung",
        "price": Decimal("26999.00"),
        "ram_gb": 8,
        "storage_gb": 256,
    },
    "S1": {
        **_CORE,
        "product_id": "S1",
        "title": "Nike Running Shoes",
        "category": "shoes",
        "brand": "Nike",
        "price": Decimal("4299.00"),
    },
    "H1": {
        **_CORE,
        "product_id": "H1",
        "title": "Sony Wireless Headphones",
        "category": "headphones",
        "brand": "Sony",
        "price": Decimal("1999.00"),
        "wireless": True,
        "connectivity": "bluetooth",
    },
}


def product_row(raw: dict) -> SimpleNamespace:
    return SimpleNamespace(**{name: raw.get(name) for name in CORE_KEYS})


def spec_row(raw: dict) -> SimpleNamespace:
    category = Category(raw["category"])
    values = {name: raw.get(name) for name in spec_columns(category)}
    return SimpleNamespace(product_id=raw["product_id"], category=raw["category"], **values)


def expected_records(ids) -> dict:
    return {pid: parse_raw(dict(RAW[pid])) for pid in ids}


def _hit(cls, pid, rank, **score):
    raw = RAW[pid]
    return cls(
        rank=rank,
        product_id=pid,
        title=raw["title"],
        brand=raw["brand"],
        category=raw["category"],
        subcategory=None,
        description=raw["description"],
        price=raw["price"],
        currency="INR",
        rating=raw["rating"],
        review_count=3,
        availability="in_stock",
        **score,
    )


def lexical_result(ids) -> LexicalResult:
    hits = [_hit(LexicalHit, pid, r, lexical_score=1.0 / r) for r, pid in enumerate(ids, 1)]
    return LexicalResult(tsquery="'x'", hits=hits, lexical_ms=1.5)


def dense_result(ids) -> DenseResult:
    hits = [_hit(DenseHit, pid, r, dense_score=1.0 - r / 100) for r, pid in enumerate(ids, 1)]
    return DenseResult(hits=hits, vector_ms=2.5)


# ---- fakes ---------------------------------------------------------------------------------------

STATEMENT_NAMES = {
    id(reranked_search.CANDIDATE_PRODUCTS_SQL): "products",
    **{id(stmt): f"spec:{cat.value}" for cat, stmt in reranked_search.CANDIDATE_SPEC_SQL.items()},
    id(SNAPSHOT_SQL): "snapshot",
}


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class RecordSession:
    """Transaction events plus the candidate-record statements answered from `RAW`.

    `products` / `specs` override the rows returned for a statement (to inject bad rows)."""

    def __init__(self, events: list, errors: dict | None = None) -> None:
        self.events = events
        self.errors = errors or {}
        self.products = None  # callable(ids) -> rows
        self.specs = None  # callable(category, ids) -> rows
        self.statements: list = []
        self._open = False

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
        name = STATEMENT_NAMES[id(statement)]
        self.statements.append((statement, dict(params or {})))
        self.events.append(name)
        if name in self.errors:
            raise self.errors[name]
        if name == "snapshot":
            return None
        ids = params["product_ids"]
        if name == "products":
            rows = (
                self.products(ids)
                if self.products
                else [product_row(RAW[pid]) for pid in ids if pid in RAW]
            )
            return _Result(rows)
        category = Category(name.split(":", 1)[1])
        if self.specs:
            return _Result(self.specs(category, ids))
        return _Result(spec_row(RAW[pid]) for pid in ids if RAW[pid]["category"] == category)


@pytest.fixture
def events():
    return []


@pytest.fixture
def sources(monkeypatch, events):
    """Fake filtered lexical / dense reads; returns a mutable config of the ids they return."""
    config = SimpleNamespace(lexical=["L1", "P1", "S1"], dense=["H1", "L1"], error=None)

    def fake_lexical(session, query, filters, limit):
        assert session.in_transaction()
        events.append(("lexical", query, filters, limit))
        if config.error == "lexical":
            raise OperationalError(SENSITIVE, {}, Exception(SENSITIVE))
        return lexical_result(config.lexical)

    def fake_dense(session, vector, spec, filters, limit):
        assert session.in_transaction()
        events.append(("dense", tuple(vector), spec, filters, limit))
        return dense_result(config.dense)

    monkeypatch.setattr(filtered_search, "filtered_lexical_search", fake_lexical)
    monkeypatch.setattr(filtered_search, "filtered_dense_search", fake_dense)
    return config


FILTERS = FilterSpec(ram_gb=8)
VECTOR = (0.6, 0.8)


def read(session, filters=FILTERS):
    return reranked_search.read_reranked_sources(
        session, "hp 8gb", list(VECTOR), ALL_MINILM_L6_V2, filters, 7, 9
    )


def names(events):
    return [e if isinstance(e, str) else e[0] for e in events]


# ---- transaction and statement order -------------------------------------------------------------


def test_one_snapshot_with_sources_then_records_then_commit(sources, events):
    session = RecordSession(events)
    result = read(session)
    assert names(events) == [
        "begin",
        "snapshot",  # first statement of the transaction
        "lexical",
        "dense",
        "products",
        "spec:laptop",
        "spec:phone",
        "spec:shoes",
        "spec:headphones",
        "commit",
    ]
    assert not session.in_transaction()
    assert result.lexical == lexical_result(sources.lexical)
    assert result.dense == dense_result(sources.dense)
    assert dict(result.records) == expected_records(["H1", "L1", "P1", "S1"])
    assert list(result.records) == ["H1", "L1", "P1", "S1"]  # deterministic, by product id
    assert result.document_read_ms is not None and result.document_read_ms >= 0


def test_sources_receive_exactly_the_v2_arguments(sources, events):
    v2_events: list = []
    session = RecordSession(events)
    read(session)
    reranked_calls = [e for e in events if isinstance(e, tuple)]
    # The same filtered functions, called by V2 with the same inputs, see identical arguments.
    events.clear()
    lexical, dense = filtered_search.read_filtered_sources(
        RecordSession(v2_events), "hp 8gb", list(VECTOR), ALL_MINILM_L6_V2, FILTERS, 7, 9
    )
    assert reranked_calls == [e for e in events if isinstance(e, tuple)]
    assert reranked_calls == [
        ("lexical", "hp 8gb", FILTERS, 7),
        ("dense", VECTOR, ALL_MINILM_L6_V2, FILTERS, 9),
    ]
    assert names(v2_events) == ["begin", "snapshot", "commit"]  # V2 reads no records
    assert (lexical, dense) == (lexical_result(sources.lexical), dense_result(sources.dense))


def test_only_categories_present_in_the_union_are_read(sources, events):
    sources.lexical, sources.dense = ["L2"], ["L1", "L2"]
    session = RecordSession(events)
    result = read(session)
    assert names(events) == [
        "begin",
        "snapshot",
        "lexical",
        "dense",
        "products",
        "spec:laptop",
        "commit",
    ]
    assert list(result.records) == ["L1", "L2"]


def test_empty_source_union_reads_no_records(sources, events):
    sources.lexical, sources.dense = [], []
    session = RecordSession(events)
    result = read(session)
    assert names(events) == ["begin", "snapshot", "lexical", "dense", "commit"]
    assert dict(result.records) == {} and result.document_read_ms is None
    assert result.lexical.hits == [] and result.dense.hits == []


def test_record_statements_are_constant_and_bind_ids_as_one_array(sources, events):
    session = RecordSession(events)
    read(session)
    record_statements = [(s, p) for s, p in session.statements if s is not SNAPSHOT_SQL]
    assert [p for _s, p in record_statements] == [
        {"product_ids": ["H1", "L1", "P1", "S1"]},  # the sorted source-ID union
        {"product_ids": ["L1"]},
        {"product_ids": ["P1"]},
        {"product_ids": ["S1"]},
        {"product_ids": ["H1"]},
    ]
    for statement, _params in record_statements:
        sql = str(statement)
        assert ":product_ids" in sql
        for pid in RAW:
            assert not re.search(rf"\b{pid}\b", sql)
        # Read-only: nothing but a SELECT, and the bind compiles to a psycopg parameter.
        assert sql.strip().upper().startswith("SELECT")
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "LOCK"):
            assert not re.search(rf"\b{verb}\b", sql.upper())
        compiled = str(statement.compile(dialect=postgresql.psycopg.dialect()))
        assert "%(product_ids)s" in compiled


def _select_columns(statement) -> list[str]:
    sql = " ".join(str(statement).split())
    columns = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    return [column.strip().split(".", 1)[1] for column in columns.split(",")]


def _from_table(statement) -> str:
    return " ".join(str(statement).split()).split(" FROM ", 1)[1].split(" ", 1)[0]


def test_record_statement_columns_match_the_catalog_schema():
    assert _select_columns(reranked_search.CANDIDATE_PRODUCTS_SQL) == list(CORE_KEYS)
    assert _from_table(reranked_search.CANDIDATE_PRODUCTS_SQL) == "products"
    assert list(reranked_search.CANDIDATE_SPEC_SQL) == list(Category)
    for category, statement in reranked_search.CANDIDATE_SPEC_SQL.items():
        assert _select_columns(statement) == ["product_id", "category", *spec_columns(category)]
        assert _from_table(statement) == SPEC_TABLES[category].__tablename__


def test_record_values_reach_the_record_through_parse_raw(sources, events):
    sources.lexical, sources.dense = ["H1"], []
    result = read(RecordSession(events))
    record = result.records["H1"]
    assert record == parse_raw(dict(RAW["H1"]))
    assert record.spec.wireless is True and record.price == Decimal("1999.00")


# ---- guards and failures ---------------------------------------------------------------------------


def test_open_transaction_is_refused_before_any_statement(sources, events):
    session = RecordSession(events)
    session._open = True
    with pytest.raises(RuntimeError, match="without an open transaction"):
        read(session)
    assert events == []


def test_non_filter_spec_is_refused_before_any_statement(sources, events):
    with pytest.raises(TypeError):
        read(RecordSession(events), filters={"ram_gb": 8})
    assert events == []


@pytest.mark.parametrize("failing", ["snapshot", "lexical", "products", "spec:phone"])
def test_database_errors_roll_back_and_propagate(sources, events, failing):
    error = OperationalError(SENSITIVE, {}, Exception(SENSITIVE))
    session = RecordSession(events, errors={} if failing == "lexical" else {failing: error})
    if failing == "lexical":
        sources.error = "lexical"
    with pytest.raises(OperationalError):
        read(session)
    assert names(events)[-1] == "rollback" and "commit" not in events
    assert not session.in_transaction()
    assert names(events).index(failing) == len(events) - 2  # nothing ran after the failure


def _drop(pid):
    return lambda ids: [product_row(RAW[p]) for p in ids if p != pid]


def _with_extra(rows_for):
    return lambda ids: rows_for(ids) + [product_row(RAW["L2"])]


def _all_products(ids):
    return [product_row(RAW[p]) for p in ids]


def _bad_product(pid, **changes):
    def rows(ids):
        out = []
        for p in ids:
            raw = dict(RAW[p])
            if p == pid:
                raw.update(changes)
            out.append(product_row(raw))
        return out

    return rows


def _default_specs(category, ids):
    return [spec_row(RAW[p]) for p in ids if RAW[p]["category"] == category]


def _specs_without(pid):
    return lambda category, ids: [r for r in _default_specs(category, ids) if r.product_id != pid]


def _duplicate_spec(pid):
    def rows(category, ids):
        out = _default_specs(category, ids)
        return out + [r for r in out if r.product_id == pid]

    return rows


def _spec_category(pid, value):
    def rows(category, ids):
        out = _default_specs(category, ids)
        for row in out:
            if row.product_id == pid:
                row.category = value
        return out

    return rows


def _spec_value(pid, **changes):
    def rows(category, ids):
        out = _default_specs(category, ids)
        for row in out:
            if row.product_id == pid:
                for key, value in changes.items():
                    setattr(row, key, value)
        return out

    return rows


def _foreign_spec(category, ids):
    # A spec row of a product outside the requested ids.
    return _default_specs(category, ids) + (
        [spec_row(RAW["L2"])] if category.value == "laptop" else []
    )


BAD_ROWS = {
    "missing product row": (_drop("P1"), None),
    "duplicate product row": (lambda ids: _all_products(ids) + [product_row(RAW["P1"])], None),
    "unexpected product row": (_with_extra(_all_products), None),
    "missing spec row": (None, _specs_without("S1")),
    "duplicate spec row": (None, _duplicate_spec("L1")),
    "spec row of another product": (None, _foreign_spec),
    "spec row category mismatch": (None, _spec_category("L1", "phone")),
    "unknown product category": (_bad_product("H1", category="toys"), None),
    "invalid stored price": (_bad_product("L1", price=Decimal("-1")), None),
    "invalid stored attribute": (None, _spec_value("L1", storage_type="FLOPPY " + SENSITIVE)),
    "invalid stored title": (_bad_product("S1", title="   "), None),
}


@pytest.mark.parametrize(("products", "specs"), BAD_ROWS.values(), ids=BAD_ROWS.keys())
def test_bad_record_rows_are_a_fixed_document_build_failure_after_commit(
    sources, events, products, specs
):
    session = RecordSession(events)
    session.products, session.specs = products, specs
    with pytest.raises(RerankDocumentError) as info:
        read(session)
    assert str(info.value) == "reranker document unavailable: document_build_failed"
    # No chained exception can carry stored data or SQL text out of the boundary.
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__
    assert events[-1] == "commit" and "rollback" not in events  # the snapshot ended normally
    assert not session.in_transaction()


def test_a_record_for_another_product_is_refused(sources, events, monkeypatch):
    original = reranked_search.parse_raw

    def other(raw):
        return original({**raw, "product_id": "ZZ9"})

    monkeypatch.setattr(reranked_search, "parse_raw", other)
    with pytest.raises(RerankDocumentError):
        read(RecordSession(events))


def test_build_candidate_records_is_pure_and_exact():
    ids = ["L1", "P1"]
    products = [product_row(RAW[p]) for p in ids]
    specs = {
        Category.LAPTOP: [spec_row(RAW["L1"])],
        Category.PHONE: [spec_row(RAW["P1"])],
    }
    snapshot = (list(products), {k: list(v) for k, v in specs.items()})
    records = reranked_search.build_candidate_records(ids, products, specs)
    assert records == expected_records(ids)
    assert (products, specs) == snapshot  # inputs untouched
    with pytest.raises(RerankDocumentError):  # a spec row in the wrong category's list
        reranked_search.build_candidate_records(
            ids, products, {Category.PHONE: [spec_row(RAW["L1"]), spec_row(RAW["P1"])]}
        )


def test_no_fusion_document_or_model_work_happens_in_this_module():
    import ast
    from pathlib import Path

    source = Path(reranked_search.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
    for forbidden in ("fuse_rrf", "build_rerank_document", "score_documents", "rerank_head"):
        assert not any(name.endswith(forbidden) or forbidden in name for name in imported)
        assert f"{forbidden}(" not in source
    assert not {"torch", "sentence_transformers", "transformers"} & {
        name.split(".")[0] for name in imported
    }
