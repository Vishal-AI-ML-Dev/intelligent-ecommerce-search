"""Deterministic listing routing policy `lr-1` (Milestone 14). Offline: no SDK, database,
settings or network.

`route_listing` maps one M13 normalization, its `jl-1` state and a provider outcome to exactly
one of the automated routes `auto_accept` < `accept_with_warning` < `manual_review` (FR-LST-4).
There is no rejection route: final rejection is a human decision only.

Owner-approved rules (2026-10-21, Option A), recorded in `docs/m14-progress.md`:

1. `auto_accept` is unreachable. Risk triage does not exist yet (M15) and no confidence threshold
   is justified, so every listing starts at `accept_with_warning` with `risk.not_assessed`. That
   route is a technical routing result, not permission to publish or a claim that the listing is
   safe.
2. The route is the strictest of that baseline, the deterministic M13 signals and the Jev
   escalations. Jev can only raise a route, never lower it; no confidence or probability is read.
3. A Jev fallback (failure or invalid response) keeps the deterministic route and adds
   `jev.fallback.<reason>`. The deterministic provider (Jev disabled) routes on M13 signals only.

Every contributing signal is kept as a `RoutingReason` with a stable code, its own route and a
subject (a field name or a question id, never seller text), in canonical order.
"""

import hashlib
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecommerce_search.decision.listing_models import (
    SUBCATEGORY_CATEGORY,
    ListingDecisionOutcome,
    ListingDecisionState,
    OmissionReason,
    SupportLabel,
    attribute_question_id,
    validate_outcome,
)
from ecommerce_search.decision.provider import JevFallbackReason
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.models import FindingKind, ListingNormalization, Outcome
from ecommerce_search.normalization.rules import Basis

POLICY_VERSION = "lr-1"


class Route(StrEnum):
    """The automated routes, least strict first. No rejection route exists."""

    AUTO_ACCEPT = "auto_accept"
    ACCEPT_WITH_WARNING = "accept_with_warning"
    MANUAL_REVIEW = "manual_review"


_SEVERITY = {route: rank for rank, route in enumerate(Route)}


def strictest(routes: list[Route] | tuple[Route, ...]) -> Route:
    return max(routes, key=_SEVERITY.__getitem__)


RISK_NOT_ASSESSED = "risk.not_assessed"
BASELINE_ROUTE = Route.ACCEPT_WITH_WARNING

# Every M13 finding kind and candidate outcome is mapped; a new kind fails at import.
FINDING_ROUTES: dict[FindingKind, Route] = {
    FindingKind.VALUE_CONFLICT: Route.MANUAL_REVIEW,
    FindingKind.CROSS_FIELD_CONTRADICTION: Route.MANUAL_REVIEW,
    FindingKind.INCOMPATIBLE_ATTRIBUTE: Route.MANUAL_REVIEW,
    FindingKind.INCOMPATIBLE_SUBCATEGORY: Route.MANUAL_REVIEW,
    FindingKind.BRAND_CATEGORY_MISMATCH: Route.MANUAL_REVIEW,
    FindingKind.MISSING_REQUIRED: Route.MANUAL_REVIEW,
    FindingKind.UNSUPPORTED_ATTRIBUTE: Route.ACCEPT_WITH_WARNING,
    FindingKind.COMPATIBILITY_UNCHECKED: Route.ACCEPT_WITH_WARNING,
    FindingKind.MISSING_EXPECTED: Route.ACCEPT_WITH_WARNING,
    FindingKind.EMPTY_VALUE: Route.ACCEPT_WITH_WARNING,
}
CANDIDATE_ROUTES: dict[Outcome, Route | None] = {
    Outcome.NORMALIZED: None,
    Outcome.INVALID: Route.MANUAL_REVIEW,
    Outcome.UNCERTAIN: Route.ACCEPT_WITH_WARNING,
    Outcome.UNSUPPORTED: Route.ACCEPT_WITH_WARNING,
}
QUESTION_CAP = "jl1.question_cap"  # an eligible claim was not asked, so it was not assessed

JEV_CATEGORY_DISAGREES = "jev.category_disagrees_with_seller"
JEV_INCOMPATIBLE_PAIR = "jev.incompatible_category_subcategory"
JEV_CLAIM_CONTRADICTED = "jev.claim_contradicted"
JEV_CATEGORY_TIE = "jev.category_tie"
JEV_CATEGORY_NONE = "jev.category_none"
JEV_SUBCATEGORY_TIE = "jev.subcategory_tie"
JEV_SUBCATEGORY_NONE = "jev.subcategory_none"
JEV_CLAIM_TIE = "jev.claim_tie"
JEV_FALLBACK_PREFIX = "jev.fallback."
JEV_ROUTES: dict[str, Route] = {
    JEV_CATEGORY_DISAGREES: Route.MANUAL_REVIEW,
    JEV_INCOMPATIBLE_PAIR: Route.MANUAL_REVIEW,
    JEV_CLAIM_CONTRADICTED: Route.MANUAL_REVIEW,
    JEV_CATEGORY_TIE: Route.ACCEPT_WITH_WARNING,
    JEV_CATEGORY_NONE: Route.ACCEPT_WITH_WARNING,
    JEV_SUBCATEGORY_TIE: Route.ACCEPT_WITH_WARNING,
    JEV_SUBCATEGORY_NONE: Route.ACCEPT_WITH_WARNING,
    JEV_CLAIM_TIE: Route.ACCEPT_WITH_WARNING,
}
JEV_FALLBACK_ROUTE = Route.ACCEPT_WITH_WARNING

if set(FINDING_ROUTES) != set(FindingKind) or set(CANDIDATE_ROUTES) != set(Outcome):
    raise AssertionError("lr-1 must map every M13 finding kind and candidate outcome")


def policy_as_dict() -> dict[str, object]:
    """Everything that decides a route under lr-1."""
    return {
        "policy_version": POLICY_VERSION,
        "routes": [r.value for r in Route],
        "auto_accept_reachable": False,
        "baseline": {"code": RISK_NOT_ASSESSED, "route": BASELINE_ROUTE.value},
        "m13_findings": {k.value: r.value for k, r in FINDING_ROUTES.items()},
        "m13_candidates": {o.value: r and r.value for o, r in CANDIDATE_ROUTES.items()},
        "question_cap": Route.ACCEPT_WITH_WARNING.value,
        "jev": {code: r.value for code, r in JEV_ROUTES.items()},
        "jev_not_stated": None,
        "jev_fallback": JEV_FALLBACK_ROUTE.value,
        "combine": "strictest; jev only escalates; confidence never read",
    }


def policy_digest() -> str:
    return hashlib.sha256(nr.canonical_json(policy_as_dict())).hexdigest()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RoutingReason(_Frozen):
    code: str = Field(min_length=1)
    route: Route
    subject: str | None = None  # a field name or a question id; never seller text


class RoutingDecision(_Frozen):
    policy_version: Literal["lr-1"] = POLICY_VERSION
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["deterministic", "jev"]
    jev_fallback_reason: JevFallbackReason | None = None
    route: Route
    deterministic_route: Route  # the baseline and M13 signals alone
    reasons: tuple[RoutingReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.reasons != tuple(sorted(set(self.reasons), key=_reason_key)):
            raise ValueError("reasons are unique and in canonical order")
        if RoutingReason(code=RISK_NOT_ASSESSED, route=BASELINE_ROUTE) not in self.reasons:
            raise ValueError("lr-1 always records risk.not_assessed")
        if self.route != strictest([r.route for r in self.reasons]):
            raise ValueError("the route is the strictest of its reasons")
        if Route.AUTO_ACCEPT in (self.route, self.deterministic_route):
            raise ValueError("auto_accept is unreachable under lr-1")
        if _SEVERITY[self.route] < _SEVERITY[self.deterministic_route]:
            raise ValueError("jev never lowers the deterministic route")
        if (self.jev_fallback_reason is not None) and self.provider != "jev":
            raise ValueError("only a jev outcome has a fallback reason")
        return self


class RoutingInputMismatch(ValueError):
    """The normalization, state and outcome do not belong together. No route is produced."""


def _reason_key(reason: RoutingReason) -> tuple[str, str]:
    return (reason.code, reason.subject or "")


def _deterministic_reasons(
    normalization: ListingNormalization, state: ListingDecisionState
) -> set[RoutingReason]:
    reasons = {RoutingReason(code=RISK_NOT_ASSESSED, route=BASELINE_ROUTE)}
    for f in normalization.findings:
        route = FINDING_ROUTES[f.kind]
        reasons.add(RoutingReason(code=f"m13.{f.kind.value}", route=route, subject=f.field))
    for c in normalization.candidates:
        route = CANDIDATE_ROUTES[c.outcome]
        if route is not None:
            code = f"m13.candidate_{c.outcome.value}"
            reasons.add(RoutingReason(code=code, route=route, subject=c.field))
    for o in state.omitted_candidates:
        if o.reason is OmissionReason.QUESTION_CAP:
            subject = attribute_question_id(o.ordinal)
            reasons.add(
                RoutingReason(code=QUESTION_CAP, route=Route.ACCEPT_WITH_WARNING, subject=subject)
            )
    return reasons


def _seller_values(normalization: ListingNormalization, field: str) -> set[str]:
    return {
        c.normalized_value
        for c in normalization.candidates
        if c.field == field
        and c.basis is Basis.SELLER_FIELD
        and c.outcome is Outcome.NORMALIZED
        and c.normalized_value is not None
    }


def _jev(code: str, subject: str | None = None) -> RoutingReason:
    return RoutingReason(code=code, route=JEV_ROUTES[code], subject=subject)


def _jev_reasons(
    normalization: ListingNormalization, outcome: ListingDecisionOutcome
) -> set[RoutingReason]:
    """Escalations from validated Jev proposals: labels only, never confidence."""
    if outcome.fallback_reason is not None:
        code = JEV_FALLBACK_PREFIX + outcome.fallback_reason
        return {RoutingReason(code=code, route=JEV_FALLBACK_ROUTE)}
    category, subcategory = outcome.category, outcome.subcategory
    if category is None or subcategory is None:  # unreachable: validated by the outcome model
        raise RoutingInputMismatch("a successful jev outcome has both proposals")
    reasons: set[RoutingReason] = set()
    for proposal, tie, none in (
        (category, JEV_CATEGORY_TIE, JEV_CATEGORY_NONE),
        (subcategory, JEV_SUBCATEGORY_TIE, JEV_SUBCATEGORY_NONE),
    ):
        if len(proposal.top_labels) > 1:
            reasons.add(_jev(tie))
        elif proposal.label is None:  # the unique top label is `none`
            reasons.add(_jev(none))
    if category.label is not None:
        seller = _seller_values(normalization, "category")
        if seller and category.label not in seller:
            reasons.add(_jev(JEV_CATEGORY_DISAGREES))
        if (
            subcategory.label is not None
            and SUBCATEGORY_CATEGORY[subcategory.label].value != category.label
        ):
            reasons.add(_jev(JEV_INCOMPATIBLE_PAIR))
    for p in outcome.attribute_support:
        subject = attribute_question_id(p.ordinal)
        if len(p.top_labels) > 1:
            reasons.add(_jev(JEV_CLAIM_TIE, subject))
        elif p.label == SupportLabel.CONTRADICTED.value:
            reasons.add(_jev(JEV_CLAIM_CONTRADICTED, subject))
        # `supported` and `not_stated` never escalate.
    return reasons


def route_listing(
    normalization: ListingNormalization,
    state: ListingDecisionState,
    outcome: ListingDecisionOutcome,
) -> RoutingDecision:
    """Route one listing under lr-1. Raises RoutingInputMismatch, and produces no route, when the
    inputs do not belong together."""
    m13 = state.m13
    if (
        normalization.output_sha256() != m13.output_sha256
        or normalization.rules_version != m13.rules_version
        or normalization.rules_digest != m13.rules_digest
        or normalization.taxonomy_version != m13.taxonomy_version
        or len(normalization.candidates) != state.candidate_count
    ):
        raise RoutingInputMismatch("the state was not built from this normalization")
    try:
        validate_outcome(state, outcome)
    except ValueError as exc:
        raise RoutingInputMismatch("the outcome does not answer this state") from exc

    deterministic = _deterministic_reasons(normalization, state)
    jev = _jev_reasons(normalization, outcome) if outcome.provider == "jev" else set()
    reasons = tuple(sorted(deterministic | jev, key=_reason_key))
    return RoutingDecision(
        policy_digest=policy_digest(),
        state_sha256=state.state_sha256(),
        provider=outcome.provider,
        jev_fallback_reason=outcome.fallback_reason,
        route=strictest([r.route for r in reasons]),
        deterministic_route=strictest([r.route for r in deterministic]),
        reasons=reasons,
    )
