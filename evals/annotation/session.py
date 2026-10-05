"""The primary-round annotation session: verified pool in, human judgements out.

The working file is `primary_judgements.jsonl` (canonical JSONL of `Judgement`, round
`primary`) in a work directory bound to one `pool_frozen` manifest by `binding.json`. After every
judgement the whole file is rewritten to a temporary file, fsynced, and atomically replaced, so
an interruption leaves either the previous or the new complete file; resuming re-validates every
stored record. The work directory defaults to the git-ignored
`data/processed/golden_annotation/`. The working file only becomes a frozen artifact when the
annotator seals the primary round (`evals.runners.seal_primary`).

Refusals: a pool that does not verify, a product whose catalog content or card changed, a work
directory bound to another pool, a foreign handle, an unknown pair, a duplicate judgement, a
non-primary round, a wrong rubric version, a timestamp before the queries were confirmed, before
the binding's `created_at` or in the future, and `relevant` for a product that violates a hard
gold constraint.

`binding.json` records `created_at` (UTC) once, when the work directory is initialized, and it
never changes on resume. Every judgement must be at or after both the query confirmation time
and `created_at`. This guards against accidental backdating before the primary seal; it is not
cryptographic tamper-proofing (the working file and binding are ordinary local files until the
annotator seals them, and the seal then binds the exact bytes by SHA-256).

Only one annotation session may write a work directory at a time: `annotation_lock` creates
`annotation.lock` with exclusive creation before any judgement is read, and removes it on normal
exit, on a handled error and on KeyboardInterrupt. A lock left behind by a hard kill is never
removed automatically: every later session (and the seal) refuses to start until the annotator
has checked that no session is running and deleted the lock by hand.
"""

import contextlib
import json
import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evals.contracts.canonical import (
    ContractError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
)
from evals.contracts.catalog import ROOT, CatalogIndex, load_catalog_index
from evals.contracts.constants import SINGLE_ANNOTATOR_HANDLE
from evals.contracts.manifest import Manifest, Role
from evals.contracts.models import (
    AuthoringAttestation,
    EligibilityRecord,
    GoldConstraint,
    Judgement,
    OverallStatus,
    PoolItem,
    ProductIntent,
    QueryRecord,
    Round,
    Sha256,
    UtcTimestamp,
)
from evals.contracts.validation import validate_judgements_against_eligibility
from evals.runners import build_pool, freeze_queries
from evals.runners.golden_io import read_frozen, refuse_stale_staging

WORK_ROOT = ROOT / "data" / "processed" / "golden_annotation"
WORK_FILE = "primary_judgements.jsonl"
BINDING_FILE = "binding.json"
LOCK_FILE = "annotation.lock"
PRIMARY_SEALED_MANIFEST = "manifests/3-primary_sealed.json"

Pair = tuple[str, str]


class AnnotationError(ContractError):
    """Annotation cannot start or a judgement cannot be stored."""


class WorkBinding(BaseModel):
    """Binds a work directory to one exact pool and the one annotator, and records when the
    annotation work was initialized (`created_at`, UTC, set once)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    pool_manifest_sha256: Sha256
    pool_file_sha256: Sha256
    annotator_handle: Literal["ann-1"] = "ann-1"
    round: Literal["primary"] = "primary"
    rubric_version: str
    created_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.pool_manifest_sha256,)


class LockInfo(BaseModel):
    """Diagnostic content of `annotation.lock` (no path, name or secret)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pool_manifest_sha256: Sha256
    annotator_handle: Literal["ann-1"] = "ann-1"
    pid: int
    started_at: UtcTimestamp


@dataclass(frozen=True)
class PoolContext:
    """A verified, frozen pool and everything the annotation view may show."""

    pool_manifest: Manifest
    queries: dict[str, QueryRecord]
    constraints: tuple[GoldConstraint, ...]
    intents: tuple[ProductIntent, ...]
    items: tuple[PoolItem, ...]  # presentation order: query id, then blind position
    eligibility: dict[Pair, EligibilityRecord]
    catalog: CatalogIndex
    rubric_version: str
    queries_confirmed_at: datetime

    @property
    def pairs(self) -> set[Pair]:
        return {(i.query_id, i.product_id) for i in self.items}


def load_pool(
    golden_dir: Path = build_pool.DEFAULT_GOLDEN_DIR,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> PoolContext:
    """Read only a verified `pool_frozen` stage (public files, catalog content and card hashes
    re-checked); the private provenance is never read here."""
    catalog = catalog or load_catalog_index()
    verified = build_pool.verify_pool(
        golden_dir, allowed_root=allowed_root, public_only=True, catalog=catalog
    )
    golden = freeze_queries.safe_golden_dir(golden_dir, allowed_root)
    if os.path.lexists(golden / PRIMARY_SEALED_MANIFEST):
        raise AnnotationError("the primary round is already sealed; it is never re-annotated")
    queries = decode_jsonl(read_frozen(golden, "queries.jsonl"), QueryRecord)
    constraints = decode_jsonl(read_frozen(golden, "gold_constraints.jsonl"), GoldConstraint)
    intents = decode_jsonl(read_frozen(golden, "product_intents.jsonl"), ProductIntent)
    attestation = decode_json_object(
        read_frozen(golden, "authoring_attestation.json"), AuthoringAttestation
    )
    pool = decode_jsonl(read_frozen(golden, build_pool.POOL_PATHS[Role.POOL]), PoolItem)
    eligibility = decode_jsonl(
        read_frozen(golden, build_pool.POOL_PATHS[Role.ELIGIBILITY]), EligibilityRecord
    )
    return PoolContext(
        pool_manifest=verified.manifest,
        queries={q.query_id: q for q in queries},
        constraints=tuple(constraints),
        intents=tuple(intents),
        items=tuple(sorted(pool, key=lambda i: (i.query_id, i.blind_position))),
        eligibility={(e.query_id, e.product_id): e for e in eligibility},
        catalog=catalog,
        rubric_version=verified.manifest.versions.rubric,
        queries_confirmed_at=attestation.confirmed_at,
    )


def validate_records(
    context: PoolContext,
    judgements: Sequence[Judgement],
    *,
    now: datetime,
    binding_created_at: datetime,
) -> None:
    """Every stored primary judgement is the approved annotator's, for a pooled pair, at most
    once, under the frozen rubric, timed at or after both the query confirmation and the work
    binding's `created_at` and not in the future, and consistent with the hard-constraint
    rule."""
    if binding_created_at < context.queries_confirmed_at or binding_created_at > now:
        raise AnnotationError("the work binding's created_at is outside the allowed window")
    not_before = max(context.queries_confirmed_at, binding_created_at)
    seen: set[Pair] = set()
    pairs = context.pairs
    for j in judgements:
        pair = (j.query_id, j.product_id)
        if j.round is not Round.PRIMARY:
            raise AnnotationError(f"{pair!r}: only primary judgements belong here")
        if j.annotator_handle != SINGLE_ANNOTATOR_HANDLE:
            raise AnnotationError(f"{pair!r}: judgement from a foreign handle")
        if pair not in pairs:
            raise AnnotationError(f"{pair!r}: not a pair of the frozen pool")
        if pair in seen:
            raise AnnotationError(f"{pair!r}: duplicate judgement")
        seen.add(pair)
        if j.rubric_version != context.rubric_version:
            raise AnnotationError(f"{pair!r}: rubric {j.rubric_version} is not the frozen rubric")
        if j.annotated_at < not_before or j.annotated_at > now:
            raise AnnotationError(f"{pair!r}: annotated_at is outside the allowed window")
    validate_judgements_against_eligibility(
        judgements, [context.eligibility[(j.query_id, j.product_id)] for j in judgements]
    )


class WorkStore:
    """The atomically replaced working file of one pool's primary round."""

    def __init__(self, context: PoolContext, work_root: Path = WORK_ROOT) -> None:
        self.context = context
        self.directory = work_root / context.pool_manifest.manifest_sha256[:16]
        self.path = self.directory / WORK_FILE
        self.binding_path = self.directory / BINDING_FILE
        self.lock_path = self.directory / LOCK_FILE

    def expected_binding(self, created_at: datetime) -> WorkBinding:
        return WorkBinding(
            pool_manifest_sha256=self.context.pool_manifest.manifest_sha256,
            pool_file_sha256=self.context.pool_manifest.entry(Role.POOL).file_sha256,
            rubric_version=self.context.rubric_version,
            created_at=created_at,
        )

    def read_binding(self, *, now: datetime) -> WorkBinding | None:
        """The stored binding (None before initialization), checked against this pool, the
        annotator and the rubric, with `created_at` inside the allowed window."""
        if self.directory.exists():
            refuse_stale_staging(self.directory)
        if not self.binding_path.exists():
            if self.path.exists():
                raise AnnotationError("a working file exists without its pool binding")
            return None
        stored = decode_json_object(self.binding_path.read_bytes(), WorkBinding)
        if stored != self.expected_binding(stored.created_at):
            raise AnnotationError(
                "the working file is bound to another pool, annotator or rubric; refusing"
            )
        if stored.created_at < self.context.queries_confirmed_at or stored.created_at > now:
            raise AnnotationError("the work binding's created_at is outside the allowed window")
        return stored

    def initialize(self, *, now: datetime) -> WorkBinding:
        """Create the binding exactly once (exclusive creation); an existing binding is kept
        unchanged, so `created_at` survives every resume."""
        existing = self.read_binding(now=now)
        if existing is not None:
            return existing
        binding = self.expected_binding(now)
        self.directory.mkdir(parents=True, exist_ok=True)
        _exclusive_write(self.binding_path, encode_json_object(binding))
        return binding

    def load(self, *, now: datetime) -> list[Judgement]:
        binding = self.read_binding(now=now)
        if binding is None or not self.path.exists():
            return []
        judgements = decode_jsonl(self.path.read_bytes(), Judgement)
        validate_records(self.context, judgements, now=now, binding_created_at=binding.created_at)
        return judgements

    def save(self, judgements: Iterable[Judgement], *, now: datetime) -> None:
        binding = self.read_binding(now=now)
        if binding is None:
            raise AnnotationError("annotation work is not initialized (no binding)")
        records = sorted(judgements, key=lambda j: j.sort_key())
        validate_records(self.context, records, now=now, binding_created_at=binding.created_at)
        _atomic_write(self.path, encode_jsonl(records))


def _exclusive_write(path: Path, data: bytes) -> None:
    try:
        with open(path, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise AnnotationError(f"refusing to overwrite {path.name}") from None


def stale_lock_message(store: WorkStore) -> str:
    try:
        detail = json.loads(store.lock_path.read_text(encoding="utf-8"))
        shown = ", ".join(f"{k}={detail.get(k)}" for k in ("pid", "started_at", "annotator_handle"))
    except (OSError, ValueError):
        shown = "unreadable"
    return (
        f"another annotation session holds {store.lock_path} ({shown}). Only one session may "
        "write a work directory. If no annotation session is running (for example after a hard "
        "kill), check the process id above, confirm `validate` passes, then delete the lock "
        "file by hand and rerun. It is never removed automatically."
    )


@contextlib.contextmanager
def annotation_lock(store: WorkStore, *, annotator: str, now: datetime) -> Iterator[LockInfo]:
    """Hold the work directory's exclusive annotation lock for one session."""
    info = LockInfo(
        pool_manifest_sha256=store.context.pool_manifest.manifest_sha256,
        annotator_handle=annotator,
        pid=os.getpid(),
        started_at=now,
    )
    store.directory.mkdir(parents=True, exist_ok=True)
    try:
        with open(store.lock_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(info.model_dump(mode="json"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise AnnotationError(stale_lock_message(store)) from None
    try:
        yield info
    finally:
        store.lock_path.unlink(missing_ok=True)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pending(context: PoolContext, judgements: Sequence[Judgement]) -> list[PoolItem]:
    done = {(j.query_id, j.product_id) for j in judgements}
    return [i for i in context.items if (i.query_id, i.product_id) not in done]


def hard_violation(context: PoolContext, item: PoolItem) -> bool:
    return (
        context.eligibility[(item.query_id, item.product_id)].hard_overall is OverallStatus.VIOLATED
    )


def hard_unknown(context: PoolContext, item: PoolItem) -> bool:
    return (
        context.eligibility[(item.query_id, item.product_id)].hard_overall is OverallStatus.UNKNOWN
    )
