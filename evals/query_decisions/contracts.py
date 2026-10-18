"""Query-decision label schema `qd-1` (Milestone 12 diagnostic batch `m12-diag-dev`).

One human-authored query gets one human decision label per question of Jev question set `jq-1`:
product `category` and `semantic_intent`. These labels are separate from the M9 relevance labels
and are never derived from them.

Gold label kinds (the label set is `jq-1`'s, including its explicit `none` option):

* `label`: exactly one label applies. `none` means no in-catalog category (or no stated intent)
  applies; it is a decision, not an abstention.
* `ambiguous`: two or more labels are plausible and the query does not decide between them
  (`candidates`, at least two, never `none`).
* `multi` (intent only): two or more intents are stated together (`candidates`, at least two).
* `unclear`: the annotator cannot label the query. Unclear queries are excluded from scoring and
  only counted.

`source` marks every query: `human_authored` for real diagnostic queries, or
`synthetic_test_fixture` for software tests only. A synthetic query is never sent to Jev in live
mode and every report over it is marked synthetic.

This is development data. It is never held-out evidence.
"""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.query_understanding.models import Intent
from ecommerce_search.search.query import QueryError, normalize_query
from evals.contracts.canonical import ContractError, encode_jsonl
from evals.contracts.models import AnnotatorHandle, FreeText, Sha256, UtcTimestamp

SCHEMA_VERSION = "qd-1"
DATASET_ID = "m12-diag-dev"
QUESTION_SET_VERSION = "jq-1"
NONE_LABEL = "none"
# The search API's default query length limit (`Settings.search_max_query_length`).
QUERY_MAX_LENGTH = 200

CATEGORY_LABELS: tuple[str, ...] = (*(c.value for c in Category), NONE_LABEL)
INTENT_LABELS: tuple[str, ...] = (*(i.value for i in Intent), NONE_LABEL)

DecisionQueryId = Annotated[StrictStr, Field(pattern=r"^dq-[0-9]{3}$")]
Slug = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class QueryDecisionError(ContractError):
    """A query-decision file or record violates the `qd-1` contract."""


class QuerySource(StrEnum):
    HUMAN_AUTHORED = "human_authored"
    SYNTHETIC_TEST_FIXTURE = "synthetic_test_fixture"


class LanguageStyle(StrEnum):
    ENGLISH = "english"
    HINGLISH = "hinglish"


class GoldKind(StrEnum):
    LABEL = "label"
    AMBIGUOUS = "ambiguous"
    MULTI = "multi"
    UNCLEAR = "unclear"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionQuery(_Frozen):
    """One diagnostic query. `text` must already be normalized exactly as the search API would
    normalize it, so the parser and Jev see the same text the annotator saw."""

    schema_version: Literal["qd-1"]
    dataset_id: Literal["m12-diag-dev"]
    query_id: DecisionQueryId
    text: StrictStr
    source: QuerySource
    author_handle: AnnotatorHandle | None
    language_style: LanguageStyle
    stratum: Slug

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            normalized = normalize_query(self.text, QUERY_MAX_LENGTH)
        except QueryError as exc:
            raise ValueError(str(exc)) from None
        if normalized != self.text:
            raise ValueError("query text must already be normalized (single spaces, trimmed)")
        human = self.source is QuerySource.HUMAN_AUTHORED
        if human != (self.author_handle is not None):
            raise ValueError("human-authored queries need an author handle; synthetic ones none")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id,)


class _Gold(_Frozen):
    kind: GoldKind
    label: StrictStr | None = None
    candidates: tuple[StrictStr, ...] = ()

    LABELS: ClassVar[tuple[str, ...]] = ()
    KINDS: ClassVar[frozenset[GoldKind]] = frozenset()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind not in self.KINDS:
            raise ValueError(f"kind {self.kind.value!r} is not allowed here")
        if self.kind is GoldKind.LABEL:
            if self.label not in self.LABELS or self.candidates:
                raise ValueError("a `label` gold needs exactly one known label and no candidates")
        elif self.kind is GoldKind.UNCLEAR:
            if self.label is not None or self.candidates:
                raise ValueError("an `unclear` gold has no label and no candidates")
        else:
            ordered = tuple(label for label in self.LABELS if label in self.candidates)
            if (
                self.label is not None
                or len(self.candidates) < 2
                or NONE_LABEL in self.candidates
                or self.candidates != ordered
            ):
                raise ValueError(
                    "ambiguous/multi golds need at least two distinct non-none candidates "
                    "in label-set order and no single label"
                )
        return self


class CategoryGold(_Gold):
    LABELS = CATEGORY_LABELS
    KINDS = frozenset({GoldKind.LABEL, GoldKind.AMBIGUOUS, GoldKind.UNCLEAR})


class IntentGold(_Gold):
    LABELS = INTENT_LABELS
    KINDS = frozenset(GoldKind)


class DecisionLabel(_Frozen):
    """One annotator's blind label for one query. `labelling_view` records what the annotator
    was shown: the query text only, never parser or Jev output."""

    schema_version: Literal["qd-1"]
    dataset_id: Literal["m12-diag-dev"]
    query_id: DecisionQueryId
    queries_sha256: Sha256
    annotator_handle: AnnotatorHandle
    labelled_at: UtcTimestamp
    labelling_view: Literal["query_text_only"]
    category: CategoryGold
    semantic_intent: IntentGold
    note: FreeText | None = None

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id,)


class LabelSeal(_Frozen):
    """Binds the exact queries and labels before any Jev call. Written once, never replaced."""

    schema_version: Literal["qd-1"]
    dataset_id: Literal["m12-diag-dev"]
    question_set_version: Literal["jq-1"]
    queries_sha256: Sha256
    labels_sha256: Sha256
    query_count: Annotated[int, Field(ge=1)]
    annotator_handle: AnnotatorHandle
    synthetic: StrictBool
    sealed_at: UtcTimestamp
    evidence_scope: Literal["development_diagnostic_not_held_out"]

    def sort_key(self) -> tuple[str, ...]:
        return (self.queries_sha256,)


def load_queries(data: bytes) -> tuple[DecisionQuery, ...]:
    """Parse the author-written queries file: UTF-8 JSONL, one `DecisionQuery` object per line
    (key order and spacing are free; blank lines, duplicate keys and duplicate ids are refused).
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise QueryDecisionError("the queries file must be UTF-8") from None
    if text.startswith("﻿"):
        raise QueryDecisionError("the queries file must not start with a byte-order mark")
    queries: list[DecisionQuery] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise QueryDecisionError(f"line {number}: blank lines are not allowed")
        try:
            json.loads(line, object_pairs_hook=_no_duplicate_keys)
            queries.append(DecisionQuery.model_validate_json(line, strict=True))
        except (ValueError, ValidationError) as exc:
            raise QueryDecisionError(f"line {number}: invalid query: {exc}") from None
    if not queries:
        raise QueryDecisionError("the queries file is empty")
    ids = [query.query_id for query in queries]
    if len(set(ids)) != len(ids):
        raise QueryDecisionError("query ids must be unique")
    return tuple(sorted(queries, key=lambda query: query.query_id))


def queries_sha256(queries: tuple[DecisionQuery, ...]) -> str:
    """SHA-256 of the canonical re-encoding, so formatting changes do not change the binding."""
    return hashlib.sha256(encode_jsonl(queries)).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result
