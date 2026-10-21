"""Typed listing-normalization output contracts (Milestone 13).

A `ListingNormalization` holds two separate kinds of record:

* `AttributeCandidate`: one value read from the seller's structured fields
  (`basis=seller_field`) or an unverified extraction from title/description text, with its
  evidence (exact source key, exact raw text, code-point span for text) and the rule that
  produced it. Only `outcome=normalized` carries a normalized value; unsupported, uncertain
  and invalid values keep their raw evidence only. None of them is a verified product fact.
* `Finding`: a listing-level relation (conflict, contradiction, compatibility, missing value)
  or a supported key the seller supplied as null or blank (`empty_value`). A present-but-empty
  key is not a valid supplied value, but it is not reported as missing either.

There is deliberately no resolved or winning value. Two different normalized values for one
field always come with a `value_conflict` finding, and a conflict finding is only valid when
such values exist.

`output_sha256()` is the deterministic output hash. It covers exactly the model fields
(`rules_version`, `rules_digest`, `taxonomy_version`, `candidates`, `findings`), with
candidates and findings in canonical order, encoded by `rules.canonical_json`. It excludes the
raw bytes and raw SHA-256, the listing identity, version numbers, database ids and timestamps,
so two raw inputs that differ only in key order (different raw SHA-256) can share one output
hash.
"""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import RawListing, RawValue, raw_value_text
from ecommerce_search.normalization.rules import Basis, ValueType

_INTEGER = re.compile(r"0|[1-9][0-9]*")


def is_empty_value(value: RawValue) -> bool:
    """Null, or a string with no non-whitespace characters: not a valid supplied value."""
    return value is None or (isinstance(value, str) and not value.split())


class Outcome(StrEnum):
    NORMALIZED = "normalized"  # deterministic rule produced a canonical value
    UNSUPPORTED = "unsupported"  # no supported rule applies (unknown unit, brand, term, ...)
    UNCERTAIN = "uncertain"  # text could mean more than one thing; not interpreted
    INVALID = "invalid"  # recognised but impossible (outside M2 hard bounds, bad precision)


class FindingKind(StrEnum):
    VALUE_CONFLICT = "value_conflict"
    CROSS_FIELD_CONTRADICTION = "cross_field_contradiction"
    INCOMPATIBLE_ATTRIBUTE = "incompatible_attribute"
    INCOMPATIBLE_SUBCATEGORY = "incompatible_subcategory"
    BRAND_CATEGORY_MISMATCH = "brand_category_mismatch"
    UNSUPPORTED_ATTRIBUTE = "unsupported_attribute"
    COMPATIBILITY_UNCHECKED = "compatibility_unchecked"
    MISSING_REQUIRED = "missing_required"
    MISSING_EXPECTED = "missing_expected"
    EMPTY_VALUE = "empty_value"  # a supported key is present with null or a blank string


_FIELD_KINDS = frozenset(
    {
        FindingKind.VALUE_CONFLICT,
        FindingKind.CROSS_FIELD_CONTRADICTION,
        FindingKind.INCOMPATIBLE_ATTRIBUTE,
        FindingKind.INCOMPATIBLE_SUBCATEGORY,
        FindingKind.BRAND_CATEGORY_MISMATCH,
        FindingKind.MISSING_REQUIRED,
        FindingKind.MISSING_EXPECTED,
        FindingKind.EMPTY_VALUE,
    }
)
_KEYED_KINDS = frozenset({FindingKind.UNSUPPORTED_ATTRIBUTE, FindingKind.EMPTY_VALUE})
_EXPECTED_ANY = frozenset(f for fields in nr.EXPECTED_FIELDS.values() for f in fields)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _check_value(field: str, value: str) -> None:
    kind = nr.FIELD_VALUE_TYPES[field]
    if kind is ValueType.INTEGER:
        if not _INTEGER.fullmatch(value):
            raise ValueError(f"{field}: {value!r} is not a canonical non-negative integer")
    elif kind is ValueType.DECIMAL:
        dp = nr.FIELD_DECIMAL_PLACES[field]
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{field}: {value!r} is not a decimal") from exc
        if not number.is_finite() or number.as_tuple().exponent != -dp or str(number) != value:
            raise ValueError(f"{field}: {value!r} is not canonical with {dp} decimal place(s)")
    elif kind is ValueType.BOOLEAN:
        if value not in ("true", "false"):
            raise ValueError(f"{field}: {value!r} is not 'true' or 'false'")
    elif kind is ValueType.ENUM:
        allowed = {str(m.value) for m in nr.FIELD_ENUMS[field]}
        if value not in allowed:
            raise ValueError(f"{field}: {value!r} is not one of {sorted(allowed)}")
    elif not value or " ".join(value.split()) != value:
        raise ValueError(f"{field}: text must be non-empty with collapsed whitespace")


class AttributeCandidate(_Frozen):
    field: str
    basis: Basis
    source_key: str = Field(min_length=1)  # the exact raw key
    raw_text: str = Field(min_length=1)  # exact raw evidence (see intake.raw_value_text)
    span_start: int | None = None  # code-point offsets into the raw text value (text bases)
    span_end: int | None = None
    outcome: Outcome
    normalized_value: str | None = None
    value_type: ValueType | None = None
    unit: str | None = None
    rule_id: str

    @model_validator(mode="after")
    def _contract(self) -> Self:
        if self.field not in nr.FIELD_VALUE_TYPES:
            raise ValueError(f"unknown listing field {self.field!r}")
        rule = nr.RULES_BY_ID.get(self.rule_id)
        if rule is None:
            raise ValueError(f"unregistered rule {self.rule_id!r}")
        if self.field not in rule.fields or self.basis not in rule.bases:
            raise ValueError(f"rule {self.rule_id!r} does not apply to {self.field}/{self.basis}")
        source = nr.canonical_key(self.source_key)
        if self.basis is Basis.SELLER_FIELD:
            if self.span_start is not None or self.span_end is not None:
                raise ValueError("a seller_field candidate has no text span")
            if source != self.field and not (rule.cross_field and source in rule.fields):
                raise ValueError(f"source key {self.source_key!r} does not match {self.field}")
        else:
            if source != self.basis.value:
                raise ValueError(f"source key {self.source_key!r} is not the {self.basis} field")
            if self.span_start is None or self.span_end is None:
                raise ValueError("a text candidate needs a span")
            if not 0 <= self.span_start < self.span_end:
                raise ValueError("span must satisfy 0 <= start < end")
            if self.span_end - self.span_start != len(self.raw_text):
                raise ValueError("span length does not match raw_text")
        if self.outcome is Outcome.NORMALIZED:
            if self.normalized_value is None:
                raise ValueError("a normalized candidate needs normalized_value")
            if self.value_type is not nr.FIELD_VALUE_TYPES[self.field]:
                raise ValueError(f"value_type must be {nr.FIELD_VALUE_TYPES[self.field]}")
            if self.unit != nr.FIELD_UNITS.get(self.field):
                raise ValueError(f"unit must be {nr.FIELD_UNITS.get(self.field)!r}")
            _check_value(self.field, self.normalized_value)
        elif (self.normalized_value, self.value_type, self.unit) != (None, None, None):
            raise ValueError(f"a {self.outcome} candidate carries no normalized value")
        return self

    def sort_key(self) -> tuple:
        return (
            self.field,
            self.basis.value,
            self.source_key,
            -1 if self.span_start is None else self.span_start,
            -1 if self.span_end is None else self.span_end,
            self.raw_text,
            self.rule_id,
            self.outcome.value,
            self.normalized_value or "",
        )


class Finding(_Frozen):
    kind: FindingKind
    field: str | None = None
    related_field: str | None = None  # cross_field_contradiction only
    source_key: str | None = None  # unsupported_attribute/empty_value only: the exact raw key
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def _contract(self) -> Self:
        kind = self.kind
        if kind in _FIELD_KINDS:
            if self.field not in nr.FIELD_VALUE_TYPES:
                raise ValueError(f"{kind} needs a known field, got {self.field!r}")
        elif self.field is not None:
            raise ValueError(f"{kind} has no field")
        if (kind is FindingKind.CROSS_FIELD_CONTRADICTION) != (self.related_field is not None):
            raise ValueError("related_field is required for, and only for, a contradiction")
        if self.related_field is not None and (
            self.related_field not in nr.FIELD_VALUE_TYPES or self.related_field == self.field
        ):
            raise ValueError(f"invalid related_field {self.related_field!r}")
        if (kind in _KEYED_KINDS) != (self.source_key is not None):
            raise ValueError(
                "source_key is required for, and only for, unsupported_attribute and empty_value"
            )
        if self.source_key is not None:
            if not self.source_key:
                raise ValueError("source_key must not be empty")
            source = nr.canonical_key(self.source_key)
            if kind is FindingKind.EMPTY_VALUE:
                if source != self.field:
                    raise ValueError(f"source key {self.source_key!r} does not match {self.field}")
            elif source in nr.IDENTITY_KEYS:
                raise ValueError("identity keys are not listing attributes")
            elif source in nr.FIELD_VALUE_TYPES:
                raise ValueError(f"{self.source_key!r} is a supported listing field")
        if kind is FindingKind.INCOMPATIBLE_ATTRIBUTE and self.field not in nr.ATTRIBUTE_FIELDS:
            raise ValueError("incompatible_attribute applies to category attributes only")
        if kind is FindingKind.INCOMPATIBLE_SUBCATEGORY and self.field != "subcategory":
            raise ValueError("incompatible_subcategory applies to subcategory")
        if kind is FindingKind.BRAND_CATEGORY_MISMATCH and self.field != "brand":
            raise ValueError("brand_category_mismatch applies to brand")
        if kind is FindingKind.MISSING_REQUIRED and self.field not in nr.REQUIRED_FIELDS:
            raise ValueError(f"{self.field!r} is not a required field")
        if kind is FindingKind.MISSING_EXPECTED and self.field not in _EXPECTED_ANY:
            raise ValueError(f"{self.field!r} is not an expected field")
        return self

    def sort_key(self) -> tuple:
        return (
            self.kind.value,
            self.field or "",
            self.related_field or "",
            self.source_key or "",
            self.message,
        )


class EvidenceMismatch(ValueError):
    """A candidate's or finding's evidence does not match the raw listing."""


class ListingNormalization(_Frozen):
    rules_version: str = Field(min_length=1)
    rules_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    taxonomy_version: str = Field(min_length=1)
    candidates: tuple[AttributeCandidate, ...] = ()
    findings: tuple[Finding, ...] = ()

    @field_validator("candidates")
    @classmethod
    def _canonical_candidates(
        cls, value: tuple[AttributeCandidate, ...]
    ) -> tuple[AttributeCandidate, ...]:
        ordered = tuple(sorted(value, key=AttributeCandidate.sort_key))
        if len(set(ordered)) != len(ordered):
            raise ValueError("duplicate candidate")
        return ordered

    @field_validator("findings")
    @classmethod
    def _canonical_findings(cls, value: tuple[Finding, ...]) -> tuple[Finding, ...]:
        ordered = tuple(sorted(value, key=Finding.sort_key))
        if len(set(ordered)) != len(ordered):
            raise ValueError("duplicate finding")
        return ordered

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        values: dict[str, set[str]] = {}
        for c in self.candidates:
            if c.outcome is Outcome.NORMALIZED:
                values.setdefault(c.field, set()).add(c.normalized_value)
        conflicted = sorted(f for f, v in values.items() if len(v) > 1)
        conflicts = [f.field for f in self.findings if f.kind is FindingKind.VALUE_CONFLICT]
        if sorted(conflicts) != conflicted:
            raise ValueError(
                f"value_conflict findings {sorted(conflicts)} must be exactly the fields with "
                f"different normalized values {conflicted}"
            )
        seller_fields = {c.field for c in self.candidates if c.basis is Basis.SELLER_FIELD}
        seller_fields |= {f.field for f in self.findings if f.kind is FindingKind.EMPTY_VALUE}
        for f in self.findings:
            missing = f.kind in (FindingKind.MISSING_REQUIRED, FindingKind.MISSING_EXPECTED)
            if missing and f.field in seller_fields:
                raise ValueError(f"{f.field} is reported missing but the seller supplied it")
        return self

    def canonical_json(self) -> bytes:
        return nr.canonical_json(self.model_dump(mode="json"))

    def output_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def verify_evidence(self, raw: RawListing) -> None:
        """Raise EvidenceMismatch unless every candidate quotes the raw listing exactly, every
        unsupported key exists in it and every empty_value key holds null or a blank string."""
        for c in self.candidates:
            if not raw.has_key(c.source_key):
                raise EvidenceMismatch(f"key {c.source_key!r} is not in the raw listing")
            value = raw.value(c.source_key)
            if c.basis is Basis.SELLER_FIELD:
                if raw_value_text(value) != c.raw_text:
                    raise EvidenceMismatch(f"{c.source_key!r}: raw_text does not match the value")
            elif not isinstance(value, str) or value[c.span_start : c.span_end] != c.raw_text:
                raise EvidenceMismatch(f"{c.source_key!r}: span does not quote raw_text")
        for f in self.findings:
            if f.source_key is not None and not raw.has_key(f.source_key):
                raise EvidenceMismatch(f"key {f.source_key!r} is not in the raw listing")
            if f.kind is FindingKind.EMPTY_VALUE and not is_empty_value(raw.value(f.source_key)):
                raise EvidenceMismatch(f"{f.source_key!r}: value is not null or blank")
