"""Calibration and evaluation partitions for the category-only shadow gate (`qd-part-1`).

A partition selects queries from one or more sealed diagnostic batches (`labeling.load_sealed`)
and is written once (`partitions/<partition-id>.json`, exclusive creation). It binds each member
by its source batch's queries and labels hashes, its query id and the SHA-256 of its normalized
text; it stores no query text and no label, so the labels stay bound by their batch seal.

* `screening="parser_abstains_on_category"` admits only queries on which the deterministic parser
  (`qu-1`) gives no category. Screening is deterministic and network-free, and it is checked
  again whenever members are resolved, so a parser change that breaks it is refused.
* `check_disjoint` refuses a calibration and an evaluation partition that share a member or the
  same normalized text, even across different batches.
* Members are resolved against the caller's batches (and, optionally, one run per batch); the
  batches must be exactly the partition's sources and every member must be found unchanged.
* Chronology: a partition must be sealed strictly after every source batch seal. This is checked
  when the partition is built and again when its members are resolved. It compares recorded
  timestamps only; it is not proof of when anything was written.

Partitions are development tooling for M12 decisions. They are never M9 search held-out evidence,
and nothing here calls Jev, labels anything or changes search behaviour.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from ecommerce_search.decision.provider import JevOutcome
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.query_understanding.models import QueryUnderstanding
from evals.contracts.canonical import decode_json_object, encode_json_object, sha256_hex
from evals.contracts.models import Sha256, UtcTimestamp
from evals.query_decisions.contracts import (
    QUESTION_SET_VERSION,
    CategoryGold,
    DecisionQuery,
    DecisionQueryId,
    QueryDecisionError,
)
from evals.query_decisions.labeling import load_sealed
from evals.query_decisions.runner import (
    JOURNAL_FILE,
    RUN_FILE,
    RUNS_DIR,
    DispatchEvent,
    RunHeader,
    read_journal,
)
from evals.runners.golden_io import safe_child, write_exclusive

PARTITION_SCHEMA_VERSION = "qd-part-1"
PARTITIONS_DIR = "partitions"
ARTIFACT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
ArtifactId = Annotated[StrictStr, Field(pattern=ARTIFACT_ID_PATTERN)]


class PartitionRole(StrEnum):
    CALIBRATION = "calibration"
    EVALUATION = "evaluation"


Screening = Literal["none", "parser_abstains_on_category"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartitionMember(_Frozen):
    source_queries_sha256: Sha256
    source_labels_sha256: Sha256
    query_id: DecisionQueryId
    text_sha256: Sha256

    def sort_key(self) -> tuple[str, ...]:
        return (self.source_queries_sha256, self.query_id)


class PartitionSeal(_Frozen):
    schema_version: Literal["qd-part-1"]
    partition_id: ArtifactId
    role: PartitionRole
    question_set_version: Literal["jq-1"]
    screening: Screening
    # Declared version labels of the parser used for screening; not proof of identical source.
    parser_version: StrictStr
    lexicon_version: StrictStr
    synthetic: StrictBool  # true when any source batch seals a synthetic test fixture
    members: tuple[PartitionMember, ...] = Field(min_length=1)
    member_count: Annotated[int, Field(ge=1)]
    sealed_at: UtcTimestamp
    evidence_scope: Literal["m12_decision_partition_not_search_held_out"]

    @model_validator(mode="after")
    def _check(self) -> Self:
        keys = [member.sort_key() for member in self.members]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("members must be unique and sorted by (source, query id)")
        texts = [member.text_sha256 for member in self.members]
        if len(set(texts)) != len(texts):
            raise ValueError("two members have the same normalized text")
        if self.member_count != len(self.members):
            raise ValueError("member_count must equal the number of members")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.partition_id,)


@dataclass(frozen=True)
class SourceSelection:
    """The query ids taken from one sealed batch."""

    work_dir: Path
    query_ids: tuple[str, ...]


@dataclass(frozen=True)
class BatchRun:
    """A sealed batch and, optionally, the run whose outcomes are scored."""

    work_dir: Path
    run_id: str | None = None


@dataclass(frozen=True)
class MemberRecord:
    """One resolved member: its sealed query and category gold, the deterministic parse
    recomputed now, and the run outcome (if a run was given and the query was dispatched)."""

    member: PartitionMember
    query: DecisionQuery
    category_gold: CategoryGold
    deterministic: QueryUnderstanding
    outcome: JevOutcome | None
    attempted: bool
    dispatched_at: datetime | None
    run: RunHeader | None
    run_complete: bool
    run_blocked_by: str | None

    @property
    def deterministic_category(self) -> str | None:
        category = self.deterministic.category
        return None if category is None else category.value


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_partition(
    partition_id: str,
    role: PartitionRole,
    sources: Sequence[SourceSelection],
    *,
    screening: Screening,
    now: datetime | None = None,
) -> PartitionSeal:
    """A validated, unwritten partition over the given sealed batches."""
    members: list[PartitionMember] = []
    synthetic = False
    seen_sources: set[str] = set()
    versions: set[tuple[str, str]] = set()
    sealed_at = now or datetime.now(UTC)
    for source in sources:
        seal, queries, _labels = load_sealed(source.work_dir)
        _check_after_source(sealed_at, seal.sealed_at)
        if seal.queries_sha256 in seen_sources:
            raise QueryDecisionError("a source batch is listed twice")
        seen_sources.add(seal.queries_sha256)
        synthetic = synthetic or seal.synthetic
        by_id = {query.query_id: query for query in queries}
        if len(set(source.query_ids)) != len(source.query_ids):
            raise QueryDecisionError("a query id is selected twice from one batch")
        for query_id in source.query_ids:
            query = by_id.get(query_id)
            if query is None:
                raise QueryDecisionError(f"{query_id} is not in its sealed batch")
            parsed = parse_normalized_query(query.text)
            versions.add((parsed.parser_version, parsed.lexicon_version))
            if screening == "parser_abstains_on_category" and parsed.category is not None:
                raise QueryDecisionError(f"{query_id}: the parser decides a category (screened)")
            members.append(
                PartitionMember(
                    source_queries_sha256=seal.queries_sha256,
                    source_labels_sha256=seal.labels_sha256,
                    query_id=query_id,
                    text_sha256=text_sha256(query.text),
                )
            )
    if not members:
        raise QueryDecisionError("a partition needs at least one member")
    if len(versions) != 1:
        raise QueryDecisionError("screening saw more than one parser version")
    ((parser_version, lexicon_version),) = versions
    return PartitionSeal(
        schema_version=PARTITION_SCHEMA_VERSION,
        partition_id=partition_id,
        role=role,
        question_set_version=QUESTION_SET_VERSION,
        screening=screening,
        parser_version=parser_version,
        lexicon_version=lexicon_version,
        synthetic=synthetic,
        members=tuple(sorted(members, key=lambda member: member.sort_key())),
        member_count=len(members),
        sealed_at=sealed_at,
        evidence_scope="m12_decision_partition_not_search_held_out",
    )


def write_partition(root: Path, seal: PartitionSeal) -> str:
    """Write the partition once; returns the SHA-256 of the written bytes."""
    data = encode_json_object(seal)
    write_exclusive(root, {f"{PARTITIONS_DIR}/{seal.partition_id}.json": data})
    return sha256_hex(data)


def load_partition(root: Path, partition_id: str) -> tuple[PartitionSeal, str]:
    """The partition and the SHA-256 of its file bytes."""
    path = safe_child(root, f"{PARTITIONS_DIR}/{partition_id}.json")
    if not path.is_file():
        raise QueryDecisionError(f"no partition {partition_id!r}")
    data = path.read_bytes()
    seal = decode_json_object(data, PartitionSeal)
    if seal.partition_id != partition_id:
        raise QueryDecisionError("the partition file name and id disagree")
    return seal, sha256_hex(data)


def check_disjoint(calibration: PartitionSeal, evaluation: PartitionSeal) -> None:
    """Refuse a calibration/evaluation pair that shares a member or a normalized text."""
    if calibration.role is not PartitionRole.CALIBRATION:
        raise QueryDecisionError("the first partition must be a calibration partition")
    if evaluation.role is not PartitionRole.EVALUATION:
        raise QueryDecisionError("the second partition must be an evaluation partition")
    shared_members = {m.sort_key() for m in calibration.members} & {
        m.sort_key() for m in evaluation.members
    }
    shared_texts = {m.text_sha256 for m in calibration.members} & {
        m.text_sha256 for m in evaluation.members
    }
    if shared_members or shared_texts:
        raise QueryDecisionError(
            f"partitions overlap: {len(shared_members)} shared member(s), "
            f"{len(shared_texts)} shared normalized text(s)"
        )


def resolve_members(seal: PartitionSeal, batches: Sequence[BatchRun]) -> tuple[MemberRecord, ...]:
    """Every member with its sealed query, gold and (optional) run outcome; see the module
    docstring for the checks."""
    loaded: dict[tuple[str, str], tuple[Any, Any, Any]] = {}
    for batch in batches:
        batch_seal, queries, labels = load_sealed(batch.work_dir)
        _check_after_source(seal.sealed_at, batch_seal.sealed_at)
        key = (batch_seal.queries_sha256, batch_seal.labels_sha256)
        if key in loaded:
            raise QueryDecisionError("a batch is listed twice")
        loaded[key] = (
            {query.query_id: query for query in queries},
            {label.query_id: label.category for label in labels},
            _run_state(batch, key),
        )
    needed = {(m.source_queries_sha256, m.source_labels_sha256) for m in seal.members}
    if set(loaded) != needed:
        raise QueryDecisionError("the batches must be exactly the partition's source batches")

    records = []
    for member in seal.members:
        queries, golds, run = loaded[(member.source_queries_sha256, member.source_labels_sha256)]
        query = queries.get(member.query_id)
        if query is None or text_sha256(query.text) != member.text_sha256:
            raise QueryDecisionError(f"{member.query_id} changed or is missing in its batch")
        parsed = parse_normalized_query(query.text)
        if seal.screening == "parser_abstains_on_category" and parsed.category is not None:
            raise QueryDecisionError(
                f"{member.query_id}: the parser now decides a category; screening no longer holds"
            )
        header, state, dispatched_at = run
        records.append(
            MemberRecord(
                member=member,
                query=query,
                category_gold=golds[member.query_id],
                deterministic=parsed,
                outcome=None if state is None else state.results.get(member.query_id),
                attempted=state is not None and member.query_id in state.dispatched,
                dispatched_at=dispatched_at.get(member.query_id),
                run=header,
                run_complete=state is not None and state.complete,
                run_blocked_by=None if state is None else state.blocked_by,
            )
        )
    return tuple(records)


def _check_after_source(partition_sealed_at: datetime, source_sealed_at: datetime) -> None:
    if partition_sealed_at <= source_sealed_at:
        raise QueryDecisionError("a partition must be sealed after every source batch seal")


def _run_state(batch: BatchRun, key: tuple[str, str]) -> Any:
    if batch.run_id is None:
        return None, None, {}
    run_file = safe_child(batch.work_dir, f"{RUNS_DIR}/{batch.run_id}/{RUN_FILE}")
    if not run_file.is_file():
        raise QueryDecisionError("no such run in the batch")
    header = decode_json_object(run_file.read_bytes(), RunHeader)
    if (header.queries_sha256, header.labels_sha256) != key:
        raise QueryDecisionError("the run does not belong to its batch")
    events, state = read_journal(
        safe_child(batch.work_dir, f"{RUNS_DIR}/{batch.run_id}/{JOURNAL_FILE}")
    )
    dispatched_at = {e.query_id: e.at for e in events if isinstance(e, DispatchEvent)}
    return header, state, dispatched_at
