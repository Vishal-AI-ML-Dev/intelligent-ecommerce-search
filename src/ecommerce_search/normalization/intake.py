"""Raw seller-listing intake contract (Milestone 13).

One raw listing is the exact bytes of one JSON object. Intake either returns a `RawListing`
that keeps those bytes unchanged, or raises `ListingIntakeError` with an explicit reason; an
invalid intake is rejected, never repaired. Problems with listing *values* (unknown units,
unsupported attributes, conflicts, ...) are not intake problems: they are recorded later by
normalization. Wrong argument types are programming errors and raise `TypeError`.

Boundaries:

* input is `bytes`, 1 to `MAX_RAW_BYTES` bytes, strict UTF-8 without a byte-order mark;
* the JSON text is one object whose values are scalars (string, number, true/false, null);
* exact duplicate keys and keys that collide after trim + casefold are rejected (never
  silently overwritten); a blank key is rejected;
* NaN/Infinity and strings containing lone surrogates (from `\\ud800`-style escapes) are
  rejected;
* `seller_id` and `seller_listing_ref` (exact key names) are required identity strings,
  validated as given and never trimmed or casefolded.

Numbers keep their exact source literal (`JsonNumber`), so evidence can quote the seller's
text exactly.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StrictStr, ValidationError

from ecommerce_search.normalization.rules import IDENTITY_KEYS, canonical_key

MAX_RAW_BYTES = 65_536
IDENTITY_MAX_LENGTH = 128
_BOM = b"\xef\xbb\xbf"


class IntakeReason(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    BYTE_ORDER_MARK = "byte_order_mark"
    NOT_UTF8 = "not_utf8"
    INVALID_JSON = "invalid_json"
    NOT_AN_OBJECT = "not_an_object"
    DUPLICATE_KEY = "duplicate_key"
    COLLIDING_KEYS = "colliding_keys"
    BLANK_KEY = "blank_key"
    NON_SCALAR_VALUE = "non_scalar_value"
    NON_FINITE_NUMBER = "non_finite_number"
    LONE_SURROGATE = "lone_surrogate"
    MISSING_IDENTITY = "missing_identity"
    INVALID_IDENTITY = "invalid_identity"


class ListingIntakeError(ValueError):
    def __init__(self, reason: IntakeReason, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason.value}: {message}")


def _identity_text(value: str) -> str:
    if not 1 <= len(value) <= IDENTITY_MAX_LENGTH:
        raise ValueError(f"must be 1 to {IDENTITY_MAX_LENGTH} characters")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("must not contain control, format or unassigned characters")
    return value


IdentityText = Annotated[StrictStr, AfterValidator(_identity_text)]


class ListingIdentity(BaseModel):
    """Seller-scoped listing identity. Values are compared exactly (case-sensitive)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seller_id: IdentityText
    seller_listing_ref: IdentityText


@dataclass(frozen=True, slots=True)
class JsonNumber:
    """A JSON number with its exact source literal."""

    literal: str

    def as_decimal(self) -> Decimal:
        return Decimal(self.literal)


RawValue = str | JsonNumber | bool | None


@dataclass(frozen=True)
class RawListing:
    raw_bytes: bytes  # exactly as received
    raw_sha256: str  # SHA-256 of raw_bytes
    identity: ListingIdentity
    fields: tuple[tuple[str, RawValue], ...]  # every key (identity included), in source order

    def value(self, key: str) -> RawValue:
        """Value of an exact key; KeyError when absent."""
        for name, value in self.fields:
            if name == key:
                return value
        raise KeyError(key)

    def has_key(self, key: str) -> bool:
        return any(name == key for name, _ in self.fields)


def raw_sha256(data: bytes) -> str:
    """SHA-256 of the exact raw bytes (no decoding, trimming or re-encoding)."""
    if not isinstance(data, bytes):
        raise TypeError(f"raw listing must be bytes, got {type(data).__name__}")
    return hashlib.sha256(data).hexdigest()


def raw_value_text(value: RawValue) -> str | None:
    """Evidence text of a raw scalar: strings verbatim, numbers as their source literal,
    booleans as JSON `true`/`false`; null has no evidence text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, JsonNumber):
        return value.literal
    return value


class _Object(list):
    """Key/value pairs of one decoded JSON object, in source order."""


def _has_lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def _pairs_hook(pairs: list[tuple[str, object]]) -> _Object:
    seen: dict[str, str] = {}
    for key, _ in pairs:
        if _has_lone_surrogate(key):
            raise ListingIntakeError(IntakeReason.LONE_SURROGATE, "a key contains a lone surrogate")
        folded = canonical_key(key)
        if not folded:
            raise ListingIntakeError(IntakeReason.BLANK_KEY, "a key is empty or whitespace")
        if folded in seen:
            if seen[folded] == key:
                raise ListingIntakeError(IntakeReason.DUPLICATE_KEY, f"duplicate key {key!r}")
            raise ListingIntakeError(
                IntakeReason.COLLIDING_KEYS,
                f"keys {seen[folded]!r} and {key!r} collide after trim + casefold",
            )
        seen[folded] = key
    return _Object(pairs)


def _non_finite(literal: str) -> None:
    raise ListingIntakeError(IntakeReason.NON_FINITE_NUMBER, f"{literal} is not allowed")


def _decode(data: bytes) -> _Object:
    if not data:
        raise ListingIntakeError(IntakeReason.EMPTY, "no bytes")
    if len(data) > MAX_RAW_BYTES:
        raise ListingIntakeError(
            IntakeReason.TOO_LARGE, f"{len(data)} bytes exceeds {MAX_RAW_BYTES}"
        )
    if data.startswith(_BOM):
        raise ListingIntakeError(IntakeReason.BYTE_ORDER_MARK, "UTF-8 byte-order mark")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ListingIntakeError(IntakeReason.NOT_UTF8, str(exc)) from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_pairs_hook,
            parse_int=JsonNumber,
            parse_float=JsonNumber,
            parse_constant=_non_finite,
        )
    except ListingIntakeError:
        raise
    except RecursionError as exc:
        # Bounded input (MAX_RAW_BYTES) nested too deeply for the decoder.
        raise ListingIntakeError(IntakeReason.INVALID_JSON, "nesting too deep") from exc
    except ValueError as exc:
        raise ListingIntakeError(IntakeReason.INVALID_JSON, str(exc)) from exc
    if not isinstance(decoded, _Object):
        raise ListingIntakeError(
            IntakeReason.NOT_AN_OBJECT, "top-level JSON value is not an object"
        )
    return decoded


def parse_raw_listing(data: bytes) -> RawListing:
    """Validate one raw listing at intake; the returned `raw_bytes` are `data` unchanged."""
    digest = raw_sha256(data)
    pairs = _decode(data)
    for key, value in pairs:
        if isinstance(value, (_Object, list)):
            raise ListingIntakeError(
                IntakeReason.NON_SCALAR_VALUE, f"value of {key!r} is an object or array"
            )
        if isinstance(value, str) and _has_lone_surrogate(value):
            raise ListingIntakeError(
                IntakeReason.LONE_SURROGATE, f"value of {key!r} contains a lone surrogate"
            )
    values = dict(pairs)
    missing = [key for key in IDENTITY_KEYS if key not in values]
    if missing:
        raise ListingIntakeError(
            IntakeReason.MISSING_IDENTITY, "missing exact key(s): " + ", ".join(missing)
        )
    try:
        identity = ListingIdentity(**{key: values[key] for key in IDENTITY_KEYS})
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ListingIntakeError(IntakeReason.INVALID_IDENTITY, problems) from exc
    return RawListing(raw_bytes=data, raw_sha256=digest, identity=identity, fields=tuple(pairs))
