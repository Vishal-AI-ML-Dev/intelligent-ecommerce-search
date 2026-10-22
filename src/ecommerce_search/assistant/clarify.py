"""Deterministic clarification (Milestone 16): decided from query understanding alone, before any
retrieval or generation. Questions are fixed templates; only canonical parsed values (never the
raw message) are inserted.
"""

from ecommerce_search.assistant.models import Clarification
from ecommerce_search.query_understanding.models import (
    AmbiguityReason,
    QueryUnderstanding,
    UnderstandingField,
)

FIELD_LABELS: dict[UnderstandingField, str] = {
    UnderstandingField.CATEGORY: "product type",
    UnderstandingField.BRAND: "brand",
    UnderstandingField.RAM_GB: "RAM",
    UnderstandingField.STORAGE_GB: "storage capacity",
    UnderstandingField.STORAGE_TYPE: "storage type",
    UnderstandingField.STORAGE_INTERFACE: "storage interface",
    UnderstandingField.MIN_PRICE: "minimum price",
    UnderstandingField.MAX_PRICE: "maximum price",
    UnderstandingField.SEMANTIC_INTENT: "use",
    UnderstandingField.PRICE_PREFERENCE: "price preference",
}

AMBIGUITY_QUESTIONS: dict[AmbiguityReason, str] = {
    AmbiguityReason.BARE_CAPACITY: (
        "You mentioned a capacity without saying what it is for. Is it RAM or storage?"
    ),
    AmbiguityReason.AMBIGUOUS_MEMORY_CAPACITY: (
        "You mentioned a memory size. Is it RAM or storage?"
    ),
    AmbiguityReason.CAPACITY_MULTIPLE_ROLES: (
        "One capacity in your request could mean RAM or storage. Which did you mean?"
    ),
    AmbiguityReason.PRICE_WITHOUT_BOUND: (
        "You mentioned a price. Is it the most you want to spend, or the least?"
    ),
    AmbiguityReason.UNSUPPORTED_GROUPED_NUMBER: (
        "I could not read one of the numbers. Please write it without separators, "
        "for example rs 50000."
    ),
    AmbiguityReason.UNSUPPORTED_RANGE: (
        "Ranges are not supported yet. Please give a single maximum or minimum, "
        "for example under rs 50000."
    ),
    AmbiguityReason.UNSUPPORTED_NUMERIC_COMPOUND: (
        "I could not read one of the values. Please give each requirement separately, "
        "for example 16gb ram."
    ),
    AmbiguityReason.OUT_OF_RANGE_VALUE: (
        "One of the values is outside the range the catalog supports. Please check it."
    ),
}

NO_PRODUCT_NEED_QUESTION = (
    "What are you looking for? Name a product type, brand or requirement, for example "
    "a laptop for coding under rs 60000."
)


def decide_clarification(understanding: QueryUnderstanding) -> Clarification | None:
    """A clarification when requirements conflict, are ambiguous, or nothing was understood."""
    if understanding.conflicts:
        conflict = understanding.conflicts[0]
        label = FIELD_LABELS.get(conflict.field, "requirement")
        values = sorted({term.value for term in conflict.candidates})
        listed = f" ({', '.join(values)})" if len(values) > 1 else ""
        return Clarification(
            reason="conflicting_requirements",
            question=f"Your request gives conflicting {label} requirements{listed}. "
            "Which one should I use?",
        )
    if understanding.ambiguities:
        return Clarification(
            reason="ambiguous_requirement",
            question=AMBIGUITY_QUESTIONS[understanding.ambiguities[0].reason],
        )
    if not understanding.matched_terms:
        return Clarification(reason="no_product_need", question=NO_PRODUCT_NEED_QUESTION)
    return None
