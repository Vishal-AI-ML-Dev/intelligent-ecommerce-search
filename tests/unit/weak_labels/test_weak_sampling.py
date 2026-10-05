"""`weak-audit-1` queue selection on SYNTHETIC proposals (fixture pool, fake logits)."""

from collections import Counter

import pytest
from evals.contracts.canonical import ContractError, decode_jsonl
from evals.contracts.constants import Label
from evals.weak_labels.contracts import AuditSlot, WeakLabelProposal
from evals.weak_labels.generation import PROPOSALS_FILE, QUEUE_FILE, build_generation
from evals.weak_labels.sampling import build_queue, proposal_sha256, validate_queue
from weak_support import PIN, weak_fixture


@pytest.fixture(scope="module")
def setup(tmp_path_factory, catalog):
    fx = weak_fixture(tmp_path_factory.mktemp("weaksample"), catalog)
    files = build_generation(fx.facts, fx.logits, PIN)
    return fx, decode_jsonl(files[PROPOSALS_FILE], WeakLabelProposal), files[QUEUE_FILE]


def test_exactly_100_pairs_five_per_query_no_duplicates(setup):
    fx, proposals, _ = setup
    queue = build_queue(proposals, fx.facts.queries)
    assert len(queue) == 100
    assert set(Counter(q.query_id for q in queue).values()) == {5}
    assert len({(q.query_id, q.product_id) for q in queue}) == 100
    assert [q.audit_position for q in queue] == list(range(1, 101))


def test_queue_is_deterministic_and_independent_of_input_order(setup):
    fx, proposals, stored = setup
    first = build_queue(proposals, fx.facts.queries)
    second = build_queue(list(reversed(proposals)), list(reversed(fx.facts.queries)))
    assert first == second
    from evals.contracts.canonical import encode_jsonl

    assert encode_jsonl(first) == stored


def test_slots_follow_the_proposals(setup):
    fx, proposals, _ = setup
    by_pair = {(p.query_id, p.product_id): p for p in proposals}
    for item in build_queue(proposals, fx.facts.queries):
        proposal = by_pair[(item.query_id, item.product_id)]
        assert item.proposal_sha256 == proposal_sha256(proposal)
        expected = {
            AuditSlot.PROPOSED_RELEVANT: Label.RELEVANT,
            AuditSlot.PROPOSED_BORDERLINE: Label.BORDERLINE,
            AuditSlot.PROPOSED_NOT_RELEVANT: Label.NOT_RELEVANT,
        }.get(item.slot)
        if expected is not None:
            assert proposal.proposed_label is expected


def test_unavailable_slot_is_filled_by_sha256_order(setup):
    fx, proposals, _ = setup
    query_id = sorted({p.query_id for p in proposals})[0]
    without_borderline = [
        p.model_copy(
            update={"proposed_label": Label.NOT_RELEVANT, "proposed_relation": "off_target"}
        )
        if p.query_id == query_id and p.proposed_label is Label.BORDERLINE
        else p
        for p in proposals
    ]
    without_borderline = [
        WeakLabelProposal.model_validate(p.model_dump()) for p in without_borderline
    ]
    queue = build_queue(without_borderline, fx.facts.queries)
    slots = [q.slot for q in queue if q.query_id == query_id]
    assert AuditSlot.PROPOSED_BORDERLINE not in slots
    assert AuditSlot.SHA256_FILL in slots and len(slots) == 5


def test_tampered_queue_is_refused(setup):
    fx, proposals, _ = setup
    queue = build_queue(proposals, fx.facts.queries)
    with pytest.raises(ContractError):
        validate_queue(queue[:-1], proposals, fx.facts.queries)
    duplicate = [*queue[:-1], queue[0].model_copy(update={"audit_position": 100})]
    with pytest.raises(ContractError, match="repeats"):
        validate_queue(duplicate, proposals, fx.facts.queries)
    changed = [queue[0].model_copy(update={"proposal_sha256": "0" * 64}), *queue[1:]]
    with pytest.raises(ContractError, match="hash differs"):
        validate_queue(changed, proposals, fx.facts.queries)


def test_real_queries_cover_every_required_query_kind(setup):
    fx, proposals, _ = setup
    queue = build_queue(proposals, fx.facts.queries)
    audited = {q.query_id for q in queue}
    kinds = {
        (q.intent_family.value, q.language_style.value)
        for q in fx.facts.queries
        if q.query_id in audited
    }
    families = {k[0] for k in kinds}
    styles = {k[1] for k in kinds}
    assert {
        "semantic_intent",
        "exact_product",
        "ambiguous",
        "zero_eligible",
        "out_of_catalog",
    } <= families
    assert {"hinglish", "noisy"} <= styles
