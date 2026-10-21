"""Combine the recorded outcomes of several runs over one set into one `le-outcomes-1` file.

Outcomes are read from each run's **journal**, the authoritative record, never from an export
file. `combine` refuses unless:

* every run is bound identically: set version and digest, `jl-1`, `lr-1` and M13 rules digests,
  model and token allowances;
* no run is in progress (locked);
* no item was dispatched in more than one run, whatever its outcome. A duplicate or conflicting
  outcome is refused, never chosen between;
* every item is a dev item of the set.

Each record carries its lineage (`source_run`: run id and journal SHA-256), and the header lists
every source run. All recorded results are kept, fallbacks included. `verify_lineage` re-checks a
combined file against the journals, so an edited record or relabelled `source_run` is refused.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from evals.listing_decisions.contracts import EvalSet, EvalSetError, Split
from evals.listing_decisions.evaluate import OUTCOMES_SCHEMA, OutcomeRecord, OutcomesHeader
from evals.listing_decisions.runner import (
    JOURNAL_FILE,
    LOCK_FILE,
    RUN_FILE,
    RUNS_DIR,
    JournalState,
    RunHeader,
    RunMode,
    read_journal,
)

BINDING_FIELDS = (
    "set_version",
    "set_sha256",
    "question_set_digest",
    "routing_policy_digest",
    "normalization_rules_digest",
    "model",
    "allowance",
)


def _load(work_dir: Path, run_id: str) -> tuple[RunHeader, JournalState, str]:
    run_dir = work_dir / RUNS_DIR / run_id
    if (run_dir / LOCK_FILE).exists():
        raise EvalSetError(f"run {run_id} is in progress (locked)")
    try:
        header = RunHeader.model_validate_json((run_dir / RUN_FILE).read_bytes())
        data = (run_dir / JOURNAL_FILE).read_bytes()
    except (OSError, ValueError) as exc:
        raise EvalSetError(f"cannot read run {run_id} ({type(exc).__name__})") from None
    return header, read_journal(run_dir / JOURNAL_FILE), hashlib.sha256(data).hexdigest()


def _records(
    work_dir: Path, eval_set: EvalSet, run_ids: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], RunHeader]:
    if len(run_ids) < 1 or len(set(run_ids)) != len(run_ids):
        raise EvalSetError("name each source run once")
    loaded = [(r, *_load(work_dir, r)) for r in run_ids]
    first = loaded[0][1]
    for run_id, header, _, _ in loaded:
        mismatched = [f for f in BINDING_FIELDS if getattr(header, f) != getattr(first, f)]
        if mismatched:
            raise EvalSetError(f"run {run_id} is bound differently: {', '.join(mismatched)}")
    if (first.set_version, first.set_sha256) != (eval_set.set_version, eval_set.set_sha256):
        raise EvalSetError("the runs are bound to another set")
    known = {i.item_id: i for i in eval_set.items}
    seen: dict[str, str] = {}
    records, sources = [], []
    for run_id, header, state, digest in loaded:
        for item_id in state.dispatched:
            if item_id in seen:
                raise EvalSetError(f"an item was dispatched in both {seen[item_id]} and {run_id}")
            seen[item_id] = run_id
            if item_id not in known or known[item_id].split is not Split.DEV:
                raise EvalSetError(
                    f"run {run_id} dispatched an item that is not a dev item of the set"
                )
        for item_id, outcome in state.results.items():
            records.append(
                {
                    "item_id": item_id,
                    "outcome": outcome.model_dump(mode="json"),
                    "source_run": {"run_id": run_id, "journal_sha256": digest},
                }
            )
        sources.append(
            {
                "run_id": run_id,
                "journal_sha256": digest,
                "mode": header.mode.value,
                "results": len(state.results),
            }
        )
    records.sort(key=lambda r: r["item_id"])
    return records, sources, first


def combine(work_dir: Path, eval_set: EvalSet, run_ids: tuple[str, ...], out: Path) -> dict:
    """Write the combined `le-outcomes-1` file (exclusive creation); return its counts."""
    records, sources, first = _records(work_dir, eval_set, run_ids)
    header = {
        "schema_version": OUTCOMES_SCHEMA,
        "set_sha256": first.set_sha256,
        "provider": "jev",
        "question_set_digest": first.question_set_digest,
        "synthetic": any(s["mode"] == RunMode.DRY_RUN.value for s in sources),
        "sources": sources,
    }
    lines = [json.dumps(header, sort_keys=True)]
    OutcomesHeader.model_validate_json(lines[0])  # exactly as the evaluator will read it
    for record in records:
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
        OutcomeRecord.model_validate_json(lines[-1])
    with open(out, "xb") as handle:
        handle.write(("\n".join(lines) + "\n").encode("utf-8"))
    return {"records": len(records), "sources": [s["run_id"] for s in sources]}


def verify_lineage(work_dir: Path, eval_set: EvalSet, path: Path) -> None:
    """Refuse unless every record of a combined file equals its source journal's result."""
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
        header = OutcomesHeader.model_validate_json(lines[0]) if lines else None
        recorded = [OutcomeRecord.model_validate_json(line) for line in lines[1:]]
    except (OSError, UnicodeDecodeError, ValueError) as exc:  # ValidationError is a ValueError
        raise EvalSetError(f"cannot read the outcomes file ({type(exc).__name__})") from None
    if header is None:
        raise EvalSetError("the outcomes file is empty")
    if not header.sources:
        raise EvalSetError("the outcomes file carries no lineage to verify")
    run_ids = tuple(s.run_id for s in header.sources)
    expected, sources, _ = _records(work_dir, eval_set, run_ids)
    if [s.model_dump(mode="json") for s in header.sources] != sources:
        raise EvalSetError("the source runs differ from their journals")
    if [r.model_dump(mode="json") for r in recorded] != [
        OutcomeRecord.model_validate_json(json.dumps(e)).model_dump(mode="json") for e in expected
    ]:
        raise EvalSetError("a record differs from its source journal")
