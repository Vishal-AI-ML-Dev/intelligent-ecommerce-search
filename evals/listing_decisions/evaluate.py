"""Offline M14 listing-decision evaluator (report `le-report-1`). No call, download or database.

Inputs:

* an `le-1` set (`contracts.load_set`);
* a raw directory holding each listing's exact bytes as `<raw_sha256>.json` (raw rows stay
  outside git); every file's SHA-256 is checked against its name and the item;
* optionally, recorded Jev outcomes (`le-outcomes-1` JSONL: one header line, then one
  `{"item_id", "outcome"}` line per item). No runner exists in M14-4; a live runner is a later
  approval dependency before M14-7.

For every item of the selected split, M13 normalization, the `jl-1` state and `lr-1` routing are
recomputed from the raw bytes. A recorded outcome must be a `jev` outcome for exactly that state
(`state_sha256`, question set version and asked question ordinals, via `validate_outcome`);
any mismatch refuses the whole evaluation. On clean items, the seller's normalized category (and
subcategory, when labelled) must equal the label: decision 1 puts the source label into the seller
fields M13 reads, and `jl-1` never shows those fields to the model.

Only the dev split is evaluated by default. The test split needs `allow_test_split=True`, which
the report records. The report has no timestamps: the same inputs give the same bytes.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ecommerce_search.decision.listing_models import (
    NONE_LABEL,
    QUESTION_SET_VERSION,
    ListingDecisionMismatch,
    ListingDecisionOutcome,
    validate_outcome,
)
from ecommerce_search.decision.listing_provider import DeterministicListingDecisionProvider
from ecommerce_search.decision.listing_questions import build_listing_state, question_set_digest
from ecommerce_search.decision.listing_routing import POLICY_VERSION, policy_digest, route_listing
from ecommerce_search.normalization.intake import (
    ListingIntakeError,
    RawListing,
    parse_raw_listing,
    raw_sha256,
)
from ecommerce_search.normalization.models import ListingNormalization, Outcome
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.rules import Basis
from evals.listing_decisions.contracts import EvalItem, EvalSet, EvalSetError, Split, Task
from evals.listing_decisions.corruption import Pair, corruption_report
from evals.listing_decisions.measurability import registry
from evals.listing_decisions.metrics import (
    ItemRecord,
    baseline_task_metrics,
    descriptive,
    task_metrics,
)

REPORT_SCHEMA = "le-report-1"
OUTCOMES_SCHEMA = "le-outcomes-1"
DISCLOSURE = (
    "External source labels from uploader-licensed datasets with unresolved upstream rights; not "
    "human-reviewed gold. Development-scoped evidence only: not held-out, production or "
    "calibration evidence. The human-reviewed M14 requirements remain unmet."
)
NO_OUTCOMES = "no recorded Jev outcomes were supplied"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OutcomesHeader(_Frozen):
    schema_version: Literal["le-outcomes-1"] = OUTCOMES_SCHEMA
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["jev"]
    question_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: bool  # true unless the outcomes are real recorded Jev output


class OutcomeRecord(_Frozen):
    item_id: str
    outcome: ListingDecisionOutcome


def load_raw(raw_dir: Path, item: EvalItem) -> RawListing:
    path = raw_dir / f"{item.raw_sha256}.json"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EvalSetError(
            f"{item.item_id}: raw listing unreadable ({type(exc).__name__})"
        ) from exc
    if raw_sha256(data) != item.raw_sha256:
        raise EvalSetError(f"{item.item_id}: raw listing SHA-256 mismatch")
    try:
        return parse_raw_listing(data)
    except ListingIntakeError as exc:
        raise EvalSetError(f"{item.item_id}: raw listing rejected ({exc.reason.value})") from exc


def load_outcomes(
    path: Path, eval_set: EvalSet
) -> tuple[OutcomesHeader, dict[str, ListingDecisionOutcome]]:
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise EvalSetError(f"cannot read the outcomes ({type(exc).__name__})") from exc
    if not lines:
        raise EvalSetError("the outcomes file is empty")
    try:
        header = OutcomesHeader.model_validate_json(lines[0])
        records = [OutcomeRecord.model_validate_json(line) for line in lines[1:]]
    except ValidationError as exc:
        raise EvalSetError(f"invalid {OUTCOMES_SCHEMA} file: {exc.error_count()} error(s)") from exc
    if header.set_sha256 != eval_set.set_sha256:
        raise EvalSetError("the outcomes were recorded for another set")
    if header.question_set_digest != question_set_digest():
        raise EvalSetError("the outcomes were recorded for another jl-1 question set")
    known = {i.item_id for i in eval_set.items}
    outcomes: dict[str, ListingDecisionOutcome] = {}
    for record in records:
        if record.item_id not in known:
            raise EvalSetError(f"an outcome names an unknown item {record.item_id!r}")
        if record.item_id in outcomes:
            raise EvalSetError(f"{record.item_id}: more than one outcome")
        if record.outcome.provider != "jev":
            raise EvalSetError(f"{record.item_id}: a recorded outcome must come from jev")
        outcomes[record.item_id] = record.outcome
    return header, outcomes


def _seller_values(normalization: ListingNormalization, field: str) -> set[str | None]:
    return {
        c.normalized_value
        for c in normalization.candidates
        if c.field == field and c.basis is Basis.SELLER_FIELD and c.outcome is Outcome.NORMALIZED
    }


def _check_seller_fields(item: EvalItem, normalization: ListingNormalization) -> None:
    for task in Task:
        label = item.label(task)
        if label is None or (task is Task.CATEGORY and label.value == NONE_LABEL):
            continue
        if _seller_values(normalization, task.value) != {label.value}:
            raise EvalSetError(f"{item.item_id}: the seller {task.value} must equal its label")


def _record(item: EvalItem, raw: RawListing, outcome: ListingDecisionOutcome | None) -> ItemRecord:
    normalization = normalize_listing(raw)
    state = build_listing_state(raw, normalization)
    baseline = DeterministicListingDecisionProvider().decide(state)
    jev = None
    if outcome is not None:
        try:
            validate_outcome(state, outcome)
        except ListingDecisionMismatch as exc:
            raise EvalSetError(f"{item.item_id}: the outcome does not answer this state") from exc
        jev = route_listing(normalization, state, outcome)
    return ItemRecord(
        item=item,
        normalization=normalization,
        state=state,
        deterministic=route_listing(normalization, state, baseline),
        outcome=outcome,
        jev=jev,
    )


def _sources(eval_set: EvalSet) -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in eval_set.sources]


def _versions(records: Sequence[ItemRecord]) -> dict[str, Any]:
    m13 = {
        (r.state.m13.rules_version, r.state.m13.rules_digest, r.state.m13.taxonomy_version)
        for r in records
    }
    if len(m13) != 1:
        raise EvalSetError("items were normalized under different M13 rules")
    rules_version, rules_digest, taxonomy_version = m13.pop()
    return {
        "question_set": QUESTION_SET_VERSION,
        "question_set_digest": question_set_digest(),
        "routing_policy": POLICY_VERSION,
        "routing_policy_digest": policy_digest(),
        "normalization_rules": rules_version,
        "normalization_rules_digest": rules_digest,
        "taxonomy": taxonomy_version,
    }


def evaluate(
    eval_set: EvalSet,
    raw_dir: Path,
    outcomes_path: Path | None = None,
    *,
    split: Split = Split.DEV,
    allow_test_split: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """The report and its failed checks (empty when every check passed)."""
    if split is Split.TEST and not allow_test_split:
        raise EvalSetError("the test split is evaluated only with an explicit allow_test_split")
    header, outcomes = (
        (None, {}) if outcomes_path is None else load_outcomes(outcomes_path, eval_set)
    )
    in_split = [i for i in eval_set.items if i.split is split]
    if not in_split:
        raise EvalSetError(f"the set has no {split.value} items")
    raws = {i.item_id: load_raw(raw_dir, i) for i in in_split}
    records = {i.item_id: _record(i, raws[i.item_id], outcomes.get(i.item_id)) for i in in_split}
    clean = [r for r in records.values() if r.item.corruption is None]
    for record in clean:
        _check_seller_fields(record.item, record.normalization)
    pairs = [
        Pair(records[r.item.twin_of], r, raws[r.item.twin_of], raws[r.item.item_id])
        for r in records.values()
        if r.item.twin_of is not None
    ]
    corruption, failed = corruption_report(pairs)
    label_metrics = {}
    for task in Task:
        label_metrics[task.value] = {
            "jev": task_metrics(clean, task)
            if header is not None
            else {"value": None, "reason": NO_OUTCOMES},
            "deterministic_baseline": baseline_task_metrics(clean, task),
        }
    synthetic = eval_set.synthetic_fixture or (header is not None and header.synthetic)
    report = {
        "schema_version": REPORT_SCHEMA,
        "disclosure": DISCLOSURE,
        "synthetic": synthetic,
        "set": {
            "schema_version": eval_set.schema_version,
            "set_version": eval_set.set_version,
            "set_sha256": eval_set.set_sha256,
            "group_key_version": eval_set.group_key_version,
            "synthetic_fixture": eval_set.synthetic_fixture,
        },
        "sources": _sources(eval_set),
        "versions": _versions(list(records.values())),
        "split": {"evaluated": split.value, "test_split_explicitly_allowed": allow_test_split},
        "items": {
            "in_split": len(in_split),
            "clean": len(clean),
            "corrupted": len(pairs),
            "outside_split": len(eval_set.items) - len(in_split),
        },
        "outcomes": None
        if header is None
        else {
            "provider": header.provider,
            "synthetic": header.synthetic,
            "records": len(outcomes),
            "in_split": sum(1 for i in in_split if i.item_id in outcomes),
            "outside_split": sum(1 for k in outcomes if k not in records),
        },
        "measurability": registry(),
        "label_metrics": label_metrics,
        "descriptive": descriptive(clean),
        "corruption": corruption,
        "failed_checks": failed,
    }
    return report, failed


def render(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
