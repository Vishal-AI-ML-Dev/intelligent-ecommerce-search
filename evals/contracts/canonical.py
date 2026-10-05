"""Canonical JSON/JSONL bytes and SHA-256 helpers.

Canonical form (one record per line):

* `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` of the record's
  JSON-mode dump; decimals and timestamps are strings, floats and NaN are refused;
* UTF-8, LF line endings, a final newline, no BOM, no blank lines;
* records sorted strictly by their declared `sort_key()` (so duplicates are refused).

The decoder is strict: it refuses anything the encoder would not have produced byte for byte, so
`encode(decode(data)) == data` holds for every accepted input. A file's SHA-256 therefore covers
exactly the recorded bytes, and a deterministic generator can be checked for byte identity.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any, Protocol, Self

from pydantic import BaseModel, ValidationError


class ContractError(ValueError):
    """A golden-dataset contract was violated (base class of every M9 contract error)."""


class CanonicalFormError(ContractError):
    """Bytes are not in canonical form, or a record cannot be represented canonically."""


class SortableRecord(Protocol):
    def sort_key(self) -> tuple[str, ...]: ...

    def model_dump(self, *, mode: str) -> dict[str, Any]: ...

    @classmethod
    def model_validate_json(cls, data: str, *, strict: bool | None = None) -> Self: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _refuse_floats(value: Any, where: str) -> None:
    if isinstance(value, float):
        raise CanonicalFormError(f"{where}: floats are not allowed in canonical records")
    if isinstance(value, dict):
        for key, item in value.items():
            _refuse_floats(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _refuse_floats(item, f"{where}[{index}]")


def canonical_json(model: BaseModel) -> str:
    """The canonical one-line JSON text of a record (no trailing newline)."""
    body = model.model_dump(mode="json")
    _refuse_floats(body, type(model).__name__)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ordered(records: Iterable[SortableRecord]) -> list[SortableRecord]:
    ordered = sorted(records, key=lambda record: record.sort_key())
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.sort_key() == current.sort_key():
            raise CanonicalFormError(f"duplicate record key {current.sort_key()!r}")
    return ordered


def encode_jsonl(records: Iterable[SortableRecord]) -> bytes:
    """Canonical JSONL bytes: sorted by key, unique keys, LF, final newline (empty → b"")."""
    lines = [canonical_json(record) for record in _ordered(records)]  # type: ignore[arg-type]
    return "".join(line + "\n" for line in lines).encode("utf-8")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalFormError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _refuse_number(text: str) -> Any:
    raise CanonicalFormError(f"non-integer JSON number {text!r} is not allowed")


def _text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise CanonicalFormError("a byte-order mark is not allowed")
    if b"\r" in data:
        raise CanonicalFormError("CR characters are not allowed; use LF line endings")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalFormError(f"not valid UTF-8: {exc.reason}") from None


def _parse_line[R: SortableRecord](line: str, model: type[R], where: str) -> R:
    try:
        raw = json.loads(
            line,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_refuse_number,
            parse_constant=_refuse_number,
        )
    except json.JSONDecodeError as exc:
        raise CanonicalFormError(f"{where}: invalid JSON ({exc.msg})") from None
    if not isinstance(raw, dict):
        raise CanonicalFormError(f"{where}: a record must be a JSON object")
    try:
        record = model.model_validate_json(line, strict=True)
    except ValidationError as exc:
        raise CanonicalFormError(f"{where}: invalid {model.__name__}: {exc}") from None
    if canonical_json(record) != line:  # type: ignore[arg-type]
        raise CanonicalFormError(f"{where}: record is not in canonical form")
    return record


def decode_jsonl[R: SortableRecord](data: bytes, model: type[R]) -> list[R]:
    """Strictly decode canonical JSONL bytes into validated records."""
    text = _text(data)
    if text == "":
        return []
    if not text.endswith("\n"):
        raise CanonicalFormError("the final line must end with a newline")
    records: list[R] = []
    for number, line in enumerate(text[:-1].split("\n"), start=1):
        if line == "":
            raise CanonicalFormError(f"line {number}: blank lines are not allowed")
        records.append(_parse_line(line, model, f"line {number}"))
    keys = [record.sort_key() for record in records]
    for number, (previous, current) in enumerate(zip(keys, keys[1:], strict=False), start=2):
        if current == previous:
            raise CanonicalFormError(f"line {number}: duplicate record key {current!r}")
        if current < previous:
            raise CanonicalFormError(f"line {number}: records are not sorted by key")
    return records


def encode_json_object(model: BaseModel) -> bytes:
    """Canonical bytes of a single-object JSON file (one line plus a final newline)."""
    return (canonical_json(model) + "\n").encode("utf-8")


def decode_json_object[R: BaseModel](data: bytes, model: type[R]) -> R:
    text = _text(data)
    if not text.endswith("\n") or "\n" in text[:-1]:
        raise CanonicalFormError("a JSON object file must be exactly one line ending in LF")
    return _parse_line(text[:-1], model, model.__name__)  # type: ignore[type-var]


def records_sha256(records: Sequence[SortableRecord]) -> str:
    """SHA-256 of the canonical re-encoding of decoded records."""
    return sha256_hex(encode_jsonl(records))
