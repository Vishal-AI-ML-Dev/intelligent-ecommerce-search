"""ann-1's targeted single-annotator adjudication under review-2 (M9).

This is adjudication, not a blind round: for each pair the annotator sees the query, the
product card, the hard-constraint status, the sealed primary judgement and the exact reasons the
pair was queued, then records an adjudication judgement under the unchanged review-1 rules.

Scope: exactly the pairs of the sealed targeted queue (`adjudication_queue.jsonl`) that are NOT
in the sealed joint-resolution queue. A pair in the joint-resolution queue is resolved jointly
by both annotators and is refused here (`validate_review_2_adjudication` refuses a
single-annotator decision for it). Under the review-2 scope revision (approved 2026-10-16,
`review_sets.split_review_2_targeted`) these pairs are split into:

* MANDATORY pairs: any reason other than a borderline primary label or an ambiguous query
  (ann-1's needs-adjudication or catalog-issue flag, low confidence, an unknown hard-constraint
  attribute). Each blocks the label freeze until adjudicated. `annotate` asks only these by
  default.
* OPTIONAL review candidates: selected only because the primary label is borderline or the
  query is ambiguous. `annotate --include-optional` offers them after every mandatory pair; one
  that is never adjudicated keeps its sealed primary judgement as the final label and is not
  reported as resolved or re-reviewed.

Inputs are read only from the verified chain: `load_sealed` re-verifies everything up to
`primary_sealed` (including the primary judgements and the byte-identical regeneration of the
sample and the targeted queue). Then the `second_review_sealed` manifest is checked as the next
legal transition, every entry's hashes are verified, the second-review round is re-validated
and the agreement report and the joint-resolution queue must regenerate byte for byte. No later
manifest may exist. Nothing in the golden directory is ever written.

Work directory (git-ignored, `data/processed/golden_adjudication/<manifest sha[:16]>/`):

* `binding.json`: binds the work to the sealed second-review manifest, the primary judgements,
  both queues, `ann-1`, the rubric and the golden version, with a `created_at` (written once,
  never before the second-review seal);
* `adjudication_judgements.jsonl`: canonical `Judgement` records (round `adjudication`),
  atomically replaced after every confirmed decision;
* `label_changes.jsonl`: the `adjudication` `LabelChange` records the contract requires, one per
  changed label and none otherwise, written after the judgements. Each one only restates its
  confirmed judgement (previous label, new label, the adjudication reason, `ann-1`, the same
  time), so it is derived from the judgements; if a save was interrupted between the two files,
  the next locked session rewrites it from the confirmed judgements and says so;
* `adjudication.lock` while a session runs (exclusive; never removed automatically).

Each decision is asked step by step with nothing prefilled or suggested: label, relation,
confidence, rationale (required for borderline, low confidence, and relevant with an unknown
hard attribute), the adjudication reason (required when the label changes; optional when it is
retained), catalog issue, then a summary saved only on an explicit `y`. Retaining the primary
label means choosing it again. The review reasons recorded are exactly the queued reasons;
`changes_label` is whether the label differs from the primary label; `needs_adjudication` is
always false (an adjudication cannot itself need adjudication).

Usage:
  uv run python -m evals.annotation.adjudication status
  uv run python -m evals.annotation.adjudication validate
  uv run python -m evals.annotation.adjudication annotate --annotator ann-1
  uv run python -m evals.annotation.adjudication annotate --annotator ann-1 --include-optional
"""

import argparse
import contextlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from evals.annotation.reviewer_bundle import SealedPrimary, load_sealed
from evals.contracts.canonical import (
    ContractError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
)
from evals.contracts.cards import render_card
from evals.contracts.catalog import ROOT, CatalogIndex
from evals.contracts.constants import (
    ALLOWED_RELATIONS,
    REVIEW_PROTOCOL_2,
    SECOND_REVIEWER_HANDLE,
    SINGLE_ANNOTATOR_HANDLE,
)
from evals.contracts.manifest import (
    Manifest,
    Role,
    Stage,
    decode_manifest,
    validate_transition,
    verify_entry,
)
from evals.contracts.models import (
    AgreementReport,
    Confidence,
    FreeText,
    JointResolutionItem,
    Judgement,
    Label,
    LabelChange,
    LabelChangeKind,
    OverallStatus,
    QueueItem,
    Relation,
    Round,
    Sha256,
    UtcTimestamp,
    canonical_gold_value,
)
from evals.contracts.review_sets import build_joint_resolution_queue, split_review_2_targeted
from evals.contracts.validation import (
    validate_adjudication_round,
    validate_judgements_against_eligibility,
    validate_second_review_round,
    validate_single_annotator,
)
from evals.metrics.agreement import build_agreement_report
from evals.runners import build_pool, freeze_queries, seal_primary, seal_second_review
from evals.runners.golden_io import read_frozen, refuse_stale_staging

WORK_ROOT = ROOT / "data" / "processed" / "golden_adjudication"
WORK_FILE = "adjudication_judgements.jsonl"
CHANGES_FILE = "label_changes.jsonl"
BINDING_FILE = "binding.json"
LOCK_FILE = "adjudication.lock"
# The complete chain this runner works on; any further manifest (a freeze) refuses it.
SEALED_CHAIN = (
    freeze_queries.MANIFEST_PATH,
    build_pool.POOL_MANIFEST_PATH,
    seal_primary.SEALED_MANIFEST_PATH,
    seal_second_review.SECOND_REVIEW_MANIFEST_PATH,
)
QUIT = "q"
LABEL_KEYS = {"r": Label.RELEVANT, "b": Label.BORDERLINE, "n": Label.NOT_RELEVANT}
CONFIDENCE_KEYS = {"h": Confidence.HIGH, "m": Confidence.MEDIUM, "l": Confidence.LOW}
HARD_VIOLATION_MESSAGE = (
    "relevant is not allowed: this product violates a hard gold constraint of the query "
    "(rubric rule). Choose borderline (substitute) or not_relevant."
)
_FREE_TEXT = TypeAdapter(FreeText)

Pair = tuple[str, str]
Ask = Callable[[str], str]
Say = Callable[[str], None]
Now = Callable[[], datetime]


class AdjudicationError(ContractError):
    """Adjudication cannot start or a decision cannot be stored; nothing was written."""


class Stop(Exception):
    """The annotator stopped (q or end of input); everything confirmed is already saved."""


class AdjudicationBinding(BaseModel):
    """Binds a work directory to one sealed second-review stage and to `ann-1`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    second_review_manifest_sha256: Sha256
    primary_judgements_sha256: Sha256
    adjudication_queue_sha256: Sha256
    joint_resolution_queue_sha256: Sha256
    annotator_handle: Literal["ann-1"] = "ann-1"
    round: Literal["adjudication"] = "adjudication"
    rubric_version: str
    golden_version: int
    created_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.second_review_manifest_sha256,)


class LockInfo(BaseModel):
    """Diagnostic content of `adjudication.lock` (no path, name or secret)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    second_review_manifest_sha256: Sha256
    annotator_handle: Literal["ann-1"] = "ann-1"
    pid: int
    started_at: UtcTimestamp


@dataclass(frozen=True)
class TargetedContext:
    """The verified sealed stage and the ann-1-only targeted items, in queue order."""

    sealed: SealedPrimary
    manifest: Manifest  # second_review_sealed
    queue: tuple[QueueItem, ...]  # the whole sealed targeted queue
    joint: dict[Pair, JointResolutionItem]
    items: tuple[QueueItem, ...]  # the queue minus every joint-resolution pair
    mandatory: tuple[QueueItem, ...]  # items that block the freeze until adjudicated
    optional: tuple[QueueItem, ...]  # review candidates only (borderline / ambiguous query)
    primaries: dict[Pair, Judgement]
    second_review_sealed_at: datetime
    rubric_version: str
    golden_version: int

    @property
    def pairs(self) -> dict[Pair, QueueItem]:
        return {(i.query_id, i.product_id): i for i in self.items}


def load_targeted(
    golden_dir: Path = build_pool.DEFAULT_GOLDEN_DIR,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> TargetedContext:
    sealed = load_sealed(
        golden_dir, allowed_root=allowed_root, catalog=catalog, primary_labels=True
    )
    golden = sealed.golden_dir
    present = sorted(p.name for p in (golden / "manifests").iterdir())
    if present != sorted(Path(p).name for p in SEALED_CHAIN):
        raise AdjudicationError(
            "expected exactly the sealed chain through second_review_sealed and no later stage; "
            f"found {present!r}"
        )
    manifest = decode_manifest(read_frozen(golden, seal_second_review.SECOND_REVIEW_MANIFEST_PATH))
    if manifest.stage is not Stage.SECOND_REVIEW_SEALED or (
        manifest.review_protocol != REVIEW_PROTOCOL_2
    ):
        raise AdjudicationError("expected the review-2 second_review_sealed manifest")
    validate_transition(sealed.manifest, manifest)
    files = {e.role: read_frozen(golden, e.path) for e in manifest.entries}
    for entry in manifest.entries:
        verify_entry(entry, files[entry.role])
    primaries = sealed.primaries or ()
    second = decode_jsonl(files[Role.SECOND_REVIEW_JUDGEMENTS], Judgement)
    report = decode_json_object(files[Role.AGREEMENT_REPORT], AgreementReport)
    validate_second_review_round(
        primaries,
        sealed.sample,
        second,
        list(sealed.eligibility.values()),
        manifest.primary_seal_sha256(),
        manifest.primary_sealed_at,  # type: ignore[arg-type]
        report.second_review_sealed_at,
    )
    regenerated_report = build_agreement_report(
        primaries,
        sealed.sample,
        second,
        primary_annotator=validate_single_annotator(primaries),
        second_annotator=SECOND_REVIEWER_HANDLE,
        second_review_sha256=manifest.entry(Role.SECOND_REVIEW_JUDGEMENTS).file_sha256,
        second_review_sealed_at=report.second_review_sealed_at,
    )
    verify_entry(
        manifest.entry(Role.AGREEMENT_REPORT),
        files[Role.AGREEMENT_REPORT],
        regenerated=encode_json_object(regenerated_report),
    )
    joint = build_joint_resolution_queue(primaries, second)
    verify_entry(
        manifest.entry(Role.JOINT_RESOLUTION_QUEUE),
        files[Role.JOINT_RESOLUTION_QUEUE],
        regenerated=encode_jsonl(joint),
    )
    queue = tuple(decode_jsonl(files[Role.ADJUDICATION_QUEUE], QueueItem))
    joint_pairs = {(i.query_id, i.product_id): i for i in joint}
    items = tuple(i for i in queue if (i.query_id, i.product_id) not in joint_pairs)
    mandatory, optional = split_review_2_targeted(queue, joint)
    return TargetedContext(
        sealed=sealed,
        manifest=manifest,
        queue=queue,
        joint=joint_pairs,
        items=items,
        mandatory=tuple(mandatory),
        optional=tuple(optional),
        primaries={(j.query_id, j.product_id): j for j in primaries},
        second_review_sealed_at=report.second_review_sealed_at,
        rubric_version=manifest.versions.rubric,
        golden_version=manifest.golden_version,
    )


def derive_label_changes(
    context: TargetedContext, judgements: Sequence[Judgement]
) -> list[LabelChange]:
    """The `adjudication` LabelChange the contract requires for each changed label (none for a
    retained label). Every field restates the confirmed judgement; nothing is added."""
    changes = []
    for j in judgements:
        if not j.changes_label:
            continue
        changes.append(
            LabelChange(
                query_id=j.query_id,
                product_id=j.product_id,
                kind=LabelChangeKind.ADJUDICATION,
                previous_label=context.primaries[(j.query_id, j.product_id)].label,
                new_label=j.label,
                reason=j.adjudication_reason,  # type: ignore[arg-type]
                changed_by=j.annotator_handle,
                changed_at=j.annotated_at,
                from_golden_version=context.golden_version,
                to_golden_version=context.golden_version,
            )
        )
    return sorted(changes, key=lambda c: c.sort_key())


def validate_records(
    context: TargetedContext,
    judgements: Sequence[Judgement],
    *,
    now: datetime,
    binding_created_at: datetime,
) -> list[LabelChange]:
    """Every stored adjudication is ann-1's, for an ann-1-only targeted pair, at most once,
    under the primary rubric, with exactly the queued reasons, timed inside the work window,
    and passes the unchanged review-1 adjudication contract. Returns the label changes."""
    if binding_created_at < context.second_review_sealed_at or binding_created_at > now:
        raise AdjudicationError("the work binding's created_at is outside the allowed window")
    targeted = context.pairs
    seen: set[Pair] = set()
    for j in judgements:
        pair = (j.query_id, j.product_id)
        if pair in context.joint:
            raise AdjudicationError(
                f"{pair!r}: refused; this pair is in the joint-resolution queue and is resolved "
                "jointly by both annotators"
            )
        if pair not in targeted:
            raise AdjudicationError(f"{pair!r}: not an ann-1-only targeted pair")
        if pair in seen:
            raise AdjudicationError(f"{pair!r}: duplicate adjudication")
        seen.add(pair)
        if j.round is not Round.ADJUDICATION:
            raise AdjudicationError(f"{pair!r}: only adjudication judgements belong here")
        if j.annotator_handle != SINGLE_ANNOTATOR_HANDLE:
            raise AdjudicationError(f"{pair!r}: adjudication from a foreign handle")
        if j.rubric_version != context.primaries[pair].rubric_version:
            raise AdjudicationError(f"{pair!r}: rubric differs from the primary judgement")
        if j.review_reasons != targeted[pair].review_reasons:
            raise AdjudicationError(f"{pair!r}: review reasons differ from the queued reasons")
        if j.annotated_at < binding_created_at or j.annotated_at > now:
            raise AdjudicationError(f"{pair!r}: annotated_at is outside the allowed window")
    validate_judgements_against_eligibility(
        judgements, [context.sealed.eligibility[(j.query_id, j.product_id)] for j in judgements]
    )
    changes = derive_label_changes(context, judgements)
    validate_adjudication_round(
        tuple(context.primaries.values()),
        (),
        context.queue,
        judgements,
        changes,
        context.golden_version,
        primary_sealed_at=context.manifest.primary_sealed_at,  # type: ignore[arg-type]
    )
    return changes


class AdjudicationStore:
    """The atomically replaced working files of one sealed stage's targeted adjudication."""

    def __init__(self, context: TargetedContext, work_root: Path = WORK_ROOT) -> None:
        self.context = context
        self.directory = work_root / context.manifest.manifest_sha256[:16]
        self.path = self.directory / WORK_FILE
        self.changes_path = self.directory / CHANGES_FILE
        self.binding_path = self.directory / BINDING_FILE
        self.lock_path = self.directory / LOCK_FILE

    def expected_binding(self, created_at: datetime) -> AdjudicationBinding:
        manifest = self.context.manifest
        return AdjudicationBinding(
            second_review_manifest_sha256=manifest.manifest_sha256,
            primary_judgements_sha256=manifest.entry(Role.PRIMARY_JUDGEMENTS).file_sha256,
            adjudication_queue_sha256=manifest.entry(Role.ADJUDICATION_QUEUE).file_sha256,
            joint_resolution_queue_sha256=manifest.entry(Role.JOINT_RESOLUTION_QUEUE).file_sha256,
            rubric_version=self.context.rubric_version,
            golden_version=self.context.golden_version,
            created_at=created_at,
        )

    def read_binding(self, *, now: datetime) -> AdjudicationBinding | None:
        if self.directory.exists():
            refuse_stale_staging(self.directory)
        if not self.binding_path.exists():
            if self.path.exists() or self.changes_path.exists():
                raise AdjudicationError("adjudication files exist without their binding")
            return None
        stored = decode_json_object(self.binding_path.read_bytes(), AdjudicationBinding)
        if stored != self.expected_binding(stored.created_at):
            raise AdjudicationError(
                "the work directory is bound to another sealed stage, annotator or rubric"
            )
        if stored.created_at < self.context.second_review_sealed_at or stored.created_at > now:
            raise AdjudicationError("the work binding's created_at is outside the allowed window")
        return stored

    def initialize(self, *, now: datetime) -> AdjudicationBinding:
        existing = self.read_binding(now=now)
        if existing is not None:
            return existing
        binding = self.expected_binding(now)
        self.directory.mkdir(parents=True, exist_ok=True)
        _exclusive_write(self.binding_path, encode_json_object(binding))
        return binding

    def load(self, *, now: datetime) -> tuple[list[Judgement], list[LabelChange]]:
        """The stored adjudications (validated) and the label changes they require."""
        binding = self.read_binding(now=now)
        if binding is None:
            return [], []
        judgements = decode_jsonl(self.path.read_bytes(), Judgement) if self.path.exists() else []
        changes = validate_records(
            self.context, judgements, now=now, binding_created_at=binding.created_at
        )
        return judgements, changes

    def changes_in_sync(self, changes: Sequence[LabelChange]) -> bool:
        stored = self.changes_path.read_bytes() if self.changes_path.exists() else b""
        return stored == encode_jsonl(changes)

    def write_changes(self, changes: Sequence[LabelChange]) -> None:
        _atomic_write(self.changes_path, encode_jsonl(changes))

    def save(self, judgements: Sequence[Judgement], *, now: datetime) -> None:
        binding = self.read_binding(now=now)
        if binding is None:
            raise AdjudicationError("adjudication work is not initialized (no binding)")
        records = sorted(judgements, key=lambda j: j.sort_key())
        changes = validate_records(
            self.context, records, now=now, binding_created_at=binding.created_at
        )
        _atomic_write(self.path, encode_jsonl(records))
        self.write_changes(changes)


def _exclusive_write(path: Path, data: bytes) -> None:
    try:
        with open(path, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise AdjudicationError(f"refusing to overwrite {path.name}") from None


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


@contextlib.contextmanager
def adjudication_lock(
    store: AdjudicationStore, *, annotator: str, now: datetime
) -> Iterator[LockInfo]:
    info = LockInfo(
        second_review_manifest_sha256=store.context.manifest.manifest_sha256,
        annotator_handle=annotator,  # type: ignore[arg-type]
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
        raise AdjudicationError(
            f"another adjudication session holds {store.lock_path.name}. If none is running "
            "(for example after a hard kill), confirm `validate` passes, then delete the lock "
            "file by hand and rerun. It is never removed automatically."
        ) from None
    try:
        yield info
    finally:
        store.lock_path.unlink(missing_ok=True)


# ---- presentation and prompts ---------------------------------------------------------------


def render_pair(
    context: TargetedContext, item: QueueItem, number: int, total: int, kind: str
) -> str:
    sealed = context.sealed
    pair = (item.query_id, item.product_id)
    query = sealed.queries[item.query_id]
    primary = context.primaries[pair]
    lines = [f"--- {kind} pair {number} of {total}: {item.query_id} / {item.product_id} ---"]
    lines.append(f"query {query.query_id}: {query.text}")
    lines += [f"  interpretation: {text}" for text in query.interpretations]
    own = sorted(
        (c for c in sealed.constraints if c.query_id == item.query_id), key=lambda c: c.sort_key()
    )
    lines += [
        f"  {c.hardness.value} gold constraint: {c.field.value} {c.operator.value} "
        f"{canonical_gold_value(c.value)}"
        for c in own
    ]
    if not own:
        lines.append("  (no gold constraints recorded for this query)")
    intents = sorted(
        (i for i in sealed.intents if i.query_id == item.query_id), key=lambda i: i.sort_key()
    )
    lines += [f"  soft {i.kind.value} intent: {i.value}" for i in intents]
    lines += [f"  hard constraints overall: {sealed.eligibility[pair].hard_overall.value}", ""]
    lines.append(render_card(sealed.catalog.products[item.product_id]))
    lines += [
        "",
        "queued for: " + ", ".join(r.value for r in item.review_reasons),
        "existing ann-1 primary judgement:",
        f"  label:              {primary.label.value}",
        f"  relation:           {primary.relation.value}",
        f"  confidence:         {primary.confidence.value}",
        f"  rationale:          {primary.rationale or '(none)'}",
        f"  needs adjudication: {'yes' if primary.needs_adjudication else 'no'}",
        f"  catalog issue:      {'yes' if primary.catalog_issue else 'no'}",
    ]
    return "\n".join(lines)


def _ask(ask: Ask, prompt: str) -> str:
    try:
        text = ask(prompt).strip()
    except EOFError:
        raise Stop from None
    if text.lower() == QUIT:
        raise Stop
    return text


def _choose[T](ask: Ask, say: Say, prompt: str, keys: dict[str, T]) -> T:
    while True:
        answer = _ask(ask, prompt).lower()
        if answer in keys:
            return keys[answer]
        say(f"please type one of: {' / '.join(keys)} (q quits)")


def _yes_no(ask: Ask, say: Say, prompt: str) -> bool:
    while True:
        answer = _ask(ask, prompt).lower()
        if answer in ("y", "n", ""):
            return answer == "y"
        say("please type y or n (Enter means n; q quits)")


def _free_text(ask: Ask, say: Say, prompt: str, required: bool) -> str | None:
    while True:
        text = _ask(ask, prompt)
        if not text:
            if not required:
                return None
            say("this is required here")
            continue
        try:
            return _FREE_TEXT.validate_python(text)
        except ValidationError as exc:
            say(f"not accepted: {exc.errors()[0]['msg']}")


def _relation_keys(label: Label) -> dict[str, Relation]:
    """The label's allowed relations, numbered from 1 in name order (as in the primary CLI)."""
    relations = sorted(ALLOWED_RELATIONS[label], key=lambda r: r.value)
    return {str(i): r for i, r in enumerate(relations, start=1)}


def summary(judgement: Judgement, primary: Judgement) -> str:
    if judgement.changes_label:
        outcome = f"CORRECTED: {primary.label.value} -> {judgement.label.value}"
    else:
        outcome = f"retained: {judgement.label.value}"
    return "\n".join(
        [
            "summary of this adjudication (nothing is saved yet):",
            f"  label:               {outcome}",
            f"  relation:            {judgement.relation.value}",
            f"  confidence:          {judgement.confidence.value}",
            f"  rationale:           {judgement.rationale or '(none)'}",
            f"  adjudication reason: {judgement.adjudication_reason or '(none)'}",
            f"  catalog issue:       {'yes' if judgement.catalog_issue else 'no'}",
            "  review reasons:      " + ", ".join(r.value for r in judgement.review_reasons),
            "  label change record: " + ("yes" if judgement.changes_label else "no"),
        ]
    )


def ask_adjudication(
    context: TargetedContext, item: QueueItem, ask: Ask, say: Say, now: Now
) -> Judgement:
    """One adjudication, step by step. Every field starts unset; only an explicit `y` saves."""
    pair = (item.query_id, item.product_id)
    primary = context.primaries[pair]
    status = context.sealed.eligibility[pair].hard_overall
    while True:
        label = _choose(
            ask, say, "1 label [r]elevant / [b]orderline / [n]ot_relevant / [q]uit: ", LABEL_KEYS
        )
        if label is Label.RELEVANT and status is OverallStatus.VIOLATED:
            say(HARD_VIOLATION_MESSAGE)
            continue
        keys = _relation_keys(label)
        menu = " / ".join(f"[{k}] {r.value}" for k, r in keys.items())
        relation = _choose(ask, say, f"2 relation {menu}: ", keys)
        confidence = _choose(ask, say, "3 confidence [h]igh / [m]edium / [l]ow: ", CONFIDENCE_KEYS)
        required = (
            label is Label.BORDERLINE
            or confidence is Confidence.LOW
            or (label is Label.RELEVANT and status is OverallStatus.UNKNOWN)
        )
        rationale = _free_text(
            ask,
            say,
            "4 rationale" + (" (required): " if required else " (optional, Enter to skip): "),
            required,
        )
        changes_label = label is not primary.label
        reason = _free_text(
            ask,
            say,
            f"5 adjudication reason (required: the label changes from {primary.label.value}): "
            if changes_label
            else "5 adjudication reason (optional, the label is retained; Enter to skip): ",
            changes_label,
        )
        catalog_issue = _yes_no(ask, say, "6 catalog issue (malformed record)? [y/N]: ")
        try:
            judgement = Judgement(
                query_id=item.query_id,
                product_id=item.product_id,
                round=Round.ADJUDICATION,
                label=label,
                relation=relation,
                confidence=confidence,
                rationale=rationale,
                needs_adjudication=False,
                catalog_issue=catalog_issue,
                annotator_handle=SINGLE_ANNOTATOR_HANDLE,
                annotated_at=now(),
                rubric_version=primary.rubric_version,
                review_reasons=item.review_reasons,
                changes_label=changes_label,
                adjudication_reason=reason,
            )
        except ValueError as exc:
            say(f"not saved: {exc}")
            continue
        say(summary(judgement, primary))
        answer = _ask(ask, "7 save this adjudication? type y to save, anything else discards: ")
        if answer.lower() == "y":
            return judgement
        say("discarded; nothing was saved for this pair. Adjudicate it again from step 1")


def annotate(
    context: TargetedContext,
    store: AdjudicationStore,
    *,
    annotator: str,
    ask: Ask = input,
    say: Say = print,
    now: Now = lambda: datetime.now(UTC),
    include_optional: bool = False,
) -> int:
    """Adjudicate pending mandatory pairs (then, only with `include_optional`, pending optional
    review candidates) until done or the annotator stops."""
    if annotator != SINGLE_ANNOTATOR_HANDLE:
        raise AdjudicationError(f"refused: only {SINGLE_ANNOTATOR_HANDLE!r} adjudicates")
    with adjudication_lock(store, annotator=annotator, now=now()):
        store.initialize(now=now())
        judgements, changes = store.load(now=now())
        if not store.changes_in_sync(changes):
            store.write_changes(changes)
            say(f"{CHANGES_FILE} rewritten from the confirmed adjudications (an earlier save "
                "was interrupted between the two files)")  # fmt: skip
        done = {(j.query_id, j.product_id) for j in judgements}
        groups = [("mandatory", context.mandatory)]
        if include_optional:
            groups.append(("optional candidate", context.optional))
        say(_progress(context, done))
        saved = 0
        for kind, group in groups:
            todo = [i for i in group if (i.query_id, i.product_id) not in done]
            first = len(group) - len(todo) + 1
            for offset, item in enumerate(todo):
                say("")
                say(render_pair(context, item, first + offset, len(group), kind))
                try:
                    judgement = ask_adjudication(context, item, ask, say, now)
                except Stop:
                    say(f"stopped; {saved} adjudication(s) saved this session")
                    return saved
                judgements.append(judgement)
                store.save(judgements, now=now())
                done.add((item.query_id, item.product_id))
                saved += 1
                say("saved")
        say(f"all {len(context.mandatory)} mandatory targeted pairs adjudicated")
        if not include_optional:
            say(
                "optional review candidates are not required; to review them, rerun with "
                "--include-optional"
            )
        return saved


def _progress(context: TargetedContext, done: set[Pair]) -> str:
    mandatory = sum((i.query_id, i.product_id) in done for i in context.mandatory)
    optional = sum((i.query_id, i.product_id) in done for i in context.optional)
    return (
        f"{mandatory} of {len(context.mandatory)} mandatory targeted pairs adjudicated; "
        f"{optional} of {len(context.optional)} optional review candidates adjudicated "
        f"({len(context.optional) - optional} keep their sealed primary label unless adjudicated)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.annotation.adjudication")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--work-root", type=Path, default=WORK_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="progress counts only")
    run = commands.add_parser("annotate", help="interactive targeted adjudication")
    run.add_argument("--annotator", required=True)
    run.add_argument(
        "--include-optional",
        action="store_true",
        help="after every mandatory pair, also offer the optional review candidates",
    )
    commands.add_parser("validate", help="re-check the working files; writes nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        context = load_targeted(args.golden_dir)
        store = AdjudicationStore(context, args.work_root)
        if args.command == "annotate":
            annotate(
                context, store, annotator=args.annotator, include_optional=args.include_optional
            )
            return 0
        judgements, changes = store.load(now=datetime.now(UTC))
        in_sync = store.changes_in_sync(changes)
        if args.command == "status":
            print(f"second_review_sealed {context.manifest.manifest_sha256}")
            print(
                f"{len(context.queue)} queued: {len(context.queue) - len(context.items)} resolved "
                f"jointly instead, {len(context.mandatory)} mandatory, {len(context.optional)} "
                "optional"
            )
            print(_progress(context, {(j.query_id, j.product_id) for j in judgements}))
            print(f"{len(changes)} label change record(s)")
            if store.lock_path.exists():
                print("an adjudication lock is present (a session is running or was hard-killed)")
            if not in_sync:
                print(f"{CHANGES_FILE} is out of date; the next annotate session rewrites it")
        else:
            if not in_sync:
                raise AdjudicationError(f"{CHANGES_FILE} does not match the adjudications")
            print(
                f"working files valid: {len(judgements)} adjudication(s), {len(changes)} change(s)"
            )
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
