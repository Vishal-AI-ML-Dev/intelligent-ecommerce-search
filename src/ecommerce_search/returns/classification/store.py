"""Recording classification runs and complaint groups (Milestone 19 Part 2).

Reads one M18 dataset version (returns, comments, inspections and linked complaints), runs the
Part 1 baseline, and appends the results to the migration-0008 tables. M18 rows are only read.

**Idempotency and versions.** A run's `run_key_sha256` covers the dataset, every classifier
version (contract, taxonomy, rules, method), the complaint-link policy and the digest of every
input. Recording the same versions over the same inputs again adds nothing and reports the
stored run (`already_recorded`); a new rules version, or changed inputs, gives a new run. A
concurrent duplicate loses on the unique key and also reports the stored run. Group runs work
the same way over (`classification_run_id`, grouping version, method, `min_pattern_size`).

**Complaint link policy** (`complaints-by-order-item-1`, owner decision 7: the caller supplies
linked complaints): a support complaint is linked to a return only when its `order_item_id`
equals the return's. Order-only and product-only complaints are not linked.

**Stored evidence** keeps code-point spans into the source text, not the text itself;
`read_result` rebuilds the full Part 1 result, including `matched_text`, from the source rows.

The writer must be the least-privilege classifier role (`require_writer_role`); see
`classification/grants.py`.
"""

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ecommerce_search.models.return_classifications import (
    ReturnCategoryPrediction,
    ReturnClassification,
    ReturnClassificationEvidence,
    ReturnClassificationInput,
    ReturnClassificationRun,
    ReturnComplaintGroup,
    ReturnComplaintGroupMember,
    ReturnComplaintGroupRun,
)
from ecommerce_search.models.returns import (
    InspectionObservationRow,
    OrderItemRow,
    ReturnCommentRow,
    ReturnDataset,
    ReturnRow,
    SupportComplaintRow,
)
from ecommerce_search.returns.classification import grouping
from ecommerce_search.returns.classification.baseline import classify
from ecommerce_search.returns.classification.contracts import (
    CONTRACT_VERSION,
    METHOD,
    RULES_VERSION,
    CategoryPrediction,
    ClassificationInput,
    ClassificationResult,
    CustomerStatement,
    EvidenceRef,
    InspectionFinding,
    InspectionInput,
    SuppressedMatch,
)
from ecommerce_search.returns.classification.taxonomy import TAXONOMY_VERSION
from ecommerce_search.returns.contracts import EvidenceType

COMPLAINT_LINK_POLICY = "complaints-by-order-item-1"
TIER_SEMANTICS = "rule_tier_not_probability"
NEW_TABLES = tuple(
    m.__tablename__
    for m in (
        ReturnClassificationRun,
        ReturnClassification,
        ReturnClassificationInput,
        ReturnCategoryPrediction,
        ReturnClassificationEvidence,
        ReturnComplaintGroupRun,
        ReturnComplaintGroup,
        ReturnComplaintGroupMember,
    )
)


class StoreError(RuntimeError):
    """A fixed, already-safe message (no input values or credentials)."""


class StoreRoleError(StoreError):
    pass


def _sha(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---- role check ----------------------------------------------------------------------------


def require_writer_role(conn: Connection) -> None:
    """Refuse a superuser, a table owner or a role that can change M18 rows."""
    row = conn.execute(
        text(
            "SELECT r.rolsuper, "
            "has_table_privilege(current_user, 'public.return_classification_runs', 'INSERT'), "
            "has_table_privilege(current_user, 'public.returns', 'INSERT, UPDATE, DELETE'), "
            "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) "
            "AND pg_has_role(current_user, c.relowner, 'USAGE')) "
            "FROM pg_roles r WHERE r.rolname = current_user"
        ),
        {"tables": list(NEW_TABLES)},
    ).one()
    superuser, can_write, can_change_m18, owns = row
    if superuser or owns or can_change_m18 or not can_write:
        raise StoreRoleError("connect as the least-privilege classifier role")


# ---- loading inputs ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadedReturn:
    return_pk: int
    product_id: str
    item: ClassificationInput
    record_sha256: dict[tuple[str, str], str]  # (evidence type, ID) -> M18 record_sha256


def dataset_pk(conn: Connection, dataset_id: str, dataset_version: int) -> int:
    pk = conn.execute(
        select(ReturnDataset.id).where(
            ReturnDataset.dataset_id == dataset_id, ReturnDataset.dataset_version == dataset_version
        )
    ).scalar_one_or_none()
    if pk is None:
        raise StoreError("the dataset version does not exist")
    return pk


def load_inputs(conn: Connection, pk: int) -> list[LoadedReturn]:
    """Every return of the dataset version, ordered by `return_id`, with its linked records."""
    comment_t, inspection_t = ReturnCommentRow.__table__, InspectionObservationRow.__table__
    complaint_t, return_t = SupportComplaintRow.__table__, ReturnRow.__table__
    comments: dict[str, list[Any]] = defaultdict(list)
    for row in conn.execute(select(comment_t).where(comment_t.c.dataset_pk == pk)):
        comments[row.return_id].append(row)
    inspections: dict[str, list[Any]] = defaultdict(list)
    for row in conn.execute(select(inspection_t).where(inspection_t.c.dataset_pk == pk)):
        inspections[row.return_id].append(row)
    complaints: dict[str, list[Any]] = defaultdict(list)
    for row in conn.execute(
        select(complaint_t).where(
            complaint_t.c.dataset_pk == pk, complaint_t.c.order_item_id.is_not(None)
        )
    ):
        complaints[row.order_item_id].append(row)
    rows = conn.execute(
        select(return_t, OrderItemRow.product_id.label("item_product_id"))
        .join(
            OrderItemRow,
            (OrderItemRow.dataset_pk == return_t.c.dataset_pk)
            & (OrderItemRow.order_item_id == return_t.c.order_item_id),
        )
        .where(return_t.c.dataset_pk == pk)
        .order_by(return_t.c.return_id)
    ).all()
    loaded = []
    for ret in rows:
        product_id = ret.item_product_id
        own_comments = sorted(comments[ret.return_id], key=lambda r: r.comment_id)
        own_complaints = sorted(complaints[ret.order_item_id], key=lambda r: r.complaint_id)
        own_inspections = sorted(inspections[ret.return_id], key=lambda r: r.observation_id)
        statements = [
            CustomerStatement(
                evidence_type=EvidenceType.RETURN_COMMENT, evidence_id=c.comment_id, text=c.text
            )
            for c in own_comments
        ] + [
            CustomerStatement(
                evidence_type=EvidenceType.SUPPORT_COMPLAINT,
                evidence_id=c.complaint_id,
                text=c.text,
                customer_topic=c.customer_topic,
            )
            for c in own_complaints
        ]
        item = ClassificationInput(
            return_id=ret.return_id,
            customer_reason_code=ret.customer_reason_code,
            customer_statements=tuple(statements),
            inspection_observations=tuple(
                InspectionInput(observation_id=o.observation_id, condition=o.condition)
                for o in own_inspections
            ),
        )
        shas = {("return", ret.return_id): ret.record_sha256}
        shas |= {("return_comment", c.comment_id): c.record_sha256 for c in own_comments}
        shas |= {("support_complaint", c.complaint_id): c.record_sha256 for c in own_complaints}
        shas |= {
            ("inspection_observation", o.observation_id): o.record_sha256 for o in own_inspections
        }
        loaded.append(LoadedReturn(ret.id, product_id, item, shas))
    return loaded


# ---- reports -------------------------------------------------------------------------------


class _Report(BaseModel):
    model_config = ConfigDict(frozen=True)


class RunReport(_Report):
    status: Literal["recorded", "already_recorded"]
    run_id: int
    dataset_id: str
    dataset_version: int
    contract_version: str
    taxonomy_version: str
    rules_version: str
    method: str
    tier_semantics: str
    complaint_link_policy: str
    return_count: int
    classified_count: int
    abstained_count: int
    needs_review_count: int
    single_category_counts: dict[str, int]  # returns with a single-category prediction of it
    unresolved_candidate_counts: dict[str, int]  # "a|b" -> returns with that unresolved set
    review_reason_counts: dict[str, int]
    tier_counts: dict[str, int]  # predictions per tier
    note: str = "counts of inferred outputs on synthetic data; not accuracy"


class GroupSummary(_Report):
    product_id: str
    signature: str
    member_count: int
    qualifying_member_count: int  # medium/high tier, not contradicted, no conflict


class GroupReport(_Report):
    status: Literal["recorded", "already_recorded"]
    group_run_id: int
    classification_run_id: int
    grouping_version: str
    method: str
    min_pattern_size: int
    group_count: int
    pattern_count: int
    grouped_return_count: int
    ungrouped_return_count: int
    patterns: list[GroupSummary]  # at least `min_pattern_size` qualifying members
    non_pattern_size_counts: dict[int, int]  # member count -> number of non-pattern groups
    non_pattern_with_enough_members: int  # non-patterns that have >= min members in total
    note: str = (
        "groupings of similar complaints, not root causes; the pattern threshold is a display "
        "threshold, not statistical significance"
    )


def run_summary(conn: Connection, run_id: int) -> RunReport:
    run_t = ReturnClassificationRun.__table__
    record = conn.execute(
        select(
            run_t,
            ReturnDataset.dataset_id.label("ds_id"),
            ReturnDataset.dataset_version.label("ds_version"),
        )
        .join(ReturnDataset, ReturnDataset.id == run_t.c.dataset_pk)
        .where(run_t.c.id == run_id)
    ).one_or_none()
    if record is None:
        raise StoreError("the classification run does not exist")
    ds_id, ds_version = record.ds_id, record.ds_version
    single: Counter[str] = Counter()
    unresolved: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    for resolution, category, candidates, tier in conn.execute(
        select(
            ReturnCategoryPrediction.resolution,
            ReturnCategoryPrediction.category,
            ReturnCategoryPrediction.candidates,
            ReturnCategoryPrediction.tier,
        )
        .join(
            ReturnClassification,
            ReturnClassification.id == ReturnCategoryPrediction.classification_id,
        )
        .where(ReturnClassification.run_id == run_id)
    ):
        tiers[tier] += 1
        if resolution == "single_category":
            single[category] += 1
        else:
            unresolved["|".join(candidates)] += 1
    reasons: Counter[str] = Counter()
    for (review_reasons,) in conn.execute(
        select(ReturnClassification.review_reasons).where(ReturnClassification.run_id == run_id)
    ):
        reasons.update(review_reasons)
    return RunReport(
        status="recorded",
        run_id=record.id,
        dataset_id=ds_id,
        dataset_version=ds_version,
        contract_version=record.contract_version,
        taxonomy_version=record.taxonomy_version,
        rules_version=record.rules_version,
        method=record.method,
        tier_semantics=record.tier_semantics,
        complaint_link_policy=record.complaint_link_policy,
        return_count=record.return_count,
        classified_count=record.classified_count,
        abstained_count=record.abstained_count,
        needs_review_count=record.needs_review_count,
        single_category_counts=dict(sorted(single.items())),
        unresolved_candidate_counts=dict(sorted(unresolved.items())),
        review_reason_counts=dict(sorted(reasons.items())),
        tier_counts=dict(sorted(tiers.items())),
    )


# ---- recording a classification run --------------------------------------------------------


def _evidence_row(ref: EvidenceRef, **extra: Any) -> dict[str, Any]:
    return {
        "source_kind": ref.source_kind.value,
        "evidence_type": ref.evidence_type.value,
        "evidence_id": ref.evidence_id,
        "field": ref.field,
        "rule_id": ref.rule_id,
        "match": ref.match.value,
        "value": ref.value,
        "span_start": ref.span[0] if ref.span else None,
        "span_end": ref.span[1] if ref.span else None,
        "prediction_ordinal": None,
        "effect": None,
        "suppression_reason": None,
        "categories": None,
        **extra,
    }


def _evidence_rows(result: ClassificationResult) -> list[dict[str, Any]]:
    rows = []
    for i, prediction in enumerate(result.predictions):
        rows += [
            _evidence_row(r, kind="supporting", prediction_ordinal=i) for r in prediction.supporting
        ]
        rows += [
            _evidence_row(r, kind="contradicting", prediction_ordinal=i)
            for r in prediction.contradicting
        ]
    rows += [
        _evidence_row(
            f.ref,
            kind="inspection_finding",
            effect=f.effect.value,
            categories=[c.value for c in f.categories],
        )
        for f in result.inspection_findings
    ]
    rows += [
        _evidence_row(
            s.ref,
            kind="suppressed_match",
            suppression_reason=s.reason,
            categories=[c.value for c in s.categories],
        )
        for s in result.suppressed_matches
    ]
    return [row | {"ordinal": n} for n, row in enumerate(rows)]


def _input_rows(loaded: LoadedReturn) -> list[dict[str, Any]]:
    item = loaded.item
    keys = [("return", item.return_id)]
    keys += [(s.evidence_type.value, s.evidence_id) for s in item.customer_statements]
    keys += [("inspection_observation", o.observation_id) for o in item.inspection_observations]
    return [
        {
            "ordinal": n,
            "evidence_type": etype,
            "evidence_id": eid,
            "record_sha256": loaded.record_sha256[(etype, eid)],
        }
        for n, (etype, eid) in enumerate(keys)
    ]


def record_classification_run(engine: Engine, dataset_id: str, dataset_version: int) -> RunReport:
    with engine.begin() as conn:
        require_writer_role(conn)
        pk = dataset_pk(conn, dataset_id, dataset_version)
        loaded = load_inputs(conn, pk)
        results = [classify(entry.item) for entry in loaded]
        input_shas = [_sha(entry.item.model_dump(mode="json")) for entry in loaded]
        input_sha = _sha([[e.item.return_id, s] for e, s in zip(loaded, input_shas, strict=True)])
        versions = {
            "contract_version": CONTRACT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
            "rules_version": RULES_VERSION,
            "method": METHOD,
            "tier_semantics": TIER_SEMANTICS,
            "complaint_link_policy": COMPLAINT_LINK_POLICY,
        }
        run_key = _sha({"dataset_pk": pk, "input_sha256": input_sha, **versions})
        abstained = sum(r.status.value == "abstained" for r in results)
        run_id = conn.execute(
            pg_insert(ReturnClassificationRun)
            .values(
                dataset_pk=pk,
                input_sha256=input_sha,
                run_key_sha256=run_key,
                return_count=len(results),
                classified_count=len(results) - abstained,
                abstained_count=abstained,
                needs_review_count=sum(r.needs_review for r in results),
                **versions,
            )
            .on_conflict_do_nothing(index_elements=["run_key_sha256"])
            .returning(ReturnClassificationRun.id)
        ).scalar_one_or_none()
        if run_id is None:
            existing = conn.execute(
                select(ReturnClassificationRun.id).where(
                    ReturnClassificationRun.run_key_sha256 == run_key
                )
            ).scalar_one()
            return run_summary(conn, existing).model_copy(update={"status": "already_recorded"})
        for entry, result, item_sha in zip(loaded, results, input_shas, strict=True):
            classification_id = conn.execute(
                insert(ReturnClassification)
                .values(
                    run_id=run_id,
                    return_pk=entry.return_pk,
                    return_id=result.return_id,
                    status=result.status.value,
                    needs_review=result.needs_review,
                    review_reasons=[r.value for r in result.review_reasons],
                    prediction_count=len(result.predictions),
                    input_sha256=item_sha,
                    result_sha256=_sha(result.model_dump(mode="json")),
                )
                .returning(ReturnClassification.id)
            ).scalar_one()
            conn.execute(
                insert(ReturnClassificationInput),
                [row | {"classification_id": classification_id} for row in _input_rows(entry)],
            )
            if result.predictions:
                conn.execute(
                    insert(ReturnCategoryPrediction),
                    [
                        {
                            "classification_id": classification_id,
                            "ordinal": n,
                            "resolution": p.resolution.value,
                            "category": p.category.value if p.category else None,
                            "candidates": [c.value for c in p.candidates],
                            "tier": p.tier.value,
                            "suggested_areas": [a.value for a in p.suggested_responsible_areas],
                        }
                        for n, p in enumerate(result.predictions)
                    ],
                )
            evidence = _evidence_rows(result)
            if evidence:
                conn.execute(
                    insert(ReturnClassificationEvidence),
                    [row | {"classification_id": classification_id} for row in evidence],
                )
        return run_summary(conn, run_id)


# ---- reading a stored result back ----------------------------------------------------------


def _ref(row: Any, texts: dict[tuple[str, str], str]) -> EvidenceRef:
    span = (row.span_start, row.span_end) if row.span_start is not None else None
    matched = texts[(row.evidence_type, row.evidence_id)][span[0] : span[1]] if span else None
    return EvidenceRef(
        source_kind=row.source_kind,
        evidence_type=row.evidence_type,
        evidence_id=row.evidence_id,
        field=row.field,
        rule_id=row.rule_id,
        match=row.match,
        value=row.value,
        span=span,
        matched_text=matched,
    )


# (evidence type, M18 table, ID column, text column or None)
_SOURCES: tuple[tuple[str, Any, Any, Any], ...] = (
    ("return", ReturnRow, ReturnRow.return_id, None),
    ("return_comment", ReturnCommentRow, ReturnCommentRow.comment_id, ReturnCommentRow.text),
    (
        "support_complaint",
        SupportComplaintRow,
        SupportComplaintRow.complaint_id,
        SupportComplaintRow.text,
    ),
    (
        "inspection_observation",
        InspectionObservationRow,
        InspectionObservationRow.observation_id,
        None,
    ),
)


class SourceMismatchError(StoreError):
    """A stored result no longer matches its M18 source records."""


def _verified_sources(
    conn: Connection, classification_id: int, pk: int
) -> dict[tuple[str, str], str]:
    """Check every input record of a result against its M18 row; return the source texts.

    Each recorded input must still exist in the run's dataset version with the recorded
    `record_sha256`. Otherwise nothing is rebuilt: `SourceMismatchError` is raised."""
    recorded = {
        (etype, eid): sha
        for etype, eid, sha in conn.execute(
            select(
                ReturnClassificationInput.evidence_type,
                ReturnClassificationInput.evidence_id,
                ReturnClassificationInput.record_sha256,
            ).where(ReturnClassificationInput.classification_id == classification_id)
        )
    }
    found: dict[tuple[str, str], str] = {}
    texts: dict[tuple[str, str], str] = {}
    for etype, model, id_col, text_col in _SOURCES:
        ids = [eid for (t, eid) in recorded if t == etype]
        if not ids:
            continue
        columns = [id_col, model.record_sha256] + ([text_col] if text_col is not None else [])
        for row in conn.execute(select(*columns).where(model.dataset_pk == pk, id_col.in_(ids))):
            found[(etype, row[0])] = row[1]
            if text_col is not None:
                texts[(etype, row[0])] = row[2]
    if set(found) != set(recorded):
        raise SourceMismatchError("a source record of this result is missing")
    if found != recorded:
        raise SourceMismatchError("a source record of this result has changed")
    return texts


def read_result(conn: Connection, classification_id: int) -> ClassificationResult:
    """The stored result as a Part 1 `ClassificationResult`.

    Every source record is verified first (`_verified_sources`), and every stored evidence ref
    must cite one of the result's recorded inputs; `matched_text` then comes from the source
    rows through the stored spans. Any failure raises `SourceMismatchError` and returns
    nothing partial."""
    classification_t = ReturnClassification.__table__
    record = conn.execute(
        select(classification_t, ReturnClassificationRun.dataset_pk.label("run_dataset_pk"))
        .join(ReturnClassificationRun, ReturnClassificationRun.id == classification_t.c.run_id)
        .where(classification_t.c.id == classification_id)
    ).one_or_none()
    if record is None:
        raise StoreError("the classification does not exist")
    texts = _verified_sources(conn, classification_id, record.run_dataset_pk)
    inputs = {
        tuple(row)
        for row in conn.execute(
            select(
                ReturnClassificationInput.evidence_type, ReturnClassificationInput.evidence_id
            ).where(ReturnClassificationInput.classification_id == classification_id)
        )
    }
    evidence_t, prediction_t = (
        ReturnClassificationEvidence.__table__,
        ReturnCategoryPrediction.__table__,
    )
    evidence = conn.execute(
        select(evidence_t)
        .where(evidence_t.c.classification_id == classification_id)
        .order_by(evidence_t.c.ordinal)
    ).all()
    if any((e.evidence_type, e.evidence_id) not in inputs for e in evidence):
        raise SourceMismatchError("stored evidence cites a record outside the result's inputs")
    predictions = []
    for p in conn.execute(
        select(prediction_t)
        .where(prediction_t.c.classification_id == classification_id)
        .order_by(prediction_t.c.ordinal)
    ):
        mine = [e for e in evidence if e.prediction_ordinal == p.ordinal]
        predictions.append(
            CategoryPrediction(
                resolution=p.resolution,
                candidates=tuple(p.candidates),
                category=p.category,
                tier=p.tier,
                suggested_responsible_areas=tuple(p.suggested_areas),
                supporting=tuple(_ref(e, texts) for e in mine if e.kind == "supporting"),
                contradicting=tuple(_ref(e, texts) for e in mine if e.kind == "contradicting"),
            )
        )
    return ClassificationResult(
        return_id=record.return_id,
        status=record.status,
        predictions=tuple(predictions),
        review_reasons=tuple(record.review_reasons),
        needs_review=record.needs_review,
        inspection_findings=tuple(
            InspectionFinding(ref=_ref(e, texts), effect=e.effect, categories=tuple(e.categories))
            for e in evidence
            if e.kind == "inspection_finding"
        ),
        suppressed_matches=tuple(
            SuppressedMatch(
                ref=_ref(e, texts), categories=tuple(e.categories), reason=e.suppression_reason
            )
            for e in evidence
            if e.kind == "suppressed_match"
        ),
    )


# ---- recording complaint groups ------------------------------------------------------------


def _group_items(conn: Connection, run_id: int) -> tuple[list[grouping.GroupItem], int]:
    rules: dict[tuple[int, int], list[str]] = defaultdict(list)
    for cid, ordinal, rule_id in conn.execute(
        select(
            ReturnClassificationEvidence.classification_id,
            ReturnClassificationEvidence.prediction_ordinal,
            ReturnClassificationEvidence.rule_id,
        )
        .join(
            ReturnClassification,
            ReturnClassification.id == ReturnClassificationEvidence.classification_id,
        )
        .where(
            ReturnClassification.run_id == run_id,
            ReturnClassificationEvidence.kind == "supporting",
        )
        .order_by(
            ReturnClassificationEvidence.classification_id, ReturnClassificationEvidence.ordinal
        )
    ):
        rules[(cid, ordinal)].append(rule_id)
    contradicted = {
        tuple(row)
        for row in conn.execute(
            select(
                ReturnClassificationEvidence.classification_id,
                ReturnClassificationEvidence.prediction_ordinal,
            )
            .join(
                ReturnClassification,
                ReturnClassification.id == ReturnClassificationEvidence.classification_id,
            )
            .where(
                ReturnClassification.run_id == run_id,
                ReturnClassificationEvidence.kind == "contradicting",
            )
        )
    }
    items = [
        grouping.GroupItem(
            cid,
            product_id,
            ordinal,
            tuple(candidates),
            tuple(rules[(cid, ordinal)]),
            tier=tier,
            contradicted=(cid, ordinal) in contradicted,
            review_reasons=tuple(reasons),
        )
        for cid, product_id, ordinal, candidates, tier, reasons in conn.execute(
            select(
                ReturnClassification.id,
                OrderItemRow.product_id,
                ReturnCategoryPrediction.ordinal,
                ReturnCategoryPrediction.candidates,
                ReturnCategoryPrediction.tier,
                ReturnClassification.review_reasons,
            )
            .join(
                ReturnCategoryPrediction,
                ReturnCategoryPrediction.classification_id == ReturnClassification.id,
            )
            .join(ReturnRow, ReturnRow.id == ReturnClassification.return_pk)
            .join(
                OrderItemRow,
                (OrderItemRow.dataset_pk == ReturnRow.dataset_pk)
                & (OrderItemRow.order_item_id == ReturnRow.order_item_id),
            )
            .where(ReturnClassification.run_id == run_id)
        )
    ]
    total = conn.execute(
        select(ReturnClassificationRun.return_count).where(ReturnClassificationRun.id == run_id)
    ).scalar_one_or_none()
    if total is None:
        raise StoreError("the classification run does not exist")
    return items, total


def group_summary(conn: Connection, group_run_id: int) -> GroupReport:
    group_run_t = ReturnComplaintGroupRun.__table__
    run = conn.execute(select(group_run_t).where(group_run_t.c.id == group_run_id)).one_or_none()
    if run is None:
        raise StoreError("the group run does not exist")
    groups = conn.execute(
        select(
            ReturnComplaintGroup.product_id,
            ReturnComplaintGroup.signature,
            ReturnComplaintGroup.member_count,
            ReturnComplaintGroup.qualifying_member_count,
            ReturnComplaintGroup.is_pattern,
        )
        .where(ReturnComplaintGroup.group_run_id == group_run_id)
        .order_by(ReturnComplaintGroup.ordinal)
    ).all()
    small = Counter(g.member_count for g in groups if not g.is_pattern)
    return GroupReport(
        status="recorded",
        group_run_id=run.id,
        classification_run_id=run.classification_run_id,
        grouping_version=run.grouping_version,
        method=run.method,
        min_pattern_size=run.min_pattern_size,
        group_count=run.group_count,
        pattern_count=run.pattern_count,
        grouped_return_count=run.grouped_return_count,
        ungrouped_return_count=run.ungrouped_return_count,
        patterns=[
            GroupSummary(
                product_id=g.product_id,
                signature=g.signature,
                member_count=g.member_count,
                qualifying_member_count=g.qualifying_member_count,
            )
            for g in groups
            if g.is_pattern
        ],
        non_pattern_size_counts=dict(sorted(small.items())),
        non_pattern_with_enough_members=sum(
            not g.is_pattern and g.member_count >= run.min_pattern_size for g in groups
        ),
    )


def record_group_run(
    engine: Engine,
    classification_run_id: int,
    min_pattern_size: int = grouping.DEFAULT_MIN_PATTERN_SIZE,
) -> GroupReport:
    with engine.begin() as conn:
        require_writer_role(conn)
        items, total = _group_items(conn, classification_run_id)
        groups = grouping.group_items(items, min_pattern_size)
        grouped = len({m.classification_id for g in groups for m in g.members})
        run_key = _sha(
            {
                "classification_run_id": classification_run_id,
                "grouping_version": grouping.GROUPING_VERSION,
                "method": grouping.METHOD,
                "min_pattern_size": min_pattern_size,
            }
        )
        group_run_id = conn.execute(
            pg_insert(ReturnComplaintGroupRun)
            .values(
                classification_run_id=classification_run_id,
                grouping_version=grouping.GROUPING_VERSION,
                method=grouping.METHOD,
                min_pattern_size=min_pattern_size,
                run_key_sha256=run_key,
                group_count=len(groups),
                pattern_count=sum(g.is_pattern for g in groups),
                grouped_return_count=grouped,
                ungrouped_return_count=total - grouped,
            )
            .on_conflict_do_nothing(index_elements=["run_key_sha256"])
            .returning(ReturnComplaintGroupRun.id)
        ).scalar_one_or_none()
        if group_run_id is None:
            existing = conn.execute(
                select(ReturnComplaintGroupRun.id).where(
                    ReturnComplaintGroupRun.run_key_sha256 == run_key
                )
            ).scalar_one()
            return group_summary(conn, existing).model_copy(update={"status": "already_recorded"})
        for n, group in enumerate(groups):
            group_id = conn.execute(
                insert(ReturnComplaintGroup)
                .values(
                    group_run_id=group_run_id,
                    ordinal=n,
                    product_id=group.product_id,
                    signature=group.signature,
                    candidates=list(group.candidates),
                    member_count=group.member_count,
                    qualifying_member_count=group.qualifying_member_count,
                    is_pattern=group.is_pattern,
                )
                .returning(ReturnComplaintGroup.id)
            ).scalar_one()
            conn.execute(
                insert(ReturnComplaintGroupMember),
                [
                    {
                        "group_id": group_id,
                        "classification_id": m.classification_id,
                        "prediction_ordinal": m.prediction_ordinal,
                        "counts_toward_pattern": m.qualifies,
                    }
                    for m in group.members
                ],
            )
        return group_summary(conn, group_run_id)
