"""Append-only persistence of seller listings and their normalizations (Milestone 13).

`submit_listing(engine, raw)` stores one accepted raw listing and its normalization under the
current rules in ONE transaction, serialized per listing identity by a PostgreSQL advisory lock
(keyed from SHA-256, never Python's process-randomized `hash()`):

* the raw input is stored as its exact bytes with its `raw_sha256`;
* a raw input identical to the identity's latest version creates no new version; any other
  input becomes the next version (A -> B -> A gives versions 1, 2, 3);
* a version normalized again under the same `rules_version` must reproduce the recorded rules
  digest, taxonomy version and output hash exactly (idempotent). Anything else raises
  `NormalizationMismatch` and writes nothing: changed behaviour needs a new rules version;
* a failure anywhere rolls back the whole submission.

`load_version` reads a stored version and its runs AS RECORDED, as plain records that are not
re-validated against today's rules, and `verify_integrity` checks them against their own
stored hashes. Old rules are never re-run, and a historical digest is not required to equal
the current one. Nothing here reads or writes catalog, search or embedding tables.
"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from ecommerce_search.models.listings import (
    ListingCandidate,
    ListingFinding,
    ListingNormalizationRun,
    ListingVersion,
)
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import (
    ListingIdentity,
    ListingIntakeError,
    RawListing,
    parse_raw_listing,
)
from ecommerce_search.normalization.models import (
    AttributeCandidate,
    Finding,
    ListingNormalization,
)
from ecommerce_search.normalization.normalizer import normalize_listing

UNDEFINED_TABLE = "42P01"  # PostgreSQL SQLSTATE: undefined_table


class StoreError(Exception):
    """A user-facing persistence error with an already-sanitized message."""


class NotMigrated(StoreError):
    pass


class NormalizationMismatch(StoreError):
    pass


class ListingNotFound(StoreError):
    pass


class IntegrityFailure(StoreError):
    pass


def listing_lock_key(identity: ListingIdentity) -> int:
    """Deterministic signed 64-bit advisory-lock key for one listing identity.

    SHA-256 of an unambiguous encoding of the exact identity (never Python's `hash()`)."""
    encoded = nr.canonical_json([identity.seller_id, identity.seller_listing_ref])
    digest = hashlib.sha256(b"listing-normalization:" + encoded).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


# ---- records as stored ------------------------------------------------------------------


@dataclass(frozen=True)
class StoredCandidate:
    field: str
    basis: str
    source_key: str
    raw_text: str
    span_start: int | None
    span_end: int | None
    outcome: str
    normalized_value: str | None
    value_type: str | None
    unit: str | None
    rule_id: str


@dataclass(frozen=True)
class StoredFinding:
    kind: str
    field: str | None
    related_field: str | None
    source_key: str | None
    message: str


def candidate_record(candidate: AttributeCandidate) -> StoredCandidate:
    return StoredCandidate(**candidate.model_dump(mode="json"))


def finding_record(finding: Finding) -> StoredFinding:
    return StoredFinding(**finding.model_dump(mode="json"))


@dataclass(frozen=True)
class StoredRun:
    rules_version: str
    rules_digest: str
    taxonomy_version: str
    output_sha256: str
    candidate_count: int
    finding_count: int
    created_at: datetime | None
    candidates: tuple[StoredCandidate, ...]
    findings: tuple[StoredFinding, ...]
    candidate_ordinals: tuple[int, ...]
    finding_ordinals: tuple[int, ...]

    def recorded_output(self) -> dict:
        """The exact structure `ListingNormalization.output_sha256` covers, rebuilt from the
        stored rows in stored order."""
        return {
            "rules_version": self.rules_version,
            "rules_digest": self.rules_digest,
            "taxonomy_version": self.taxonomy_version,
            "candidates": [asdict(c) for c in self.candidates],
            "findings": [asdict(f) for f in self.findings],
        }

    def recomputed_output_sha256(self) -> str:
        return hashlib.sha256(nr.canonical_json(self.recorded_output())).hexdigest()


@dataclass(frozen=True)
class StoredVersion:
    seller_id: str
    seller_listing_ref: str
    version_number: int
    version_count: int  # the identity's latest version number
    raw_bytes: bytes
    raw_sha256: str
    submitted_at: datetime | None
    runs: tuple[StoredRun, ...]  # ordered by created time, then id


def verify_integrity(stored: StoredVersion) -> None:
    """Raise IntegrityFailure unless the stored record is self-consistent.

    Checks the raw bytes against `raw_sha256` and the stored identity, and each run's rows
    against its counts, ordinals and `output_sha256`. It does not re-run any rules."""
    problems: list[str] = []
    if hashlib.sha256(stored.raw_bytes).hexdigest() != stored.raw_sha256:
        problems.append("raw bytes do not match raw_sha256")
    else:
        try:
            identity = parse_raw_listing(stored.raw_bytes).identity
        except ListingIntakeError as exc:
            problems.append(f"stored raw input does not pass intake ({exc.reason.value})")
        else:
            if (identity.seller_id, identity.seller_listing_ref) != (
                stored.seller_id,
                stored.seller_listing_ref,
            ):
                problems.append("raw input identity does not match the stored identity")
    for run in stored.runs:
        where = f"run {run.rules_version!r}"
        if len(run.candidates) != run.candidate_count:
            problems.append(
                f"{where}: {len(run.candidates)} candidate rows, recorded {run.candidate_count}"
            )
        if len(run.findings) != run.finding_count:
            problems.append(
                f"{where}: {len(run.findings)} finding rows, recorded {run.finding_count}"
            )
        if run.candidate_ordinals != tuple(range(len(run.candidates))) or (
            run.finding_ordinals != tuple(range(len(run.findings)))
        ):
            problems.append(f"{where}: row ordinals are not contiguous from 0")
        if run.recomputed_output_sha256() != run.output_sha256:
            problems.append(f"{where}: stored rows do not reproduce output_sha256")
    if problems:
        raise IntegrityFailure(
            f"listing {stored.seller_id!r}/{stored.seller_listing_ref!r} version "
            f"{stored.version_number} failed its integrity check: " + "; ".join(problems)
        )


# ---- write ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmitResult:
    seller_id: str
    seller_listing_ref: str
    version_number: int
    version_created: bool
    run_created: bool
    raw_sha256: str
    rules_version: str
    output_sha256: str

    def summary(self) -> str:
        version = "created" if self.version_created else "unchanged (identical to latest)"
        run = "created" if self.run_created else "unchanged (identical output)"
        return (
            f"listing {self.seller_id!r}/{self.seller_listing_ref!r}: version "
            f"{self.version_number} {version}; normalization {self.rules_version} {run}; "
            f"raw_sha256={self.raw_sha256} output_sha256={self.output_sha256}"
        )


def _add_version(session: Session, raw: RawListing, number: int) -> ListingVersion:
    version = ListingVersion(
        seller_id=raw.identity.seller_id,
        seller_listing_ref=raw.identity.seller_listing_ref,
        version_number=number,
        raw_bytes=raw.raw_bytes,
        raw_sha256=raw.raw_sha256,
    )
    session.add(version)
    session.flush()
    return version


def _add_run(
    session: Session, version: ListingVersion, result: ListingNormalization, output_sha256: str
) -> ListingNormalizationRun:
    run = ListingNormalizationRun(
        listing_version_id=version.id,
        rules_version=result.rules_version,
        rules_digest=result.rules_digest,
        taxonomy_version=result.taxonomy_version,
        output_sha256=output_sha256,
        candidate_count=len(result.candidates),
        finding_count=len(result.findings),
    )
    session.add(run)
    session.flush()
    return run


def _add_candidates(session: Session, run: ListingNormalizationRun, result: ListingNormalization):
    session.add_all(
        ListingCandidate(run_id=run.id, ordinal=i, **asdict(candidate_record(c)))
        for i, c in enumerate(result.candidates)
    )
    session.flush()


def _add_findings(session: Session, run: ListingNormalizationRun, result: ListingNormalization):
    session.add_all(
        ListingFinding(run_id=run.id, ordinal=i, **asdict(finding_record(f)))
        for i, f in enumerate(result.findings)
    )
    session.flush()


def _require_same(
    run: ListingNormalizationRun,
    result: ListingNormalization,
    output_sha256: str,
    raw: RawListing,
    number: int,
) -> None:
    recorded = (run.rules_digest, run.taxonomy_version, run.output_sha256)
    if recorded != (result.rules_digest, result.taxonomy_version, output_sha256):
        raise NormalizationMismatch(
            f"listing {raw.identity.seller_id!r}/{raw.identity.seller_listing_ref!r} version "
            f"{number} was already normalized under rules version {run.rules_version!r} with "
            f"rules_digest={run.rules_digest} taxonomy_version={run.taxonomy_version!r} "
            f"output_sha256={run.output_sha256}, but today's output under the same rules "
            f"version has rules_digest={result.rules_digest} "
            f"taxonomy_version={result.taxonomy_version!r} output_sha256={output_sha256}. "
            "Changed normalization behaviour needs a new rules version. Nothing was written"
        )


def submit_listing(engine: Engine, raw: RawListing) -> SubmitResult:
    """Store one accepted raw listing and its current normalization atomically."""
    if not isinstance(raw, RawListing):
        raise TypeError(f"expected RawListing, got {type(raw).__name__}")
    result = normalize_listing(raw)  # pure: before any database work
    result.verify_evidence(raw)
    output_sha256 = result.output_sha256()
    identity = raw.identity
    try:
        with Session(engine) as session, session.begin():
            # Serialize every read-then-write decision for this identity.
            session.execute(select(func.pg_advisory_xact_lock(listing_lock_key(identity))))
            latest = session.scalars(
                select(ListingVersion)
                .where(
                    ListingVersion.seller_id == identity.seller_id,
                    ListingVersion.seller_listing_ref == identity.seller_listing_ref,
                )
                .order_by(ListingVersion.version_number.desc())
                .limit(1)
            ).first()
            same = (
                latest is not None
                and latest.raw_sha256 == raw.raw_sha256
                and latest.raw_bytes == raw.raw_bytes
            )
            if same:
                version, version_created = latest, False
            else:
                number = 1 if latest is None else latest.version_number + 1
                version, version_created = _add_version(session, raw, number), True
            run = session.scalars(
                select(ListingNormalizationRun).where(
                    ListingNormalizationRun.listing_version_id == version.id,
                    ListingNormalizationRun.rules_version == result.rules_version,
                )
            ).first()
            if run is None:
                run = _add_run(session, version, result, output_sha256)
                _add_candidates(session, run, result)
                _add_findings(session, run, result)
                run_created = True
            else:
                _require_same(run, result, output_sha256, raw, version.version_number)
                run_created = False
            number = version.version_number
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNDEFINED_TABLE:
            raise NotMigrated(
                "the database is not migrated to the current revision (a listing table is "
                "missing); run `uv run alembic upgrade head` and retry. Nothing was written"
            ) from None
        raise
    except IntegrityError as exc:
        # Expected only if something bypassed the advisory lock; never echo SQL or parameters.
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        detail = f" ({name})" if name else ""
        raise StoreError(
            f"the submission conflicts with stored listing history{detail}. Nothing was written"
        ) from None
    return SubmitResult(
        seller_id=identity.seller_id,
        seller_listing_ref=identity.seller_listing_ref,
        version_number=number,
        version_created=version_created,
        run_created=run_created,
        raw_sha256=raw.raw_sha256,
        rules_version=result.rules_version,
        output_sha256=output_sha256,
    )


# ---- read -------------------------------------------------------------------------------


def _stored_run(session: Session, run: ListingNormalizationRun) -> StoredRun:
    candidates = list(
        session.scalars(
            select(ListingCandidate)
            .where(ListingCandidate.run_id == run.id)
            .order_by(ListingCandidate.ordinal)
        )
    )
    findings = list(
        session.scalars(
            select(ListingFinding)
            .where(ListingFinding.run_id == run.id)
            .order_by(ListingFinding.ordinal)
        )
    )
    candidate_fields = StoredCandidate.__dataclass_fields__
    finding_fields = StoredFinding.__dataclass_fields__
    return StoredRun(
        rules_version=run.rules_version,
        rules_digest=run.rules_digest,
        taxonomy_version=run.taxonomy_version,
        output_sha256=run.output_sha256,
        candidate_count=run.candidate_count,
        finding_count=run.finding_count,
        created_at=run.created_at,
        candidates=tuple(
            StoredCandidate(**{name: getattr(c, name) for name in candidate_fields})
            for c in candidates
        ),
        findings=tuple(
            StoredFinding(**{name: getattr(f, name) for name in finding_fields}) for f in findings
        ),
        candidate_ordinals=tuple(c.ordinal for c in candidates),
        finding_ordinals=tuple(f.ordinal for f in findings),
    )


def load_version(
    session: Session, seller_id: str, seller_listing_ref: str, version_number: int | None = None
) -> StoredVersion:
    """One stored version (default: the latest) with all its runs, as recorded."""
    identity = (
        ListingVersion.seller_id == seller_id,
        ListingVersion.seller_listing_ref == seller_listing_ref,
    )
    latest = session.scalar(select(func.max(ListingVersion.version_number)).where(*identity))
    if latest is None:
        raise ListingNotFound(f"no stored listing {seller_id!r}/{seller_listing_ref!r}")
    wanted = latest if version_number is None else version_number
    version = session.scalars(
        select(ListingVersion).where(*identity, ListingVersion.version_number == wanted)
    ).first()
    if version is None:
        raise ListingNotFound(
            f"listing {seller_id!r}/{seller_listing_ref!r} has no version {wanted} "
            f"(latest is {latest})"
        )
    runs = session.scalars(
        select(ListingNormalizationRun)
        .where(ListingNormalizationRun.listing_version_id == version.id)
        .order_by(ListingNormalizationRun.created_at, ListingNormalizationRun.id)
    )
    return StoredVersion(
        seller_id=version.seller_id,
        seller_listing_ref=version.seller_listing_ref,
        version_number=version.version_number,
        version_count=latest,
        raw_bytes=bytes(version.raw_bytes),
        raw_sha256=version.raw_sha256,
        submitted_at=version.submitted_at,
        runs=tuple(_stored_run(session, run) for run in runs),
    )


def read_listing(
    engine: Engine, seller_id: str, seller_listing_ref: str, version_number: int | None = None
) -> StoredVersion:
    """`load_version` in a READ ONLY transaction, then `verify_integrity`."""
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            stored = load_version(session, seller_id, seller_listing_ref, version_number)
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNDEFINED_TABLE:
            raise NotMigrated(
                "the database is not migrated to the current revision (a listing table is "
                "missing); run `uv run alembic upgrade head`"
            ) from None
        raise
    verify_integrity(stored)
    return stored
