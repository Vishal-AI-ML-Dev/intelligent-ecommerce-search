"""Generation providers (Milestone 16, ADR-009): the typed interface and its output contract.

The optional Gemini adapter lives in `generation/gemini_provider.py` and is imported only when
`ASSISTANT_GENERATION_PROVIDER=gemini`. Nothing in the search engine depends on this
package.
"""

from ecommerce_search.generation.provider import (
    FACT_FIELDS,
    FactField,
    GenerationFailure,
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
    GroundedDraft,
    SelectedProduct,
)

__all__ = [
    "FACT_FIELDS",
    "FactField",
    "GenerationFailure",
    "GenerationProvider",
    "GenerationRequest",
    "GenerationResult",
    "GroundedDraft",
    "SelectedProduct",
]
