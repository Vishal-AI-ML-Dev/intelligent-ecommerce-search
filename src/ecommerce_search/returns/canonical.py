"""Canonical forms and hashes for `return-data-v1` (contracts §4.6–4.7).

Accepted input is deliberately narrow, so a JSON bundle and a CSV set of the same records
canonicalize, and therefore hash, identically:

* timestamps: RFC 3339 with an explicit offset and at most 6 fractional digits, canonically UTC
  `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always 6 digits);
* money: a JSON string or CSV text `^(0|[1-9][0-9]{0,7})(\\.[0-9]{1,2})?$` (JSON numbers are
  rejected), canonically exactly 2 fractional digits;
* integers: a JSON integer (not `1.0`, not a boolean) or CSV `^(0|[1-9][0-9]*)$`;
* booleans: JSON `true`/`false` or CSV `true`/`false`;
* text: exact code points, never trimmed or normalized.

Only ASCII digits are accepted: Python's `\\d` would also match other scripts' digits.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ecommerce_search.returns.versions import CONTRACT_VERSION

_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,6}))?([Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_MONEY = re.compile(r"(0|[1-9][0-9]{0,7})(\.[0-9]{1,2})?")
_CSV_INTEGER = re.compile(r"0|[1-9][0-9]*")


class CanonicalFormError(ValueError):
    """A value outside the accepted input forms. `code` is a `RowError` code (§4.8)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_timestamp(value: object) -> datetime:
    """An aware UTC datetime from RFC 3339 text with an explicit offset (no leap seconds)."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalFormError("format", "timestamp needs an explicit offset")
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise CanonicalFormError("type", "timestamp must be a string")
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise CanonicalFormError("format", "timestamp is not RFC 3339 with an offset")
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    if offset in ("Z", "z"):
        tz = UTC
    else:
        off_hours, off_minutes = int(offset[1:3]), int(offset[4:6])
        if off_hours > 23 or off_minutes > 59:
            raise CanonicalFormError("format", "timestamp offset is out of range")
        delta = timedelta(hours=off_hours, minutes=off_minutes)
        tz = timezone(-delta if offset[0] == "-" else delta)
    try:
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),  # 60 (a leap second) is rejected here
            int((fraction or "").ljust(6, "0")),
            tzinfo=tz,
        )
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise CanonicalFormError("format", "timestamp is not a valid instant") from exc


def format_timestamp(value: datetime) -> str:
    """Canonical UTC form, always with 6 fractional digits."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalFormError("format", "timestamp needs an explicit offset")
    u = value.astimezone(UTC)
    return (
        f"{u.year:04d}-{u.month:02d}-{u.day:02d}T"
        f"{u.hour:02d}:{u.minute:02d}:{u.second:02d}.{u.microsecond:06d}Z"
    )


def parse_date(value: object) -> date:
    """A calendar date from `YYYY-MM-DD` text."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise CanonicalFormError("type", "date must be a string")
    match = _DATE.fullmatch(value)
    if match is None:
        raise CanonicalFormError("format", "date is not YYYY-MM-DD")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise CanonicalFormError("format", "date is not a valid calendar date") from exc


def format_date(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def parse_money(value: object) -> Decimal:
    """A non-negative amount from decimal text; JSON numbers (and booleans) are rejected."""
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.quantize(Decimal("0.01")):
            raise CanonicalFormError("format", "amount has more than 2 fractional digits")
        value = format_money(value)
    if not isinstance(value, str):
        raise CanonicalFormError("type", "amount must be a decimal string")
    if _MONEY.fullmatch(value) is None:
        raise CanonicalFormError("format", "amount is not a canonical decimal")
    return Decimal(value).quantize(Decimal("0.01"))


def format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def parse_csv_integer(text: str) -> int:
    """A CSV integer field: ASCII digits, no sign, no leading zeros, no fraction."""
    if _CSV_INTEGER.fullmatch(text) is None:
        raise CanonicalFormError("format", "integer is not canonical")
    return int(text)


def parse_csv_boolean(text: str) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    raise CanonicalFormError("format", "boolean must be true or false")


def canonical_json(value: Mapping[str, Any]) -> str:
    """Sorted keys, `,` and `:` separators, non-ASCII kept. Values must already be canonical."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_sha256(entity: str, record: Mapping[str, Any]) -> str:
    """SHA-256 of `"return-data-v1\\n" + entity + "\\n" + JSON(record)`.

    `record` holds exactly the entity's contract fields in canonical form, absent optional
    values as `None`; batch, file and row references are not part of it."""
    return _sha256(f"{CONTRACT_VERSION}\n{entity}\n{canonical_json(record)}")


def descriptor_sha256(descriptor: Mapping[str, Any]) -> str:
    """The same canonical hash over the `dataset` block (entity name `dataset`)."""
    return record_sha256("dataset", descriptor)


def bundle_sha256(files: Mapping[str, str]) -> str:
    """Identifies one request's exact content: canonical file name -> file SHA-256."""
    lines = "".join(f"{name}\0{sha}\n" for name, sha in sorted(files.items()))
    return _sha256(f"{CONTRACT_VERSION}\n{lines}")


def records_sha256(records: Iterable[tuple[str, str, str]]) -> str:
    """Format-independent content checksum of one dataset version (§4.7).

    `records` are (entity, source_record_id, record_sha256), sorted here by entity then by the
    record ID's bytes (IDs are ASCII, so code-point order is byte order)."""
    lines = sorted(records, key=lambda r: (r[0].encode("utf-8"), r[1].encode("utf-8")))
    return _sha256("".join(f"{e}\t{rid}\t{sha}\n" for e, rid, sha in lines))
