"""Acceptance scorer for one frozen category threshold on one evaluation partition (offline).

Inputs: a frozen threshold (`gate.load_threshold`, which refuses a changed calibration partition),
an evaluation partition, and the batches and runs that hold its outcomes. Before scoring it
refuses:

* a calibration and evaluation partition that overlap (`partitions.check_disjoint`);
* a run whose model or question set differs from the threshold's;
* any evaluation member dispatched at or before the threshold was frozen (the threshold must be
  fixed before the evaluation run).

Scoring uses `category_metrics.fill_counts` at the frozen threshold and the criteria frozen with
it. Bounds are exact one-sided Clopper-Pearson bounds at `criteria.confidence_level`, rounded
outward to six places (upper bounds up, lower bounds down).

Outcome, in this order:

* `insufficient_evidence`: a run is missing, incomplete or blocked; the unknown-or-fallback rate
  or the unclear rate exceeds its maximum; there are fewer accepted fills than the minimum; or
  there is no single-category gold to measure coverage;
* `fail`: the wrong-commit upper bound exceeds its maximum, or coverage is below its minimum;
* `pass`: otherwise. A pass means only a candidate soft category gate for a later retrieval
  experiment on this sampled design. It is not a production decision, not a hard filter and
  not search held-out evidence.

`confirm_acceptance` scores and records one confirmation. Evaluation data is consumed by its
content, not by a file name or path: the identity is the SHA-256 of the evaluation partition's
canonical members (source hashes, query ids, text hashes), so a renamed or re-created partition
with the same members is the same evaluation data. A confirmation is identified by the frozen
threshold's file hash, that content identity, and each run's `run.json` and journal hashes.

* A consumption record (`consumption/<content-sha256>.json`) is written before the report
  (`acceptance/<threshold-id>.json`), each with `write_exclusive` (all or nothing).
* Refused: evaluation data, any member or any normalized text already consumed by a different
  confirmation; a threshold already used for a different confirmation; a report without its
  consumption record; a stored report that differs from the recomputed one.
* An identical repeat (same threshold, content and runs) writes nothing new and returns the
  stored report, after checking that the recomputed report has the recorded hash.
* An interruption after the record but before the report is completed by an identical repeat.
  An interruption inside a write leaves a staging directory that makes every later write in
  the root refuse until a person inspects and removes it.

Enforcement boundary: records live in the work root. They stop reuse only for confirmations
recorded in the same root; a copy of the data scored in another root, or a deleted record, is
not detected. The one-confirmation rule is therefore enforced locally and stays procedural
beyond it.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictStr

from evals.contracts.canonical import (
    canonical_json,
    decode_json_object,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.models import Sha256, UtcTimestamp
from evals.query_decisions.category_metrics import fill_counts, ratio
from evals.query_decisions.contracts import GoldKind, QueryDecisionError
from evals.query_decisions.gate import load_threshold
from evals.query_decisions.labeling import load_sealed
from evals.query_decisions.partitions import (
    ArtifactId,
    BatchRun,
    PartitionRole,
    check_disjoint,
    load_partition,
    resolve_members,
)
from evals.query_decisions.runner import JOURNAL_FILE, RUN_FILE, RUNS_DIR
from evals.runners.golden_io import safe_child, write_exclusive

REPORT_VERSION = "qd-acceptance-1"
ACCEPTANCE_DIR = "acceptance"
CONSUMPTION_DIR = "consumption"
CONSUMPTION_SCHEMA_VERSION = "qd-consumption-1"
_PLACES = Decimal("0.000001")
_BISECTION_STEPS = 200


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def _cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    return math.fsum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1))


def clopper_pearson_upper(successes: int, trials: int, confidence: Decimal) -> Decimal | None:
    """One-sided upper bound on the rate, or None when there are no trials."""
    if trials == 0:
        return None
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    if successes == trials:
        return Decimal(1)
    alpha = 1.0 - float(confidence)
    low, high = successes / trials, 1.0
    for _ in range(_BISECTION_STEPS):  # P(X <= successes) falls as p rises
        middle = (low + high) / 2
        if _cdf(successes, trials, middle) > alpha:
            low = middle
        else:
            high = middle
    return Decimal(repr(high)).quantize(_PLACES, ROUND_CEILING)


def clopper_pearson_lower(successes: int, trials: int, confidence: Decimal) -> Decimal | None:
    """One-sided lower bound on the rate, or None when there are no trials."""
    if trials == 0:
        return None
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    if successes == 0:
        return Decimal(0)
    alpha = 1.0 - float(confidence)
    low, high = 0.0, successes / trials
    for _ in range(_BISECTION_STEPS):  # P(X >= successes) rises with p
        middle = (low + high) / 2
        if 1.0 - _cdf(successes - 1, trials, middle) < alpha:
            low = middle
        else:
            high = middle
    return Decimal(repr(low)).quantize(_PLACES, ROUND_FLOOR)


def score_acceptance(
    root: Path,
    *,
    threshold_id: str,
    evaluation_partition_id: str,
    batches: Sequence[BatchRun],
) -> dict[str, Any]:
    """The acceptance report (see the module docstring). Writes nothing."""
    frozen, threshold_sha256 = load_threshold(root, threshold_id)
    calibration, _ = load_partition(root, frozen.calibration_partition_id)
    evaluation, evaluation_sha256 = load_partition(root, evaluation_partition_id)
    if evaluation.role is not PartitionRole.EVALUATION:
        raise QueryDecisionError("acceptance is scored on an evaluation partition only")
    check_disjoint(calibration, evaluation)
    records = resolve_members(evaluation, batches)
    for record in records:
        run = record.run
        if run is not None and (
            run.model != frozen.model or run.question_set_version != frozen.question_set_version
        ):
            raise QueryDecisionError("a run's model or question set differs from the threshold")
        if record.dispatched_at is not None and record.dispatched_at <= frozen.frozen_at:
            raise QueryDecisionError(
                "an evaluation member was dispatched before the threshold was frozen"
            )

    criteria = frozen.criteria
    members = len(records)
    unclear = sum(r.category_gold.kind is GoldKind.UNCLEAR for r in records)
    unknown = sum(r.attempted and r.outcome is None for r in records)
    not_attempted = sum(not r.attempted for r in records)
    fallbacks = sum(
        r.outcome is not None and r.outcome.fallback_reason is not None for r in records
    )
    missing_run = any(r.run is None for r in records)
    incomplete = any(r.run is not None and not r.run_complete for r in records)
    blocked = any(r.run_blocked_by is not None for r in records)
    run_issue_rate = Decimal(unknown + fallbacks + not_attempted) / members
    unclear_rate = Decimal(unclear) / members

    fills = fill_counts(records, frozen.threshold)
    wrong = fills[f"wrong_commits_{criteria.wrong_commit_definition}"]
    accepted = fills["accepted_fills"]
    wrong_upper = clopper_pearson_upper(wrong, accepted, criteria.confidence_level)
    coverage_lower = clopper_pearson_lower(
        fills["correct_fills"], fills["coverage_denominator"], criteria.confidence_level
    )
    coverage = (
        None
        if fills["coverage_denominator"] == 0
        else Decimal(fills["correct_fills"]) / fills["coverage_denominator"]
    )

    insufficient = []
    if missing_run:
        insufficient.append("a member has no run")
    if incomplete or blocked:
        insufficient.append("a run is incomplete or blocked")
    if run_issue_rate > criteria.max_unknown_or_fallback_rate:
        insufficient.append("unknown-or-fallback rate above its maximum")
    if unclear_rate > criteria.max_unclear_rate:
        insufficient.append("unclear rate above its maximum")
    if accepted < criteria.min_accepted_fills:
        insufficient.append("fewer accepted fills than the minimum")
    if coverage is None:
        insufficient.append("no single-category gold where the parser abstains")
    failures = []
    # Neither value is None once nothing is insufficient (at least one accepted fill, and a
    # coverage denominator); the checks are written so a None can never pass.
    if not insufficient:
        if wrong_upper is None or wrong_upper > criteria.max_wrong_commit_upper_bound:
            failures.append("wrong-commit upper bound above its maximum")
        if coverage is None or coverage < criteria.min_coverage:
            failures.append("coverage below its minimum")
    outcome = "insufficient_evidence" if insufficient else ("fail" if failures else "pass")

    synthetic = evaluation.synthetic or any(
        r.run is not None and r.run.mode.value == "dry_run" for r in records
    )
    return {
        "report_version": REPORT_VERSION,
        "outcome": outcome,
        "reasons": insufficient or failures,
        "meaning": (
            "pass = candidate soft category gate for a later retrieval experiment on this "
            "sampled design only; not a production decision, not a hard filter, not search "
            "held-out evidence"
        ),
        "synthetic": synthetic,
        "threshold_id": frozen.threshold_id,
        "threshold_sha256": threshold_sha256,
        "threshold": str(frozen.threshold),
        "rule_version": frozen.rule_version,
        "model": frozen.model,
        "calibration_partition_id": frozen.calibration_partition_id,
        "calibration_partition_sha256": frozen.calibration_partition_sha256,
        "evaluation_partition_id": evaluation.partition_id,
        "evaluation_partition_sha256": evaluation_sha256,
        "criteria": frozen.criteria.model_dump(mode="json"),
        "members": members,
        "unclear": unclear,
        "unclear_rate": ratio(unclear, members),
        "run": {
            "unknown_outcome": unknown,
            "fallbacks": fallbacks,
            "not_attempted": not_attempted,
            "unknown_or_fallback_rate": ratio(unknown + fallbacks + not_attempted, members),
            "missing_run": missing_run,
            "incomplete_or_blocked": incomplete or blocked,
        },
        "fills": fills,
        "wrong_commits_deciding": wrong,
        "wrong_commit_rate": ratio(wrong, accepted),
        "wrong_commit_upper_bound": None if wrong_upper is None else str(wrong_upper),
        "lenient_wrong_commit_upper_bound": _text(
            clopper_pearson_upper(
                fills["wrong_commits_lenient"], accepted, criteria.confidence_level
            )
        ),
        "coverage": fills["coverage"],
        "coverage_lower_bound": _text(coverage_lower),
    }


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunBinding(_Frozen):
    queries_sha256: Sha256
    labels_sha256: Sha256
    run_id: ArtifactId | None
    run_json_sha256: Sha256 | None
    journal_sha256: Sha256 | None

    def sort_key(self) -> tuple[str, ...]:
        return (self.queries_sha256,)


class _ConfirmationIdentity(_Frozen):
    threshold_sha256: Sha256
    evaluation_content_sha256: Sha256
    runs: tuple[RunBinding, ...]


class ConsumptionRecord(_Frozen):
    schema_version: Literal["qd-consumption-1"]
    evaluation_content_sha256: Sha256
    member_keys: tuple[StrictStr, ...]  # "<source queries sha256>:<query id>", sorted
    text_sha256s: tuple[Sha256, ...]  # sorted
    confirmation_sha256: Sha256
    threshold_id: ArtifactId
    threshold_sha256: Sha256
    evaluation_partition_id: ArtifactId
    evaluation_partition_sha256: Sha256
    runs: tuple[RunBinding, ...]
    report_sha256: Sha256
    recorded_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.evaluation_content_sha256,)


@dataclass(frozen=True)
class Confirmation:
    report: dict[str, Any]
    report_path: Path
    record: ConsumptionRecord
    repeated: bool  # true when an identical confirmation was already recorded


def report_bytes(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def evaluation_content_sha256(members: Sequence[Any]) -> str:
    """The content identity of evaluation data: its canonical members, not a name or path."""
    return sha256_hex(encode_jsonl(members))


def confirm_acceptance(
    root: Path,
    *,
    threshold_id: str,
    evaluation_partition_id: str,
    batches: Sequence[BatchRun],
    now: datetime | None = None,
) -> Confirmation:
    """Score, then record the consumption and write the report (see the module docstring)."""
    report = score_acceptance(
        root,
        threshold_id=threshold_id,
        evaluation_partition_id=evaluation_partition_id,
        batches=batches,
    )
    data = report_bytes(report)
    evaluation, evaluation_sha256 = load_partition(root, evaluation_partition_id)
    _frozen, threshold_sha256 = load_threshold(root, threshold_id)
    content = evaluation_content_sha256(evaluation.members)
    runs = tuple(sorted((_run_binding(batch) for batch in batches), key=RunBinding.sort_key))
    identity = sha256_hex(
        canonical_json(
            _ConfirmationIdentity(
                threshold_sha256=threshold_sha256, evaluation_content_sha256=content, runs=runs
            )
        ).encode("utf-8")
    )
    keys = tuple(sorted(f"{m.source_queries_sha256}:{m.query_id}" for m in evaluation.members))
    texts = tuple(sorted(m.text_sha256 for m in evaluation.members))
    record = ConsumptionRecord(
        schema_version=CONSUMPTION_SCHEMA_VERSION,
        evaluation_content_sha256=content,
        member_keys=keys,
        text_sha256s=texts,
        confirmation_sha256=identity,
        threshold_id=threshold_id,
        threshold_sha256=threshold_sha256,
        evaluation_partition_id=evaluation_partition_id,
        evaluation_partition_sha256=evaluation_sha256,
        runs=runs,
        report_sha256=sha256_hex(data),
        recorded_at=now or datetime.now(UTC),
    )

    existing = _consumption_records(root)
    same = [r for r in existing if r.confirmation_sha256 == identity]
    for other in existing:
        if other.confirmation_sha256 == identity:
            continue
        if (
            other.evaluation_content_sha256 == content
            or set(other.member_keys) & set(keys)
            or set(other.text_sha256s) & set(texts)
        ):
            raise QueryDecisionError(
                "this evaluation data was already consumed by a different confirmation"
            )
        if other.threshold_sha256 == threshold_sha256:
            raise QueryDecisionError("this threshold was already used for a different confirmation")

    report_relative = f"{ACCEPTANCE_DIR}/{threshold_id}.json"
    report_path = safe_child(root, report_relative)
    if same:
        (recorded,) = same
        if recorded.report_sha256 != record.report_sha256:
            raise QueryDecisionError("the recomputed report differs from the recorded confirmation")
    else:
        if report_path.exists():
            raise QueryDecisionError("an acceptance report exists without its consumption record")
        write_exclusive(root, {f"{CONSUMPTION_DIR}/{content}.json": encode_json_object(record)})
        recorded = record
    if report_path.exists():
        if report_path.read_bytes() != data:
            raise QueryDecisionError("the stored acceptance report differs from the recomputed one")
    else:
        write_exclusive(root, {report_relative: data})
    return Confirmation(
        report=report, report_path=report_path, record=recorded, repeated=bool(same)
    )


def _run_binding(batch: BatchRun) -> RunBinding:
    seal, _queries, _labels = load_sealed(batch.work_dir)
    if batch.run_id is None:
        run_json = journal = None
    else:
        run_dir = f"{RUNS_DIR}/{batch.run_id}"
        run_json = sha256_hex(safe_child(batch.work_dir, f"{run_dir}/{RUN_FILE}").read_bytes())
        journal_path = safe_child(batch.work_dir, f"{run_dir}/{JOURNAL_FILE}")
        journal = sha256_hex(journal_path.read_bytes()) if journal_path.is_file() else None
    return RunBinding(
        queries_sha256=seal.queries_sha256,
        labels_sha256=seal.labels_sha256,
        run_id=batch.run_id,
        run_json_sha256=run_json,
        journal_sha256=journal,
    )


def _consumption_records(root: Path) -> list[ConsumptionRecord]:
    directory = safe_child(root, CONSUMPTION_DIR)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.iterdir()):
        record = decode_json_object(path.read_bytes(), ConsumptionRecord)
        if path.name != f"{record.evaluation_content_sha256}.json":
            raise QueryDecisionError(f"consumption record {path.name} is misnamed")
        records.append(record)
    return records


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
