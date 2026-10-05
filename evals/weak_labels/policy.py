"""`weak-label-policy-1`: deterministic machine label proposals. Pure; no model, no network.

Inputs per pair: the frozen query record, the query's human-authored gold constraints and
product intents, the deterministic eligibility record, the `card-1` catalog facts, and two
local signals (private, from `evals.weak_labels.signals`): the pinned cross-encoder's rank of
the product inside the query's pool, and how many of the six pooled retrieval systems returned
it within depth.

The label comes from the catalog/gold facts; the signals only lower confidence when they
disagree (`signal_conflict`). Rules, first match wins:

1. Out-of-catalog query: `not_relevant/out_of_catalog`.
2. Hard category violated: `not_relevant/complement` for an accessory category of the requested
   one (`COMPLEMENT_CATEGORIES`), else `not_relevant/off_target`. Complements are never
   relevant for primary-product retrieval.
3. Other hard violations (relevant is never proposed for a hard violation):
   two or more: `not_relevant/off_target`; brand only: `not_relevant/off_target` (medium);
   the processor only, with the query's soft series token in the title: `borderline/partial`
   (low); one near miss (`near_miss`): `borderline/substitute` (low); any other single
   violation: `not_relevant/off_target` (medium).
4. Hard unknown (no violation, a missing value): never a violation; `relevant/satisfies_intent`
   at low confidence for human review.
5. Hard satisfied (or no hard constraint): ambiguous query (interpretation category match),
   soft semantic intent (catalog evidence), soft low-price preference (pool median of the
   eligible products), soft series intent (title token), then direct match.

Confidence: semantic and ambiguous proposals are at most medium. A conflict (proposed relevant
but the cross-encoder ranks it in the bottom third of the pool; or proposed not_relevant while
the cross-encoder ranks it in the top third and at least four systems retrieved it) always lowers
the confidence one step, so a conflicting proposal is never high. `requires_human_review` is
set for every non-high or conflicting proposal.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.filtering.policy import FilterOperator
from evals.contracts.catalog import CatalogProduct, title_tokens
from evals.contracts.constants import Label, Relation
from evals.contracts.models import (
    Confidence,
    ConstraintStatus,
    EligibilityRecord,
    GoldConstraint,
    GoldField,
    Hardness,
    IntentFamily,
    OverallStatus,
    ProductIntent,
    ProductIntentKind,
    QueryRecord,
)
from evals.weak_labels.contracts import (
    CONFIDENCE_ORDER,
    Band,
    Consensus,
    ProposalRule,
    WeakSignal,
)

# An accessory category is a complement of the requested primary category.
COMPLEMENT_CATEGORIES: Mapping[Category, frozenset[Category]] = {
    Category.PHONE: frozenset({Category.HEADPHONES}),
    Category.LAPTOP: frozenset({Category.HEADPHONES}),
}
PRICE_NEAR_FACTOR = Decimal("1.10")  # at most 10% over an upper price bound
CAPACITY_NEAR_RATIOS = (Decimal(2), Decimal("0.5"))  # one capacity step up or down
NEAR_GENDER = {"women": "unisex", "men": "unisex"}
STRONG_CONSENSUS = 4
DEDICATED_GPU = re.compile(r"\b(?:nvidia|geforce|rtx|gtx|radeon rx)\b", re.IGNORECASE)
OFFICE_SUBCATEGORIES = frozenset({"business"})
OFFICE_WORD = re.compile(r"\boffice\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryFacts:
    query: QueryRecord
    hard: tuple[GoldConstraint, ...]
    soft: tuple[GoldConstraint, ...]
    intents: tuple[ProductIntent, ...]
    eligible_price_median: Decimal | None


@dataclass(frozen=True)
class Proposal:
    label: Label
    relation: Relation
    confidence: Confidence
    rule: ProposalRule
    reason: str
    reranker_band: Band
    consensus: Consensus
    conflict: bool
    violated: tuple[str, ...]
    unknown: tuple[str, ...]


def query_facts(
    query: QueryRecord,
    constraints: Sequence[GoldConstraint],
    intents: Sequence[ProductIntent],
    pool_products: Sequence[tuple[CatalogProduct, EligibilityRecord]],
) -> QueryFacts:
    own = [c for c in constraints if c.query_id == query.query_id]
    eligible = sorted(
        p.price for p, e in pool_products if e.hard_overall is OverallStatus.SATISFIED
    )
    median: Decimal | None = None
    if eligible:
        middle = len(eligible) // 2
        median = (
            eligible[middle] if len(eligible) % 2 else (eligible[middle - 1] + eligible[middle]) / 2
        )
    return QueryFacts(
        query=query,
        hard=tuple(c for c in own if c.hardness is Hardness.HARD),
        soft=tuple(c for c in own if c.hardness is Hardness.SOFT),
        intents=tuple(i for i in intents if i.query_id == query.query_id),
        eligible_price_median=median,
    )


def reranker_band(signal: WeakSignal) -> Band:
    """Tertile of the cross-encoder's rank inside the query's pool (1 = best)."""
    third = signal.pool_size / 3
    if signal.reranker_rank_in_pool <= third:
        return Band.HIGH
    if signal.reranker_rank_in_pool > 2 * third:
        return Band.LOW
    return Band.MEDIUM


def consensus(signal: WeakSignal) -> Consensus:
    systems = len(signal.source_hits)
    if systems >= STRONG_CONSENSUS:
        return Consensus.STRONG
    return Consensus.PARTIAL if systems else Consensus.NONE


def _step_down(confidence: Confidence) -> Confidence:
    return CONFIDENCE_ORDER[max(0, CONFIDENCE_ORDER.index(confidence) - 1)]


def _cap(confidence: Confidence, cap: Confidence) -> Confidence:
    return min(confidence, cap, key=CONFIDENCE_ORDER.index)


def near_miss(constraint: GoldConstraint, product: CatalogProduct) -> bool:
    """A single violated hard constraint close enough for a substitute proposal."""
    field, value = constraint.field, constraint.value
    if field is GoldField.PRICE:
        if constraint.operator is FilterOperator.LTE:
            return product.price <= constraint.price_value() * PRICE_NEAR_FACTOR
        return False
    if field in (GoldField.RAM_GB, GoldField.STORAGE_GB):
        actual = product.spec.get(field.value)
        if type(actual) is not int or type(value) is not int:
            return False
        return Decimal(actual) / Decimal(value) in CAPACITY_NEAR_RATIOS
    if field is GoldField.GENDER:
        return product.spec.get("gender") == NEAR_GENDER.get(str(value))
    return False


def _interpretation_categories(query: QueryRecord) -> frozenset[str]:
    tokens: set[str] = set()
    for text in query.interpretations:
        for token in title_tokens(text):
            tokens.add(token)
            tokens.add(token.removesuffix("s"))
    return frozenset(c.value for c in Category if c.value in tokens)


def _series_tokens(facts: QueryFacts) -> list[str]:
    return [i.value.casefold() for i in facts.intents if i.kind is ProductIntentKind.SERIES]


def _soft_value(facts: QueryFacts, field: GoldField) -> str | None:
    for c in facts.soft:
        if c.field is field:
            return str(c.value)
    return None


def _semantic(intent: str, product: CatalogProduct) -> tuple[ProposalRule, Label, Relation, str]:
    """Catalog evidence for a soft semantic intent (only `gaming` and `office` occur)."""
    if intent == "gaming":
        gpu = product.spec.get("gpu")
        if isinstance(gpu, str) and DEDICATED_GPU.search(gpu):
            return (
                ProposalRule.SEMANTIC_SUPPORTED,
                Label.RELEVANT,
                Relation.SATISFIES_INTENT,
                "Satisfies every hard gold constraint; a dedicated GPU supports the soft "
                "gaming intent.",
            )
        if product.subcategory == "performance":
            return (
                ProposalRule.SEMANTIC_PARTIAL,
                Label.BORDERLINE,
                Relation.PARTIAL,
                "Satisfies every hard gold constraint; a performance laptop without a "
                "dedicated GPU only partly supports the soft gaming intent.",
            )
        return (
            ProposalRule.SEMANTIC_UNSUPPORTED,
            Label.NOT_RELEVANT,
            Relation.OFF_TARGET,
            "Satisfies the hard gold constraints, but no catalog evidence supports the soft "
            "gaming intent.",
        )
    if intent == "office":
        if product.subcategory in OFFICE_SUBCATEGORIES or OFFICE_WORD.search(
            product.description or ""
        ):
            return (
                ProposalRule.SEMANTIC_SUPPORTED,
                Label.RELEVANT,
                Relation.SATISFIES_INTENT,
                "Satisfies every hard gold constraint; the catalog describes it for office or "
                "business use.",
            )
        return (
            ProposalRule.SEMANTIC_PARTIAL,
            Label.RELEVANT,
            Relation.SATISFIES_INTENT,
            "Satisfies every hard gold constraint; no explicit office evidence, but the soft "
            "office intent is plausibly met.",
        )
    return (
        ProposalRule.SEMANTIC_PARTIAL,
        Label.BORDERLINE,
        Relation.PARTIAL,
        f"Satisfies every hard gold constraint; no evidence rule exists for the soft "
        f"{intent} intent.",
    )


def propose(
    facts: QueryFacts,
    product: CatalogProduct,
    eligibility: EligibilityRecord,
    signal: WeakSignal,
) -> Proposal:
    """The deterministic proposal for one pair (see the module docstring)."""
    if (signal.query_id, signal.product_id) != (eligibility.query_id, eligibility.product_id):
        raise ValueError("signal and eligibility belong to different pairs")
    band, agreement = reranker_band(signal), consensus(signal)
    violated_checks = [
        c
        for c in eligibility.checks
        if c.status in (ConstraintStatus.VIOLATED, ConstraintStatus.NOT_APPLICABLE)
    ]
    violated = tuple(sorted(c.field.value for c in violated_checks))
    unknown = tuple(
        sorted(c.field.value for c in eligibility.checks if c.status is ConstraintStatus.UNKNOWN)
    )
    by_field = {c.field: c for c in facts.hard}
    cap = Confidence.HIGH
    family = facts.query.intent_family

    if family is IntentFamily.OUT_OF_CATALOG:
        rule, label, relation, confidence = (
            ProposalRule.OUT_OF_CATALOG_QUERY,
            Label.NOT_RELEVANT,
            Relation.OUT_OF_CATALOG,
            Confidence.HIGH,
        )
        reason = "The query asks for a product type the catalog does not carry."
    elif "category" in violated:
        wanted = Category(str(by_field[GoldField.CATEGORY].value))
        if product.category in COMPLEMENT_CATEGORIES.get(wanted, frozenset()):
            rule, label, relation, confidence = (
                ProposalRule.COMPLEMENT_CATEGORY,
                Label.NOT_RELEVANT,
                Relation.COMPLEMENT,
                Confidence.MEDIUM,
            )
            reason = (
                f"An accessory ({product.category.value}) of the requested {wanted.value}; a "
                "complement is not relevant for primary-product retrieval."
            )
        else:
            rule, label, relation, confidence = (
                ProposalRule.CATEGORY_MISMATCH,
                Label.NOT_RELEVANT,
                Relation.OFF_TARGET,
                Confidence.HIGH,
            )
            reason = f"Wrong category: the query asks for {wanted.value}."
    elif violated:
        listed = ", ".join(violated)
        single = violated_checks[0] if len(violated_checks) == 1 else None
        series = _series_tokens(facts)
        if single is None:
            rule, label, relation, confidence = (
                ProposalRule.MULTIPLE_HARD_VIOLATIONS,
                Label.NOT_RELEVANT,
                Relation.OFF_TARGET,
                Confidence.HIGH,
            )
            reason = f"Right category, but violates several hard gold constraints ({listed})."
        elif single.field is GoldField.BRAND:
            rule, label, relation, confidence = (
                ProposalRule.BRAND_ONLY_VIOLATION,
                Label.NOT_RELEVANT,
                Relation.OFF_TARGET,
                Confidence.MEDIUM,
            )
            reason = "Right category and specifications, but a different brand than requested."
        elif (
            single.field is GoldField.PROCESSOR
            and series
            and any(s in title_tokens(product.title) for s in series)
        ):
            rule, label, relation, confidence = (
                ProposalRule.SERIES_PARTIAL,
                Label.BORDERLINE,
                Relation.PARTIAL,
                Confidence.LOW,
            )
            reason = "The requested series, but not the requested processor (a hard constraint)."
        elif near_miss(by_field[single.field], product):
            rule, label, relation, confidence = (
                ProposalRule.NEAR_MISS_SUBSTITUTE,
                Label.BORDERLINE,
                Relation.SUBSTITUTE,
                Confidence.LOW,
            )
            reason = (
                f"Misses one hard gold constraint ({listed}) by a small margin; a possible "
                "substitute."
            )
        else:
            rule, label, relation, confidence = (
                ProposalRule.SINGLE_HARD_VIOLATION,
                Label.NOT_RELEVANT,
                Relation.OFF_TARGET,
                Confidence.MEDIUM,
            )
            reason = f"Right category, but violates the hard gold constraint {listed}."
    elif eligibility.hard_overall is OverallStatus.UNKNOWN:
        rule, label, relation, confidence = (
            ProposalRule.HARD_UNKNOWN,
            Label.RELEVANT,
            Relation.SATISFIES_INTENT,
            Confidence.LOW,
        )
        reason = (
            f"No hard violation, but the catalog does not state {', '.join(unknown)}; unknown "
            "is not treated as a violation."
        )
    else:
        semantic = _soft_value(facts, GoldField.SEMANTIC_INTENT)
        preference = _soft_value(facts, GoldField.PRICE_PREFERENCE)
        series = _series_tokens(facts)
        if family is IntentFamily.AMBIGUOUS:
            cap = Confidence.MEDIUM
            if product.category.value in _interpretation_categories(facts.query):
                rule, label, relation, confidence = (
                    ProposalRule.AMBIGUOUS_INTERPRETATION_MATCH,
                    Label.RELEVANT,
                    Relation.SATISFIES_INTENT,
                    Confidence.MEDIUM,
                )
                reason = (
                    "Matches the hard constraints and one recorded interpretation of an "
                    "ambiguous query."
                )
            else:
                rule, label, relation, confidence = (
                    ProposalRule.AMBIGUOUS_OTHER_CATEGORY,
                    Label.BORDERLINE,
                    Relation.PARTIAL,
                    Confidence.LOW,
                )
                reason = "Matches the hard constraints but none of the recorded interpretations."
        elif semantic is not None:
            cap = Confidence.MEDIUM
            rule, label, relation, reason = _semantic(semantic, product)
            confidence = (
                Confidence.MEDIUM if rule is ProposalRule.SEMANTIC_SUPPORTED else Confidence.LOW
            )
        elif preference == "low" and facts.eligible_price_median is not None:
            if product.price <= facts.eligible_price_median:
                rule, label, relation, confidence = (
                    ProposalRule.PRICE_PREFERENCE_MET,
                    Label.RELEVANT,
                    Relation.SATISFIES_INTENT,
                    Confidence.MEDIUM,
                )
                reason = (
                    "Satisfies every hard gold constraint and is among the cheaper eligible "
                    "options."
                )
            else:
                rule, label, relation, confidence = (
                    ProposalRule.PRICE_PREFERENCE_UNMET,
                    Label.BORDERLINE,
                    Relation.PARTIAL,
                    Confidence.LOW,
                )
                reason = (
                    "Satisfies every hard gold constraint but is among the pricier eligible "
                    "options."
                )
        elif series:
            if any(s in title_tokens(product.title) for s in series):
                rule, label, relation, confidence = (
                    ProposalRule.SERIES_EXACT_MATCH,
                    Label.RELEVANT,
                    Relation.EXACT_MATCH,
                    Confidence.HIGH,
                )
                reason = "Satisfies every hard gold constraint and the requested series."
            else:
                rule, label, relation, confidence = (
                    ProposalRule.SERIES_ABSENT,
                    Label.RELEVANT,
                    Relation.SATISFIES_INTENT,
                    Confidence.MEDIUM,
                )
                reason = "Satisfies every hard gold constraint, but not the soft series intent."
        elif not facts.hard:
            rule, label, relation, confidence = (
                ProposalRule.NO_HARD_CONSTRAINTS,
                Label.BORDERLINE,
                Relation.PARTIAL,
                Confidence.LOW,
            )
            reason = "The query has no hard gold constraint to check against."
        elif all(c.field is GoldField.CATEGORY for c in facts.hard):
            rule, label, relation, confidence = (
                ProposalRule.CATEGORY_ONLY_MATCH,
                Label.RELEVANT,
                Relation.SATISFIES_INTENT,
                Confidence.HIGH,
            )
            reason = "In the requested category; the query states no other constraint."
        else:
            rule, label, relation, confidence = (
                ProposalRule.DIRECT_MATCH,
                Label.RELEVANT,
                Relation.EXACT_MATCH,
                Confidence.HIGH,
            )
            reason = "Satisfies every hard gold constraint of the query."

    conflict = (label is Label.RELEVANT and band is Band.LOW) or (
        label is Label.NOT_RELEVANT and band is Band.HIGH and agreement is Consensus.STRONG
    )
    confidence = _cap(confidence, cap)
    if conflict:
        confidence = _step_down(confidence)
    return Proposal(
        label=label,
        relation=relation,
        confidence=confidence,
        rule=rule,
        reason=reason,
        reranker_band=band,
        consensus=agreement,
        conflict=conflict,
        violated=violated,
        unknown=unknown,
    )
