"""Typed contracts of the AI-assisted weak-label development set `weak_dev_v1`.

Identities are fixed and separate:

* every machine proposal is authored by `weak-label-policy-1` (never `ann-1`);
* `ann-1` appears only on an explicit human audit decision (accept or override);
* a final weak label records its source: `machine_unreviewed`, `human_accepted_proposal` or
  `human_override`.

Nothing here is a strict `Judgement`, `FinalJudgement`, consistency sample or Golden manifest,
and nothing is written into the strict Golden paths.

Public vs private: a `WeakLabelProposal` (public, committed) carries only coarse signal bands
and a short reason. The cross-encoder logits and the per-system retrieval ranks are kept in the
private `WeakSignal` file (git-ignored), bound by its SHA-256 in the public proposal manifest.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from evals.contracts.constants import ALLOWED_RELATIONS, Label, Relation
from evals.contracts.models import (
    Confidence,
    FreeText,
    InclusionReason,
    OverallStatus,
    ProductId,
    QueryId,
    RubricVersion,
    Sha256,
    SourceHit,
    UtcTimestamp,
    _Frozen,
    _Record,
)

WEAK_SET_ID = "weak_dev_v1"
PROPOSER_HANDLE = "weak-label-policy-1"
POLICY_VERSION = "weak-label-policy-1"
AUDIT_POLICY_VERSION = "weak-audit-1"
SIGNALS_VERSION = "weak-signals-1"
AUDIT_SIZE = 100
AUDIT_PER_QUERY = 5
HUMAN_AUDITOR = "ann-1"
# Recomputed cross-encoder logits must reproduce every V3 score in the verified provenance.
RERANKER_TOLERANCE = 1e-4

BANNER = (
    "PROVISIONAL — AI-assisted weak labels with a 100-pair non-blind human audit. "
    "Not final human-reviewed test evidence."
)
DISCLOSURES: tuple[str, ...] = (
    "weak_dev_v1 is a human-audited provisional development set built from AI/model-assisted "
    "label proposals (deterministic rules plus local cross-encoder and retrieval signals).",
    "It is not a human-reviewed Golden Dataset and not final held-out test evidence.",
    "The audit is non-blind: the human auditor sees each machine proposal before deciding.",
    "Only 100 of 568 pairs are human-audited; every other label is an unreviewed machine "
    "proposal and no accuracy is claimed for it.",
    "There is no delayed intra-annotator consistency measurement and no inter-annotator agreement.",
    "All metrics computed on it are provisional.",
)


class LabelSource(StrEnum):
    MACHINE_UNREVIEWED = "machine_unreviewed"
    HUMAN_ACCEPTED_PROPOSAL = "human_accepted_proposal"
    HUMAN_OVERRIDE = "human_override"


class Band(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Consensus(StrEnum):
    STRONG = "strong"  # retrieved within depth by at least 4 of the 6 pooled systems
    PARTIAL = "partial"  # by 1 to 3 systems
    NONE = "none"  # by no system (eligibility completion or a negative)


class ProposalRule(StrEnum):
    OUT_OF_CATALOG_QUERY = "out_of_catalog_query"
    CATEGORY_MISMATCH = "category_mismatch"
    COMPLEMENT_CATEGORY = "complement_category"
    MULTIPLE_HARD_VIOLATIONS = "multiple_hard_violations"
    BRAND_ONLY_VIOLATION = "brand_only_violation"
    NEAR_MISS_SUBSTITUTE = "near_miss_substitute"
    SERIES_PARTIAL = "series_partial"
    SINGLE_HARD_VIOLATION = "single_hard_violation"
    HARD_UNKNOWN = "hard_unknown"
    AMBIGUOUS_INTERPRETATION_MATCH = "ambiguous_interpretation_match"
    AMBIGUOUS_OTHER_CATEGORY = "ambiguous_other_category"
    SEMANTIC_SUPPORTED = "semantic_supported"
    SEMANTIC_PARTIAL = "semantic_partial"
    SEMANTIC_UNSUPPORTED = "semantic_unsupported"
    PRICE_PREFERENCE_MET = "price_preference_met"
    PRICE_PREFERENCE_UNMET = "price_preference_unmet"
    SERIES_EXACT_MATCH = "series_exact_match"
    SERIES_ABSENT = "series_absent"
    CATEGORY_ONLY_MATCH = "category_only_match"
    DIRECT_MATCH = "direct_match"
    NO_HARD_CONSTRAINTS = "no_hard_constraints"


CONFIDENCE_ORDER: tuple[Confidence, ...] = (Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)


def _check_relation(label: Label, relation: Relation) -> None:
    if relation not in ALLOWED_RELATIONS[label]:
        raise ValueError(f"relation {relation.value!r} is not allowed for {label.value!r}")


# ---- private signals ---------------------------------------------------------------------------


class WeakSignal(_Record):
    """Private local evidence for one pooled pair (never shown to the auditor): the pinned
    cross-encoder logit (`repr(float)`), its rank inside the query's pool, and the retrieval
    hits and inclusion reason from the verified private pool provenance."""

    signals_version: Literal["weak-signals-1"] = SIGNALS_VERSION
    query_id: QueryId
    product_id: ProductId
    reranker_logit: Annotated[StrictStr, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?(?:e[-+]?[0-9]+)?$")]
    reranker_rank_in_pool: Annotated[StrictInt, Field(ge=1)]
    pool_size: Annotated[StrictInt, Field(ge=1)]
    added_by: InclusionReason
    source_hits: tuple[SourceHit, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.reranker_rank_in_pool > self.pool_size:
            raise ValueError("reranker_rank_in_pool exceeds the pool size")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


# ---- public proposals --------------------------------------------------------------------------


class WeakLabelProposal(_Record):
    """One machine label proposal. Authored by `weak-label-policy-1`, never by a human handle.
    It is a proposal, not a judgement: it may be wrong."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    query_id: QueryId
    product_id: ProductId
    proposed_label: Label
    proposed_relation: Relation
    proposal_confidence: Confidence
    proposal_rule: ProposalRule
    proposal_source: Literal["weak-label-policy-1"] = PROPOSER_HANDLE
    policy_version: Literal["weak-label-policy-1"] = POLICY_VERSION
    hard_constraint_status: OverallStatus
    violated_fields: tuple[StrictStr, ...] = ()
    unknown_fields: tuple[StrictStr, ...] = ()
    reranker_band: Band
    retrieval_consensus: Consensus
    signal_conflict: StrictBool
    requires_human_review: StrictBool
    reason: FreeText
    pool_manifest_sha256: Sha256
    product_content_sha256: Sha256
    card_sha256: Sha256
    catalog_checksum_sha256: Sha256
    reranker_model_id: StrictStr
    reranker_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    rerank_document_version: StrictStr
    rubric_version: RubricVersion

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.proposed_label, self.proposed_relation)
        if self.proposed_label is Label.RELEVANT and (
            self.hard_constraint_status is OverallStatus.VIOLATED
        ):
            raise ValueError("a hard-violating product is never proposed as relevant")
        expected_review = self.proposal_confidence is not Confidence.HIGH or self.signal_conflict
        if self.requires_human_review != expected_review:
            raise ValueError("requires_human_review must be set exactly for non-high or conflict")
        if self.signal_conflict and self.proposal_confidence is Confidence.HIGH:
            raise ValueError("a conflicting proposal is never high confidence")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class AuditSlot(StrEnum):
    PROPOSED_RELEVANT = "proposed_relevant"
    PROPOSED_BORDERLINE = "proposed_borderline"
    PROPOSED_NOT_RELEVANT = "proposed_not_relevant"
    LOWEST_CONFIDENCE_OR_CONFLICT = "lowest_confidence_or_conflict"
    SHA256_SELECTED = "sha256_selected"
    SHA256_FILL = "sha256_fill"  # a slot whose target was unavailable, filled by SHA-256 order


class AuditQueueItem(_Record):
    """One pair of the deterministic 100-pair audit queue (chosen from proposals only)."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    audit_policy_version: Literal["weak-audit-1"] = AUDIT_POLICY_VERSION
    audit_position: Annotated[StrictInt, Field(ge=1, le=AUDIT_SIZE)]
    query_id: QueryId
    product_id: ProductId
    slot_index: Annotated[StrictInt, Field(ge=1, le=AUDIT_PER_QUERY)]
    slot: AuditSlot
    proposal_sha256: Sha256

    def sort_key(self) -> tuple[str, ...]:
        return (f"{self.audit_position:03d}",)


class CountEntry(_Frozen):
    key: StrictStr
    count: Annotated[StrictInt, Field(ge=0)]


class NamedHash(_Frozen):
    path: StrictStr
    sha256: Sha256


class ProposalManifest(_Record):
    """Deterministic manifest of one proposal generation (no wall-clock time)."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    status: Literal["proposals_generated"] = "proposals_generated"
    proposal_source: Literal["weak-label-policy-1"] = PROPOSER_HANDLE
    policy_version: Literal["weak-label-policy-1"] = POLICY_VERSION
    audit_policy_version: Literal["weak-audit-1"] = AUDIT_POLICY_VERSION
    signals_version: Literal["weak-signals-1"] = SIGNALS_VERSION
    queries_manifest_sha256: Sha256
    pool_manifest_sha256: Sha256
    provenance_binding: Sha256
    signals_binding: Sha256
    catalog_checksum_sha256: Sha256
    reranker_model_id: StrictStr
    reranker_revision: StrictStr
    reranker_config_sha256: Sha256
    rerank_document_version: StrictStr
    rubric_version: RubricVersion
    files: tuple[NamedHash, ...]
    proposal_count: StrictInt
    audit_count: StrictInt
    by_label: tuple[CountEntry, ...]
    by_confidence: tuple[CountEntry, ...]
    requires_review_count: StrictInt
    disclosures: tuple[StrictStr, ...] = DISCLOSURES

    def sort_key(self) -> tuple[str, ...]:
        return (self.pool_manifest_sha256,)


class GenerationRun(_Record):
    """Non-deterministic run metadata, kept out of the deterministic bytes."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    proposal_manifest_sha256: Sha256
    generated_at: UtcTimestamp
    independent_runs: Literal[2] = 2
    runs_byte_identical: Literal[True] = True
    network_guard: Literal[True] = True
    remote_provider_used: Literal[False] = False
    max_reranker_abs_diff_vs_provenance: StrictStr
    reranker_scores_checked_vs_provenance: StrictInt

    def sort_key(self) -> tuple[str, ...]:
        return (self.proposal_manifest_sha256,)


# ---- human audit -------------------------------------------------------------------------------


class AuditDecision(_Record):
    """One explicit human audit decision. Stored separately from the proposal it audits; the
    proposal is bound by its hash and never modified."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    query_id: QueryId
    product_id: ProductId
    sequence: Annotated[StrictInt, Field(ge=1)]
    decision: Literal["human_accepted_proposal", "human_override"]
    label: Label
    relation: Relation
    human_confidence: Confidence | None = None
    proposal_sha256: Sha256
    card_sha256: Sha256
    annotator_handle: Literal["ann-1"] = HUMAN_AUDITOR
    decided_at: UtcTimestamp
    audit_policy_version: Literal["weak-audit-1"] = AUDIT_POLICY_VERSION
    rubric_version: RubricVersion

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.label, self.relation)
        if self.decision == "human_override" and self.human_confidence is None:
            raise ValueError("an override records the auditor's confidence")
        if self.decision == "human_accepted_proposal" and self.human_confidence is not None:
            raise ValueError("an acceptance records no separate confidence")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class AuditBinding(_Frozen):
    """Binds an audit work directory to one exact proposal generation and the one auditor."""

    schema_version: Literal[1] = 1
    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    proposal_manifest_sha256: Sha256
    pool_manifest_sha256: Sha256
    annotator_handle: Literal["ann-1"] = HUMAN_AUDITOR
    audit_policy_version: Literal["weak-audit-1"] = AUDIT_POLICY_VERSION
    created_at: UtcTimestamp


class AuditLockInfo(_Frozen):
    proposal_manifest_sha256: Sha256
    annotator_handle: Literal["ann-1"] = HUMAN_AUDITOR
    pid: int
    started_at: UtcTimestamp


# ---- assembled weak set ------------------------------------------------------------------------


class WeakFinalLabel(_Record):
    """The provisional label of one pooled pair in `weak_dev_v1`, with its source. It is never
    a strict `FinalJudgement`."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    query_id: QueryId
    product_id: ProductId
    label: Label
    relation: Relation
    label_source: LabelSource
    proposed_label: Label
    proposed_relation: Relation
    proposal_confidence: Confidence
    proposal_sha256: Sha256
    decided_by: Literal["ann-1"] | None = None
    rubric_version: RubricVersion

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.label, self.relation)
        same = (self.label, self.relation) == (self.proposed_label, self.proposed_relation)
        if self.label_source is LabelSource.MACHINE_UNREVIEWED:
            if not same or self.decided_by is not None:
                raise ValueError("an unreviewed label is the machine proposal, with no human")
        elif self.decided_by != HUMAN_AUDITOR:
            raise ValueError("a human audit outcome is decided by ann-1")
        elif (self.label_source is LabelSource.HUMAN_ACCEPTED_PROPOSAL) != same:
            raise ValueError("an acceptance keeps the proposal; an override changes it")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class WeakDevSealed(_Record):
    """The `weak_dev_sealed` status of `weak_dev_v1`. It is not `primary_sealed`,
    `labels_frozen` or `provenance_unsealed`, and the strict Golden lifecycle is untouched."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    status: Literal["weak_dev_sealed"] = "weak_dev_sealed"
    banner: Literal[
        "PROVISIONAL — AI-assisted weak labels with a 100-pair non-blind human audit. "
        "Not final human-reviewed test evidence."
    ] = BANNER
    human_reviewed_golden_dataset: Literal[False] = False
    final_test_evidence: Literal[False] = False
    blind_audit: Literal[False] = False
    consistency_measured: Literal[False] = False
    proposal_manifest_sha256: Sha256
    pool_manifest_sha256: Sha256
    files: tuple[NamedHash, ...]
    label_count: StrictInt
    audited_count: StrictInt
    accepted_count: StrictInt
    override_count: StrictInt
    machine_unreviewed_count: StrictInt
    sealed_by: Literal["ann-1"] = HUMAN_AUDITOR
    sealed_at: UtcTimestamp
    disclosures: tuple[StrictStr, ...] = DISCLOSURES

    def sort_key(self) -> tuple[str, ...]:
        return (self.proposal_manifest_sha256,)


# ---- machine-only finalization (no human audit) -------------------------------------------------

MACHINE_STATUS = "weak_machine_dev_sealed"
LABEL_BASIS = "deterministic_machine_weak_labels"
# Two human audit decisions were started, then abandoned when the machine-only track was chosen.
# They are archived (git-ignored) and never imported into labels, metrics or statistics.
ABANDONED_HUMAN_DECISION_COUNT = 2
MACHINE_WARNING = (
    "WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit "
    "coverage: 0/568 (0%). Two started human audit decisions were abandoned, archived and "
    "excluded. These results are provisional development diagnostics only. They are not Golden "
    "Dataset results or final held-out test evidence."
)
ABANDONED_DISCLOSURE = (
    "Two human audit decisions were started and then abandoned when the user selected the "
    "machine-only track. They are archived for auditability and excluded from all labels, "
    "metrics and human-audit claims."
)
MACHINE_DISCLOSURES: tuple[str, ...] = (
    "This is a deterministic machine weak-label development set: 568/568 labels are "
    "unaudited machine-generated labels from weak-label-policy-1.",
    "Human audit coverage is 0/568. No label was reviewed, accepted or overridden by a human.",
    ABANDONED_DISCLOSURE,
    "It is not a human-reviewed Golden Dataset and not final held-out test evidence.",
    "There is no delayed intra-annotator consistency measurement and no inter-annotator agreement.",
    "Every metric computed on it is a provisional development evaluation only.",
)


class MachineWeakLabel(_Record):
    """One unaudited machine-generated weak label. There is deliberately no annotator, human
    timestamp, acceptance, override or agreement field."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    label_basis: Literal["deterministic_machine_weak_labels"] = LABEL_BASIS
    query_id: QueryId
    product_id: ProductId
    label: Label
    relation: Relation
    heuristic_confidence: Confidence
    label_source: Literal["machine_unreviewed"] = "machine_unreviewed"
    human_audited: Literal[False] = False
    proposal_rule: ProposalRule
    proposal_source: Literal["weak-label-policy-1"] = PROPOSER_HANDLE
    proposal_policy_version: Literal["weak-label-policy-1"] = POLICY_VERSION
    requires_human_review: StrictBool
    signal_conflict: StrictBool
    proposal_sha256: Sha256
    proposal_manifest_sha256: Sha256
    pool_manifest_sha256: Sha256
    product_card_sha256: Sha256
    product_content_sha256: Sha256
    catalog_checksum_sha256: Sha256
    reranker_model_id: StrictStr
    reranker_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    rerank_document_version: StrictStr
    rubric_version: RubricVersion

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.label, self.relation)
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class VersionSystem(_Frozen):
    """Which pooled retrieval system a reported search version is."""

    version: Literal["v0", "v1", "v2", "v3"]
    system: StrictStr


class MachineDevSealed(_Record):
    """The `weak_machine_dev_sealed` manifest. Deterministic (no wall-clock time). It is not
    `weak_dev_sealed`, `primary_sealed`, `labels_frozen` or `provenance_unsealed`."""

    weak_set_id: Literal["weak_dev_v1"] = WEAK_SET_ID
    status: Literal["weak_machine_dev_sealed"] = MACHINE_STATUS
    label_basis: Literal["deterministic_machine_weak_labels"] = LABEL_BASIS
    policy_version: Literal["weak-label-policy-1"] = POLICY_VERSION
    human_audit_coverage: Literal[0] = 0
    provisional: Literal[True] = True
    abandoned_human_decision_count: Literal[2] = ABANDONED_HUMAN_DECISION_COUNT
    abandoned_human_decisions_included: Literal[False] = False
    human_reviewed_golden_dataset: Literal[False] = False
    final_test_evidence: Literal[False] = False
    consistency_measured: Literal[False] = False
    warning: Literal[
        "WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit "
        "coverage: 0/568 (0%). Two started human audit decisions were abandoned, archived and "
        "excluded. These results are provisional development diagnostics only. They are not "
        "Golden Dataset results or final held-out test evidence."
    ] = MACHINE_WARNING
    proposal_manifest_sha256: Sha256
    pool_manifest_sha256: Sha256
    queries_manifest_sha256: Sha256
    provenance_binding: Sha256
    signals_binding: Sha256
    catalog_checksum_sha256: Sha256
    reranker_model_id: StrictStr
    reranker_revision: StrictStr
    rerank_document_version: StrictStr
    rubric_version: RubricVersion
    evaluator_version: StrictStr
    version_systems: tuple[VersionSystem, ...]
    label_count: StrictInt
    files: tuple[NamedHash, ...]
    disclosures: tuple[StrictStr, ...] = MACHINE_DISCLOSURES

    def sort_key(self) -> tuple[str, ...]:
        return (self.proposal_manifest_sha256,)
