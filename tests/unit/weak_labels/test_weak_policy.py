"""`weak-label-policy-1` rules on the committed (public) frozen queries, gold constraints and
catalog, with SYNTHETIC signals. Nothing here is a real proposal or label."""

import pytest
from evals.contracts.constants import Label, Relation
from evals.contracts.eligibility import evaluate
from evals.contracts.models import (
    Confidence,
    ConstraintCheck,
    ConstraintStatus,
    EligibilityRecord,
    GoldField,
    InclusionReason,
    OverallStatus,
    SourceHit,
    SourceSystem,
)
from evals.weak_labels.contracts import Band, Consensus, ProposalRule, WeakSignal
from evals.weak_labels.generation import load_facts
from evals.weak_labels.policy import consensus, propose, query_facts, reranker_band


@pytest.fixture(scope="module")
def facts(catalog):
    return load_facts(with_provenance=False, catalog=catalog)


def signal(query_id, product_id, *, rank=1, size=30, systems=6) -> WeakSignal:
    hits = tuple(SourceHit(system=s, rank=1) for s in list(SourceSystem)[:systems])
    return WeakSignal(
        query_id=query_id,
        product_id=product_id,
        reranker_logit="1.0",
        reranker_rank_in_pool=rank,
        pool_size=size,
        added_by=InclusionReason.SYSTEM_UNION if hits else InclusionReason.NEGATIVE_SAME_CATEGORY,
        source_hits=hits,
    )


def run(facts, catalog, query_id, product_id, **signal_kwargs):
    query = next(q for q in facts.queries if q.query_id == query_id)
    pool_products = [
        (catalog.products[i.product_id], facts.eligibility[(i.query_id, i.product_id)])
        for i in facts.pool
        if i.query_id == query_id
    ]
    qf = query_facts(query, facts.constraints, facts.intents, pool_products)
    product = catalog.products[product_id]
    eligibility = evaluate(query_id, facts.constraints, product)
    return propose(qf, product, eligibility, signal(query_id, product_id, **signal_kwargs))


def first(catalog, predicate) -> str:
    ordered = sorted(catalog.products.values(), key=lambda p: p.product_id)
    return next(p.product_id for p in ordered if predicate(p))


def outcome(result):
    return result.label, result.relation, result.rule, result.confidence


def test_direct_match_and_category_only(facts, catalog):
    dell = first(catalog, lambda p: p.brand == "Dell" and p.category.value == "laptop")
    assert outcome(run(facts, catalog, "gq-p02", dell)) == (
        Label.RELEVANT,
        Relation.EXACT_MATCH,
        ProposalRule.DIRECT_MATCH,
        Confidence.HIGH,
    )
    headphones = first(catalog, lambda p: p.category.value == "headphones")
    assert outcome(run(facts, catalog, "gq-p01", headphones)) == (
        Label.RELEVANT,
        Relation.SATISFIES_INTENT,
        ProposalRule.CATEGORY_ONLY_MATCH,
        Confidence.HIGH,
    )


def test_category_mismatch_and_complement(facts, catalog):
    shoe = first(catalog, lambda p: p.category.value == "shoes")
    assert outcome(run(facts, catalog, "gq-p02", shoe))[:3] == (
        Label.NOT_RELEVANT,
        Relation.OFF_TARGET,
        ProposalRule.CATEGORY_MISMATCH,
    )
    # Headphones for a phone query: a complement, never relevant for primary retrieval.
    headphones = first(catalog, lambda p: p.category.value == "headphones")
    assert outcome(run(facts, catalog, "gq-p05", headphones))[:3] == (
        Label.NOT_RELEVANT,
        Relation.COMPLEMENT,
        ProposalRule.COMPLEMENT_CATEGORY,
    )


def test_brand_only_and_multiple_violations(facts, catalog):
    hp = first(catalog, lambda p: p.brand == "HP" and p.category.value == "laptop")
    assert outcome(run(facts, catalog, "gq-p02", hp, rank=15)) == (
        Label.NOT_RELEVANT,
        Relation.OFF_TARGET,
        ProposalRule.BRAND_ONLY_VIOLATION,
        Confidence.MEDIUM,
    )
    # laptop 16gb ram 512gb ssd: an 8 GB / 256 GB laptop violates two hard constraints.
    small = first(
        catalog,
        lambda p: (
            p.category.value == "laptop"
            and p.spec.get("ram_gb") == 8
            and p.spec.get("storage_gb") == 256
        ),
    )
    assert outcome(run(facts, catalog, "gq-p07", small))[:3] == (
        Label.NOT_RELEVANT,
        Relation.OFF_TARGET,
        ProposalRule.MULTIPLE_HARD_VIOLATIONS,
    )


def test_near_miss_is_a_low_confidence_substitute(facts, catalog):
    ram32 = first(catalog, lambda p: p.category.value == "laptop" and p.spec.get("ram_gb") == 32)
    assert outcome(run(facts, catalog, "gq-p19", ram32)) == (
        Label.BORDERLINE,
        Relation.SUBSTITUTE,
        ProposalRule.NEAR_MISS_SUBSTITUTE,
        Confidence.LOW,
    )
    unisex = first(
        catalog,
        lambda p: (
            p.category.value == "shoes"
            and p.subcategory == "running"
            and p.spec.get("gender") == "unisex"
        ),
    )
    assert outcome(run(facts, catalog, "gq-p12", unisex))[:2] == (
        Label.BORDERLINE,
        Relation.SUBSTITUTE,
    )


def test_series_rules(facts, catalog):
    exact = first(
        catalog, lambda p: "Lumen" in p.title and p.spec.get("processor") == "AMD Ryzen 7"
    )
    assert outcome(run(facts, catalog, "gq-p04", exact))[:3] == (
        Label.RELEVANT,
        Relation.EXACT_MATCH,
        ProposalRule.SERIES_EXACT_MATCH,
    )
    other = first(
        catalog, lambda p: "Lumen" in p.title and p.spec.get("processor") != "AMD Ryzen 7"
    )
    assert outcome(run(facts, catalog, "gq-p04", other)) == (
        Label.BORDERLINE,
        Relation.PARTIAL,
        ProposalRule.SERIES_PARTIAL,
        Confidence.LOW,
    )


def test_semantic_proposals_are_never_high(facts, catalog):
    gpu = first(catalog, lambda p: "RTX" in str(p.spec.get("gpu")))
    assert outcome(run(facts, catalog, "gq-p11", gpu)) == (
        Label.RELEVANT,
        Relation.SATISFIES_INTENT,
        ProposalRule.SEMANTIC_SUPPORTED,
        Confidence.MEDIUM,
    )
    plain = first(
        catalog,
        lambda p: (
            p.category.value == "laptop"
            and p.spec.get("gpu") is None
            and p.subcategory == "everyday"
        ),
    )
    assert outcome(run(facts, catalog, "gq-p11", plain)) == (
        Label.NOT_RELEVANT,
        Relation.OFF_TARGET,
        ProposalRule.SEMANTIC_UNSUPPORTED,
        Confidence.LOW,
    )
    for query_id in ("gq-p11", "gq-p13", "gq-p15"):
        for item in [i for i in facts.pool if i.query_id == query_id]:
            result = run(facts, catalog, query_id, item.product_id)
            if result.rule.value.startswith("semantic"):
                assert result.confidence is not Confidence.HIGH


def test_ambiguous_query_is_capped_at_medium(facts, catalog):
    phone = first(catalog, lambda p: p.brand == "Apple" and p.category.value == "phone")
    assert outcome(run(facts, catalog, "gq-p18", phone)) == (
        Label.RELEVANT,
        Relation.SATISFIES_INTENT,
        ProposalRule.AMBIGUOUS_INTERPRETATION_MATCH,
        Confidence.MEDIUM,
    )


def test_out_of_catalog_query(facts, catalog):
    headphones = first(catalog, lambda p: p.category.value == "headphones")
    assert outcome(run(facts, catalog, "gq-p20", headphones, rank=30)) == (
        Label.NOT_RELEVANT,
        Relation.OUT_OF_CATALOG,
        ProposalRule.OUT_OF_CATALOG_QUERY,
        Confidence.HIGH,
    )


def test_unknown_is_not_a_violation(facts, catalog):
    query = next(q for q in facts.queries if q.query_id == "gq-p07")
    product = next(
        p
        for p in sorted(catalog.products.values(), key=lambda p: p.product_id)
        if evaluate("gq-p07", facts.constraints, p).hard_overall is OverallStatus.SATISFIED
    )
    checks = tuple(
        ConstraintCheck(
            field=c.field,
            operator=c.operator,
            fp1_filterable=c.fp1_filterable,
            status=ConstraintStatus.UNKNOWN if c.field is GoldField.STORAGE_TYPE else c.status,
        )
        for c in evaluate("gq-p07", facts.constraints, product).checks
    )
    record = EligibilityRecord(
        query_id="gq-p07",
        product_id=product.product_id,
        checks=checks,
        hard_overall=OverallStatus.UNKNOWN,
        fp1_overall=OverallStatus.UNKNOWN,
    )
    qf = query_facts(query, facts.constraints, facts.intents, [])
    result = propose(qf, product, record, signal("gq-p07", product.product_id))
    assert outcome(result) == (
        Label.RELEVANT,
        Relation.SATISFIES_INTENT,
        ProposalRule.HARD_UNKNOWN,
        Confidence.LOW,
    )
    assert result.unknown == ("storage_type",) and result.violated == ()


def test_signal_conflicts_lower_confidence(facts, catalog):
    dell = first(catalog, lambda p: p.brand == "Dell" and p.category.value == "laptop")
    result = run(facts, catalog, "gq-p02", dell, rank=30, size=30)  # bottom third
    assert result.conflict and result.reranker_band is Band.LOW
    assert (result.label, result.confidence) == (Label.RELEVANT, Confidence.MEDIUM)
    shoe = first(catalog, lambda p: p.category.value == "shoes")
    result = run(facts, catalog, "gq-p02", shoe, rank=1, size=30, systems=6)
    assert result.conflict and result.confidence is Confidence.MEDIUM
    result = run(facts, catalog, "gq-p02", shoe, rank=1, size=30, systems=2)
    assert not result.conflict and result.confidence is Confidence.HIGH


def test_bands_and_consensus():
    assert reranker_band(signal("gq-p01", "X1", rank=10, size=30)) is Band.HIGH
    assert reranker_band(signal("gq-p01", "X1", rank=11, size=30)) is Band.MEDIUM
    assert reranker_band(signal("gq-p01", "X1", rank=21, size=30)) is Band.LOW
    assert consensus(signal("gq-p01", "X1", systems=4)) is Consensus.STRONG
    assert consensus(signal("gq-p01", "X1", systems=3)) is Consensus.PARTIAL
    assert consensus(signal("gq-p01", "X1", systems=0)) is Consensus.NONE
