"""Freeze the review-2 labels: the `labels_frozen` stage (M9, review-2 only).

Inputs, all read-only:

* the golden directory sealed through `second_review_sealed`, re-verified end to end by
  `joint_resolution.load_joint` (every earlier stage, the primary and second-review rounds, and
  the byte-identical regeneration of the cs-1 sample, the targeted queue, the agreement report
  and the joint-resolution queue). Any later manifest refuses the freeze;
* `ann-1`'s single-annotator adjudication work directory
  (`data/processed/golden_adjudication/<second-review manifest sha[:16]>/`) and the joint
  resolution work directory (`data/processed/golden_joint_resolution/<same>/`). Each must hold
  its binding to exactly this sealed stage, its records file and its label-change file, and no
  lock. Their exact bytes are read once and validated from those bytes.

Subcommands:

* `check`: read-only. Runs every rule below and prints counts, the SHA-256 of each input file
  and the combined `inputs_sha256`. It never prints a label.
* `freeze`: the **human confirmation step**:
  `--annotator ann-1 --confirm-human-review --inputs-sha256 <sha printed by check>`. It re-runs
  every check, refuses inputs whose combined SHA-256 differs from the reviewed one, stamps
  `labels_frozen_at`, and writes exclusively (never overwriting):
  `adjudication_judgements.jsonl` and `consensus_adjudications.jsonl` (the exact work-file
  bytes), `label_changes.jsonl` (the adjudication and consensus label changes, merged),
  `partitions.jsonl`, `final_judgements.jsonl` (generated) and, last,
  `manifests/5-labels_frozen.json`. It then re-reads every written file, verifies it against
  the manifest, and regenerates the final judgements from the written records byte for byte.
  An AI assistant must never run `freeze`.

Rules (nothing is inferred, generated or repaired; a failure writes nothing):

* every joint-resolution pair has exactly one joint record and every mandatory targeted-queue
  item a single-annotator adjudication (`validate_review_2_adjudication`); an unresolved item
  refuses the freeze;
* the stored adjudication label changes equal the ones the adjudications require; no
  consensus label change exists for a pair without a joint record;
* final labels come only from `derive_final_judgements_review_2`; an optional review candidate
  that was not adjudicated keeps its sealed primary label (`decided_in: primary`) and is counted
  as not adjudicated, never as resolved or re-reviewed;
* `partitions.jsonl` restates each frozen query record's own `partition` field
  (`validate_partitions`). It is not a separately authored held-out test partition: under
  golden version 1 every query is `pilot_dev`, and no test partition exists;
* `labels_frozen_at` is no earlier than any included record or the second-review seal
  (`validate_labels_frozen_at`).

The private provenance stays a binding only; `provenance_unsealed` is a separate later stage
(`evals.runners.unseal_provenance`).

Usage:
  uv run python -m evals.runners.freeze_labels check
  uv run python -m evals.runners.freeze_labels freeze --annotator ann-1 --confirm-human-review \\
      --inputs-sha256 <hex>
"""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.annotation import adjudication, joint_resolution
from evals.annotation.joint_resolution import JointContext, JointStore, load_joint
from evals.contracts.canonical import ContractError, decode_jsonl, encode_jsonl, sha256_hex
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import (
    DISCLOSURE_REVIEW_2,
    REVIEW_PROTOCOL_2,
    SINGLE_ANNOTATOR_HANDLE,
)
from evals.contracts.manifest import (
    Manifest,
    Role,
    Stage,
    build_manifest,
    decode_manifest,
    encode_manifest,
    entry_for,
    validate_transition,
    verify_entry,
)
from evals.contracts.models import (
    ConsensusAdjudication,
    FinalJudgement,
    Judgement,
    LabelChange,
    Partition,
    PartitionAssignment,
)
from evals.contracts.review_sets import derive_final_judgements_review_2
from evals.contracts.validation import (
    validate_final_judgements_review_2,
    validate_labels_frozen_at,
    validate_partitions,
    validate_review_2_adjudication,
)
from evals.runners import build_pool
from evals.runners.golden_io import FreezeError, read_frozen, refuse_stale_staging, write_exclusive

LABELS_MANIFEST_PATH = "manifests/5-labels_frozen.json"
FREEZE_PATHS: dict[Role, str] = {
    Role.ADJUDICATION_JUDGEMENTS: "adjudication_judgements.jsonl",
    Role.CONSENSUS_ADJUDICATIONS: "consensus_adjudications.jsonl",
    Role.LABEL_CHANGES: "label_changes.jsonl",
    Role.PARTITIONS: "partitions.jsonl",
    Role.FINAL_JUDGEMENTS: "final_judgements.jsonl",
}
PARTITION_NOTE = (
    "partitions.jsonl restates each frozen query record's own partition; it is not a "
    "separately authored held-out test partition, and no held-out test data exists"
)

FURTHER_DISCLOSURES = (
    "ann-1 had seen machine proposals for this pool, so the primary round was not fully blind; "
    "ann-1 received AI-assistant explanations of the rubric during primary annotation; ann-2's "
    "independence is procedural and cannot be verified technically (docs/golden-dataset-m9.md)"
)


class LabelFreezeError(FreezeError):
    """The labels cannot be checked or frozen; nothing was written."""


@dataclass(frozen=True)
class FreezeInputs:
    """Everything the freeze writes, validated, with the exact work-file bytes it came from."""

    joint: JointContext
    adjudication_data: bytes
    adjudication_changes_data: bytes
    consensus_data: bytes
    consensus_changes_data: bytes
    adjudications: tuple[Judgement, ...]
    consensus: tuple[ConsensusAdjudication, ...]
    label_changes: tuple[LabelChange, ...]
    partitions: tuple[PartitionAssignment, ...]
    finals: tuple[FinalJudgement, ...]

    def digests(self) -> dict[str, str]:
        """The sealed stage and every work file this freeze is bound to."""
        return {
            "second_review_manifest_sha256": self.joint.targeted.manifest.manifest_sha256,
            "adjudication_judgements_sha256": sha256_hex(self.adjudication_data),
            "adjudication_label_changes_sha256": sha256_hex(self.adjudication_changes_data),
            "consensus_adjudications_sha256": sha256_hex(self.consensus_data),
            "consensus_label_changes_sha256": sha256_hex(self.consensus_changes_data),
        }

    @property
    def inputs_sha256(self) -> str:
        text = json.dumps(self.digests(), sort_keys=True, separators=(",", ":"))
        return sha256_hex(text.encode("utf-8"))

    def files(self) -> dict[str, bytes]:
        return {
            FREEZE_PATHS[Role.ADJUDICATION_JUDGEMENTS]: self.adjudication_data,
            FREEZE_PATHS[Role.CONSENSUS_ADJUDICATIONS]: self.consensus_data,
            FREEZE_PATHS[Role.LABEL_CHANGES]: encode_jsonl(self.label_changes),
            FREEZE_PATHS[Role.PARTITIONS]: encode_jsonl(self.partitions),
            FREEZE_PATHS[Role.FINAL_JUDGEMENTS]: encode_jsonl(self.finals),
        }


def _work_files(
    directory: Path, lock: str, binding: str, records: str, changes: str, what: str
) -> tuple[bytes, bytes]:
    if (directory / lock).exists():
        raise LabelFreezeError(f"a {what} lock exists: a session may be running ({lock})")
    for name in (binding, records, changes):
        if not (directory / name).is_file():
            raise LabelFreezeError(f"the {what} work directory has no {name}: {directory}")
    return (directory / records).read_bytes(), (directory / changes).read_bytes()


def load_inputs(
    golden_dir: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    adjudication_root: Path = adjudication.WORK_ROOT,
    joint_root: Path = joint_resolution.WORK_ROOT,
    now: datetime,
) -> FreezeInputs:
    """Verify the sealed chain and both work directories, then every freeze rule (see the module
    docstring). Reads only; returns what `freeze` would write."""
    joint = load_joint(golden_dir, allowed_root=allowed_root, catalog=catalog)
    targeted = joint.targeted
    golden = targeted.sealed.golden_dir
    refuse_stale_staging(golden)

    adjudication_store = adjudication.AdjudicationStore(targeted, adjudication_root)
    adjudication_data, adjudication_changes_data = _work_files(
        adjudication_store.directory,
        adjudication.LOCK_FILE,
        adjudication.BINDING_FILE,
        adjudication.WORK_FILE,
        adjudication.CHANGES_FILE,
        "adjudication",
    )
    adjudication_binding = adjudication_store.read_binding(now=now)
    if adjudication_binding is None:  # pragma: no cover - _work_files requires the binding
        raise LabelFreezeError("the adjudication work has no binding")
    adjudications = decode_jsonl(adjudication_data, Judgement)
    adjudication_changes = adjudication.validate_records(
        targeted, adjudications, now=now, binding_created_at=adjudication_binding.created_at
    )
    if adjudication_changes_data != encode_jsonl(adjudication_changes):
        raise LabelFreezeError(
            f"{adjudication.CHANGES_FILE} does not match the adjudications; run the adjudication "
            "tool's validate"
        )

    joint_store = JointStore(joint, joint_root)
    consensus_data, consensus_changes_data = _work_files(
        joint_store.directory,
        joint_resolution.LOCK_FILE,
        joint_resolution.BINDING_FILE,
        joint_resolution.RECORDS_FILE,
        joint_resolution.CHANGES_FILE,
        "joint-resolution",
    )
    joint_binding = joint_store.read_binding(now=now)
    if joint_binding is None:  # pragma: no cover - _work_files requires the binding
        raise LabelFreezeError("the joint-resolution work has no binding")
    consensus = decode_jsonl(consensus_data, ConsensusAdjudication)
    consensus_changes = decode_jsonl(consensus_changes_data, LabelChange)
    if joint_store.orphans(consensus, consensus_changes):
        raise LabelFreezeError(
            f"{joint_resolution.CHANGES_FILE} holds a label change for a pair with no joint "
            "record (an interrupted save)"
        )
    joint_resolution.validate_records(
        joint, consensus, consensus_changes, now=now, binding_created_at=joint_binding.created_at
    )

    primaries = targeted.sealed.primaries or ()
    second_reviews = tuple(joint.second_reviews.values())
    label_changes = tuple(
        sorted([*adjudication_changes, *consensus_changes], key=lambda c: c.sort_key())
    )
    validate_review_2_adjudication(
        primaries,
        second_reviews,
        targeted.queue,
        joint.items,
        adjudications,
        consensus,
        label_changes,
        targeted.golden_version,
        primary_sealed_at=targeted.manifest.primary_sealed_at,  # type: ignore[arg-type]
        report=joint.report,
    )
    finals = derive_final_judgements_review_2(primaries, adjudications, consensus)
    validate_final_judgements_review_2(primaries, adjudications, consensus, finals)
    queries = sorted(targeted.sealed.queries.values(), key=lambda q: q.sort_key())
    partitions = tuple(
        PartitionAssignment(query_id=q.query_id, partition=q.partition) for q in queries
    )
    validate_partitions(queries, partitions)
    return FreezeInputs(
        joint=joint,
        adjudication_data=adjudication_data,
        adjudication_changes_data=adjudication_changes_data,
        consensus_data=consensus_data,
        consensus_changes_data=consensus_changes_data,
        adjudications=tuple(adjudications),
        consensus=tuple(consensus),
        label_changes=label_changes,
        partitions=partitions,
        finals=tuple(finals),
    )


def summary_lines(inputs: FreezeInputs, *, frozen: bool) -> list[str]:
    """Counts and required disclosures only; never a label of any pair."""
    targeted = inputs.joint.targeted
    adjudicated = {(j.query_id, j.product_id) for j in inputs.adjudications}
    optional = {(i.query_id, i.product_id) for i in targeted.optional}
    optional_done = len(optional & adjudicated)
    decided = Counter(f.decided_in for f in inputs.finals)
    kinds = Counter(c.kind.value for c in inputs.label_changes)
    partitions = Counter(p.partition for p in inputs.partitions)
    return [
        f"second_review_sealed {targeted.manifest.manifest_sha256}",
        f"primary judgements (sealed): {len(targeted.primaries)}",
        f"second-review judgements (sealed): {len(inputs.joint.second_reviews)}",
        f"joint-resolution pairs: {len(inputs.joint.items)}; joint records: "
        f"{len(inputs.consensus)}; unresolved: {len(inputs.joint.items) - len(inputs.consensus)}",
        f"targeted queue: {len(targeted.queue)} items; "
        f"{len(targeted.queue) - len(targeted.items)} settled by a joint record",
        f"mandatory targeted items: {len(targeted.mandatory)}; adjudicated by ann-1: "
        f"{len(adjudicated - optional)}",
        f"optional review candidates: {len(optional)}; adjudicated: {optional_done}; not "
        f"adjudicated: {len(optional) - optional_done} (they keep their sealed primary label "
        "and were not re-reviewed)",
        f"label changes: {kinds.get('adjudication', 0)} adjudication, "
        f"{kinds.get('consensus', 0)} consensus",
        f"final judgements: {len(inputs.finals)} (decided_in primary {decided.get('primary', 0)}, "
        f"adjudication {decided.get('adjudication', 0)}, consensus {decided.get('consensus', 0)})",
        f"partition assignments: {len(inputs.partitions)} ("
        + ", ".join(f"{p.value} {partitions.get(p, 0)}" for p in Partition)
        + f"); {PARTITION_NOTE}",
        f"disclosure ({REVIEW_PROTOCOL_2}"
        + ("" if frozen else "; to be used only once the labels are frozen")
        + f"): {DISCLOSURE_REVIEW_2}",
        f"further disclosures: {FURTHER_DISCLOSURES}",
    ]


def _role_files(manifest: Manifest, golden: Path) -> dict[Role, bytes]:
    files = {e.role: read_frozen(golden, e.path) for e in manifest.entries}
    for entry in manifest.entries:
        verify_entry(entry, files[entry.role])
    return files


def check(
    golden_dir: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    adjudication_root: Path = adjudication.WORK_ROOT,
    joint_root: Path = joint_resolution.WORK_ROOT,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FreezeInputs:
    return load_inputs(
        golden_dir,
        allowed_root=allowed_root,
        catalog=catalog,
        adjudication_root=adjudication_root,
        joint_root=joint_root,
        now=now(),
    )


def freeze(
    golden_dir: Path,
    *,
    annotator: str,
    confirm_human_review: bool,
    inputs_sha256: str,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    adjudication_root: Path = adjudication.WORK_ROOT,
    joint_root: Path = joint_resolution.WORK_ROOT,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    link: Callable[[Path, Path], None] = os.link,
) -> tuple[Manifest, FreezeInputs]:
    if not confirm_human_review:
        raise LabelFreezeError(
            "refused: --confirm-human-review is the annotator's own confirmation"
        )
    if annotator != SINGLE_ANNOTATOR_HANDLE:
        raise LabelFreezeError(f"refused: only {SINGLE_ANNOTATOR_HANDLE!r} can freeze the labels")
    inputs = check(
        golden_dir,
        allowed_root=allowed_root,
        catalog=catalog,
        adjudication_root=adjudication_root,
        joint_root=joint_root,
        now=now,
    )
    if inputs.inputs_sha256 != inputs_sha256:
        raise LabelFreezeError("refused: the freeze inputs differ from the reviewed SHA-256")
    frozen_at = now()
    previous = inputs.joint.targeted.manifest
    golden = inputs.joint.targeted.sealed.golden_dir
    files = inputs.files()
    entries = [*previous.entries] + [
        entry_for(role, path, files[path]) for role, path in FREEZE_PATHS.items()
    ]
    manifest = build_manifest(
        golden_version=previous.golden_version,
        stage=Stage.LABELS_FROZEN,
        dataset_id=previous.dataset_id,
        dataset_version=previous.dataset_version,
        dataset_checksum_sha256=previous.dataset_checksum_sha256,
        versions=previous.versions,
        entries=tuple(sorted(entries, key=lambda e: e.path)),
        provenance_binding=previous.provenance_binding,
        primary_sealed_at=previous.primary_sealed_at,
        labels_frozen_at=frozen_at,
        previous_manifest_sha256=previous.manifest_sha256,
        supersedes_manifest_sha256=previous.supersedes_manifest_sha256,
        review_protocol=REVIEW_PROTOCOL_2,
    )
    validate_transition(previous, manifest)
    role_files: Mapping[Role, bytes] = {
        **_role_files(previous, golden),
        **{role: files[path] for role, path in FREEZE_PATHS.items()},
    }
    validate_labels_frozen_at(manifest, role_files)
    files[LABELS_MANIFEST_PATH] = encode_manifest(manifest)
    write_exclusive(golden, files, last=LABELS_MANIFEST_PATH, link=link)
    written = verify_written(golden, previous)
    if written != manifest:  # pragma: no cover - the bytes were just verified by hash
        raise LabelFreezeError("the written manifest differs from the one built")
    return written, inputs


def verify_written(golden: Path, previous: Manifest) -> Manifest:
    """Re-read the written stage: the manifest is the next legal transition, every entry matches
    its bytes, the freeze time covers every record, and the generated final judgements are
    byte-identical to a regeneration from the written records."""
    written = decode_manifest(read_frozen(golden, LABELS_MANIFEST_PATH))
    validate_transition(previous, written)
    files = _role_files(written, golden)
    validate_labels_frozen_at(written, files)
    primaries = decode_jsonl(files[Role.PRIMARY_JUDGEMENTS], Judgement)
    adjudications = decode_jsonl(files[Role.ADJUDICATION_JUDGEMENTS], Judgement)
    consensus = decode_jsonl(files[Role.CONSENSUS_ADJUDICATIONS], ConsensusAdjudication)
    verify_entry(
        written.entry(Role.FINAL_JUDGEMENTS),
        files[Role.FINAL_JUDGEMENTS],
        regenerated=encode_jsonl(
            derive_final_judgements_review_2(primaries, adjudications, consensus)
        ),
    )
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.freeze_labels")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--adjudication-root", type=Path, default=adjudication.WORK_ROOT)
    parser.add_argument("--joint-root", type=Path, default=joint_resolution.WORK_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate every freeze input; writes nothing")
    run = commands.add_parser("freeze", help="human confirmation: freeze the review-2 labels")
    run.add_argument("--annotator", required=True)
    run.add_argument("--confirm-human-review", action="store_true")
    run.add_argument("--inputs-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = {"adjudication_root": args.adjudication_root, "joint_root": args.joint_root}
    try:
        if args.command == "check":
            inputs = check(args.golden_dir, **roots)
            for line in summary_lines(inputs, frozen=False):
                print(line)
            for name, digest in inputs.digests().items():
                print(f"{name} {digest}")
            print(f"inputs_sha256 {inputs.inputs_sha256}")
        else:
            manifest, inputs = freeze(
                args.golden_dir,
                annotator=args.annotator,
                confirm_human_review=args.confirm_human_review,
                inputs_sha256=args.inputs_sha256,
                **roots,
            )
            for line in summary_lines(inputs, frozen=True):
                print(line)
            print(f"froze labels; manifest_sha256 {manifest.manifest_sha256}")
    except (ContractError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
