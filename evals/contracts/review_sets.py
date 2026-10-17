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
* `consistency_disagreements` and `derive_final_judgements` (review-1).
* `second_review_disagreements`, `build_joint_resolution_queue` and
  `derive_final_judgements_review_2` (review-2: the same cs-1 sample is re-labelled by a second,
  independent human; disagreements and second-reviewer flags are resolved by joint records).
* `split_review_2_targeted` (review-2 scope revision, approved 2026-10-16): the targeted-queue
  items that are not routed for joint resolution, split into the mandatory items (any reason
  other than a borderline primary label or an ambiguous query) and the optional review
  candidates (selected only for those two reasons).
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
    ConsensusAdjudication,
    ConsistencySample,
    EligibilityRecord,
    FinalJudgement,
    IntentFamily,
    JointReason,
    JointResolutionItem,
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


# ---- review-2 -------------------------------------------------------------------------------


def second_review_disagreements(
    primaries: Sequence[Judgement], second_reviews: Sequence[Judgement]
) -> frozenset[Pair]:
    """review-2: the sampled pairs whose second-review label differs from the primary label.
    Each one needs a joint consensus record before the labels can be frozen."""
    primary = _by_pair(primaries, Round.PRIMARY)
    reviewed = _by_pair(second_reviews, Round.SECOND_REVIEW)
    missing = sorted(set(reviewed) - set(primary))
    if missing:
        raise ContractError(f"second-review judgements without a primary judgement: {missing!r}")
    return frozenset(pair for pair, j in reviewed.items() if j.label is not primary[pair].label)


def build_joint_resolution_queue(
    primaries: Sequence[Judgement], second_reviews: Sequence[Judgement]
) -> list[JointResolutionItem]:
    """review-2: every sampled pair that both humans must resolve jointly before the freeze:
    a label disagreement, or a needs-adjudication or catalog-issue flag set by the second
    reviewer, also when the labels agree. Both flags are preserved as separate reasons."""
    primary = _by_pair(primaries, Round.PRIMARY)
    reviewed = _by_pair(second_reviews, Round.SECOND_REVIEW)
    missing = sorted(set(reviewed) - set(primary))
    if missing:
        raise ContractError(f"second-review judgements without a primary judgement: {missing!r}")
    queue = []
    for pair, j in sorted(reviewed.items()):
        reasons: set[JointReason] = set()
        if j.label is not primary[pair].label:
            reasons.add(JointReason.SECOND_REVIEW_DISAGREEMENT)
        if j.needs_adjudication:
            reasons.add(JointReason.SECOND_REVIEW_NEEDS_ADJUDICATION)
        if j.catalog_issue:
            reasons.add(JointReason.SECOND_REVIEW_CATALOG_ISSUE)
        if reasons:
            queue.append(
                JointResolutionItem(
                    query_id=pair[0], product_id=pair[1], reasons=tuple(sorted(reasons))
                )
            )
    return queue


# review-2 scope revision (approved 2026-10-16). The first review-2 text required every
# targeted-queue item to be resolved before the freeze; the revision keeps that requirement
# only for items carrying a reason below that is not automatic selection. An item is optional
# only when every one of its reasons is in REVIEW_2_OPTIONAL_REASONS, so any other (or future)
# reason keeps it mandatory. Joint-resolution pairs are always resolved jointly.
REVIEW_2_OPTIONAL_REASONS: frozenset[ReviewReason] = frozenset(
    {ReviewReason.BORDERLINE, ReviewReason.AMBIGUOUS_QUERY}
)
REVIEW_2_MANDATORY_REASONS: frozenset[ReviewReason] = frozenset(
    {
        ReviewReason.NEEDS_ADJUDICATION,  # ann-1's explicit flag
        ReviewReason.CATALOG_ISSUE,  # ann-1's explicit flag
        ReviewReason.LOW_CONFIDENCE,  # ann-1's own confidence (MASTER_PLAN 6.3)
        ReviewReason.HARD_CONSTRAINT_UNKNOWN,  # missing hard-constraint evidence
    }
)


def is_review_2_mandatory(item: QueueItem) -> bool:
    return not set(item.review_reasons) <= REVIEW_2_OPTIONAL_REASONS


def split_review_2_targeted(
    queue: Sequence[QueueItem], joint_queue: Sequence[JointResolutionItem]
) -> tuple[list[QueueItem], list[QueueItem]]:
    """review-2: the targeted-queue items not routed for joint resolution, as (mandatory,
    optional), both in queue order. A routed pair is in neither: its joint record settles it."""
    routed = {(i.query_id, i.product_id) for i in joint_queue}
    mandatory: list[QueueItem] = []
    optional: list[QueueItem] = []
    for item in queue:
        if (item.query_id, item.product_id) in routed:
            continue
        (mandatory if is_review_2_mandatory(item) else optional).append(item)
    return mandatory, optional


def derive_final_judgements_review_2(
    primaries: Sequence[Judgement],
    adjudications: Sequence[Judgement],
    consensus: Sequence[ConsensusAdjudication],
) -> list[FinalJudgement]:
    """review-2 final labels: the joint resolution's label for a routed pair (disagreement or
    second-reviewer flag), else the single-annotator adjudication of a targeted-queue item, else
    the primary label (`decided_in: primary`; this includes an optional review candidate that
    was not adjudicated, which is never reported as resolved or re-reviewed)."""
    primary = _by_pair(primaries, Round.PRIMARY)
    decided = _by_pair(adjudications, Round.ADJUDICATION)
    agreed: dict[Pair, ConsensusAdjudication] = {}
    for record in consensus:
        pair = (record.query_id, record.product_id)
        if pair in agreed:
            raise ContractError(f"duplicate consensus record for {pair!r}")
        agreed[pair] = record
    unknown = sorted((set(decided) | set(agreed)) - set(primary))
    if unknown:
        raise ContractError(f"decisions without a primary judgement: {unknown!r}")
    both = sorted(set(decided) & set(agreed))
    if both:
        raise ContractError(f"pairs decided both by one annotator and by consensus: {both!r}")
    finals = []
    for pair in sorted(primary):
        if pair in agreed:
            record = agreed[pair]
            label, relation, decided_in, rubric = (
                record.label,
                record.relation,
                "consensus",
                record.rubric_version,
            )
        else:
            source = decided.get(pair, primary[pair])
            label, relation, rubric = source.label, source.relation, source.rubric_version
            decided_in = "adjudication" if pair in decided else "primary"
        finals.append(
            FinalJudgement(
                query_id=pair[0],
                product_id=pair[1],
                label=label,
                relation=relation,
                decided_in=decided_in,  # type: ignore[arg-type]
                rubric_version=rubric,
            )
        )
    return finals
