"""Contract rules of the weak-label records. Every record is a SYNTHETIC TEST FIXTURE."""

from datetime import UTC, datetime

import pytest
from evals.contracts.constants import Label, Relation
from evals.contracts.models import Confidence, OverallStatus
from evals.weak_labels.contracts import (
    BANNER,
    AuditDecision,
    Band,
    Consensus,
    LabelSource,
    NamedHash,
    ProposalRule,
    WeakDevSealed,
    WeakFinalLabel,
    WeakLabelProposal,
)
from pydantic import ValidationError

H = "a" * 64
T = datetime(2026, 10, 6, 9, 0, 0, tzinfo=UTC)


def proposal(**overrides) -> WeakLabelProposal:
    fields = dict(
        query_id="gq-p01",
        product_id="SYN-HDP-0001",
        proposed_label=Label.RELEVANT,
        proposed_relation=Relation.SATISFIES_INTENT,
        proposal_confidence=Confidence.HIGH,
        proposal_rule=ProposalRule.CATEGORY_ONLY_MATCH,
        hard_constraint_status=OverallStatus.SATISFIED,
        reranker_band=Band.HIGH,
        retrieval_consensus=Consensus.STRONG,
        signal_conflict=False,
        requires_human_review=False,
        reason="Fixture reason.",
        pool_manifest_sha256=H,
        product_content_sha256=H,
        card_sha256=H,
        catalog_checksum_sha256=H,
        reranker_model_id="fixture/model",
        reranker_revision="0" * 40,
        rerank_document_version="rd-1",
        rubric_version="rubric-1",
    )
    fields.update(overrides)
    return WeakLabelProposal(**fields)


def test_proposal_identity_is_the_machine_policy_never_a_human_handle():
    assert proposal().proposal_source == "weak-label-policy-1"
    with pytest.raises(ValidationError):
        proposal(proposal_source="ann-1")
    with pytest.raises(ValidationError):
        proposal(policy_version="ann-1")


def test_proposal_rules():
    with pytest.raises(ValidationError, match="never proposed as relevant"):
        proposal(hard_constraint_status=OverallStatus.VIOLATED)
    with pytest.raises(ValidationError, match="requires_human_review"):
        proposal(proposal_confidence=Confidence.MEDIUM)
    proposal(proposal_confidence=Confidence.MEDIUM, requires_human_review=True)
    with pytest.raises(ValidationError):
        proposal(signal_conflict=True, requires_human_review=True)
    with pytest.raises(ValidationError, match="not allowed"):
        proposal(proposed_relation=Relation.COMPLEMENT)
    # An unknown hard status is not a violation: relevant stays allowed.
    proposal(
        hard_constraint_status=OverallStatus.UNKNOWN,
        proposal_confidence=Confidence.LOW,
        requires_human_review=True,
    )


def decision(**overrides) -> AuditDecision:
    fields = dict(
        query_id="gq-p01",
        product_id="SYN-HDP-0001",
        sequence=1,
        decision="human_accepted_proposal",
        label=Label.RELEVANT,
        relation=Relation.SATISFIES_INTENT,
        proposal_sha256=H,
        card_sha256=H,
        decided_at=T,
        rubric_version="rubric-1",
    )
    fields.update(overrides)
    return AuditDecision(**fields)


def test_audit_decisions_are_ann1_only_and_typed():
    assert decision().annotator_handle == "ann-1"
    with pytest.raises(ValidationError):
        decision(annotator_handle="weak-label-policy-1")
    with pytest.raises(ValidationError):
        decision(annotator_handle="ann-2")
    with pytest.raises(ValidationError, match="confidence"):
        decision(decision="human_override")
    decision(decision="human_override", human_confidence=Confidence.LOW)
    with pytest.raises(ValidationError, match="no separate confidence"):
        decision(human_confidence=Confidence.HIGH)
    with pytest.raises(ValidationError):
        decision(decision="machine_unreviewed")


def final(**overrides) -> WeakFinalLabel:
    fields = dict(
        query_id="gq-p01",
        product_id="SYN-HDP-0001",
        label=Label.RELEVANT,
        relation=Relation.SATISFIES_INTENT,
        label_source=LabelSource.MACHINE_UNREVIEWED,
        proposed_label=Label.RELEVANT,
        proposed_relation=Relation.SATISFIES_INTENT,
        proposal_confidence=Confidence.HIGH,
        proposal_sha256=H,
        rubric_version="rubric-1",
    )
    fields.update(overrides)
    return WeakFinalLabel(**fields)


def test_final_label_source_attribution():
    final()
    with pytest.raises(ValidationError, match="unreviewed"):
        final(decided_by="ann-1")
    with pytest.raises(ValidationError, match="unreviewed"):
        final(label=Label.BORDERLINE, relation=Relation.SUBSTITUTE)
    with pytest.raises(ValidationError, match="ann-1"):
        final(label_source=LabelSource.HUMAN_ACCEPTED_PROPOSAL)
    final(label_source=LabelSource.HUMAN_ACCEPTED_PROPOSAL, decided_by="ann-1")
    with pytest.raises(ValidationError, match="override changes"):
        final(label_source=LabelSource.HUMAN_OVERRIDE, decided_by="ann-1")
    final(
        label_source=LabelSource.HUMAN_OVERRIDE,
        decided_by="ann-1",
        label=Label.NOT_RELEVANT,
        relation=Relation.OFF_TARGET,
    )


def test_weak_dev_sealed_discloses_and_never_claims_golden_status():
    sealed = WeakDevSealed(
        proposal_manifest_sha256=H,
        pool_manifest_sha256=H,
        files=(NamedHash(path="x", sha256=H),),
        label_count=1,
        audited_count=0,
        accepted_count=0,
        override_count=0,
        machine_unreviewed_count=1,
        sealed_at=T,
    )
    assert sealed.status == "weak_dev_sealed"
    assert sealed.banner == BANNER
    assert BANNER.startswith("PROVISIONAL — AI-assisted weak labels with a 100-pair non-blind")
    assert not sealed.human_reviewed_golden_dataset and not sealed.final_test_evidence
    assert not sealed.blind_audit and not sealed.consistency_measured
    for field, value in (
        ("status", "primary_sealed"),
        ("human_reviewed_golden_dataset", True),
        ("final_test_evidence", True),
        ("banner", "final"),
    ):
        with pytest.raises(ValidationError):
            WeakDevSealed.model_validate({**sealed.model_dump(mode="json"), field: value})
