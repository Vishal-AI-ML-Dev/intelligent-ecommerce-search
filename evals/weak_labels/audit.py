"""The non-blind human audit of the 100 queued machine proposals (auditor `ann-1` only).

This is a separate workflow from the strict blind primary annotation: its own work directory
(`data/processed/weak_audit/<proposal manifest sha256[:16]>/`, git-ignored), its own binding,
lock and decision file, and its own record type (`AuditDecision`). It never reads or writes the
strict primary-round working file or its binding.

For each queued pair the auditor sees the query (text, interpretations, own gold constraints and
intents), the `card-1` product card, the machine proposal (label, relation, confidence), its
short public reason and a warning that it may be wrong. Never shown: cross-encoder scores,
ranks, retrieval sources or inclusion reasons (they stay in the private signals).

Commands (one per prompt; nothing is preselected and an empty answer does nothing):

* `a`: accept the proposal (`human_accepted_proposal`);
* `r<n><c>`, `b<n><c>`, `n<n><c>`: override as relevant / borderline / not_relevant with
  relation number `n` (the fixed per-label numbering shown on the codes line) and confidence
  `c` = h/m/l (`human_override`). An override equal to the proposal is refused (use `a`);
  `relevant` for a hard-violating product is refused (rubric rule);
* `u`: undo the most recent decision and show that pair again;
* `?`: help; `q`: quit (every decision is already saved). End of input quits the same way.

Every decision is saved atomically before the next pair. Decisions bind the proposal hash and
the card hash; the work directory binds the proposal manifest and pool. A changed proposal file,
pool or card, a foreign handle, a duplicate or an unknown pair fails closed. One session at a
time holds `audit.lock` (exclusive creation); a lock left by a hard kill is never removed
automatically.
"""

import contextlib
import json
import os
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from evals.contracts.canonical import (
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
)
from evals.contracts.cards import card_sha256, render_card
from evals.contracts.catalog import ROOT
from evals.contracts.constants import ALLOWED_RELATIONS, Label, Relation
from evals.contracts.models import (
    Confidence,
    OverallStatus,
    canonical_gold_value,
)
from evals.runners.golden_io import refuse_stale_staging
from evals.weak_labels.contracts import (
    AUDIT_SIZE,
    HUMAN_AUDITOR,
    AuditBinding,
    AuditDecision,
    AuditLockInfo,
    AuditQueueItem,
    WeakLabelProposal,
)
from evals.weak_labels.generation import FrozenFacts, Generation, WeakLabelError

AUDIT_ROOT = ROOT / "data" / "processed" / "weak_audit"
DECISIONS_FILE = "audit_decisions.jsonl"
BINDING_FILE = "binding.json"
LOCK_FILE = "audit.lock"

Ask = Callable[[str], str]
Say = Callable[[str], None]
Now = Callable[[], datetime]
Pair = tuple[str, str]

LABEL_KEYS = {"r": Label.RELEVANT, "b": Label.BORDERLINE, "n": Label.NOT_RELEVANT}
CONFIDENCE_KEYS = {"h": Confidence.HIGH, "m": Confidence.MEDIUM, "l": Confidence.LOW}
ACCEPT, UNDO, HELP, QUIT = "a", "u", "?", "q"
_OVERRIDE_RE = re.compile(r"^([rbn])([1-9])([hml])$")
WARNING = (
    "WARNING: this is an AI/model-assisted proposal (weak-label-policy-1), not a human "
    "judgement. It may be wrong. Accept only if you agree; otherwise override."
)
INPUT_ENDED_MESSAGE = "Input ended; progress saved. Resume by running the audit command again."


class AuditError(WeakLabelError):
    """The audit cannot start or a decision cannot be stored."""


@dataclass(frozen=True)
class AuditContext:
    facts: FrozenFacts
    generation: Generation

    @property
    def proposals(self) -> dict[Pair, WeakLabelProposal]:
        return {(p.query_id, p.product_id): p for p in self.generation.proposals}

    @property
    def queue(self) -> tuple[AuditQueueItem, ...]:
        return self.generation.queue


def numbered_relations(label: Label) -> dict[int, Relation]:
    relations = sorted(ALLOWED_RELATIONS[label], key=lambda r: r.value)
    return dict(enumerate(relations, start=1))


def relation_legend() -> str:
    parts = []
    for key, label in LABEL_KEYS.items():
        numbered = " ".join(f"{n}={r.value}" for n, r in numbered_relations(label).items())
        parts.append(f"{key} ({label.value}): {numbered}")
    return "codes  " + " | ".join(parts) + " | confidence h/m/l"


HELP_TEXT = "\n".join(
    [
        "a      accept the machine proposal as shown",
        "r<n><c> / b<n><c> / n<n><c>  override: label r=relevant b=borderline n=not_relevant,",
        "       relation number n from the codes line, your confidence c = h/m/l (e.g. n2h)",
        "u      undo your most recent decision and show that pair again",
        "?      this help      q  quit (all decisions are already saved; EOF does the same)",
        "There is no default: an empty answer does nothing. There is no accept-all.",
    ]
)


def _hard_violated(context: AuditContext, pair: Pair) -> bool:
    return context.facts.eligibility[pair].hard_overall is OverallStatus.VIOLATED


def validate_decisions(
    context: AuditContext,
    decisions: Sequence[AuditDecision],
    *,
    now: datetime,
    created_at: datetime,
) -> None:
    queue = {(q.query_id, q.product_id): q for q in context.queue}
    proposals = context.proposals
    cards = {(i.query_id, i.product_id): i.card_sha256 for i in context.facts.pool}
    seen: set[Pair] = set()
    sequences: set[int] = set()
    for d in decisions:
        pair = (d.query_id, d.product_id)
        if d.annotator_handle != HUMAN_AUDITOR:  # pragma: no cover - the model pins the handle
            raise AuditError(f"{pair!r}: decision from a foreign handle")
        item = queue.get(pair)
        if item is None:
            raise AuditError(f"{pair!r}: not a pair of the audit queue")
        if pair in seen:
            raise AuditError(f"{pair!r}: duplicate audit decision")
        seen.add(pair)
        if d.sequence in sequences:
            raise AuditError(f"{pair!r}: duplicate decision sequence {d.sequence}")
        sequences.add(d.sequence)
        if d.proposal_sha256 != item.proposal_sha256:
            raise AuditError(f"{pair!r}: the audited proposal changed")
        if d.card_sha256 != cards[pair]:
            raise AuditError(f"{pair!r}: the product card changed")
        if d.rubric_version != context.facts.rubric_version:
            raise AuditError(f"{pair!r}: rubric {d.rubric_version} is not the frozen rubric")
        if d.decided_at < created_at or d.decided_at > now:
            raise AuditError(f"{pair!r}: decided_at is outside the allowed window")
        proposal = proposals[pair]
        same = (d.label, d.relation) == (proposal.proposed_label, proposal.proposed_relation)
        if (d.decision == "human_accepted_proposal") != same:
            raise AuditError(f"{pair!r}: acceptance/override does not match the proposal")
        if d.label is Label.RELEVANT and _hard_violated(context, pair):
            raise AuditError(f"{pair!r}: relevant for a hard-violating product")


class AuditStore:
    """The atomically replaced decision file of one proposal generation's audit."""

    def __init__(self, context: AuditContext, audit_root: Path = AUDIT_ROOT) -> None:
        self.context = context
        self.directory = audit_root / context.generation.manifest_sha256[:16]
        self.path = self.directory / DECISIONS_FILE
        self.binding_path = self.directory / BINDING_FILE
        self.lock_path = self.directory / LOCK_FILE

    def expected_binding(self, created_at: datetime) -> AuditBinding:
        return AuditBinding(
            proposal_manifest_sha256=self.context.generation.manifest_sha256,
            pool_manifest_sha256=self.context.facts.pool_manifest.manifest_sha256,
            created_at=created_at,
        )

    def read_binding(self, *, now: datetime) -> AuditBinding | None:
        if self.directory.exists():
            refuse_stale_staging(self.directory)
        if not self.binding_path.exists():
            if self.path.exists():
                raise AuditError("an audit decision file exists without its binding")
            return None
        stored = decode_json_object(self.binding_path.read_bytes(), AuditBinding)
        if stored != self.expected_binding(stored.created_at):
            raise AuditError("the audit work is bound to other proposals or another pool")
        if stored.created_at > now:
            raise AuditError("the audit binding's created_at is in the future")
        return stored

    def initialize(self, *, now: datetime) -> AuditBinding:
        existing = self.read_binding(now=now)
        if existing is not None:
            return existing
        binding = self.expected_binding(now)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.binding_path, "xb") as handle:
                handle.write(encode_json_object(binding))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise AuditError("refusing to overwrite the audit binding") from None
        return binding

    def load(self, *, now: datetime) -> list[AuditDecision]:
        binding = self.read_binding(now=now)
        if binding is None or not self.path.exists():
            return []
        decisions = decode_jsonl(self.path.read_bytes(), AuditDecision)
        validate_decisions(self.context, decisions, now=now, created_at=binding.created_at)
        return decisions

    def save(self, decisions: Sequence[AuditDecision], *, now: datetime) -> None:
        binding = self.read_binding(now=now)
        if binding is None:
            raise AuditError("the audit is not initialized (no binding)")
        validate_decisions(self.context, decisions, now=now, created_at=binding.created_at)
        _atomic_write(self.path, encode_jsonl(decisions))


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
def audit_lock(store: AuditStore, *, now: datetime) -> Iterator[AuditLockInfo]:
    info = AuditLockInfo(
        proposal_manifest_sha256=store.context.generation.manifest_sha256,
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
        raise AuditError(
            f"another audit session holds {store.lock_path}. If no audit session is running "
            "(for example after a hard kill), check `validate` passes, then delete the lock "
            "file by hand and rerun. It is never removed automatically."
        ) from None
    try:
        yield info
    finally:
        store.lock_path.unlink(missing_ok=True)


# ---- interactive loop --------------------------------------------------------------------------


class _Quit(Exception):
    pass


class _InputEnded(_Quit):
    pass


def query_block(context: AuditContext, query_id: str) -> str:
    facts = context.facts
    query = next(q for q in facts.queries if q.query_id == query_id)
    lines = [f"query {query.query_id}: {query.text}"]
    for interpretation in query.interpretations:
        lines.append(f"  interpretation: {interpretation}")
    own = sorted(
        (c for c in facts.constraints if c.query_id == query_id), key=lambda c: c.sort_key()
    )
    for c in own:
        lines.append(
            f"  {c.hardness.value} gold constraint: {c.field.value} {c.operator.value} "
            f"{canonical_gold_value(c.value)}"
        )
    for intent in facts.intents:
        if intent.query_id == query_id:
            lines.append(f"  soft {intent.kind.value} intent: {intent.value}")
    if not own:
        lines.append("  (no gold constraints recorded for this query)")
    return "\n".join(lines)


def proposal_block(proposal: WeakLabelProposal) -> str:
    return "\n".join(
        [
            f"machine proposal ({proposal.proposal_source}): {proposal.proposed_label.value} / "
            f"{proposal.proposed_relation.value} / confidence {proposal.proposal_confidence.value}",
            f"reason: {proposal.reason}",
            WARNING,
        ]
    )


def parse_override(token: str) -> tuple[Label, Relation, Confidence] | None:
    match = _OVERRIDE_RE.fullmatch(token)
    if match is None:
        return None
    label = LABEL_KEYS[match.group(1)]
    relation = numbered_relations(label).get(int(match.group(2)))
    if relation is None:
        return None
    return label, relation, CONFIDENCE_KEYS[match.group(3)]


def audit(
    context: AuditContext,
    store: AuditStore,
    *,
    annotator: str,
    ask: Ask = input,
    say: Say = print,
    now: Now,
) -> int:
    """Audit pending pairs until done or the auditor quits. Returns decisions saved now."""
    if annotator != HUMAN_AUDITOR:
        raise AuditError(f"refused: only {HUMAN_AUDITOR!r} audits weak-label proposals")
    with audit_lock(store, now=now()):
        return _audit_locked(context, store, ask=ask, say=say, now=now)


def _ask(ask: Ask, prompt: str) -> str:
    try:
        return ask(prompt).strip().lower()
    except EOFError:
        raise _InputEnded from None


def _audit_locked(context: AuditContext, store: AuditStore, *, ask: Ask, say: Say, now: Now) -> int:
    store.initialize(now=now())
    decisions = store.load(now=now())
    proposals = context.proposals
    cards = {(i.query_id, i.product_id): i.card_sha256 for i in context.facts.pool}
    say(f"{len(decisions)} of {AUDIT_SIZE} audit pairs decided (weak_dev_v1, non-blind audit)")
    saved = 0
    first: Pair | None = None  # a pair to show next after an undo
    while True:
        done = {(d.query_id, d.product_id) for d in decisions}
        pending = [q for q in context.queue if (q.query_id, q.product_id) not in done]
        if not pending:
            say(f"all {AUDIT_SIZE} audit pairs decided. Next: `assemble --annotator ann-1`")
            return saved
        item = next((q for q in pending if (q.query_id, q.product_id) == first), pending[0])
        first = None
        pair = (item.query_id, item.product_id)
        proposal = proposals[pair]
        product = context.facts.catalog.products[item.product_id]
        if card_sha256(product) != cards[pair]:
            raise AuditError(f"{pair!r}: the product card changed since the pool was frozen")
        say("")
        say(f"--- audit pair {item.audit_position} of {AUDIT_SIZE} ({len(done)} decided) ---")
        say(query_block(context, item.query_id))
        say("")
        say(render_card(product))
        say("")
        say(proposal_block(proposal))
        say(relation_legend())
        while True:
            try:
                token = _ask(ask, "decision [a / r|b|n<num><h|m|l> / u / ? / q]: ")
            except _InputEnded:
                say(INPUT_ENDED_MESSAGE)
                return saved
            if token == QUIT:
                say(f"stopped; {saved} decision(s) saved this session")
                return saved
            if token == HELP:
                say(HELP_TEXT)
                say(relation_legend())
                continue
            if token == UNDO:
                if not decisions:
                    say("nothing to undo")
                    continue
                latest = max(decisions, key=lambda d: d.sequence)
                decisions = [d for d in decisions if d is not latest]
                store.save(decisions, now=now())
                say(f"undone: {latest.query_id}/{latest.product_id} ({latest.decision})")
                first = (latest.query_id, latest.product_id)
                break
            if token == ACCEPT:
                kind, label, relation, confidence = (
                    "human_accepted_proposal",
                    proposal.proposed_label,
                    proposal.proposed_relation,
                    None,
                )
            else:
                parsed = parse_override(token)
                if parsed is None:
                    if token:
                        say("not a command (type ? for help)")
                    continue
                label, relation, confidence = parsed
                if (label, relation) == (proposal.proposed_label, proposal.proposed_relation):
                    say("that equals the proposal; type a to accept it")
                    continue
                if label is Label.RELEVANT and _hard_violated(context, pair):
                    say(
                        "relevant is not allowed: this product violates a hard gold constraint "
                        "(rubric rule). Choose borderline or not_relevant."
                    )
                    continue
                kind = "human_override"
            decision = AuditDecision(
                query_id=item.query_id,
                product_id=item.product_id,
                sequence=max((d.sequence for d in decisions), default=0) + 1,
                decision=kind,  # type: ignore[arg-type]
                label=label,
                relation=relation,
                human_confidence=confidence,
                proposal_sha256=item.proposal_sha256,
                card_sha256=cards[pair],
                decided_at=now(),
                rubric_version=context.facts.rubric_version,
            )
            decisions = [*decisions, decision]
            store.save(decisions, now=now())
            saved += 1
            say(f"saved: {kind} -> {label.value} / {relation.value}")
            break
