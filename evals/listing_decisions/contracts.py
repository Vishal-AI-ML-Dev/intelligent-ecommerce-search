"""External listing-decision evaluation set, schema `le-1` (Milestone 14, offline).

A set records items built from external, uploader-licensed sources (M14-0: Flipkart, Smartphones,
Laptop Price) and the labels those sources provide. **These are external source labels, not
human-reviewed gold.** No label kind for human review exists here; the human-reviewed M14
requirements stay unmet (see `docs/m14-progress.md`, evaluation amendment).

* `SourceManifest`: where a source came from (dataset, version, file SHA-256s), its declared
  licence and who declared it, the unresolved upstream-rights caveat, adapter and mapping versions.
* `EvalItem`: one listing, referenced by the SHA-256 of its exact M13 raw bytes (raw rows stay
  outside git), with its duplicate group, split, labels, and for a synthetic corruption its clean
  twin and `CorruptionRef`.
* `TaskLabel`: a category or subcategory value from the closed `jl-1` label sets, with its
  `label_kind` (`source` or `mapped`), the source field it came from and, for `mapped`, the
  mapping version. Subcategory labels carry a scoring policy fixed by the mapping, before any
  prediction is read: `exact`, or `excluded_ambiguous_mapping` (only `sports`, whose source label
  may include running shoes while the taxonomy separates `running`).
* `CorruptionRef`: `label_kind=synthetic_corruption`, the `corrupt-2` rule, its seed, the exact
  raw key it changed and the original raw value. Robustness tests only, never task labels.

`set_sha256` is the SHA-256 of the canonical JSON of the set without that field. It detects
accidental edits only; it is not a signature.
"""

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from ecommerce_search.decision.listing_models import CATEGORY_LABELS, SUBCATEGORY_LABELS
from ecommerce_search.normalization import rules as nr
from evals.contracts.canonical import ContractError
from evals.listing_decisions.measurability import MEASURED_SUBCATEGORY_LABELS

SET_SCHEMA = "le-1"
CORRUPTION_RULES_VERSION = "corrupt-2"
_SHA256 = r"^[0-9a-f]{64}$"
_ID = r"^[a-z0-9][a-z0-9_.:-]{0,127}$"
AMBIGUOUS_SPORTS_REASON = (
    "the source label 'sports' may include running shoes, which the taxonomy separates as "
    "'running'; exact subcategory accuracy is not scored for it, whatever is predicted"
)


class EvalSetError(ContractError):
    """The evaluation set, its raw listings or recorded outcomes are invalid or do not match."""


class LabelKind(StrEnum):
    SOURCE = "source"  # read directly from a source field or dataset membership
    MAPPED = "mapped"  # derived from a source field by a versioned deterministic mapping
    SYNTHETIC_CORRUPTION = "synthetic_corruption"  # a seeded, versioned robustness corruption


class Task(StrEnum):
    CATEGORY = "category"
    SUBCATEGORY = "subcategory"


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


class SubcategoryScoring(StrEnum):
    EXACT = "exact"
    EXCLUDED_AMBIGUOUS_MAPPING = "excluded_ambiguous_mapping"


class CorruptionRule(StrEnum):
    CATEGORY_SWAP = "category_swap"
    ATTRIBUTE_SWAP = "attribute_swap"
    OUT_OF_SET_SUBCATEGORY = "out_of_set_subcategory"
    INSTRUCTION_INJECTION = "instruction_injection"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceFile(_Frozen):
    name: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256)
    size_bytes: int = Field(ge=0)


class SourceManifest(_Frozen):
    source_id: str = Field(pattern=_ID)
    dataset: str = Field(min_length=1)  # e.g. the dataset page or slug
    dataset_version: str = Field(min_length=1)
    files: tuple[SourceFile, ...] = Field(min_length=1)
    declared_licence: str = Field(min_length=1)
    licence_declared_by: str = Field(min_length=1)
    rights_caveat: str = Field(min_length=1)  # unresolved upstream rights, stated plainly
    adapter_version: str = Field(min_length=1)
    mapping_version: str | None = None


class TaskLabel(_Frozen):
    task: Task
    value: str
    label_kind: Literal[LabelKind.SOURCE, LabelKind.MAPPED]
    origin_field: str = Field(min_length=1)  # the source column, or "dataset_membership"
    mapping_version: str | None = None
    scoring: SubcategoryScoring | None = None  # subcategory labels only

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if (self.label_kind is LabelKind.MAPPED) != (self.mapping_version is not None):
            raise ValueError("a mapping version is given exactly for mapped labels")
        if self.task is Task.CATEGORY:
            if self.value not in CATEGORY_LABELS:
                raise ValueError(f"unknown category label {self.value!r}")
            if self.scoring is not None:
                raise ValueError("only subcategory labels carry a scoring policy")
            return self
        if self.value not in SUBCATEGORY_LABELS:
            raise ValueError(f"unknown subcategory label {self.value!r}")
        if self.value not in MEASURED_SUBCATEGORY_LABELS:
            raise ValueError(f"no external source supports a {self.value!r} label (le-measure-1)")
        if self.scoring is None:
            raise ValueError("a subcategory label states its scoring policy")
        if self.scoring is SubcategoryScoring.EXCLUDED_AMBIGUOUS_MAPPING and self.value != "sports":
            raise ValueError("only 'sports' has an ambiguous mapping")
        return self


class CorruptionRef(_Frozen):
    label_kind: Literal[LabelKind.SYNTHETIC_CORRUPTION] = LabelKind.SYNTHETIC_CORRUPTION
    rule: CorruptionRule
    rules_version: Literal["corrupt-2"] = CORRUPTION_RULES_VERSION
    seed: int = Field(ge=0)
    target_key: str = Field(min_length=1)  # the exact raw key that was changed
    original_value: str | None = None  # the twin's raw value text under target_key


class EvalItem(_Frozen):
    item_id: str = Field(pattern=_ID)
    source_id: str = Field(pattern=_ID)
    source_row_id: str = Field(min_length=1)
    group_key: str = Field(min_length=1)
    split: Split
    raw_sha256: str = Field(pattern=_SHA256)
    labels: tuple[TaskLabel, ...] = ()
    twin_of: str | None = None
    corruption: CorruptionRef | None = None

    @model_validator(mode="after")
    def _valid(self) -> Self:
        tasks = [label.task for label in self.labels]
        if len(tasks) != len(set(tasks)):
            raise ValueError("at most one label per task")
        if (self.twin_of is None) != (self.corruption is None):
            raise ValueError("a corrupted item names its clean twin, and only a corrupted one")
        if self.corruption is not None and self.labels:
            raise ValueError("a corrupted item carries no task label; it is scored by its rule")
        return self

    def label(self, task: Task) -> TaskLabel | None:
        return next((label for label in self.labels if label.task is task), None)


class EvalSet(_Frozen):
    schema_version: Literal["le-1"] = SET_SCHEMA
    set_version: str = Field(min_length=1)
    group_key_version: str = Field(min_length=1)
    synthetic_fixture: bool  # true for software-test sets: every report over it is synthetic
    sources: tuple[SourceManifest, ...] = Field(min_length=1)
    items: tuple[EvalItem, ...] = Field(min_length=1)
    set_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _valid(self, info: ValidationInfo) -> Self:
        source_ids = [s.source_id for s in self.sources]
        if source_ids != sorted(set(source_ids)):
            raise ValueError("sources are unique and sorted by source_id")
        item_ids = [i.item_id for i in self.items]
        if item_ids != sorted(set(item_ids)):
            raise ValueError("items are unique and sorted by item_id")
        mapping = {s.source_id: s.mapping_version for s in self.sources}
        by_id = {i.item_id: i for i in self.items}
        for item in self.items:
            if item.source_id not in mapping:
                raise ValueError(f"{item.item_id}: unknown source {item.source_id!r}")
            for label in item.labels:
                if (
                    label.label_kind is LabelKind.MAPPED
                    and label.mapping_version != mapping[item.source_id]
                ):
                    raise ValueError(f"{item.item_id}: mapping version differs from its source")
            if item.twin_of is not None:
                twin = by_id.get(item.twin_of)
                if twin is None or twin.corruption is not None:
                    raise ValueError(f"{item.item_id}: its twin must be a clean item of the set")
                if (twin.split, twin.group_key, twin.source_id) != (
                    item.split,
                    item.group_key,
                    item.source_id,
                ):
                    raise ValueError(f"{item.item_id}: a twin shares source, group and split")
        groups: dict[str, Split] = {}
        for item in self.items:
            if groups.setdefault(item.group_key, item.split) is not item.split:
                raise ValueError(f"group {item.group_key!r} crosses splits")
        draft = isinstance(info.context, dict) and info.context.get("draft") is True
        if not draft and self.set_sha256 != set_digest(self):
            raise ValueError("set_sha256 does not match the set")
        return self

    def item(self, item_id: str) -> EvalItem:
        return next(i for i in self.items if i.item_id == item_id)

    def source(self, source_id: str) -> SourceManifest:
        return next(s for s in self.sources if s.source_id == source_id)


def set_digest(value: EvalSet) -> str:
    """SHA-256 of the canonical JSON of the set (defaults included) without `set_sha256`."""
    data = value.model_dump(mode="json")
    data.pop("set_sha256")
    return hashlib.sha256(nr.canonical_json(data)).hexdigest()


def load_set(path: Path) -> EvalSet:
    try:
        return EvalSet.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise EvalSetError(f"invalid {SET_SCHEMA} set: {exc.error_count()} error(s)") from exc
    except OSError as exc:
        raise EvalSetError(f"cannot read the set: {type(exc).__name__}") from exc


def dump_set(data: dict) -> bytes:
    """Canonical bytes of a set given as JSON-mode data, with its `set_sha256` filled in."""
    draft = EvalSet.model_validate_json(
        json.dumps(dict(data) | {"set_sha256": "0" * 64}), context={"draft": True}
    )
    final = draft.model_dump(mode="json") | {"set_sha256": set_digest(draft)}
    EvalSet.model_validate_json(json.dumps(final))
    return nr.canonical_json(final)
