"""Assemble and verify the audited provisional weak set (`weak_dev_sealed`).

Runs only after all 100 audit decisions exist and no audit session holds the lock, and only
for the human auditor (`--annotator ann-1`). Every pooled pair gets one `WeakFinalLabel`:

* audited pairs take the human outcome (`human_accepted_proposal` or `human_override`);
* every other pair keeps its machine proposal (`machine_unreviewed`).

Written exclusively (never overwritten) beside the proposals: `audit_decisions.jsonl`,
`weak_final_labels.jsonl` and, last, `weak_dev_sealed.json`. Nothing is written to the strict
Golden paths; this is not `primary_sealed`, `labels_frozen` or `provenance_unsealed`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from evals.contracts.canonical import (
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.runners.golden_io import read_frozen, write_exclusive
from evals.weak_labels.audit import AuditContext, AuditError, AuditStore, validate_decisions
from evals.weak_labels.contracts import (
    AUDIT_SIZE,
    HUMAN_AUDITOR,
    AuditDecision,
    LabelSource,
    NamedHash,
    WeakDevSealed,
    WeakFinalLabel,
)
from evals.weak_labels.generation import DEFAULT_WEAK_DIR, WEAKSET_ROOT, safe_weak_dir
from evals.weak_labels.sampling import proposal_sha256

DECISIONS_FILE = "audit_decisions.jsonl"
FINAL_FILE = "weak_final_labels.jsonl"
SEALED_FILE = "weak_dev_sealed.json"
SEALED_FILES = (DECISIONS_FILE, FINAL_FILE)


@dataclass(frozen=True)
class SealedWeakSet:
    sealed: WeakDevSealed
    decisions: tuple[AuditDecision, ...]
    finals: tuple[WeakFinalLabel, ...]


def build_finals(context: AuditContext, decisions: Sequence[AuditDecision]) -> list[WeakFinalLabel]:
    by_pair = {(d.query_id, d.product_id): d for d in decisions}
    finals = []
    for proposal in context.generation.proposals:
        pair = (proposal.query_id, proposal.product_id)
        decision = by_pair.get(pair)
        common = {
            "query_id": proposal.query_id,
            "product_id": proposal.product_id,
            "proposed_label": proposal.proposed_label,
            "proposed_relation": proposal.proposed_relation,
            "proposal_confidence": proposal.proposal_confidence,
            "proposal_sha256": proposal_sha256(proposal),
            "rubric_version": context.facts.rubric_version,
        }
        if decision is None:
            finals.append(
                WeakFinalLabel(
                    label=proposal.proposed_label,
                    relation=proposal.proposed_relation,
                    label_source=LabelSource.MACHINE_UNREVIEWED,
                    **common,
                )
            )
        else:
            finals.append(
                WeakFinalLabel(
                    label=decision.label,
                    relation=decision.relation,
                    label_source=LabelSource(decision.decision),
                    decided_by=HUMAN_AUDITOR,
                    **common,
                )
            )
    return sorted(finals, key=lambda f: f.sort_key())


def _complete(context: AuditContext, decisions: Sequence[AuditDecision]) -> None:
    queued = {(q.query_id, q.product_id) for q in context.queue}
    decided = {(d.query_id, d.product_id) for d in decisions}
    if len(decisions) != AUDIT_SIZE or decided != queued:
        raise AuditError(
            f"assembly needs all {AUDIT_SIZE} audit decisions; {len(decided & queued)} exist"
        )


def assemble(
    context: AuditContext,
    store: AuditStore,
    *,
    annotator: str,
    now: datetime,
    weak_dir: Path = DEFAULT_WEAK_DIR,
    allowed_root: Path = WEAKSET_ROOT,
) -> WeakDevSealed:
    if annotator != HUMAN_AUDITOR:
        raise AuditError(f"refused: only {HUMAN_AUDITOR!r} seals the audited weak set")
    if store.lock_path.exists():
        raise AuditError("an audit session holds the lock; finish or check it first")
    decisions = sorted(store.load(now=now), key=lambda d: d.sort_key())
    _complete(context, decisions)
    finals = build_finals(context, decisions)
    files = {DECISIONS_FILE: encode_jsonl(decisions), FINAL_FILE: encode_jsonl(finals)}
    sources = [f.label_source for f in finals]
    sealed = WeakDevSealed(
        proposal_manifest_sha256=context.generation.manifest_sha256,
        pool_manifest_sha256=context.facts.pool_manifest.manifest_sha256,
        files=tuple(NamedHash(path=p, sha256=sha256_hex(files[p])) for p in SEALED_FILES),
        label_count=len(finals),
        audited_count=len(decisions),
        accepted_count=sources.count(LabelSource.HUMAN_ACCEPTED_PROPOSAL),
        override_count=sources.count(LabelSource.HUMAN_OVERRIDE),
        machine_unreviewed_count=sources.count(LabelSource.MACHINE_UNREVIEWED),
        sealed_at=now,
    )
    directory = safe_weak_dir(weak_dir, allowed_root)
    write_exclusive(directory, {**files, SEALED_FILE: encode_json_object(sealed)}, last=SEALED_FILE)
    verify_sealed(context, weak_dir, allowed_root=allowed_root)
    return sealed


def verify_sealed(
    context: AuditContext,
    weak_dir: Path = DEFAULT_WEAK_DIR,
    *,
    allowed_root: Path = WEAKSET_ROOT,
) -> SealedWeakSet:
    """The sealed files match their hashes, every decision re-validates, and the final labels
    regenerate byte for byte from the proposals and the decisions."""
    directory = safe_weak_dir(weak_dir, allowed_root)
    sealed = decode_json_object(read_frozen(directory, SEALED_FILE), WeakDevSealed)
    if sealed.proposal_manifest_sha256 != context.generation.manifest_sha256:
        raise AuditError("the sealed weak set is bound to other proposals")
    if sealed.pool_manifest_sha256 != context.facts.pool_manifest.manifest_sha256:
        raise AuditError("the sealed weak set is bound to another pool")
    if [f.path for f in sealed.files] != list(SEALED_FILES):
        raise AuditError("unexpected sealed file list")
    content = {}
    for entry in sealed.files:
        content[entry.path] = read_frozen(directory, entry.path)
        if sha256_hex(content[entry.path]) != entry.sha256:
            raise AuditError(f"{entry.path} does not match weak_dev_sealed")
    decisions = decode_jsonl(content[DECISIONS_FILE], AuditDecision)
    _complete(context, decisions)
    first = min(d.decided_at for d in decisions)
    validate_decisions(context, decisions, now=sealed.sealed_at, created_at=first)
    finals = build_finals(context, decisions)
    if encode_jsonl(finals) != content[FINAL_FILE]:
        raise AuditError("the final weak labels do not regenerate from proposals and decisions")
    sources = [f.label_source for f in finals]
    counts = (
        len(finals),
        len(decisions),
        sources.count(LabelSource.HUMAN_ACCEPTED_PROPOSAL),
        sources.count(LabelSource.HUMAN_OVERRIDE),
        sources.count(LabelSource.MACHINE_UNREVIEWED),
    )
    if counts != (
        sealed.label_count,
        sealed.audited_count,
        sealed.accepted_count,
        sealed.override_count,
        sealed.machine_unreviewed_count,
    ):
        raise AuditError("weak_dev_sealed counts do not match the labels")
    return SealedWeakSet(sealed, tuple(decisions), tuple(finals))
