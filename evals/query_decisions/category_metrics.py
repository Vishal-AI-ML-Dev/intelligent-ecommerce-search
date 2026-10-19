"""Category-only classification metrics over resolved partition members (offline, counts first).

Three predictors are scored against the sealed category gold with `evaluate.score`:

* `deterministic`: the `qu-1` category, recomputed now, or abstain;
* `jev`: Jev's unique top label (`none` included) or abstain (`evaluate.jev_prediction`);
* `shadow_gate`: `gate.shadow_category_gate` at a given threshold. A gate that applies no
  category is an abstention here: a Jev `none` never fills anything.

Reported per predictor:

* outcome counts for every gold kind, and the unclear golds, which are only counted;
* single-category golds (`label`, not `none`): n, correct, wrong, `predicted_none`, abstain,
  accuracy (correct / n) and coverage (decided with a category / n);
* a confusion matrix over `label` golds (the four categories and `none`) against predictions
  (the labels plus `abstain`), and per-category precision, recall and F1 from it, with the macro
  F1 over the categories where F1 is defined;
* ambiguous golds: `none_on_ambiguous`, `commit_in_candidates`, `commit_outside_candidates`,
  `abstain`.

`fill_counts` (used by the threshold sweep and the acceptance scorer) counts, on the scored
members where the parser abstains, the gate's accepted fills and their correct and wrong commits.
`strict` wrong commits include a commit inside an ambiguous query's candidates; `lenient` ones
do not.

Ratios are decimal strings rounded to six places, or `None` when the denominator is zero.
Nothing here selects a threshold: the sweep scores only thresholds the caller supplies.

`intent_metrics` scores semantic intent with every gold kind kept apart (see its docstring).
"""

from collections import Counter
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, cast

from evals.query_decisions.contracts import (
    CATEGORY_LABELS,
    INTENT_LABELS,
    NONE_LABEL,
    GoldKind,
    IntentGold,
)
from evals.query_decisions.evaluate import jev_prediction, score
from evals.query_decisions.gate import shadow_category_gate
from evals.query_decisions.partitions import MemberRecord

CATEGORIES = tuple(label for label in CATEGORY_LABELS if label != NONE_LABEL)
INTENTS = tuple(label for label in INTENT_LABELS if label != NONE_LABEL)
ABSTAIN = "abstain"
STRICT_WRONG = frozenset(
    {"wrong", "wrong_commit", "commit_in_candidates", "commit_outside_candidates"}
)
LENIENT_WRONG = STRICT_WRONG - {"commit_in_candidates"}
_PLACES = Decimal("0.000001")

Predictor = Callable[[MemberRecord], str | None]


def ratio(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    value = (Decimal(numerator) / Decimal(denominator)).quantize(_PLACES, ROUND_HALF_EVEN)
    return str(value)


def deterministic_predictor(record: MemberRecord) -> str | None:
    return record.deterministic_category


def jev_predictor(record: MemberRecord) -> str | None:
    return jev_prediction(record.outcome, record.attempted, "category")[0]


def gate_predictor(threshold: Decimal) -> Predictor:
    def predict(record: MemberRecord) -> str | None:
        return shadow_category_gate(record.deterministic, record.outcome, threshold).category

    return predict


def classification_metrics(records: Sequence[MemberRecord], predict: Predictor) -> dict[str, Any]:
    """See the module docstring."""
    outcomes: Counter[str] = Counter()
    single: Counter[str] = Counter()
    ambiguous: Counter[str] = Counter()
    columns = (*CATEGORY_LABELS, ABSTAIN)
    confusion = {gold: dict.fromkeys(columns, 0) for gold in CATEGORY_LABELS}
    unclear = 0
    for record in records:
        gold = record.category_gold
        prediction = predict(record)
        outcome = score(gold, prediction)
        outcomes[outcome] += 1
        if gold.kind is GoldKind.UNCLEAR:
            unclear += 1
        elif gold.kind is GoldKind.AMBIGUOUS:
            ambiguous[outcome] += 1
        else:
            confusion[gold.label][ABSTAIN if prediction is None else prediction] += 1
            if gold.label != NONE_LABEL:
                single[outcome] += 1

    per_category, macro = _per_label(confusion, CATEGORY_LABELS, CATEGORIES)
    single_n = sum(single.values())
    decided = single["correct"] + single["wrong"]
    return {
        "members": len(records),
        "unclear_excluded": unclear,
        "outcomes": dict(sorted(outcomes.items())),
        "single_category": {
            "n": single_n,
            "correct": single["correct"],
            "wrong": single["wrong"],
            "predicted_none": single["predicted_none"],
            "abstain": single[ABSTAIN],
            "accuracy": ratio(single["correct"], single_n),
            "coverage": ratio(decided, single_n),
        },
        "confusion_matrix": {"columns": list(columns), "rows": confusion},
        "per_category": per_category,
        "macro_f1": macro,
        "macro_f1_categories": sum(1 for c in per_category.values() if c["f1"] is not None),
        "ambiguous": {
            name: ambiguous[name]
            for name in (
                "none_on_ambiguous",
                "commit_in_candidates",
                "commit_outside_candidates",
                ABSTAIN,
            )
        },
    }


def _per_label(
    confusion: dict[str, dict[str, int]], gold_labels: Sequence[str], scored: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Per-label support, precision, recall and F1 from a confusion matrix whose rows are the
    `label`-kind golds, and the macro F1 over the labels where F1 is defined."""
    per_label = {}
    f1_values = []
    for label in scored:
        true_positive = confusion[label][label]
        predicted = sum(confusion[gold][label] for gold in gold_labels)
        actual = sum(confusion[label].values())
        f1 = ratio(2 * true_positive, predicted + actual)  # 2PR/(P+R) on counts
        if f1 is not None:
            f1_values.append(Decimal(f1))
        per_label[label] = {
            "support": actual,
            "predicted": predicted,
            "true_positive": true_positive,
            "precision": ratio(true_positive, predicted),
            "recall": ratio(true_positive, actual),
            "f1": f1,
        }
    macro = (
        None
        if not f1_values
        else str((sum(f1_values) / len(f1_values)).quantize(_PLACES, ROUND_HALF_EVEN))
    )
    return per_label, macro


def deterministic_intent_predictor(record: MemberRecord) -> str | None:
    return record.deterministic_intent


def jev_intent_predictor(record: MemberRecord) -> str | None:
    return jev_prediction(record.outcome, record.attempted, "semantic_intent")[0]


def intent_metrics(records: Sequence[MemberRecord], predict: Predictor) -> dict[str, Any]:
    """Semantic-intent metrics with every gold kind kept apart.

    * single intent (`label`, not `none`): correct, wrong, `predicted_none` (a missed intent),
      abstain; accuracy = correct / n and coverage = (correct + wrong) / n;
    * gold `none`: `correct_none`, `wrong_commit`, abstain (an abstention applies no intent,
      but it is not counted as a `none` decision);
    * `multi`: `in_set`, `out_of_set`, `predicted_none`, abstain; `in_set` picks one of the
      stated intents and is not single-intent correctness;
    * `ambiguous`: `none_on_ambiguous`, `commit_in_candidates`, `commit_outside_candidates`,
      abstain;
    * `unclear`: counted only, never scored.

    The confusion matrix covers `label`-kind golds (the intents and `none`) against predictions
    (the labels plus `abstain`); per-intent precision, recall and F1 come from it.
    """
    if any(record.intent_gold is None for record in records):
        raise ValueError("intent metrics need every record's intent gold")
    kinds: dict[str, Counter[str]] = {
        name: Counter() for name in ("single", "none", "multi", "ambiguous")
    }
    columns = (*INTENT_LABELS, ABSTAIN)
    confusion = {gold: dict.fromkeys(columns, 0) for gold in INTENT_LABELS}
    outcomes: Counter[str] = Counter()
    unclear = 0
    for record in records:
        gold = cast(IntentGold, record.intent_gold)  # checked above
        prediction = predict(record)
        outcome = score(gold, prediction)
        outcomes[outcome] += 1
        if gold.kind is GoldKind.UNCLEAR:
            unclear += 1
        elif gold.kind is GoldKind.MULTI:
            kinds["multi"][outcome] += 1
        elif gold.kind is GoldKind.AMBIGUOUS:
            kinds["ambiguous"][outcome] += 1
        else:
            confusion[gold.label][ABSTAIN if prediction is None else prediction] += 1
            kinds["none" if gold.label == NONE_LABEL else "single"][outcome] += 1

    def section(name: str, fields: tuple[str, ...]) -> dict[str, int]:
        counts = kinds[name]
        return {"n": sum(counts.values()), **{field: counts[field] for field in fields}}

    single = section("single", ("correct", "wrong", "predicted_none", ABSTAIN))
    per_intent, macro = _per_label(confusion, INTENT_LABELS, INTENTS)
    return {
        "members": len(records),
        "unclear_excluded": unclear,
        "outcomes": dict(sorted(outcomes.items())),
        "single_intent": {
            **single,
            "accuracy": ratio(single["correct"], single["n"]),
            "coverage": ratio(single["correct"] + single["wrong"], single["n"]),
        },
        "gold_none": section("none", ("correct_none", "wrong_commit", ABSTAIN)),
        "multi": section("multi", ("in_set", "out_of_set", "predicted_none", ABSTAIN)),
        "ambiguous": section(
            "ambiguous",
            ("none_on_ambiguous", "commit_in_candidates", "commit_outside_candidates", ABSTAIN),
        ),
        "confusion_matrix": {"columns": list(columns), "rows": confusion},
        "per_intent": per_intent,
        "macro_f1": macro,
        "macro_f1_intents": sum(1 for i in per_intent.values() if i["f1"] is not None),
    }


def fill_counts(records: Sequence[MemberRecord], threshold: Decimal) -> dict[str, Any]:
    """Gate fills on the scored members where the parser abstains (see the module docstring)."""
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for record in records:
        gold = record.category_gold
        if gold.kind is GoldKind.UNCLEAR:
            counts["unclear"] += 1
            continue
        if record.deterministic_category is not None:
            counts["deterministic_decided"] += 1
            continue
        counts["parser_abstains_scored"] += 1
        if gold.kind is GoldKind.LABEL and gold.label != NONE_LABEL:
            counts["coverage_denominator"] += 1
        decision = shadow_category_gate(record.deterministic, record.outcome, threshold)
        reasons[decision.reason] += 1
        if decision.source != "jev":
            continue
        counts["accepted_fills"] += 1
        outcome = score(gold, decision.category)
        counts["correct_fills"] += outcome == "correct"
        counts["wrong_commits_strict"] += outcome in STRICT_WRONG
        counts["wrong_commits_lenient"] += outcome in LENIENT_WRONG
    names = (
        "unclear",
        "deterministic_decided",
        "parser_abstains_scored",
        "coverage_denominator",
        "accepted_fills",
        "correct_fills",
        "wrong_commits_strict",
        "wrong_commits_lenient",
    )
    result: dict[str, Any] = {name: counts[name] for name in names}
    result["gate_reasons"] = dict(sorted(reasons.items()))
    result["coverage"] = ratio(result["correct_fills"], result["coverage_denominator"])
    return result


def threshold_sweep(
    records: Sequence[MemberRecord], thresholds: Sequence[Decimal]
) -> list[dict[str, Any]]:
    """`fill_counts` at each caller-supplied threshold (no default grid, no selection)."""
    if not thresholds:
        raise ValueError("supply at least one threshold")
    if len(set(thresholds)) != len(thresholds):
        raise ValueError("thresholds must be distinct")
    if any(not (Decimal(0) < t <= Decimal(1)) for t in thresholds):
        raise ValueError("thresholds must be in (0, 1]")
    return [{"threshold": str(t), **fill_counts(records, t)} for t in sorted(thresholds)]
