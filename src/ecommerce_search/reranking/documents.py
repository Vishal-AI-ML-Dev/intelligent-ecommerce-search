"""The reranker document `rd-1` (pure: no database, no network, no model).

`rd-1` is, by definition, the reviewed embedding text version 1 of a product
(`ecommerce_search.embeddings.text.build_embedding_text`), unchanged: title; brand and category;
category-specific specifications; description. Field order, cleaning and the handling of missing
values and negative booleans are exactly those of that contract, and the same closed field
whitelist applies, so the document never contains raw seller lines, provenance, prices, ratings,
seller ids or evaluation labels.

The reranker document is therefore coupled to the embedding text: if `EMBEDDING_TEXT_VERSION`
changes, building an `rd-1` document refuses to run until a new reranker document version is
defined and reviewed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ecommerce_search.catalog.schemas import CatalogRecord
from ecommerce_search.embeddings.text import EMBEDDING_TEXT_VERSION, build_embedding_text

RERANK_DOCUMENT_VERSION = "rd-1"
# (contract, version) that `rd-1` reuses verbatim.
RERANK_DOCUMENT_SOURCE: tuple[str, str] = ("embedding_text", "1")


class RerankDocumentError(Exception):
    """A reranker document could not be built. The message is fixed and carries no detail."""

    reason = "document_build_failed"

    def __init__(self) -> None:
        super().__init__("reranker document unavailable: document_build_failed")


@dataclass(frozen=True)
class RerankDocument:
    product_id: str
    text: str


def _require_source_contract() -> None:
    if RERANK_DOCUMENT_SOURCE[1] != EMBEDDING_TEXT_VERSION:
        raise RuntimeError(
            "the embedding text version changed: define and review a new reranker document version"
        )


def build_rerank_document(record: CatalogRecord) -> str:
    """The `rd-1` text of one validated record (byte-identical to embedding text v1)."""
    _require_source_contract()
    if not isinstance(record, CatalogRecord):
        raise TypeError("record must be a CatalogRecord")
    return build_embedding_text(record)


def build_rerank_documents(
    product_ids: Sequence[str], records: Mapping[str, CatalogRecord]
) -> list[RerankDocument]:
    """`rd-1` documents for `product_ids`, in the given order.

    `product_ids` must be unique, non-empty strings (a duplicate or malformed id is a
    programming error: `ValueError`/`TypeError`). `records` maps product id to its validated
    record and may hold more products than requested. A requested product without a record, a
    record of another product, a record the text builder cannot read, or an empty text raises
    `RerankDocumentError`. Records are only read, never modified."""
    _require_source_contract()
    if isinstance(product_ids, str | bytes) or not isinstance(product_ids, Sequence):
        raise TypeError("product_ids must be a sequence of product ids")
    if not isinstance(records, Mapping):
        raise TypeError("records must be a mapping of product id to CatalogRecord")
    for product_id in product_ids:
        if not isinstance(product_id, str) or not product_id:
            raise TypeError("product ids must be non-empty strings")
    if len(set(product_ids)) != len(product_ids):
        raise ValueError("product ids must be unique")
    documents = []
    for product_id in product_ids:
        record = records.get(product_id)
        if not isinstance(record, CatalogRecord) or record.product_id != product_id:
            raise RerankDocumentError
        try:
            text = build_embedding_text(record)
        except (AttributeError, LookupError, TypeError, ValueError):
            # An unvalidated or malformed record; its content never reaches the message.
            raise RerankDocumentError from None
        if not isinstance(text, str) or not text.strip():
            raise RerankDocumentError
        documents.append(RerankDocument(product_id=product_id, text=text))
    return documents
