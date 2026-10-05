"""Deterministic review sets for the single-annotator workflow (D5 final).

* `build_consistency_sample`: the statistical consistency sample, built only from a sealed
  primary round. Exactly T = ceil(20% × N) items overall (not per stratum), allocated by
  `rr-label-query-1`:
  1. inside each (label, query) stratum, items are ordered by sha256("cs-1|query|product");
  2. labels are visited round-robin in the fixed order relevant → borderline → not_relevant;
     each visit takes the next item of that label from the non-exhausted query stratum with the
     fewest picks for that label, then the fewest picks for that query across all labels, then
     the smallest sha256("cs-1|query");
  3. exhausted labels and strata are skipped, and selection stops at exactly T.
  The per-label counts therefore differ by at most one wherever supply allows, and the queries
  inside a label are as even as possible. Items are re-ordered blind by
  sha256("consistency-1|<primary seal>|query|product") and carry no previous label.
* `build_adjudication_queue`: the separate targeted-review set. It never changes N, T or the
  consistency denominator.
* `consistency_disagreements` and `derive_final_judgements`.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from evals.contracts.canonical import ContractError
from evals.contracts.constants import (
    CONSISTENCY_BLIND_ORDER_VERSION,
    CONSISTENCY_SAMPLE_VERSION,
    LABEL_ORDER,
    Label,
)
from evals.contracts.models import (
    AllocationCell,
    Confidence,
    ConsistencySample,
    EligibilityRecord,
    FinalJudgement,
    IntentFamily,
    Judgement,
    OverallStatus,
    QueryRecord,
    QueueItem,
    ReviewReason,
    Round,
    SampleItem,
    consistency_target,
)

Pair = tuple[str, str]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _by_pair(judgements: Iterable[Judgement], round_: Round) -> dict[Pair, Judgement]:
    result: dict[Pair, Judgement] = {}
    for judgement in judgements:
        if judgement.round is not round_:
            raise ContractError(f"expected only {round_.value} judgements")
        pair = (judgement.query_id, judgement.product_id)
        if pair in result:
            raise ContractError(f"duplicate {round_.value} judgement for {pair!r}")
        result[pair] = judgement
    return result


def build_consistency_sample(
    primaries: Sequence[Judgement], primary_seal_sha256: str, primary_sealed_at: datetime
) -> ConsistencySample:
    by_pair = _by_pair(primaries, Round.PRIMARY)
    strata: dict[Label, dict[str, list[Pair]]] = defaultdict(lambda: defaultdict(list))
    for (query_id, product_id), judgement in by_pair.items():
        strata[judgement.label][query_id].append((query_id, product_id))
    for per_query in strata.values():
        for items in per_query.values():
            items.sort(key=lambda pair: _sha(f"{CONSISTENCY_SAMPLE_VERSION}|{pair[0]}|{pair[1]}"))

    target = consistency_target(len(by_pair))
    picks: dict[tuple[Label, str], int] = defaultdict(int)
    query_picks: dict[str, int] = defaultdict(int)
    selected: list[Pair] = []
    while len(selected) < target:
        progressed = False
        for label in LABEL_ORDER:
            if len(selected) == target:
                break
            available = [q for q, items in strata.get(label, {}).items() if items]
            if not available:
                continue
            query_id = min(
                available,
                key=lambda q: (
                    picks.get((label, q), 0),  # .get: never create zero-count cells
                    query_picks.get(q, 0),
                    _sha(f"{CONSISTENCY_SAMPLE_VERSION}|{q}"),
                ),
            )
            selected.append(strata[label][query_id].pop(0))
            picks[(label, query_id)] += 1
            query_picks[query_id] += 1
            progressed = True
        if not progressed:  # cannot happen: target <= population
            raise ContractError("the consistency sample cannot reach its target")

    blind = sorted(
        selected,
        key=lambda pair: _sha(
            f"{CONSISTENCY_BLIND_ORDER_VERSION}|{primary_seal_sha256}|{pair[0]}|{pair[1]}"
        ),
    )
    allocation = sorted(
        (
            AllocationCell(label=label, query_id=query_id, count=count)
            for (label, query_id), count in picks.items()
        ),
        key=lambda cell: (LABEL_ORDER.index(cell.label), cell.query_id),
    )
    return ConsistencySample(
        primary_seal_sha256=primary_seal_sha256,
        primary_sealed_at=primary_sealed_at,
        population_count=len(by_pair),
        target_count=target,
        actual_count=len(selected),
        allocation=tuple(allocation),
        items=tuple(
            SampleItem(query_id=q, product_id=p, blind_position=position)
            for position, (q, p) in enumerate(blind, start=1)
        ),
    )


def build_adjudication_queue(
    primaries: Sequence[Judgement],
    queries: Sequence[QueryRecord],
    eligibility: Sequence[EligibilityRecord],
) -> list[QueueItem]:
    families = {q.query_id: q.intent_family for q in queries}
    hard = {(e.query_id, e.product_id): e.hard_overall for e in eligibility}
    queue: list[QueueItem] = []
    for pair, judgement in sorted(_by_pair(primaries, Round.PRIMARY).items()):
        reasons: set[ReviewReason] = set()
        if judgement.confidence is Confidence.LOW:
            reasons.add(ReviewReason.LOW_CONFIDENCE)
        if judgement.needs_adjudication:
            reasons.add(ReviewReason.NEEDS_ADJUDICATION)
        if families.get(pair[0]) is IntentFamily.AMBIGUOUS:
            reasons.add(ReviewReason.AMBIGUOUS_QUERY)
        if judgement.label is Label.BORDERLINE:
            reasons.add(ReviewReason.BORDERLINE)
        if hard.get(pair) is OverallStatus.UNKNOWN:
            reasons.add(ReviewReason.HARD_CONSTRAINT_UNKNOWN)
        if judgement.catalog_issue:
            reasons.add(ReviewReason.CATALOG_ISSUE)
        if reasons:
            queue.append(
                QueueItem(
                    query_id=pair[0], product_id=pair[1], review_reasons=tuple(sorted(reasons))
                )
            )
    return queue


def consistency_disagreements(
    primaries: Sequence[Judgement], consistency: Sequence[Judgement]
) -> frozenset[Pair]:
    primary = _by_pair(primaries, Round.PRIMARY)
    repeat = _by_pair(consistency, Round.CONSISTENCY)
    missing = sorted(set(repeat) - set(primary))
    if missing:
        raise ContractError(f"consistency judgements without a primary judgement: {missing!r}")
    return frozenset(pair for pair, j in repeat.items() if j.label is not primary[pair].label)


def derive_final_judgements(
    primaries: Sequence[Judgement], adjudications: Sequence[Judgement]
) -> list[FinalJudgement]:
    primary = _by_pair(primaries, Round.PRIMARY)
    decided: Mapping[Pair, Judgement] = _by_pair(adjudications, Round.ADJUDICATION)
    unknown = sorted(set(decided) - set(primary))
    if unknown:
        raise ContractError(f"adjudications without a primary judgement: {unknown!r}")
    finals = []
    for pair in sorted(primary):
        source = decided.get(pair, primary[pair])
        finals.append(
            FinalJudgement(
                query_id=pair[0],
                product_id=pair[1],
                label=source.label,
                relation=source.relation,
                decided_in="adjudication" if pair in decided else "primary",
                rubric_version=source.rubric_version,
            )
        )
    return finals
