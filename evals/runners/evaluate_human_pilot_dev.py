"""Human-reviewed pilot/dev quality evaluation (M10, development-scoped): `eval-1` metrics of the
recorded V0–V3 retrieval runs against the frozen review-2 Golden labels.

These are evaluations of **previously recorded retrieval runs** with newly frozen human labels,
not fresh retrieval runs. The ranked lists are the top-20 source runs in the unsealed pool
provenance (`provenance.json`, recorded before labelling); the labels are the frozen final
judgements; constraint satisfaction uses the frozen eligibility records. No search, database,
model or network is used, and no latency is measured.

Systems: V0 `v0_lexical`, V1 `v1_hybrid`, V2 `v2_filtered`, V3 `v3_reranked_d20` (the
configured default depth) and the secondary V3 arm `v3_reranked_d10`. The pooling-only source
`dense_only` is not evaluated. The metric semantics are exactly `evals/metrics/retrieval.py`
(`eval-1`): K = 5, 10, 20 (primary 10), strict (primary) and lenient relevance, macro means with
`n` and `excluded`, unjudged@K counted separately.

Subcommands:

* `check`: read-only. It re-verifies the complete review-2 chain through `provenance_unsealed`
  (`unseal_provenance.load_labels_frozen` with the stage-6 chain, then
  `unseal_provenance.verify_unsealed`, which re-assembles the pool from the unsealed provenance
  byte for byte), checks the recorded runs and the partitions, and prints the hashes to review.
  It computes no metric and writes nothing. It refuses when the destination already exists.
* `run --labels-frozen-sha256 <hex> --provenance-unsealed-sha256 <hex>`: re-runs every check,
  refuses manifests other than the reviewed ones, builds the outputs in this process and in two
  fresh processes, refuses unless all three are byte-identical, then writes them exclusively
  (the run record last) and re-reads them.
* `verify`: re-verifies the chain, regenerates every output and compares it byte for byte with
  the written files. The run record's `evaluation_source` block (git state at run time) is
  carried over as recorded; the evaluator source must still hash to the recorded value.

Outputs (`evals/results/internal-search/v1/human-pilot-dev/` by default; never overwritten,
never mixed with the golden directory or the machine-label weak set):

* `metrics/<system>.json`: one canonical `EvaluationResult` per system;
* `summary.json`: the primary metrics per system with `n`/`excluded`, plus unjudged and returned
  totals per K;
* `run_record.json` (written last): label, manifest, provenance, evaluator-source and
  configuration hashes, the recorded run configuration, the git state of the evaluation code and
  the disclosures.

Scope: 20 `pilot_dev` queries, development / rubric-calibration data. This is not held-out test
evidence, not official M10 completion and no `rrf_k` or embedding-model re-decision.

Usage:
  uv run --offline python -m evals.runners.evaluate_human_pilot_dev check
  uv run --offline python -m evals.runners.evaluate_human_pilot_dev run \\
      --labels-frozen-sha256 <hex> --provenance-unsealed-sha256 <hex>
  uv run --offline python -m evals.runners.evaluate_human_pilot_dev verify
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from evals.contracts.canonical import (
    ContractError,
    canonical_json,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    sha256_hex,
)
from evals.contracts.catalog import ROOT, CatalogIndex, load_catalog_index
from evals.contracts.constants import REVIEW_PROTOCOL_2, Label
from evals.contracts.manifest import Manifest, Role, Stage, verify_entry
from evals.contracts.models import (
    EligibilityRecord,
    FinalJudgement,
    Partition,
    PartitionAssignment,
    PoolProvenance,
    SourceSystem,
)
from evals.metrics.retrieval import (
    EVALUATOR_VERSION,
    K_VALUES,
    PRIMARY_K,
    EvaluationConfig,
    EvaluationResult,
    evaluate_run,
    macro_value,
)
from evals.runners import build_pool
from evals.runners import unseal_provenance as up
from evals.runners.golden_io import FreezeError, is_link, read_frozen, write_exclusive

EVALUATION_VERSION = "hpd-1"
RESULTS_ROOT = ROOT / "evals" / "results"
DEFAULT_OUT_DIR = RESULTS_ROOT / "internal-search" / "v1" / "human-pilot-dev"
UNSEALED_CHAIN: tuple[str, ...] = (*up.FROZEN_CHAIN, up.UNSEALED_MANIFEST_PATH)

# (system, report label). V3-d20 is the primary V3 (the configured default depth).
SYSTEMS: tuple[tuple[SourceSystem, str], ...] = (
    (SourceSystem.V0_LEXICAL, "V0"),
    (SourceSystem.V1_HYBRID, "V1"),
    (SourceSystem.V2_FILTERED, "V2"),
    (SourceSystem.V3_RERANKED_D20, "V3"),
    (SourceSystem.V3_RERANKED_D10, "V3-d10 (secondary)"),
)
NOT_EVALUATED: tuple[SourceSystem, ...] = (SourceSystem.DENSE_ONLY,)

SUMMARY_PATH = "summary.json"
RECORD_PATH = "run_record.json"
EVALUATOR_SOURCE = "evals/metrics/retrieval.py"
SOURCE_PATHS: tuple[str, ...] = (
    EVALUATOR_SOURCE,
    "evals/runners/evaluate_human_pilot_dev.py",
    "evals/runners/unseal_provenance.py",
    "evals/contracts/canonical.py",
    "evals/contracts/constants.py",
    "evals/contracts/manifest.py",
    "evals/contracts/models.py",
)
GOLDEN_ROLES: tuple[Role, ...] = (Role.FINAL_JUDGEMENTS, Role.ELIGIBILITY, Role.PROVENANCE)

# The metrics `summary.json` lists per system: (metric, k, definition).
PRIMARY_METRICS: tuple[tuple[str, int | None, str], ...] = (
    ("precision", PRIMARY_K, "strict"),
    ("precision", PRIMARY_K, "lenient"),
    ("recall", PRIMARY_K, "strict"),
    ("recall", PRIMARY_K, "lenient"),
    ("reciprocal_rank", PRIMARY_K, "strict"),
    ("reciprocal_rank", PRIMARY_K, "lenient"),
    ("ndcg", PRIMARY_K, "none"),
    ("csr", PRIMARY_K, "hard"),
    ("csr", PRIMARY_K, "fp1"),
    ("irrelevant_result_rate", PRIMARY_K, "none"),
    ("unjudged", PRIMARY_K, "none"),
    ("zero_result", None, "none"),
    ("correct_empty", None, "strict"),
)

DISCLOSURES: tuple[str, ...] = (
    "Development-scoped evaluation on the human-reviewed pilot/dev Golden Dataset (review-2). "
    "It is development / rubric-calibration data, not held-out test evidence; no held-out "
    "performance claim is permitted.",
    "Recorded retrieval runs: the ranked lists are the top-20 source runs in the unsealed pool "
    "provenance, recorded before labelling and scored here with the newly frozen human labels. "
    "No retrieval was re-run and no latency was measured.",
    "Not official M10 completion. rrf_k and the embedding model are not re-decided here; any "
    "recommendation drawn from these results is development-scoped.",
    "Pooled recall: the denominator counts only the relevant pairs in the judged pool (pool-1), "
    "so recall is biased upward where more products are relevant than were pooled.",
    "V3-d10 at K=20: ranks 11-20 of the depth-10 lists were not reranked and keep the fused V2 "
    "order, so V3-d10@20 mixes reranked and unreranked results.",
    "Unjudged results count as not relevant and are counted separately (unjudged@K). Every "
    "evaluated list was pooled, so unjudged@K is expected to be 0; it is computed, not assumed.",
    "Label limitations (see docs/golden-dataset-m9.md): the primary annotator (ann-1) saw "
    "machine label proposals and was AI-assisted on the rubric; blindness was procedural; "
    "inter-annotator agreement describes the cs-1 sample only; optional adjudication candidates "
    "were not re-adjudicated.",
    "Small slices: the pilot has few Hinglish and noisy queries; per-query and per-slice values "
    "are anecdotes, not estimates. No significance test is run.",
    "The machine-label provisional M10 analysis (docs/m10-provisional-evaluation.md) is separate "
    "historical evidence and is not combined with these results.",
)


class EvaluationError(FreezeError):
    """The evaluation cannot be checked, run or verified; nothing was written."""


@dataclass(frozen=True)
class Inputs:
    golden_dir: Path
    labels_frozen: Manifest
    unsealed: Manifest
    files: dict[Role, bytes]
    finals: tuple[FinalJudgement, ...]
    eligibility: dict[tuple[str, str], EligibilityRecord]
    provenance: PoolProvenance
    partitions: tuple[PartitionAssignment, ...]
    runs: dict[SourceSystem, dict[str, tuple[str, ...]]]


# ---- inputs ---------------------------------------------------------------------------------


def recorded_runs(provenance: PoolProvenance) -> dict[SourceSystem, dict[str, tuple[str, ...]]]:
    runs: dict[SourceSystem, dict[str, tuple[str, ...]]] = {}
    for run in provenance.source_runs:
        runs.setdefault(run.system, {})[run.query_id] = run.product_ids
    return runs


def load_inputs(
    golden_dir: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> Inputs:
    """Re-verify the review-2 chain through `provenance_unsealed` and decode the inputs."""
    catalog = catalog or load_catalog_index()
    golden, labels = up.load_labels_frozen(
        golden_dir, allowed_root=allowed_root, catalog=catalog, chain=UNSEALED_CHAIN
    )
    unsealed = up.verify_unsealed(golden, labels, allowed_root=allowed_root, catalog=catalog)
    if unsealed.stage is not Stage.PROVENANCE_UNSEALED or (
        unsealed.review_protocol != REVIEW_PROTOCOL_2
    ):
        raise EvaluationError("expected the review-2 provenance_unsealed manifest")
    files = {e.role: read_frozen(golden, e.path) for e in unsealed.entries}
    for entry in unsealed.entries:
        verify_entry(entry, files[entry.role])
    if unsealed.entry(Role.PROVENANCE).file_sha256 != unsealed.provenance_binding:
        raise EvaluationError("the unsealed provenance does not match its binding")
    for role in (Role.FINAL_JUDGEMENTS, Role.ELIGIBILITY, Role.PARTITIONS):
        if labels.entry(role) != unsealed.entry(role):
            raise EvaluationError(f"the {role.value} entry differs from labels_frozen")
    finals = tuple(decode_jsonl(files[Role.FINAL_JUDGEMENTS], FinalJudgement))
    eligibility = {
        (e.query_id, e.product_id): e
        for e in decode_jsonl(files[Role.ELIGIBILITY], EligibilityRecord)
    }
    provenance = decode_json_object(files[Role.PROVENANCE], PoolProvenance)
    partitions = tuple(decode_jsonl(files[Role.PARTITIONS], PartitionAssignment))
    if not partitions or any(p.partition is not Partition.PILOT_DEV for p in partitions):
        raise EvaluationError("only pilot_dev queries may be evaluated by this runner")
    label_queries = {f.query_id for f in finals}
    if label_queries != {p.query_id for p in partitions}:
        raise EvaluationError("the final judgements and the partitions cover different queries")
    if set(eligibility) != {(f.query_id, f.product_id) for f in finals}:
        raise EvaluationError("the eligibility records and the final judgements differ in pairs")
    runs = recorded_runs(provenance)
    for system, _ in SYSTEMS:
        if set(runs.get(system, {})) != label_queries:
            raise EvaluationError(f"{system.value}: the recorded runs do not cover every query")
    return Inputs(
        golden_dir=golden,
        labels_frozen=labels,
        unsealed=unsealed,
        files=files,
        finals=finals,
        eligibility=eligibility,
        provenance=provenance,
        partitions=partitions,
        runs=runs,
    )


# ---- evaluation source (git state; recorded, never used to compute a metric) -----------------


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["git", *args],  # noqa: S607 - git from PATH, as every harness here
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def _git_state(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "outside_repository"
    status = _git("status", "--porcelain", "--untracked-files=all", "--", relative)
    if status.startswith("??"):
        return "untracked"
    if status.strip():
        return "modified"
    tracked = _git("ls-files", "--", relative).strip()
    return "committed_unchanged" if tracked else "not_tracked"


def _head_tree(name: str) -> str | None:
    try:
        return _git("rev-parse", f"HEAD:{name}").strip()
    except subprocess.CalledProcessError:
        return None


def current_source_state(inputs: Inputs) -> dict:
    """The git state of the evaluation code and the golden files at run time. It identifies
    uncommitted code by per-file SHA-256; it never claims a commit the code is not in."""
    status = [line for line in _git("status", "--porcelain", "--untracked-files=all").splitlines()]
    code = [
        {
            "path": relative,
            "sha256": sha256_hex((ROOT / relative).read_bytes()),
            "git_state": _git_state(ROOT / relative),
        }
        for relative in SOURCE_PATHS
    ]
    golden = [
        {
            "path": inputs.unsealed.entry(role).path,
            "sha256": inputs.unsealed.entry(role).file_sha256,
            "git_state": _git_state(inputs.golden_dir / inputs.unsealed.entry(role).path),
        }
        for role in GOLDEN_ROLES
    ] + [
        {
            "path": manifest,
            "sha256": sha256_hex(read_frozen(inputs.golden_dir, manifest)),
            "git_state": _git_state(inputs.golden_dir / manifest),
        }
        for manifest in (UNSEALED_CHAIN[-2], UNSEALED_CHAIN[-1])
    ]
    trees = {v.name: v.value for v in inputs.provenance.run_context.runtime_trees}
    head_trees = {name: _head_tree(name) for name in trees}
    statements = []
    if any(c["git_state"] != "committed_unchanged" for c in code):
        statements.append(
            "The evaluation code was not fully committed when this ran: each source file is "
            "identified by its SHA-256 and git state, not by a commit."
        )
    if any(g["git_state"] != "committed_unchanged" for g in golden):
        statements.append(
            "The golden chain files were not committed when this ran: they are identified by "
            "their manifest and file SHA-256 values."
        )
    if status:
        statements.append(
            f"The working tree was not clean ({len(status)} uncommitted paths); the git HEAD "
            "alone does not reproduce this evaluation."
        )
    if head_trees != trees:
        statements.append(
            "The runtime trees recorded with the retrieval runs differ from HEAD; the recorded "
            "runs, not the current code, are what is evaluated."
        )
    return {
        "git_head": _git("rev-parse", "HEAD").strip(),
        "working_tree_clean": not status,
        "uncommitted_path_count": len(status),
        "code_files": code,
        "golden_files": golden,
        "retrieval_runtime_trees_equal_head": head_trees == trees,
        "statements": statements,
    }


# ---- outputs --------------------------------------------------------------------------------


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def metrics_path(system: SourceSystem) -> str:
    return f"metrics/{system.value}.json"


def evaluate(inputs: Inputs) -> dict[SourceSystem, EvaluationResult]:
    def lookup(query_id: str, product_id: str) -> EligibilityRecord:
        return inputs.eligibility[(query_id, product_id)]

    return {
        system: evaluate_run(
            system.value,
            inputs.runs[system],
            inputs.finals,
            eligibility=lookup,
            golden_manifest_sha256=inputs.unsealed.manifest_sha256,
        )
        for system, _ in SYSTEMS
    }


def _totals(result: EvaluationResult, metric: str) -> dict[str, int]:
    """Per K, the sum over queries of a count metric (`unjudged`) or of returned results."""
    out = {}
    for k in K_VALUES:
        if metric == "returned":
            out[str(k)] = sum(min(q.result_count, k) for q in result.per_query)
        else:
            out[str(k)] = sum(
                int(Decimal(v.value or "0"))
                for q in result.per_query
                for v in q.values
                if (v.metric, v.k, v.definition) == (metric, k, "none")
            )
    return out


def summary(results: Mapping[SourceSystem, EvaluationResult]) -> dict:
    systems = []
    for system, label in SYSTEMS:
        result = results[system]
        systems.append(
            {
                "system": system.value,
                "label": label,
                "query_count": result.query_count,
                "zero_result_queries": sum(1 for q in result.per_query if not q.result_count),
                "returned_total": _totals(result, "returned"),
                "unjudged_total": _totals(result, "unjudged"),
                "primary": [
                    macro_value(result, *key).model_dump(mode="json") for key in PRIMARY_METRICS
                ],
            }
        )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "k_values": list(K_VALUES),
        "primary_k": PRIMARY_K,
        "primary_definition": "strict",
        "note": "Macro means over the queries where defined (n defined, excluded undefined). "
        "Every metric at every K is in metrics/<system>.json.",
        "systems": systems,
    }


def _named(values) -> dict[str, str]:
    return {v.name: v.value for v in values}


def run_record(inputs: Inputs, outputs: Mapping[str, bytes], source_state: dict) -> dict:
    context = inputs.provenance.run_context
    labels: dict[str, int] = {label.value: 0 for label in Label}
    decided: dict[str, int] = {}
    for f in inputs.finals:
        labels[f.label.value] += 1
        decided[f.decided_in] = decided.get(f.decided_in, 0) + 1
    partitions: dict[str, int] = {p.value: 0 for p in Partition}
    for p in inputs.partitions:
        partitions[p.partition.value] += 1
    systems = []
    for system, label in SYSTEMS:
        source = [r for r in inputs.provenance.source_runs if r.system is system]
        systems.append(
            {
                "system": system.value,
                "label": label,
                "endpoints": sorted({r.endpoint for r in source}),
                "search_versions": sorted({r.search_version for r in source}),
                "reranker_depths": sorted({r.reranker.depth for r in source if r.reranker}),
                "queries": len(source),
                "empty_queries": sum(1 for r in source if not r.product_ids),
            }
        )
    evaluator = (ROOT / EVALUATOR_SOURCE).read_bytes()
    return {
        "record_version": 1,
        "evaluation_version": EVALUATION_VERSION,
        "golden": {
            "golden_id": inputs.unsealed.golden_id,
            "golden_version": inputs.unsealed.golden_version,
            "review_protocol": inputs.unsealed.review_protocol,
            "dataset_id": inputs.unsealed.dataset_id,
            "dataset_version": inputs.unsealed.dataset_version,
            "dataset_checksum_sha256": inputs.unsealed.dataset_checksum_sha256,
            "labels_frozen_manifest_sha256": inputs.labels_frozen.manifest_sha256,
            "labels_frozen_at": inputs.unsealed.labels_frozen_at.isoformat(),  # type: ignore[union-attr]
            "provenance_unsealed_manifest_sha256": inputs.unsealed.manifest_sha256,
            "provenance_binding": inputs.unsealed.provenance_binding,
            "final_judgements_sha256": inputs.unsealed.entry(Role.FINAL_JUDGEMENTS).file_sha256,
            "final_judgement_count": len(inputs.finals),
            "final_labels": labels,
            "final_decided_in": dict(sorted(decided.items())),
            "eligibility_sha256": inputs.unsealed.entry(Role.ELIGIBILITY).file_sha256,
            "eligibility_count": len(inputs.eligibility),
            "provenance_sha256": inputs.unsealed.entry(Role.PROVENANCE).file_sha256,
            "partitions_sha256": inputs.unsealed.entry(Role.PARTITIONS).file_sha256,
            "partitions": partitions,
            "versions": dict(inputs.unsealed.versions),
        },
        "retrieval_runs": {
            "kind": "recorded pool-provenance source runs (not fresh retrieval runs)",
            "provenance_run_id": inputs.provenance.run_id,
            "configuration_sha256": sha256_hex(canonical_json(context).encode("utf-8")),
            "source_top_k": context.source_top_k,
            "settings": _named(context.settings),
            "model_pins": _named(context.model_pins),
            "component_versions": _named(context.component_versions),
            "library_versions": _named(context.library_versions),
            "runtime_trees": _named(context.runtime_trees),
            "systems": systems,
            "not_evaluated": [s.value for s in NOT_EVALUATED],
        },
        "evaluator": {
            "version": EVALUATOR_VERSION,
            "config": EvaluationConfig().model_dump(mode="json"),
            "source_path": EVALUATOR_SOURCE,
            "source_sha256": sha256_hex(evaluator),
        },
        "outputs": {path: sha256_hex(data) for path, data in sorted(outputs.items())},
        "evaluation_source": source_state,
        "disclosures": list(DISCLOSURES),
    }


def build_outputs(inputs: Inputs, source_state: dict) -> dict[str, bytes]:
    """Every output file (pure apart from reading the evaluator source for its hash)."""
    results = evaluate(inputs)
    files = {metrics_path(system): encode_json_object(results[system]) for system, _ in SYSTEMS}
    files[SUMMARY_PATH] = _dump(summary(results))
    files[RECORD_PATH] = _dump(run_record(inputs, files, source_state))
    return files


def compare_builds(first: Mapping[str, bytes], second: Mapping[str, bytes]) -> None:
    if set(first) != set(second):
        raise EvaluationError("two builds produced different files")
    differing = sorted(p for p in first if first[p] != second[p])
    if differing:
        raise EvaluationError(f"two builds are not byte-identical: {differing}")


# ---- destination ----------------------------------------------------------------------------


def safe_out_dir(out_dir: Path, results_root: Path = RESULTS_ROOT) -> Path:
    """A not-yet-existing directory strictly inside `results_root`, reached without `..` and
    without any symlink or junction component."""
    if ".." in out_dir.parts:
        raise EvaluationError("the output directory must not contain '..'")
    absolute = out_dir if out_dir.is_absolute() else Path.cwd() / out_dir
    root = results_root if results_root.is_absolute() else Path.cwd() / results_root
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        raise EvaluationError(f"the output directory must be inside {results_root}") from None
    if not relative.parts:
        raise EvaluationError("the output directory must be below the results root")
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        if is_link(current):
            raise EvaluationError(f"symlinks and junctions are refused: {current}")
    if os.path.lexists(absolute):
        raise EvaluationError(
            f"the output directory already exists: {absolute}; results are never overwritten "
            "or mixed"
        )
    return absolute


def _written(out_dir: Path) -> dict[str, bytes]:
    return {
        p.relative_to(out_dir).as_posix(): p.read_bytes()
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() or is_link(p)
    }


# ---- fresh processes ------------------------------------------------------------------------


def child_main(golden_dir: Path, allowed_root: Path, out: Path) -> int:  # pragma: no cover
    """One independent build in a fresh process, written to a scratch directory."""
    inputs = load_inputs(golden_dir, allowed_root=allowed_root)
    for relative, data in build_outputs(inputs, current_source_state(inputs)).items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return 0


def fresh_process_builds(golden_dir: Path, allowed_root: Path) -> list[dict[str, bytes]]:
    """Two builds, each in a fresh interpreter (no state shared with this process)."""
    builds = []
    with tempfile.TemporaryDirectory(prefix="hpd-build-") as tmp:
        for index in (1, 2):
            out = Path(tmp) / f"build{index}"
            argv = [
                sys.executable,
                "-m",
                "evals.runners.evaluate_human_pilot_dev",
                "--golden-dir",
                str(golden_dir),
                "--allowed-root",
                str(allowed_root),
                "_child",
                "--out",
                str(out),
            ]
            subprocess.run(argv, cwd=ROOT, check=True)  # noqa: S603 - fixed argv, sys.executable
            builds.append(_written(out))
    return builds


# ---- commands -------------------------------------------------------------------------------


def check(
    golden_dir: Path,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    results_root: Path = RESULTS_ROOT,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> Inputs:
    """Read-only: verify the chain and the inputs, and that the destination is free."""
    safe_out_dir(out_dir, results_root)
    return load_inputs(golden_dir, allowed_root=allowed_root, catalog=catalog)


def run(
    golden_dir: Path,
    *,
    labels_frozen_sha256: str,
    provenance_unsealed_sha256: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    results_root: Path = RESULTS_ROOT,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    fresh_builds: Callable[[Path, Path], list[dict[str, bytes]]] = fresh_process_builds,
    link: Callable[[Path, Path], None] = os.link,
) -> dict[str, bytes]:
    target = safe_out_dir(out_dir, results_root)
    inputs = load_inputs(golden_dir, allowed_root=allowed_root, catalog=catalog)
    if inputs.labels_frozen.manifest_sha256 != labels_frozen_sha256:
        raise EvaluationError(
            "refused: the labels_frozen manifest differs from the reviewed SHA-256"
        )
    if inputs.unsealed.manifest_sha256 != provenance_unsealed_sha256:
        raise EvaluationError(
            "refused: the provenance_unsealed manifest differs from the reviewed SHA-256"
        )
    files = build_outputs(inputs, current_source_state(inputs))
    builds = fresh_builds(inputs.golden_dir, allowed_root)
    if len(builds) != 2:
        raise EvaluationError("two fresh-process builds are required")
    for other in builds:
        compare_builds(files, other)
    target = safe_out_dir(out_dir, results_root)  # still free after the builds
    target.parent.mkdir(parents=True, exist_ok=True)
    write_exclusive(
        target.parent,
        {f"{target.name}/{relative}": data for relative, data in files.items()},
        last=f"{target.name}/{RECORD_PATH}",
        link=link,
    )
    if _written(target) != files:  # pragma: no cover - the bytes were just written
        raise EvaluationError("the written outputs differ from the build")
    return files


def verify(
    golden_dir: Path,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
) -> dict:
    """Regenerate every output and compare it byte for byte with the written files."""
    if is_link(out_dir) or not out_dir.is_dir():
        raise EvaluationError(f"no written evaluation at {out_dir}")
    written = _written(out_dir)
    if RECORD_PATH not in written:
        raise EvaluationError("the run record is missing")
    record = json.loads(written[RECORD_PATH])
    current = sha256_hex((ROOT / EVALUATOR_SOURCE).read_bytes())
    if record.get("evaluator", {}).get("source_sha256") != current:
        raise EvaluationError(
            "the evaluator source changed since the run; the written metrics are not "
            "reproducible with the current evaluator"
        )
    inputs = load_inputs(golden_dir, allowed_root=allowed_root, catalog=catalog)
    expected = build_outputs(inputs, record["evaluation_source"])
    if set(written) != set(expected):
        raise EvaluationError(
            f"unexpected or missing output files: {sorted(set(written) ^ set(expected))}"
        )
    differing = sorted(p for p in expected if expected[p] != written[p])
    if differing:
        raise EvaluationError(f"the written outputs do not regenerate: {differing}")
    return record


# ---- CLI ------------------------------------------------------------------------------------


def check_lines(inputs: Inputs) -> list[str]:
    """Hashes and aggregate counts only; computes no metric."""
    lines = [
        f"labels_frozen_sha256 {inputs.labels_frozen.manifest_sha256}",
        f"provenance_unsealed_sha256 {inputs.unsealed.manifest_sha256}",
    ]
    for role in (*GOLDEN_ROLES, Role.PARTITIONS):
        entry = inputs.unsealed.entry(role)
        lines.append(f"{entry.path} {entry.file_sha256}")
    pilot = sum(1 for p in inputs.partitions if p.partition is Partition.PILOT_DEV)
    lines += [
        f"queries {len(inputs.partitions)} (pilot_dev {pilot}, test 0); "
        f"final judgements {len(inputs.finals)}; eligibility records {len(inputs.eligibility)}",
        f"provenance run_id {inputs.provenance.run_id}, source_top_k "
        f"{inputs.provenance.run_context.source_top_k}",
        "systems " + ", ".join(f"{label}={system.value}" for system, label in SYSTEMS),
        f"evaluator {EVALUATOR_VERSION} source sha256 "
        f"{sha256_hex((ROOT / EVALUATOR_SOURCE).read_bytes())}",
        "chain verified through provenance_unsealed; destination free; no metric computed, "
        "nothing written",
    ]
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.evaluate_human_pilot_dev")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--allowed-root", type=Path, default=build_pool.ALLOWED_ROOT,
                        help=argparse.SUPPRESS)  # fmt: skip
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="verify the chain and inputs; computes and writes nothing")
    run_parser = commands.add_parser("run", help="evaluate and write the outputs exclusively")
    run_parser.add_argument("--labels-frozen-sha256", required=True)
    run_parser.add_argument("--provenance-unsealed-sha256", required=True)
    commands.add_parser("verify", help="regenerate the written outputs and compare them")
    child = commands.add_parser("_child", help=argparse.SUPPRESS)
    child.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "_child":  # pragma: no cover - runs in a fresh process
            return child_main(args.golden_dir, args.allowed_root, args.out)
        if args.command == "check":
            inputs = check(args.golden_dir, out_dir=args.out_dir, allowed_root=args.allowed_root)
            for line in check_lines(inputs):
                print(line)
        elif args.command == "run":
            files = run(
                args.golden_dir,
                labels_frozen_sha256=args.labels_frozen_sha256,
                provenance_unsealed_sha256=args.provenance_unsealed_sha256,
                out_dir=args.out_dir,
                allowed_root=args.allowed_root,
            )
            print(f"wrote {len(files)} files to {args.out_dir} (three byte-identical builds)")
            print(f"run_record sha256 {sha256_hex(files[RECORD_PATH])}")
        else:
            record = verify(args.golden_dir, out_dir=args.out_dir, allowed_root=args.allowed_root)
            print("OK: every output regenerates byte for byte from the verified chain")
            unsealed_sha = record["golden"]["provenance_unsealed_manifest_sha256"]
            print(f"provenance_unsealed_sha256 {unsealed_sha}")
            for statement in record["evaluation_source"]["statements"]:
                print(f"note: {statement}")
    except (ContractError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
