"""`returns verify`: compare a release with the stored records of its dataset version (§4.7).

The release side is the given upload (a JSON bundle or CSV set): Phase A steps 1-4
(`validate_upload`, in memory) give its records, their `record_sha256` and the release
`records_sha256`. The stored side is read in one READ ONLY, REPEATABLE READ transaction:
the dataset row by (`dataset_id`, `dataset_version`), then `source_record_id` and
`record_sha256` of every entity row of that dataset version. Per entity it reports:

* `missing`: in the release, not stored;
* `extra`: stored, not in the release;
* `changed`: stored under the same natural key with a different `record_sha256`.

Status `match` means equal `records_sha256` and an equal dataset descriptor; `mismatch` means
any difference; `invalid` means the release itself failed Phase A, and the database is not
read. Verify never writes and takes no lock. It reads no raw file contents, no catalog table
and no free text, so the API role can run it. Nothing is logged, and no value, file name,
credential or library message reaches the report or an exception.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import SQLAlchemyError

from ecommerce_search.models import returns as rm
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.validation import validate_upload
from ecommerce_search.returns.versions import CONTRACT_VERSION

# The declared verify surface (AC-13 (5)); a test pins it exactly. It only reads.
__all__ = ("VerifyCounts", "VerifyReport", "VerifyStorageError", "verify_dataset")

E = rc.Entity
VerifyStatus = Literal["match", "mismatch", "invalid"]
Checkpoint = Callable[[str, Connection], None]


class VerifyStorageError(RuntimeError):
    """The stored records could not be read; nothing was written."""

    def __init__(self) -> None:
        super().__init__("the stored records could not be read")


class VerifyCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected: int
    stored: int
    missing: int
    extra: int
    changed: int


class VerifyReport(BaseModel):
    """Release versus stored content of one dataset version. Counts are empty when `invalid`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = CONTRACT_VERSION
    status: VerifyStatus
    dataset_id: str | None = None
    dataset_version: int | None = None
    checked_at: Annotated[datetime, PlainSerializer(cf.format_timestamp, return_type=str)]
    dataset_stored: bool | None = None
    descriptor_matches: bool | None = None
    expected_records_sha256: str | None = None
    stored_records_sha256: str | None = None
    counts: dict[str, VerifyCounts] = {}
    error_count: int = 0
    error_count_is_lower_bound: bool = False
    errors_truncated: bool = False
    errors: tuple[er.RowError, ...] = ()


def _stored(
    conn: Connection, descriptor: rc.DatasetDescriptor
) -> tuple[str | None, dict[E, dict[str, str]]]:
    """The stored descriptor hash (None when the dataset version is not stored) and, per
    entity, `source_record_id` -> `record_sha256`."""
    datasets = rm.ReturnDataset.__table__
    row = conn.execute(
        select(datasets.c.id, datasets.c.descriptor_sha256).where(
            datasets.c.dataset_id == descriptor.dataset_id,
            datasets.c.dataset_version == descriptor.dataset_version,
        )
    ).first()
    stored: dict[E, dict[str, str]] = {e: {} for e in E}
    if row is None:
        return None, stored
    for entity in E:
        table = rm.ENTITY_ROWS[entity].__table__
        result = conn.execute(
            select(table.c.source_record_id, table.c.record_sha256).where(
                table.c.dataset_pk == row.id
            )
        )
        stored[entity] = {rid: sha for rid, sha in result}
    return row.descriptor_sha256, stored


def verify_dataset(
    engine: Engine,
    parts: Sequence[Part],
    *,
    checked_at: datetime | None = None,
    checkpoint: Checkpoint | None = None,
) -> VerifyReport:
    """Compare the release in `parts` with what is stored for its dataset version.

    Never writes. `checkpoint` is a test hook called once inside the read transaction
    (`snapshot`)."""
    checked_at = datetime.now(UTC) if checked_at is None else checked_at
    upload = validate_upload(parts, checked_at=checked_at)
    a = upload.report
    if a.error_count or upload.dataset is None:
        return VerifyReport(
            status="invalid",
            checked_at=checked_at,
            error_count=a.error_count,
            error_count_is_lower_bound=a.error_count_is_lower_bound,
            errors_truncated=a.errors_truncated,
            errors=a.errors,
        )
    descriptor = upload.dataset
    expected = {
        e: {
            v.record.source_record_id(): v.record.record_sha256() for v in upload.records.get(e, ())
        }
        for e in E
    }
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
            conn.begin(),
        ):
            conn.execute(text("SET TRANSACTION READ ONLY"))
            if checkpoint is not None:
                checkpoint("snapshot", conn)
            stored_descriptor, stored = _stored(conn, descriptor)
            conn.rollback()
    except SQLAlchemyError:
        raise VerifyStorageError() from None

    counts = {}
    for e in E:
        want, have = expected[e], stored[e]
        counts[e.value] = VerifyCounts(
            expected=len(want),
            stored=len(have),
            missing=sum(1 for rid in want if rid not in have),
            extra=sum(1 for rid in have if rid not in want),
            changed=sum(1 for rid, sha in want.items() if rid in have and have[rid] != sha),
        )
    expected_sha = cf.records_sha256((e.value, r, s) for e in E for r, s in expected[e].items())
    stored_sha = cf.records_sha256((e.value, r, s) for e in E for r, s in stored[e].items())
    dataset_stored = stored_descriptor is not None
    descriptor_matches = (
        stored_descriptor == descriptor.descriptor_sha256() if dataset_stored else None
    )
    same = dataset_stored and descriptor_matches and expected_sha == stored_sha
    return VerifyReport(
        status="match" if same else "mismatch",
        dataset_id=descriptor.dataset_id,
        dataset_version=descriptor.dataset_version,
        checked_at=checked_at,
        dataset_stored=dataset_stored,
        descriptor_matches=descriptor_matches,
        expected_records_sha256=expected_sha,
        stored_records_sha256=stored_sha,
        counts=counts,
    )
