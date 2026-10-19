"""Offline diagnostic evaluator for the M12 batch: deterministic `qu-1` vs Jev `jq-1` decisions.

Inputs are the sealed queries and labels and one run's journal. The deterministic decision is
recomputed at evaluation time from the sealed query text (`parse_normalized_query`,
network-free); it is not recorded during the run. The report records the parser and lexicon
version labels, which are declared constants, not proof that the parser source is unchanged.
No call is made.
Counts only: no rates with intervals, no thresholds, no calibration, no retrieval. The report is
development diagnostic evidence, never held-out evidence; a dry run is marked synthetic.

Predictions:

* deterministic: the parsed `category` / `semantic_intent`, or abstain (`None`, including a
  conflict). The parser never outputs `none`.
* Jev: the unique top label (which may be `none`, an explicit decision), or abstain, split into
  `tie` (no unique top label), `fallback` (a recorded fallback reason), `unknown` (dispatched,
  no recorded outcome) and `not_attempted`.

Scoring of one decided prediction against the gold label (`score`):

| gold | prediction | outcome |
|---|---|---|
| unclear | any | `excluded_unclear` |
| any other | abstain | `abstain` |
| label L (not none) | L / none / other | `correct` / `predicted_none` / `wrong` |
| none | none / a label | `correct_none` / `wrong_commit` |
| ambiguous {A, B, ...} | none / in set / outside | `none_on_ambiguous` / `commit_in_candidates` / |
| | | `commit_outside_candidates` |
| multi {A, B, ...} | none / in set / outside | `predicted_none` / `in_set` / `out_of_set` |

For gold `none`, a deterministic abstention means no constraint is applied, which is the right
search outcome; it is still counted as `abstain`, not as a decision.

`predicted_none` is a missed decision (a single or multi gold where `none` was predicted);
`none_on_ambiguous` is `none` on an ambiguous gold, where no label is committed. They are kept
apart (`qd-diagnostic-2`; `qd-diagnostic-1` reported both as `predicted_none`).

Reported per question: gold kinds; deterministic and Jev outcome counts; Jev abstention reasons;
D/J disagreements (both decided, different labels) with the outcome pair; and Jev's outcome on
the queries where the deterministic parser abstains. `deterministic_abstains_total` counts every
such query; `deterministic_abstains_scored` excludes unclear golds and is the denominator of
`jev_on_deterministic_abstains` and the fills (`correct` and `in_set` are correct fills; `wrong`,
`wrong_commit`, `commit_*` and `out_of_set` are incorrect fills).
Deterministic-first gating could only change those abstaining queries; it cannot correct a wrong
deterministic decision.

Category-only batches (`qd-cat-1` labels): intent was not annotated. Each intent cell has gold
kind `not_annotated` and outcome `excluded_not_annotated` for both predictors; it is never
scored as `none` or `unclear` and is left out of the fills. The report then also carries
`annotation_mode: category_only`; for full-label batches the report is unchanged.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from ecommerce_search.decision.provider import CategoryProposal, IntentProposal, JevOutcome
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.query_understanding.models import QueryUnderstanding
from evals.contracts.canonical import decode_json_object
from evals.query_decisions.contracts import (
    NONE_LABEL,
    AnnotationMode,
    AnyLabel,
    AnySeal,
    CategoryGold,
    DecisionQuery,
    GoldKind,
    IntentGold,
    QueryDecisionError,
)
from evals.query_decisions.labeling import load_sealed
from evals.query_decisions.runner import (
    JOURNAL_FILE,
    RUN_FILE,
    RUNS_DIR,
    RunHeader,
    RunMode,
    read_journal,
)
from evals.runners.golden_io import safe_child

QUESTIONS = ("category", "semantic_intent")
CORRECT_FILLS = frozenset({"correct", "in_set"})
INCORRECT_FILLS = frozenset(
    {"wrong", "wrong_commit", "commit_in_candidates", "commit_outside_candidates", "out_of_set"}
)
ABSTAIN = "abstain"
NOT_ANNOTATED = "not_annotated"
EXCLUDED_NOT_ANNOTATED = "excluded_not_annotated"
EXCLUDED = frozenset({"excluded_unclear", EXCLUDED_NOT_ANNOTATED})


def score(gold: CategoryGold | IntentGold, prediction: str | None) -> str:
    """Outcome of one prediction (a label, `none`, or `None` for abstain); see the module table."""
    if gold.kind is GoldKind.UNCLEAR:
        return "excluded_unclear"
    if prediction is None:
        return ABSTAIN
    if gold.kind is GoldKind.LABEL:
        if gold.label == NONE_LABEL:
            return "correct_none" if prediction == NONE_LABEL else "wrong_commit"
        if prediction == gold.label:
            return "correct"
        return "predicted_none" if prediction == NONE_LABEL else "wrong"
    if prediction == NONE_LABEL:
        return "none_on_ambiguous" if gold.kind is GoldKind.AMBIGUOUS else "predicted_none"
    inside = prediction in gold.candidates
    if gold.kind is GoldKind.AMBIGUOUS:
        return "commit_in_candidates" if inside else "commit_outside_candidates"
    return "in_set" if inside else "out_of_set"


def gold_kind(gold: CategoryGold | IntentGold) -> str:
    if gold.kind is GoldKind.LABEL:
        return "none" if gold.label == NONE_LABEL else "label"
    return gold.kind.value


def jev_prediction(
    outcome: JevOutcome | None, attempted: bool, question: str
) -> tuple[str | None, str | None]:
    """(prediction, abstention reason). The prediction is the unique top label, `none` included."""
    if outcome is None:
        return None, "unknown" if attempted else "not_attempted"
    if outcome.fallback_reason is not None:
        return None, "fallback"
    proposal: CategoryProposal | IntentProposal | None = getattr(outcome, question)
    if proposal is None or len(proposal.top_labels) != 1:
        return None, "tie"
    return proposal.top_labels[0], None


def evaluate(work_dir: Path, run_id: str) -> dict[str, Any]:
    seal, queries, labels = load_sealed(work_dir)
    run_file = safe_child(work_dir, f"{RUNS_DIR}/{run_id}/{RUN_FILE}")
    if not run_file.is_file():
        raise QueryDecisionError("no such run in the work directory")
    header = decode_json_object(run_file.read_bytes(), RunHeader)
    if (header.queries_sha256, header.labels_sha256) != (seal.queries_sha256, seal.labels_sha256):
        raise ValueError("the run does not belong to the sealed batch")
    _events, state = read_journal(safe_child(work_dir, f"{RUNS_DIR}/{run_id}/{JOURNAL_FILE}"))
    by_id: dict[str, AnyLabel] = {label.query_id: label for label in labels}

    parses = {query.query_id: parse_normalized_query(query.text) for query in queries}
    rows = [_row(q, parses[q.query_id], by_id[q.query_id], state) for q in queries]
    outcomes = list(state.results.values())
    synthetic = header.mode is RunMode.DRY_RUN or seal.synthetic
    report: dict[str, Any] = {
        "report_version": "qd-diagnostic-2",
        "dataset_id": seal.dataset_id,
        "evidence_scope": "development diagnostic; not held-out evidence",
        "synthetic": synthetic,
        "synthetic_reason": _synthetic_reason(header, seal.synthetic),
        "run_id": run_id,
        "mode": header.mode.value,
        "model": header.model,
        "question_set_version": header.question_set_version,
        "queries_sha256": seal.queries_sha256,
        "labels_sha256": seal.labels_sha256,
        "annotator_handle": seal.annotator_handle,
        "queries": len(queries),
        "query_sources": {source.value: count for source, count in seal.counts().items()},
        "ai_author_tools": sorted({q.author_tool for q in queries if q.author_tool}),
        "evidence_notes": _evidence_notes(seal),
        "author_is_annotator": sum(q.author_handle == seal.annotator_handle for q in queries),
        "deterministic": {
            "computed": "at evaluation time, from the sealed query text",
            "parser_versions": sorted({p.parser_version for p in parses.values()}),
            "lexicon_versions": sorted({p.lexicon_version for p in parses.values()}),
            "note": "declared version labels only; not proof that the parser source is "
            "identical to any earlier evaluation",
        },
        "run": {
            "attempts": state.attempts,
            "max_attempts": header.max_attempts,
            "results": len(state.results),
            "unknown_outcome": len(state.unresolved),
            "not_attempted": len(queries) - len(state.dispatched),
            "valid_results": sum(o.fallback_reason is None for o in outcomes),
            "fallbacks": _counts(o.fallback_reason for o in outcomes if o.fallback_reason),
            "input_tokens": sum(o.input_tokens or 0 for o in outcomes),
            "output_tokens": sum(o.output_tokens or 0 for o in outcomes),
            "complete": state.complete,
        },
        "questions": {q: _question_summary(rows, q) for q in QUESTIONS},
        "per_query": rows,
    }
    if seal.annotation_mode is AnnotationMode.CATEGORY_ONLY:
        report["annotation_mode"] = seal.annotation_mode.value
    return report


def _evidence_notes(seal: AnySeal) -> list[str]:
    notes = ["development diagnostic only; never held-out evidence"]
    if seal.ai_authored_queries:
        notes.append(
            f"{seal.ai_authored_queries} of {seal.query_count} queries are AI-authored "
            "(development-only; labels are human annotations; not held-out evidence)"
        )
    if seal.synthetic:
        notes.append("contains synthetic test fixtures: software-test data, not evidence")
    return notes


def _synthetic_reason(header: RunHeader, synthetic_queries: bool) -> str | None:
    reasons = []
    if header.mode is RunMode.DRY_RUN:
        reasons.append("dry run: fixed synthetic responses, not Jev output")
    if synthetic_queries:
        reasons.append("synthetic test-fixture queries")
    return "; ".join(reasons) or None


def _row(
    query: DecisionQuery, parsed: QueryUnderstanding, label: AnyLabel, state: Any
) -> dict[str, Any]:
    deterministic = {
        "category": None if parsed.category is None else parsed.category.value,
        "semantic_intent": None if parsed.semantic_intent is None else parsed.semantic_intent.value,
    }
    outcome = state.results.get(query.query_id)
    attempted = query.query_id in state.dispatched
    row: dict[str, Any] = {
        "query_id": query.query_id,
        "source": query.source.value,
        "stratum": query.stratum,
        "language_style": query.language_style.value,
    }
    golds: dict[str, CategoryGold | IntentGold | None] = {
        "category": label.category,
        "semantic_intent": label.intent_gold,
    }
    for question in QUESTIONS:
        gold = golds[question]
        j_prediction, j_abstain = jev_prediction(outcome, attempted, question)
        d_prediction = deterministic[question]
        row[question] = {
            "gold_kind": NOT_ANNOTATED if gold is None else gold_kind(gold),
            "deterministic": d_prediction,
            "deterministic_outcome": _scored(gold, d_prediction),
            "jev": j_prediction,
            "jev_abstain_reason": j_abstain,
            "jev_outcome": _scored(gold, j_prediction),
        }
    return row


def _scored(gold: CategoryGold | IntentGold | None, prediction: str | None) -> str:
    return EXCLUDED_NOT_ANNOTATED if gold is None else score(gold, prediction)


def _question_summary(rows: list[dict[str, Any]], question: str) -> dict[str, Any]:
    cells = [(row["query_id"], row[question]) for row in rows]
    decided = [(qid, c) for qid, c in cells if c["deterministic"] and c["jev"]]
    disagreements = [(qid, c) for qid, c in decided if c["deterministic"] != c["jev"]]
    abstains = [c for _, c in cells if c["deterministic"] is None]
    fills = [c for c in abstains if c["jev_outcome"] not in EXCLUDED]
    fill_outcomes = Counter(c["jev_outcome"] for c in fills)
    return {
        "gold_kinds": _counts(c["gold_kind"] for _, c in cells),
        "deterministic_outcomes": _counts(c["deterministic_outcome"] for _, c in cells),
        "jev_outcomes": _counts(c["jev_outcome"] for _, c in cells),
        "jev_abstain_reasons": _counts(
            c["jev_abstain_reason"] for _, c in cells if c["jev_abstain_reason"]
        ),
        "both_decided": len(decided),
        "disagreements": len(disagreements),
        "disagreement_outcomes": _counts(
            f"{c['deterministic_outcome']}|{c['jev_outcome']}" for _, c in disagreements
        ),
        "disagreement_query_ids": [qid for qid, _ in disagreements],
        "deterministic_abstains_total": len(abstains),
        "deterministic_abstains_scored": len(fills),
        "jev_on_deterministic_abstains": dict(sorted(fill_outcomes.items())),
        "correct_fills": sum(fill_outcomes[o] for o in CORRECT_FILLS),
        "incorrect_fills": sum(fill_outcomes[o] for o in INCORRECT_FILLS),
    }


def _counts(values: Any) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
