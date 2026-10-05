"""`weak-audit-1`: the deterministic 100-pair human audit queue. Pure.

Exactly five pairs per query for each of the 20 pilot queries, chosen from the machine
proposals only (never from any human label). Per query, the slots are filled in order:

1. a proposed `relevant` pair;
2. a proposed `borderline` pair;
3. a proposed `not_relevant` pair;
4. the lowest-confidence pair, conflicts first;
5. a SHA-256-selected remaining pair.

Inside a slot, ties are broken by `sha256("weak-audit-1|<slot>|<query_id>|<product_id>")`. A
slot whose target is unavailable (no unselected pair with that proposed label) is filled from
the unselected pairs in `sha256("weak-audit-1|fill|<query_id>|<product_id>")` order and recorded
as `sha256_fill`. No pair is selected twice. Queue positions follow query id, then slot.
"""

import hashlib
from collections.abc import Sequence

from evals.contracts.canonical import ContractError, canonical_json, sha256_hex
from evals.contracts.constants import Label
from evals.contracts.models import IntentFamily, LanguageStyle, QueryRecord
from evals.weak_labels.contracts import (
    AUDIT_PER_QUERY,
    AUDIT_POLICY_VERSION,
    AUDIT_SIZE,
    CONFIDENCE_ORDER,
    AuditQueueItem,
    AuditSlot,
    WeakLabelProposal,
)

SLOT_LABELS: dict[AuditSlot, Label] = {
    AuditSlot.PROPOSED_RELEVANT: Label.RELEVANT,
    AuditSlot.PROPOSED_BORDERLINE: Label.BORDERLINE,
    AuditSlot.PROPOSED_NOT_RELEVANT: Label.NOT_RELEVANT,
}
SLOT_ORDER: tuple[AuditSlot, ...] = (
    AuditSlot.PROPOSED_RELEVANT,
    AuditSlot.PROPOSED_BORDERLINE,
    AuditSlot.PROPOSED_NOT_RELEVANT,
    AuditSlot.LOWEST_CONFIDENCE_OR_CONFLICT,
    AuditSlot.SHA256_SELECTED,
)
# Query kinds the queue must cover (every pilot query is audited, so this is a guard).
REQUIRED_FAMILIES = frozenset(
    {
        IntentFamily.SEMANTIC_INTENT,
        IntentFamily.EXACT_PRODUCT,
        IntentFamily.AMBIGUOUS,
        IntentFamily.ZERO_ELIGIBLE,
        IntentFamily.OUT_OF_CATALOG,
    }
)
REQUIRED_STYLES = frozenset({LanguageStyle.HINGLISH, LanguageStyle.NOISY})


def proposal_sha256(proposal: WeakLabelProposal) -> str:
    return sha256_hex(canonical_json(proposal).encode("utf-8"))


def _order(slot: str, proposal: WeakLabelProposal) -> str:
    text = f"{AUDIT_POLICY_VERSION}|{slot}|{proposal.query_id}|{proposal.product_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pick(slot: AuditSlot, available: list[WeakLabelProposal]) -> WeakLabelProposal | None:
    if slot in SLOT_LABELS:
        wanted = [p for p in available if p.proposed_label is SLOT_LABELS[slot]]
        return min(wanted, key=lambda p: _order(slot.value, p), default=None)
    if slot is AuditSlot.LOWEST_CONFIDENCE_OR_CONFLICT:
        return min(
            available,
            key=lambda p: (
                CONFIDENCE_ORDER.index(p.proposal_confidence),
                not p.signal_conflict,
                _order(slot.value, p),
            ),
            default=None,
        )
    return min(available, key=lambda p: _order(slot.value, p), default=None)


def build_queue(
    proposals: Sequence[WeakLabelProposal], queries: Sequence[QueryRecord]
) -> list[AuditQueueItem]:
    by_query: dict[str, list[WeakLabelProposal]] = {}
    for p in proposals:
        by_query.setdefault(p.query_id, []).append(p)
    query_ids = sorted(q.query_id for q in queries)
    if sorted(by_query) != query_ids or len(query_ids) * AUDIT_PER_QUERY != AUDIT_SIZE:
        raise ContractError(f"the audit needs exactly {AUDIT_SIZE // AUDIT_PER_QUERY} queries")
    queue: list[AuditQueueItem] = []
    for query_id in query_ids:
        available = sorted(by_query[query_id], key=lambda p: p.product_id)
        if len(available) < AUDIT_PER_QUERY:
            raise ContractError(f"{query_id}: fewer than {AUDIT_PER_QUERY} pooled pairs")
        for index, slot in enumerate(SLOT_ORDER, start=1):
            chosen, recorded = _pick(slot, available), slot
            if chosen is None:
                chosen = _pick(AuditSlot.SHA256_FILL, available)
                recorded = AuditSlot.SHA256_FILL
            assert chosen is not None  # noqa: S101 - at least five pairs per query
            available.remove(chosen)
            queue.append(
                AuditQueueItem(
                    audit_position=len(queue) + 1,
                    query_id=query_id,
                    product_id=chosen.product_id,
                    slot_index=index,
                    slot=recorded,
                    proposal_sha256=proposal_sha256(chosen),
                )
            )
    validate_queue(queue, proposals, queries)
    return queue


def validate_queue(
    queue: Sequence[AuditQueueItem],
    proposals: Sequence[WeakLabelProposal],
    queries: Sequence[QueryRecord],
) -> None:
    """Exactly 100 unique pooled pairs, five per query, bound to the current proposals, and
    covering every required query kind."""
    if len(queue) != AUDIT_SIZE:
        raise ContractError(f"the audit queue must hold exactly {AUDIT_SIZE} pairs")
    if [q.audit_position for q in queue] != list(range(1, AUDIT_SIZE + 1)):
        raise ContractError("audit positions must be 1..100 in order")
    by_pair = {(p.query_id, p.product_id): p for p in proposals}
    pairs = [(q.query_id, q.product_id) for q in queue]
    if len(set(pairs)) != len(pairs):
        raise ContractError("the audit queue repeats a pair")
    counts: dict[str, int] = {}
    for item in queue:
        proposal = by_pair.get((item.query_id, item.product_id))
        if proposal is None:
            raise ContractError(f"{item.query_id}/{item.product_id}: not a proposed pair")
        if item.proposal_sha256 != proposal_sha256(proposal):
            raise ContractError(f"{item.query_id}/{item.product_id}: proposal hash differs")
        counts[item.query_id] = counts.get(item.query_id, 0) + 1
    if sorted(counts) != sorted(q.query_id for q in queries) or set(counts.values()) != {
        AUDIT_PER_QUERY
    }:
        raise ContractError(f"the audit queue must hold exactly {AUDIT_PER_QUERY} pairs per query")
    covered = [q for q in queries if q.query_id in counts]
    families = {q.intent_family for q in covered}
    styles = {q.language_style for q in covered}
    if not (families >= REQUIRED_FAMILIES and styles >= REQUIRED_STYLES):
        raise ContractError("the audit queue does not cover every required query kind")
