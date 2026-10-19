"""Category-only shadow gate (`cg-shadow-1`) and its frozen threshold (`qd-threshold-1`).

The gate is a pure function for offline evaluation. It is never imported by the search
application: it takes the deterministic parse and a recorded `JevOutcome` and returns a
`GateDecision` record. It reads no `FilterSpec`, builds none and changes no search result,
endpoint, default or `fp-1` rule.

Rule: the deterministic category wins whenever the parser decides one. Otherwise Jev's category
is accepted only when Jev has a unique top label that is not `none` and that label's raw
provider probability is at least the frozen threshold. A Jev `none`, a tie, a fallback, a missing
outcome or a probability below the threshold applies no category. The raw probability is used
exactly as returned; it is not a calibrated gate confidence (FR-DEC-5), which is what the
calibration and evaluation partitions are for.

The frozen threshold is an operator-supplied value written once
(`thresholds/<threshold-id>.json`, exclusive creation). It binds the calibration partition's file
hash, the model and question set, and the acceptance criteria, all fixed before any evaluation
run. Nothing here chooses the threshold or supplies default criteria.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ecommerce_search.decision.provider import JevOutcome
from ecommerce_search.query_understanding.models import QueryUnderstanding
from evals.contracts.canonical import decode_json_object, encode_json_object, sha256_hex
from evals.contracts.models import FreeText, Sha256, UtcTimestamp
from evals.query_decisions.contracts import NONE_LABEL, QueryDecisionError
from evals.query_decisions.partitions import ArtifactId, PartitionRole, load_partition
from evals.runners.golden_io import safe_child, write_exclusive

GATE_RULE_VERSION = "cg-shadow-1"
THRESHOLD_SCHEMA_VERSION = "qd-threshold-1"
THRESHOLDS_DIR = "thresholds"

UnitDecimal = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
GateReason = Literal[
    "deterministic_decided",
    "accepted",
    "below_threshold",
    "jev_none",
    "jev_tie",
    "jev_fallback",
    "jev_missing",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AcceptanceCriteria(_Frozen):
    """Pre-registered acceptance criteria. Every value is required; none has a default."""

    confidence_level: Annotated[Decimal, Field(gt=Decimal("0.5"), lt=1, allow_inf_nan=False)]
    # Wrong commits among accepted fills: the one-sided upper Clopper-Pearson bound must not
    # exceed this. `strict` counts a commit inside an ambiguous query's candidates as wrong.
    wrong_commit_definition: Literal["strict", "lenient"]
    max_wrong_commit_upper_bound: UnitDecimal
    # Correct fills / single-category golds where the parser abstains.
    min_coverage: UnitDecimal
    min_accepted_fills: Annotated[int, Field(ge=1)]
    # (unknown outcomes + fallbacks + not attempted) / members; unclear golds / members.
    max_unknown_or_fallback_rate: UnitDecimal
    max_unclear_rate: UnitDecimal


class FrozenThreshold(_Frozen):
    schema_version: Literal["qd-threshold-1"]
    threshold_id: ArtifactId
    rule_version: Literal["cg-shadow-1"]
    question_set_version: Literal["jq-1"]
    model: StrictStr
    threshold: Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)]
    calibration_partition_id: ArtifactId
    calibration_partition_sha256: Sha256
    criteria: AcceptanceCriteria
    selection_note: FreeText | None = None  # the operator's own account of how t was chosen
    frozen_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.threshold_id,)


class GateDecision(_Frozen):
    """The shadow gate's category: the deterministic one, an accepted Jev one, or none."""

    category: StrictStr | None
    source: Literal["deterministic", "jev", "none"]
    reason: GateReason
    jev_probability: Decimal | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.category is None) != (self.source == "none"):
            raise ValueError("a category is set exactly when the source is not none")
        return self


def shadow_category_gate(
    deterministic: QueryUnderstanding, jev: JevOutcome | None, threshold: Decimal
) -> GateDecision:
    """Apply `cg-shadow-1` (see the module docstring). Pure: reads its inputs, changes nothing."""
    if deterministic.category is not None:
        return GateDecision(
            category=deterministic.category.value,
            source="deterministic",
            reason="deterministic_decided",
        )
    if jev is None:
        return GateDecision(category=None, source="none", reason="jev_missing")
    if jev.fallback_reason is not None or jev.category is None:
        return GateDecision(category=None, source="none", reason="jev_fallback")
    proposal = jev.category
    if len(proposal.top_labels) != 1:
        return GateDecision(category=None, source="none", reason="jev_tie")
    (top,) = proposal.top_labels
    probability = Decimal(repr(proposal.provider_probabilities[top]))
    if top == NONE_LABEL:
        return GateDecision(
            category=None, source="none", reason="jev_none", jev_probability=probability
        )
    if probability < threshold:
        return GateDecision(
            category=None, source="none", reason="below_threshold", jev_probability=probability
        )
    return GateDecision(category=top, source="jev", reason="accepted", jev_probability=probability)


def freeze_threshold(
    root: Path,
    *,
    threshold_id: str,
    calibration_partition_id: str,
    threshold: Decimal,
    model: str,
    criteria: AcceptanceCriteria,
    selection_note: str | None = None,
    now: datetime | None = None,
) -> tuple[FrozenThreshold, str]:
    """Write the threshold once, bound to the calibration partition's current file bytes.
    Returns the artifact and the SHA-256 of its written bytes."""
    partition, partition_sha256 = load_partition(root, calibration_partition_id)
    if partition.role is not PartitionRole.CALIBRATION:
        raise QueryDecisionError("a threshold is frozen from a calibration partition only")
    frozen = FrozenThreshold(
        schema_version=THRESHOLD_SCHEMA_VERSION,
        threshold_id=threshold_id,
        rule_version=GATE_RULE_VERSION,
        question_set_version=partition.question_set_version,
        model=model,
        threshold=threshold,
        calibration_partition_id=calibration_partition_id,
        calibration_partition_sha256=partition_sha256,
        criteria=criteria,
        selection_note=selection_note,
        frozen_at=now or datetime.now(UTC),
    )
    data = encode_json_object(frozen)
    write_exclusive(root, {f"{THRESHOLDS_DIR}/{threshold_id}.json": data})
    return frozen, sha256_hex(data)


def load_threshold(root: Path, threshold_id: str) -> tuple[FrozenThreshold, str]:
    """The frozen threshold and the SHA-256 of its file bytes. Refuses a threshold whose
    calibration partition file no longer has the bound hash."""
    path = safe_child(root, f"{THRESHOLDS_DIR}/{threshold_id}.json")
    if not path.is_file():
        raise QueryDecisionError(f"no frozen threshold {threshold_id!r}")
    data = path.read_bytes()
    frozen = decode_json_object(data, FrozenThreshold)
    if frozen.threshold_id != threshold_id:
        raise QueryDecisionError("the threshold file name and id disagree")
    _partition, partition_sha256 = load_partition(root, frozen.calibration_partition_id)
    if partition_sha256 != frozen.calibration_partition_sha256:
        raise QueryDecisionError("the calibration partition changed after the threshold was frozen")
    return frozen, sha256_hex(data)
