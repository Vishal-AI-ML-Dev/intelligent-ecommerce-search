"""Milestone 8 Session G: latency-only benchmark of V3 `/search/reranked` (disabled, depth 10, 20).

Scope: request and stage latency, cold model-load latency, deterministic output, descriptive
rank movement and overlap, process memory facts, and API/configuration contract facts. It does
NOT compute or report any relevance or quality measure, does not choose a depth and does not
compare configurations as better or worse. Rank movement is a neutral description of position
changes. Quality comparison of the same three configurations is Milestone 10 (Golden Dataset).

Scratch databases only (`ecommerce_search_bench_<12 hex>`); the development database is refused
by name and never connected to. Offline: both pinned snapshots (embedding, reranker) must verify
against their manifests, hub offline mode is forced and outbound non-loopback sockets are
refused in the parent and every run process. Every run process repeats the parent's preflight
(clean tree, production commit, query checksum, approved settings, both model pins) before any
setup, so invoking it directly skips nothing.

Predeclared protocol (`PREDECLARED`): the 17 committed M5 hybrid benchmark queries in their
committed order (checksum `QUERY_SET_SHA256`); three arms of in-process `GET /search/reranked` at
top_k=10: reranker disabled, enabled at depth 10, enabled at depth 20; 3 runs, each a fresh
subprocess on a fresh scratch database (migrated to head, the frozen 240-product seed, current
embeddings from the pinned embedding model); arm order rotated per run. Per arm: a fresh app
(fresh embedding provider and fresh, unloaded reranker), 1 cold request (the first query), then
per query 20 untimed warm-ups and 200 timed requests. Every request is recorded; no sample is
excluded or retried, and any contract violation aborts the run. Percentiles are nearest-rank.

Shared within a run (documented, not hidden): the torch/sentence-transformers import and the OS
file cache, both warmed by the setup step that generates the embeddings. Cold `model_load_ms`
and `reranker_load_ms` therefore measure constructing each model from the local snapshot into a
fresh provider (the reranker load includes its offline manifest re-verification), not a cold
interpreter. Arm rotation controls order effects.

Everything in a summary is derived from the raw JSONL records; `--verify` recomputes it and
refuses missing, duplicated, reordered, incomplete or altered records, other settings, other
model pins or another query checksum.
Artifacts (git-ignored): data/processed/rerank_benchmark/<experiment_id>.{samples.jsonl,json,md}.

Local-snapshot mode (M10, `--local-snapshot`): the same predeclared protocol on the current
working tree, for work that is not committed yet. It does not relax the M8 mode, which stays the
default and unchanged. Instead of a clean tree at a reviewed commit it requires:
- the production commit is an ancestor of HEAD;
- every guarded path that differs from the production commit (committed since, or uncommitted)
  is declared with `--accept-guarded-change PATH=BLOB`, BLOB being the working-tree file's git
  blob hash (or `deleted`); an undeclared, unknown or mismatched declaration is refused;
- a binding SHA-256 over the source fingerprint (every tracked and untracked file under src/,
  migrations/, scripts/, data/seed/ and the runtime config files), the declared guarded
  differences, the Python and library versions, the model pins, the settings and the protocol
  equals the reviewed `--binding-sha256` (printed by `--preflight`), in the parent and in every
  run process;
- a new `--output-dir` inside data/processed/ that is git-ignored, empty or absent and not the M8
  result directory (nothing is overwritten or mixed).
The experiment id is `rerank-m10-local-<binding sha256 prefix>`. HEAD alone does not reproduce
such a run; the header records the binding, the source fingerprint and how many uncommitted
paths there were. The PostgreSQL server facts are recorded at run time but are not part of the
binding.

Usage (execution requires a separate approval):
  HF_HUB_OFFLINE=1 uv run --offline python scripts/benchmark_reranked.py --execute
  uv run --offline python scripts/benchmark_reranked.py --verify <artifact .json>
  uv run --offline python scripts/benchmark_reranked.py --print-protocol
  HF_HUB_OFFLINE=1 uv run --offline python scripts/benchmark_reranked.py --preflight \\
      --local-snapshot [--accept-guarded-change PATH=BLOB ...] --output-dir <data/processed/...>
  HF_HUB_OFFLINE=1 uv run --offline python scripts/benchmark_reranked.py --execute \\
      --local-snapshot --accept-guarded-change PATH=BLOB ... --binding-sha256 <hex> \\
      --output-dir <data/processed/...>
"""

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchmark_hybrid as hybrid  # noqa: E402 - scripts/ is not a package
from benchmark_dense import (  # noqa: E402
    BenchmarkError,
    GitError,
    assert_scratch_name,
    check_model,
    committed_catalog_identity,
    embedding_corpus_digest,
    install_network_guard,
    new_scratch_name,
    power_events,
    scratch_database,
)
from benchmark_dense import environment as server_environment  # noqa: E402
from benchmark_hybrid import check_output_dir, dropped_after  # noqa: E402
from benchmark_lexical import (  # noqa: E402
    _git,
    dumps,
    is_relevant,
    read_samples,
    source_fingerprint,
    summarize,
)

# ---- pinned provenance ---------------------------------------------------------------------------

# The reviewed V3 production commit. Later harness/test-only commits are allowed; every
# runtime-affecting path must be identical to it.
PRODUCTION_COMMIT = "558bd39e69cc503410beb7efcb648fac169708b2"
GUARDED_PATHS: tuple[str, ...] = hybrid.GUARDED_PATHS
OUTPUT_DIR = ROOT / "data" / "processed" / "rerank_benchmark"
EXPERIMENT_PREFIX = "rerank-m8-latency-"
EXPERIMENT_ID_RE = re.compile(r"rerank-m8-latency-[0-9a-f]{12}")
SEED_PRODUCTS = 240
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
# Local-snapshot mode (M10): bound to a reviewed binding hash instead of a clean reviewed commit.
LOCAL_MODE = "local_snapshot"
LOCAL_EXPERIMENT_PREFIX = "rerank-m10-local-"
LOCAL_EXPERIMENT_ID_RE = re.compile(r"rerank-m10-local-[0-9a-f]{12}")
BLOB_RE = re.compile(r"[0-9a-f]{40}|deleted")

# ---- predeclared query set: the committed M5 hybrid queries, unchanged --------------------------

QUERIES: tuple[str, ...] = hybrid.QUERIES
QUERY_SET_SHA256 = "ea1df08db89a8bceb0e5b6437f18e75a30aed997fb70d727d080b441ebbd78d3"
EXPECTED_QUERY_COUNT = 17

# ---- predeclared arms, runs and sampling --------------------------------------------------------

ENDPOINT = "/search/reranked"
METHOD = "GET"
TOP_K = 10
ARMS: dict[str, dict] = {
    "disabled": {"reranker_enabled": False, "search_rerank_depth": 20},
    "depth10": {"reranker_enabled": True, "search_rerank_depth": 10},
    "depth20": {"reranker_enabled": True, "search_rerank_depth": 20},
}
ARM_SCHEDULE: dict[int, tuple[str, str, str]] = {
    1: ("disabled", "depth10", "depth20"),
    2: ("depth10", "depth20", "disabled"),
    3: ("depth20", "disabled", "depth10"),
}
RUNS = 3
COLD_PER_ARM = 1
WARMUP_PER_QUERY = 20
TIMED_PER_QUERY = 200
# The effective configuration every arm starts from (environment or .env must not change it).
# Arms override only `reranker_enabled` and `search_rerank_depth`.
BASE_SETTINGS: dict[str, object] = {
    "search_lexical_k": 50,
    "search_dense_k": 50,
    "search_candidate_k": 50,
    "search_rrf_k": 100,
    "search_default_top_k": 10,
    "search_max_query_length": 200,
    "embedding_batch_size": 32,
    "reranker_batch_size": 16,
    "reranker_enabled": True,
    "search_rerank_depth": 20,
    "embedding_models_dir": None,
}
RRF_K_STATUS = "provisional"
EXPECTED_FUSION = {
    "method": "rrf",
    "rrf_k": 100,
    "rrf_k_status": RRF_K_STATUS,
    "lexical_k": 50,
    "dense_k": 50,
    "candidate_k": 50,
}
SEARCH_VERSION = "v3_reranked"
FILTER_POLICY_VERSION = "fp-1"
UNDERSTANDING = {"usage": "filter_source", "provider": "deterministic", "provider_version": "qu-1"}
PREDECLARED: dict = {
    "experiment": "M8 Session G latency-only V3 reranker benchmark",
    "endpoint": ENDPOINT,
    "method": METHOD,
    "top_k": TOP_K,
    "runs": RUNS,
    "arms": {name: dict(cfg) for name, cfg in ARMS.items()},
    "arm_schedule": {str(run): list(order) for run, order in ARM_SCHEDULE.items()},
    "cold_per_arm": COLD_PER_ARM,
    "cold_query_index": 0,
    "warmup_per_query": WARMUP_PER_QUERY,
    "timed_per_query": TIMED_PER_QUERY,
    "fresh_app_per_arm": True,
    "fresh_process_and_scratch_db_per_run": True,
    "excluded_samples": "none",
    "retries": "none",
    "percentile_method": "nearest-rank",
    "primary": "per-run, per-arm figures; pooled figures are secondary",
}
STAGE_FIELDS: tuple[str, ...] = (
    "total_ms",
    "query_understanding_ms",
    "filter_translation_ms",
    "model_load_ms",
    "query_embedding_ms",
    "lexical_ms",
    "vector_ms",
    "document_read_ms",
    "rrf_ms",
    "reranker_load_ms",
    "rerank_document_ms",
    "reranking_ms",
)
TIMING_FIELDS: tuple[str, ...] = ("request_ms", "serialization_ms", *STAGE_FIELDS)
ALWAYS_PRESENT: frozenset[str] = frozenset(
    {
        "request_ms",
        "serialization_ms",
        "total_ms",
        "query_understanding_ms",
        "filter_translation_ms",
        "query_embedding_ms",
        "lexical_ms",
        "vector_ms",
        "rrf_ms",
    }
)
NULLABILITY_RULES: dict[str, str] = {
    "always_present": "request_ms, serialization_ms, total_ms, query_understanding_ms, "
    "filter_translation_ms, query_embedding_ms, lexical_ms, vector_ms, rrf_ms (every query has "
    "searchable text)",
    "model_load_ms": "non-null exactly on the cold request of each arm (fresh embedding provider)",
    "reranker_load_ms": "disabled: always null; enabled: non-null exactly on the first request of "
    "the arm whose reranker status is applied",
    "document_read_ms": "disabled: always null; enabled: non-null exactly when candidate_count > 0",
    "rerank_document_ms, reranking_ms": "non-null exactly when the reranker status is applied",
    "statistics": "timed samples only; a metric's count is its non-null values, nulls are counted "
    "separately and never replaced",
}
METRIC_DEFINITIONS: dict[str, str] = {
    "request_ms": "in-process TestClient GET, ends before response.json(); not network latency",
    "serialization_ms": "the parsed RerankedSearchResponse's model_dump_json(), timed by the "
    "harness on its own after request_ms has ended (never request_ms - total_ms)",
    "total_ms": "API latency_ms.total_ms: handler entry through result-model construction",
    "other stage fields": "the API's latency_ms fields, unchanged",
    "peak_rss_bytes": "process-lifetime peak resident set to date (Windows PeakWorkingSetSize "
    "via psutil); monotonic, so it cannot be separated per arm within one run process",
}
SCOPE: dict[str, str] = {
    "measured": "latency, cold model loads, determinism, descriptive rank movement/overlap, "
    "process memory, API and configuration contract facts",
    "not_measured": "relevance or quality of any kind; no configuration is selected or ranked",
    "quality_comparison": "Milestone 10 on the human-reviewed Golden Dataset",
    "human_labels": "none exist or are created here",
    "cost": "local CPU models and local database only; no paid or metered service",
}
DISCLAIMER = "No relevance or quality conclusion and no configuration winner."
RANK_LABEL = "descriptive position facts only; not a relevance or quality measure"
MOVEMENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("disabled", "depth10"),
    ("disabled", "depth20"),
    ("depth10", "depth20"),
)
RECORD_TYPES = frozenset({"run", "arm_start", "sample", "arm_end"})
SAMPLE_KEYS = frozenset(
    {
        "experiment_id",
        "run",
        "sequence",
        "record",
        "arm",
        "arm_position",
        "arm_order",
        "query_index",
        "query_sha256",
        "kind",
        "sample_index",
        "timestamp_utc",
        "settings",
        "models",
        "status_code",
        "search_version",
        "reranker",
        "fusion",
        "counts",
        "filter_policy_version",
        "applied_filters",
        "ignored_constraints",
        "understanding",
        "results",
        "timings",
        "hashes",
    }
)
RESULT_KEYS = (
    "product_ids",
    "rrf_ranks",
    "rerank_ranks",
    "rerank_scores",
    "rrf_scores",
    "lexical_ranks",
    "dense_ranks",
)
COUNT_KEYS = (
    "lexical_hit_count",
    "dense_hit_count",
    "overlap_count",
    "fused_count",
    "candidate_count",
    "top_k",
    "result_count",
)


class ProvenanceError(BenchmarkError):
    """The tree, production commit, query checksum, settings or a model pin is not as required."""


# ---- identity (pure; unit-tested) ----------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def query_identity(queries: Sequence[str] = QUERIES, pinned: str = QUERY_SET_SHA256) -> dict:
    """The predeclared query set: exactly the committed M5 queries, order and checksum."""
    if len(queries) != EXPECTED_QUERY_COUNT or len(set(queries)) != len(queries):
        raise ProvenanceError(f"the query set must be {EXPECTED_QUERY_COUNT} distinct queries")
    digest = sha256_text(dumps(list(queries)))
    if digest != pinned or digest != hybrid.QUERY_SET_SHA256:
        raise ProvenanceError("the query set differs from the committed M5 query checksum")
    return {
        "count": len(queries),
        "source": "scripts/benchmark_hybrid.py QUERY_PROTOCOL (committed M5 order)",
        "queries": list(queries),
        "query_set_sha256": digest,
        "query_sha256": [sha256_text(q) for q in queries],
    }


def experiment_id_for(harness_head: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", harness_head or ""):
        raise BenchmarkError("the harness HEAD is not a commit id")
    return f"{EXPERIMENT_PREFIX}{harness_head[:12]}"


def artifact_paths(output_dir: Path, experiment_id: str) -> dict[str, Path]:
    return {
        "raw": output_dir / f"{experiment_id}.samples.jsonl",
        "summary": output_dir / f"{experiment_id}.json",
        "report": output_dir / f"{experiment_id}.md",
    }


def check_fresh_output(output_dir: Path) -> Path:
    """The output directory must hold no earlier output: nothing is overwritten or mixed."""
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise BenchmarkError("the output directory already contains files; refusing to mix runs")
    return output_dir


def arm_order(run: int) -> tuple[str, str, str]:
    if run not in ARM_SCHEDULE:
        raise BenchmarkError(f"no predeclared arm order for run {run}")
    return ARM_SCHEDULE[run]


def arm_settings(arm: str) -> dict:
    """The effective settings recorded with every request of an arm."""
    if arm not in ARMS:
        raise BenchmarkError(f"unknown arm {arm!r}")
    return {
        "reranker_enabled": ARMS[arm]["reranker_enabled"],
        "search_rerank_depth": ARMS[arm]["search_rerank_depth"],
        "top_k": TOP_K,
        "search_lexical_k": BASE_SETTINGS["search_lexical_k"],
        "search_dense_k": BASE_SETTINGS["search_dense_k"],
        "search_candidate_k": BASE_SETTINGS["search_candidate_k"],
        "search_rrf_k": BASE_SETTINGS["search_rrf_k"],
    }


def request_plan(query_count: int = EXPECTED_QUERY_COUNT) -> list[tuple[str, int, int]]:
    """The exact request sequence of one arm: (kind, query_index, sample_index)."""
    plan = [("cold", PREDECLARED["cold_query_index"], i) for i in range(COLD_PER_ARM)]
    for qi in range(query_count):
        plan += [("warmup", qi, i) for i in range(WARMUP_PER_QUERY)]
        plan += [("timed", qi, i) for i in range(TIMED_PER_QUERY)]
    return plan


def expected_request_count() -> int:
    return RUNS * len(ARMS) * len(request_plan())


# ---- provenance, settings and model checks (pure with injected dependencies; unit-tested) --------


def check_provenance(root: Path, git: Callable[..., bytes] = _git) -> dict:
    """Refuse a dirty tree or any runtime-affecting difference from the production commit."""
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    if status.strip():
        raise ProvenanceError("the working tree is not clean; commit or remove changes first")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    try:
        git(root, "merge-base", "--is-ancestor", PRODUCTION_COMMIT, head)
    except GitError:
        raise ProvenanceError(
            f"the production commit {PRODUCTION_COMMIT} is missing or not an ancestor of HEAD"
        ) from None
    changed = git(root, "diff", "--name-only", PRODUCTION_COMMIT, head, "--", *GUARDED_PATHS)
    differing = [line for line in changed.decode().splitlines() if line.strip()]
    if differing:
        raise ProvenanceError(
            "runtime-affecting paths differ from the production commit: "
            + ", ".join(differing[:10])
        )
    return {
        "harness_head": head,
        "production_commit": PRODUCTION_COMMIT,
        "guarded": list(GUARDED_PATHS),
    }


# ---- local-snapshot provenance (M10; pure with injected dependencies; unit-tested) --------------


@dataclass(frozen=True)
class LocalSnapshot:
    """The operator's explicit local-snapshot declaration: every accepted guarded-path difference
    (path -> working-tree blob hash or `deleted`) and the reviewed binding hash (None only for
    `--preflight`, which prints it)."""

    accepted: Mapping[str, str]
    binding_sha256: str | None = None


def parse_accepted(values: Sequence[str] | None) -> dict[str, str]:
    """`PATH=BLOB` declarations; duplicates and malformed values are refused."""
    accepted: dict[str, str] = {}
    for value in values or ():
        path, sep, blob = value.partition("=")
        if not sep or not path or not BLOB_RE.fullmatch(blob):
            raise ProvenanceError(
                f"--accept-guarded-change must be PATH=<40-hex blob>|deleted, got {value!r}"
            )
        if path in accepted:
            raise ProvenanceError(f"guarded path declared twice: {path}")
        accepted[path] = blob
    return accepted


def _porcelain_paths(data: bytes) -> list[str]:
    """Paths of `git status --porcelain=v1 -z` entries (both sides of a rename or copy)."""
    tokens = [t for t in data.decode("utf-8").split("\0") if t]
    paths, index = [], 0
    while index < len(tokens):
        entry = tokens[index]
        paths.append(entry[3:])
        if entry[:1] in ("R", "C"):
            index += 1
            if index < len(tokens):
                paths.append(tokens[index])
        index += 1
    return paths


def _blob_at(root: Path, git: Callable[..., bytes], rev: str, path: str) -> str:
    try:
        return git(root, "rev-parse", f"{rev}:{path}").decode().strip()
    except GitError:
        return "absent"


def _working_blob(root: Path, git: Callable[..., bytes], path: str) -> str:
    if not (root / path).is_file():
        return "deleted"
    return git(root, "hash-object", "--", path).decode().strip()


def guarded_differences(root: Path, git: Callable[..., bytes] = _git) -> list[dict]:
    """Every guarded path whose content differs from the production commit, committed since it
    or uncommitted, with its production, HEAD and working-tree blob hashes."""
    diff = git(root, "diff", "--name-only", PRODUCTION_COMMIT, "HEAD", "--", *GUARDED_PATHS)
    committed = {line.strip() for line in diff.decode().splitlines() if line.strip()}
    status = git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *GUARDED_PATHS
    )
    uncommitted = set(_porcelain_paths(status))
    return [
        {
            "path": path,
            "production_blob": _blob_at(root, git, PRODUCTION_COMMIT, path),
            "head_blob": _blob_at(root, git, "HEAD", path),
            "working_blob": _working_blob(root, git, path),
            "committed_since_production": path in committed,
            "uncommitted": path in uncommitted,
        }
        for path in sorted(committed | uncommitted)
    ]


def check_local_snapshot(
    root: Path,
    accepted: Mapping[str, str],
    git: Callable[..., bytes] = _git,
    fingerprint: Callable[[Path], dict] = source_fingerprint,
) -> dict:
    """The local-snapshot provenance: the production commit is an ancestor of HEAD and every
    guarded-path difference is declared exactly. Nothing is ignored silently."""
    head = git(root, "rev-parse", "HEAD").decode().strip()
    try:
        git(root, "merge-base", "--is-ancestor", PRODUCTION_COMMIT, head)
    except GitError:
        raise ProvenanceError(
            f"the production commit {PRODUCTION_COMMIT} is missing or not an ancestor of HEAD"
        ) from None
    differences = guarded_differences(root, git)
    found = {d["path"]: d["working_blob"] for d in differences}
    problems = [
        f"undeclared guarded difference: --accept-guarded-change {path}={blob}"
        for path, blob in found.items()
        if path not in accepted
    ]
    problems += [
        f"declared guarded path does not differ from the production commit: {path}"
        for path in sorted(accepted)
        if path not in found
    ]
    problems += [
        f"guarded path {path} is now {found[path]}, not the declared {accepted[path]}"
        for path in sorted(accepted)
        if path in found and accepted[path] != found[path]
    ]
    if problems:
        raise ProvenanceError("guarded paths are not accounted for: " + "; ".join(problems))
    source = fingerprint(root)
    status = _porcelain_paths(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    return {
        "mode": LOCAL_MODE,
        "harness_head": head,
        "production_commit": PRODUCTION_COMMIT,
        "guarded": list(GUARDED_PATHS),
        "guarded_differences": differences,
        "source_tree_sha256": source["source_tree_sha256"],
        "source_working_tree_dirty": source["git_working_tree_dirty"],
        "uncommitted_paths": len(status),
        "uncommitted_paths_outside_source": sum(1 for p in status if not is_relevant(p)),
    }


def local_binding(provenance: dict, identity: dict, settings: dict, models: dict) -> dict:
    """Everything a local-snapshot run is bound to (uncommitted-path counts are recorded in the
    provenance but are not part of the binding)."""
    return {
        "mode": LOCAL_MODE,
        "production_commit": PRODUCTION_COMMIT,
        "source_tree_sha256": provenance["source_tree_sha256"],
        "guarded_differences": provenance["guarded_differences"],
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "libraries": library_versions(),
        "models": models,
        "base_settings": settings,
        "arm_settings": {arm: arm_settings(arm) for arm in ARMS},
        "protocol_sha256": sha256_text(dumps(PREDECLARED)),
        "query_set_sha256": identity["query_set_sha256"],
    }


def binding_sha256(binding: dict) -> str:
    return sha256_text(dumps(binding))


def local_experiment_id(binding_hash: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", binding_hash or ""):
        raise BenchmarkError("the binding hash is not a SHA-256")
    return f"{LOCAL_EXPERIMENT_PREFIX}{binding_hash[:12]}"


def check_local_output_dir(root: Path, out_dir: Path, git: Callable[..., bytes] = _git) -> Path:
    """A new, git-ignored directory under data/processed/ that is not the M8 result directory."""
    resolved = check_output_dir(root, out_dir, git)
    m8 = OUTPUT_DIR.resolve()
    if resolved == m8 or m8 in resolved.parents or resolved in m8.parents:
        raise BenchmarkError("the M8 result directory is preserved; choose a new output directory")
    return check_fresh_output(resolved)


def check_base_settings(settings, rrf_k_status: str) -> dict:
    """Refuse unless the effective configuration is exactly the approved base configuration and
    the default model pins. Values are refused, not merely recorded."""
    from ecommerce_search.embeddings.spec import DEFAULT_EMBEDDING_MODEL_ID, EMBEDDING_MODELS
    from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS

    differing = []
    for name, approved in BASE_SETTINGS.items():
        value = getattr(settings, name, "<missing>")
        if type(value) is not type(approved) or value != approved:
            differing.append(f"{name}={value!r} (approved {approved!r})")
    embedding = EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL_ID]
    reranker = RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]
    pins = {
        "embedding_model_id": embedding.model_id,
        "embedding_model_revision": embedding.revision,
        "reranker_model_id": reranker.model_id,
        "reranker_model_revision": reranker.revision,
    }
    for name, approved in pins.items():
        value = getattr(settings, name, "<missing>")
        if value != approved:
            differing.append(f"{name} differs from the registry pin")
    if differing:
        raise ProvenanceError("the approved settings are required: " + ", ".join(sorted(differing)))
    if rrf_k_status != RRF_K_STATUS:
        raise ProvenanceError(f"RRF_K_STATUS is {rrf_k_status!r}; expected {RRF_K_STATUS!r}")
    for arm in ARMS:
        if ARMS[arm]["search_rerank_depth"] > BASE_SETTINGS["search_candidate_k"]:
            raise ProvenanceError(f"arm {arm}: depth exceeds search_candidate_k")
    if min(cfg["search_rerank_depth"] for cfg in ARMS.values()) < TOP_K:
        raise ProvenanceError("top_k must not exceed the smallest rerank depth")
    return {name: BASE_SETTINGS[name] for name in BASE_SETTINGS if name != "embedding_models_dir"}


def check_reranker(models_dir: Path | None, spec, verify=None, config_problems=None) -> dict:
    """Offline manifest re-verification and configuration check of the pinned reranker."""
    if models_dir is None:
        raise ProvenanceError("no models directory is known (not a source checkout)")
    if verify is None or config_problems is None:
        from ecommerce_search.embeddings.fetch import RERANKER_PROFILE, verify_manifest
        from ecommerce_search.reranking.cross_encoder_provider import snapshot_config_problems

        verify = verify or (lambda d, m, r: verify_manifest(d, m, r, profile=RERANKER_PROFILE))
        config_problems = config_problems or snapshot_config_problems
    problems = verify(models_dir, spec.model_id, spec.revision)
    if problems:
        raise ProvenanceError(f"the pinned reranker snapshot failed verification: {problems[:3]}")
    if config_problems(models_dir, spec):
        raise ProvenanceError("the pinned reranker snapshot configuration differs from its spec")
    return {
        "model_id": spec.model_id,
        "revision": spec.revision,
        "max_length": spec.max_length,
        "num_labels": spec.num_labels,
        "score_type": spec.score_type,
        "config_sha256": spec.config_sha256(),
        "manifest_problems": [],
    }


def pinned_models() -> dict:
    """The model identity every header must carry: the registry defaults, manifests verified."""
    from ecommerce_search.embeddings.spec import (
        DEFAULT_EMBEDDING_MODEL_ID,
        EMBEDDING_MODELS,
        repository_models_dir,
    )
    from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS

    models_dir = repository_models_dir()
    from ecommerce_search.embeddings.fetch import verify_manifest

    return {
        "embedding": check_model(
            models_dir, EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL_ID], verify_manifest
        ),
        "reranker": check_reranker(models_dir, RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]),
    }


def record_models(models: dict) -> dict:
    """The compact model identity carried by every sample record."""
    return {
        "embedding_model_id": models["embedding"]["model_id"],
        "embedding_model_revision": models["embedding"]["revision"],
        "embedding_config_sha256": models["embedding"]["config_sha256"],
        "reranker_model_id": models["reranker"]["model_id"],
        "reranker_model_revision": models["reranker"]["revision"],
        "reranker_config_sha256": models["reranker"]["config_sha256"],
    }


def check_header(header: dict, models: dict | None = None) -> list[str]:
    """Identity a header must carry: id from its harness HEAD, the production commit, the
    predeclared protocol, the pinned query checksum, approved settings and the model pins."""
    problems = []
    provenance = header.get("provenance") or {}
    head = str(provenance.get("harness_head"))
    if provenance.get("mode") == LOCAL_MODE:
        problems += check_local_header(header, models)
    else:
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            problems.append("header harness HEAD is missing or not a commit id")
        elif header.get("experiment_id") != experiment_id_for(head):
            problems.append("experiment id is not derived from the harness HEAD")
        if not EXPERIMENT_ID_RE.fullmatch(str(header.get("experiment_id"))):
            problems.append("experiment id is not a reranker benchmark id")
    if provenance.get("production_commit") != PRODUCTION_COMMIT:
        problems.append("header production commit differs from the reviewed V3 commit")
    if provenance.get("guarded") != list(GUARDED_PATHS):
        problems.append("header guarded paths differ from the protected runtime paths")
    if header.get("protocol") != PREDECLARED:
        problems.append("the protocol differs from the predeclared protocol")
    try:
        if header.get("query_identity") != query_identity():
            problems.append("header query identity differs from the pinned query set")
    except ProvenanceError as exc:
        problems.append(str(exc))
    expected_base = {k: v for k, v in BASE_SETTINGS.items() if k != "embedding_models_dir"}
    if header.get("base_settings") != expected_base:
        problems.append("header settings differ from the approved base settings")
    if header.get("arm_settings") != {arm: arm_settings(arm) for arm in ARMS}:
        problems.append("header arm settings differ from the predeclared arms")
    if header.get("models") != (models if models is not None else pinned_models()):
        problems.append("header model identity differs from the pinned models")
    if header.get("scope") != SCOPE or header.get("nullability_rules") != NULLABILITY_RULES:
        problems.append("header scope or nullability rules differ from the predeclared ones")
    return problems


def check_local_header(header: dict, models: dict | None = None) -> list[str]:
    """Identity a local-snapshot header must carry: the experiment id derived from the binding,
    a binding that hashes to its recorded SHA-256 and agrees with the header's own source
    fingerprint, libraries, models, settings and protocol."""
    problems = []
    provenance = header.get("provenance") or {}
    binding = provenance.get("binding")
    recorded = str(provenance.get("binding_sha256"))
    if not isinstance(binding, dict) or binding_sha256(binding) != recorded:
        return ["local-snapshot binding is missing or does not hash to its recorded SHA-256"]
    if not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("harness_head"))):
        problems.append("header harness HEAD is missing or not a commit id")
    if not LOCAL_EXPERIMENT_ID_RE.fullmatch(str(header.get("experiment_id"))) or (
        header.get("experiment_id") != local_experiment_id(recorded)
    ):
        problems.append("experiment id is not derived from the local-snapshot binding")
    source = header.get("source") or {}
    if binding.get("source_tree_sha256") != source.get("source_tree_sha256") or (
        binding.get("source_tree_sha256") != provenance.get("source_tree_sha256")
    ):
        problems.append("binding source fingerprint differs from the header source fingerprint")
    if binding.get("guarded_differences") != provenance.get("guarded_differences"):
        problems.append("binding guarded differences differ from the header provenance")
    if binding.get("libraries") != header.get("libraries"):
        problems.append("binding library versions differ from the header")
    if binding.get("models") != header.get("models") or (
        models is not None and binding.get("models") != models
    ):
        problems.append("binding models differ from the header or the pinned models")
    if binding.get("base_settings") != header.get("base_settings") or binding.get(
        "arm_settings"
    ) != header.get("arm_settings"):
        problems.append("binding settings differ from the header settings")
    if binding.get("protocol_sha256") != sha256_text(dumps(PREDECLARED)):
        problems.append("binding protocol differs from the predeclared protocol")
    if binding.get("mode") != LOCAL_MODE or binding.get("production_commit") != PRODUCTION_COMMIT:
        problems.append("binding mode or production commit is not the local-snapshot identity")
    return problems


def check_child_args(args: argparse.Namespace, development_db: str) -> None:
    """The child accepts only the exact arguments its parent passes for a predeclared run."""
    if not isinstance(args.run, int) or args.run not in ARM_SCHEDULE:
        raise BenchmarkError(f"child run must be 1..{RUNS}")
    if (args.warmup, args.samples) != (WARMUP_PER_QUERY, TIMED_PER_QUERY):
        raise BenchmarkError("child sampling differs from the predeclared protocol")
    if getattr(args, "local_snapshot", False):
        if args.experiment_id != local_experiment_id(args.binding_sha256 or ""):
            raise BenchmarkError("child experiment id is not derived from the reviewed binding")
    elif not EXPERIMENT_ID_RE.fullmatch(args.experiment_id or ""):
        raise BenchmarkError("child experiment id is not a reranker benchmark id")
    if not args.out or ROOT.resolve() in Path(args.out).resolve().parents:
        raise BenchmarkError("child output must be a file outside the repository")
    assert_scratch_name(args.database or "", development_db)


# ---- per-request record and contract (pure; shared by the run process and --verify) -------------


def _finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def ranking_sha256(results: dict) -> str:
    keep = {k: results[k] for k in ("product_ids", "rrf_ranks", "rerank_ranks")}
    return sha256_text(dumps(keep))


def scores_sha256(results: dict) -> str:
    keep = {k: results[k] for k in ("rerank_scores", "rrf_scores")}
    return sha256_text(dumps(keep))


def facts_sha256(record: dict) -> str:
    keep = {
        k: record[k]
        for k in (
            "search_version",
            "reranker",
            "fusion",
            "counts",
            "filter_policy_version",
            "applied_filters",
            "ignored_constraints",
            "understanding",
        )
    }
    return sha256_text(dumps(keep))


def body_to_record(body: dict, serialization_ms: float, request_ms: float) -> dict:
    """The request-dependent part of a sample record, taken from the response body only."""
    rows = body["results"]
    results = {
        "product_ids": [r["product_id"] for r in rows],
        "rrf_ranks": [r["rrf_rank"] for r in rows],
        "rerank_ranks": [r["rerank_rank"] for r in rows],
        "rerank_scores": [r["rerank_score"] for r in rows],
        "rrf_scores": [r["rrf_score"] for r in rows],
        "lexical_ranks": [r["lexical_rank"] for r in rows],
        "dense_ranks": [r["dense_rank"] for r in rows],
    }
    if [r["rank"] for r in rows] != list(range(1, len(rows) + 1)):
        raise BenchmarkError("response ranks are not 1..n")
    understanding = body["query_understanding"]
    record = {
        "search_version": body["search_version"],
        "reranker": body["reranker"],
        "fusion": body["fusion"],
        "counts": {k: body[k] for k in COUNT_KEYS},
        "filter_policy_version": body["filter_policy"]["version"],
        "applied_filters": body["applied_filters"],
        "ignored_constraints": body["ignored_constraints"],
        "understanding": {
            "usage": understanding["usage"],
            "provider": understanding["provider"],
            "provider_version": understanding["provider_version"],
            "sha256": sha256_text(dumps(understanding["understanding"])),
        },
        "embedding_model_id": body["embedding_model_id"],
        "embedding_model_revision": body["embedding_model_revision"],
        "results": results,
        "timings": {
            "request_ms": request_ms,
            "serialization_ms": serialization_ms,
            **{k: body["latency_ms"][k] for k in STAGE_FIELDS},
        },
    }
    return record


def record_hashes(record: dict) -> dict:
    return {
        "ranking_sha256": ranking_sha256(record["results"]),
        "scores_sha256": scores_sha256(record["results"]),
        "facts_sha256": facts_sha256(record),
    }


def expected_status(arm: str, candidate_count: int) -> str:
    if not ARMS[arm]["reranker_enabled"]:
        return "disabled"
    return "applied" if candidate_count > 0 else "skipped_no_candidates"


def check_sample(record: dict, models: dict, first_applied: bool) -> None:
    """Every contract fact of one recorded request. Raises BenchmarkError on any violation.

    `first_applied`: whether this is the first request of its arm whose status is applied
    (the only one allowed to report a reranker load)."""
    what = (
        f"run {record.get('run')} {record.get('arm')} q{record.get('query_index')} "
        f"{record.get('kind')}#{record.get('sample_index')}"
    )
    if set(record) != SAMPLE_KEYS:
        raise BenchmarkError(f"{what}: sample keys differ from the record schema")
    arm = record["arm"]
    if arm not in ARMS:
        raise BenchmarkError(f"{what}: unknown arm")
    cfg = arm_settings(arm)
    if record["settings"] != cfg:
        raise BenchmarkError(f"{what}: settings differ from the predeclared arm")
    if record["models"] != record_models(models):
        raise BenchmarkError(f"{what}: model identity differs from the pins")
    if record["status_code"] != 200 or record["search_version"] != SEARCH_VERSION:
        raise BenchmarkError(f"{what}: not a 200 {SEARCH_VERSION} response")
    if record["fusion"] != EXPECTED_FUSION:
        raise BenchmarkError(f"{what}: fusion settings differ")
    if record["filter_policy_version"] != FILTER_POLICY_VERSION:
        raise BenchmarkError(f"{what}: filter policy version differs")
    understanding = record["understanding"]
    if {k: understanding.get(k) for k in UNDERSTANDING} != UNDERSTANDING:
        raise BenchmarkError(f"{what}: query understanding provider differs")
    if not re.fullmatch(r"[0-9a-f]{64}", str(understanding.get("sha256"))):
        raise BenchmarkError(f"{what}: query understanding hash is missing")
    if not isinstance(record["applied_filters"], list) or not isinstance(
        record["ignored_constraints"], list
    ):
        raise BenchmarkError(f"{what}: filters are not lists")
    c = record["counts"]
    if set(c) != set(COUNT_KEYS) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in c.values()
    ):
        raise BenchmarkError(f"{what}: counts are missing or not non-negative integers")
    if (
        c["top_k"] != TOP_K
        or c["lexical_hit_count"] > cfg["search_lexical_k"]
        or c["dense_hit_count"] > cfg["search_dense_k"]
        or c["overlap_count"] > min(c["lexical_hit_count"], c["dense_hit_count"])
        or c["fused_count"] != c["lexical_hit_count"] + c["dense_hit_count"] - c["overlap_count"]
        or c["candidate_count"] != min(c["fused_count"], cfg["search_candidate_k"])
        or c["result_count"] != min(c["candidate_count"], TOP_K)
    ):
        raise BenchmarkError(f"{what}: candidate, source or result counts are inconsistent")
    res = record["results"]
    if set(res) != set(RESULT_KEYS) or any(len(res[k]) != c["result_count"] for k in RESULT_KEYS):
        raise BenchmarkError(f"{what}: result lists differ in length from result_count")
    ids = res["product_ids"]
    if len(set(ids)) != len(ids) or not all(isinstance(p, str) and p for p in ids):
        raise BenchmarkError(f"{what}: result product ids are not unique strings")
    rrf_ranks = res["rrf_ranks"]
    if len(set(rrf_ranks)) != len(rrf_ranks) or not all(
        isinstance(r, int) and 1 <= r <= c["candidate_count"] for r in rrf_ranks
    ):
        raise BenchmarkError(f"{what}: rrf ranks are not unique positions in the candidate list")
    for lex, den, score in zip(
        res["lexical_ranks"], res["dense_ranks"], res["rrf_scores"], strict=True
    ):
        if lex is None and den is None:
            raise BenchmarkError(f"{what}: a result has neither a lexical nor a dense rank")
        if not _finite_number(score) or score <= 0:
            raise BenchmarkError(f"{what}: an rrf score is not a positive finite number")
    block = record["reranker"]
    status = expected_status(arm, c["candidate_count"])
    expected_block = {
        "enabled": cfg["reranker_enabled"],
        "status": status,
        "model_id": models["reranker"]["model_id"],
        "model_revision": models["reranker"]["revision"],
        "document_version": "rd-1",
        "max_length": models["reranker"]["max_length"],
        "rerank_depth": cfg["search_rerank_depth"],
        "reranked_count": (
            min(cfg["search_rerank_depth"], c["candidate_count"]) if status == "applied" else 0
        ),
        "score_type": models["reranker"]["score_type"],
    }
    if block != expected_block:
        raise BenchmarkError(f"{what}: reranker block differs (status/config/reranked_count)")
    scored = block["reranked_count"]
    if status == "applied":
        head = [i for i, r in enumerate(res["rerank_ranks"]) if r is not None]
        if head != list(range(min(scored, c["result_count"]))):
            raise BenchmarkError(f"{what}: scored results are not exactly the leading positions")
        for i in head:
            score = res["rerank_scores"][i]
            if res["rerank_ranks"][i] != i + 1 or not _finite_number(score):
                raise BenchmarkError(f"{what}: a scored result lacks a finite logit or rank")
            if rrf_ranks[i] > scored:
                raise BenchmarkError(f"{what}: a scored result lies beyond the rerank depth")
            if i and (
                res["rerank_scores"][i - 1] < score
                or (res["rerank_scores"][i - 1] == score and rrf_ranks[i - 1] > rrf_ranks[i])
            ):
                raise BenchmarkError(f"{what}: scored head is not ordered by score then rrf rank")
        tail = range(len(head), c["result_count"])
        if any(res["rerank_scores"][i] is not None for i in tail):
            raise BenchmarkError(f"{what}: an unscored tail result carries a score")
        tail_ranks = [rrf_ranks[i] for i in tail]
        if tail_ranks != sorted(tail_ranks) or any(r <= scored for r in tail_ranks):
            raise BenchmarkError(f"{what}: the unscored tail is not in rrf order beyond the head")
    else:
        if any(r is not None for r in res["rerank_ranks"]) or any(
            s is not None for s in res["rerank_scores"]
        ):
            raise BenchmarkError(f"{what}: rerank fields are not null without reranking")
        if rrf_ranks != list(range(1, c["result_count"] + 1)):
            raise BenchmarkError(f"{what}: results without reranking are not in rrf order")
    by_rrf = [score for _, score in sorted(zip(rrf_ranks, res["rrf_scores"], strict=True))]
    if any(a < b for a, b in zip(by_rrf, by_rrf[1:], strict=False)):
        raise BenchmarkError(f"{what}: rrf scores are not non-increasing in rrf rank")
    check_nullability(record, status, first_applied, what)
    if record["hashes"] != record_hashes(record):
        raise BenchmarkError(f"{what}: deterministic fact hashes do not recompute")


def check_nullability(record: dict, status: str, first_applied: bool, what: str) -> None:
    timings = record["timings"]
    if set(timings) != set(TIMING_FIELDS):
        raise BenchmarkError(f"{what}: timing fields differ")
    enabled = ARMS[record["arm"]]["reranker_enabled"]
    expected_present = {
        **{k: True for k in ALWAYS_PRESENT},
        "model_load_ms": record["kind"] == "cold",
        "reranker_load_ms": enabled and status == "applied" and first_applied,
        "document_read_ms": enabled and record["counts"]["candidate_count"] > 0,
        "rerank_document_ms": status == "applied",
        "reranking_ms": status == "applied",
    }
    for name, present in expected_present.items():
        value = timings[name]
        if present and (not _finite_number(value) or value < 0):
            raise BenchmarkError(f"{what}: {name} must be a non-negative number")
        if not present and value is not None:
            raise BenchmarkError(f"{what}: {name} must be null")


# ---- descriptive rank facts (pure; unit-tested) -------------------------------------------------


def frac(value: Fraction) -> dict:
    return {"fraction": str(value), "float": round(float(value), 6)}


def rank_movement(a: Sequence[str], b: Sequence[str]) -> dict:
    """Neutral position facts of list `b` relative to list `a` (no judgement of either)."""
    pos_a = {p: i for i, p in enumerate(a, start=1)}
    pos_b = {p: i for i, p in enumerate(b, start=1)}
    common = [p for p in a if p in pos_b]
    compared = max(len(a), len(b))
    same = sum(1 for x, y in zip(a, b, strict=False) if x == y)
    movement = sum(abs(pos_a[p] - pos_b[p]) for p in common)
    return {
        "overlap": len(common),
        "compared_positions": compared,
        "same_position": same,
        "changed_positions": compared - same,
        "identical_order": list(a) == list(b),
        "mean_abs_movement_common": frac(Fraction(movement, len(common))) if common else None,
        "promoted": [
            {"product_id": p, "from": pos_a[p], "to": pos_b[p]}
            for p in b
            if p in pos_a and pos_b[p] < pos_a[p]
        ],
        "demoted": [
            {"product_id": p, "from": pos_a[p], "to": pos_b[p]}
            for p in b
            if p in pos_a and pos_b[p] > pos_a[p]
        ],
        "only_in_first": [p for p in a if p not in pos_b],
        "only_in_second": [p for p in b if p not in pos_a],
    }


def head_movement(results: dict) -> dict:
    """Within one reranked list: position versus fused (RRF) position."""
    pairs = list(zip(range(1, len(results["rrf_ranks"]) + 1), results["rrf_ranks"], strict=True))
    moved = [abs(rank - rrf) for rank, rrf in pairs]
    return {
        "results": len(pairs),
        "position_differs_from_rrf_rank": sum(1 for m in moved if m),
        "mean_abs_rank_minus_rrf_rank": frac(Fraction(sum(moved), len(moved))) if moved else None,
        "results_with_rrf_rank_beyond_top_k": sum(1 for _, rrf in pairs if rrf > TOP_K),
    }


# ---- raw record integrity and derivation (pure; used by --verify) --------------------------------


def check_record_stream(header: dict, records: list[dict]) -> None:
    """Records must be complete per run, in (run, sequence) order, unduplicated and typed."""
    expected_run, expected_sequence = 1, 0
    for record in records:
        for key in ("experiment_id", "run", "sequence", "record"):
            if key not in record:
                raise BenchmarkError(f"a record is missing {key!r}")
        if record["experiment_id"] != header["experiment_id"]:
            raise BenchmarkError("a record carries a different experiment_id")
        if record["record"] not in RECORD_TYPES:
            raise BenchmarkError(f"unexpected record type {record['record']!r}")
        if record["run"] == expected_run + 1 and record["sequence"] == 0 and expected_sequence:
            expected_run, expected_sequence = expected_run + 1, 0
        if (record["run"], record["sequence"]) != (expected_run, expected_sequence):
            raise BenchmarkError(
                f"records are missing, duplicated or reordered near run {record['run']} "
                f"sequence {record['sequence']}"
            )
        expected_sequence += 1
    if expected_run != header["protocol"]["runs"] or not records:
        raise BenchmarkError(f"expected records for {header['protocol']['runs']} runs")


def _metric_summary(values: list) -> dict:
    present = [v for v in values if v is not None]
    return {
        "count": len(present),
        "null_count": len(values) - len(present),
        "stats": summarize(present) if present else None,
    }


def _check_resources(entry: dict, what: str) -> None:
    for key in ("rss_bytes", "peak_rss_bytes"):
        value = entry.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise BenchmarkError(f"{what}: {key} is missing or not a positive integer")
    if entry["peak_rss_bytes"] < entry["rss_bytes"]:
        raise BenchmarkError(f"{what}: peak RSS is below the current RSS")


def derive(header: dict, records: list[dict]) -> dict:
    """Every reported figure, recomputed from raw records only. Raises on incomplete data."""
    check_record_stream(header, records)
    protocol, models = header["protocol"], header["models"]
    queries = header["query_identity"]["queries"]
    query_hashes = header["query_identity"]["query_sha256"]
    plan = request_plan(len(queries))
    catalog = header["catalog"]
    out: dict = {"disclaimer": DISCLAIMER, "runs": {}}
    lists: dict[tuple[int, str, int], dict] = {}
    by_run = {
        run: [r for r in records if r["run"] == run] for run in range(1, protocol["runs"] + 1)
    }
    pooled: dict[str, dict[str, list]] = {arm: {m: [] for m in TIMING_FIELDS} for arm in ARMS}
    for run, run_records in by_run.items():
        order = arm_order(run)
        expected_count = 1 + len(order) * (2 + len(plan))
        if len(run_records) != expected_count:
            raise BenchmarkError(
                f"run {run}: expected {expected_count} records, found {len(run_records)}"
            )
        info = run_records[0]
        if info["record"] != "run" or info.get("arm_order") != list(order):
            raise BenchmarkError(f"run {run}: run record or arm order differs from the schedule")
        expected_datasets = [{k: catalog[k] for k in hybrid.CATALOG_KEYS}]
        if info.get("catalog_datasets") != expected_datasets:
            raise BenchmarkError(f"run {run}: scratch catalog identity differs from the header")
        if info.get("migration_head") != "0004":
            raise BenchmarkError(f"run {run}: scratch database is not at migration head 0004")
        if (info.get("product_count"), info.get("embedding_rows")) != (SEED_PRODUCTS,) * 2:
            raise BenchmarkError(f"run {run}: product or embedding count is not {SEED_PRODUCTS}")
        _check_resources(info.get("setup_resources") or {}, f"run {run} setup")
        threads = info.get("torch_num_threads")
        if not isinstance(threads, int) or threads < 1:
            raise BenchmarkError(f"run {run}: torch thread count is missing")
        run_out: dict = {
            "arm_order": list(order),
            "torch_num_threads": threads,
            "setup_resources": info["setup_resources"],
            "arms": {},
        }
        stamps: list[str] = []
        cursor = 1
        for position, arm in enumerate(order):
            what = f"run {run} {arm}"
            block = run_records[cursor : cursor + 2 + len(plan)]
            cursor += 2 + len(plan)
            start, samples, end = block[0], block[1:-1], block[-1]
            for marker, kind in ((start, "arm_start"), (end, "arm_end")):
                if (
                    marker["record"] != kind
                    or marker.get("arm") != arm
                    or marker.get("arm_position") != position
                    or marker.get("arm_order") != list(order)
                    or marker.get("settings") != arm_settings(arm)
                ):
                    raise BenchmarkError(f"{what}: {kind} record differs from the schedule")
                _check_resources(marker, f"{what} {kind}")
            probe = end.get("reranker_probe") or {}
            enabled = ARMS[arm]["reranker_enabled"]
            if probe.get("max_checked_out_during_reranker_calls") != 0:
                raise BenchmarkError(f"{what}: a database connection was held during reranker work")
            if not enabled and (
                probe.get("load_calls"),
                probe.get("count_calls"),
                probe.get("score_calls"),
                probe.get("loaded"),
            ) != (0, 0, 0, False):
                raise BenchmarkError(f"{what}: the disabled arm touched the reranker")
            if end.get("embedder_loaded") is not True or end.get("requests") != len(plan):
                raise BenchmarkError(f"{what}: arm end facts are incomplete")
            if end.get("pool_checked_out_after") != 0:
                raise BenchmarkError(f"{what}: a connection was still checked out after the arm")
            first_seen: dict[int, dict] = {}
            score_hashes: dict[int, set] = {}
            seen_applied = False
            applied = 0
            timed: dict[int, list[dict]] = {}
            cold_timings = None
            for record, (kind, qi, si) in zip(samples, plan, strict=True):
                if (
                    record["record"] != "sample"
                    or record.get("arm") != arm
                    or record.get("arm_position") != position
                    or record.get("arm_order") != list(order)
                    or (record.get("kind"), record.get("query_index"), record.get("sample_index"))
                    != (kind, qi, si)
                ):
                    raise BenchmarkError(
                        f"{what}: sample {kind} q{qi}#{si} is missing, duplicated or reordered"
                    )
                if record["query_sha256"] != query_hashes[qi]:
                    raise BenchmarkError(f"{what}: q{qi} query hash differs from the query set")
                status = expected_status(arm, record["counts"]["candidate_count"])
                first_applied = status == "applied" and not seen_applied
                check_sample(record, models, first_applied)
                if status == "applied":
                    seen_applied, applied = True, applied + 1
                hashes = record["hashes"]
                first = first_seen.setdefault(qi, record)
                if (
                    hashes["ranking_sha256"] != first["hashes"]["ranking_sha256"]
                    or hashes["facts_sha256"] != first["hashes"]["facts_sha256"]
                ):
                    raise BenchmarkError(f"{what}: q{qi} result list changed between requests")
                score_hashes.setdefault(qi, set()).add(hashes["scores_sha256"])
                stamps.append(record["timestamp_utc"])
                if kind == "cold":
                    cold_timings = record["timings"]
                elif kind == "timed":
                    timed.setdefault(qi, []).append(record["timings"])
            if enabled and (
                probe.get("load_calls") != applied
                or probe.get("count_calls") != applied
                or probe.get("score_calls") != applied
                or probe.get("loaded") is not (applied > 0)
            ):
                raise BenchmarkError(f"{what}: reranker call counts differ from applied requests")
            all_timed = [t for qi in range(len(queries)) for t in timed[qi]]
            for metric in TIMING_FIELDS:
                pooled[arm][metric] += [t[metric] for t in all_timed]
            run_out["arms"][arm] = {
                "position": position,
                "settings": arm_settings(arm),
                "requests": len(plan),
                "counts": {
                    "cold": sum(1 for k, _, _ in plan if k == "cold"),
                    "warmup": sum(1 for k, _, _ in plan if k == "warmup"),
                    "timed": len(all_timed),
                },
                "cold": {
                    "query_index": PREDECLARED["cold_query_index"],
                    "timings": cold_timings,
                },
                "timed_overall": {
                    m: _metric_summary([t[m] for t in all_timed]) for m in TIMING_FIELDS
                },
                "timed_per_query": {
                    str(qi): {m: _metric_summary([t[m] for t in timed[qi]]) for m in TIMING_FIELDS}
                    for qi in range(len(queries))
                },
                "resources": {
                    "start": {k: start[k] for k in ("rss_bytes", "peak_rss_bytes")},
                    "end": {k: end[k] for k in ("rss_bytes", "peak_rss_bytes")},
                },
                "reranker_probe": probe,
                "applied_requests": applied,
                "determinism": {
                    "ranking_identical_within_query": True,
                    "facts_identical_within_query": True,
                    "queries_with_bitwise_identical_scores": sum(
                        1 for qi in range(len(queries)) if len(score_hashes[qi]) == 1
                    ),
                    "distinct_score_vectors_per_query": {
                        str(qi): len(score_hashes[qi]) for qi in range(len(queries))
                    },
                },
            }
            for qi in range(len(queries)):
                lists[(run, arm, qi)] = first_seen[qi]
        ordered_stamps = sorted(stamps)
        gaps = [
            (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
            for a, b in zip(ordered_stamps, ordered_stamps[1:], strict=False)
        ]
        run_out["max_sampling_gap_s"] = round(max(gaps), 3) if gaps else 0.0
        run_out["rank_facts"] = _rank_facts(run, lists, len(queries))
        out["runs"][str(run)] = run_out
    out["pooled_timed_secondary"] = {
        arm: {m: _metric_summary(values) for m, values in metrics.items()}
        for arm, metrics in pooled.items()
    }
    out["determinism_across_runs"] = _across_runs(lists, protocol["runs"], len(queries))
    out["counts"] = {
        "sample_records": sum(1 for r in records if r["record"] == "sample"),
        "timed": sum(1 for r in records if r.get("kind") == "timed"),
        "warmup": sum(1 for r in records if r.get("kind") == "warmup"),
        "cold": sum(1 for r in records if r.get("kind") == "cold"),
        "status_200": sum(1 for r in records if r.get("status_code") == 200),
    }
    return out


def _rank_facts(run: int, lists: dict, query_count: int) -> dict:
    """Cross-arm V2-compatibility checks (hard) and descriptive movement facts (neutral)."""
    per_query: dict = {}
    totals = {
        f"{a}|{b}": {"overlap": 0, "same_position": 0, "changed_positions": 0, "identical": 0}
        for a, b in MOVEMENT_PAIRS
    }
    for qi in range(query_count):
        by_arm = {arm: lists[(run, arm, qi)] for arm in ARMS}
        disabled = by_arm["disabled"]["results"]
        shared = {
            k: by_arm["disabled"][k]
            for k in ("counts", "fusion", "applied_filters", "ignored_constraints", "understanding")
        }
        for arm, record in by_arm.items():
            if {k: record[k] for k in shared} != shared:
                raise BenchmarkError(f"run {run} q{qi}: {arm} candidate facts differ from disabled")
            res = record["results"]
            for i, rrf_rank in enumerate(res["rrf_ranks"]):
                if rrf_rank <= len(disabled["product_ids"]):
                    j = rrf_rank - 1
                    if (
                        res["product_ids"][i],
                        res["rrf_scores"][i],
                        res["lexical_ranks"][i],
                        res["dense_ranks"][i],
                    ) != (
                        disabled["product_ids"][j],
                        disabled["rrf_scores"][j],
                        disabled["lexical_ranks"][j],
                        disabled["dense_ranks"][j],
                    ):
                        raise BenchmarkError(
                            f"run {run} q{qi}: {arm} fused candidate facts differ from disabled"
                        )
        entry: dict = {
            "candidate_count": by_arm["disabled"]["counts"]["candidate_count"],
            "ids": {arm: by_arm[arm]["results"]["product_ids"] for arm in ARMS},
            "pairs": {},
            "head_movement": {
                arm: head_movement(by_arm[arm]["results"])
                for arm in ARMS
                if ARMS[arm]["reranker_enabled"]
            },
        }
        for a, b in MOVEMENT_PAIRS:
            moved = rank_movement(
                by_arm[a]["results"]["product_ids"], by_arm[b]["results"]["product_ids"]
            )
            entry["pairs"][f"{a}|{b}"] = moved
            total = totals[f"{a}|{b}"]
            total["overlap"] += moved["overlap"]
            total["same_position"] += moved["same_position"]
            total["changed_positions"] += moved["changed_positions"]
            total["identical"] += moved["identical_order"]
        per_query[str(qi)] = entry
    return {
        "label": RANK_LABEL,
        "per_query": per_query,
        "totals": {
            pair: {
                "overlap_sum": t["overlap"],
                "same_position_sum": t["same_position"],
                "changed_positions_sum": t["changed_positions"],
                "queries_with_identical_order": t["identical"],
                "queries": query_count,
            }
            for pair, t in totals.items()
        },
    }


def _across_runs(lists: dict, runs: int, query_count: int) -> dict:
    out: dict = {}
    for arm in ARMS:
        ranking = [
            qi
            for qi in range(query_count)
            if len({lists[(r, arm, qi)]["hashes"]["ranking_sha256"] for r in range(1, runs + 1)})
            != 1
        ]
        scores = [
            qi
            for qi in range(query_count)
            if len({lists[(r, arm, qi)]["hashes"]["scores_sha256"] for r in range(1, runs + 1)})
            != 1
        ]
        out[arm] = {
            "ranking_identical_all_queries": not ranking,
            "queries_with_ranking_differences": ranking,
            "scores_bitwise_identical_all_queries": not scores,
            "queries_with_score_differences": scores,
        }
    return out


# ---- artifacts -----------------------------------------------------------------------------------


def raw_body(header: dict, records: list[dict]) -> str:
    return "\n".join([dumps({"record": "header", **header}), *(dumps(r) for r in records)]) + "\n"


def write_raw(paths: dict[str, Path], header: dict, records: list[dict]) -> str:
    """Write the raw JSONL first (exact LF bytes), so it survives a later derivation failure."""
    body = raw_body(header, records).encode("utf-8")
    paths["raw"].write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def write_summary(
    paths: dict[str, Path], header: dict, records: list[dict], raw_sha: str, extra: dict
) -> Path:
    summary = {
        "experiment_id": header["experiment_id"],
        "raw_samples": {"file": paths["raw"].name, "sha256": raw_sha, "records": len(records)},
        "header": header,
        "derived": json.loads(json.dumps(derive(header, records))),
        "metadata_not_recomputed": extra,
    }
    paths["summary"].write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    paths["report"].write_text(markdown(summary), encoding="utf-8")
    return paths["summary"]


def verify_artifacts(json_path: Path, models: dict | None = None) -> list[str]:
    try:
        summary = json.loads(json_path.read_text(encoding="utf-8"))
        raw_path = json_path.with_name(summary["raw_samples"]["file"])
        raw_bytes = raw_path.read_bytes()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [f"artifacts are missing or unreadable: {type(exc).__name__}"]
    problems = []
    if hashlib.sha256(raw_bytes).hexdigest() != summary["raw_samples"]["sha256"]:
        problems.append("raw samples file hash differs from the summary")
    if raw_path.name != f"{summary.get('experiment_id')}.samples.jsonl" or json_path.name != (
        f"{summary.get('experiment_id')}.json"
    ):
        problems.append("artifact file names differ from the experiment id")
    header, records = read_samples(raw_path)
    header = {k: v for k, v in header.items() if k != "record"}
    if header != summary["header"]:
        problems.append("raw header differs from the summary header")
    if len(records) != summary["raw_samples"]["records"]:
        problems.append("raw record count differs from the summary")
    problems += check_header(header, models)
    if header.get("protocol") != PREDECLARED:
        return problems
    try:
        again = json.loads(json.dumps(derive(header, records)))
    except (BenchmarkError, KeyError, TypeError, ValueError, IndexError) as exc:
        return [*problems, f"raw records are incomplete or inconsistent: {exc}"]
    if again != summary["derived"]:
        problems.append("derived summary (latency, determinism or rank facts) does not recompute")
    return problems


def _ms(value) -> str:
    return "null" if value is None else f"{value:.3f}"


def markdown(summary: dict) -> str:
    d, h = summary["derived"], summary["header"]
    lines = [
        f"# V3 reranker latency benchmark (generated) `{summary['experiment_id']}`",
        "",
        f"**{DISCLAIMER}** Latency, determinism, descriptive rank movement, memory and contract "
        "facts only.",
        "",
        f"- Harness HEAD `{h['provenance']['harness_head']}`; production commit "
        f"`{h['provenance']['production_commit']}`",
        *(
            [
                f"- Local snapshot (not reproducible from HEAD): binding "
                f"`{h['provenance']['binding_sha256']}`, source tree "
                f"`{h['provenance']['source_tree_sha256']}`, guarded differences "
                f"{[d['path'] for d in h['provenance']['guarded_differences']]}, "
                f"{h['provenance']['uncommitted_paths']} uncommitted paths"
            ]
            if h["provenance"].get("mode") == LOCAL_MODE
            else []
        ),
        f"- Queries: {h['query_identity']['count']} (sha256 "
        f"`{h['query_identity']['query_set_sha256']}`)",
        f"- Embedding `{h['models']['embedding']['model_id']}@"
        f"{h['models']['embedding']['revision']}`; reranker "
        f"`{h['models']['reranker']['model_id']}@{h['models']['reranker']['revision']}`",
        f"- Protocol: {RUNS} runs; per arm 1 cold request, then per query {WARMUP_PER_QUERY} "
        f"warm-ups and {TIMED_PER_QUERY} timed; `{METHOD} {ENDPOINT}` top_k={TOP_K}; "
        "nearest-rank percentiles; no exclusions or retries",
        f"- Records: {d['counts']['sample_records']} requests ({d['counts']['cold']} cold, "
        f"{d['counts']['warmup']} warm-up, {d['counts']['timed']} timed), "
        f"{d['counts']['status_200']} with status 200",
        "",
        "## Timed latency per run and arm (ms)",
        "",
        "| run | arm | metric | count | nulls | min | mean | p50 | p95 | p99 | max |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run, data in d["runs"].items():
        for arm, a in data["arms"].items():
            for metric, s in a["timed_overall"].items():
                st = s["stats"]
                if st is None:
                    lines.append(
                        f"| {run} | {arm} | {metric} | 0 | {s['null_count']} |" + " - |" * 6
                    )
                    continue
                lines.append(
                    f"| {run} | {arm} | {metric} | {s['count']} | {s['null_count']} | "
                    f"{st['min']} | {st['mean']} | {st['p50']} | {st['p95']} | {st['p99']} | "
                    f"{st['max']} |"
                )
    lines += [
        "",
        "## Cold request per arm (ms)",
        "",
        "| run | arm | position | request_ms | total_ms | model_load_ms | reranker_load_ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for run, data in d["runs"].items():
        for arm, a in data["arms"].items():
            t = a["cold"]["timings"]
            lines.append(
                f"| {run} | {arm} | {a['position'] + 1} | {_ms(t['request_ms'])} | "
                f"{_ms(t['total_ms'])} | {_ms(t['model_load_ms'])} | {_ms(t['reranker_load_ms'])} |"
            )
    lines += [
        "",
        "## Process memory (MiB; peak is process-lifetime to date)",
        "",
        "| run | point | rss | peak |",
        "|---|---|---|---|",
    ]
    for run, data in d["runs"].items():
        s = data["setup_resources"]
        lines.append(
            f"| {run} | after setup | {s['rss_bytes'] / 2**20:.1f} | "
            f"{s['peak_rss_bytes'] / 2**20:.1f} |"
        )
        for arm, a in data["arms"].items():
            e = a["resources"]["end"]
            lines.append(
                f"| {run} | end of {arm} | {e['rss_bytes'] / 2**20:.1f} | "
                f"{e['peak_rss_bytes'] / 2**20:.1f} |"
            )
    lines += [
        "",
        "## Descriptive rank facts (top 10; neutral position facts)",
        "",
        "| run | pair | overlap sum | same position sum | changed positions sum | "
        "identical-order queries |",
        "|---|---|---|---|---|---|",
    ]
    for run, data in d["runs"].items():
        for pair, t in data["rank_facts"]["totals"].items():
            lines.append(
                f"| {run} | {pair} | {t['overlap_sum']} | {t['same_position_sum']} | "
                f"{t['changed_positions_sum']} | {t['queries_with_identical_order']}/"
                f"{t['queries']} |"
            )
    lines += ["", "## Determinism", ""]
    for arm, facts in d["determinism_across_runs"].items():
        lines.append(
            f"- {arm}: ranking identical across runs {facts['ranking_identical_all_queries']}; "
            f"scores bitwise identical across runs {facts['scores_bitwise_identical_all_queries']}"
        )
    for run, data in d["runs"].items():
        lines.append(
            f"- run {run}: max sampling gap {data['max_sampling_gap_s']} s; torch threads "
            f"{data['torch_num_threads']}"
        )
    return "\n".join(lines) + "\n"


def protocol_text() -> str:
    identity = query_identity()
    lines = [f"query set sha256: {identity['query_set_sha256']}", ""]
    lines += [f"{i:2d} | {q}" for i, q in enumerate(identity["queries"])]
    lines += [
        "",
        f"arms: {json.dumps(ARMS)}",
        f"schedule: {json.dumps(PREDECLARED['arm_schedule'])}",
    ]
    lines.append(f"requests in total: {expected_request_count()}")
    return "\n".join(lines)


# ---- run process (database and model work; executed only with approval) --------------------------


def peak_rss_bytes(process) -> int:
    """Process-lifetime peak resident set: Windows PeakWorkingSetSize (psutil `peak_wset`), or
    POSIX ru_maxrss. Refuses rather than inventing a value."""
    info = process.memory_info()
    peak = getattr(info, "peak_wset", None)
    if peak is not None:
        return int(peak)
    if sys.platform.startswith("linux"):
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    raise BenchmarkError("peak RSS cannot be measured accurately on this platform")


def resources(process) -> dict:
    return {"rss_bytes": int(process.memory_info().rss), "peak_rss_bytes": peak_rss_bytes(process)}


class RerankerProbe:
    """Pass-through RerankerProvider: delegates unchanged and counts calls, and records how many
    pooled database connections were checked out at each call (the API guarantees none)."""

    def __init__(self, inner, checked_out: Callable[[], int]) -> None:
        self._inner = inner
        self._checked_out = checked_out
        self.load_calls = self.count_calls = self.score_calls = 0
        self.max_checked_out = 0

    def _observe(self) -> None:
        self.max_checked_out = max(self.max_checked_out, self._checked_out())

    @property
    def spec(self):
        return self._inner.spec

    @property
    def loaded(self) -> bool:
        return self._inner.loaded

    def load(self):
        self.load_calls += 1
        self._observe()
        return self._inner.load()

    def count_pair_tokens(self, query, documents):
        self.count_calls += 1
        self._observe()
        return self._inner.count_pair_tokens(query, documents)

    def score(self, query, documents):
        self.score_calls += 1
        self._observe()
        return self._inner.score(query, documents)

    def facts(self) -> dict:
        return {
            "load_calls": self.load_calls,
            "count_calls": self.count_calls,
            "score_calls": self.score_calls,
            "max_checked_out_during_reranker_calls": self.max_checked_out,
            "loaded": self.loaded,
        }


def collect_arm(
    client,
    arm: str,
    position: int,
    run: int,
    queries: Sequence[str],
    models: dict,
    emit: Callable[[dict], None],
    parse: Callable[[dict], object],
) -> None:
    """Issue the predeclared request sequence of one arm; check and record every request."""
    order = list(arm_order(run))
    settings = arm_settings(arm)
    model_ids = record_models(models)
    first_seen: dict[int, dict] = {}
    seen_applied = False
    for kind, qi, si in request_plan(len(queries)):
        query = queries[qi]
        wall = datetime.now(UTC).isoformat(timespec="microseconds")
        started = time.perf_counter()
        response = client.get(ENDPOINT, params={"q": query, "top_k": TOP_K})
        request_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise BenchmarkError(f"run {run} {arm} q{qi}: status {response.status_code}")
        body = response.json()
        parsed = parse(body)
        started = time.perf_counter()
        parsed.model_dump_json()
        serialization_ms = (time.perf_counter() - started) * 1000
        part = body_to_record(body, serialization_ms, request_ms)
        if (part.pop("embedding_model_id"), part.pop("embedding_model_revision")) != (
            model_ids["embedding_model_id"],
            model_ids["embedding_model_revision"],
        ):
            raise BenchmarkError(f"run {run} {arm} q{qi}: embedding model differs from the pin")
        record = {
            "experiment_id": None,
            "run": run,
            "sequence": None,
            "record": "sample",
            "arm": arm,
            "arm_position": position,
            "arm_order": order,
            "query_index": qi,
            "query_sha256": sha256_text(query),
            "kind": kind,
            "sample_index": si,
            "timestamp_utc": wall,
            "settings": settings,
            "models": model_ids,
            "status_code": response.status_code,
            **part,
        }
        record["hashes"] = record_hashes(record)
        status = expected_status(arm, record["counts"]["candidate_count"])
        check_sample(record, models, status == "applied" and not seen_applied)
        seen_applied = seen_applied or status == "applied"
        first = first_seen.setdefault(qi, record)
        if (
            record["hashes"]["ranking_sha256"] != first["hashes"]["ranking_sha256"]
            or record["hashes"]["facts_sha256"] != first["hashes"]["facts_sha256"]
        ):
            raise BenchmarkError(f"run {run} {arm} q{qi}: result list changed between requests")
        emit(record)


def library_versions() -> dict:
    names = ("torch", "sentence-transformers", "transformers", "tokenizers", "fastapi", "psutil")
    out = {}
    for name in names:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def preflight(settings, local: LocalSnapshot | None = None) -> dict:
    """Every invariant the benchmark depends on, before any setup, database or model work.
    Runs in the parent and again in every child. With `local`, the local-snapshot provenance
    replaces the clean-tree check and the binding must equal the reviewed one (if given)."""
    from ecommerce_search.search.hybrid import RRF_K_STATUS as runtime_status

    if local is None:
        provenance = check_provenance(ROOT)
    else:
        provenance = check_local_snapshot(ROOT, local.accepted)
    identity = query_identity()
    hybrid.check_protocol_identity()
    base = check_base_settings(settings, runtime_status)
    models = pinned_models()
    import psutil

    peak_rss_bytes(psutil.Process())  # refuse now if peak RSS is not measurable
    if local is not None:
        binding = local_binding(provenance, identity, base, models)
        digest = binding_sha256(binding)
        if local.binding_sha256 is not None and local.binding_sha256 != digest:
            raise ProvenanceError(
                f"the local-snapshot binding is {digest}, not the reviewed {local.binding_sha256}"
            )
        provenance = {**provenance, "binding": binding, "binding_sha256": digest}
    return {"provenance": provenance, "identity": identity, "settings": base, "models": models}


def local_from_args(args: argparse.Namespace) -> LocalSnapshot | None:
    if not getattr(args, "local_snapshot", False):
        return None
    return LocalSnapshot(parse_accepted(args.accept_guarded_change), args.binding_sha256)


def child_main(args: argparse.Namespace) -> int:  # pragma: no cover - needs a database and model
    install_network_guard()
    os.environ.update(OFFLINE_ENV)
    import psutil

    from ecommerce_search.config import get_settings

    process = psutil.Process()
    settings = get_settings()
    check_child_args(args, settings.postgres_db)
    checked = preflight(settings, local_from_args(args))
    name = args.database
    queries = checked["identity"]["queries"]
    models = checked["models"]

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ecommerce_search.api.app import create_app
    from ecommerce_search.api.schemas import RerankedSearchResponse
    from ecommerce_search.embeddings.sentence_transformers_provider import (
        SentenceTransformerEmbedder,
    )
    from ecommerce_search.ingestion.service import ingest_file
    from ecommerce_search.search.dense_indexing import embed, embedding_status

    records: list[dict] = []

    def emit(record: dict) -> None:
        record.update({"experiment_id": args.experiment_id, "run": args.run})
        record["sequence"] = len(records)
        records.append(record)

    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with scratch_database(admin, name, settings.postgres_db):
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            config.attributes["database"] = name
            command.upgrade(config, "head")
            engine = create_engine(settings.database_url(database=name))
            with engine.connect() as conn:
                head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if head != "0004":
                raise BenchmarkError("the scratch database is not at migration head 0004")
            seed = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
            outcome = ingest_file(engine, seed, seed.with_name("catalog_seed_v1.provenance.json"))
            if outcome.result is None or outcome.result.inserted != SEED_PRODUCTS:
                raise BenchmarkError("the committed seed did not ingest as 240 products")
            spec = settings.embedding_spec()
            provider = SentenceTransformerEmbedder(
                spec, settings.resolved_models_dir(), settings.embedding_batch_size
            )
            provider.load()
            first = embed(engine, provider)
            with Session(engine) as session:
                status = embedding_status(session, spec, provider)
            if first.inserted != SEED_PRODUCTS or not status.current:
                raise BenchmarkError("embeddings are not CURRENT for all 240 products")
            del provider
            import torch

            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("ANALYZE"))
                datasets = [
                    dict(row)
                    for row in conn.execute(
                        text(
                            "SELECT dataset_id, dataset_version, checksum_sha256, record_count, "
                            "transform_version, taxonomy_version, rules_version "
                            "FROM catalog_datasets ORDER BY id"
                        )
                    ).mappings()
                ]
                counts = {
                    "product_count": conn.execute(
                        text("SELECT count(*) FROM products")
                    ).scalar_one(),
                    "embedding_rows": conn.execute(
                        text("SELECT count(*) FROM product_embeddings")
                    ).scalar_one(),
                }
            engine.dispose()
            engine = None
            order = arm_order(args.run)
            emit(
                {
                    "record": "run",
                    "arm_order": list(order),
                    "catalog_datasets": datasets,
                    **counts,
                    "migration_head": head,
                    "setup_resources": resources(process),
                    "torch_num_threads": torch.get_num_threads(),
                    "libraries": library_versions(),
                }
            )
            base = settings.model_copy(update={"postgres_db": name})
            for position, arm in enumerate(order):
                arm_cfg = base.model_copy(update=dict(ARMS[arm]))
                if (arm_cfg.reranker_enabled, arm_cfg.search_rerank_depth) != (
                    ARMS[arm]["reranker_enabled"],
                    ARMS[arm]["search_rerank_depth"],
                ):
                    raise BenchmarkError(f"arm {arm}: settings were not applied")
                app = create_app(arm_cfg)
                probe = RerankerProbe(
                    app.state.reranker, lambda app=app: app.state.engine.pool.checkedout()
                )
                app.state.reranker = probe
                common = {
                    "arm": arm,
                    "arm_position": position,
                    "arm_order": list(order),
                    "settings": arm_settings(arm),
                }
                emit({"record": "arm_start", **common, **resources(process)})
                with TestClient(app) as client:
                    collect_arm(
                        client,
                        arm,
                        position,
                        args.run,
                        queries,
                        models,
                        emit,
                        RerankedSearchResponse.model_validate,
                    )
                    checked_out = app.state.engine.pool.checkedout()
                    embedder_loaded = app.state.embedder is not None and app.state.embedder.loaded
                emit(
                    {
                        "record": "arm_end",
                        **common,
                        **resources(process),
                        "reranker_probe": probe.facts(),
                        "embedder_loaded": embedder_loaded,
                        "requests": len(request_plan(len(queries))),
                        "pool_checked_out_after": checked_out,
                    }
                )
                del app, probe, client
    finally:
        if engine is not None:
            engine.dispose()
        admin.dispose()
    Path(args.out).write_text("\n".join(dumps(r) for r in records) + "\n", encoding="utf-8")
    return 0


# ---- parent: orchestration -----------------------------------------------------------------------


def power_status() -> dict:  # pragma: no cover - Windows only
    """AC line status from GetSystemPowerStatus (read-only)."""
    if platform.system() != "Windows":
        return {"available": False, "reason": "not Windows"}

    class SystemPowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = SystemPowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return {"available": False, "reason": "GetSystemPowerStatus failed"}
    return {
        "available": True,
        "ac_line_status": int(status.ACLineStatus),
        "ac_online": status.ACLineStatus == 1,
        "battery_percent": int(status.BatteryLifePercent),
    }


def database_exists(admin, name: str) -> bool:  # pragma: no cover - reads the server
    from sqlalchemy import text

    with admin.connect() as conn:
        found = conn.execute(
            text("SELECT count(*) FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar_one()
    return bool(found)


def local_child_args(local: LocalSnapshot | None, binding_hash: str | None) -> list[str]:
    """The extra child arguments of a local-snapshot run (none in the M8 mode)."""
    if local is None:
        return []
    argv = ["--local-snapshot", "--binding-sha256", str(binding_hash)]
    for path, blob in sorted(local.accepted.items()):
        argv += ["--accept-guarded-change", f"{path}={blob}"]
    return argv


def run_preflight(local: LocalSnapshot | None, output_dir: Path | None) -> int:
    """Print every identity a run would be bound to; no database, no model load, no writes."""
    from ecommerce_search.config import get_settings

    install_network_guard()
    os.environ.update(OFFLINE_ENV)
    checked = preflight(get_settings(), local)
    provenance = checked["provenance"]
    if local is None:
        out_dir = check_output_dir(ROOT, OUTPUT_DIR)
        check_fresh_output(out_dir)
        experiment_id = experiment_id_for(provenance["harness_head"])
    else:
        out_dir = check_local_output_dir(ROOT, output_dir)  # type: ignore[arg-type]
        experiment_id = local_experiment_id(provenance["binding_sha256"])
        for diff in provenance["guarded_differences"]:
            print(
                f"guarded difference {diff['path']}: production {diff['production_blob']}, "
                f"HEAD {diff['head_blob']}, working tree {diff['working_blob']} "
                f"(committed since production: {diff['committed_since_production']}, "
                f"uncommitted: {diff['uncommitted']})"
            )
        print(f"source_tree_sha256 {provenance['source_tree_sha256']}")
        print(
            f"uncommitted paths {provenance['uncommitted_paths']} "
            f"({provenance['uncommitted_paths_outside_source']} outside the fingerprinted source)"
        )
        print(f"binding_sha256 {provenance['binding_sha256']}")
    print(f"harness HEAD {provenance['harness_head']}")
    print(f"production commit {provenance['production_commit']}")
    print(f"experiment id {experiment_id}")
    print(f"output directory {out_dir.relative_to(ROOT).as_posix()} (free)")
    print("preflight OK: no database, no model load, nothing written")
    return 0


def parent_main(
    local: LocalSnapshot | None = None, output_dir: Path | None = None
) -> int:  # pragma: no cover - runs the benchmark
    from sqlalchemy import create_engine

    from ecommerce_search.config import get_settings
    from ecommerce_search.embeddings.text import EMBEDDING_TEXT_VERSION
    from ecommerce_search.reranking.documents import RERANK_DOCUMENT_VERSION

    install_network_guard()
    os.environ.update(OFFLINE_ENV)
    settings = get_settings()
    checked = preflight(settings, local)
    provenance = checked["provenance"]
    if local is None:
        out_dir = check_output_dir(ROOT, OUTPUT_DIR)
        experiment_id = experiment_id_for(provenance["harness_head"])
    else:
        out_dir = check_local_output_dir(ROOT, output_dir)  # type: ignore[arg-type]
        experiment_id = local_experiment_id(provenance["binding_sha256"])
    paths = artifact_paths(check_fresh_output(out_dir), experiment_id)
    power_start = power_status()
    if power_start.get("available") and not power_start.get("ac_online"):
        raise BenchmarkError("the host is not on AC power")
    started = datetime.now(UTC)
    header = {
        "experiment_id": experiment_id,
        "generated_at_utc": started.isoformat(timespec="seconds"),
        "provenance": provenance,
        "source": source_fingerprint(ROOT),
        "models": checked["models"],
        "catalog": committed_catalog_identity(),
        "embedding_text_version": EMBEDDING_TEXT_VERSION,
        "embedding_corpus_digest": embedding_corpus_digest(),
        "rerank_document_version": RERANK_DOCUMENT_VERSION,
        "query_identity": checked["identity"],
        "protocol": PREDECLARED,
        "base_settings": checked["settings"],
        "arm_settings": {arm: arm_settings(arm) for arm in ARMS},
        "scope": SCOPE,
        "nullability_rules": NULLABILITY_RULES,
        "metric_definitions": METRIC_DEFINITIONS,
        "environment": server_environment(settings),
        "libraries": library_versions(),
    }
    records: list[dict] = []
    cleanup: list[dict] = []
    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with tempfile.TemporaryDirectory(prefix="rerank-bench-") as tmp:
            for run in range(1, RUNS + 1):
                name = assert_scratch_name(new_scratch_name(), settings.postgres_db)
                out = Path(tmp) / f"run{run}.jsonl"
                print(f"run {run}/{RUNS} arms {list(arm_order(run))} ...", flush=True)
                argv = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child",
                    "--run",
                    str(run),
                    "--database",
                    name,
                    "--experiment-id",
                    experiment_id,
                    "--out",
                    str(out),
                    "--warmup",
                    str(WARMUP_PER_QUERY),
                    "--samples",
                    str(TIMED_PER_QUERY),
                    *local_child_args(local, provenance.get("binding_sha256")),
                ]
                run_started = time.perf_counter()
                with dropped_after(admin, name, settings.postgres_db):
                    subprocess.run(  # noqa: S603 - fixed argv, sys.executable
                        argv, cwd=ROOT, check=True, env={**os.environ, **OFFLINE_ENV}
                    )
                cleanup.append(
                    {
                        "run": run,
                        "scratch_database_dropped": not database_exists(admin, name),
                        "run_seconds": round(time.perf_counter() - run_started, 3),
                    }
                )
                records += [
                    json.loads(line)
                    for line in out.read_text(encoding="utf-8").splitlines()
                    if line
                ]
    finally:
        admin.dispose()
    ended = datetime.now(UTC)
    check_fresh_output(out_dir).mkdir(parents=True, exist_ok=True)
    raw_sha = write_raw(paths, header, records)
    extra = {
        "window_utc": [started.isoformat(timespec="seconds"), ended.isoformat(timespec="seconds")],
        "power_status": {"start": power_start, "end": power_status()},
        "power_events": power_events(started, ended),
        "cleanup": cleanup,
    }
    json_path = write_summary(paths, header, records, raw_sha, extra)
    problems = verify_artifacts(json_path, checked["models"])
    if problems:
        raise BenchmarkError("artifact self-verification failed: " + "; ".join(problems))
    print(f"wrote {json_path.relative_to(ROOT).as_posix()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute", action="store_true", help="run the benchmark (needs approval)")
    mode.add_argument("--verify", type=Path, help="recompute a summary from its raw records")
    mode.add_argument(
        "--print-protocol", action="store_true", help="print the predeclared protocol"
    )
    mode.add_argument(
        "--preflight", action="store_true", help="check and print the run identity; no run"
    )
    mode.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--local-snapshot", action="store_true", help="M10 local-snapshot mode (see the docstring)"
    )
    parser.add_argument(
        "--accept-guarded-change",
        action="append",
        metavar="PATH=BLOB",
        help="declare one guarded-path difference (local-snapshot mode only)",
    )
    parser.add_argument("--binding-sha256", help="the reviewed local-snapshot binding hash")
    parser.add_argument("--output-dir", type=Path, help="new output directory (local mode only)")
    parser.add_argument("--run", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--database", help=argparse.SUPPRESS)
    parser.add_argument("--experiment-id", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    parser.add_argument("--warmup", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--samples", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def check_mode_args(args: argparse.Namespace) -> None:
    """Local-snapshot options exist only in the local-snapshot mode; the M8 mode is unchanged."""
    local_options = (args.accept_guarded_change, args.binding_sha256, args.output_dir)
    if not args.local_snapshot:
        if any(option is not None for option in local_options):
            raise BenchmarkError("local-snapshot options require --local-snapshot")
        return
    if not (args.execute or args.preflight or args.child):
        raise BenchmarkError("--local-snapshot applies only to --preflight and --execute")
    if args.execute and (args.binding_sha256 is None or args.output_dir is None):
        raise BenchmarkError("--execute --local-snapshot needs --binding-sha256 and --output-dir")
    if args.preflight and args.output_dir is None:
        raise BenchmarkError("--preflight --local-snapshot needs --output-dir")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    check_mode_args(args)
    if args.print_protocol:
        print(protocol_text())
        return 0
    if args.verify:
        problems = verify_artifacts(args.verify)
        print("\n".join(problems) or "OK: summary recomputes exactly from raw records")
        return 1 if problems else 0
    if args.child:
        return child_main(args)
    if args.preflight:
        return run_preflight(local_from_args(args), args.output_dir)
    return parent_main(local_from_args(args), args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
