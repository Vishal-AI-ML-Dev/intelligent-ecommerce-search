"""Freeze the `queries_frozen` stage of the internal Golden Dataset (M9 Session C1).

Subcommands:

* `preview`: validate the AI-drafted input and print the query table, the input SHA-256 and the
  generated extraction bytes. Writes nothing.
* `freeze`: the **human confirmation step**. Only the human annotator runs it, with
  `--annotator ann-1 --confirm-human-review --input-sha256 <sha of the reviewed input>`. It
  stamps the confirmation time, validates every record, and writes the recorded queries, gold
  constraints, product intents and authoring attestation, the generated qu-1 extraction record
  and the `queries_frozen` manifest. An AI assistant must never run it: the flags are the
  annotator's own statement that they reviewed the exact input bytes.
* `verify`: read-only re-verification of a frozen stage against its manifest.

Safety: every path must stay inside `evals/goldens/` with no `..`, symlink or junction on the
way; nothing that already exists is overwritten; all files are staged in a temporary directory
and hard-linked into place with exclusive creation, manifest last, and any failure removes what
this run created. It creates no pool, provenance, label or metric file.

Usage:
  uv run python -m evals.runners.freeze_queries preview
  uv run python -m evals.runners.freeze_queries verify
  uv run python -m evals.runners.freeze_queries freeze --annotator ann-1 \\
      --confirm-human-review --input-sha256 <hex>
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ecommerce_search.embeddings.spec import ALL_MINILM_L6_V2
from ecommerce_search.filtering.policy import FILTER_POLICY_VERSION
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2
from evals.contracts.canonical import (
    ContractError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import ROOT, CatalogIndex, load_catalog_index
from evals.contracts.constants import (
    BLIND_ORDER_VERSION,
    CONSISTENCY_SAMPLE_VERSION,
    POOL_POLICY_VERSION,
    SINGLE_ANNOTATOR_HANDLE,
)
from evals.contracts.eligibility import summarize
from evals.contracts.extraction import derive_query_extraction
from evals.contracts.manifest import (
    Manifest,
    ManifestVersions,
    Role,
    Stage,
    build_manifest,
    decode_manifest,
    encode_manifest,
    entry_for,
    verify_entry,
)
from evals.contracts.models import (
    AuthoringAttestation,
    GoldConstraint,
    ProductIntent,
    QueryExtraction,
    QueryRecord,
    canonical_gold_value,
)
from evals.contracts.pilot import PilotInput, QueryStageRecords, build_records, validate_pilot
from evals.contracts.validation import (
    validate_attestation,
    validate_gold_constraints,
    validate_product_intents,
    validate_queries,
    validate_query_extraction,
)

ALLOWED_ROOT = ROOT / "evals" / "goldens"
DEFAULT_GOLDEN_DIR = ALLOWED_ROOT / "internal-search" / "v1"
INPUT_PATH = "authoring/pilot_input.json"
MANIFEST_PATH = "manifests/1-queries_frozen.json"
ARTIFACT_PATHS: dict[Role, str] = {
    Role.QUERIES: "queries.jsonl",
    Role.GOLD_CONSTRAINTS: "gold_constraints.jsonl",
    Role.PRODUCT_INTENTS: "product_intents.jsonl",
    Role.QUERY_EXTRACTION: "query_extraction.jsonl",
    Role.AUTHORING_ATTESTATION: "authoring_attestation.json",
}
GOLDEN_VERSION = 1


class FreezeError(ContractError):
    """The stage cannot be frozen or verified; nothing was written (or it was rolled back)."""


# ---- paths --------------------------------------------------------------------------------


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def safe_golden_dir(path: Path, allowed_root: Path = ALLOWED_ROOT) -> Path:
    """`path` as an absolute directory strictly inside `allowed_root`, reached without `..` and
    without any symlink or junction component (checked on the unresolved path)."""
    if ".." in path.parts:
        raise FreezeError("the golden directory must not contain '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    root = allowed_root if allowed_root.is_absolute() else Path.cwd() / allowed_root
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        raise FreezeError(f"the golden directory must be inside {allowed_root}") from None
    if not relative.parts:
        raise FreezeError("the golden directory must be below the allowed root, not the root")
    current = root
    if _is_link(current):
        raise FreezeError("the allowed root must not be a symlink or junction")
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise FreezeError(f"symlinks and junctions are refused: {current}")
    if not absolute.is_dir():
        raise FreezeError(f"the golden directory does not exist: {absolute}")
    if absolute.resolve() != root.resolve() / relative:
        raise FreezeError("the golden directory resolves outside its expected location")
    return absolute


def _safe_child(golden_dir: Path, relative: str) -> Path:
    target = golden_dir / relative
    current = golden_dir
    for part in Path(relative).parts:
        current = current / part
        if _is_link(current):
            raise FreezeError(f"symlinks and junctions are refused: {current}")
    return target


def read_input(golden_dir: Path) -> tuple[PilotInput, bytes]:
    path = _safe_child(golden_dir, INPUT_PATH)
    if not path.is_file():
        raise FreezeError(f"the pilot input does not exist: {path}")
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf") or b"\r" in data:
        raise FreezeError("the pilot input must be UTF-8 with LF line endings and no BOM")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise FreezeError("the pilot input has a duplicate JSON key")
        return dict(pairs)

    try:
        json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
        draft = PilotInput.model_validate_json(data, strict=True)
    except (UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, FreezeError):
            raise
        detail = exc if isinstance(exc, ValidationError) else type(exc).__name__
        raise FreezeError(f"the pilot input is invalid: {detail}") from None
    return draft, data


# ---- building and validating the stage ------------------------------------------------------


def manifest_versions(rubric: str) -> ManifestVersions:
    return ManifestVersions(
        rubric=rubric,
        pool_policy=POOL_POLICY_VERSION,
        blind_order=BLIND_ORDER_VERSION,
        consistency_sample=CONSISTENCY_SAMPLE_VERSION,
        parser="qu-1",
        lexicon="1",
        filter_policy=FILTER_POLICY_VERSION,
        embedding_model=ALL_MINILM_L6_V2.model_id,
        embedding_revision=ALL_MINILM_L6_V2.revision,
        reranker_model=MS_MARCO_MINILM_L6_V2.model_id,
        reranker_revision=MS_MARCO_MINILM_L6_V2.revision,
    )


def validate_stage(
    records: QueryStageRecords, extraction: list[QueryExtraction], catalog: CatalogIndex
) -> None:
    validate_queries(records.queries)
    validate_gold_constraints(records.queries, records.constraints, catalog)
    validate_product_intents(records.queries, records.intents, catalog)
    validate_attestation(records.queries, records.constraints, records.intents, records.attestation)
    validate_query_extraction(records.queries, records.constraints, records.intents, extraction)
    validate_pilot(records.queries, records.constraints, records.intents, catalog)


def build_stage(
    draft: PilotInput,
    input_bytes: bytes,
    *,
    annotator: str,
    confirmed_at: datetime,
    catalog: CatalogIndex,
) -> tuple[dict[str, bytes], Manifest]:
    """Every file of the stage (path -> bytes, manifest included) after full validation."""
    try:
        records = build_records(
            draft,
            annotator=annotator,
            confirmed_at=confirmed_at,
            input_sha256=sha256_hex(input_bytes),
        )
    except ValidationError as exc:
        raise FreezeError(f"the pilot input does not build valid records: {exc}") from None
    extraction = derive_query_extraction(records.queries, records.constraints, records.intents)
    validate_stage(records, extraction, catalog)
    data: dict[Role, bytes] = {
        Role.QUERIES: encode_jsonl(records.queries),
        Role.GOLD_CONSTRAINTS: encode_jsonl(records.constraints),
        Role.PRODUCT_INTENTS: encode_jsonl(records.intents),
        Role.QUERY_EXTRACTION: encode_jsonl(extraction),
        Role.AUTHORING_ATTESTATION: encode_json_object(records.attestation),
    }
    entries = tuple(
        sorted(
            (entry_for(role, ARTIFACT_PATHS[role], data[role]) for role in data),
            key=lambda e: e.path,
        )
    )
    manifest = build_manifest(
        golden_version=GOLDEN_VERSION,
        stage=Stage.QUERIES_FROZEN,
        dataset_id=catalog.dataset_id,
        dataset_version=catalog.dataset_version,
        dataset_checksum_sha256=catalog.checksum_sha256,
        versions=manifest_versions(draft.rubric_version),
        entries=entries,
    )
    files = {ARTIFACT_PATHS[role]: content for role, content in data.items()}
    files[MANIFEST_PATH] = encode_manifest(manifest)
    return files, manifest


# ---- atomic, exclusive writing --------------------------------------------------------------


def write_exclusive(
    golden_dir: Path,
    files: Mapping[str, bytes],
    *,
    link: Callable[[Path, Path], None] = os.link,
) -> None:
    """Write every file or none. Targets must not exist (not even as broken links); files are
    staged and fsynced in a temporary directory inside `golden_dir`, then hard-linked into place
    with exclusive creation (the manifest last). A failure removes every target this call
    created, its staging directory and any directory it created."""
    ordered = sorted(files, key=lambda p: (p == MANIFEST_PATH, p))
    targets = {relative: _safe_child(golden_dir, relative) for relative in ordered}
    existing = [str(t) for t in targets.values() if os.path.lexists(t)]
    if existing:
        raise FreezeError(f"refusing to overwrite frozen artifacts: {existing}")
    created_dirs: list[Path] = []
    placed: list[Path] = []
    staging = Path(tempfile.mkdtemp(prefix=".freeze-staging-", dir=golden_dir))
    try:
        for index, relative in enumerate(ordered):
            staged = staging / f"{index:02d}"
            with open(staged, "xb") as handle:
                handle.write(files[relative])
                handle.flush()
                os.fsync(handle.fileno())
        for index, relative in enumerate(ordered):
            target = targets[relative]
            parent = target.parent
            if not parent.exists():
                parent.mkdir()
                created_dirs.append(parent)
            link(staging / f"{index:02d}", target)  # fails if the target appeared meanwhile
            placed.append(target)
    except BaseException:
        for target in reversed(placed):
            target.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---- commands -------------------------------------------------------------------------------


def freeze(
    golden_dir: Path,
    *,
    annotator: str,
    confirm_human_review: bool,
    input_sha256: str,
    allowed_root: Path = ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    link: Callable[[Path, Path], None] = os.link,
) -> Manifest:
    """The human confirmation step. Refuses unless the approved annotator explicitly confirms
    having reviewed the exact input bytes (by their SHA-256)."""
    if not confirm_human_review:
        raise FreezeError("refused: --confirm-human-review is the annotator's own confirmation")
    if annotator != SINGLE_ANNOTATOR_HANDLE:
        raise FreezeError(f"refused: only {SINGLE_ANNOTATOR_HANDLE!r} can confirm the pilot")
    golden_dir = safe_golden_dir(golden_dir, allowed_root)
    draft, data = read_input(golden_dir)
    if sha256_hex(data) != input_sha256:
        raise FreezeError("refused: the input file differs from the reviewed SHA-256")
    files, manifest = build_stage(
        draft,
        data,
        annotator=annotator,
        confirmed_at=now(),
        catalog=catalog or load_catalog_index(),
    )
    write_exclusive(golden_dir, files, link=link)
    return manifest


def _read_frozen(golden_dir: Path, relative: str) -> bytes:
    path = _safe_child(golden_dir, relative)
    if not path.is_file():
        raise FreezeError(f"missing frozen file: {path}")
    return path.read_bytes()


def verify(
    golden_dir: Path,
    *,
    allowed_root: Path = ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> Manifest:
    """Read-only: the manifest, every entry's bytes, the regenerated extraction, the input
    binding and every stage validator."""
    golden_dir = safe_golden_dir(golden_dir, allowed_root)
    catalog = catalog or load_catalog_index()
    manifest = decode_manifest(_read_frozen(golden_dir, MANIFEST_PATH))
    if manifest.stage is not Stage.QUERIES_FROZEN or manifest.golden_version != GOLDEN_VERSION:
        raise FreezeError("expected the version 1 queries_frozen manifest")
    if (manifest.dataset_id, manifest.dataset_version, manifest.dataset_checksum_sha256) != (
        catalog.dataset_id,
        catalog.dataset_version,
        catalog.checksum_sha256,
    ):
        raise FreezeError("the manifest names another catalog dataset")
    content: dict[Role, bytes] = {}
    for entry in manifest.entries:
        if entry.path != ARTIFACT_PATHS[entry.role]:
            raise FreezeError(f"unexpected path for {entry.role.value}: {entry.path}")
        content[entry.role] = _read_frozen(golden_dir, entry.path)
        verify_entry(entry, content[entry.role])
    queries = decode_jsonl(content[Role.QUERIES], QueryRecord)
    constraints = decode_jsonl(content[Role.GOLD_CONSTRAINTS], GoldConstraint)
    intents = decode_jsonl(content[Role.PRODUCT_INTENTS], ProductIntent)
    attestation = decode_json_object(content[Role.AUTHORING_ATTESTATION], AuthoringAttestation)
    extraction = derive_query_extraction(queries, constraints, intents)
    verify_entry(
        manifest.entry(Role.QUERY_EXTRACTION),
        content[Role.QUERY_EXTRACTION],
        regenerated=encode_jsonl(extraction),
    )
    _, input_bytes = read_input(golden_dir)
    if sha256_hex(input_bytes) != attestation.input_sha256:
        raise FreezeError("the pilot input changed after it was confirmed")
    records = QueryStageRecords(queries, constraints, intents, attestation)
    validate_stage(records, extraction, catalog)
    if manifest.versions != manifest_versions(manifest.versions.rubric):
        raise FreezeError("the manifest versions differ from the frozen system pins")
    return manifest


def preview_text(golden_dir: Path, *, allowed_root: Path = ALLOWED_ROOT) -> str:
    """The table and generated bytes the annotator reviews. Uses a placeholder time only to run
    validation; nothing is written and the placeholder never reaches a file."""
    golden_dir = safe_golden_dir(golden_dir, allowed_root)
    draft, data = read_input(golden_dir)
    catalog = load_catalog_index()
    placeholder = datetime(2000, 1, 1, tzinfo=UTC)
    files, _ = build_stage(
        draft, data, annotator=SINGLE_ANNOTATOR_HANDLE, confirmed_at=placeholder, catalog=catalog
    )
    records = build_records(
        draft, annotator=SINGLE_ANNOTATOR_HANDLE, confirmed_at=placeholder, input_sha256="0" * 64
    )
    lines = [f"input: {INPUT_PATH}", f"input_sha256: {sha256_hex(data)}", ""]
    lines.append("| ID | Query | Style | Family | Seen | Hard | Soft / intent | Hard-eligible |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for q in records.queries:
        own = [c for c in records.constraints if c.query_id == q.query_id]
        hard = ", ".join(
            f"{c.field.value} {c.operator.value} {canonical_gold_value(c.value)}"
            for c in own
            if c.hardness == "hard"
        )
        soft = [
            f"{c.field.value}={canonical_gold_value(c.value)}" for c in own if c.hardness == "soft"
        ]
        soft += [
            f"{i.kind.value}={i.value} (exact series/model)"
            for i in records.intents
            if i.query_id == q.query_id
        ]
        eligible = len(summarize(q.query_id, records.constraints, catalog).satisfied)
        lines.append(
            f"| {q.query_id} | {q.text} | {q.language_style.value} | {q.intent_family.value} | "
            f"{str(q.seen_in_development).lower()} | {hard or '-'} | {', '.join(soft) or '-'} | "
            f"{eligible if own else 'n/a'} |"
        )
    lines += ["", f"generated {ARTIFACT_PATHS[Role.QUERY_EXTRACTION]}:", ""]
    lines.append(files[ARTIFACT_PATHS[Role.QUERY_EXTRACTION]].decode("utf-8"))
    lines.append(
        "recorded files get annotator_handle ann-1 and the real confirmation time when the "
        "annotator runs `freeze`."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.freeze_queries")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preview", help="validate the input and print it; writes nothing")
    commands.add_parser("verify", help="re-verify a frozen stage; writes nothing")
    run = commands.add_parser("freeze", help="human confirmation: freeze queries_frozen")
    run.add_argument("--annotator", required=True)
    run.add_argument("--confirm-human-review", action="store_true")
    run.add_argument("--input-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview":
            print(preview_text(args.golden_dir))
        elif args.command == "verify":
            manifest = verify(args.golden_dir)
            print(f"verified queries_frozen manifest {manifest.manifest_sha256}")
        else:
            manifest = freeze(
                args.golden_dir,
                annotator=args.annotator,
                confirm_human_review=args.confirm_human_review,
                input_sha256=args.input_sha256,
            )
            print(f"froze queries_frozen; manifest_sha256 {manifest.manifest_sha256}")
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
