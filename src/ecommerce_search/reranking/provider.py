"""The typed reranker-provider interface and model-independent output validation (pure).

Callers depend on `RerankerProvider`, never on a concrete library. A provider scores
(query, document) pairs and returns one raw logit per document, in input order: identity travels
by position, and the ranking layer, not the provider, pairs scores with product ids.

Every failure to make the reranker usable, and every malformed output, raises
`RerankerUnavailable` with a fixed reason code. Its message never contains query or document
text, paths, exception text or other internal detail, so it is safe to log and to map to a fixed
HTTP 503. Nothing here logs, reads settings or files, or touches a model or the network.
"""

import math
from collections.abc import Sequence
from numbers import Real
from typing import Literal, Protocol, get_args

from ecommerce_search.reranking.spec import RerankerModelSpec

RerankerReasonCode = Literal[
    "snapshot_missing",
    "load_failed",
    "config_mismatch",
    "input_too_long",
    "invalid_output",
    "score_failed",
]
REASON_CODES: frozenset[str] = frozenset(get_args(RerankerReasonCode))


class RerankerUnavailable(Exception):
    """The reranker cannot be used. `reason` is one of `RerankerReasonCode`."""

    def __init__(self, reason: RerankerReasonCode) -> None:
        if reason not in REASON_CODES:
            raise ValueError("unknown reranker reason code")
        self.reason = reason
        super().__init__(f"reranker unavailable: {reason}")


class RerankerProvider(Protocol):
    """A local cross-encoder. Loading is lazy; `load` returns the load time in ms, or None when
    the model was already loaded."""

    @property
    def spec(self) -> RerankerModelSpec: ...

    def load(self) -> float | None: ...

    def count_pair_tokens(self, query: str, documents: Sequence[str]) -> list[int]: ...

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


def _check_count(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a non-negative integer")


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, str | bytes | bytearray | memoryview
    )


def validate_scores(raw: object, *, count: int) -> list[float]:
    """Return `raw` as `count` plain finite floats, or raise `RerankerUnavailable`.

    Accepted: a list or tuple (any non-string, non-bytes `Sequence`) of exactly `count` real
    numeric scalars (`numbers.Real`, which includes NumPy floating scalars). Rejected with
    `invalid_output`: another container (including an array, which a provider converts to a list
    of scalars first), a wrong length, `bool`, strings, bytes, nested sequences, non-numeric
    values, NaN, +/-Inf and values too large for a float. An empty output is valid only for
    `count == 0`."""
    _check_count(count)
    if not _is_sequence(raw) or len(raw) != count:
        raise RerankerUnavailable("invalid_output")
    scores: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise RerankerUnavailable("invalid_output")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise RerankerUnavailable("invalid_output") from None
        if not math.isfinite(number):
            raise RerankerUnavailable("invalid_output")
        scores.append(number)
    return scores


def validate_token_counts(raw: object, *, count: int, max_length: int) -> list[int]:
    """Return `count` pair token counts, or raise `RerankerUnavailable`.

    Malformed counts (wrong container or length, non-`int` values, `bool`, negative values) are
    `invalid_output` and are checked for every pair first; then any pair longer than
    `max_length` is `input_too_long`. Pairs are never truncated."""
    _check_count(count)
    if isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1:
        raise ValueError("max_length must be a positive integer")
    if not _is_sequence(raw) or len(raw) != count:
        raise RerankerUnavailable("invalid_output")
    counts: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RerankerUnavailable("invalid_output")
        counts.append(value)
    if any(value > max_length for value in counts):
        raise RerankerUnavailable("input_too_long")
    return counts


def _check_inputs(query: str, documents: Sequence[str]) -> list[str]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not _is_sequence(documents):
        raise TypeError("documents must be a sequence of strings")
    texts = list(documents)
    if not all(isinstance(text, str) for text in texts):
        raise TypeError("documents must be a sequence of strings")
    return texts


def score_documents(
    provider: RerankerProvider, query: str, documents: Sequence[str]
) -> list[float]:
    """Check pair lengths, score and validate: one finite logit per document, in input order.

    An empty `documents` returns `[]` without calling the provider. The provider must already be
    loaded by the caller (so the load can be timed separately); it is never asked to truncate.

    Error boundary: a provider reports expected availability and scoring failures as
    `RerankerUnavailable`, which propagates unchanged; malformed provider output becomes
    `invalid_output` here. Any other exception (a programming error) is deliberately not caught.
    Either way no score is returned unless every score is valid."""
    texts = _check_inputs(query, documents)
    if not texts:
        return []
    validate_token_counts(
        provider.count_pair_tokens(query, texts),
        count=len(texts),
        max_length=provider.spec.max_length,
    )
    return validate_scores(provider.score(query, texts), count=len(texts))
