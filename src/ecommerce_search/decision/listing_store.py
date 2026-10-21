"""Append-only persistence of inferred listing decisions (Milestone 14).

`decide_and_store(engine, seller_id, ref, version_number, provider)` records one decision for one
stored M13 listing version:

1. read the stored version (READ ONLY, integrity-verified by the M13 store);
2. normalize its exact stored raw bytes under the current rules and require the result to equal
   the run recorded for the current `rules_version` (a missing or differing run is refused and
   nothing is written: decisions are only ever linked to an exact stored normalization);
3. require the decision tables to exist (before the provider is called), build the `jl-1`
   state, call the provider once, validate the outcome and route it under `lr-1`
   (no transaction is open during the provider call);
4. write the decision run, its proposals and its reasons in ONE transaction, linked to that
   normalization run. A provider fallback is a valid outcome and is recorded as returned.

Every call appends a new decision run; nothing is deduplicated, replaced or chosen between.
Nothing here writes an M13, catalog, search or embedding row.

`read_decisions` loads a version's decision runs AS RECORDED (READ ONLY, not re-validated against
today's contracts or policy) and `verify_decision` checks each against its own stored hashes and
its linked normalization run. A historical digest need not equal today's.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from ecommerce_search.decision.listing_models import ListingDecisionOutcome
from ecommerce_search.decision.listing_provider import ListingDecisionProvider, decide_listing
from ecommerce_search.decision.listing_routing import RoutingDecision, route_listing
from ecommerce_search.models.listing_decisions import (
    ListingDecisionProbability,
    ListingDecisionProposal,
    ListingDecisionReason,
    ListingDecisionRun,
)
from ecommerce_search.models.listings import ListingNormalizationRun, ListingVersion
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.store import (
    UNDEFINED_TABLE,
    IntegrityFailure,
    NotMigrated,
    StoreError,
    read_listing,
)

OUTCOME_FIELDS = (
    "provider",
    "question_set_version",
    "state_sha256",
    "model",
    "response_model",
    "latency_ms",
    "request_id",
    "input_tokens",
    "output_tokens",
    "usage",
    "rejection",
    "rejection_call",
    "rejection_question",
    "fallback_reason",
)
PROPOSAL_FIELDS = (
    "label",
    "raw_choice",
    "provider_confidence",
    "gate_confidence",
    "decision_type",
)


class NormalizationNotStored(StoreError):
    pass


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(nr.canonical_json(value)).hexdigest()


def _not_migrated(exc: ProgrammingError) -> NotMigrated | None:
    if getattr(exc.orig, "sqlstate", None) == UNDEFINED_TABLE:
        return NotMigrated(
            "the database is not migrated to the current revision (a listing decision table is "
            "missing); run `uv run alembic upgrade head`. Nothing was written"
        )
    return None


# ---- records as stored ------------------------------------------------------------------


@dataclass(frozen=True)
class StoredProbability:
    label_ordinal: int  # position in the question's label set when recorded
    label: str
    probability: float
    is_top: bool


@dataclass(frozen=True)
class StoredProposal:
    ordinal: int
    decision_type: str
    claim_ordinal: int | None
    label: str | None
    raw_choice: str
    provider_confidence: float
    gate_confidence: float | None
    probabilities: tuple[StoredProbability, ...]  # ordered by label_ordinal

    def recorded(self) -> dict[str, Any]:
        """The proposal exactly as `ListingDecisionOutcome.model_dump(mode="json")` gives it."""
        out = {name: getattr(self, name) for name in PROPOSAL_FIELDS}
        out["top_labels"] = [p.label for p in self.probabilities if p.is_top]
        out["provider_probabilities"] = {p.label: p.probability for p in self.probabilities}
        if self.decision_type == "attribute_support":
            out["ordinal"] = self.claim_ordinal
        return out


@dataclass(frozen=True)
class StoredReason:
    ordinal: int
    code: str
    route: str
    subject: str | None


@dataclass(frozen=True)
class StoredDecision:
    decision_run_id: int
    normalization_rules_version: str
    normalization_output_sha256: str  # as copied onto the decision run
    linked_output_sha256: str  # as recorded on the linked normalization run
    question_set_version: str
    question_set_digest: str
    policy_version: str
    policy_digest: str
    route: str
    deterministic_route: str
    proposal_count: int
    reason_count: int
    outcome_sha256: str
    decision_sha256: str
    created_at: datetime | None
    telemetry: dict[str, Any]  # every OUTCOME_FIELDS value as stored
    proposals: tuple[StoredProposal, ...]
    reasons: tuple[StoredReason, ...]

    def recorded_outcome(self) -> dict[str, Any]:
        by_type: dict[str, list[dict[str, Any]]] = {}
        for p in self.proposals:
            by_type.setdefault(p.decision_type, []).append(p.recorded())
        single = {
            t: (by_type[t][0] if len(by_type.get(t, ())) == 1 else None)
            for t in ("category", "subcategory")
        }
        return {
            **self.telemetry,
            "category": single["category"],
            "subcategory": single["subcategory"],
            "attribute_support": by_type.get("attribute_support", []),
        }

    def recorded_decision(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "state_sha256": self.telemetry["state_sha256"],
            "provider": self.telemetry["provider"],
            "jev_fallback_reason": self.telemetry["fallback_reason"],
            "route": self.route,
            "deterministic_route": self.deterministic_route,
            "reasons": [
                {"code": r.code, "route": r.route, "subject": r.subject} for r in self.reasons
            ],
        }


def verify_decision(stored: StoredDecision) -> None:
    """Raise IntegrityFailure unless a stored decision is self-consistent and still matches the
    normalization run it is linked to. Nothing is re-run."""
    problems: list[str] = []
    if stored.normalization_output_sha256 != stored.linked_output_sha256:
        problems.append("the linked normalization run's output_sha256 differs")
    if len(stored.proposals) != stored.proposal_count:
        problems.append(f"{len(stored.proposals)} proposal rows, recorded {stored.proposal_count}")
    if len(stored.reasons) != stored.reason_count:
        problems.append(f"{len(stored.reasons)} reason rows, recorded {stored.reason_count}")
    if tuple(p.ordinal for p in stored.proposals) != tuple(range(len(stored.proposals))) or (
        tuple(r.ordinal for r in stored.reasons) != tuple(range(len(stored.reasons)))
    ):
        problems.append("row ordinals are not contiguous from 0")
    types = [p.decision_type for p in stored.proposals]
    head = [t for t in types if t != "attribute_support"]
    if head not in ([], ["category", "subcategory"]) or types[: len(head)] != head:
        problems.append("proposals are not category, subcategory, then attribute support")
    if canonical_sha256(stored.recorded_outcome()) != stored.outcome_sha256:
        problems.append("stored rows do not reproduce outcome_sha256")
    if canonical_sha256(stored.recorded_decision()) != stored.decision_sha256:
        problems.append("stored rows do not reproduce decision_sha256")
    if problems:
        raise IntegrityFailure(
            f"listing decision {stored.decision_run_id} failed its integrity check: "
            + "; ".join(problems)
        )


# ---- write ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionStoreResult:
    decision_run_id: int
    seller_id: str
    seller_listing_ref: str
    version_number: int
    rules_version: str
    normalization_output_sha256: str
    provider: str
    fallback_reason: str | None
    route: str
    deterministic_route: str
    outcome_sha256: str
    decision_sha256: str

    def summary(self) -> str:
        fallback = f" fallback={self.fallback_reason}" if self.fallback_reason else ""
        return (
            f"listing {self.seller_id!r}/{self.seller_listing_ref!r} version "
            f"{self.version_number}: decision {self.decision_run_id} recorded "
            f"(provider={self.provider}{fallback}; route={self.route}, "
            f"deterministic_route={self.deterministic_route}; normalization "
            f"{self.rules_version} output_sha256={self.normalization_output_sha256}). A route is "
            "an automated routing result, not a human review"
        )


def _add_proposals(session: Session, run_id: int, outcome: ListingDecisionOutcome) -> None:
    proposals = [outcome.category, outcome.subcategory, *outcome.attribute_support]
    for ordinal, p in enumerate(q for q in proposals if q is not None):
        dumped = p.model_dump(mode="json")
        row = ListingDecisionProposal(
            decision_run_id=run_id,
            ordinal=ordinal,
            claim_ordinal=dumped.get("ordinal"),
            **{name: dumped[name] for name in PROPOSAL_FIELDS},
        )
        session.add(row)
        session.flush()
        session.add_all(
            ListingDecisionProbability(
                proposal_id=row.id,
                label_ordinal=type(p).LABELS.index(label),
                label=label,
                probability=probability,
                is_top=label in p.top_labels,
            )
            for label, probability in p.provider_probabilities.items()
        )


def _record(
    session: Session,
    run: ListingNormalizationRun,
    outcome: ListingDecisionOutcome,
    decision: RoutingDecision,
    question_set_digest: str,
) -> int:
    dumped = outcome.model_dump(mode="json")
    proposals_count = sum(p is not None for p in (outcome.category, outcome.subcategory))
    proposals_count += len(outcome.attribute_support)
    row = ListingDecisionRun(
        normalization_run_id=run.id,
        normalization_output_sha256=run.output_sha256,
        question_set_digest=question_set_digest,
        policy_version=decision.policy_version,
        policy_digest=decision.policy_digest,
        route=decision.route.value,
        deterministic_route=decision.deterministic_route.value,
        proposal_count=proposals_count,
        reason_count=len(decision.reasons),
        outcome_sha256=canonical_sha256(dumped),
        decision_sha256=canonical_sha256(decision.model_dump(mode="json")),
        **{name: dumped[name] for name in OUTCOME_FIELDS},
    )
    session.add(row)
    session.flush()
    _add_proposals(session, row.id, outcome)
    session.add_all(
        ListingDecisionReason(
            decision_run_id=row.id,
            ordinal=i,
            code=r.code,
            route=r.route.value,
            subject=r.subject,
        )
        for i, r in enumerate(decision.reasons)
    )
    session.flush()
    return row.id


def _normalization_run(
    session: Session, seller_id: str, ref: str, number: int, rules_version: str
) -> ListingNormalizationRun | None:
    return session.scalars(
        select(ListingNormalizationRun)
        .join(ListingVersion, ListingVersion.id == ListingNormalizationRun.listing_version_id)
        .where(
            ListingVersion.seller_id == seller_id,
            ListingVersion.seller_listing_ref == ref,
            ListingVersion.version_number == number,
            ListingNormalizationRun.rules_version == rules_version,
        )
    ).first()


def _require_decision_tables(engine: Engine) -> None:
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            for model in (ListingDecisionRun, ListingDecisionProposal, ListingDecisionReason):
                session.execute(select(model.id).limit(0))
    except ProgrammingError as exc:
        if (missing := _not_migrated(exc)) is not None:
            raise missing from None
        raise


def decide_and_store(
    engine: Engine,
    seller_id: str,
    seller_listing_ref: str,
    version_number: int | None,
    provider: ListingDecisionProvider,
) -> DecisionStoreResult:
    """Decide one stored listing version with `provider` and append the decision."""
    stored = read_listing(engine, seller_id, seller_listing_ref, version_number)
    raw = parse_raw_listing(stored.raw_bytes)
    normalization = normalize_listing(raw)
    output_sha256 = normalization.output_sha256()
    where = (
        f"listing {seller_id!r}/{seller_listing_ref!r} version {stored.version_number} under "
        f"rules version {normalization.rules_version!r}"
    )
    recorded = [r for r in stored.runs if r.rules_version == normalization.rules_version]
    if not recorded:
        raise NormalizationNotStored(
            f"{where} has no stored normalization; submit the listing first. Nothing was written"
        )
    if recorded[0].output_sha256 != output_sha256 or (
        recorded[0].rules_digest != normalization.rules_digest
    ):
        raise NormalizationNotStored(
            f"{where}: the stored normalization differs from today's output, so no decision can "
            "be linked to it. Nothing was written"
        )

    _require_decision_tables(engine)  # before any (possibly paid) provider call
    state, outcome = decide_listing(raw, normalization, provider)  # no transaction open
    decision = route_listing(normalization, state, outcome)

    try:
        with Session(engine) as session, session.begin():
            run = _normalization_run(
                session,
                seller_id,
                seller_listing_ref,
                stored.version_number,
                normalization.rules_version,
            )
            if run is None or run.output_sha256 != output_sha256:  # append-only: unreachable
                raise NormalizationNotStored(f"{where}: the normalization run changed")
            run_id = _record(session, run, outcome, decision, state.question_set_digest)
    except ProgrammingError as exc:
        if (missing := _not_migrated(exc)) is not None:
            raise missing from None
        raise
    except IntegrityError as exc:
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        detail = f" ({name})" if name else ""
        raise StoreError(
            f"the decision violates a stored-history constraint{detail}. Nothing was written"
        ) from None
    return DecisionStoreResult(
        decision_run_id=run_id,
        seller_id=seller_id,
        seller_listing_ref=seller_listing_ref,
        version_number=stored.version_number,
        rules_version=normalization.rules_version,
        normalization_output_sha256=output_sha256,
        provider=outcome.provider,
        fallback_reason=outcome.fallback_reason,
        route=decision.route.value,
        deterministic_route=decision.deterministic_route.value,
        outcome_sha256=canonical_sha256(outcome.model_dump(mode="json")),
        decision_sha256=canonical_sha256(decision.model_dump(mode="json")),
    )


# ---- read -------------------------------------------------------------------------------


def _stored_decision(
    session: Session, row: ListingDecisionRun, run: ListingNormalizationRun
) -> StoredDecision:
    proposals = list(
        session.scalars(
            select(ListingDecisionProposal)
            .where(ListingDecisionProposal.decision_run_id == row.id)
            .order_by(ListingDecisionProposal.ordinal)
        )
    )
    probabilities: dict[int, list[StoredProbability]] = {p.id: [] for p in proposals}
    for q in session.scalars(
        select(ListingDecisionProbability)
        .where(ListingDecisionProbability.proposal_id.in_(probabilities))
        .order_by(ListingDecisionProbability.proposal_id, ListingDecisionProbability.label_ordinal)
    ):
        probabilities[q.proposal_id].append(
            StoredProbability(
                label_ordinal=q.label_ordinal,
                label=q.label,
                probability=q.probability,
                is_top=q.is_top,
            )
        )
    reasons = session.scalars(
        select(ListingDecisionReason)
        .where(ListingDecisionReason.decision_run_id == row.id)
        .order_by(ListingDecisionReason.ordinal)
    )
    return StoredDecision(
        decision_run_id=row.id,
        normalization_rules_version=run.rules_version,
        normalization_output_sha256=row.normalization_output_sha256,
        linked_output_sha256=run.output_sha256,
        question_set_version=row.question_set_version,
        question_set_digest=row.question_set_digest,
        policy_version=row.policy_version,
        policy_digest=row.policy_digest,
        route=row.route,
        deterministic_route=row.deterministic_route,
        proposal_count=row.proposal_count,
        reason_count=row.reason_count,
        outcome_sha256=row.outcome_sha256,
        decision_sha256=row.decision_sha256,
        created_at=row.created_at,
        telemetry={name: getattr(row, name) for name in OUTCOME_FIELDS},
        proposals=tuple(
            StoredProposal(
                ordinal=p.ordinal,
                decision_type=p.decision_type,
                claim_ordinal=p.claim_ordinal,
                label=p.label,
                raw_choice=p.raw_choice,
                provider_confidence=p.provider_confidence,
                gate_confidence=p.gate_confidence,
                probabilities=tuple(probabilities[p.id]),
            )
            for p in proposals
        ),
        reasons=tuple(
            StoredReason(ordinal=r.ordinal, code=r.code, route=r.route, subject=r.subject)
            for r in reasons
        ),
    )


@dataclass(frozen=True)
class StoredDecisions:
    seller_id: str
    seller_listing_ref: str
    version_number: int
    raw_sha256: str
    decisions: tuple[StoredDecision, ...]  # ordered by created time, then id


def read_decisions(
    engine: Engine, seller_id: str, seller_listing_ref: str, version_number: int | None = None
) -> StoredDecisions:
    """One stored version's decisions, as recorded, READ ONLY; each is verified."""
    stored = read_listing(engine, seller_id, seller_listing_ref, version_number)
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            pairs = session.execute(
                select(ListingDecisionRun, ListingNormalizationRun)
                .join(
                    ListingNormalizationRun,
                    ListingNormalizationRun.id == ListingDecisionRun.normalization_run_id,
                )
                .join(
                    ListingVersion, ListingVersion.id == ListingNormalizationRun.listing_version_id
                )
                .where(
                    ListingVersion.seller_id == seller_id,
                    ListingVersion.seller_listing_ref == seller_listing_ref,
                    ListingVersion.version_number == stored.version_number,
                )
                .order_by(ListingDecisionRun.created_at, ListingDecisionRun.id)
            ).all()
            decisions = tuple(_stored_decision(session, row, run) for row, run in pairs)
    except ProgrammingError as exc:
        if (missing := _not_migrated(exc)) is not None:
            raise missing from None
        raise
    for decision in decisions:
        verify_decision(decision)
    return StoredDecisions(
        seller_id=stored.seller_id,
        seller_listing_ref=stored.seller_listing_ref,
        version_number=stored.version_number,
        raw_sha256=stored.raw_sha256,
        decisions=decisions,
    )
