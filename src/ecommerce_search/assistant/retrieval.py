"""The assistant's retriever interface (Milestone 16). The API layer adapts the existing V3
reranked search to it; the assistant never runs retrieval of its own."""

from dataclasses import dataclass
from typing import Protocol

from ecommerce_search.assistant.models import EvidenceProduct


class RetrievalUnavailable(Exception):
    """Search failed; the assistant answers with a fixed 503 and never guesses."""


@dataclass(frozen=True)
class Retrieval:
    search_version: str
    products: tuple[EvidenceProduct, ...]
    retrieval_ms: float


class CatalogRetriever(Protocol):
    def retrieve(self, query: str, top_k: int) -> Retrieval: ...
