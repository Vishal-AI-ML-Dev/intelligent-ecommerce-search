"""M14-6 shared lexical matching `lt-1`: whole-token mentions, brand-only exclusion, and its use
by the `corrupt-2` generator, the pair checker and the disclosures (synthetic data only)."""

import copy

import pytest
from evals.listing_decisions import corrupt
from evals.listing_decisions.contracts import EvalSetError, load_set
from evals.listing_decisions.evaluate import evaluate
from evals.listing_decisions.matching import attribute_evidence, mentions
from evals.listing_decisions.prepare import raw_bytes
from listing_eval_support import ITEMS, SHOE_CASUAL, corruption, write_fixture

from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing


@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("Redmi Note 12", "Red", False),  # inside another word
        ("Red loafers", "Red", True),
        ("RED, suede loafers", "red", True),  # case and punctuation boundary
        ("navy-blue sneakers", "Navy Blue", True),  # multi-word value across a hyphen
        ("Navy/Blue sneakers", "Navy Blue", True),
        ("navy sneakers in blue", "Navy Blue", False),  # tokens present but not contiguous
        ("Off White canvas", "Off-White", True),
        ("Blackberry case", "Black", False),
        ("anything", "", False),
        ("anything", "---", False),  # a value with no token is never mentioned
    ],
)
def test_mentions_matches_whole_token_sequences_only(text, value, expected) -> None:
    assert mentions(text, value) is expected


@pytest.mark.parametrize(
    ("title", "description", "brand", "value", "expected"),
    [
        ("Redmi Note 12 Pro", "", "Xiaomi", "Red", False),  # Red inside Redmi: ineligible
        ("Red Tape Men's Loafers", "Comfortable loafers.", "Red Tape", "Red", False),  # brand only
        ("Red Tape Men's Red Loafers", "", "Red Tape", "Red", True),  # also an explicit mention
        ("Nike Loafers", "Brown leather loafers.", "Nike", "Brown", True),  # explicit colour
        ("Nike Loafers", "Brown leather loafers.", "Nike", "Leather", True),  # explicit material
        ("Nike Loafers", "Navy-blue mesh upper.", "Nike", "Navy Blue", True),  # multi-word
        ("Brown Loafers", "", None, "Brown", False),  # no brand: ambiguous, excluded
        ("Brown Loafers", "", "", "Brown", False),
        ("Woodland Boots", "Rugged boots.", "Woodland", "Wood", False),  # inside the brand word
    ],
)
def test_attribute_evidence_excludes_substrings_brand_only_and_ambiguous_cases(
    title, description, brand, value, expected
) -> None:
    assert attribute_evidence(title, description, brand, value) is expected


def _clean(fields: dict) -> corrupt.Clean:
    raw = parse_raw_listing(raw_bytes(fields))
    return corrupt.Clean(
        item_id="fk:t",
        source_id="flipkart",
        source_row_id="t",
        group_key="flipkart:t",
        split="dev",
        category="shoes",
        fields=fields,
        normalization=normalize_listing(raw),
    )


def _swap(fields: dict, h: str = "0" * 64):
    return corrupt.proposed_change(_clean(fields), "attribute_swap", h)


_SHOE = {
    "seller_id": "s",
    "seller_listing_ref": "r",
    "title": "Nike Men's Loafers",
    "description": "Brown leather loafers.",
    "brand": "Nike",
    "category": "shoes",
    "color": "Brown",
    "material": "Leather",
}


def test_the_generator_skips_brand_only_and_substring_evidence() -> None:
    brand_only = _SHOE | {
        "title": "Red Tape Men's Loafers",
        "description": "Comfortable loafers.",
        "brand": "Red Tape",
        "color": "Red",
        "material": "Suede",
    }
    assert _swap(brand_only) is None
    redmi = _SHOE | {"title": "Loafers for Redmi fans", "description": "", "color": "Red"}
    redmi.pop("material")
    assert _swap(redmi) is None
    no_brand = {k: v for k, v in _SHOE.items() if k != "brand"}
    assert _swap(no_brand) is None


def test_the_generator_takes_an_explicit_mention_and_an_absent_replacement() -> None:
    key, original, new = _swap(_SHOE)
    assert (key, original) == ("color", "Brown")
    assert not mentions(f"{_SHOE['title']}\n{_SHOE['description']}", new)


@pytest.mark.parametrize("h", [f"{i:064x}" for i in range(12)])
def test_a_replacement_is_never_mentioned_but_substrings_do_not_block_it(h) -> None:
    fields = _SHOE | {"description": "Brown leather loafers with black and blue laces, Grey-ish."}
    _, _, new = _swap(fields, h)
    assert new not in ("Black", "Blue") and not mentions(fields["description"], new)
    blackberry = _SHOE | {"description": "Brown leather loafers, Blackberry edition."}
    assert {_swap(blackberry, f"{i:064x}")[2] for i in range(40)} >= {"Black"}  # not blocked


def _run(tmp_path, twin_fields: dict, corrupt_fields: dict, ref: dict):
    # Only this pair corrupts the replaced twin; the fixture's other copies of it are dropped.
    items = {k: v for k, v in copy.deepcopy(ITEMS).items() if k not in ("shoe-1-c1", "shoe-1-c3")}
    _, source, group, split, labels, _, _ = items["shoe-1"]
    items["shoe-1"] = (twin_fields, source, group, split, labels, None, None)
    items["shoe-1-c2"] = (corrupt_fields, source, group, split, [], "shoe-1", ref)
    set_path, raw_dir = write_fixture(tmp_path, items)
    return evaluate(load_set(set_path), raw_dir)


def test_the_pair_checker_refuses_brand_only_evidence(tmp_path) -> None:
    twin = SHOE_CASUAL | {
        "title": "Red Tape Men's Leather Loafers",
        "description": "Leather loafers for everyday wear.",
        "brand": "Red Tape",
        "color": "Red",
    }
    bad = twin | {"seller_listing_ref": "shoe-1-c2", "color": "Blue"}
    with pytest.raises(EvalSetError, match="original value in the text"):
        _run(tmp_path, twin, bad, corruption("attribute_swap", "color", "Red"))


def test_the_pair_checker_refuses_a_replacement_already_in_the_text(tmp_path) -> None:
    twin = SHOE_CASUAL | {"description": "Brown leather loafers with black laces."}
    bad = twin | {"seller_listing_ref": "shoe-1-c2", "color": "Black"}
    with pytest.raises(EvalSetError, match="replacement absent from the text"):
        _run(tmp_path, twin, bad, corruption("attribute_swap", "color", "Brown"))


def test_disclosures_share_the_matching_definition(tmp_path) -> None:
    set_path, raw_dir = write_fixture(tmp_path)
    report, _ = evaluate(load_set(set_path), raw_dir)
    disclosure = report["descriptive"]["title_word_disclosure"]
    assert disclosure["matching"] == "lt-1"
