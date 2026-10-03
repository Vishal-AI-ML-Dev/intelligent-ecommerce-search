"""Milestone 8 Session A: the reranker provider contract, sanitized errors and output validation."""

import math
from fractions import Fraction

import pytest

from ecommerce_search.reranking.provider import (
    REASON_CODES,
    RerankerProvider,
    RerankerUnavailable,
    score_documents,
    validate_scores,
    validate_token_counts,
)
from fake_reranker import FAKE_SPEC, FakeReranker

pytestmark = pytest.mark.usefixtures("no_socket_connect")


def _reason(excinfo) -> str:
    return excinfo.value.reason


# ---- errors ------------------------------------------------------------------------------------


def test_reason_codes_are_closed_and_messages_fixed():
    assert {
        "snapshot_missing",
        "load_failed",
        "config_mismatch",
        "input_too_long",
        "invalid_output",
        "score_failed",
    } == REASON_CODES
    for reason in REASON_CODES:
        exc = RerankerUnavailable(reason)
        assert exc.reason == reason
        assert str(exc) == f"reranker unavailable: {reason}"


@pytest.mark.parametrize("reason", ["", "boom", "C:/secret/path", "invalid output"])
def test_unknown_reason_codes_are_rejected(reason):
    with pytest.raises(ValueError, match="unknown reranker reason code"):
        RerankerUnavailable(reason)  # type: ignore[arg-type]


def test_fake_satisfies_the_protocol_structurally():
    provider: RerankerProvider = FakeReranker()
    assert provider.spec is FAKE_SPEC


# ---- validate_scores ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([], []),
        ((), []),
        ([1.5, -2.25, 0.0], [1.5, -2.25, 0.0]),
        ((3, -4), [3.0, -4.0]),
        ([Fraction(1, 4)], [0.25]),
        ([-0.0], [0.0]),
    ],
)
def test_valid_scores(raw, expected):
    count = len(expected)
    out = validate_scores(raw, count=count)
    assert out == expected
    assert all(type(v) is float for v in out)


@pytest.mark.parametrize(
    ("raw", "count"),
    [
        ([1.0], 0),
        ([], 1),
        ([1.0, 2.0], 1),
        ([1.0], 2),
        ([True], 1),
        ([False, 1.0], 2),
        (["1.0"], 1),
        ([b"1"], 1),
        ([[1.0]], 1),
        ([(1.0,)], 1),
        ([None], 1),
        ([{"score": 1.0}], 1),
        ([math.nan], 1),
        ([math.inf], 1),
        ([-math.inf], 1),
        ([1 + 2j], 1),
        ("1", 1),
        (b"\x01", 1),
        (bytearray(b"\x01"), 1),
        (memoryview(b"\x01"), 1),
        ({1.0}, 1),
        ({0: 1.0}, 1),
        (iter([1.0]), 1),
        (1.0, 1),
        (None, 0),
    ],
)
def test_invalid_scores_are_invalid_output(raw, count):
    with pytest.raises(RerankerUnavailable) as excinfo:
        validate_scores(raw, count=count)
    assert _reason(excinfo) == "invalid_output"
    assert str(excinfo.value) == "reranker unavailable: invalid_output"


def test_huge_integer_overflow_is_invalid_output():
    with pytest.raises(RerankerUnavailable) as excinfo:
        validate_scores([10**400], count=1)
    assert _reason(excinfo) == "invalid_output"


@pytest.mark.parametrize("count", [-1, True, 1.0, "1", None])
def test_bad_expected_count_is_a_programming_error(count):
    with pytest.raises(ValueError, match="count"):
        validate_scores([], count=count)


# ---- validate_token_counts ---------------------------------------------------------------------


def test_token_counts_within_the_limit():
    assert validate_token_counts([3, 512, 0], count=3, max_length=512) == [3, 512, 0]
    assert validate_token_counts([], count=0, max_length=1) == []


def test_over_limit_pair_is_input_too_long_never_truncated():
    with pytest.raises(RerankerUnavailable) as excinfo:
        validate_token_counts([10, 513], count=2, max_length=512)
    assert _reason(excinfo) == "input_too_long"


@pytest.mark.parametrize(
    ("raw", "count"),
    [
        ([1], 2),
        ([1, 2], 1),
        ([True], 1),
        ([-1], 1),
        ([1.0], 1),
        (["3"], 1),
        ([[3]], 1),
        ("3", 1),
        (b"\x03", 1),
        (memoryview(b"\x03"), 1),
        (None, 0),
        ({3}, 1),
    ],
)
def test_malformed_token_counts_are_invalid_output(raw, count):
    with pytest.raises(RerankerUnavailable) as excinfo:
        validate_token_counts(raw, count=count, max_length=512)
    assert _reason(excinfo) == "invalid_output"


def test_malformed_counts_win_over_too_long_counts_in_any_position():
    for raw in ([10_000, -1], [-1, 10_000]):
        with pytest.raises(RerankerUnavailable) as excinfo:
            validate_token_counts(raw, count=2, max_length=512)
        assert _reason(excinfo) == "invalid_output"


@pytest.mark.parametrize("max_length", [0, -5, True, 1.5])
def test_bad_max_length_is_a_programming_error(max_length):
    with pytest.raises(ValueError, match="max_length"):
        validate_token_counts([], count=0, max_length=max_length)


# ---- score_documents ---------------------------------------------------------------------------


def _loaded() -> FakeReranker:
    fake = FakeReranker()
    fake.load()
    return fake


def test_scores_one_logit_per_document_in_input_order():
    fake = _loaded()
    docs = ["hp laptop silver", "nike shoes", "hp printer"]
    assert score_documents(fake, "hp laptop", docs) == [2.0, 0.0, 1.0]
    assert fake.count_calls == [("hp laptop", docs)]
    assert fake.score_calls == [("hp laptop", docs)]


def test_empty_documents_never_call_the_provider():
    fake = FakeReranker()
    assert score_documents(fake, "anything", []) == []
    assert (fake.load_calls, fake.count_calls, fake.score_calls) == (0, [], [])


def test_length_check_happens_before_scoring():
    fake = _loaded()
    fake.token_output = lambda q, docs: [FAKE_SPEC.max_length + 1 for _ in docs]
    with pytest.raises(RerankerUnavailable) as excinfo:
        score_documents(fake, "q", ["a", "b"])
    assert _reason(excinfo) == "input_too_long"
    assert fake.score_calls == []


@pytest.mark.parametrize(
    "output",
    [
        lambda q, d: [1.0] * (len(d) + 1),
        lambda q, d: [1.0] * (len(d) - 1),
        lambda q, d: [math.nan] * len(d),
        lambda q, d: [True] * len(d),
        lambda q, d: ["1"] * len(d),
        lambda q, d: [[1.0]] * len(d),
        lambda q, d: None,
        lambda q, d: {"scores": [1.0] * len(d)},
    ],
)
def test_malformed_provider_output_is_invalid_output(output):
    fake = _loaded()
    fake.score_output = output
    with pytest.raises(RerankerUnavailable) as excinfo:
        score_documents(fake, "q", ["a", "b"])
    assert _reason(excinfo) == "invalid_output"


def test_provider_failures_propagate_unchanged():
    fake = _loaded()
    fake.failures["score"] = RerankerUnavailable("score_failed")
    with pytest.raises(RerankerUnavailable) as excinfo:
        score_documents(fake, "q", ["a"])
    assert _reason(excinfo) == "score_failed"
    fake = _loaded()
    fake.failures["count"] = RerankerUnavailable("load_failed")
    with pytest.raises(RerankerUnavailable) as excinfo:
        score_documents(fake, "q", ["a"])
    assert _reason(excinfo) == "load_failed"
    assert fake.score_calls == []


@pytest.mark.parametrize("stage", ["count", "score"])
def test_unexpected_provider_errors_are_not_hidden(stage):
    # Programming errors are not mapped to a reason code (they surface as a generic 500 later).
    fake = _loaded()
    fake.failures[stage] = RuntimeError("programming error")
    with pytest.raises(RuntimeError, match="programming error"):
        score_documents(fake, "q", ["a"])


def test_numpy_float_scalars_are_accepted_but_arrays_must_be_converted():
    # The future concrete provider deliberately converts its array output to a list of scalars.
    np = pytest.importorskip("numpy")
    out = validate_scores([np.float32(1.5), np.float64(-2.0)], count=2)
    assert out == [1.5, -2.0] and all(type(v) is float for v in out)
    assert validate_scores(np.asarray([1.5, -2.0], dtype=np.float32).tolist(), count=2) == out
    for raw in (np.asarray([1.5, -2.0]), [np.bool_(True), 1.0], [np.float32("nan"), 1.0]):
        with pytest.raises(RerankerUnavailable) as excinfo:
            validate_scores(raw, count=2)
        assert _reason(excinfo) == "invalid_output"


def test_unloaded_fake_refuses_to_score():
    with pytest.raises(RerankerUnavailable) as excinfo:
        score_documents(FakeReranker(), "q", ["a"])
    assert _reason(excinfo) == "load_failed"


@pytest.mark.parametrize(
    ("query", "documents"),
    [(None, ["a"]), (b"q", ["a"]), ("q", "abc"), ("q", [1]), ("q", None), ("q", ["a", None])],
)
def test_malformed_inputs_are_programming_errors(query, documents):
    with pytest.raises(TypeError):
        score_documents(_loaded(), query, documents)


def test_scoring_is_deterministic():
    docs = ["hp laptop", "dell laptop 16gb", "laptop bag"]
    assert score_documents(_loaded(), "laptop 16gb", docs) == score_documents(
        _loaded(), "laptop 16gb", docs
    )
