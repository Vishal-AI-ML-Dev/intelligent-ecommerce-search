"""The staged, hash-chained Golden Dataset manifest.

Hashes are non-circular:

* an entry's `file_sha256` covers the exact file bytes, and its `records_sha256` covers the
  canonical re-encoding of the decoded records (equal to `file_sha256` for an accepted file,
  which proves canonical form); no data file contains a hash of itself;
* `manifest_sha256` is the SHA-256 of the canonical manifest body with the `manifest_sha256`
  key removed, and the manifest never lists itself as an entry;
* `previous_manifest_sha256` points only backwards, so the chain has no cycle;
* `supersedes_manifest_sha256` (null exactly for golden version 1) binds every stage of version
  N+1 to version N's terminal `provenance_unsealed` manifest. It also points only backwards, at a
  finished manifest, so corrections stay auditable across versions without any cycle.

Lifecycle (strictly ordered; each stage is a new manifest file and earlier ones are never
edited): `queries_frozen` (queries, gold constraints, product intents, the generated qu-1
extraction record and the recorded authoring attestation) → `pool_frozen` (the public pool, its
eligibility records and the aggregate pool report) → `primary_sealed` → `labels_frozen` →
`provenance_unsealed`. That is the `review-1` order. Under the `review-2` amendment (an explicit
`review_protocol` field, set from `second_review_sealed` onwards and omitted before, so every
earlier manifest keeps its exact bytes) the order is `queries_frozen` → `pool_frozen` →
`primary_sealed` → `second_review_sealed` (the blind second-review judgements, the
inter-annotator agreement report and the joint-resolution queue) → `labels_frozen` →
`provenance_unsealed`; its `labels_frozen` carries joint consensus records instead of
consistency judgements. Role sets
and transitions are branched explicitly by protocol. From `pool_frozen` the private provenance
(one `PoolProvenance` object file) is represented only by its checksum (`provenance_binding`);
it becomes an entry at `provenance_unsealed`, where its hash must equal that binding (D12).
Every entry of a stage is carried unchanged into the next stage.
`labels_frozen_at` (UTC, hashed like every other field) is the explicit freeze time: null before
`labels_frozen`, set from it onwards and never changed; it is no earlier than the primary seal
here, and no earlier than any included record (`validation.validate_labels_frozen_at`).

`recorded` entries (human-authored files with timestamps) are verified by their recorded bytes;
`generated` entries carry no timestamps or paths and are additionally checked for byte identity
against a regeneration when one is supplied.
"""

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StrictInt,
    StrictStr,
    model_serializer,
    model_validator,
)

from evals.contracts.canonical import (
    CanonicalFormError,
    ContractError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.constants import GOLDEN_ID, REVIEW_PROTOCOL_2
from evals.contracts.models import (
    AgreementReport,
    AuthoringAttestation,
    ConsensusAdjudication,
    ConsistencySample,
    EligibilityRecord,
    FinalJudgement,
    GoldConstraint,
    JointResolutionItem,
    Judgement,
    LabelChange,
    PartitionAssignment,
    PoolItem,
    PoolProvenance,
    PoolReport,
    ProductIntent,
    QueryExtraction,
    QueryRecord,
    QueueItem,
    Round,
    Sha256,
    UtcTimestamp,
    _Frozen,
    _Record,
)

ENTRY_PATH_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$"


class Stage(StrEnum):
    QUERIES_FROZEN = "queries_frozen"
    POOL_FROZEN = "pool_frozen"
    PRIMARY_SEALED = "primary_sealed"
    SECOND_REVIEW_SEALED = "second_review_sealed"  # review-2 only
    LABELS_FROZEN = "labels_frozen"
    PROVENANCE_UNSEALED = "provenance_unsealed"


# The review-1 lifecycle (unchanged; a manifest without `review_protocol`).
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.QUERIES_FROZEN,
    Stage.POOL_FROZEN,
    Stage.PRIMARY_SEALED,
    Stage.LABELS_FROZEN,
    Stage.PROVENANCE_UNSEALED,
)
# The review-2 lifecycle: the second review is sealed between the primary seal and the freeze.
REVIEW_2_STAGE_ORDER: tuple[Stage, ...] = (
    Stage.QUERIES_FROZEN,
    Stage.POOL_FROZEN,
    Stage.PRIMARY_SEALED,
    Stage.SECOND_REVIEW_SEALED,
    Stage.LABELS_FROZEN,
    Stage.PROVENANCE_UNSEALED,
)
_STAGE_ORDERS: dict[str | None, tuple[Stage, ...]] = {
    None: STAGE_ORDER,
    REVIEW_PROTOCOL_2: REVIEW_2_STAGE_ORDER,
}


def stage_order(review_protocol: str | None = None) -> tuple[Stage, ...]:
    try:
        return _STAGE_ORDERS[review_protocol]
    except KeyError:
        raise ContractError(f"unknown review protocol {review_protocol!r}") from None


def stage_rank(stage: Stage) -> int:
    """A protocol-independent rank (both lifecycles are sub-sequences of this order), used only
    for "at or after stage X" checks."""
    return REVIEW_2_STAGE_ORDER.index(stage)


class Role(StrEnum):
    QUERIES = "queries"
    GOLD_CONSTRAINTS = "gold_constraints"
    PRODUCT_INTENTS = "product_intents"
    QUERY_EXTRACTION = "query_extraction"
    AUTHORING_ATTESTATION = "authoring_attestation"
    POOL = "pool"
    ELIGIBILITY = "eligibility"
    POOL_REPORT = "pool_report"
    PRIMARY_JUDGEMENTS = "primary_judgements"
    CONSISTENCY_SAMPLE = "consistency_sample"
    ADJUDICATION_QUEUE = "adjudication_queue"
    CONSISTENCY_JUDGEMENTS = "consistency_judgements"
    ADJUDICATION_JUDGEMENTS = "adjudication_judgements"
    LABEL_CHANGES = "label_changes"
    PARTITIONS = "partitions"
    FINAL_JUDGEMENTS = "final_judgements"
    PROVENANCE = "provenance"
    # review-2 only
    SECOND_REVIEW_JUDGEMENTS = "second_review_judgements"
    AGREEMENT_REPORT = "agreement_report"
    CONSENSUS_ADJUDICATIONS = "consensus_adjudications"
    JOINT_RESOLUTION_QUEUE = "joint_resolution_queue"


class EntryKind(StrEnum):
    GENERATED = "generated"
    RECORDED = "recorded"


ROLE_KIND: dict[Role, EntryKind] = {
    Role.QUERIES: EntryKind.RECORDED,
    Role.GOLD_CONSTRAINTS: EntryKind.RECORDED,
    Role.PRODUCT_INTENTS: EntryKind.RECORDED,
    Role.QUERY_EXTRACTION: EntryKind.GENERATED,
    Role.AUTHORING_ATTESTATION: EntryKind.RECORDED,
    Role.POOL: EntryKind.GENERATED,
    Role.ELIGIBILITY: EntryKind.GENERATED,
    Role.POOL_REPORT: EntryKind.GENERATED,
    Role.PRIMARY_JUDGEMENTS: EntryKind.RECORDED,
    Role.CONSISTENCY_SAMPLE: EntryKind.GENERATED,
    Role.ADJUDICATION_QUEUE: EntryKind.GENERATED,
    Role.CONSISTENCY_JUDGEMENTS: EntryKind.RECORDED,
    Role.ADJUDICATION_JUDGEMENTS: EntryKind.RECORDED,
    Role.LABEL_CHANGES: EntryKind.RECORDED,
    Role.PARTITIONS: EntryKind.RECORDED,
    Role.FINAL_JUDGEMENTS: EntryKind.GENERATED,
    Role.PROVENANCE: EntryKind.GENERATED,
    Role.SECOND_REVIEW_JUDGEMENTS: EntryKind.RECORDED,
    Role.AGREEMENT_REPORT: EntryKind.GENERATED,
    Role.CONSENSUS_ADJUDICATIONS: EntryKind.RECORDED,
    Role.JOINT_RESOLUTION_QUEUE: EntryKind.GENERATED,
}

# Record model per role.
ROLE_MODEL: dict[Role, type[BaseModel]] = {
    Role.QUERIES: QueryRecord,
    Role.GOLD_CONSTRAINTS: GoldConstraint,
    Role.PRODUCT_INTENTS: ProductIntent,
    Role.QUERY_EXTRACTION: QueryExtraction,
    Role.AUTHORING_ATTESTATION: AuthoringAttestation,
    Role.POOL: PoolItem,
    Role.ELIGIBILITY: EligibilityRecord,
    Role.POOL_REPORT: PoolReport,
    Role.PRIMARY_JUDGEMENTS: Judgement,
    Role.CONSISTENCY_SAMPLE: ConsistencySample,
    Role.ADJUDICATION_QUEUE: QueueItem,
    Role.CONSISTENCY_JUDGEMENTS: Judgement,
    Role.ADJUDICATION_JUDGEMENTS: Judgement,
    Role.LABEL_CHANGES: LabelChange,
    Role.PARTITIONS: PartitionAssignment,
    Role.FINAL_JUDGEMENTS: FinalJudgement,
    Role.PROVENANCE: PoolProvenance,
    Role.SECOND_REVIEW_JUDGEMENTS: Judgement,
    Role.AGREEMENT_REPORT: AgreementReport,
    Role.CONSENSUS_ADJUDICATIONS: ConsensusAdjudication,
    Role.JOINT_RESOLUTION_QUEUE: JointResolutionItem,
}
# Single-object files; every other role is JSONL.
OBJECT_ROLES = frozenset(
    {
        Role.CONSISTENCY_SAMPLE,
        Role.AUTHORING_ATTESTATION,
        Role.POOL_REPORT,
        Role.PROVENANCE,
        Role.AGREEMENT_REPORT,
    }
)
ROLE_ROUND: dict[Role, Round] = {
    Role.PRIMARY_JUDGEMENTS: Round.PRIMARY,
    Role.CONSISTENCY_JUDGEMENTS: Round.CONSISTENCY,
    Role.ADJUDICATION_JUDGEMENTS: Round.ADJUDICATION,
    Role.SECOND_REVIEW_JUDGEMENTS: Round.SECOND_REVIEW,
}

_ADDED_AT: dict[Stage, frozenset[Role]] = {
    Stage.QUERIES_FROZEN: frozenset(
        {
            Role.QUERIES,
            Role.GOLD_CONSTRAINTS,
            Role.PRODUCT_INTENTS,
            Role.QUERY_EXTRACTION,
            Role.AUTHORING_ATTESTATION,
        }
    ),
    Stage.POOL_FROZEN: frozenset({Role.POOL, Role.ELIGIBILITY, Role.POOL_REPORT}),
    Stage.PRIMARY_SEALED: frozenset(
        {Role.PRIMARY_JUDGEMENTS, Role.CONSISTENCY_SAMPLE, Role.ADJUDICATION_QUEUE}
    ),
    Stage.LABELS_FROZEN: frozenset(
        {
            Role.CONSISTENCY_JUDGEMENTS,
            Role.ADJUDICATION_JUDGEMENTS,
            Role.LABEL_CHANGES,
            Role.PARTITIONS,
            Role.FINAL_JUDGEMENTS,
        }
    ),
    Stage.PROVENANCE_UNSEALED: frozenset({Role.PROVENANCE}),
}
# review-2: identical up to `primary_sealed` (so the primary seal is unchanged); the consistency
# sample sealed there is the second-review sample; no consistency judgements exist.
_ADDED_AT_REVIEW_2: dict[Stage, frozenset[Role]] = {
    **{stage: _ADDED_AT[stage] for stage in STAGE_ORDER[:3]},
    Stage.SECOND_REVIEW_SEALED: frozenset(
        {Role.SECOND_REVIEW_JUDGEMENTS, Role.AGREEMENT_REPORT, Role.JOINT_RESOLUTION_QUEUE}
    ),
    Stage.LABELS_FROZEN: frozenset(
        {
            Role.ADJUDICATION_JUDGEMENTS,
            Role.CONSENSUS_ADJUDICATIONS,
            Role.LABEL_CHANGES,
            Role.PARTITIONS,
            Role.FINAL_JUDGEMENTS,
        }
    ),
    Stage.PROVENANCE_UNSEALED: _ADDED_AT[Stage.PROVENANCE_UNSEALED],
}
_ADDED_AT_BY_PROTOCOL: dict[str | None, dict[Stage, frozenset[Role]]] = {
    None: _ADDED_AT,
    REVIEW_PROTOCOL_2: _ADDED_AT_REVIEW_2,
}


def stage_roles(stage: Stage, review_protocol: str | None = None) -> frozenset[Role]:
    """The exact set of roles a manifest at `stage` must list (cumulative) under the review
    protocol (`None` is review-1)."""
    order = stage_order(review_protocol)
    if stage not in order:
        raise ContractError(f"stage {stage.value} is not part of review protocol {review_protocol}")
    added = _ADDED_AT_BY_PROTOCOL[review_protocol]
    return frozenset().union(*(added[s] for s in order[: order.index(stage) + 1]))


class ManifestEntry(_Frozen):
    path: Annotated[StrictStr, Field(pattern=ENTRY_PATH_PATTERN, max_length=200)]
    role: Role
    kind: EntryKind
    file_sha256: Sha256
    records_sha256: Sha256
    record_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if ".." in self.path.split("/"):
            raise ValueError("entry paths must not contain '..'")
        if self.kind is not ROLE_KIND[self.role]:
            raise ValueError(f"role {self.role.value} is {ROLE_KIND[self.role].value}")
        return self


class ManifestVersions(_Frozen):
    rubric: Annotated[StrictStr, Field(min_length=1)]
    pool_policy: Annotated[StrictStr, Field(min_length=1)]
    blind_order: Annotated[StrictStr, Field(min_length=1)]
    consistency_sample: Annotated[StrictStr, Field(min_length=1)]
    parser: Annotated[StrictStr, Field(min_length=1)]
    lexicon: Annotated[StrictStr, Field(min_length=1)]
    filter_policy: Annotated[StrictStr, Field(min_length=1)]
    embedding_model: Annotated[StrictStr, Field(min_length=1)]
    embedding_revision: Annotated[StrictStr, Field(min_length=1)]
    reranker_model: Annotated[StrictStr, Field(min_length=1)]
    reranker_revision: Annotated[StrictStr, Field(min_length=1)]


def compute_manifest_sha256(body: dict[str, Any]) -> str:
    """SHA-256 of the canonical manifest body without its own `manifest_sha256` key."""
    content = {key: value for key, value in body.items() if key != "manifest_sha256"}
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(text.encode("utf-8"))


class Manifest(_Record):
    golden_id: Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = GOLDEN_ID
    golden_version: Annotated[StrictInt, Field(ge=1)]
    stage: Stage
    dataset_id: Annotated[StrictStr, Field(min_length=1)]
    dataset_version: Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,8}$")]
    dataset_checksum_sha256: Sha256
    versions: ManifestVersions
    entries: tuple[ManifestEntry, ...]
    provenance_binding: Sha256 | None = None
    primary_sealed_at: UtcTimestamp | None = None
    labels_frozen_at: UtcTimestamp | None = None
    previous_manifest_sha256: Sha256 | None = None
    supersedes_manifest_sha256: Sha256 | None = None
    review_protocol: Literal["review-2"] | None = None
    manifest_sha256: Sha256

    @model_serializer(mode="wrap")
    def _omit_review_1_protocol(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        return _without_implicit_protocol(handler(self))

    @model_validator(mode="after")
    def _check(self) -> Self:
        paths = [e.path for e in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("entries must have unique paths, sorted by path")
        roles = [e.role for e in self.entries]
        if len(set(roles)) != len(roles):
            raise ValueError("each role appears at most once")
        if self.stage not in stage_order(self.review_protocol):
            raise ValueError(f"stage {self.stage.value} requires review_protocol review-2")
        if self.review_protocol is not None and stage_rank(self.stage) < stage_rank(
            Stage.SECOND_REVIEW_SEALED
        ):
            raise ValueError("review_protocol is set from second_review_sealed onwards only")
        if set(roles) != stage_roles(self.stage, self.review_protocol):
            expected = sorted(r.value for r in stage_roles(self.stage, self.review_protocol))
            raise ValueError(f"stage {self.stage.value} requires exactly the roles {expected}")
        index = stage_rank(self.stage)
        if (self.provenance_binding is None) != (index < stage_rank(Stage.POOL_FROZEN)):
            raise ValueError("provenance_binding is set from pool_frozen onwards, and only then")
        if (self.primary_sealed_at is None) != (index < stage_rank(Stage.PRIMARY_SEALED)):
            raise ValueError("primary_sealed_at is set from primary_sealed onwards, and only then")
        if (self.labels_frozen_at is None) != (index < stage_rank(Stage.LABELS_FROZEN)):
            raise ValueError("labels_frozen_at is set from labels_frozen onwards, and only then")
        if (
            self.labels_frozen_at is not None
            and self.primary_sealed_at is not None
            and self.labels_frozen_at < self.primary_sealed_at
        ):
            raise ValueError("labels_frozen_at is earlier than primary_sealed_at")
        if (self.previous_manifest_sha256 is None) != (self.stage is Stage.QUERIES_FROZEN):
            raise ValueError("only the first stage has no previous manifest")
        if (self.supersedes_manifest_sha256 is None) != (self.golden_version == 1):
            raise ValueError(
                "supersedes_manifest_sha256 is set exactly when golden_version is greater than 1"
            )
        if self.stage is Stage.PROVENANCE_UNSEALED:
            provenance = self.entry(Role.PROVENANCE)
            if provenance.file_sha256 != self.provenance_binding:
                raise ValueError("the unsealed provenance does not match its binding")
        if compute_manifest_sha256(self.model_dump(mode="json")) != self.manifest_sha256:
            raise ValueError("manifest_sha256 does not match the manifest body")
        return self

    def entry(self, role: Role) -> ManifestEntry:
        for entry in self.entries:
            if entry.role is role:
                return entry
        raise ContractError(f"stage {self.stage.value} has no {role.value} entry")

    def primary_seal_sha256(self) -> str:
        """The primary round's seal: its recorded file hash, fixed from `primary_sealed`."""
        return self.entry(Role.PRIMARY_JUDGEMENTS).file_sha256

    def sort_key(self) -> tuple[str, ...]:
        return (self.golden_id, f"{self.golden_version:06d}", self.stage.value)


def build_manifest(**fields: Any) -> Manifest:
    """Build a manifest from its fields, computing `manifest_sha256` (never supplied)."""
    if "manifest_sha256" in fields:
        raise ContractError("manifest_sha256 is computed, never supplied")
    draft = {"schema_version": 1, "golden_id": GOLDEN_ID, **fields}
    body = _ManifestBody.model_validate(draft).model_dump(mode="json")
    return Manifest.model_validate({**body, "manifest_sha256": compute_manifest_sha256(body)})


class _ManifestBody(_Record):
    """The manifest without its self-hash (used only to normalize fields before hashing)."""

    golden_id: StrictStr
    golden_version: StrictInt
    stage: Stage
    dataset_id: StrictStr
    dataset_version: StrictStr
    dataset_checksum_sha256: StrictStr
    versions: ManifestVersions
    entries: tuple[ManifestEntry, ...]
    provenance_binding: StrictStr | None = None
    primary_sealed_at: UtcTimestamp | None = None
    labels_frozen_at: UtcTimestamp | None = None
    previous_manifest_sha256: StrictStr | None = None
    supersedes_manifest_sha256: StrictStr | None = None
    review_protocol: StrictStr | None = None

    @model_serializer(mode="wrap")
    def _omit_review_1_protocol(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        return _without_implicit_protocol(handler(self))


def _without_implicit_protocol(body: dict[str, Any]) -> dict[str, Any]:
    """review-1 is implicit: a null `review_protocol` is never serialized, so every manifest
    written before the review-2 amendment keeps its exact bytes and `manifest_sha256`."""
    if body.get("review_protocol") is None:
        body.pop("review_protocol", None)
    return body


def encode_manifest(manifest: Manifest) -> bytes:
    return encode_json_object(manifest)


def decode_manifest(data: bytes) -> Manifest:
    return decode_json_object(data, Manifest)


# ---- entries --------------------------------------------------------------------------------


def decode_role(role: Role, data: bytes) -> list[BaseModel]:
    """Strictly decode a role's file; judgement files must contain only their own round."""
    model = ROLE_MODEL[role]
    if role in OBJECT_ROLES:
        return [decode_json_object(data, model)]
    records = decode_jsonl(data, model)  # type: ignore[type-var]
    wanted = ROLE_ROUND.get(role)
    if wanted is not None and any(r.round is not wanted for r in records):  # type: ignore[attr-defined]
        raise ContractError(f"{role.value} must contain only {wanted.value} judgements")
    return records


def _canonical_bytes(role: Role, records: Sequence[BaseModel]) -> bytes:
    if role in OBJECT_ROLES:
        return encode_json_object(records[0])
    return encode_jsonl(records)  # type: ignore[arg-type]


def entry_for(role: Role, path: str, data: bytes) -> ManifestEntry:
    records = decode_role(role, data)
    return ManifestEntry(
        path=path,
        role=role,
        kind=ROLE_KIND[role],
        file_sha256=sha256_hex(data),
        records_sha256=sha256_hex(_canonical_bytes(role, records)),
        record_count=len(records),
    )


def verify_entry(entry: ManifestEntry, data: bytes, regenerated: bytes | None = None) -> None:
    """Verify recorded bytes against the entry; for a generated entry, also require byte
    identity with a regeneration when one is supplied. Recorded entries are never regenerated."""
    if sha256_hex(data) != entry.file_sha256:
        raise ContractError(f"{entry.path}: file bytes do not match file_sha256")
    try:
        records = decode_role(entry.role, data)
    except CanonicalFormError as exc:
        raise ContractError(f"{entry.path}: {exc}") from None
    if sha256_hex(_canonical_bytes(entry.role, records)) != entry.records_sha256:
        raise ContractError(f"{entry.path}: records do not match records_sha256")
    if len(records) != entry.record_count:
        raise ContractError(f"{entry.path}: record count differs")
    if regenerated is not None:
        if entry.kind is not EntryKind.GENERATED:
            raise ContractError(f"{entry.path}: recorded files are verified, never regenerated")
        if regenerated != data:
            raise ContractError(f"{entry.path}: regeneration is not byte-identical")


# ---- lifecycle ------------------------------------------------------------------------------

_IDENTITY_FIELDS = (
    "golden_id",
    "golden_version",
    "dataset_id",
    "dataset_version",
    "dataset_checksum_sha256",
    "versions",
    "supersedes_manifest_sha256",
)


def validate_transition(previous: Manifest, current: Manifest) -> None:
    """One legal step of the current manifest's protocol. The protocol can change only from
    review-1 (implicit) to review-2, and only on the step into `second_review_sealed`."""
    if previous.review_protocol != current.review_protocol and not (
        previous.review_protocol is None
        and current.review_protocol == REVIEW_PROTOCOL_2
        and current.stage is Stage.SECOND_REVIEW_SEALED
    ):
        raise ContractError(
            "review_protocol can change only from review-1 to review-2, "
            "on the step into second_review_sealed"
        )
    order = stage_order(current.review_protocol)
    if (
        previous.stage not in order
        or current.stage not in order
        or order.index(current.stage) != order.index(previous.stage) + 1
    ):
        raise ContractError(
            f"illegal stage transition {previous.stage.value} → {current.stage.value}"
        )
    if current.previous_manifest_sha256 != previous.manifest_sha256:
        raise ContractError("previous_manifest_sha256 does not point at the previous manifest")
    for name in _IDENTITY_FIELDS:
        if getattr(current, name) != getattr(previous, name):
            raise ContractError(f"{name} changed between stages")
    carried = {e.role: e for e in current.entries}
    for entry in previous.entries:
        if carried.get(entry.role) != entry:
            raise ContractError(f"{entry.role.value} changed after it was frozen")
    if previous.provenance_binding is not None and (
        current.provenance_binding != previous.provenance_binding
    ):
        raise ContractError("provenance_binding changed after pool_frozen")
    if previous.primary_sealed_at is not None and (
        current.primary_sealed_at != previous.primary_sealed_at
    ):
        raise ContractError("primary_sealed_at changed after primary_sealed")
    if previous.labels_frozen_at is not None and (
        current.labels_frozen_at != previous.labels_frozen_at
    ):
        raise ContractError("labels_frozen_at changed after labels_frozen")


def validate_chain(manifests: Sequence[Manifest]) -> None:
    """A complete-so-far chain: starts at queries_frozen, and every step is a legal transition."""
    if not manifests:
        raise ContractError("an empty manifest chain")
    if manifests[0].stage is not Stage.QUERIES_FROZEN:
        raise ContractError("the chain must start at queries_frozen")
    for previous, current in zip(manifests, manifests[1:], strict=False):
        validate_transition(previous, current)


def validate_succession(superseded_terminal: Manifest, successor_first: Manifest) -> None:
    """Golden version N+1 starts a new chain that names version N's terminal manifest. Within
    the new chain, `validate_transition` keeps `supersedes_manifest_sha256` unchanged."""
    if superseded_terminal.stage is not Stage.PROVENANCE_UNSEALED:
        raise ContractError("only a terminal provenance_unsealed manifest can be superseded")
    if successor_first.stage is not Stage.QUERIES_FROZEN:
        raise ContractError("the superseding chain must start at its first stage, queries_frozen")
    if successor_first.golden_id != superseded_terminal.golden_id:
        raise ContractError("a superseding version must keep the golden_id")
    if successor_first.golden_version != superseded_terminal.golden_version + 1:
        raise ContractError("a superseding manifest must be the next golden version")
    if successor_first.supersedes_manifest_sha256 != superseded_terminal.manifest_sha256:
        raise ContractError(
            "supersedes_manifest_sha256 does not point at the superseded terminal manifest"
        )
