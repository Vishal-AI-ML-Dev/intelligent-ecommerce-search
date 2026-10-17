"""Seal the review-2 blind second review: the `second_review_sealed` stage.

Subcommands:

* `check --workspace <bundle dir>`: read-only. Verifies the sealed primary stage (including the
  primary judgements and the byte-identical regeneration of the cs-1 sample and queue), that
  the workspace's `bundle.json` and `review.py` are byte-identical to a regeneration from the
  repository with the workspace's own rubric, that `binding.json` belongs to that bundle and to
  `ann-2`, and that the second-review judgements are canonical and satisfy
  `validate_second_review_round` (exactly the sampled pairs, `ann-2` only, at or after the
  primary seal and the binding, hard-constraint rule). Refused while `review.lock` exists.
  Prints counts and the judgements file's SHA-256 only, never a label or agreement figure.
* `seal`: the **second annotator's own confirmation step**:
  `--annotator ann-2 --confirm-human-review --judgements-sha256 <sha printed by finish/check>`.
  It re-runs every check, stamps `second_review_sealed_at`, computes the `iaa-1` agreement
  report from the sealed bytes (before any consensus adjudication), routes every pair that
  needs joint human resolution (a disagreement, or a needs-adjudication or catalog-issue flag
  set by `ann-2`, also when the labels agree) into the generated joint-resolution queue, and
  writes `second_review_judgements.jsonl`, `agreement_report.json`,
  `joint_resolution_queue.jsonl` and `manifests/4-second_review_sealed.json` (last,
  `review_protocol: review-2`) exclusively.
  An AI assistant must never run `seal`.

Usage:
  uv run python -m evals.runners.seal_second_review check --workspace <dir>
  uv run python -m evals.runners.seal_second_review seal --workspace <dir> --annotator ann-2 \\
      --confirm-human-review --judgements-sha256 <hex>
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from evals.annotation import reviewer_tool
from evals.annotation.reviewer_bundle import (
    BUNDLE_FILE,
    TOOL_FILE,
    BundleError,
    SealedPrimary,
    build_bundle,
    bundle_rubric,
    load_sealed,
)
from evals.contracts.canonical import (
    ContractError,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import REVIEW_PROTOCOL_2, SECOND_REVIEWER_HANDLE
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
from evals.contracts.models import Judgement, _parse_timestamp
from evals.contracts.review_sets import build_joint_resolution_queue
from evals.contracts.validation import validate_second_review_round, validate_single_annotator
from evals.metrics.agreement import build_agreement_report
from evals.runners import build_pool
from evals.runners.golden_io import read_frozen, write_exclusive

SECOND_REVIEW_MANIFEST_PATH = "manifests/4-second_review_sealed.json"
SECOND_REVIEW_PATHS: dict[Role, str] = {
    Role.SECOND_REVIEW_JUDGEMENTS: "second_review_judgements.jsonl",
    Role.AGREEMENT_REPORT: "agreement_report.json",
    Role.JOINT_RESOLUTION_QUEUE: "joint_resolution_queue.jsonl",
}


class SecondReviewSealError(BundleError):
    """The second review cannot be checked or sealed; nothing was written."""


def read_workspace(sealed: SealedPrimary, workspace: Path) -> tuple[bytes, datetime]:
    """The judgements file's exact bytes and the binding's `created_at`, after checking that
    the workspace is exactly a bundle regenerated from this sealed stage."""
    if (workspace / reviewer_tool.LOCK_FILE).exists():
        raise SecondReviewSealError("a review.lock exists: a review session may be running")
    rubric, suffix = bundle_rubric(workspace)
    expected = build_bundle(sealed, rubric, suffix)
    for name in (BUNDLE_FILE, TOOL_FILE):
        if (workspace / name).read_bytes() != expected[name]:
            raise SecondReviewSealError(f"{name} differs from the bundle built from this stage")
    bundle_sha = sha256_hex(expected[BUNDLE_FILE])
    binding_path = workspace / reviewer_tool.BINDING_FILE
    judgements_path = workspace / reviewer_tool.JUDGEMENTS_FILE
    if not binding_path.is_file() or not judgements_path.is_file():
        raise SecondReviewSealError("the workspace has no binding or judgements file")
    binding = json.loads(binding_path.read_bytes())
    if set(binding) != {"annotator_handle", "bundle_sha256", "created_at"}:
        raise SecondReviewSealError("binding.json has unexpected fields")
    if binding["bundle_sha256"] != bundle_sha:
        raise SecondReviewSealError("binding.json belongs to another bundle")
    if binding["annotator_handle"] != SECOND_REVIEWER_HANDLE:
        raise SecondReviewSealError("binding.json belongs to another annotator")
    try:
        created_at = _parse_timestamp(binding["created_at"])
    except ValueError as exc:
        raise SecondReviewSealError(f"binding.json: {exc}") from None
    return judgements_path.read_bytes(), created_at


def check_second_review(
    sealed: SealedPrimary, data: bytes, *, created_at: datetime, now: datetime
) -> list[Judgement]:
    judgements = decode_jsonl(data, Judgement)
    primaries = sealed.primaries
    if primaries is None:  # pragma: no cover - always loaded with primary labels here
        raise SecondReviewSealError("the primary judgements were not loaded")
    primary_sealed_at = sealed.manifest.primary_sealed_at
    if primary_sealed_at is None or created_at < primary_sealed_at or created_at > now:
        raise SecondReviewSealError("the workspace binding's created_at is outside its window")
    validate_second_review_round(
        primaries,
        sealed.sample,
        judgements,
        list(sealed.eligibility.values()),
        sealed.manifest.primary_seal_sha256(),
        primary_sealed_at,
        now,
    )
    early = [j for j in judgements if j.annotated_at < created_at]
    if early:
        raise SecondReviewSealError("a second-review judgement predates the workspace binding")
    return judgements


def _load(golden_dir: Path, allowed_root: Path, catalog: CatalogIndex | None) -> SealedPrimary:
    sealed = load_sealed(
        golden_dir, allowed_root=allowed_root, catalog=catalog, primary_labels=True
    )
    if os.path.lexists(sealed.golden_dir / SECOND_REVIEW_MANIFEST_PATH):
        raise SecondReviewSealError("the second review is already sealed")
    return sealed


def check(
    golden_dir: Path,
    workspace: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[int, str]:
    sealed = _load(golden_dir, allowed_root, catalog)
    data, created_at = read_workspace(sealed, workspace)
    judgements = check_second_review(sealed, data, created_at=created_at, now=now())
    return len(judgements), sha256_hex(data)


def seal(
    golden_dir: Path,
    workspace: Path,
    *,
    annotator: str,
    confirm_human_review: bool,
    judgements_sha256: str,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    link: Callable[[Path, Path], None] = os.link,
) -> Manifest:
    if not confirm_human_review:
        raise SecondReviewSealError(
            "refused: --confirm-human-review is the second annotator's own confirmation"
        )
    if annotator != SECOND_REVIEWER_HANDLE:
        raise SecondReviewSealError(
            f"refused: only {SECOND_REVIEWER_HANDLE!r} can seal the second review"
        )
    sealed = _load(golden_dir, allowed_root, catalog)
    data, created_at = read_workspace(sealed, workspace)
    if sha256_hex(data) != judgements_sha256:
        raise SecondReviewSealError(
            "refused: the judgements file differs from the reviewed SHA-256"
        )
    sealed_at = now()
    judgements = check_second_review(sealed, data, created_at=created_at, now=sealed_at)
    report = build_agreement_report(
        sealed.primaries or (),
        sealed.sample,
        judgements,
        primary_annotator=validate_single_annotator(sealed.primaries or ()),
        second_annotator=SECOND_REVIEWER_HANDLE,
        second_review_sha256=sha256_hex(data),
        second_review_sealed_at=sealed_at,
    )
    joint_queue = encode_jsonl(build_joint_resolution_queue(sealed.primaries or (), judgements))
    files = {
        SECOND_REVIEW_PATHS[Role.SECOND_REVIEW_JUDGEMENTS]: data,
        SECOND_REVIEW_PATHS[Role.AGREEMENT_REPORT]: encode_json_object(report),
        SECOND_REVIEW_PATHS[Role.JOINT_RESOLUTION_QUEUE]: joint_queue,
    }
    previous = sealed.manifest
    entries = [*previous.entries] + [
        entry_for(role, path, files[path]) for role, path in SECOND_REVIEW_PATHS.items()
    ]
    manifest = build_manifest(
        golden_version=previous.golden_version,
        stage=Stage.SECOND_REVIEW_SEALED,
        dataset_id=previous.dataset_id,
        dataset_version=previous.dataset_version,
        dataset_checksum_sha256=previous.dataset_checksum_sha256,
        versions=previous.versions,
        entries=tuple(sorted(entries, key=lambda e: e.path)),
        provenance_binding=previous.provenance_binding,
        primary_sealed_at=previous.primary_sealed_at,
        previous_manifest_sha256=previous.manifest_sha256,
        supersedes_manifest_sha256=previous.supersedes_manifest_sha256,
        review_protocol=REVIEW_PROTOCOL_2,
    )
    validate_transition(previous, manifest)
    files[SECOND_REVIEW_MANIFEST_PATH] = encode_manifest(manifest)
    write_exclusive(sealed.golden_dir, files, last=SECOND_REVIEW_MANIFEST_PATH, link=link)
    written = decode_manifest(read_frozen(sealed.golden_dir, SECOND_REVIEW_MANIFEST_PATH))
    for role, path in SECOND_REVIEW_PATHS.items():
        verify_entry(written.entry(role), read_frozen(sealed.golden_dir, path))
    verify_entry(
        written.entry(Role.AGREEMENT_REPORT),
        read_frozen(sealed.golden_dir, SECOND_REVIEW_PATHS[Role.AGREEMENT_REPORT]),
        regenerated=encode_json_object(report),
    )
    verify_entry(
        written.entry(Role.JOINT_RESOLUTION_QUEUE),
        read_frozen(sealed.golden_dir, SECOND_REVIEW_PATHS[Role.JOINT_RESOLUTION_QUEUE]),
        regenerated=joint_queue,
    )
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.seal_second_review")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="validate the workspace; writes nothing")
    checking.add_argument("--workspace", type=Path, required=True)
    run = commands.add_parser("seal", help="second annotator's confirmation: seal the review")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--annotator", required=True)
    run.add_argument("--confirm-human-review", action="store_true")
    run.add_argument("--judgements-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            count, digest = check(args.golden_dir, args.workspace)
            print(f"complete second review: {count} judgements, one per sampled pair")
            print(f"judgements_sha256 {digest}")
        else:
            manifest = seal(
                args.golden_dir,
                args.workspace,
                annotator=args.annotator,
                confirm_human_review=args.confirm_human_review,
                judgements_sha256=args.judgements_sha256,
            )
            print(f"sealed second review; manifest_sha256 {manifest.manifest_sha256}")
    except (ContractError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
