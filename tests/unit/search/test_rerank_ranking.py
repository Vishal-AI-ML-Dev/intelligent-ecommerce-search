"""Milestone 8 Session A: deterministic head-only reranking.

Expected orders are written by hand from the approved rule (score desc, then rrf_rank, then
product_id; unchanged RRF tail), not copied from the implementation."""

import dataclasses
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

import pytest

from ecommerce_search.reranking.provider import RerankerUnavailable
from ecommerce_search.reranking.ranking import (
    RerankedCandidate,
    check_rrf_order,
    head_size,
    rerank_head,
)
from ecommerce_search.search.dense import DenseHit
from ecommerce_search.search.hybrid import fuse_rrf
from ecommerce_search.search.lexical import LexicalHit

pytestmark = pytest.mark.usefixtures("no_socket_connect")

HIT_FIELDS = {
    "title": "t",
    "brand": "b",
    "category": "laptop",
    "subcategory": None,
    "description": None,
    "price": Decimal("1.00"),
    "currency": "INR",
    "rating": None,
    "review_count": None,
    "availability": "in_stock",
}


@dataclass(frozen=True)
class Cand:
    """Shaped like `search.hybrid.FusedCandidate` (which this pure module must not import)."""

    product_id: str
    rrf_score: Fraction
    lexical_rank: int | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None


def rrf_list(n: int, rrf_k: int = 100) -> list[Cand]:
    """n candidates in strict RRF order: P01 best, then P02, ..."""
    return [
        Cand(f"P{i:02d}", Fraction(1, rrf_k + i), lexical_rank=i, dense_score=0.5)
        for i in range(1, n + 1)
    ]


def ids(out: list[RerankedCandidate]) -> list[str]:
    return [item.candidate.product_id for item in out]


def test_head_is_reordered_and_tail_is_unchanged():
    cands = rrf_list(6)
    out = rerank_head(cands, [0.1, 3.0, -1.0, 2.0], rerank_depth=4)
    assert ids(out) == ["P02", "P04", "P01", "P03", "P05", "P06"]
    assert [i.rerank_rank for i in out] == [1, 2, 3, 4, None, None]
    assert [i.rerank_score for i in out] == [3.0, 2.0, 0.1, -1.0, None, None]
    assert [i.rrf_rank for i in out] == [2, 4, 1, 3, 5, 6]
    # Tail items wrap the very same objects, in RRF order.
    assert out[4].candidate is cands[4] and out[5].candidate is cands[5]


def test_original_rrf_and_source_fields_are_preserved_by_identity():
    cands = rrf_list(5)
    out = rerank_head(cands, [5, 4, 3, 2, 1], rerank_depth=5)
    by_id = {c.product_id: c for c in cands}
    for item in out:
        assert item.candidate is by_id[item.candidate.product_id]
        assert item.rrf_rank == cands.index(item.candidate) + 1


def test_equal_scores_keep_rrf_order():
    cands = rrf_list(5)
    out = rerank_head(cands, [1.0, 1.0, 1.0, 1.0, 1.0], rerank_depth=5)
    assert ids(out) == ["P01", "P02", "P03", "P04", "P05"]
    out = rerank_head(cands, [0.0, 2.0, 0.0, 2.0, 0.0], rerank_depth=5)
    assert ids(out) == ["P02", "P04", "P01", "P03", "P05"]


def test_negative_zero_ties_with_zero():
    out = rerank_head(rrf_list(2), [0.0, -0.0], rerank_depth=2)
    assert ids(out) == ["P01", "P02"]


def test_product_id_is_the_final_guard_for_equal_rrf_scores():
    # Equal rrf_score is legal in RRF output (product_id breaks the tie) and must stay ordered.
    same = Fraction(1, 101)
    cands = [Cand("A", same), Cand("B", same), Cand("C", Fraction(1, 102))]
    out = rerank_head(cands, [1.0, 1.0, 1.0], rerank_depth=3)
    assert ids(out) == ["A", "B", "C"]
    assert ids(rerank_head(cands, [0.0, 1.0, 1.0], rerank_depth=3)) == ["B", "C", "A"]


@pytest.mark.parametrize(("n", "depth", "head"), [(0, 1, 0), (3, 1, 1), (3, 3, 3), (3, 20, 3)])
def test_head_size(n, depth, head):
    assert head_size(n, depth) == head


def test_zero_candidates():
    assert rerank_head([], [], rerank_depth=20) == []


def test_depth_one_scores_only_the_first():
    out = rerank_head(rrf_list(3), [-100.0], rerank_depth=1)
    assert ids(out) == ["P01", "P02", "P03"]
    assert [i.rerank_rank for i in out] == [1, None, None]


def test_depth_at_or_above_count_scores_everything():
    for depth in (3, 4, 50):
        out = rerank_head(rrf_list(3), [1.0, 3.0, 2.0], rerank_depth=depth)
        assert ids(out) == ["P02", "P03", "P01"]
        assert [i.rerank_rank for i in out] == [1, 2, 3]
        assert all(i.rerank_score is not None for i in out)


@pytest.mark.parametrize("seed", range(25))
def test_randomized_invariants(seed):
    rng = random.Random(seed)  # noqa: S311 - seeded test data, not security
    n = rng.randint(0, 50)
    depth = rng.randint(1, 60)
    cands = rrf_list(n)
    size = min(n, depth)
    scores = [float(rng.choice([-2, -1, 0, 0, 1, 2])) + rng.choice([0.0, 0.5]) for _ in range(size)]
    out = rerank_head(cands, scores, rerank_depth=depth)
    # permutation of the input, by object identity
    assert sorted(map(id, (i.candidate for i in out))) == sorted(map(id, cands))
    assert len(out) == n
    head, tail = out[:size], out[size:]
    assert [i.rerank_rank for i in head] == list(range(1, size + 1))
    assert all(i.rerank_rank is None and i.rerank_score is None for i in tail)
    assert [i.candidate for i in tail] == cands[size:]
    assert [i.rrf_rank for i in tail] == list(range(size + 1, n + 1))
    keys = [(-i.rerank_score, i.rrf_rank) for i in head]
    assert keys == sorted(keys)
    assert {i.candidate.product_id for i in head} == {c.product_id for c in cands[:size]}
    for item in head:
        assert item.rerank_score == scores[item.rrf_rank - 1]
    assert rerank_head(cands, scores, rerank_depth=depth) == out


@pytest.mark.parametrize(
    ("scores", "depth"),
    [
        ([1.0, 2.0], 3),  # too few for min(n=3, depth=3)
        ([1.0, 2.0, 3.0, 4.0], 3),  # too many
        ([1.0], 2),
        ([math.nan, 1.0, 2.0], 3),
        ([math.inf, 1.0, 2.0], 3),
        ([True, 1.0, 2.0], 3),
        (["1", 1.0, 2.0], 3),
        ([[1.0], 1.0, 2.0], 3),
        (None, 3),
    ],
)
def test_invalid_scores_are_invalid_output(scores, depth):
    with pytest.raises(RerankerUnavailable) as excinfo:
        rerank_head(rrf_list(3), scores, rerank_depth=depth)
    assert excinfo.value.reason == "invalid_output"


@pytest.mark.parametrize("depth", [0, -1, True, 1.0, "20", None])
def test_invalid_depth(depth):
    with pytest.raises(ValueError, match="rerank_depth"):
        rerank_head(rrf_list(3), [1.0], rerank_depth=depth)
    with pytest.raises(ValueError):
        head_size(3, depth)


@pytest.mark.parametrize("count", [-1, True, 2.0, None])
def test_invalid_candidate_count(count):
    with pytest.raises(ValueError, match="candidate_count"):
        head_size(count, 5)


def test_duplicate_ids_are_rejected():
    cands = [Cand("A", Fraction(1, 101)), Cand("A", Fraction(1, 102))]
    with pytest.raises(ValueError, match="unique"):
        rerank_head(cands, [1.0, 2.0], rerank_depth=2)


@pytest.mark.parametrize(
    "cands",
    [
        [Cand("P02", Fraction(1, 102)), Cand("P01", Fraction(1, 101))],  # scores ascending
        [Cand("B", Fraction(1, 101)), Cand("A", Fraction(1, 101))],  # tie not by product_id
    ],
)
def test_input_not_in_rrf_order_is_rejected(cands):
    with pytest.raises(ValueError, match="RRF order"):
        rerank_head(cands, [1.0, 2.0], rerank_depth=2)


@pytest.mark.parametrize(
    "bad",
    [
        Cand("", Fraction(1, 101)),
        Cand(None, Fraction(1, 101)),  # type: ignore[arg-type]
        Cand("A", None),  # type: ignore[arg-type]
        Cand("A", True),  # type: ignore[arg-type]
        Cand("A", "0.1"),  # type: ignore[arg-type]
        Cand("A", math.nan),  # type: ignore[arg-type]
        Cand("A", math.inf),  # type: ignore[arg-type]
        object(),
    ],
)
def test_malformed_candidates_are_rejected(bad):
    with pytest.raises(ValueError):
        rerank_head([bad], [1.0], rerank_depth=1)


@pytest.mark.parametrize("cands", ["P01", None, {Cand("A", Fraction(1, 101))}])
def test_candidates_must_be_a_sequence(cands):
    with pytest.raises(ValueError, match="sequence"):
        check_rrf_order(cands)


def test_huge_exact_rrf_score_is_handled_without_overflow():
    cands = [Cand("A", Fraction(10**400)), Cand("B", Fraction(1, 101))]
    assert ids(rerank_head(cands, [0.0, 1.0], rerank_depth=2)) == ["B", "A"]


@pytest.mark.parametrize("seed", range(10))
def test_committed_fuse_rrf_output_is_accepted_and_reversal_rejected(seed):
    # The RRF-order check uses the same rule as the committed `fuse_rrf`, including the
    # product_id tie-break for equal exact scores (a lexical-only and a dense-only candidate at
    # the same rank tie; the explicit case below guarantees such ties).
    tied = fuse_rrf(
        [
            LexicalHit(rank=1, lexical_score=1.0, product_id="B", **HIT_FIELDS),
            LexicalHit(rank=2, lexical_score=1.0, product_id="D", **HIT_FIELDS),
        ],
        [
            DenseHit(rank=1, dense_score=0.5, product_id="C", **HIT_FIELDS),
            DenseHit(rank=2, dense_score=0.5, product_id="A", **HIT_FIELDS),
        ],
        rrf_k=100,
        candidate_k=50,
    )
    assert [c.product_id for c in tied] == ["B", "C", "A", "D"]  # 1/101, 1/101, 1/102, 1/102
    check_rrf_order(tied)
    with pytest.raises(ValueError, match="RRF order"):
        check_rrf_order([tied[1], tied[0], tied[2], tied[3]])  # equal score, ids descending
    rng = random.Random(seed)  # noqa: S311 - seeded test data, not security
    pool = [f"P{i:02d}" for i in range(30)]
    lex_ids = rng.sample(pool, rng.randint(0, 15))
    den_ids = rng.sample(pool, rng.randint(0, 15))
    lexical = [
        LexicalHit(rank=r, lexical_score=1.0, product_id=p, **HIT_FIELDS)
        for r, p in enumerate(lex_ids, start=1)
    ]
    dense = [
        DenseHit(rank=r, dense_score=0.5, product_id=p, **HIT_FIELDS)
        for r, p in enumerate(den_ids, start=1)
    ]
    fused = fuse_rrf(lexical, dense, rrf_k=100, candidate_k=50)
    check_rrf_order(fused)
    out = rerank_head(fused, [0.0] * min(len(fused), 20), rerank_depth=20)
    assert [i.candidate for i in out] == fused  # all-equal scores keep RRF order
    if len(fused) > 1:
        with pytest.raises(ValueError, match="RRF order"):
            check_rrf_order(fused[::-1])


def test_input_sequence_and_candidates_are_not_modified():
    cands = rrf_list(6)
    snapshot = [dataclasses.replace(c) for c in cands]
    as_tuple = tuple(cands)
    scores = [0.1, 3.0, -1.0, 2.0]
    rerank_head(cands, scores, rerank_depth=4)
    rerank_head(as_tuple, tuple(scores), rerank_depth=4)
    assert cands == snapshot and all(a is b for a, b in zip(cands, as_tuple, strict=True))
    assert scores == [0.1, 3.0, -1.0, 2.0]


def test_float_rrf_scores_are_accepted():
    cands = [Cand("A", 0.02), Cand("B", 0.01)]  # type: ignore[arg-type]
    assert ids(rerank_head(cands, [0.0, 1.0], rerank_depth=2)) == ["B", "A"]


def test_reranked_candidate_is_frozen():
    (item,) = rerank_head(rrf_list(1), [1.0], rerank_depth=1)
    with pytest.raises(AttributeError):
        item.rerank_rank = 2  # type: ignore[misc]
