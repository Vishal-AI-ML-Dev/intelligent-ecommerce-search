"""Machine-only finalization of `weak_dev_v1`: a deterministic machine weak-label development set.

WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit coverage:
0/568 (0%). Two started human audit decisions were abandoned, archived and excluded. These
results are provisional development diagnostics only. They are not Golden Dataset results or
final held-out test evidence.

Pipeline (no human audit, no model inference):

    568 verified weak-label proposals
    -> `labels.jsonl` (`MachineWeakLabel`, every record `machine_unreviewed`, `human_audited`
       false, authored by `weak-label-policy-1`)
    -> provisional V0–V3 metrics (`eval-1` definitions, top-20 lists from the verified private
       pool provenance)
    -> a provisional development report

`assemble-machine-only` requires `--acknowledge-unaudited`. It refuses when:
- an audit lock or any audit decision is active;
- the audit root points into the abandoned-audit archive;
- the archive metadata is missing or does not record exactly two excluded decisions;
- the proposals, pool, cards or private signal binding do not verify;
- any strict judgement, seal or consistency file exists;
- the outputs already exist;
- two fresh-process builds are not byte-identical.

The archived decisions are never read: only the archive's metadata record is checked.

Public outputs (`evals/weaksets/internal-search/v1/machine-only/`) are deterministic. The
operational record (time of assembly) is written to the git-ignored private signals directory.

Search versions are the pooled systems V0 `v0_lexical`, V1 `v1_hybrid`, V2 `v2_filtered` and
V3 `v3_reranked_d20` (the configured default `search_rerank_depth=20`). The pooling-only
sources `dense_only` and `v3_reranked_d10` are not reported.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from evals.contracts.canonical import (
    decode_json_object,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import ROOT, CatalogIndex
from evals.contracts.constants import LABEL_ORDER, Label, Relation
from evals.contracts.models import SourceSystem
from evals.metrics.retrieval import EVALUATOR_VERSION, MacroValue, QueryMetrics
from evals.runners import build_pool
from evals.runners.golden_io import read_frozen, write_exclusive
from evals.weak_labels.audit import AUDIT_ROOT, DECISIONS_FILE, LOCK_FILE
from evals.weak_labels.contracts import (
    ABANDONED_DISCLOSURE,
    ABANDONED_HUMAN_DECISION_COUNT,
    CONFIDENCE_ORDER,
    LABEL_BASIS,
    MACHINE_DISCLOSURES,
    MACHINE_STATUS,
    MACHINE_WARNING,
    POLICY_VERSION,
    MachineDevSealed,
    MachineWeakLabel,
    NamedHash,
    ProposalRule,
    VersionSystem,
)
from evals.weak_labels.generation import (
    DEFAULT_WEAK_DIR,
    PRIVATE_ROOT,
    WEAKSET_ROOT,
    FrozenFacts,
    Generation,
    RerankerPin,
    WeakLabelError,
    load_facts,
    safe_weak_dir,
    signals_path,
    verify_generation,
)
from evals.weak_labels.reports import REPORTED_METRICS, system_metrics
from evals.weak_labels.sampling import proposal_sha256

MACHINE_SUBDIR = "machine-only"
LABELS_FILE = "labels.jsonl"
MANIFEST_FILE = "manifest.json"
SUMMARY_FILE = "proposal-summary.json"
COMPARISON_FILE = "metrics/comparison.json"
REPORT_FILE = "reports/provisional-v0-v3.md"
VERSION_SYSTEMS: dict[str, SourceSystem] = {
    "v0": SourceSystem.V0_LEXICAL,
    "v1": SourceSystem.V1_HYBRID,
    "v2": SourceSystem.V2_FILTERED,
    "v3": SourceSystem.V3_RERANKED_D20,
}
METRIC_FILES: dict[str, str] = {v: f"metrics/{v}.json" for v in VERSION_SYSTEMS}
CONTENT_FILES: tuple[str, ...] = (
    LABELS_FILE,
    SUMMARY_FILE,
    *METRIC_FILES.values(),
    COMPARISON_FILE,
    REPORT_FILE,
)
DETERMINISTIC_FILES: tuple[str, ...] = (*CONTENT_FILES, MANIFEST_FILE)
ARCHIVE_ROOT = ROOT / "data" / "processed" / "weak_audit_archive"
ARCHIVE_METADATA = ARCHIVE_ROOT / "abandoned-2026-10-05-9dc1fab28fbd89e9.archive.json"
STRICT_WORK_ROOT = ROOT / "data" / "processed" / "golden_annotation"
STRICT_MANIFESTS = frozenset({"1-queries_frozen.json", "2-pool_frozen.json"})
STRICT_FORBIDDEN_NAMES = (
    "primary_judgements",
    "primary_sealed",
    "consistency",
    "adjudication",
    "final_judgement",
    "labels_frozen",
    "provenance_unsealed",
)
OPS_FILE = "machine-only-run.json"
DETERMINISTIC_RULE_DEFINITION = (
    "Every label and relation comes from the deterministic catalog/gold-constraint rules. A "
    "record is counted as model-assisted when the local cross-encoder and retrieval-consensus "
    "signals disagreed with the rule outcome and lowered its heuristic confidence "
    "(signal_conflict); otherwise it is deterministic-rule only."
)
HEURISTIC_LIMITATIONS: tuple[str, ...] = (
    "Labels are produced by fixed heuristics (weak-label-policy-1) over catalog and "
    "gold-constraint facts; no label was checked by a human and no accuracy is known.",
    "Semantic intents (gaming, office), the low-price preference, near-miss substitutes and "
    "the headphones-as-complement mapping are hand-chosen rules, not learned or validated.",
    "Heuristic confidence is a rule-based ordering, not a calibrated probability.",
    "The cross-encoder (the provisional M8 compatibility pin, ADR-007) and retrieval consensus "
    "only lower confidence; labels can still be wrong where the facts are incomplete.",
    "The labels partly reuse the systems being evaluated (their consensus affects confidence), "
    "and gold constraints mirror what V2 filters on, so V2/V3 may be favoured.",
)
POOLED_RECALL_LIMITATIONS: tuple[str, ...] = (
    "Pooled evaluation: only the frozen pool (union of the six systems' top 20 plus "
    "eligibility completion and 4 negatives, at most 60 per query) is labelled.",
    "Recall is pooled recall. For gq-p01, gq-p11, gq-p13 and gq-p15 more than 25 products are "
    "eligible, completion did not run, and pooled recall is biased upward.",
    "Every evaluated result list is inside the pool, so unjudged@K is 0 by construction; this "
    "does not mean the catalog was exhaustively judged.",
    "20 pilot development queries only (3 Hinglish, 2 noisy); not a held-out test partition.",
)
STRICT_STATUS: tuple[str, ...] = (
    "Strict M9 remains paused: 0/568 primary judgements, no primary seal, no 72-hour "
    "consistency clock, no labels freeze.",
    "The strict human-reviewed Golden Dataset is incomplete, and final M10 evidence is incomplete.",
)


class MachineOnlyError(WeakLabelError):
    """Machine-only finalization cannot proceed; nothing was written."""


@dataclass(frozen=True)
class MachinePaths:
    golden_dir: Path = build_pool.DEFAULT_GOLDEN_DIR
    golden_root: Path = build_pool.ALLOWED_ROOT
    pool_private: Path = build_pool.PRIVATE_ROOT
    weak_dir: Path = DEFAULT_WEAK_DIR
    weak_root: Path = WEAKSET_ROOT
    signals_root: Path = PRIVATE_ROOT
    audit_root: Path = AUDIT_ROOT
    archive_root: Path = ARCHIVE_ROOT
    archive_metadata: Path = ARCHIVE_METADATA
    strict_work_root: Path = STRICT_WORK_ROOT

    @property
    def machine_dir(self) -> Path:
        return self.weak_dir / MACHINE_SUBDIR


# ---- refusals ----------------------------------------------------------------------------------


def check_no_active_audit(paths: MachinePaths) -> None:
    root = paths.audit_root.resolve()
    archive = paths.archive_root.resolve()
    if root == archive or root.is_relative_to(archive):
        raise MachineOnlyError("archived audit decisions are never an input; refusing")
    if not paths.audit_root.is_dir():
        return
    if any(paths.audit_root.rglob(LOCK_FILE)):
        raise MachineOnlyError("an active audit lock exists; finish or check the audit first")
    for path in paths.audit_root.rglob(DECISIONS_FILE):
        if path.read_bytes().strip():
            raise MachineOnlyError(
                "active human audit decisions exist; the machine-only path needs zero"
            )


def check_strict_untouched(paths: MachinePaths) -> None:
    manifests = paths.golden_dir / "manifests"
    names = {p.name for p in manifests.iterdir()} if manifests.is_dir() else set()
    if names - STRICT_MANIFESTS:
        raise MachineOnlyError(f"strict stages beyond pool_frozen exist: {sorted(names)}")
    for directory in (paths.golden_dir, paths.strict_work_root):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if any(name in path.name for name in STRICT_FORBIDDEN_NAMES):
                raise MachineOnlyError(f"a strict judgement/seal file exists: {path.name}")


def check_archive_metadata(paths: MachinePaths) -> None:
    """The abandoned-audit archive is disclosed, so its metadata must exist and agree. Only
    the metadata is read; the archived decisions themselves are never opened."""
    if not paths.archive_metadata.is_file():
        raise MachineOnlyError("the abandoned-audit archive metadata is missing")
    try:
        record = json.loads(paths.archive_metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MachineOnlyError("the abandoned-audit archive metadata is unreadable") from None
    expected = {
        "status": "abandoned_partial_audit",
        "decision_count": ABANDONED_HUMAN_DECISION_COUNT,
        "included_in_machine_labels": False,
        "included_in_metrics": False,
        "included_in_human_audit_statistics": False,
    }
    if any(record.get(k) != v for k, v in expected.items()):
        raise MachineOnlyError("the archive metadata does not record two excluded decisions")


def load_verified(
    paths: MachinePaths, *, pin: RerankerPin | None = None, catalog: CatalogIndex | None = None
) -> tuple[FrozenFacts, Generation]:
    """Verified queries, pool, cards, provenance, proposals, queue and private signals."""
    facts = load_facts(
        paths.golden_dir,
        allowed_root=paths.golden_root,
        private_root=paths.pool_private,
        catalog=catalog,
    )
    generation = verify_generation(
        facts,
        paths.weak_dir,
        allowed_root=paths.weak_root,
        private_root=paths.signals_root,
        pin=pin,
    )
    return facts, generation


# ---- deterministic build -----------------------------------------------------------------------


def _metadata(facts: FrozenFacts, generation: Generation) -> dict:
    return {
        "warning": MACHINE_WARNING,
        "status": MACHINE_STATUS,
        "label_basis": LABEL_BASIS,
        "human_audit_coverage": 0,
        "abandoned_human_decision_count": ABANDONED_HUMAN_DECISION_COUNT,
        "abandoned_human_decisions_included": False,
        "provisional": True,
        "policy_version": POLICY_VERSION,
        "proposal_manifest_sha256": generation.manifest_sha256,
        "pool_manifest_sha256": facts.pool_manifest.manifest_sha256,
        "evaluator_version": EVALUATOR_VERSION,
        "disclosures": list(MACHINE_DISCLOSURES),
    }


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_labels(generation: Generation) -> list[MachineWeakLabel]:
    return [
        MachineWeakLabel(
            query_id=p.query_id,
            product_id=p.product_id,
            label=p.proposed_label,
            relation=p.proposed_relation,
            heuristic_confidence=p.proposal_confidence,
            proposal_rule=p.proposal_rule,
            requires_human_review=p.requires_human_review,
            signal_conflict=p.signal_conflict,
            proposal_sha256=proposal_sha256(p),
            proposal_manifest_sha256=generation.manifest_sha256,
            pool_manifest_sha256=p.pool_manifest_sha256,
            product_card_sha256=p.card_sha256,
            product_content_sha256=p.product_content_sha256,
            catalog_checksum_sha256=p.catalog_checksum_sha256,
            reranker_model_id=p.reranker_model_id,
            reranker_revision=p.reranker_revision,
            rerank_document_version=p.rerank_document_version,
            rubric_version=p.rubric_version,
        )
        for p in sorted(generation.proposals, key=lambda p: p.sort_key())
    ]


def _count(values: list[str], order: list[str]) -> dict[str, int]:
    return {key: values.count(key) for key in order}


def proposal_summary(labels: list[MachineWeakLabel], metadata: dict) -> dict:
    total = len(labels)
    confidence_order = [c.value for c in reversed(CONFIDENCE_ORDER)]
    confidences = [x.heuristic_confidence.value for x in labels]
    conflicts = sum(x.signal_conflict for x in labels)
    per_query: dict[str, dict[str, int]] = {}
    for x in labels:
        row = per_query.setdefault(x.query_id, {label.value: 0 for label in LABEL_ORDER})
        row[x.label.value] += 1
    return {
        **metadata,
        "total_machine_labels": total,
        "label_counts": _count([x.label.value for x in labels], [v.value for v in LABEL_ORDER]),
        "relation_counts": _count([x.relation.value for x in labels], [r.value for r in Relation]),
        "confidence_counts": _count(confidences, confidence_order),
        "unaudited_confidence_counts": _count(confidences, confidence_order),
        "rule_counts": _count(
            [x.proposal_rule.value for x in labels], [r.value for r in ProposalRule]
        ),
        "label_derivation": {
            "deterministic_rule_only": total - conflicts,
            "model_assisted_confidence": conflicts,
            "definition": DETERMINISTIC_RULE_DEFINITION,
        },
        "requires_human_review_count": sum(x.requires_human_review for x in labels),
        "machine_only_coverage": f"{total}/{total}",
        "human_audit_coverage_fraction": f"0/{total}",
        "abandoned_human_decisions": {
            "count": ABANDONED_HUMAN_DECISION_COUNT,
            "excluded": True,
            "disclosure": ABANDONED_DISCLOSURE,
        },
        "per_query_label_counts": dict(sorted(per_query.items())),
    }


def _metric_payload(per_query: list[QueryMetrics], aggregated: tuple[MacroValue, ...]) -> dict:
    return {
        "query_count": len(per_query),
        "macro": [v.model_dump(mode="json") for v in aggregated],
        "per_query": [q.model_dump(mode="json") for q in per_query],
    }


def build_machine_files(facts: FrozenFacts, generation: Generation) -> dict[str, bytes]:
    """Every deterministic machine-only file (pure: the same inputs give the same bytes)."""
    provenance = facts.provenance
    if provenance is None:
        raise MachineOnlyError("the verified private pool provenance is required")
    labels = build_labels(generation)
    if len(labels) != len(facts.pool):
        raise MachineOnlyError("the machine labels do not cover exactly the frozen pool")
    metadata = _metadata(facts, generation)
    by_query: dict[str, dict[str, Label]] = {}
    for x in labels:
        by_query.setdefault(x.query_id, {})[x.product_id] = x.label
    computed = system_metrics(provenance, by_query, facts.eligibility)
    files: dict[str, bytes] = {LABELS_FILE: encode_jsonl(labels)}
    summary = proposal_summary(labels, metadata)
    files[SUMMARY_FILE] = _dump(summary)
    metrics: dict[str, dict] = {}
    for version, system in VERSION_SYSTEMS.items():
        per_query, aggregated = computed[system.value]
        metrics[version] = {
            **metadata,
            "version": version,
            "system": system.value,
            **_metric_payload(per_query, aggregated),
        }
        files[METRIC_FILES[version]] = _dump(metrics[version])
    comparison = {**metadata, "versions": _versions(), "macro": _comparison(metrics)}
    files[COMPARISON_FILE] = _dump(comparison)
    files[REPORT_FILE] = report_markdown(summary, metrics, comparison).encode("utf-8")
    pin_source = generation.manifest
    manifest = MachineDevSealed(
        proposal_manifest_sha256=generation.manifest_sha256,
        pool_manifest_sha256=facts.pool_manifest.manifest_sha256,
        queries_manifest_sha256=facts.queries_manifest_sha256,
        provenance_binding=pin_source.provenance_binding,
        signals_binding=pin_source.signals_binding,
        catalog_checksum_sha256=facts.catalog.checksum_sha256,
        reranker_model_id=pin_source.reranker_model_id,
        reranker_revision=pin_source.reranker_revision,
        rerank_document_version=pin_source.rerank_document_version,
        rubric_version=facts.rubric_version,
        evaluator_version=EVALUATOR_VERSION,
        version_systems=tuple(
            VersionSystem(version=v, system=s.value)  # type: ignore[arg-type]
            for v, s in VERSION_SYSTEMS.items()
        ),
        label_count=len(labels),
        files=tuple(NamedHash(path=p, sha256=sha256_hex(files[p])) for p in CONTENT_FILES),
    )
    files[MANIFEST_FILE] = encode_json_object(manifest)
    return files


def _versions() -> list[dict[str, str]]:
    return [{"version": v, "system": s.value} for v, s in VERSION_SYSTEMS.items()]


def _comparison(metrics: Mapping[str, dict]) -> list[dict]:
    keys = [(m["metric"], m["k"], m["definition"]) for m in metrics["v0"]["macro"]]
    rows = []
    for metric, k, definition in keys:
        values = {}
        for version, data in metrics.items():
            value = next(
                m
                for m in data["macro"]
                if (m["metric"], m["k"], m["definition"]) == (metric, k, definition)
            )
            values[version] = {
                "value": value["value"],
                "n": value["n"],
                "excluded": value["excluded"],
            }
        rows.append({"metric": metric, "k": k, "definition": definition, "values": values})
    return rows


# ---- report ------------------------------------------------------------------------------------

PER_QUERY_COLUMNS: tuple[tuple[str, int | None, str, str], ...] = (
    ("precision", 5, "strict", "P@5 s"),
    ("precision", 10, "strict", "P@10 s"),
    ("precision", 10, "lenient", "P@10 l"),
    ("recall", 5, "strict", "R@5 s"),
    ("recall", 10, "strict", "R@10 s"),
    ("recall", 10, "lenient", "R@10 l"),
    ("reciprocal_rank", 10, "strict", "MRR@10 s"),
    ("ndcg", 5, "none", "NDCG@5"),
    ("ndcg", 10, "none", "NDCG@10"),
    ("csr", 10, "hard", "CSR@10 hard"),
    ("csr", 10, "fp1", "CSR@10 fp1"),
    ("irrelevant_result_rate", 10, "none", "Irrel@10"),
    ("zero_result", None, "none", "Zero"),
)


def _short(value: str | None) -> str:
    if value is None:
        return "—"
    return format(Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN), "f")


def _label(metric: str, k: int | None, definition: str) -> str:
    name = {"reciprocal_rank": "MRR", "irrelevant_result_rate": "irrelevant-result rate"}.get(
        metric, metric
    )
    text = f"{name}@{k}" if k is not None else name
    return text if definition == "none" else f"{text} ({definition})"


def report_markdown(summary: dict, metrics: Mapping[str, dict], comparison: dict) -> str:
    lines = [
        "# weak_dev_v1 — provisional V0–V3 development evaluation (machine-only)",
        "",
        f"> **{MACHINE_WARNING}**",
        "",
        "Deterministic machine weak-label development set: unaudited machine-generated labels "
        "(`weak-label-policy-1`), provisional development evaluation. Not a human-reviewed "
        "Golden Dataset; not final held-out test evidence.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| status | `{summary['status']}` |",
        f"| label_basis | `{summary['label_basis']}` |",
        f"| policy_version | `{summary['policy_version']}` |",
        f"| proposal manifest | `{summary['proposal_manifest_sha256']}` |",
        f"| pool manifest | `{summary['pool_manifest_sha256']}` |",
        f"| evaluator | `{summary['evaluator_version']}` |",
        "| provisional | true |",
        "",
        "## Disclosures",
        "",
        *[f"- {d}" for d in MACHINE_DISCLOSURES],
        "",
        "## Labels",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Total machine labels | {summary['total_machine_labels']} |",
        f"| Machine-only coverage | {summary['machine_only_coverage']} |",
        f"| Human audit coverage | {summary['human_audit_coverage_fraction']} |",
        f"| Abandoned human decisions | {summary['abandoned_human_decisions']['count']}, "
        "excluded |",
        f"| requires_human_review | {summary['requires_human_review_count']} |",
        f"| Deterministic-rule only | {summary['label_derivation']['deterministic_rule_only']} |",
        f"| Model-assisted (confidence lowered by local signals) | "
        f"{summary['label_derivation']['model_assisted_confidence']} |",
        "",
        DETERMINISTIC_RULE_DEFINITION,
        "",
    ]
    for title, key in (
        ("Label counts", "label_counts"),
        ("Relation counts", "relation_counts"),
        ("Unaudited heuristic-confidence counts", "unaudited_confidence_counts"),
    ):
        lines += [f"### {title}", "", "| Value | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in summary[key].items()]
        lines.append("")
    versions = list(metrics)
    lines += [
        "## Macro V0–V3 aggregates",
        "",
        "Macro means over the queries where the metric is defined (`n` = defined queries). "
        + "Versions: "
        + ", ".join(f"{v} = `{metrics[v]['system']}`" for v in versions)
        + ".",
        "",
        "| Metric | " + " | ".join(v.upper() for v in versions) + " |",
        "|---|" + "---|" * len(versions),
    ]
    rows = {(r["metric"], r["k"], r["definition"]): r for r in comparison["macro"]}
    for key in REPORTED_METRICS:
        row = rows[key]
        cells = [
            f"{_short(row['values'][v]['value'])} (n={row['values'][v]['n']})" for v in versions
        ]
        lines.append(f"| {_label(*key)} | " + " | ".join(cells) + " |")
    lines += ["", "## Per-query V0–V3 metrics", ""]
    header = "| Query | " + " | ".join(c[3] for c in PER_QUERY_COLUMNS) + " |"
    for version in versions:
        lines += [
            f"### {version.upper()} (`{metrics[version]['system']}`)",
            "",
            header,
            "|---|" + "---|" * len(PER_QUERY_COLUMNS),
        ]
        for q in metrics[version]["per_query"]:
            values = {(v["metric"], v["k"], v["definition"]): v["value"] for v in q["values"]}
            cells = [_short(values[(m, k, d)]) for m, k, d, _ in PER_QUERY_COLUMNS]
            lines.append(f"| {q['query_id']} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Heuristic limitations", "", *[f"- {x}" for x in HEURISTIC_LIMITATIONS], ""]
    lines += ["## Pooled-recall limitations", "", *[f"- {x}" for x in POOLED_RECALL_LIMITATIONS]]
    lines += ["", "## Strict Golden Dataset status", "", *[f"- {x}" for x in STRICT_STATUS], ""]
    lines += [f"> **{MACHINE_WARNING}**", ""]
    return "\n".join(lines)


# ---- running, storing and verifying ------------------------------------------------------------


def compare_runs(first: Mapping[str, bytes], second: Mapping[str, bytes]) -> None:
    for name in DETERMINISTIC_FILES:
        a, b = first.get(name), second.get(name)
        if a is None or b is None:
            raise MachineOnlyError(f"{name}: missing from a run; nothing written")
        if a != b:
            lines_a, lines_b = a.split(b"\n"), b.split(b"\n")
            line = next(
                (i for i, (x, y) in enumerate(zip(lines_a, lines_b, strict=False), 1) if x != y),
                min(len(lines_a), len(lines_b)) + 1,
            )
            raise MachineOnlyError(
                f"runs differ; nothing written. First difference: {name} line {line}"
            )


def child_main(out_dir: Path) -> int:  # pragma: no cover - a fresh process on the real paths
    facts, generation = load_verified(MachinePaths())
    for name, data in build_machine_files(facts, generation).items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return 0


def run_once(out_dir: Path) -> dict[str, bytes]:  # pragma: no cover - spawns a process
    argv = [sys.executable, "-m", "evals.weak_labels", "_machine_only", "--out", str(out_dir)]
    try:
        subprocess.run(argv, cwd=ROOT, check=True)  # noqa: S603 - fixed argv, sys.executable
    except subprocess.CalledProcessError as exc:
        message = f"a machine-only run failed (exit {exc.returncode}); nothing written"
        raise MachineOnlyError(message) from None
    return {name: (out_dir / name).read_bytes() for name in DETERMINISTIC_FILES}


def fresh_process_runs() -> list[dict[str, bytes]]:  # pragma: no cover - spawns processes
    with tempfile.TemporaryDirectory(prefix="weak-machine-runs-") as tmp:
        return [run_once(Path(tmp) / f"run{i}") for i in (1, 2)]


def preflight(paths: MachinePaths, *, acknowledge_unaudited: bool) -> None:
    if not acknowledge_unaudited:
        raise MachineOnlyError(
            "refused: --acknowledge-unaudited is required. Every label would be an unaudited "
            "machine-generated weak label (human audit coverage 0/568)."
        )
    check_no_active_audit(paths)
    check_strict_untouched(paths)
    check_archive_metadata(paths)
    if os.path.lexists(paths.machine_dir):
        raise MachineOnlyError("machine-only outputs already exist; they are never overwritten")


def assemble_machine_only(
    paths: MachinePaths,
    *,
    acknowledge_unaudited: bool,
    runs: Callable[[], list[dict[str, bytes]]] = fresh_process_runs,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    pin: RerankerPin | None = None,
    catalog: CatalogIndex | None = None,
) -> MachineDevSealed:
    preflight(paths, acknowledge_unaudited=acknowledge_unaudited)
    facts, generation = load_verified(paths, pin=pin, catalog=catalog)
    first, second = runs()
    compare_runs(first, second)
    compare_runs(first, build_machine_files(facts, generation))
    directory = safe_weak_dir(paths.weak_dir, paths.weak_root)
    write_exclusive(
        directory,
        {f"{MACHINE_SUBDIR}/{name}": first[name] for name in DETERMINISTIC_FILES},
        last=f"{MACHINE_SUBDIR}/{MANIFEST_FILE}",
    )
    manifest = decode_json_object(first[MANIFEST_FILE], MachineDevSealed)
    ops = signals_path(facts, paths.signals_root).with_name(OPS_FILE)
    record = {
        "status": MACHINE_STATUS,
        "assembled_at": now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "acknowledged_unaudited": True,
        "independent_fresh_process_runs": 2,
        "runs_byte_identical": True,
        "machine_manifest_sha256": sha256_hex(first[MANIFEST_FILE]),
        "proposal_manifest_sha256": generation.manifest_sha256,
    }
    with open(ops, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    verify_machine_only(paths, pin=pin, catalog=catalog)
    return manifest


def verify_machine_only(
    paths: MachinePaths, *, pin: RerankerPin | None = None, catalog: CatalogIndex | None = None
) -> MachineDevSealed:
    """Re-verify everything upstream and regenerate every machine-only file byte for byte."""
    facts, generation = load_verified(paths, pin=pin, catalog=catalog)
    directory = safe_weak_dir(paths.weak_dir, paths.weak_root) / MACHINE_SUBDIR
    stored_names = sorted(
        p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
    )
    if stored_names != sorted(DETERMINISTIC_FILES):
        raise MachineOnlyError(f"unexpected machine-only files: {stored_names}")
    manifest = decode_json_object(read_frozen(directory, MANIFEST_FILE), MachineDevSealed)
    for entry in manifest.files:
        if sha256_hex(read_frozen(directory, entry.path)) != entry.sha256:
            raise MachineOnlyError(f"{entry.path} does not match the machine-only manifest")
    expected = build_machine_files(facts, generation)
    for name in DETERMINISTIC_FILES:
        if read_frozen(directory, name) != expected[name]:
            raise MachineOnlyError(f"{name} does not regenerate byte for byte")
    return manifest
