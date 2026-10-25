"""Privacy-safe, deterministic `RowError` reporting (contracts §4.8, §5.1).

* `message` is a fixed template per code; it never contains an input value, a client file
  name or library exception text.
* `file` is a canonical file name, or `unexpected part #n` for a part whose client name is not
  allowlisted (client names are never echoed).
* Keys and CSV header names are echoed only when they are contract names or match
  `^[a-z_]{1,40}$`; otherwise they become `<unrecognized>`. JSON pointers are built only from
  such keys and array indices.
* `value_preview` is set only by the caller for non-sensitive scalar fields, through
  `redaction.safe_preview`; never for free text, actor references or dataset text.

Errors are ordered by phase, then file, then row, then field, then code. Inside `bundle.json`
the entity name (alphabetical, as the CSV file names sort) comes between file and row, and line
and pointer break any remaining ties. Collection stops at the 1,000th error; at most 200 are
returned.
"""

import re
from collections.abc import Iterable
from typing import Final

from pydantic import BaseModel, ConfigDict

from ecommerce_search.returns import contracts as rc

# Phase A steps 1-4 (contracts §4.3) run by this package, in order.
PHASE_ENVELOPE: Final = "envelope"
PHASE_PARSING: Final = "parsing"
PHASE_FIELDS: Final = "fields"
PHASE_BUNDLE_STRUCTURE: Final = "bundle_structure"
PHASES: Final = (PHASE_ENVELOPE, PHASE_PARSING, PHASE_FIELDS, PHASE_BUNDLE_STRUCTURE)

COLLECTION_LIMIT: Final = 1000
RETURNED_LIMIT: Final = 200
UNRECOGNIZED: Final = "<unrecognized>"
_SAFE_KEY = re.compile(r"[a-z_]{1,40}")

MESSAGES: Final[dict[str, str]] = {
    "required": "A required value is missing.",
    "type": "The value has the wrong type.",
    "format": "The value is not in the accepted format.",
    "enum": "The value is not one of the allowed values.",
    "too_long": "The text is longer than allowed.",
    "out_of_range": "The number is outside the allowed range.",
    "unknown_column": "The column or key is not part of the contract.",
    "missing_column": "A contract column is missing from the header.",
    "duplicate_in_bundle": "Another record in this bundle has the same key.",
    "unknown_reference": "The referenced record does not exist in this dataset version.",
    "unknown_product": "The referenced product does not exist in the catalog.",
    "snapshot_mismatch": "The listing snapshot does not match the stored catalog record.",
    "inconsistent_reference": "The referenced records do not agree with each other.",
    "temporal_order": "The time is before a time it must follow, or after the check time.",
    "quantity_exceeded": "Returned quantity exceeds the order item quantity.",
    "duplicate_shipment": "The order already has a shipment.",
    "conflict_existing": "A stored record with this key has different content.",
    "not_synthetic": "Only synthetic records are accepted in this contract version.",
    "looks_personal": "The value looks like personal data and is not accepted here.",
    "encoding": "The content is not valid UTF-8 text.",
    "contract_version": "The contract version is not supported.",
    "dataset_mismatch": "The dataset block differs from the stored dataset version.",
    "missing_file": "A required file is missing from the set.",
    "unexpected_file": "The part is not allowed in this set.",
    "checksum_mismatch": "The file checksum does not match the manifest.",
    "row_count_mismatch": "The file row count does not match the manifest.",
    "bundle_too_large": "The bundle is larger than allowed.",
    "file_too_large": "The file is larger than allowed.",
    "too_many_files": "The set has more files than allowed.",
    "too_many_rows": "The bundle or entity has more rows than allowed.",
    "too_many_columns": "The file has more columns than allowed.",
    "field_too_large": "The field is larger than allowed.",
}
ROW_ERROR_CODES: Final = frozenset(MESSAGES)

KNOWN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract_version",
        "dataset",
        "files",
        "name",
        "entity",
        "sha256",
        "rows",
        *rc.DatasetDescriptor.model_fields,
        *(e.value for e in rc.Entity),
        *(f for model in rc.ENTITY_MODELS.values() for f in model.model_fields),
    }
)


class RowError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    entity: str | None = None  # None for bundle-, manifest- and dataset-level errors
    row: int | None = None  # 1-based data row
    line: int | None = None  # 1-based physical line (CSV, and JSON syntax errors)
    pointer: str | None = None
    field: str | None = None
    code: str
    message: str
    value_preview: str | None = None


def safe_key(key: object) -> str:
    if isinstance(key, str) and (key in KNOWN_KEYS or _SAFE_KEY.fullmatch(key)):
        return key
    return UNRECOGNIZED


def pointer(*parts: str | int) -> str:
    """A JSON pointer of safe keys and array indices (safe keys never need RFC 6901 escapes)."""
    return "".join(f"/{p}" if isinstance(p, int) else f"/{safe_key(p)}" for p in parts)


def unexpected_part(position: int) -> str:
    return f"unexpected part #{position}"


def row_error(
    code: str,
    file: str,
    *,
    entity: str | None = None,
    row: int | None = None,
    line: int | None = None,
    pointer: str | None = None,
    field: str | None = None,
    value_preview: str | None = None,
) -> RowError:
    return RowError(
        file=file,
        entity=entity,
        row=row,
        line=line,
        pointer=pointer,
        field=field,
        code=code,
        message=MESSAGES[code],
        value_preview=value_preview,
    )


def _file_key(file: str) -> tuple[int, str, int]:
    if file.startswith("unexpected part #"):
        return (1, "", int(file.rsplit("#", 1)[1]))
    return (0, file, 0)


def sort_key(error: RowError) -> tuple:
    return (
        _file_key(error.file),
        error.entity or "",
        -1 if error.row is None else error.row,
        error.field or "",
        error.code,
        -1 if error.line is None else error.line,
        error.pointer or "",
    )


class ErrorCollector:
    """Collects errors phase by phase in the deterministic report order."""

    def __init__(self, limit: int = COLLECTION_LIMIT) -> None:
        self._limit = limit
        self._errors: list[RowError] = []
        self.stopped = False  # an error beyond the limit was found and dropped

    def add_phase(self, errors: Iterable[RowError]) -> None:
        """Add all errors of one phase; phases must be added in `PHASES` order."""
        for error in sorted(errors, key=sort_key):
            if len(self._errors) >= self._limit:
                self.stopped = True
                return
            self._errors.append(error)

    @property
    def full(self) -> bool:
        return len(self._errors) >= self._limit

    @property
    def count(self) -> int:
        return len(self._errors)

    def returned(self) -> tuple[RowError, ...]:
        return tuple(self._errors[:RETURNED_LIMIT])
