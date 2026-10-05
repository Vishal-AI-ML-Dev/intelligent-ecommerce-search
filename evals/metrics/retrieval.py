"""Retrieval metrics (`eval-1`) over final Golden labels. Pure, deterministic, offline.

Inputs: one system's ranked product ids per query, the final judgements (pooled pairs with a
label), and, for CSR, an eligibility lookup derived from the hard gold constraints.

Definitions (K ∈ `K_VALUES` = 5, 10, 20; `PRIMARY_K` = 10):

* **Binary relevance (D2).** *Strict* (primary): `relevant`. *Lenient* (secondary): `relevant`
  or `borderline`. Every binary metric is reported under both definitions; no blended score.
* **Unjudged results** (returned but not in the judged pool) count as not relevant, gain 0 and
  irrelevant, and are counted separately (`unjudged@K`), because pooled evaluation undercounts
  what was never pooled.
* **Precision@K** = relevant results in the top K / K. The denominator is always K, so a list
  shorter than K is penalised. Defined for every query.
* **Recall@K** (pooled) = relevant results in the top K / relevant judged pairs of the query.
  Undefined (null) for a query with no relevant judged pair.
* **MRR@K**: reciprocal rank of the first relevant result within the top K, 0 if none. Null for
  a query with no relevant judged pair.
* **NDCG@K**: gains relevant 2, borderline 1, not_relevant 0 (D2); DCG = Σ gain_i / log2(i + 1)
  over the top K; IDCG from the query's judged labels in ideal order. Null when IDCG = 0
  (no relevant or borderline judged pair).
* **CSR@K** (constraint satisfaction rate), `hard` over every hard gold constraint and `fp1`
  over the fp-1-filterable ones: satisfied results in the top K / results returned in the top
  K. Unknown counts as not satisfied and is reported separately (`csr_unknown`). Null when the
  query has no such constraint or returned nothing. `csr_all_satisfied` is 1 when every returned
  top-K result satisfies the constraints, else 0 (same nulls).
* **Irrelevant-result rate@K** = results in the top K labelled `not_relevant` or unjudged /
  results returned in the top K. Null when nothing was returned.
* **Zero-result** = 1 when the system returned no result, else 0.
* **Correct-empty** (zero-relevant queries only): a query with no relevant judged pair under a
  definition is *zero-relevant*; its recall, MRR and NDCG are null (never averaged as 0) and it
  is reported through `correct_empty` (1 when the system returned nothing) and the
  irrelevant-result rate.

**Aggregation**: macro mean (exact rational mean of the serialized per-query values) over the
queries where the value is defined; each macro value states how many queries it covers (`n`)
and how many were excluded as undefined (`excluded`).

**Serialization**: values are decimal strings rounded half-even to 10 places (exact rationals
for the count-based metrics, `math.log2` for NDCG), so results serialize canonically.
"""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from evals.contracts.canonical import ContractError
from evals.contracts.constants import (
    LENIENT_RELEVANT,
    NDCG_GAINS,
    STRICT_RELEVANT,
    Label,
)
from evals.contracts.models import EligibilityRecord, FinalJudgement, OverallStatus

EVALUATOR_VERSION = "eval-1"
K_VALUES: tuple[int, ...] = (5, 10, 20)
PRIMARY_K = 10
DECIMALS = 10

Pair = tuple[str, str]
Definition = Literal["strict", "lenient", "hard", "fp1", "none"]
DEFINITIONS: dict[str, frozenset[Label]] = {"strict": STRICT_RELEVANT, "lenient": LENIENT_RELEVANT}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricValue(_Frozen):
    metric: StrictStr
    k: StrictInt | None
    definition: Definition
    value: StrictStr | None  # decimal string, or null when undefined


class QueryMetrics(_Frozen):
    query_id: StrictStr
    result_count: StrictInt
    values: tuple[MetricValue, ...]


class MacroValue(_Frozen):
    metric: StrictStr
    k: StrictInt | None
    definition: Definition
    value: StrictStr | None
    n: StrictInt
    excluded: StrictInt


class EvaluationConfig(_Frozen):
    evaluator_version: Literal["eval-1"] = EVALUATOR_VERSION
    k_values: tuple[int, ...] = K_VALUES
    primary_k: int = PRIMARY_K
    gains: tuple[tuple[str, int], ...] = tuple((label.value, g) for label, g in NDCG_GAINS.items())
    strict_labels: tuple[str, ...] = tuple(sorted(label.value for label in STRICT_RELEVANT))
    lenient_labels: tuple[str, ...] = tuple(sorted(label.value for label in LENIENT_RELEVANT))
    primary_definition: Literal["strict"] = "strict"
    decimals: int = DECIMALS


class EvaluationResult(_Frozen):
    """One system's metrics on one frozen label set (canonical via `canonical_json`)."""

    schema_version: Literal[1] = 1
    config: EvaluationConfig
    system: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    golden_manifest_sha256: StrictStr | None
    query_count: StrictInt
    per_query: tuple[QueryMetrics, ...]
    macro: tuple[MacroValue, ...]

    def sort_key(self) -> tuple[str, ...]:
        return (self.system,)


def fmt(value: Fraction | float | None) -> str | None:
    """A decimal string rounded half-even to `DECIMALS` places (None stays None)."""
    if value is None:
        return None
    with localcontext() as context:
        context.prec = 50
        if isinstance(value, Fraction):
            number = Decimal(value.numerator) / Decimal(value.denominator)
        else:
            if not math.isfinite(value):
                raise ContractError("metric values must be finite")
            number = Decimal(repr(value))
        quantum = Decimal(1).scaleb(-DECIMALS)
        result = number.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return format(abs(result) if result.is_zero() else result, "f")  # fixed point, never 1E-10


def dcg(gains: Sequence[int]) -> float:
    return math.fsum(g / math.log2(i + 2) for i, g in enumerate(gains))


def _labels_by_query(judgements: Iterable[FinalJudgement]) -> dict[str, dict[str, Label]]:
    labels: dict[str, dict[str, Label]] = {}
    for j in judgements:
        per_query = labels.setdefault(j.query_id, {})
        if j.product_id in per_query:
            raise ContractError(f"duplicate final judgement for {(j.query_id, j.product_id)!r}")
        per_query[j.product_id] = j.label
    return labels


def query_metrics(
    query_id: str,
    ranked: Sequence[str],
    labels: Mapping[str, Label],
    eligibility: Callable[[str, str], EligibilityRecord] | None,
) -> QueryMetrics:
    if len(set(ranked)) != len(ranked):
        raise ContractError(f"{query_id}: a ranked list repeats a product")
    values: list[MetricValue] = []

    def add(metric: str, k: int | None, definition: Definition, value) -> None:
        values.append(MetricValue(metric=metric, k=k, definition=definition, value=fmt(value)))

    judged_gains = sorted((NDCG_GAINS[label] for label in labels.values()), reverse=True)
    for k in K_VALUES:
        top = ranked[:k]
        returned = len(top)
        top_labels = [labels.get(p) for p in top]
        for name, relevant in DEFINITIONS.items():
            total = sum(1 for label in labels.values() if label in relevant)
            hits = [label in relevant for label in top_labels]
            add("precision", k, name, Fraction(sum(hits), k))  # type: ignore[arg-type]
            add("recall", k, name, Fraction(sum(hits), total) if total else None)  # type: ignore[arg-type]
            first = next((i for i, hit in enumerate(hits, start=1) if hit), None)
            rr = Fraction(1, first) if first else Fraction(0)
            add("reciprocal_rank", k, name, rr if total else None)  # type: ignore[arg-type]
        ideal = dcg(judged_gains[:k])
        gains = [NDCG_GAINS[label] if label is not None else 0 for label in top_labels]
        add("ndcg", k, "none", dcg(gains) / ideal if ideal > 0 else None)
        irrelevant = sum(1 for label in top_labels if label in (None, Label.NOT_RELEVANT))
        add(
            "irrelevant_result_rate",
            k,
            "none",
            Fraction(irrelevant, returned) if returned else None,
        )
        add("unjudged", k, "none", Fraction(sum(1 for label in top_labels if label is None)))
        if eligibility is not None:
            records = [eligibility(query_id, p) for p in top]
            for name, attribute in (("hard", "hard_overall"), ("fp1", "fp1_overall")):
                statuses = [getattr(r, attribute) for r in records]
                applicable = returned > 0 and all(
                    s is not OverallStatus.NO_CONSTRAINTS for s in statuses
                )
                satisfied = sum(1 for s in statuses if s is OverallStatus.SATISFIED)
                unknown = sum(1 for s in statuses if s is OverallStatus.UNKNOWN)
                add("csr", k, name, Fraction(satisfied, returned) if applicable else None)  # type: ignore[arg-type]
                add("csr_unknown", k, name, Fraction(unknown, returned) if applicable else None)  # type: ignore[arg-type]
                all_ok = Fraction(int(satisfied == returned)) if applicable else None
                add("csr_all_satisfied", k, name, all_ok)  # type: ignore[arg-type]
    add("zero_result", None, "none", Fraction(int(not ranked)))
    for name, relevant in DEFINITIONS.items():
        zero_relevant = not any(label in relevant for label in labels.values())
        add("correct_empty", None, name, Fraction(int(not ranked)) if zero_relevant else None)  # type: ignore[arg-type]
    return QueryMetrics(query_id=query_id, result_count=len(ranked), values=tuple(values))


def macro(per_query: Sequence[QueryMetrics]) -> tuple[MacroValue, ...]:
    keys: list[tuple[str, int | None, str]] = []
    collected: dict[tuple[str, int | None, str], list[str | None]] = {}
    for q in per_query:
        for v in q.values:
            key = (v.metric, v.k, v.definition)
            if key not in collected:
                keys.append(key)
                collected[key] = []
            collected[key].append(v.value)
    out = []
    for key in keys:
        defined = [Fraction(Decimal(v)) for v in collected[key] if v is not None]
        value = sum(defined, Fraction(0)) / len(defined) if defined else None
        out.append(
            MacroValue(
                metric=key[0],
                k=key[1],
                definition=key[2],  # type: ignore[arg-type]
                value=fmt(value),
                n=len(defined),
                excluded=len(collected[key]) - len(defined),
            )
        )
    return tuple(out)


def evaluate_run(
    system: str,
    run: Mapping[str, Sequence[str]],
    judgements: Iterable[FinalJudgement],
    *,
    eligibility: Callable[[str, str], EligibilityRecord] | None = None,
    golden_manifest_sha256: str | None = None,
) -> EvaluationResult:
    """Score one system. `run` maps every judged query to its ranked product ids (an empty list
    for no results); a query missing from `run`, or a run query without labels, is refused."""
    labels = _labels_by_query(judgements)
    if set(run) != set(labels):
        missing, extra = sorted(set(labels) - set(run)), sorted(set(run) - set(labels))
        raise ContractError(
            f"run and labels cover different queries: missing {missing}, extra {extra}"
        )
    per_query = tuple(
        query_metrics(q, list(run[q]), labels[q], eligibility) for q in sorted(labels)
    )
    return EvaluationResult(
        config=EvaluationConfig(),
        system=system,
        golden_manifest_sha256=golden_manifest_sha256,
        query_count=len(per_query),
        per_query=per_query,
        macro=macro(per_query),
    )


def macro_value(
    result: EvaluationResult, metric: str, k: int | None, definition: str
) -> MacroValue:
    for value in result.macro:
        if (value.metric, value.k, value.definition) == (metric, k, definition):
            return value
    raise KeyError((metric, k, definition))


def query_value(
    result: EvaluationResult, query_id: str, metric: str, k: int | None, definition: str
) -> str | None:
    for q in result.per_query:
        if q.query_id == query_id:
            for v in q.values:
                if (v.metric, v.k, v.definition) == (metric, k, definition):
                    return v.value
    raise KeyError((query_id, metric, k, definition))
