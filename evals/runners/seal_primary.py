"""Seal the primary annotation round: the `primary_sealed` stage (M9; tooling only in C2).

Subcommands:

* `check`: read-only. Validates the working file against the verified `pool_frozen` stage:
  exactly one primary judgement per pooled pair (missing, extra and duplicate records refused),
  the approved annotator `ann-1` only, the frozen rubric, timestamps at or after both the query
  confirmation and the work binding's `created_at` and not in the future, canonical bytes,
  catalog content and card hashes, and the hard-constraint rule. Refused while an annotation
  lock exists. Prints counts and the working file's SHA-256 only.
* `seal`: the **human confirmation step**. Only the annotator runs it, with
  `--annotator ann-1 --confirm-human-review --judgements-sha256 <sha printed by check>`. It
  re-runs every check, stamps `primary_sealed_at` (never before the last judgement), generates
  the deterministic consistency sample and adjudication queue from the sealed bytes, and writes
  `primary_judgements.jsonl`, `consistency_sample.json`, `adjudication_queue.jsonl` and
  `manifests/3-primary_sealed.json` (last) exclusively. The private provenance stays private:
  only its binding is carried. An AI assistant must never run `seal`.

The 72-hour consistency delay is measured from `primary_sealed_at`; nothing here creates a
consistency judgement, an adjudication or a final label.

Usage:
  uv run python -m evals.runners.seal_primary check
  uv run python -m evals.runners.seal_primary seal --annotator ann-1 --confirm-human-review \\
      --judgements-sha256 <hex>
"""

import argparse
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from evals.annotation.session import (
    WORK_ROOT,
    PoolContext,
    WorkStore,
    load_pool,
    stale_lock_message,
    validate_records,
)
from evals.contracts.canonical import (
    ContractError,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import SINGLE_ANNOTATOR_HANDLE
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
from evals.contracts.models import Judgement
from evals.contracts.review_sets import build_adjudication_queue, build_consistency_sample
from evals.contracts.validation import (
    validate_primary_round,
    validate_primary_seal_time,
    validate_single_annotator,
)
from evals.runners import build_pool, freeze_queries
from evals.runners.golden_io import FreezeError, read_frozen, refuse_stale_staging, write_exclusive

SEALED_MANIFEST_PATH = "manifests/3-primary_sealed.json"
SEAL_PATHS: dict[Role, str] = {
    Role.PRIMARY_JUDGEMENTS: "primary_judgements.jsonl",
    Role.CONSISTENCY_SAMPLE: "consistency_sample.json",
    Role.ADJUDICATION_QUEUE: "adjudication_queue.jsonl",
}


class SealError(FreezeError):
    """The primary round cannot be sealed; nothing was written."""


def check_primary(
    context: PoolContext, data: bytes, *, now: datetime, binding_created_at: datetime
) -> list[Judgement]:
    """Every rule a sealable primary round must satisfy (see the module docstring)."""
    judgements = decode_jsonl(data, Judgement)
    if encode_jsonl(judgements) != data:  # pragma: no cover - decode_jsonl already refuses
        raise SealError("the judgements file is not in canonical form")
    validate_records(context, judgements, now=now, binding_created_at=binding_created_at)
    validate_primary_round(list(context.items), judgements)
    validate_single_annotator(judgements)
    return judgements


def read_primary(store: WorkStore, *, now: datetime) -> tuple[bytes, datetime]:
    """The working file's exact bytes and its binding's `created_at`. Refused while an
    annotation lock exists (a session may be writing), before initialization, or without a
    working file."""
    if store.lock_path.exists():
        raise SealError(stale_lock_message(store))
    binding = store.read_binding(now=now)
    if binding is None or not store.path.is_file():
        raise SealError("there is no working file to check or seal")
    return store.path.read_bytes(), binding.created_at


def seal(
    golden_dir: Path,
    *,
    annotator: str,
    confirm_human_review: bool,
    judgements_sha256: str,
    work_root: Path = WORK_ROOT,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    link: Callable[[Path, Path], None] = os.link,
) -> Manifest:
    if not confirm_human_review:
        raise SealError("refused: --confirm-human-review is the annotator's own confirmation")
    if annotator != SINGLE_ANNOTATOR_HANDLE:
        raise SealError(f"refused: only {SINGLE_ANNOTATOR_HANDLE!r} can seal the primary round")
    context = load_pool(golden_dir, allowed_root=allowed_root, catalog=catalog)
    golden = freeze_queries.safe_golden_dir(golden_dir, allowed_root)
    refuse_stale_staging(golden)
    store = WorkStore(context, work_root)
    data, created_at = read_primary(store, now=now())
    if sha256_hex(data) != judgements_sha256:
        raise SealError("refused: the working file differs from the reviewed SHA-256")
    sealed_at = now()
    judgements = check_primary(context, data, now=sealed_at, binding_created_at=created_at)
    validate_primary_seal_time(judgements, sealed_at)
    sample = build_consistency_sample(judgements, sha256_hex(data), sealed_at)
    queue = build_adjudication_queue(
        judgements, list(context.queries.values()), list(context.eligibility.values())
    )
    files = {
        SEAL_PATHS[Role.PRIMARY_JUDGEMENTS]: data,
        SEAL_PATHS[Role.CONSISTENCY_SAMPLE]: encode_json_object(sample),
        SEAL_PATHS[Role.ADJUDICATION_QUEUE]: encode_jsonl(queue),
    }
    previous = context.pool_manifest
    entries = [*previous.entries] + [
        entry_for(role, path, files[path]) for role, path in SEAL_PATHS.items()
    ]
    manifest = build_manifest(
        golden_version=previous.golden_version,
        stage=Stage.PRIMARY_SEALED,
        dataset_id=previous.dataset_id,
        dataset_version=previous.dataset_version,
        dataset_checksum_sha256=previous.dataset_checksum_sha256,
        versions=previous.versions,
        entries=tuple(sorted(entries, key=lambda e: e.path)),
        provenance_binding=previous.provenance_binding,
        primary_sealed_at=sealed_at,
        previous_manifest_sha256=previous.manifest_sha256,
        supersedes_manifest_sha256=previous.supersedes_manifest_sha256,
    )
    validate_transition(previous, manifest)
    if manifest.primary_seal_sha256() != sample.primary_seal_sha256:  # pragma: no cover
        raise SealError("the consistency sample is not bound to the sealed bytes")
    files[SEALED_MANIFEST_PATH] = encode_manifest(manifest)
    write_exclusive(golden, files, last=SEALED_MANIFEST_PATH, link=link)
    written = decode_manifest(read_frozen(golden, SEALED_MANIFEST_PATH))
    for role, path in SEAL_PATHS.items():
        verify_entry(written.entry(role), read_frozen(golden, path))
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.seal_primary")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--work-root", type=Path, default=WORK_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate the working file; writes nothing")
    run = commands.add_parser("seal", help="human confirmation: seal the primary round")
    run.add_argument("--annotator", required=True)
    run.add_argument("--confirm-human-review", action="store_true")
    run.add_argument("--judgements-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            context = load_pool(args.golden_dir)
            store = WorkStore(context, args.work_root)
            data, created_at = read_primary(store, now=datetime.now(UTC))
            judgements = check_primary(
                context, data, now=datetime.now(UTC), binding_created_at=created_at
            )
            print(f"complete primary round: {len(judgements)} judgements, one per pooled pair")
            print(f"judgements_sha256 {sha256_hex(data)}")
        else:
            manifest = seal(
                args.golden_dir,
                annotator=args.annotator,
                confirm_human_review=args.confirm_human_review,
                judgements_sha256=args.judgements_sha256,
                work_root=args.work_root,
            )
            print(f"sealed primary round; manifest_sha256 {manifest.manifest_sha256}")
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
