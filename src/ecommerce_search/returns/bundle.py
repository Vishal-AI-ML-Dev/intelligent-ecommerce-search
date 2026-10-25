"""Bounded parsing of a `return-data-v1` upload (contracts §4.1, §4.2, §4.4; Phase A steps 1-2).

An upload is a sequence of parts (client name, bytes), held in memory only. Client names
select a canonical file name from the allowlist (`bundle.json`, `manifest.json`,
`<entity>.csv`) and are never stored, logged or echoed; any other part is reported as
`unexpected part #n`.

* Step 1, envelope and limits: names, format (one `bundle.json`, or `manifest.json` plus entity
  CSVs), file count, per-file, manifest and bundle byte limits. Any error here is bundle-level,
  and no later step runs.
* Step 2, encoding and parsing: strict UTF-8 (one leading BOM tolerated for CSV only; U+0000 and
  lone surrogates rejected because the database cannot store them), JSON without duplicate keys
  or NaN and with at most 4 nesting levels, a strict RFC 4180 CSV reader (comma, `"` quoting,
  LF or CRLF), exact CSV headers, manifest listing, checksums and row counts, and row limits.
  Errors in the bundle envelope, the manifest or the set's consistency are bundle-level; a CSV
  file that fails to parse is excluded from later steps, and a malformed row is excluded alone.

Library exception text is never kept; every problem becomes a `RowError` code.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns import errors as er
from ecommerce_search.returns.canonical import bundle_sha256
from ecommerce_search.returns.versions import CONTRACT_VERSION

BUNDLE_NAME = "bundle.json"
MANIFEST_NAME = "manifest.json"
CSV_NAMES: dict[str, rc.Entity] = {f"{e.value}.csv": e for e in rc.Entity}
FILE_NAMES = frozenset({BUNDLE_NAME, MANIFEST_NAME, *CSV_NAMES})

MAX_FILES = 12
BUNDLE_MAX_BYTES = 8 * 1024 * 1024
FILE_MAX_BYTES = 5 * 1024 * 1024
MANIFEST_MAX_BYTES = 64 * 1024
BUNDLE_MAX_ROWS = 10_000
ENTITY_MAX_ROWS = 5_000
CSV_MAX_COLUMNS = 30
CSV_FIELD_MAX_BYTES = 4096
JSON_MAX_DEPTH = 4

_BOM = b"\xef\xbb\xbf"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UNQUOTED = re.compile(r"[^,\r\n]*")
_BUNDLE_KEYS = frozenset({"contract_version", "dataset", *(e.value for e in rc.Entity)})
_MANIFEST_KEYS = frozenset({"contract_version", "dataset", "files"})
_MANIFEST_FILE_KEYS = frozenset({"name", "entity", "sha256", "rows"})
_ENTITY_KEYS = frozenset(e.value for e in rc.Entity)

Format = Literal["json", "csv"]


@dataclass(frozen=True, slots=True)
class Part:
    """One uploaded part. `name` is the client's name: used only to pick a canonical name."""

    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class BundleFile:
    """One file of the set under its canonical name (the bytes are kept for a later commit)."""

    name: str
    entity: rc.Entity | None
    media_type: str
    data: bytes
    sha256: str
    row_count: int | None  # None for manifest.json

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class RawRow:
    """One data row before field validation: JSON values, or CSV text (`None` = empty)."""

    entity: rc.Entity
    file: str
    row: int  # 1-based data row
    line: int | None  # CSV physical line where the record starts
    values: Any


@dataclass
class ParsedBundle:
    format: Format | None = None
    files: tuple[BundleFile, ...] = ()
    bundle_sha256: str | None = None
    dataset: Any = None  # raw dataset block, when present
    dataset_file: str | None = None
    rows: dict[rc.Entity, list[RawRow]] = field(default_factory=dict)
    received: dict[str, int] = field(default_factory=dict)
    phases_completed: list[str] = field(default_factory=list)
    excluded_input: bool = False  # a file or row was dropped from later phases after an error
    stopped: bool = False  # a bundle-level error: no later phase runs


class _JsonProblem(Exception):
    def __init__(self, code: str, *, key: object = None, line: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.key = key
        self.line = line


class _CsvSyntax(Exception):
    def __init__(self, line: int) -> None:
        super().__init__("csv syntax")
        self.line = line


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bad_text(text: str) -> bool:
    return "\x00" in text or any(0xD800 <= ord(c) <= 0xDFFF for c in text)


# ---- step 1 --------------------------------------------------------------------------------


def _envelope(parts: Sequence[Part], out: ParsedBundle, errs: list[er.RowError]) -> dict:
    named: dict[str, bytes] = {}
    for position, part in enumerate(parts, start=1):
        if part.name in FILE_NAMES and part.name not in named:
            named[part.name] = part.data
        else:
            errs.append(er.row_error("unexpected_file", er.unexpected_part(position)))
    if BUNDLE_NAME in named:
        out.format, anchor = "json", BUNDLE_NAME
        for name in sorted(named.keys() - {BUNDLE_NAME}):
            errs.append(er.row_error("unexpected_file", name))
            del named[name]
    elif MANIFEST_NAME in named:
        out.format, anchor = "csv", MANIFEST_NAME
    else:
        anchor = MANIFEST_NAME if named else BUNDLE_NAME
        errs.append(er.row_error("missing_file", anchor))
    if len(parts) > MAX_FILES:
        errs.append(er.row_error("too_many_files", anchor))
    for name, data in named.items():
        if len(data) > (MANIFEST_MAX_BYTES if name == MANIFEST_NAME else FILE_MAX_BYTES):
            errs.append(er.row_error("file_too_large", name))
    if sum(len(p.data) for p in parts) > BUNDLE_MAX_BYTES:
        errs.append(er.row_error("bundle_too_large", anchor))
    return named


# ---- JSON ----------------------------------------------------------------------------------


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonProblem("format", key=key)
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise _JsonProblem("format")


def _decode_json(data: bytes) -> Any:
    """Parse strict UTF-8 JSON (no BOM); raise `_JsonProblem` with a `RowError` code."""
    if data.startswith(_BOM):
        raise _JsonProblem("encoding")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _JsonProblem("encoding") from None
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise _JsonProblem("format", line=exc.lineno) from None
    except (ValueError, RecursionError):  # e.g. an over-long integer literal, deep nesting
        raise _JsonProblem("format") from None


def _walk(value: Any) -> tuple[int, list[tuple]]:
    """Nesting depth (containers) and paths of strings or keys that are not storable text."""
    depth, bad = 0, []
    stack: list[tuple[Any, tuple, int]] = [(value, (), 1)]
    while stack:
        node, path, level = stack.pop()
        if isinstance(node, dict):
            depth = max(depth, level)
            for key, child in node.items():
                if _bad_text(key):
                    bad.append(path)
                stack.append((child, (*path, key), level + 1))
        elif isinstance(node, list):
            depth = max(depth, level)
            stack.extend((child, (*path, i), level + 1) for i, child in enumerate(node))
        elif isinstance(node, str) and _bad_text(node):
            bad.append(path)
    return depth, bad


def _load_json(name: str, data: bytes, errs: list[er.RowError]) -> tuple[Any, list[tuple]]:
    """Decoded value and the paths of non-storable strings, or (None, []) after an error."""
    try:
        value = _decode_json(data)
    except _JsonProblem as problem:
        errs.append(
            er.row_error(
                problem.code,
                name,
                line=problem.line,
                field=None if problem.key is None else er.safe_key(problem.key),
            )
        )
        return None, []
    depth, bad = _walk(value)
    if depth > JSON_MAX_DEPTH:
        errs.append(er.row_error("format", name))
        return None, []
    if not isinstance(value, dict):
        errs.append(er.row_error("type", name, pointer=""))
        return None, []
    return value, bad


def _top_level(name: str, value: dict, allowed: frozenset[str], errs: list[er.RowError]) -> bool:
    ok = True
    for key in value:
        if key not in allowed:
            errs.append(
                er.row_error(
                    "unknown_column", name, pointer=er.pointer(key), field=er.safe_key(key)
                )
            )
            ok = False
    for key in ("contract_version", "dataset"):
        if key not in value:
            errs.append(er.row_error("required", name, pointer=er.pointer(key), field=key))
            ok = False
    version = value.get("contract_version")
    if "contract_version" in value and version != CONTRACT_VERSION:
        code = "contract_version" if isinstance(version, str) else "type"
        errs.append(er.row_error(code, name, pointer="/contract_version", field="contract_version"))
        ok = False
    if "dataset" in value and not isinstance(value["dataset"], dict):
        errs.append(er.row_error("type", name, pointer="/dataset", field="dataset"))
        ok = False
    return ok


def _bad_rows(name: str, bad: list[tuple], out: ParsedBundle, errs: list[er.RowError]) -> set:
    """Report non-storable text; returns the (entity, index) rows to exclude."""
    rows = set()
    for path in bad:
        entity = rc.Entity(path[0]) if path and path[0] in _ENTITY_KEYS else None
        if entity is not None and len(path) > 1 and isinstance(path[1], int):
            rows.add((entity, path[1]))
            errs.append(
                er.row_error(
                    "encoding",
                    name,
                    entity=entity.value,
                    row=path[1] + 1,
                    pointer=er.pointer(*path),
                    field=er.safe_key(path[2]) if len(path) > 2 else None,
                )
            )
        else:
            errs.append(er.row_error("encoding", name, pointer=er.pointer(*path)))
            if path and path[0] == "dataset":
                out.dataset = None
    return rows


def _parse_json_bundle(data: bytes, out: ParsedBundle, errs: list[er.RowError]) -> None:
    value, bad = _load_json(BUNDLE_NAME, data, errs)
    if value is None or not _top_level(BUNDLE_NAME, value, _BUNDLE_KEYS, errs):
        out.stopped = True
        return
    arrays: dict[rc.Entity, list] = {}
    for entity in rc.Entity:
        items = value.get(entity.value, [])
        if not isinstance(items, list):
            errs.append(
                er.row_error(
                    "type", BUNDLE_NAME, entity=entity.value, pointer=er.pointer(entity.value)
                )
            )
            out.stopped = True
            continue
        arrays[entity] = items
        if len(items) > ENTITY_MAX_ROWS:
            errs.append(er.row_error("too_many_rows", BUNDLE_NAME, entity=entity.value))
            out.stopped = True
    if sum(len(items) for items in arrays.values()) > BUNDLE_MAX_ROWS:
        errs.append(er.row_error("too_many_rows", BUNDLE_NAME))
        out.stopped = True
    if out.stopped:
        return
    out.dataset, out.dataset_file = value["dataset"], BUNDLE_NAME
    excluded = _bad_rows(BUNDLE_NAME, bad, out, errs)
    out.excluded_input = bool(excluded)
    for entity, items in arrays.items():
        out.received[entity.value] = len(items)
        out.rows[entity] = [
            RawRow(entity, BUNDLE_NAME, i + 1, None, item)
            for i, item in enumerate(items)
            if (entity, i) not in excluded
        ]
    out.files = (
        BundleFile(
            BUNDLE_NAME, None, "application/json", data, _sha(data), sum(out.received.values())
        ),
    )


# ---- CSV -----------------------------------------------------------------------------------


def _csv_records(text: str):
    """Yield (start line, fields) per record; raise `_CsvSyntax` on an RFC 4180 violation."""
    i, n, line = 0, len(text), 1
    while i < n:
        start, fields = line, []
        while True:
            if i < n and text[i] == '"':
                i += 1
                parts = []
                while True:
                    j = text.find('"', i)
                    if j < 0:
                        raise _CsvSyntax(start)
                    parts.append(text[i:j])
                    line += text.count("\n", i, j)
                    if j + 1 < n and text[j + 1] == '"':
                        parts.append('"')
                        i = j + 2
                        continue
                    i = j + 1
                    break
                value = "".join(parts)
                if i < n and text[i] not in ",\r\n":
                    raise _CsvSyntax(line)
            else:
                match = _UNQUOTED.match(text, i)
                value, i = match.group(), match.end()
                if '"' in value:
                    raise _CsvSyntax(line)
            fields.append(value)
            if i >= n:
                yield start, fields
                return
            if text[i] == ",":
                i += 1
                continue
            if text[i] == "\n":
                i, line = i + 1, line + 1
                break
            if text[i + 1 : i + 2] == "\n":  # text[i] is "\r"
                i, line = i + 2, line + 1
                break
            raise _CsvSyntax(line)  # a bare CR
        yield start, fields


def _parse_manifest(data: bytes, out: ParsedBundle, errs: list[er.RowError]) -> dict | None:
    """Validated manifest listing {name: (sha256, rows)}, or None after a bundle-level error."""
    value, bad = _load_json(MANIFEST_NAME, data, errs)
    if value is None or not _top_level(MANIFEST_NAME, value, _MANIFEST_KEYS, errs):
        return None
    if bad:
        for path in bad:
            errs.append(er.row_error("encoding", MANIFEST_NAME, pointer=er.pointer(*path)))
        return None
    files = value.get("files")
    if not isinstance(files, list) or not files:
        code = "required" if files is None or files == [] else "type"
        errs.append(er.row_error(code, MANIFEST_NAME, pointer="/files", field="files"))
        return None
    listing: dict[str, tuple[str, int]] = {}
    before = len(errs)
    for i, entry in enumerate(files):
        at = ("files", i)
        if not isinstance(entry, dict):
            errs.append(er.row_error("type", MANIFEST_NAME, pointer=er.pointer(*at)))
            continue
        for key in entry.keys() - _MANIFEST_FILE_KEYS:
            errs.append(
                er.row_error(
                    "unknown_column",
                    MANIFEST_NAME,
                    pointer=er.pointer(*at, key),
                    field=er.safe_key(key),
                )
            )
        for key in sorted(_MANIFEST_FILE_KEYS - entry.keys()):
            errs.append(
                er.row_error("required", MANIFEST_NAME, pointer=er.pointer(*at, key), field=key)
            )
        name, entity, sha, rows = (entry.get(k) for k in ("name", "entity", "sha256", "rows"))
        if "name" in entry and name not in CSV_NAMES:
            errs.append(
                er.row_error("format", MANIFEST_NAME, pointer=er.pointer(*at, "name"), field="name")
            )
        elif name in listing:
            errs.append(
                er.row_error(
                    "duplicate_in_bundle",
                    MANIFEST_NAME,
                    pointer=er.pointer(*at, "name"),
                    field="name",
                )
            )
        if "entity" in entry and name in CSV_NAMES and CSV_NAMES[name].value != entity:
            errs.append(
                er.row_error(
                    "format", MANIFEST_NAME, pointer=er.pointer(*at, "entity"), field="entity"
                )
            )
        if "sha256" in entry and not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
            errs.append(
                er.row_error(
                    "format", MANIFEST_NAME, pointer=er.pointer(*at, "sha256"), field="sha256"
                )
            )
        if "rows" in entry:
            if not isinstance(rows, int) or isinstance(rows, bool):
                errs.append(
                    er.row_error(
                        "type", MANIFEST_NAME, pointer=er.pointer(*at, "rows"), field="rows"
                    )
                )
            elif not 0 <= rows <= ENTITY_MAX_ROWS:
                code = "too_many_rows" if rows > ENTITY_MAX_ROWS else "out_of_range"
                errs.append(
                    er.row_error(code, MANIFEST_NAME, pointer=er.pointer(*at, "rows"), field="rows")
                )
        if len(errs) == before and name not in listing:
            listing[name] = (sha, rows)
    if len(errs) > before:
        return None
    if sum(rows for _, rows in listing.values()) > BUNDLE_MAX_ROWS:
        errs.append(er.row_error("too_many_rows", MANIFEST_NAME))
        return None
    out.dataset, out.dataset_file = value["dataset"], MANIFEST_NAME
    return listing


def _parse_csv(
    name: str, data: bytes, expected_rows: int, errs: list[er.RowError]
) -> tuple[list[RawRow], bool] | None:
    """(rows, whether a malformed row was dropped), or None if the file fails as a whole."""
    entity = CSV_NAMES[name]
    columns = set(rc.ENTITY_MODELS[entity].model_fields)
    body = data.removeprefix(_BOM)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        errs.append(er.row_error("encoding", name, entity=entity.value))
        return None
    records = _csv_records(text)
    try:
        first = next(records, None)
        if first is None:
            errs.append(er.row_error("format", name, entity=entity.value, line=1))
            return None
        _, header = first
        if len(header) > CSV_MAX_COLUMNS:
            errs.append(er.row_error("too_many_columns", name, entity=entity.value, line=1))
            return None
        failed = False
        seen: set[str] = set()
        for column in header:
            if column in seen:
                errs.append(
                    er.row_error(
                        "format", name, entity=entity.value, line=1, field=er.safe_key(column)
                    )
                )
                failed = True
            elif column not in columns:
                errs.append(
                    er.row_error(
                        "unknown_column",
                        name,
                        entity=entity.value,
                        line=1,
                        field=er.safe_key(column),
                    )
                )
                failed = True
            seen.add(column)
        for column in sorted(columns - seen):
            errs.append(
                er.row_error("missing_column", name, entity=entity.value, line=1, field=column)
            )
            failed = True
        if failed:
            return None
        rows: list[RawRow] = []
        count = 0
        for line, fields in records:
            count += 1
            if count > ENTITY_MAX_ROWS:
                break
            problem = None
            if len(fields) != len(header):
                problem = er.row_error("format", name, entity=entity.value, row=count, line=line)
            else:
                for column, value in zip(header, fields, strict=True):
                    if len(value.encode("utf-8")) > CSV_FIELD_MAX_BYTES:
                        problem = er.row_error(
                            "field_too_large",
                            name,
                            entity=entity.value,
                            row=count,
                            line=line,
                            field=column,
                        )
                        break
                    if "\x00" in value:
                        problem = er.row_error(
                            "encoding",
                            name,
                            entity=entity.value,
                            row=count,
                            line=line,
                            field=column,
                        )
                        break
            if problem is not None:
                errs.append(problem)
                continue
            values = {c: (v if v != "" else None) for c, v in zip(header, fields, strict=True)}
            rows.append(RawRow(entity, name, count, line, values))
    except _CsvSyntax as exc:
        errs.append(er.row_error("format", name, entity=entity.value, line=exc.line))
        return None
    if count != expected_rows:
        errs.append(er.row_error("row_count_mismatch", name, entity=entity.value))
        return None
    return rows, len(rows) != count


def _parse_csv_set(named: dict[str, bytes], out: ParsedBundle, errs: list[er.RowError]) -> None:
    listing = _parse_manifest(named[MANIFEST_NAME], out, errs)
    if listing is None:
        out.stopped = True
        return
    uploaded = named.keys() - {MANIFEST_NAME}
    for name in sorted(listing.keys() - uploaded):
        errs.append(er.row_error("missing_file", name))
    for name in sorted(uploaded - listing.keys()):
        errs.append(er.row_error("unexpected_file", name))
    for name in sorted(uploaded & listing.keys()):
        if _sha(named[name]) != listing[name][0]:
            errs.append(er.row_error("checksum_mismatch", name))
    if errs:
        out.stopped = True
        return
    manifest = named[MANIFEST_NAME]
    files = [BundleFile(MANIFEST_NAME, None, "application/json", manifest, _sha(manifest), None)]
    for name in sorted(listing):
        entity, expected = CSV_NAMES[name], listing[name][1]
        parsed = _parse_csv(name, named[name], expected, errs)
        out.received[entity.value] = expected
        files.append(BundleFile(name, entity, "text/csv", named[name], _sha(named[name]), expected))
        if parsed is None:
            out.excluded_input = True
            continue
        out.rows[entity], dropped = parsed
        out.excluded_input = out.excluded_input or dropped
    out.files = tuple(files)


# ---- entry point ---------------------------------------------------------------------------


def parse_upload(parts: Sequence[Part], collector: er.ErrorCollector) -> ParsedBundle:
    """Run Phase A steps 1-2 and add their errors to `collector`."""
    if not all(isinstance(p, Part) and isinstance(p.data, bytes) for p in parts):
        raise TypeError("parts must be Part instances holding bytes")
    out = ParsedBundle()
    errs: list[er.RowError] = []
    named = _envelope(parts, out, errs)
    collector.add_phase(errs)
    out.phases_completed.append(er.PHASE_ENVELOPE)
    if errs:
        out.stopped = True
        return out
    out.bundle_sha256 = bundle_sha256({name: _sha(data) for name, data in named.items()})
    errs = []
    if out.format == "json":
        _parse_json_bundle(named[BUNDLE_NAME], out, errs)
    else:
        _parse_csv_set(named, out, errs)
    collector.add_phase(errs)
    if not out.stopped:
        out.phases_completed.append(er.PHASE_PARSING)
    return out
