"""Audit report and provisional V0–V3 metrics on the sealed `weak_dev_v1`. Pure, offline.

Both reports require a verified `weak_dev_sealed`. Every report carries the `BANNER` and the
disclosures, is written exclusively to `evals/weaksets/internal-search/v1/reports/` (never to
an M10 report path), and claims no accuracy for unaudited machine labels.

* `audit_report`: acceptance and override rates; agreement by proposed label and by proposal
  confidence (exact = label and relation kept; label = label kept); the proposed × final label
  confusion matrix on the audited pairs; per-query coverage; audited, unaudited and machine-only
  counts and rates; limitations.
* `provisional_metrics`: the six pooled systems (V0 lexical, dense-only, V1 hybrid, V2 filtered,
  V3 reranked at depth 10 and 20) re-scored from their ranked lists in the verified private pool
  provenance (top 20) with the `eval-1` per-query and macro definitions, strict and lenient,
  with hard and fp-1 CSR from the frozen eligibility.
"""

import json
from collections.abc import Mapping, Sequence
from fractions import Fraction

from evals.contracts.constants import LABEL_ORDER, Label
from evals.contracts.models import EligibilityRecord, PoolProvenance
from evals.metrics.retrieval import (
    EVALUATOR_VERSION,
    MacroValue,
    QueryMetrics,
    fmt,
    macro,
    query_metrics,
)
from evals.weak_labels.assembly import SealedWeakSet
from evals.weak_labels.audit import AuditContext
from evals.weak_labels.contracts import (
    BANNER,
    CONFIDENCE_ORDER,
    DISCLOSURES,
    LabelSource,
    WeakFinalLabel,
)
from evals.weak_labels.generation import WeakLabelError

REPORT_VERSION = "weak-report-1"
LIMITATIONS: tuple[str, ...] = (
    "The audit is non-blind: the auditor saw each proposal, so agreement is biased upward "
    "(anchoring) and is not an accuracy estimate.",
    "The 100 audit pairs are a stratified, not a uniform, sample (five per query, by proposed "
    "label and lowest confidence); rates do not extrapolate to the 468 unaudited pairs.",
    "Unaudited labels are unreviewed machine proposals; no accuracy is claimed for them.",
    "One human auditor (ann-1); no inter-annotator agreement and no delayed intra-annotator "
    "consistency measurement.",
    "Pooled evaluation over a frozen pool of at most 60 pairs per query; pooled recall is "
    "biased for queries with more than 25 eligible products.",
    "The 20 queries are pilot development/rubric-calibration queries, not a held-out test set.",
    "Auditing proposals exposes ann-1 to machine labels for these pairs; any later strict "
    "blind annotation of the same pool is no longer fully blind for them.",
)
REPORTED_METRICS: tuple[tuple[str, int | None, str], ...] = (
    ("precision", 5, "strict"),
    ("precision", 10, "strict"),
    ("precision", 5, "lenient"),
    ("precision", 10, "lenient"),
    ("recall", 5, "strict"),
    ("recall", 10, "strict"),
    ("recall", 5, "lenient"),
    ("recall", 10, "lenient"),
    ("reciprocal_rank", 10, "strict"),
    ("reciprocal_rank", 10, "lenient"),
    ("ndcg", 5, "none"),
    ("ndcg", 10, "none"),
    ("csr", 10, "hard"),
    ("csr", 10, "fp1"),
    ("irrelevant_result_rate", 10, "none"),
    ("zero_result", None, "none"),
)


def _rate(part: int, whole: int) -> str | None:
    return fmt(Fraction(part, whole)) if whole else None


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _header(title: str) -> list[str]:
    return [f"# {title}", "", f"> **{BANNER}**", "", "## Disclosures", ""] + [
        f"- {d}" for d in DISCLOSURES
    ]


# ---- audit report ------------------------------------------------------------------------------


def audit_report(context: AuditContext, weak: SealedWeakSet) -> dict:
    audited = [f for f in weak.finals if f.label_source is not LabelSource.MACHINE_UNREVIEWED]
    total, n = len(weak.finals), len(audited)
    accepted = sum(f.label_source is LabelSource.HUMAN_ACCEPTED_PROPOSAL for f in audited)

    def agreement(group: Sequence[WeakFinalLabel]) -> dict:
        exact = sum(f.label_source is LabelSource.HUMAN_ACCEPTED_PROPOSAL for f in group)
        same_label = sum(f.label is f.proposed_label for f in group)
        return {
            "audited": len(group),
            "exact_agreement": exact,
            "exact_agreement_rate": _rate(exact, len(group)),
            "label_agreement": same_label,
            "label_agreement_rate": _rate(same_label, len(group)),
        }

    by_label = {
        label.value: agreement([f for f in audited if f.proposed_label is label])
        for label in LABEL_ORDER
    }
    by_confidence = {
        c.value: agreement([f for f in audited if f.proposal_confidence is c])
        for c in reversed(CONFIDENCE_ORDER)
    }
    confusion = {
        p.value: {
            a.value: sum(f.proposed_label is p and f.label is a for f in audited)
            for a in LABEL_ORDER
        }
        for p in LABEL_ORDER
    }
    per_query = []
    for query in sorted(context.facts.queries, key=lambda q: q.query_id):
        own = [f for f in weak.finals if f.query_id == query.query_id]
        own_audited = [f for f in own if f.label_source is not LabelSource.MACHINE_UNREVIEWED]
        per_query.append(
            {
                "query_id": query.query_id,
                "intent_family": query.intent_family.value,
                "language_style": query.language_style.value,
                "pool_size": len(own),
                "audited": len(own_audited),
                "accepted": sum(
                    f.label_source is LabelSource.HUMAN_ACCEPTED_PROPOSAL for f in own_audited
                ),
                "overridden": sum(
                    f.label_source is LabelSource.HUMAN_OVERRIDE for f in own_audited
                ),
                "machine_unreviewed": len(own) - len(own_audited),
            }
        )
    machine_only = total - n
    return {
        "report_version": REPORT_VERSION,
        "banner": BANNER,
        "disclosures": list(DISCLOSURES),
        "weak_set_id": weak.sealed.weak_set_id,
        "proposal_manifest_sha256": weak.sealed.proposal_manifest_sha256,
        "pool_manifest_sha256": weak.sealed.pool_manifest_sha256,
        "label_count": total,
        "audited_count": n,
        "unaudited_count": machine_only,
        "accepted_count": accepted,
        "override_count": n - accepted,
        "accept_rate": _rate(accepted, n),
        "override_rate": _rate(n - accepted, n),
        "machine_only_label_count": machine_only,
        "machine_only_label_rate": _rate(machine_only, total),
        "agreement_by_proposed_label": by_label,
        "agreement_by_proposal_confidence": by_confidence,
        "confusion_matrix_proposed_by_final": confusion,
        "per_query": per_query,
        "limitations": list(LIMITATIONS),
    }


def audit_report_markdown(report: dict) -> str:
    lines = _header("weak_dev_v1 audit report")
    lines += [
        "",
        "## Counts",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Labels | {report['label_count']} |",
        f"| Audited (human) | {report['audited_count']} |",
        f"| Unaudited (machine only) | {report['unaudited_count']} |",
        f"| Machine-only label rate | {report['machine_only_label_rate']} |",
        f"| Accepted | {report['accepted_count']} (rate {report['accept_rate']}) |",
        f"| Overridden | {report['override_count']} (rate {report['override_rate']}) |",
        "",
        "## Agreement by proposed label",
        "",
        "| Proposed | Audited | Exact agreement | Label agreement |",
        "|---|---|---|---|",
    ]
    for key, row in report["agreement_by_proposed_label"].items():
        lines.append(
            f"| {key} | {row['audited']} | {row['exact_agreement_rate']} | "
            f"{row['label_agreement_rate']} |"
        )
    lines += [
        "",
        "## Agreement by proposal confidence",
        "",
        "| Confidence | Audited | Exact agreement | Label agreement |",
        "|---|---|---|---|",
    ]
    for key, row in report["agreement_by_proposal_confidence"].items():
        lines.append(
            f"| {key} | {row['audited']} | {row['exact_agreement_rate']} | "
            f"{row['label_agreement_rate']} |"
        )
    labels = [label.value for label in LABEL_ORDER]
    lines += [
        "",
        "## Confusion matrix (rows: proposed, columns: final, audited pairs only)",
        "",
        "| Proposed \\ Final | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for p in labels:
        row = report["confusion_matrix_proposed_by_final"][p]
        lines.append(f"| {p} | " + " | ".join(str(row[a]) for a in labels) + " |")
    lines += [
        "",
        "## Per-query coverage",
        "",
        "| Query | Family | Style | Pool | Audited | Accepted | Overridden | Machine only |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in report["per_query"]:
        lines.append(
            f"| {q['query_id']} | {q['intent_family']} | {q['language_style']} | "
            f"{q['pool_size']} | {q['audited']} | {q['accepted']} | {q['overridden']} | "
            f"{q['machine_unreviewed']} |"
        )
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


# ---- provisional metrics -----------------------------------------------------------------------


def system_metrics(
    provenance: PoolProvenance,
    labels: Mapping[str, Mapping[str, Label]],
    eligibility: Mapping[tuple[str, str], EligibilityRecord],
) -> dict[str, tuple[list[QueryMetrics], tuple[MacroValue, ...]]]:
    """`eval-1` per-query and macro metrics of every pooled system's top-20 list in the
    verified provenance, against the given labels (pure)."""

    def lookup(query_id: str, product_id: str) -> EligibilityRecord:
        return eligibility[(query_id, product_id)]

    runs: dict[str, dict[str, tuple[str, ...]]] = {}
    for run in provenance.source_runs:
        runs.setdefault(run.system.value, {})[run.query_id] = run.product_ids
    out = {}
    for system, ranked in runs.items():
        if set(ranked) != set(labels):
            raise WeakLabelError(f"{system}: the run and the labels cover different queries")
        per_query = [query_metrics(q, list(ranked[q]), labels[q], lookup) for q in sorted(labels)]
        out[system] = (per_query, macro(per_query))
    return out


def provisional_metrics(context: AuditContext, weak: SealedWeakSet) -> dict:
    provenance = context.facts.provenance
    if provenance is None:
        raise WeakLabelError("the verified private pool provenance is required for metrics")
    labels: dict[str, dict[str, Label]] = {}
    for f in weak.finals:
        labels.setdefault(f.query_id, {})[f.product_id] = f.label
    systems = [
        {
            "system": system,
            "macro": [v.model_dump(mode="json") for v in aggregated],
            "per_query": [q.model_dump(mode="json") for q in per_query],
        }
        for system, (per_query, aggregated) in system_metrics(
            provenance, labels, context.facts.eligibility
        ).items()
    ]
    sources = [f.label_source.value for f in weak.finals]
    return {
        "report_version": REPORT_VERSION,
        "banner": BANNER,
        "disclosures": list(DISCLOSURES),
        "evaluator_version": EVALUATOR_VERSION,
        "weak_set_id": weak.sealed.weak_set_id,
        "proposal_manifest_sha256": weak.sealed.proposal_manifest_sha256,
        "pool_manifest_sha256": weak.sealed.pool_manifest_sha256,
        "label_sources": {s.value: sources.count(s.value) for s in LabelSource},
        "query_count": len(labels),
        "systems": systems,
        "limitations": list(LIMITATIONS),
    }


def _macro(system: dict, metric: str, k: int | None, definition: str) -> dict:
    for value in system["macro"]:
        if (value["metric"], value["k"], value["definition"]) == (metric, k, definition):
            return value
    raise KeyError((metric, k, definition))


def metrics_markdown(report: dict) -> str:
    lines = _header("weak_dev_v1 provisional V0–V3 metrics")
    sources = report["label_sources"]
    lines += [
        "",
        f"Evaluator `{report['evaluator_version']}`; {report['query_count']} queries; label "
        f"sources: {sources}. Values are macro means over the queries where defined "
        "(`n` defined / `excluded` undefined).",
        "",
        "## Macro results",
        "",
    ]
    names = [s["system"] for s in report["systems"]]
    lines.append("| Metric | " + " | ".join(names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for metric, k, definition in REPORTED_METRICS:
        label = f"{metric}@{k}" if k is not None else metric
        if definition != "none":
            label += f" ({definition})"
        cells = []
        for system in report["systems"]:
            value = _macro(system, metric, k, definition)
            cells.append(f"{value['value']} (n={value['n']})")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Per-query values are in the JSON report.", "", "## Limitations", ""]
    lines += [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def report_files(prefix: str, data: dict, markdown: str) -> dict[str, bytes]:
    return {
        f"reports/{prefix}.json": _dump(data),
        f"reports/{prefix}.md": markdown.encode("utf-8"),
    }
