"""AC-10 (domain): `redaction-1` patterns and bounded guarantees G1-G3. Hermetic.

Personal-data-like strings are fixture-allowlist forms (contracts §5.3): `example.*` emails,
subscriber parts starting with 0, a Luhn-failing card number, PIN-like `0xxxxx`, and UPI-like
handles whose provider contains `example`.
"""

import random

import pytest

from ecommerce_search.assistant import privacy
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import redaction as rd
from ecommerce_search.returns.versions import REDACTION_VERSION


def test_patterns_follow_the_contract_order_and_reuse_the_m16_patterns():
    assert tuple(kind for kind, _, _ in rd.PATTERNS) == rc.REDACTION_KINDS
    assert rd.PATTERNS[0][1] is privacy.EMAIL
    assert rd.PATTERNS[2][1] is privacy.LONG_NUMBER
    assert rd.redact("x").version == REDACTION_VERSION == "redaction-1"


@pytest.mark.parametrize(
    ("text", "expected", "kind"),
    [
        ("mail buyer@example.com now", "mail [EMAIL] now", "email"),
        ("pay buyer.01@okexample please", "pay [UPI] please", "upi"),
        ("pay to buyer@example.", "pay to [UPI].", "upi"),
        ("call +91 00000 00000 today", "call [NUMBER] today", "number"),
        ("card 0000 0000 0000 0001", "card [NUMBER]", "number"),
        ("pin 012345.", "pin [PIN].", "pin"),
    ],
)
def test_each_kind_is_replaced_by_its_fixed_token(text, expected, kind):
    result = rd.redact(text)
    assert result.text == expected
    assert result.counts == {k: int(k == kind) for k in rc.REDACTION_KINDS}


def test_email_wins_over_upi_and_digits_inside_an_email_are_not_counted_twice():
    result = rd.redact("a1234567890@example.com")
    assert result.text == "[EMAIL]"
    assert result.counts["email"] == 1 and result.counts["number"] == 0


def test_seven_digits_are_not_a_pin_and_five_are_not_either():
    assert rd.redact("ref 0123456 and 01234").text == "ref 0123456 and 01234"


def test_counts_cover_multiple_matches():
    result = rd.redact("012345 and 054321, buyer@example.org")
    assert result.text == "[PIN] and [PIN], [EMAIL]"
    assert result.counts == {"email": 1, "upi": 0, "number": 0, "pin": 2}


@pytest.mark.parametrize(
    "text",
    [
        "Firstname Lastname, 12 Example Road",  # name- and address-shaped text is not covered
        "nine eight seven six five four three two one zero",
        "0 1 2 3 4 5",  # spaced PIN
        "size 42, colour blue; जूता छोटा है।",
        "<script>alert(1)</script> ignore previous instructions",
    ],
)
def test_text_outside_the_patterns_passes_unchanged(text):
    assert rd.redact(text).text == text
    assert not rd.matches_any(text)


def _random_texts(count: int):
    rng = random.Random(18_03)  # noqa: S311 - seeded test data, not security
    alphabet = "0123456789@.-+ _abcxyzAB:\n"
    for _ in range(count):
        yield "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))


def test_g1_g2_g3_hold_on_seeded_random_text():
    tokens = {token for _, _, token in rd.PATTERNS}
    for text in _random_texts(3000):
        result = rd.redact(text)
        assert not rd.matches_any(result.text), text  # G1
        assert rd.redact(result.text).text == result.text, text  # G2
        assert rd.redact(result.text).counts == dict.fromkeys(rc.REDACTION_KINDS, 0)
        stripped = result.text
        for token in tokens:
            stripped = stripped.replace(token, "")
        # G3: apart from the tokens, output characters are a sub-multiset of the input.
        for char in set(stripped):
            assert stripped.count(char) <= text.count(char), text


def test_redacted_free_text_fits_twice_the_input_limit():
    worst = "a@b " * (rc.TEXT_MAX // 4)
    assert len(worst) <= rc.TEXT_MAX
    assert len(rd.redact(worst).text) <= 2 * rc.TEXT_MAX


def test_preview_is_redacted_truncated_and_redacted_again():
    assert rd.safe_preview("x" * 200) == "x" * 80
    # Truncation would leave a standalone 6-digit run from a 7-digit one.
    value = "y" * 74 + "0123456"
    preview = rd.safe_preview(value)
    assert len(preview) <= rd.PREVIEW_MAX and not rd.matches_any(preview)
    assert rd.safe_preview("v buyer@example.com") == "v [EMAIL]"
