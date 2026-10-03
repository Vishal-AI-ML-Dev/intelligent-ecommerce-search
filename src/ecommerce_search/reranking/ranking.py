"""Deterministic head-only reranking of the RRF candidate list (pure: no I/O, no model).

Input is the existing fused list in RRF order (`rrf_score` descending, then `product_id`
ascending, as `fuse_rrf` returns it). Its 1-based position is the candidate's `rrf_rank`. Only
the first `rerank_depth` candidates (the head) are scored. The head is reordered by:

1. `rerank_score` descending;
2. then the original `rrf_rank` ascending (so equal scores keep RRF order);
3. then `product_id` ascending (unreachable, because `rrf_rank` is unique; kept as a guard).

The unscored tail follows the head unchanged, in RRF order. Each output item wraps the original
candidate object untouched, so RRF and source ranks and scores are preserved exactly.
`rerank_rank` is contiguous `1..len(head)` on the head; `rerank_rank` and `rerank_score` are
`None` on the tail. The output is always a permutation of the input.

Three ranks are kept distinct. The final response rank is the 1-based position in the returned
list (it is not stored). `rrf_rank` is the original position in the RRF list. `rerank_rank` is
the position within the scored head only. On the head the final rank equals `rerank_rank`; on
the tail it equals `rrf_rank`.

Scores are raw logits: uncalibrated, not probabilities, comparable only within one query.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from numbers import Rational, Real
from typing import Protocol

from ecommerce_search.reranking.provider import validate_scores


class RankedCandidate(Protocol):
    """What reranking reads from a fused candidate (for example `search.hybrid.FusedCandidate`)."""

    @property
    def product_id(self) -> str: ...

    @property
    def rrf_score(self) -> Fraction | float: ...


@dataclass(frozen=True)
class RerankedCandidate[C: RankedCandidate]:
    candidate: C  # the original fused candidate, unchanged
    rrf_rank: int  # 1-based position in the RRF list
    rerank_rank: int | None  # 1..head size on the scored head; None on the tail
    rerank_score: float | None  # raw logit on the scored head; None on the tail


def _check_depth(rerank_depth: int) -> None:
    if isinstance(rerank_depth, bool) or not isinstance(rerank_depth, int) or rerank_depth < 1:
        raise ValueError("rerank_depth must be a positive integer")


def head_size(candidate_count: int, rerank_depth: int) -> int:
    """How many candidates are scored: `min(candidate_count, rerank_depth)`."""
    _check_depth(rerank_depth)
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
        raise ValueError("candidate_count must be a non-negative integer")
    if candidate_count < 0:
        raise ValueError("candidate_count must be a non-negative integer")
    return min(candidate_count, rerank_depth)


def _rrf_key(candidate: RankedCandidate) -> tuple:
    product_id = getattr(candidate, "product_id", None)
    score = getattr(candidate, "rrf_score", None)
    if not isinstance(product_id, str) or not product_id:
        raise ValueError("candidate product ids must be non-empty strings")
    if isinstance(score, bool) or not isinstance(score, Real):
        raise ValueError("candidate rrf_score must be a real number")
    # A Rational (e.g. the exact `Fraction` of `fuse_rrf`) is always finite; converting a huge
    # one to float for `isfinite` would overflow.
    if not isinstance(score, Rational) and not math.isfinite(score):
        raise ValueError("candidate rrf_score must be finite")
    return (-score, product_id)


def check_rrf_order(candidates: Sequence[RankedCandidate]) -> None:
    """Raise `ValueError` unless `candidates` have unique ids and are in strict RRF order."""
    if isinstance(candidates, str | bytes) or not isinstance(candidates, Sequence):
        raise ValueError("candidates must be a sequence")
    keys = [_rrf_key(candidate) for candidate in candidates]
    if len({key[1] for key in keys}) != len(keys):
        raise ValueError("candidate product ids must be unique")
    if any(keys[i] >= keys[i + 1] for i in range(len(keys) - 1)):
        raise ValueError("candidates must be in RRF order (rrf_score desc, product_id asc)")


def rerank_head[C: RankedCandidate](
    candidates: Sequence[C], head_scores: Sequence[float], rerank_depth: int
) -> list[RerankedCandidate[C]]:
    """Rerank the first `rerank_depth` RRF candidates by `head_scores` (pure, deterministic).

    `head_scores[i]` is the score of `candidates[i]` and must hold exactly
    `head_size(len(candidates), rerank_depth)` finite numbers (else `RerankerUnavailable`
    `invalid_output`). Malformed candidates, duplicate ids, a list not in RRF order or an
    invalid depth raise `ValueError`. Zero candidates give `[]`; a depth at or above the
    candidate count scores every candidate and leaves no tail."""
    _check_depth(rerank_depth)
    check_rrf_order(candidates)
    size = head_size(len(candidates), rerank_depth)
    scores = validate_scores(head_scores, count=size)
    head = sorted(
        (
            (score, rrf_rank, candidate)
            for rrf_rank, (candidate, score) in enumerate(
                zip(candidates[:size], scores, strict=True), start=1
            )
        ),
        key=lambda item: (-item[0], item[1], item[2].product_id),
    )
    out: list[RerankedCandidate[C]] = [
        RerankedCandidate(
            candidate=candidate, rrf_rank=rrf_rank, rerank_rank=position, rerank_score=score
        )
        for position, (score, rrf_rank, candidate) in enumerate(head, start=1)
    ]
    out += [
        RerankedCandidate(
            candidate=candidate, rrf_rank=rrf_rank, rerank_rank=None, rerank_score=None
        )
        for rrf_rank, candidate in enumerate(candidates[size:], start=size + 1)
    ]
    return out
