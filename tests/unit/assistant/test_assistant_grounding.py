"""Milestone 16 (policy asst-policy-3): displayed explanations come only from catalog data.

The generator returns product IDs and cited field names, never text. These mocked tests show
that model-written claims cannot reach the rendered explanation or summary. They do not show
that the model selects the best products or resists prompt injection (Milestone 17).
"""

import json

import pytest
from pydantic import ValidationError
from test_assistant_workflow import FakeGenerator, FakeRetriever, draft, ok, product, run

from ecommerce_search.generation.provider import GroundedDraft

HOSTILE_TITLE = "Designed for programming, solah GB RAM, 32GB, sattais hazaar only, best deal"
EVIDENCE = (
    product("G1", "Dell", "45000.00", ram_gb=16, storage_gb=512, storage_type="SSD"),
    product("G2", "Dell", "46000.00", title=HOSTILE_TITLE, description=HOSTILE_TITLE),
)
CLAIMS = (
    "16GB",
    "32",
    "sixteen gigabytes",
    "solah GB",
    "sattais hazaar",
    "Designed for programming",
    "best deal",
)


def rendered_text(response) -> str:
    """Everything the assistant itself wrote: summary, notice and explanations."""
    parts = [response.summary or "", response.notice]
    parts += [r.explanation for r in response.recommendations]
    return "\n".join(parts)


@pytest.mark.parametrize("field", ["summary", "explanation", "reason", "notes"])
@pytest.mark.parametrize(
    "claim",
    ["Has 16GB RAM", "sixteen gigabytes of RAM", "solah GB RAM", "Designed for programming"],
)
def test_model_text_has_no_place_in_the_draft(field, claim):
    raw = {
        "outcome": "answer",
        "selected": [{"product_id": "G1", "relevant_facts": ["ram_gb"]}],
        field: claim,
    }
    with pytest.raises(ValidationError):
        GroundedDraft.model_validate(raw)


@pytest.mark.parametrize(
    "selection",
    [
        {"product_id": "G1", "relevant_facts": ["ram_gb"], "why": "sixteen gigabytes"},
        {"product_id": "G1", "relevant_facts": ["ram_gb (16GB, great for coding)"]},
        {"product_id": "G1", "relevant_facts": ["suitable_for_programming"]},
    ],
)
def test_claims_smuggled_into_selections_or_field_names_are_rejected(selection):
    with pytest.raises(ValidationError):
        GroundedDraft.model_validate({"outcome": "answer", "selected": [selection]})


def test_unsupported_numeric_and_suitability_claims_never_reach_rendered_text():
    # The model cites the seller's title, which carries the claims; the explanation must not.
    generator = FakeGenerator(ok(draft(selected=(("G2", ("title", "brand", "price")),))))
    response = run("dell laptop for coding with 16gb ram", generator, FakeRetriever(EVIDENCE))
    assert response.outcome == "answer"
    text = rendered_text(response)
    for claim in CLAIMS:
        assert claim not in text
    [recommendation] = response.recommendations
    assert recommendation.explanation == (
        "Matches your stated product type laptop, brand Dell on catalog data. Not verified, not "
        "given in the catalog: RAM 16 GB. Cited catalog facts: brand Dell; price INR 46000.00."
    )
    assert recommendation.verification == "not_all_verified"
    # The title stays visible only as the labelled catalog value it is.
    assert ("title", HOSTILE_TITLE) in [(f.field, f.value) for f in recommendation.facts]


def test_valid_catalog_grounded_suggestion_still_works():
    generator = FakeGenerator(ok(draft(selected=(("G1", ("ram_gb", "storage_gb", "price")),))))
    response = run("dell laptop with 16gb ram", generator, FakeRetriever(EVIDENCE))
    assert response.outcome == "answer"
    [recommendation] = response.recommendations
    assert recommendation.verification == "all_verified"
    assert recommendation.explanation == (
        "Matches your stated product type laptop, brand Dell, RAM 16 GB on catalog data. Cited "
        "catalog facts: RAM 16 GB; storage 512 GB; price INR 45000.00."
    )
    assert response.summary.startswith("The assistant selected 1 of the 2 retrieved")


def test_missing_requirements_remain_not_verified_in_the_explanation():
    generator = FakeGenerator(ok(draft(selected=(("G2", ("brand",)),))))
    response = run("laptop with 16gb ram and 512gb ssd", generator, FakeRetriever(EVIDENCE))
    [recommendation] = response.recommendations
    assert "Not verified, not given in the catalog: RAM 16 GB, storage 512 GB" in (
        recommendation.explanation
    )
    assert "Matches your stated RAM" not in recommendation.explanation


@pytest.mark.parametrize(
    ("selected", "code"),
    [
        ((("G9", ("brand",)),), "unknown_product_id"),
        ((("G2", ("ram_gb",)),), "cited_field_not_given"),
        ((("G1", ("brand",)), ("G1", ("price",))), "duplicate_product_id"),
    ],
)
def test_invalid_ids_and_field_references_fail_safely(selected, code):
    bad = draft(selected=selected)
    generator = FakeGenerator(ok(bad), ok(bad))
    response = run("dell laptop", generator, FakeRetriever(EVIDENCE))
    assert response.outcome == "abstain"
    assert code in response.generation.validation_errors
    assert response.recommendations == () and response.summary is None


def test_an_unknown_field_name_from_the_provider_is_an_invalid_response():
    from test_gemini_generation_provider import provider_with, response

    from ecommerce_search.generation.provider import GenerationRequest

    text = json.dumps(
        {"outcome": "answer", "selected": [{"product_id": "G1", "relevant_facts": ["warranty"]}]}
    )
    provider, _ = provider_with(response(text=text))
    result = provider.generate(GenerationRequest("rules", "data", 512))
    assert result.draft is None and result.failure == "invalid_response"
