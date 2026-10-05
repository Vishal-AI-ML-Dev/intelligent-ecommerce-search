"""The annotation-facing product card (`card-1`) and its hash.

A card is the exact projection of a catalog product that an annotator may see (D4, D8):

* product id, title, description, category, subcategory, brand, price and currency;
* the category attributes (the normalized category spec), which relevance depends on.

Seller, availability, rating and review count are never part of a card, and neither is any
source, rank, score, inclusion reason, parser output or filter. `card_sha256` is the SHA-256 of
the canonical JSON of the card, and every pool item binds it, so annotation refuses a product
whose shown content changed after the pool was frozen.
"""

import json
from collections.abc import Mapping
from typing import Any

from evals.contracts.canonical import ContractError, sha256_hex
from evals.contracts.catalog import CatalogProduct
from evals.contracts.constants import CARD_VERSION

# Catalog fields that are never shown during relevance annotation (D8).
HIDDEN_FIELDS: frozenset[str] = frozenset({"seller_id", "availability", "rating", "review_count"})


def card(product: CatalogProduct) -> dict[str, Any]:
    """The `card-1` projection of one catalog product (JSON-ready, no floats)."""
    spec = dict(product.spec)
    for key, value in spec.items():
        if isinstance(value, float):  # pragma: no cover - the catalog spec has no floats
            raise ContractError(f"{product.product_id}: spec field {key} is a float")
    return {
        "attributes": spec,
        "brand": product.brand,
        "card_version": CARD_VERSION,
        "category": product.category.value,
        "currency": product.currency,
        "description": product.description,
        "price": f"{product.price:.2f}",
        "product_id": product.product_id,
        "subcategory": product.subcategory,
        "title": product.title,
    }


def card_bytes(product: CatalogProduct) -> bytes:
    text = json.dumps(card(product), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def card_sha256(product: CatalogProduct) -> str:
    return sha256_hex(card_bytes(product))


def render_card(product: CatalogProduct) -> str:
    """A plain-text rendering of the card for the annotation CLI. Missing values are shown as
    "not stated"; nothing outside the card projection is rendered."""
    data = card(product)
    lines = [
        f"product_id : {data['product_id']}",
        f"title      : {data['title']}",
        f"brand      : {data['brand']}",
        f"category   : {data['category']}",
        f"subcategory: {data['subcategory'] or 'not stated'}",
        f"price      : {data['price']} {data['currency']}",
        f"description: {data['description'] or 'not stated'}",
        "attributes :",
    ]
    attributes: Mapping[str, Any] = data["attributes"]
    for key in sorted(attributes):
        lines.append(f"  {key}: {_shown(attributes[key])}")
    return "\n".join(lines)


def _shown(value: Any) -> str:
    if value is None:
        return "not stated"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
