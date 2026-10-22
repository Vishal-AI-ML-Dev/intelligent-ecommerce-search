"""Deterministic checks of a generated draft (Milestone 16).

What passing means, and what it does not: every selected ID is a retrieved record, cited fields
exist in the catalog contract, explicit constraints hold on the catalog values (category, brand,
price bounds, and RAM/storage where the catalog gives them; otherwise `not_verified`), and the
summary carries no digits, price, offer, availability, link or markup wording. Passing does NOT
show that the summary's remaining words are supported by the records; faithfulness is evaluated
by humans in Milestone 17.
"""

import re
from decimal import Decimal

from ecommerce_search.assistant.models import (
    CitedFact,
    ConstraintCheck,
    EvidenceProduct,
    Recommendation,
)
from ecommerce_search.generation.provider import GroundedDraft
from ecommerce_search.query_understanding.models import QueryUnderstanding

PRICE_OR_OFFER_TERMS = re.compile(
    r"[₹$€£¥]|\b(?:rs|inr|usd|eur|rupees?|dollars?|price[sd]?|pricing|cost[s]?|costly|cheap"
    r"|cheaper|cheapest|expensive|afford\w*|budget|discount\w*|offers?|deals?|sale|coupons?"
    r"|free|emi)\b",
    re.IGNORECASE,
)
AVAILABILITY_TERMS = re.compile(
    r"\b(?:in[- ]stock|out[- ]of[- ]stock|stock|available|availability|unavailable|delivery"
    r"|deliver\w*|ship\w*|dispatch\w*|discontinued)\b",
    re.IGNORECASE,
)
LINK_OR_MARKUP = re.compile(r"[<>\[\]`]|https?:|www\.", re.IGNORECASE)


def _fact_value(product: EvidenceProduct, field: str) -> str | None:
    value = getattr(product, field)
    if value is None:
        return None
    return str(value)


def constraint_checks(
    product: EvidenceProduct, understanding: QueryUnderstanding
) -> tuple[ConstraintCheck, ...]:
    """Explicit constraints re-checked against the catalog record's typed fields."""
    checks: list[ConstraintCheck] = []
    if understanding.category is not None:
        ok = product.category == understanding.category.value
        checks.append(_check("category", understanding.category.value, ok))
    if understanding.brand is not None:
        ok = product.brand.casefold() == understanding.brand.casefold()
        checks.append(_check("brand", understanding.brand, ok))
    price = Decimal(product.price)
    if understanding.min_price is not None:
        checks.append(
            _check("min_price", str(understanding.min_price), price >= understanding.min_price)
        )
    if understanding.max_price is not None:
        checks.append(
            _check("max_price", str(understanding.max_price), price <= understanding.max_price)
        )
    # Exact equality, as the search filter policy (fp-1) applies these fields. A field the catalog
    # record does not give stays `not_verified`: never passed, never inferred from text.
    for field, requested in (
        ("ram_gb", understanding.ram_gb),
        ("storage_gb", understanding.storage_gb),
        ("storage_type", understanding.storage_type),
        ("storage_interface", understanding.attributes.storage_interface),
    ):
        if requested is None:
            continue
        wanted = str(requested)
        actual = getattr(product, field)
        if actual is None:
            checks.append(ConstraintCheck(field=field, requested=wanted, status="not_verified"))
        else:
            checks.append(_check(field, wanted, str(actual) == wanted))
    return tuple(checks)


def _verification(checks: tuple[ConstraintCheck, ...]) -> str:
    if not checks:
        return "no_requirements"
    if all(check.status == "passed" for check in checks):
        return "all_verified"
    return "not_all_verified"


def _check(field: str, requested: str, ok: bool) -> ConstraintCheck:
    return ConstraintCheck(field=field, requested=requested, status="passed" if ok else "failed")


def summary_errors(summary: str) -> list[str]:
    errors = []
    if any(char.isdigit() for char in summary):
        errors.append("summary_contains_digit")
    if PRICE_OR_OFFER_TERMS.search(summary):
        errors.append("summary_contains_price_or_offer_term")
    if AVAILABILITY_TERMS.search(summary):
        errors.append("summary_contains_availability_term")
    if LINK_OR_MARKUP.search(summary):
        errors.append("summary_contains_link_or_markup")
    return errors


def validate_draft(
    draft: GroundedDraft,
    evidence: tuple[EvidenceProduct, ...],
    understanding: QueryUnderstanding,
) -> tuple[tuple[str, ...], tuple[Recommendation, ...]]:
    """Fixed error codes (empty = passed) and, when passed, the recommendations rendered from
    catalog records."""
    by_id = {product.product_id: product for product in evidence}
    errors: list[str] = []
    ids = [selection.product_id for selection in draft.selected]
    if draft.outcome == "answer" and not ids:
        errors.append("answer_without_products")
    if draft.outcome == "abstain" and ids:
        errors.append("abstain_with_products")
    if draft.outcome == "answer" and not draft.summary.strip():
        errors.append("summary_empty")
    if len(set(ids)) != len(ids):
        errors.append("duplicate_product_id")
    if any(product_id not in by_id for product_id in ids):
        errors.append("unknown_product_id")
    if any(len(set(s.relevant_facts)) != len(s.relevant_facts) for s in draft.selected):
        errors.append("duplicate_fact")
    errors.extend(summary_errors(draft.summary))
    recommendations: list[Recommendation] = []
    if not errors:
        for selection in draft.selected:
            product = by_id[selection.product_id]
            checks = constraint_checks(product, understanding)
            if any(check.status == "failed" for check in checks):
                errors.append("constraint_violated")
                break
            recommendations.append(
                Recommendation(
                    product_id=product.product_id,
                    facts=tuple(
                        CitedFact(field=field, value=_fact_value(product, field))
                        for field in selection.relevant_facts
                    ),
                    constraint_checks=checks,
                    verification=_verification(checks),
                )
            )
    if errors:
        return tuple(dict.fromkeys(errors)), ()
    return (), tuple(recommendations)
