"""`propose`: generate the `weak_dev_v1` proposals twice in fresh processes and store them.

1. Verify `queries_frozen`, `pool_frozen` and the private provenance (read-only).
2. Refuse if any proposal artifact or private signal file already exists (never overwritten).
3. Run two independent child processes (`_generate`), each recomputing every cross-encoder
   logit offline with the pinned snapshot and building signals, proposals, queue and manifest.
4. The four deterministic files must be byte-identical across both runs, or nothing is written.
5. Write the private signals exclusively, then the public files with the proposal manifest
   last (all-or-nothing); a failure removes everything this run wrote.
6. Re-verify the stored generation without a model.

`generation_run.json` (wall-clock time, run facts) is written beside the deterministic files and
is never part of the comparison.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from evals.contracts.canonical import encode_json_object, sha256_hex
from evals.contracts.catalog import ROOT
from evals.runners.golden_io import refuse_stale_staging, write_exclusive
from evals.weak_labels.contracts import GenerationRun
from evals.weak_labels.generation import (
    DEFAULT_WEAK_DIR,
    MANIFEST_FILE,
    PRIVATE_ROOT,
    PROPOSALS_FILE,
    QUEUE_FILE,
    RUN_FILE,
    SIGNALS_FILE,
    WEAKSET_ROOT,
    WeakLabelError,
    build_generation,
    load_facts,
    pinned_reranker,
    safe_weak_dir,
    signals_path,
    verify_generation,
)

DETERMINISTIC_FILES = (SIGNALS_FILE, PROPOSALS_FILE, QUEUE_FILE, MANIFEST_FILE)
CHECK_FILE = "reranker_check.json"


class DeterminismError(WeakLabelError):
    """The two independent runs differ; nothing was written."""


def compare_runs(first: dict[str, bytes], second: dict[str, bytes]) -> None:
    """Byte identity of every deterministic file; a difference names the file and line only."""
    for name in DETERMINISTIC_FILES:
        a, b = first.get(name), second.get(name)
        if a is None or b is None:
            raise DeterminismError(f"{name}: missing from a run")
        if a != b:
            lines_a, lines_b = a.split(b"\n"), b.split(b"\n")
            line = next(
                (i for i, (x, y) in enumerate(zip(lines_a, lines_b, strict=False), 1) if x != y),
                min(len(lines_a), len(lines_b)) + 1,
            )
            raise DeterminismError(
                f"runs differ; nothing written. First difference: {name} line {line}"
            )


def child_main(out_dir: Path) -> int:  # pragma: no cover - loads the model
    from evals.weak_labels.generation import check_logits_against_provenance
    from evals.weak_labels.signals import score_pool

    facts = load_facts()
    logits = score_pool(facts)
    files = build_generation(facts, logits, pinned_reranker())
    assert facts.provenance is not None  # noqa: S101 - load_facts verified it
    worst, checked = check_logits_against_provenance(facts.provenance, logits)
    out_dir.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    check = {"max_abs_diff": repr(worst), "checked": checked}
    (out_dir / CHECK_FILE).write_text(json.dumps(check, sort_keys=True), encoding="utf-8")
    return 0


def run_once(out_dir: Path) -> dict[str, bytes]:  # pragma: no cover - loads the model
    from evals.runners.pool_sources import OFFLINE_ENV

    argv = [sys.executable, "-m", "evals.weak_labels", "_generate", "--out", str(out_dir)]
    try:
        subprocess.run(  # noqa: S603 - fixed argv, sys.executable
            argv, cwd=ROOT, check=True, env={**os.environ, **OFFLINE_ENV}
        )
    except subprocess.CalledProcessError as exc:
        message = f"a proposal run failed (exit {exc.returncode}); nothing written"
        raise WeakLabelError(message) from None
    return {name: (out_dir / name).read_bytes() for name in (*DETERMINISTIC_FILES, CHECK_FILE)}


def store_generation(
    files: dict[str, bytes],
    run: GenerationRun,
    *,
    weak_dir: Path,
    allowed_root: Path,
    private_path: Path,
) -> None:
    """Private signals first (exclusive), then the public files, manifest last."""
    weak_dir.mkdir(parents=True, exist_ok=True)
    directory = safe_weak_dir(weak_dir, allowed_root)
    refuse_stale_staging(directory)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(private_path, "xb") as handle:
            handle.write(files[SIGNALS_FILE])
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        message = "private weak-label signals already exist; never overwritten"
        raise WeakLabelError(message) from None
    try:
        write_exclusive(
            directory,
            {
                PROPOSALS_FILE: files[PROPOSALS_FILE],
                QUEUE_FILE: files[QUEUE_FILE],
                RUN_FILE: encode_json_object(run),
                MANIFEST_FILE: files[MANIFEST_FILE],
            },
            last=MANIFEST_FILE,
        )
    except BaseException:
        private_path.unlink(missing_ok=True)
        raise


def propose(weak_dir: Path = DEFAULT_WEAK_DIR) -> int:  # pragma: no cover - loads the model
    from evals.runners.pool_sources import install_network_guard

    install_network_guard()
    facts = load_facts()
    print(f"verified pool_frozen {facts.pool_manifest.manifest_sha256} and private provenance")
    private_path = signals_path(facts, PRIVATE_ROOT)
    existing = [weak_dir / n for n in (PROPOSALS_FILE, QUEUE_FILE, MANIFEST_FILE, RUN_FILE)]
    if private_path.exists() or any(os.path.lexists(p) for p in existing):
        raise WeakLabelError("weak-label proposals already exist; they are never regenerated")
    with tempfile.TemporaryDirectory(prefix="weak-label-runs-") as tmp:
        outputs = []
        for index in (1, 2):
            print(f"run {index}/2: fresh process, pinned local cross-encoder, offline", flush=True)
            outputs.append(run_once(Path(tmp) / f"run{index}"))
        compare_runs(outputs[0], outputs[1])
        print("determinism: both runs byte-identical for " + ", ".join(DETERMINISTIC_FILES))
        check = json.loads(outputs[0][CHECK_FILE])
        run = GenerationRun(
            proposal_manifest_sha256=sha256_hex(outputs[0][MANIFEST_FILE]),
            generated_at=datetime.now(UTC),
            max_reranker_abs_diff_vs_provenance=check["max_abs_diff"],
            reranker_scores_checked_vs_provenance=check["checked"],
        )
        store_generation(
            outputs[0],
            run,
            weak_dir=weak_dir,
            allowed_root=WEAKSET_ROOT,
            private_path=private_path,
        )
    generation = verify_generation(facts, weak_dir)
    print(f"wrote proposals; proposal_manifest sha256 {generation.manifest_sha256}")
    print(f"private signals (uncommitted): {private_path.relative_to(ROOT).as_posix()}")
    return 0
