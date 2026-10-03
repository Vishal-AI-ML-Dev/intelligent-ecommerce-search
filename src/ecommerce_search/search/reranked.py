"""V3 reranked source retrieval (Milestone 8): the V2 filtered reads plus the candidates' records.

`read_reranked_sources` runs, in one REPEATABLE READ READ ONLY transaction, exactly the reads of
`read_filtered_sources` (the same `SNAPSHOT_SQL` first, then the unchanged filtered lexical and
dense functions with the same query, vector, `FilterSpec` and depths) and then, only when the
union of both source lists is non-empty, the catalog rows of that union: `products` plus the
category-specific spec rows of each product's own category. The transaction commits (nothing is
written) or rolls back, and the session releases its connection, before this returns. Fusion,
reranker documents and the model all run later, outside the transaction.

Rows are rebuilt into validated `CatalogRecord`s through the reviewed catalog path
(`flat_from_rows` + `parse_raw`), keyed by product id. Every requested product must have exactly
one product row and exactly one spec row of its category, the rebuilt record must validate and
carry the requested id; otherwise the whole read fails with the fixed `RerankDocumentError`
(`document_build_failed`), which never carries SQL, data, paths or exception text. Nothing is
filtered, relaxed or retried here.

The statements are constant text: product ids are bound as one array parameter, and no value is
ever part of the statement.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import TextClause, text
from sqlalchemy.orm import Session

from ecommerce_search.catalog.mapping import flat_from_rows
from ecommerce_search.catalog.schemas import CatalogRecord, parse_raw
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.embeddings.spec import EmbeddingModelSpec
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.reranking.documents import RerankDocumentError
from ecommerce_search.search import filtered
from ecommerce_search.search.dense import DenseResult
from ecommerce_search.search.hybrid import SNAPSHOT_SQL
from ecommerce_search.search.lexical import LexicalResult

# Constant statements. Their column lists are the catalog core fields and each category's
# attribute columns (`CORE_KEYS`, `spec_columns`); a unit test keeps them equal.
CANDIDATE_PRODUCTS_SQL = text(
    """
    SELECT p.product_id, p.seller_id, p.title, p.description, p.category, p.subcategory,
           p.brand, p.price, p.currency, p.rating, p.review_count, p.availability,
           p.source_type, p.is_synthetic
    FROM products p
    WHERE p.product_id = ANY(CAST(:product_ids AS text[]))
    ORDER BY p.product_id
    """
)
LAPTOP_SPECS_SQL = text(
    """
    SELECT s.product_id, s.category, s.ram_gb, s.storage_gb, s.storage_type,
           s.storage_interface, s.processor, s.gpu, s.screen_size_inches, s.operating_system,
           s.weight_kg
    FROM laptop_specs s
    WHERE s.product_id = ANY(CAST(:product_ids AS text[]))
    ORDER BY s.product_id
    """
)
PHONE_SPECS_SQL = text(
    """
    SELECT s.product_id, s.category, s.ram_gb, s.storage_gb, s.camera, s.battery_mah,
           s.screen_size_inches, s.operating_system
    FROM phone_specs s
    WHERE s.product_id = ANY(CAST(:product_ids AS text[]))
    ORDER BY s.product_id
    """
)
SHOE_SPECS_SQL = text(
    """
    SELECT s.product_id, s.category, s.size, s.size_system, s.color, s.material, s.gender
    FROM shoe_specs s
    WHERE s.product_id = ANY(CAST(:product_ids AS text[]))
    ORDER BY s.product_id
    """
)
HEADPHONE_SPECS_SQL = text(
    """
    SELECT s.product_id, s.category, s.wireless, s.anc, s.battery_life_hours, s.connectivity
    FROM headphone_specs s
    WHERE s.product_id = ANY(CAST(:product_ids AS text[]))
    ORDER BY s.product_id
    """
)
# The spec statement of each category, in taxonomy order.
CANDIDATE_SPEC_SQL: Mapping[Category, TextClause] = MappingProxyType(
    {
        Category.LAPTOP: LAPTOP_SPECS_SQL,
        Category.PHONE: PHONE_SPECS_SQL,
        Category.SHOES: SHOE_SPECS_SQL,
        Category.HEADPHONES: HEADPHONE_SPECS_SQL,
    }
)


@dataclass(frozen=True)
class RerankedSources:
    lexical: LexicalResult
    dense: DenseResult
    # Validated records of every product in either source list, keyed by product id.
    records: Mapping[str, CatalogRecord]
    # Time to read and rebuild the records; None when no record read ran (empty union).
    document_read_ms: float | None


def _source_union(lexical: LexicalResult, dense: DenseResult) -> list[str]:
    return sorted({hit.product_id for hit in lexical.hits} | {hit.product_id for hit in dense.hits})


def _read_candidate_rows(
    session: Session, product_ids: list[str]
) -> tuple[list, dict[Category, list]]:
    """Product rows of `product_ids`, then the spec rows of each category present among them."""
    products = list(session.execute(CANDIDATE_PRODUCTS_SQL, {"product_ids": product_ids}).all())
    categories: dict[str, list[str]] = {}
    for row in products:
        categories.setdefault(row.category, []).append(row.product_id)
    specs: dict[Category, list] = {}
    for category in Category:
        ids = categories.get(category.value)
        if ids:
            statement = CANDIDATE_SPEC_SQL[category]
            specs[category] = list(session.execute(statement, {"product_ids": ids}).all())
    return products, specs


def build_candidate_records(
    product_ids: Sequence[str], products: Sequence, specs: Mapping[Category, Sequence]
) -> dict[str, CatalogRecord]:
    """Validated records for exactly `product_ids` from the rows read in the snapshot (pure).

    A missing, duplicate, unexpected or mismatched row, or a row set that no longer validates,
    raises `RerankDocumentError`."""
    wanted = set(product_ids)
    by_id: dict[str, object] = {}
    for row in products:
        product_id = getattr(row, "product_id", None)
        if product_id not in wanted or product_id in by_id:
            raise RerankDocumentError
        by_id[product_id] = row
    if by_id.keys() != wanted:
        raise RerankDocumentError
    spec_by_id: dict[str, object] = {}
    for category, rows in specs.items():
        for row in rows:
            product_id = getattr(row, "product_id", None)
            product = by_id.get(product_id)
            if (
                product is None
                or product_id in spec_by_id
                or getattr(row, "category", None) != category.value
                or getattr(product, "category", None) != category.value
            ):
                raise RerankDocumentError
            spec_by_id[product_id] = row
    if spec_by_id.keys() != wanted:
        raise RerankDocumentError
    records: dict[str, CatalogRecord] = {}
    for product_id in sorted(wanted):
        try:
            record = parse_raw(flat_from_rows(by_id[product_id], spec_by_id[product_id]))
        except (ValueError, LookupError, AttributeError, TypeError):
            # RecordValidationError is a ValueError; its text (stored data) never propagates.
            raise RerankDocumentError from None
        if record.product_id != product_id:
            raise RerankDocumentError
        records[product_id] = record
    return records


def read_reranked_sources(
    session: Session,
    query: str,
    query_vector: Sequence[float],
    spec: EmbeddingModelSpec,
    filters: FilterSpec,
    lexical_k: int,
    dense_k: int,
) -> RerankedSources:
    """The V2 filtered source reads plus the candidates' records, in one read-only snapshot.

    Database errors propagate after rollback and release; record problems raise
    `RerankDocumentError` after the transaction ended."""
    if session.in_transaction():
        raise RuntimeError("read_reranked_sources needs a session without an open transaction")
    filtered.active_fields(filters)  # reject a non-FilterSpec before any database work
    read_ms = 0.0
    product_ids: list[str] = []
    products: list = []
    specs: dict[Category, list] = {}
    with session.begin():
        session.execute(SNAPSHOT_SQL)  # must be the first statement of the transaction
        lexical_result = filtered.filtered_lexical_search(session, query, filters, lexical_k)
        dense_result = filtered.filtered_dense_search(session, query_vector, spec, filters, dense_k)
        product_ids = _source_union(lexical_result, dense_result)
        if product_ids:
            read_started = time.perf_counter()
            products, specs = _read_candidate_rows(session, product_ids)
            read_ms = time.perf_counter() - read_started
    if not product_ids:
        return RerankedSources(lexical_result, dense_result, MappingProxyType({}), None)
    build_started = time.perf_counter()
    records = build_candidate_records(product_ids, products, specs)
    read_ms += time.perf_counter() - build_started
    return RerankedSources(
        lexical_result, dense_result, MappingProxyType(records), round(read_ms * 1000, 3)
    )
