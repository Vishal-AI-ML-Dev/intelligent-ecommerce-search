"""A read-only index of the committed seed catalog, for offline cross-checks.

It reuses the Milestone 2 loader (checksum, validation, normalization, content hash) and refuses
a catalog that does not match its provenance file or that has any invalid record. No database.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.ingestion.loader import (
    LoaderError,
    load_catalog,
    load_provenance,
    verify_against_provenance,
)
from evals.contracts.canonical import ContractError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
DEFAULT_PROVENANCE = ROOT / "data" / "seed" / "catalog_seed_v1.provenance.json"

_TOKEN_RE = re.compile(r"[0-9a-z]+")


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(title.casefold()))


@dataclass(frozen=True)
class CatalogProduct:
    product_id: str
    category: Category
    brand: str
    content_sha256: str
    title: str
    spec: Mapping[str, Any]
    price: Decimal
    subcategory: str | None


@dataclass(frozen=True)
class CatalogIndex:
    dataset_id: str
    dataset_version: str
    checksum_sha256: str
    products: Mapping[str, CatalogProduct]

    def processors(self) -> frozenset[str]:
        return frozenset(
            str(p.spec["processor"]) for p in self.products.values() if p.spec.get("processor")
        )

    def has_title_token(self, value: str) -> bool:
        wanted = value.casefold()
        return any(wanted in title_tokens(p.title) for p in self.products.values())


def load_catalog_index(
    catalog_path: Path = DEFAULT_CATALOG, provenance_path: Path = DEFAULT_PROVENANCE
) -> CatalogIndex:
    try:
        provenance = load_provenance(provenance_path)
        catalog = load_catalog(catalog_path)
        verify_against_provenance(catalog, provenance)
    except LoaderError as exc:
        raise ContractError(f"cannot load the catalog: {exc}") from None
    if len(catalog.lines) != catalog.total_lines:
        raise ContractError("the catalog contains invalid records")
    products: dict[str, CatalogProduct] = {}
    for line in catalog.lines:
        record = line.record
        if record.product_id in products:
            raise ContractError(f"duplicate product_id {record.product_id!r} in the catalog")
        products[record.product_id] = CatalogProduct(
            product_id=record.product_id,
            category=record.category,
            brand=record.brand,
            content_sha256=line.content_sha256,
            title=record.title,
            spec=MappingProxyType(record.spec.model_dump(mode="json")),
            price=record.price,
            subcategory=record.subcategory,
        )
    return CatalogIndex(
        dataset_id=provenance.dataset_id,
        dataset_version=provenance.dataset_version,
        checksum_sha256=catalog.checksum_sha256,
        products=MappingProxyType(products),
    )
