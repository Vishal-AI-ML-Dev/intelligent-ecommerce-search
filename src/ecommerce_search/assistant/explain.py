"""Deterministic explanations of a grounded selection (Milestone 16, policy asst-policy-3).

Every displayed sentence is built from fixed templates and two sources only: the results of the
explicit-requirement checks (requested values come from the deterministic query parser) and the
structured catalog values of the fields the generator cited. Free-text seller fields (title,
subcategory, description) are never placed in an explanation, and no model text exists to place.

This controls which claims are displayed. It does not show that the generator picked the best
products, or that it resists prompt injection; Milestone 17 evaluates that.
"""

from collections.abc import Callable

from ecommerce_search.assistant.models import ConstraintCheck, EvidenceProduct

# Structured fields only; each renders a catalog value with a fixed label.
FACT_TEXT: dict[str, Callable[[EvidenceProduct], str]] = {
    "brand": lambda p: f"brand {p.brand}",
    "category": lambda p: f"category {p.category}",
    "price": lambda p: f"price {p.currency} {p.price}",
    "rating": lambda p: f"rating {p.rating} out of 5",
    "review_count": lambda p: f"{p.review_count} reviews",
    "availability": lambda p: p.availability.replace("_", " "),
    "ram_gb": lambda p: f"RAM {p.ram_gb} GB",
    "storage_gb": lambda p: f"storage {p.storage_gb} GB",
    "storage_type": lambda p: f"storage type {p.storage_type}",
    "storage_interface": lambda p: f"storage interface {p.storage_interface}",
}
# Cited, but never rendered into an explanation: seller-written free text.
FREE_TEXT_FIELDS = frozenset({"title", "subcategory"})

REQUIREMENT_TEXT: dict[str, Callable[[str, EvidenceProduct], str]] = {
    "category": lambda v, p: f"product type {v}",
    "brand": lambda v, p: f"brand {v}",
    "min_price": lambda v, p: f"minimum price {p.currency} {v}",
    "max_price": lambda v, p: f"maximum price {p.currency} {v}",
    "ram_gb": lambda v, p: f"RAM {v} GB",
    "storage_gb": lambda v, p: f"storage {v} GB",
    "storage_type": lambda v, p: f"storage type {v}",
    "storage_interface": lambda v, p: f"storage interface {v}",
}


def render_explanation(
    product: EvidenceProduct, cited: tuple[str, ...], checks: tuple[ConstraintCheck, ...]
) -> str:
    def requirements(status: str) -> list[str]:
        return [
            REQUIREMENT_TEXT[c.field](c.requested, product) for c in checks if c.status == status
        ]

    sentences = []
    if passed := requirements("passed"):
        sentences.append(f"Matches your stated {', '.join(passed)} on catalog data.")
    if unverified := requirements("not_verified"):
        sentences.append(f"Not verified, not given in the catalog: {', '.join(unverified)}.")
    if not checks:
        sentences.append("You stated no requirement that the catalog can check.")
    facts = [FACT_TEXT[field](product) for field in cited if field in FACT_TEXT]
    if facts:
        sentences.append(f"Cited catalog facts: {'; '.join(facts)}.")
    return " ".join(sentences)


def render_summary(selected: int, retrieved: int) -> str:
    noun = "product" if retrieved == 1 else "products"
    return (
        f"The assistant selected {selected} of the {retrieved} retrieved catalog {noun}. Each "
        "explanation below uses catalog data only; the selection itself is not a verified "
        "best choice."
    )
