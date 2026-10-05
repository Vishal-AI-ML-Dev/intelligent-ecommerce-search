"""Deterministic pool assembly (pool-1). The frozen pilot queries and gold constraints are read
from a temporary copy; every source response is SYNTHETIC (`pool_support.fake_sources`)."""

import random
from dataclasses import replace
from types import MappingProxyType

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.constants import (
    ELIGIBILITY_COMPLETION_CAP,
    MAX_POOL_SIZE_PER_QUERY,
    NEGATIVES_PER_QUERY,
)
from evals.contracts.eligibility import evaluate, hard_constraints, summarize
from evals.contracts.models import (
    CompletionStatus,
    InclusionReason,
    OverallStatus,
    PoolItem,
)
from evals.contracts.pooling import (
    FetchedSource,
    assemble_pool,
    negative_order_key,
    select_negatives,
    sources_from_provenance,
)
from evals.contracts.validation import (
    PoolCapExceeded,
    blind_order_key,
    check_pool_cap,
    validate_eligibility,
    validate_pool,
    validate_provenance,
)
from evals.runners import build_pool
from pool_support import context_for, copy_queries_stage, fake_sources


@pytest.fixture(scope="module")
def frozen(tmp_path_factory, catalog):
    root, target = copy_queries_stage(tmp_path_factory.mktemp("pooling"))
    return build_pool.load_frozen_queries(target, allowed_root=root, catalog=catalog)


@pytest.fixture(scope="module")
def query_ids(frozen):
    return sorted(q.query_id for q in frozen.queries)


@pytest.fixture(scope="module")
def build(frozen, catalog, query_ids):
    return assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        fake_sources(query_ids, catalog),
        context_for(frozen, catalog),
    )


def _reasons(build, query_id):
    return {e.product_id: e.added_by for e in build.provenance.entries if e.query_id == query_id}


def _summary(build, query_id):
    return next(s for s in build.report.queries if s.query_id == query_id)


def test_the_build_satisfies_every_pool_validator(build, frozen, catalog):
    check_pool_cap(build.pool, build.provenance.entries)
    validate_pool(frozen.queries, build.pool, catalog)
    validate_provenance(build.pool, build.provenance.entries, frozen.constraints, catalog)
    validate_eligibility(build.pool, frozen.constraints, build.eligibility)
    assert build.report.total_pool_items == len(build.pool)


def test_the_union_is_deduplicated_by_product_id(build, catalog, query_ids):
    sources = fake_sources(query_ids, catalog)
    for query_id in query_ids:
        union = {p for s in sources if s.run.query_id == query_id for p in s.run.product_ids}
        reasons = _reasons(build, query_id)
        assert {p for p, r in reasons.items() if r is InclusionReason.SYSTEM_UNION} == union
        assert _summary(build, query_id).union_size == len(union)
    pairs = [(i.query_id, i.product_id) for i in build.pool]
    assert len(pairs) == len(set(pairs))


def test_assembly_is_invariant_to_input_order(frozen, catalog, query_ids, build):
    sources = fake_sources(query_ids, catalog)
    for seed in range(3):
        rng = random.Random(seed)  # noqa: S311 - a test permutation, not cryptography
        shuffled = sources[:]
        rng.shuffle(shuffled)
        queries = frozen.queries[:]
        rng.shuffle(queries)
        constraints = frozen.constraints[:]
        rng.shuffle(constraints)
        again = assemble_pool(queries, constraints, catalog, shuffled, context_for(frozen, catalog))
        assert build_pool.run_files(again) == build_pool.run_files(build)


def test_repeated_assembly_is_byte_identical(frozen, catalog, query_ids, build):
    again = assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        fake_sources(query_ids, catalog),
        context_for(frozen, catalog),
    )
    assert build_pool.run_files(again) == build_pool.run_files(build)


def test_eligibility_completion_is_all_or_nothing(build, frozen, catalog, query_ids):
    for query_id in query_ids:
        summary = summarize(query_id, frozen.constraints, catalog)
        hard = hard_constraints(query_id, frozen.constraints)
        reasons = _summary(build, query_id)
        pooled = set(_reasons(build, query_id))
        completed = {
            p
            for p, r in _reasons(build, query_id).items()
            if r is InclusionReason.ELIGIBILITY_COMPLETION
        }
        eligible = set(summary.satisfied)
        if not hard:
            assert reasons.completion is CompletionStatus.NO_HARD_CONSTRAINTS and not completed
        elif not eligible:
            assert reasons.completion is CompletionStatus.ZERO_ELIGIBLE and not completed
        elif len(eligible) > ELIGIBILITY_COMPLETION_CAP:
            # Never truncated: nothing is added, and not every eligible product is pooled.
            assert reasons.completion is CompletionStatus.OVER_COMPLETION_CAP and not completed
        else:
            assert reasons.completion is CompletionStatus.APPLIED
            assert eligible <= pooled
            assert completed == eligible - {
                p for p, r in _reasons(build, query_id).items() if r is InclusionReason.SYSTEM_UNION
            }
        assert reasons.completion_added == len(completed) <= ELIGIBILITY_COMPLETION_CAP


def test_completion_actually_adds_missing_eligible_products(build):
    # gq-p04 has exactly one hard-eligible product; the synthetic sources miss it.
    reasons = _reasons(build, "gq-p04")
    assert reasons["SYN-LAP-0003"] is InclusionReason.ELIGIBILITY_COMPLETION
    assert _summary(build, "gq-p04").completion_added == 1


def test_negatives_are_deterministic_violating_and_never_eligible_or_unknown(
    build, frozen, catalog, query_ids
):
    for query_id in query_ids:
        hard = hard_constraints(query_id, frozen.constraints)
        records = {e.product_id: e for e in build.eligibility if e.query_id == query_id}
        reasons = _reasons(build, query_id)
        negatives = [
            p
            for p, r in reasons.items()
            if r
            in (InclusionReason.NEGATIVE_SAME_CATEGORY, InclusionReason.NEGATIVE_OTHER_CATEGORY)
        ]
        summary = _summary(build, query_id)
        assert len(negatives) + summary.negative_shortfall == NEGATIVES_PER_QUERY
        for product_id in negatives:
            status = records[product_id].hard_overall
            if hard:
                assert status is OverallStatus.VIOLATED
            else:
                assert status is OverallStatus.NO_CONSTRAINTS
        # The chosen negatives are the first candidates of their tier in pool-1 order.
        pooled_before = {
            p
            for p, r in reasons.items()
            if r in (InclusionReason.SYSTEM_UNION, InclusionReason.ELIGIBILITY_COMPLETION)
        }
        statuses = {
            p: evaluate(query_id, hard, product).hard_overall
            for p, product in catalog.products.items()
        }
        expected = select_negatives(query_id, hard, statuses, pooled_before, catalog)
        assert set(negatives) == set(expected.same_category) | set(expected.other_category)


def test_query_specific_negative_rules(build, catalog):
    # Category-only query: every same-category product is eligible, so all four are other tier.
    p01 = _summary(build, "gq-p01")
    assert (p01.negatives_same_category, p01.negatives_other_category) == (0, 4)
    # gq-p18 (brand Apple, no category): every negative violates brand=Apple.
    for product_id, reason in _reasons(build, "gq-p18").items():
        if reason.value.startswith("negative"):
            assert catalog.products[product_id].brand != "Apple"
            assert reason is InclusionReason.NEGATIVE_OTHER_CATEGORY
    # gq-p20 (out of catalog, no constraints): ordinary catalog products as probes.
    p20 = _summary(build, "gq-p20")
    assert p20.completion is CompletionStatus.NO_HARD_CONSTRAINTS
    assert p20.negatives_other_category == 4 and p20.eligible_count is None
    # A query with a hard category gets two same-category negatives that violate another field.
    p02 = _summary(build, "gq-p02")
    assert (p02.negatives_same_category, p02.negatives_other_category) == (2, 2)
    for product_id, reason in _reasons(build, "gq-p02").items():
        if reason is InclusionReason.NEGATIVE_SAME_CATEGORY:
            product = catalog.products[product_id]
            assert product.category.value == "laptop" and product.brand != "Dell"


def test_negative_selection_orders_fills_and_reports_shortfall(catalog, frozen):
    hard = hard_constraints("gq-p02", frozen.constraints)  # laptop + Dell
    laptops = sorted(p for p, x in catalog.products.items() if x.category.value == "laptop")
    others = sorted(p for p, x in catalog.products.items() if x.category.value != "laptop")
    non_dell = [p for p in laptops if catalog.products[p].brand != "Dell"]
    statuses = {p: OverallStatus.VIOLATED for p in non_dell + others}
    # Only one other-category candidate left: the same tier fills the gap.
    pooled = set(others[1:])
    chosen = select_negatives("gq-p02", hard, statuses, pooled, catalog)
    assert len(chosen.same_category) == 3 and chosen.other_category == (others[0],)
    assert chosen.shortfall == 0
    ordered = sorted(non_dell, key=lambda p: negative_order_key("gq-p02", p))
    assert chosen.same_category == tuple(ordered[:3])
    # Nothing left anywhere: a reported shortfall, never an invented product.
    none_left = select_negatives("gq-p02", hard, statuses, set(statuses), catalog)
    assert none_left.same_category == none_left.other_category == ()
    assert none_left.shortfall == NEGATIVES_PER_QUERY
    # Unknown and satisfied products are never negatives.
    mixed = {p: OverallStatus.UNKNOWN for p in non_dell} | {
        p: OverallStatus.SATISFIED for p in others
    }
    empty = select_negatives("gq-p02", hard, mixed, set(), catalog)
    assert empty.shortfall == NEGATIVES_PER_QUERY


def test_blind_order_is_independent_of_sources(build):
    for query_id in {i.query_id for i in build.pool}:
        items = sorted(
            (i for i in build.pool if i.query_id == query_id), key=lambda i: i.blind_position
        )
        assert [i.product_id for i in items] == sorted(
            (i.product_id for i in items), key=lambda p: blind_order_key(query_id, p)
        )


def test_public_pool_carries_no_provenance(build):
    assert set(PoolItem.model_fields) == {
        "schema_version",
        "query_id",
        "product_id",
        "product_content_sha256",
        "card_sha256",
        "blind_position",
    }
    text = (build.pool_bytes() + build.eligibility_bytes() + build.report_bytes()).decode()
    for forbidden in (
        '"rank"',
        '"score"',
        '"sources"',
        '"added_by"',
        "system_union",
        "eligibility_completion",
        'negative_same_category"',
        'negative_other_category"',
        "applied_filters",
        "parser",
        "seller",
        "availability",
        "rating",
        "review_count",
    ):
        assert forbidden not in text


def test_the_report_has_only_aggregate_counts(build):
    text = build.report_bytes().decode()
    assert "SYN-" not in text


def test_a_pool_over_the_cap_is_refused_and_never_trimmed(frozen, catalog, query_ids):
    sources = fake_sources(query_ids, catalog, disjoint=True)  # six disjoint 20-item lists
    big = assemble_pool(
        frozen.queries, frozen.constraints, catalog, sources, context_for(frozen, catalog)
    )
    sizes = {s.query_id: s.pool_size for s in big.report.queries}
    assert all(size > MAX_POOL_SIZE_PER_QUERY for size in sizes.values())
    assert not big.report.all_within_cap
    with pytest.raises(PoolCapExceeded) as raised:
        check_pool_cap(big.pool, big.provenance.entries)
    details = {d.query_id: d for d in raised.value.details}
    assert set(details) == set(query_ids)
    assert dict(details["gq-p01"].union_by_system)["v0_lexical"] == 20
    assert "SYN-" not in str(raised.value)
    assert len(big.pool) == sum(sizes.values())  # nothing was trimmed


def test_provenance_reassembles_the_same_bytes(build, frozen, catalog):
    again = assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        sources_from_provenance(build.provenance),
        build.provenance.run_context,
    )
    assert build_pool.run_files(again) == build_pool.run_files(build)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "unknown_product", "unknown_query"])
def test_bad_source_inputs_are_refused(frozen, catalog, query_ids, problem):
    sources = fake_sources(query_ids, catalog)
    if problem == "missing":
        sources = sources[1:]
    elif problem == "duplicate":
        sources = [*sources, sources[0]]
    elif problem == "unknown_product":
        run = sources[0].run.model_copy(update={"product_ids": ("NOPE-0001",)})
        sources[0] = FetchedSource(run=run, scores=("1.0",))
    else:
        run = sources[0].run.model_copy(update={"query_id": "gq-p99"})
        sources[0] = FetchedSource(run=run, scores=sources[0].scores)
    with pytest.raises(ContractError):
        assemble_pool(
            frozen.queries, frozen.constraints, catalog, sources, context_for(frozen, catalog)
        )


def test_a_changed_product_card_or_content_is_detected(build, frozen, catalog):
    item = build.pool[0]
    product = catalog.products[item.product_id]
    for changed in (
        replace(product, title=product.title + " X"),
        replace(product, content_sha256="0" * 64),
    ):
        products = dict(catalog.products)
        products[item.product_id] = changed
        mutated = replace(catalog, products=MappingProxyType(products))
        with pytest.raises(ContractError, match="hash differs"):
            validate_pool(frozen.queries, build.pool, mutated)


def test_the_provenance_bundle_is_self_consistent(build):
    from evals.contracts.models import PoolProvenance
    from pydantic import ValidationError

    body = build.provenance.model_dump(mode="json")
    with pytest.raises(ValidationError, match="run_id"):
        PoolProvenance.model_validate({**body, "run_id": "0" * 16})
    entries = body["entries"]
    first = next(i for i, e in enumerate(entries) if e["sources"])
    broken = [dict(e) for e in entries]
    broken[first] = {**broken[first], "sources": broken[first]["sources"][1:]}
    if broken[first]["sources"] or broken[first]["added_by"] != "system_union":
        with pytest.raises(ValidationError):
            PoolProvenance.model_validate({**body, "entries": broken})
    with pytest.raises(ValidationError, match="source hit has no provenance entry"):
        PoolProvenance.model_validate({**body, "entries": entries[1:]})
    # The run identity carries no time, path or database name.
    text = build.provenance_bytes().decode()
    for forbidden in ("ecommerce_search_bench", ":\\\\", "/home/", "/Users/", "generated_at"):
        assert forbidden not in text
