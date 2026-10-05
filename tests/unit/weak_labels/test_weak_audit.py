"""The non-blind weak-label audit CLI on a SYNTHETIC temporary generation. Every decision typed
here is a SYNTHETIC TEST INPUT, never a human audit decision; the real audit directory, the
strict annotation directory and the real weak-set directory are never touched."""

from datetime import timedelta

import pytest
from evals.contracts.canonical import ContractError, canonical_json, decode_jsonl
from evals.contracts.constants import Label, Relation
from evals.contracts.models import Confidence, OverallStatus
from evals.weak_labels.audit import (
    WARNING,
    AuditError,
    AuditStore,
    audit,
    numbered_relations,
    parse_override,
)
from evals.weak_labels.contracts import AuditDecision
from evals.weak_labels.generation import PROPOSALS_FILE
from weak_support import AUDIT_T0, Clock, audit_context, generate, weak_fixture

# One monotonic SYNTHETIC clock across every session of this module; LATER is after all of it.
CLOCK = Clock()
LATER = AUDIT_T0 + timedelta(days=365)


@pytest.fixture(scope="module")
def fx(tmp_path_factory, catalog):
    fixture = weak_fixture(tmp_path_factory.mktemp("weakaudit"), catalog)
    generate(fixture)
    return fixture


@pytest.fixture(scope="module")
def context(fx):
    return audit_context(fx)


@pytest.fixture
def store(context, tmp_path):
    return AuditStore(context, tmp_path / "audit")


class Script:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.said.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.said)


def run(context, store, answers, clock=None, annotator="ann-1"):
    script = Script(answers)
    saved = audit(
        context, store, annotator=annotator, ask=script.ask, say=script.say, now=clock or CLOCK
    )
    return saved, script


def override_for(context, item, *, avoid_relevant=True) -> str:
    """A SYNTHETIC override code that differs from the item's proposal."""
    proposal = context.proposals[(item.query_id, item.product_id)]
    if proposal.proposed_label is Label.NOT_RELEVANT:
        return "b2h"  # borderline / substitute
    return "n2m"  # not_relevant / off_target


def test_accept_and_override_are_recorded_separately_from_the_proposal(context, store, fx):
    proposals_before = (fx.weak_dir / PROPOSALS_FILE).read_bytes()
    first, second = context.queue[0], context.queue[1]
    saved, script = run(context, store, ["a", override_for(context, second), "q"])
    assert saved == 2
    decisions = {(d.query_id, d.product_id): d for d in store.load(now=LATER)}
    accepted = decisions[(first.query_id, first.product_id)]
    proposal = context.proposals[(first.query_id, first.product_id)]
    assert accepted.decision == "human_accepted_proposal"
    assert (accepted.label, accepted.relation) == (
        proposal.proposed_label,
        proposal.proposed_relation,
    )
    assert accepted.annotator_handle == "ann-1" and accepted.human_confidence is None
    overridden = decisions[(second.query_id, second.product_id)]
    assert overridden.decision == "human_override"
    assert overridden.proposal_sha256 == second.proposal_sha256
    assert (fx.weak_dir / PROPOSALS_FILE).read_bytes() == proposals_before
    assert WARNING in script.text and "machine proposal (weak-label-policy-1)" in script.text


def test_audit_view_never_shows_private_signals(context, store):
    _, script = run(context, store, ["q"])
    for hidden in ("logit", "rank", "score", "source", "provenance", "consensus", "band"):
        assert hidden not in script.text.lower().replace("weak-label-policy-1", "")


def test_no_default_and_no_automatic_acceptance(context, store):
    saved, script = run(context, store, ["", "   ", "x", "accept-all", "aa", "q"])
    assert saved == 0 and store.load(now=LATER) == []
    saved, _ = run(context, store, [])  # immediate EOF
    assert saved == 0 and store.load(now=LATER) == []


def test_eof_quits_cleanly_and_resume_continues(context, store):
    saved, script = run(context, store, ["a"])  # then EOF
    assert saved == 1 and "Input ended" in script.text
    assert not store.lock_path.exists()
    saved, script = run(context, store, ["a", "q"])
    decided = [(d.query_id, d.product_id) for d in store.load(now=LATER)]
    assert saved == 1 and len(decided) == 2
    assert set(decided) == {(q.query_id, q.product_id) for q in context.queue[:2]}
    assert "2 of 100" in script.text or "1 of 100" in script.text


def test_undo_removes_the_latest_decision_and_shows_it_again(context, store):
    first, second = context.queue[0], context.queue[1]
    saved, script = run(context, store, ["a", "a", "u", "q"])
    decisions = store.load(now=LATER)
    assert [(d.query_id, d.product_id) for d in decisions] == [(first.query_id, first.product_id)]
    assert f"undone: {second.query_id}/{second.product_id}" in script.text
    # Undo across sessions works too, and an empty store has nothing to undo.
    run(context, store, ["u", "u", "q"])
    assert store.load(now=LATER) == []
    _, script = run(context, store, ["u", "q"])
    assert "nothing to undo" in script.text


def test_override_equal_to_proposal_and_relevant_on_violation_are_refused(context, store):
    item = context.queue[0]
    proposal = context.proposals[(item.query_id, item.product_id)]
    numbers = {r: n for n, r in numbered_relations(proposal.proposed_label).items()}
    same = f"{proposal.proposed_label.value[0]}{numbers[proposal.proposed_relation]}h"
    _, script = run(context, store, [same, "q"])
    assert "type a to accept" in script.text and store.load(now=LATER) == []
    violated = next(
        q
        for q in context.queue
        if context.facts.eligibility[(q.query_id, q.product_id)].hard_overall
        is OverallStatus.VIOLATED
    )
    before = [q for q in context.queue if q.audit_position < violated.audit_position]
    answers = [override_for(context, q) for q in before] + ["r1h", "q"]
    _, script = run(context, store, answers)
    assert "relevant is not allowed" in script.text
    assert (violated.query_id, violated.product_id) not in {
        (d.query_id, d.product_id) for d in store.load(now=LATER)
    }


def test_override_codes():
    assert parse_override("r1h") == (Label.RELEVANT, Relation.EXACT_MATCH, "high")
    assert parse_override("n3l")[1] is Relation.OUT_OF_CATALOG
    for bad in ("r3h", "x1h", "r1", "r1hh", "a", "", "R1H "):
        assert parse_override(bad) is None


def test_only_ann1_audits(context, store):
    with pytest.raises(AuditError, match="only 'ann-1'"):
        run(context, store, ["a"], annotator="weak-label-policy-1")
    with pytest.raises(AuditError):
        run(context, store, ["a"], annotator="ann-2")


def test_exclusive_lock(context, store):
    store.directory.mkdir(parents=True, exist_ok=True)
    store.lock_path.write_text("{}", encoding="utf-8")
    with pytest.raises(AuditError, match="another audit session"):
        run(context, store, ["a"])
    assert store.lock_path.exists()  # never removed automatically


def _stored(store, context):
    run(context, store, ["a", "a", "q"])
    return decode_jsonl(store.path.read_bytes(), AuditDecision)


def test_tampered_decision_files_fail_closed(context, store):
    decisions = _stored(store, context)
    first = decisions[0]
    cases = {
        "duplicate": [first, first.model_copy(update={"sequence": 99})],
        "not a pair of the audit queue": [first.model_copy(update={"product_id": "SYN-XXX-9999"})],
        "proposal changed": [first.model_copy(update={"proposal_sha256": "0" * 64})],
        "card changed": [first.model_copy(update={"card_sha256": "0" * 64})],
        "does not match the proposal": [
            first.model_copy(
                update={"decision": "human_override", "human_confidence": Confidence.HIGH}
            )
        ],
    }
    for message, records in cases.items():
        lines = [canonical_json(r) for r in records]
        store.path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))
        with pytest.raises(ContractError, match=message):
            store.load(now=LATER)


def test_binding_to_other_proposals_fails_closed(context, store):
    run(context, store, ["q"])
    raw = store.binding_path.read_bytes()
    store.binding_path.write_bytes(
        raw.replace(context.generation.manifest_sha256.encode(), b"0" * 64)
    )
    with pytest.raises(AuditError, match="bound to other proposals"):
        store.load(now=LATER)


def test_changed_card_in_catalog_fails_closed(context, store):
    from dataclasses import replace
    from types import MappingProxyType

    item = context.queue[0]
    products = dict(context.facts.catalog.products)
    products[item.product_id] = replace(products[item.product_id], title="Changed title")
    catalog = replace(context.facts.catalog, products=MappingProxyType(products))
    changed = replace(context, facts=replace(context.facts, catalog=catalog))
    with pytest.raises(AuditError, match="card changed"):
        run(changed, AuditStore(changed, store.directory.parent), ["a"])
