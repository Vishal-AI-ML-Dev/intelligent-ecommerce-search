"""`search/reranked.py` (Milestone 8, V3) against real PostgreSQL scratch databases (fake embedder
only; the development database is never touched).

Independent evidence:

* Expected records come from the committed seed file (`load_catalog(SEED)`), not from a database
  read, adjusted only by the deliberate NULL mutations written below.
* Source lists are compared with the committed V2 `read_filtered_sources` on the same catalog.
* Array binding is checked with plain SQL counts and the statement text the driver received.
* Damaged-record cases change the module-owned scratch catalog and restore it afterwards; the
  restore is verified against a content digest of every catalog table.
"""

import hashlib
import json
from decimal import Decimal

import pytest
from catalog_support import SEED
from dense_support import embed_fake
from filter_support import (
    MISSING_EMBEDDING,
    NULL_RAM_LAPTOP,
    NULL_STORAGE_TYPE_LAPTOP,
    STALE_EMBEDDING,
    catalog_facts,
    current_embedding_ids,
    oracle_eligible,
)
from sqlalchemy import event, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from ecommerce_search.catalog.schemas import parse_raw
from ecommerce_search.catalog.taxonomy import Category, StorageType
from ecommerce_search.embeddings.spec import ALL_MINILM_L6_V2
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.reranking.documents import RerankDocumentError
from ecommerce_search.search import reranked as reranked_search
from ecommerce_search.search.filtered import read_filtered_sources
from ecommerce_search.search.reranked import (
    CANDIDATE_PRODUCTS_SQL,
    CANDIDATE_SPEC_SQL,
    read_reranked_sources,
)
from fake_embedder import FakeEmbedder

pytestmark = pytest.mark.integration

SPEC = ALL_MINILM_L6_V2
LEXICAL_K = DENSE_K = 50
INJECTION_IDS = [
    "x'); DROP TABLE products; --",
    "SYN-LAP-0001' OR '1'='1",
    'SYN-LAP-0001"',
    "%",
    "_",
    "*",
    "{SYN-LAP-0001,SYN-PHN-0001}",
    "SYN-LAP-0001,SYN-PHN-0001",
    "NULL",
    "",
]
# Category -> spec table, in taxonomy order (written by hand from the catalog schema).
SPEC_TABLE_ORDER = (
    ("laptop", "laptop_specs"),
    ("phone", "phone_specs"),
    ("shoes", "shoe_specs"),
    ("headphones", "headphone_specs"),
)
CATALOG_TABLES = (
    "products",
    "laptop_specs",
    "phone_specs",
    "shoe_specs",
    "headphone_specs",
    "product_embeddings",
)


# ------------------------------------------------------------------------------ catalog state


def _seed_raw() -> dict[str, dict]:
    """product_id -> the committed seed's raw record (a plain dict per line)."""
    out = {}
    for line in SEED.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            out[raw["product_id"]] = raw
    return out


SEED_RAW = _seed_raw()
# The NULL mutations this module applies (valid records; only their values change).
NULLED = {
    NULL_RAM_LAPTOP: {"ram_gb": None},
    NULL_STORAGE_TYPE_LAPTOP: {"storage_type": None, "storage_interface": None},
}


def expected_record(product_id: str):
    """The record the database should hold: the seed line plus this module's NULL mutations."""
    return parse_raw({**SEED_RAW[product_id], **NULLED.get(product_id, {})})


def apply_valid_mutations(engine) -> None:
    """Stale / missing embeddings and NULL spec values: every product keeps a valid record."""
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


def catalog_digest(engine) -> str:
    digest = hashlib.sha256()
    with engine.connect() as conn:
        for table in CATALOG_TABLES:
            rows = conn.execute(text(f"SELECT * FROM {table} ORDER BY product_id")).all()  # noqa: S608
            digest.update(repr([tuple(row) for row in rows]).encode())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def world(writable_seeded_engine):
    engine = writable_seeded_engine
    assert engine.url.database.startswith("ecommerce_search_test_")
    embed_fake(engine)
    apply_valid_mutations(engine)
    return {"engine": engine, "facts": catalog_facts(engine), "digest": catalog_digest(engine)}


def classify(statement: str) -> str:
    flat = " ".join(statement.split())
    if flat.startswith("SET TRANSACTION"):
        return "snapshot"
    if "ts_rank" in flat:
        return "lexical"
    if "<=>" in flat:
        return "dense"
    if "numnode" in flat:
        return "tsquery"
    if "p.seller_id" in flat:
        return "products"
    for table in ("laptop_specs", "phone_specs", "shoe_specs", "headphone_specs"):
        if f"FROM {table} s WHERE s.product_id = ANY" in flat:
            return f"spec:{table}"
    return flat[:40]


def record_log(engine) -> tuple[list, list]:
    """Transaction events + classified statements, and (statement, parameters) pairs."""
    log: list = []
    sent: list = []

    def before(conn, cursor, statement, params, context, many):
        log.append(classify(statement))
        sent.append((statement, params))

    event.listen(engine, "begin", lambda conn: log.append("BEGIN"))
    event.listen(engine, "commit", lambda conn: log.append("COMMIT"))
    event.listen(engine, "rollback", lambda conn: log.append("ROLLBACK"))
    event.listen(engine, "before_cursor_execute", before)
    return log, sent


@pytest.fixture
def engine(world):
    """The module catalog through a dedicated engine, so event listeners never leak."""
    from sqlalchemy import create_engine

    own = create_engine(world["engine"].url)
    yield own
    assert own.pool.checkedout() == 0
    own.dispose()


def read(engine, query: str, filters: FilterSpec, lexical_k=LEXICAL_K, dense_k=DENSE_K):
    vector = FakeEmbedder().embed_query(query)
    with Session(engine) as session:
        result = read_reranked_sources(session, query, vector, SPEC, filters, lexical_k, dense_k)
        assert not session.in_transaction()
    return result


# ------------------------------------------------------------------------------ array binding


def _count(engine, statement, ids) -> list[str]:
    with engine.connect() as conn:
        return [row.product_id for row in conn.execute(statement, {"product_ids": ids}).all()]


def test_product_ids_bind_as_one_text_array_for_zero_one_and_many(engine, world):
    all_ids = sorted(world["facts"])
    assert len(all_ids) == 240
    log, sent = record_log(engine)
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, []) == []
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, ["SYN-SHO-0007"]) == ["SYN-SHO-0007"]
    many = all_ids[::2][:100]  # 100 ids from every category
    assert len(many) == 100 and {pid[:7] for pid in many} == {
        "SYN-LAP",
        "SYN-PHN",
        "SYN-SHO",
        "SYN-HDP",
    }
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, many) == many  # ordered, no duplicates
    # Duplicated and unknown ids never multiply or invent rows.
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, ["SYN-LAP-0001"] * 3 + ["NOPE"]) == [
        "SYN-LAP-0001"
    ]
    with engine.connect() as conn:
        full = conn.execute(text("SELECT count(*) FROM products")).scalar()
    assert full == 240
    # Every bound statement carried the ids as one list parameter, never in the SQL text.
    record_sends = [(s, p) for s, p in sent if "ANY" in s]
    assert len(record_sends) == 4
    for statement, params in record_sends:
        assert "ANY(CAST(%(product_ids)s AS text[]))" in " ".join(statement.split())
        assert isinstance(params["product_ids"], list)
        assert "SYN-" not in statement
    assert log.count("products") == 4


def test_injection_like_ids_stay_bound_values(engine, world):
    before = catalog_digest(engine)
    _log, sent = record_log(engine)
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, INJECTION_IDS) == []
    for category, statement in CANDIDATE_SPEC_SQL.items():
        assert _count(engine, statement, INJECTION_IDS) == [], category
    mixed = INJECTION_IDS + ["SYN-LAP-0001"]
    assert _count(engine, CANDIDATE_PRODUCTS_SQL, mixed) == ["SYN-LAP-0001"]
    for statement, params in sent:
        assert params["product_ids"] in (INJECTION_IDS, mixed)
        for value in INJECTION_IDS:
            if len(value) > 3:  # single characters such as % or _ occur in any SQL
                assert value not in statement
    assert catalog_digest(engine) == before == world["digest"]  # nothing was dropped or changed


@pytest.mark.parametrize("category", list(Category))
def test_spec_statements_return_exactly_their_categorys_rows(engine, world, category):
    by_category = {
        cat: sorted(pid for pid, fact in world["facts"].items() if fact["category"] == cat.value)
        for cat in Category
    }
    every = sorted(world["facts"])
    # Asked for every product id, a spec statement returns only (all) its own category.
    with engine.connect() as conn:
        rows = conn.execute(CANDIDATE_SPEC_SQL[category], {"product_ids": every}).all()
    assert [row.product_id for row in rows] == by_category[category]
    assert {row.category for row in rows} == {category.value}


# ------------------------------------------------------------------------------ the snapshot read


QUERIES = {
    # query, filters, expect an empty source union
    "unfiltered": ("comfortable gift", FilterSpec(), False),
    "laptop-ram": ("hp laptop 8gb ram", FilterSpec(category=Category.LAPTOP, ram_gb=8), False),
    "brand-across": ("apple", FilterSpec(brand="Apple"), False),
    "storage-type": ("ssd", FilterSpec(storage_type=StorageType.SSD), False),
    "price": ("headphones", FilterSpec(max_price=Decimal("2099.00")), False),
    "impossible": (
        "phone ssd",
        FilterSpec(category=Category.PHONE, storage_type=StorageType.SSD),
        True,
    ),
    "punctuation": ("!!!", FilterSpec(), False),  # lexical is empty, dense is not
}


@pytest.mark.parametrize("label", QUERIES, ids=QUERIES)
def test_sources_equal_v2_and_records_equal_the_seed(engine, world, label):
    query, filters, empty = QUERIES[label]
    vector = FakeEmbedder().embed_query(query)
    with Session(engine) as session:
        v2_lexical, v2_dense = read_filtered_sources(
            session, query, vector, SPEC, filters, LEXICAL_K, DENSE_K
        )
    log, _sent = record_log(engine)
    result = read(engine, query, filters)
    # The V2 reads, unchanged: ids, ranks, scores and display fields.
    assert result.lexical.tsquery == v2_lexical.tsquery
    assert result.lexical.hits == v2_lexical.hits and result.dense.hits == v2_dense.hits
    union = sorted({h.product_id for h in result.lexical.hits + result.dense.hits})
    assert union == sorted(dict(result.records))
    assert union == list(result.records)
    if empty:
        assert union == [] and result.document_read_ms is None
        assert oracle_eligible(world["facts"], filters) == set()
    else:
        assert union and result.document_read_ms is not None and result.document_read_ms >= 0
    for pid in union:
        assert result.records[pid] == expected_record(pid), pid
        assert world["facts"][pid]["category"] == result.records[pid].category.value
    # One transaction; the snapshot first; records read only for a non-empty union, one spec
    # statement per category present, nothing after the commit.
    present = {result.records[pid].category.value for pid in union}
    spec_reads = [f"spec:{table}" for category, table in SPEC_TABLE_ORDER if category in present]
    lexical_part = ["tsquery"] + (["lexical"] if result.lexical.tsquery else [])
    expected = ["BEGIN", "snapshot", *lexical_part, "dense"]
    if union:
        expected += ["products", *spec_reads]
    assert log == [*expected, "COMMIT"]
    # Stale and missing embeddings only shorten the dense list, exactly as in V2.
    current = current_embedding_ids(engine, SPEC)
    assert {h.product_id for h in result.dense.hits} <= current
    assert STALE_EMBEDDING not in current and MISSING_EMBEDDING not in current


def test_every_category_and_the_nulled_records_round_trip(engine, world):
    """Dense depth above the catalog size: the union is every product with a current
    embedding, so all four categories and both NULL-mutated laptops are rebuilt."""
    result = read(engine, "laptop", FilterSpec(), dense_k=1000)
    current = current_embedding_ids(engine, SPEC)
    union = {h.product_id for h in result.lexical.hits + result.dense.hits}
    assert {h.product_id for h in result.dense.hits} == current and len(current) == 238
    assert set(result.records) == union
    assert {r.category for r in result.records.values()} == set(Category)
    assert {NULL_RAM_LAPTOP, NULL_STORAGE_TYPE_LAPTOP} <= set(result.records)
    assert result.records[NULL_RAM_LAPTOP].spec.ram_gb is None
    assert result.records[NULL_STORAGE_TYPE_LAPTOP].spec.storage_type is None
    for pid, record in result.records.items():
        assert record == expected_record(pid), pid


def test_stale_embedding_product_still_gets_its_record_from_lexical(engine, world):
    # SYN-LAP-0001 has a stale embedding (no dense hit) but matches lexically.
    result = read(engine, "hp aurelia laptop", FilterSpec(category=Category.LAPTOP))
    assert STALE_EMBEDDING in {h.product_id for h in result.lexical.hits}
    assert STALE_EMBEDDING not in {h.product_id for h in result.dense.hits}
    assert result.records[STALE_EMBEDDING] == expected_record(STALE_EMBEDDING)


# ------------------------------------------------------------------------------ failures


def test_a_record_statement_error_rolls_back_and_releases(engine, world, monkeypatch):
    broken = text(
        "SELECT product_id FROM no_such_table WHERE product_id = ANY(CAST(:product_ids AS text[]))"
    )
    monkeypatch.setattr(reranked_search, "CANDIDATE_PRODUCTS_SQL", broken)
    log, _sent = record_log(engine)
    checkouts: list = []
    event.listen(engine.pool, "checkout", lambda *a: checkouts.append("out"))
    event.listen(engine.pool, "checkin", lambda *a: checkouts.append("in"))
    with Session(engine) as session:
        with pytest.raises(ProgrammingError) as info:
            read_reranked_sources(
                session,
                "hp laptop",
                FakeEmbedder().embed_query("hp laptop"),
                SPEC,
                FilterSpec(),
                LEXICAL_K,
                DENSE_K,
            )
        assert not session.in_transaction()
        assert engine.pool.checkedout() == 0
    assert getattr(info.value.orig, "sqlstate", None) == "42P01"
    assert log[0] == "BEGIN" and log[-1] == "ROLLBACK" and "COMMIT" not in log
    assert log.count("BEGIN") == 1
    assert checkouts == ["out", "in"]


def test_an_open_transaction_is_refused_before_any_statement(engine, world):
    log, _sent = record_log(engine)
    with Session(engine) as session:
        session.execute(text("SELECT 1"))  # autobegin
        with pytest.raises(RuntimeError, match="without an open transaction"):
            read_reranked_sources(
                session, "hp", [1.0] + [0.0] * 383, SPEC, FilterSpec(), LEXICAL_K, DENSE_K
            )
    assert [e for e in log if e not in ("BEGIN", "ROLLBACK")] == ["SELECT 1"]


def _constraint(conn, table: str, suffix: str) -> tuple[str, str]:
    """The one CHECK constraint of `table` whose name ends with `suffix`, and its definition."""
    name, definition = conn.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c' "
            "AND conname LIKE :c"
        ),
        {"t": table, "c": f"%{suffix}"},
    ).one()
    return name, definition


# label -> the product damaged. The price and attribute cases first lift the CHECK constraint
# that would refuse the bad value (restored afterwards); spec-row duplication or a spec row of the
# wrong category cannot be stored at all (primary key; composite FK + CHECK), so those cases are
# covered with fake rows in the unit tests only.
DAMAGE = {
    "missing spec row": "SYN-LAP-0002",
    "invalid stored price": "SYN-PHN-0002",
    "invalid stored attribute": "SYN-LAP-0004",
}


FULL = 1000  # dense depth above the catalog size: every current product is in the union


@pytest.mark.parametrize("label", DAMAGE, ids=DAMAGE)
def test_damaged_records_fail_deterministically_after_commit(engine, world, label):
    pid = DAMAGE[label]
    query = "laptop phone"
    vector = FakeEmbedder().embed_query(query)
    with Session(engine) as session:
        lexical, dense = read_filtered_sources(
            session, query, vector, SPEC, FilterSpec(), LEXICAL_K, FULL
        )
    assert pid in {h.product_id for h in lexical.hits + dense.hits}  # really in the union
    with engine.begin() as conn:
        if label == "missing spec row":
            saved = (
                conn.execute(text("SELECT * FROM laptop_specs WHERE product_id = :p"), {"p": pid})
                .mappings()
                .one()
            )
            conn.execute(text("DELETE FROM laptop_specs WHERE product_id = :p"), {"p": pid})
        elif label == "invalid stored price":
            name, definition = _constraint(conn, "products", "price_positive")
            saved = conn.execute(
                text("SELECT price FROM products WHERE product_id = :p"), {"p": pid}
            ).scalar()
            conn.execute(text(f'ALTER TABLE products DROP CONSTRAINT "{name}"'))
            conn.execute(text("UPDATE products SET price = -1 WHERE product_id = :p"), {"p": pid})
        else:
            name, definition = _constraint(conn, "laptop_specs", "storage_type_valid")
            saved = conn.execute(
                text("SELECT storage_type FROM laptop_specs WHERE product_id = :p"), {"p": pid}
            ).scalar()
            conn.execute(text(f'ALTER TABLE laptop_specs DROP CONSTRAINT "{name}"'))
            conn.execute(
                text("UPDATE laptop_specs SET storage_type = 'FLOPPY' WHERE product_id = :p"),
                {"p": pid},
            )
    try:
        log, _sent = record_log(engine)
        for _attempt in range(2):  # deterministic: the same fixed failure every time
            with pytest.raises(RerankDocumentError) as info:
                read(engine, query, FilterSpec(), dense_k=FULL)
            assert str(info.value) == "reranker document unavailable: document_build_failed"
            assert info.value.__cause__ is None
        # The snapshot ended normally; the failure is raised after the commit.
        assert log.count("BEGIN") == log.count("COMMIT") == 2 and "ROLLBACK" not in log
        assert engine.pool.checkedout() == 0
        # V2 is unaffected by the damaged record.
        with Session(engine) as session:
            v2_lexical, v2_dense = read_filtered_sources(
                session, query, vector, SPEC, FilterSpec(), LEXICAL_K, FULL
            )
        assert [h.product_id for h in v2_lexical.hits] == [h.product_id for h in lexical.hits]
        assert [h.product_id for h in v2_dense.hits] == [h.product_id for h in dense.hits]
    finally:
        with engine.begin() as conn:
            if label == "missing spec row":
                columns = ", ".join(saved.keys())
                values = ", ".join(f":{key}" for key in saved)
                conn.execute(
                    text(f"INSERT INTO laptop_specs ({columns}) VALUES ({values})"),  # noqa: S608
                    dict(saved),
                )
            elif label == "invalid stored price":
                conn.execute(
                    text("UPDATE products SET price = :v WHERE product_id = :p"),
                    {"v": saved, "p": pid},
                )
                conn.execute(text(f'ALTER TABLE products ADD CONSTRAINT "{name}" {definition}'))
            else:
                conn.execute(
                    text("UPDATE laptop_specs SET storage_type = :v WHERE product_id = :p"),
                    {"v": saved, "p": pid},
                )
                conn.execute(text(f'ALTER TABLE laptop_specs ADD CONSTRAINT "{name}" {definition}'))
    assert catalog_digest(engine) == world["digest"]  # the module catalog is restored
    assert read(engine, query, FilterSpec(), dense_k=FULL).records[pid] == expected_record(pid)
