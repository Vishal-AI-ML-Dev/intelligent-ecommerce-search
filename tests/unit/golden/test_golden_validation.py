"""Cross-record validators. Products come from the committed seed (read offline); every query,
constraint, pool and judgement here is a synthetic test fixture, not Golden Dataset content."""

from datetime import timedelta

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.constants import Label
from evals.contracts.models import (
    ConstraintStatus,
    GoldField,
    Hardness,
    LabelChange,
    LabelChangeKind,
    Partition,
    PartitionAssignment,
    ProductIntent,
    ProductIntentKind,
    ProvenanceEntry,
    ReviewReason,
    Round,
    SourceHit,
    SourceSystem,
)
from evals.contracts.review_sets import (
    build_adjudication_queue,
    build_consistency_sample,
    derive_final_judgements,
)
from evals.contracts.validation import (
    PoolCapExceeded,
    check_pool_cap,
    validate_adjudication_round,
    validate_consistency_round,
    validate_eligibility,
    validate_final_judgements,
    validate_gold_constraints,
    validate_judgements_against_eligibility,
    validate_partitions,
    validate_pool,
    validate_primary_round,
    validate_product_intents,
    validate_provenance,
    validate_queries,
    validate_single_annotator,
)
from golden_support import HANDLE, T0, after_delay, eligibility, gold, judgement, pool_for, query

from ecommerce_search.filtering.policy import FilterOperator

EQ, LTE, GTE = FilterOperator.EQ, FilterOperator.LTE, FilterOperator.GTE
SEAL = "d" * 64
LAPTOPS = [f"SYN-LAP-{n:04d}" for n in range(1, 9)]


def test_catalog_index_matches_the_committed_seed(catalog):
    assert len(catalog.products) == 240
    assert catalog.dataset_id == "synthetic-seed" and catalog.dataset_version == "1"
    assert catalog.checksum_sha256 == (
        "2c1c5fa43eec5f7346df40c17e57c07f6594ab1833c5d9d4c862e6d2e8bbbbc8"
    )
    assert "AMD Ryzen 7" in catalog.processors()
    assert catalog.has_title_token("lumen") and not catalog.has_title_token("macbook")


# ---- queries and partitions -----------------------------------------------------------------


def test_query_ids_and_texts_are_unique():
    with pytest.raises(ContractError, match="texts must be unique"):
        validate_queries([query("gq-p01", "Dell Laptop"), query("gq-t01", "dell laptop")])
    with pytest.raises(ContractError, match="ids must be unique"):
        validate_queries([query("gq-p01", "a"), query("gq-p01", "b")])


def test_partitions_cover_queries_exactly():
    queries = [query("gq-p01", "a"), query("gq-t01", "b")]
    ok = [
        PartitionAssignment(query_id="gq-p01", partition=Partition.PILOT_DEV),
        PartitionAssignment(query_id="gq-t01", partition=Partition.TEST),
    ]
    validate_partitions(queries, ok)
    with pytest.raises(ContractError, match="cover exactly"):
        validate_partitions(queries, ok[:1])
    with pytest.raises(ContractError, match="exactly one partition"):
        validate_partitions(queries, [*ok, ok[0]])


# ---- gold constraints and product intents ---------------------------------------------------


def test_lumen_example_hard_constraints_and_soft_intent(catalog):  # D14 shape
    queries = [query("gq-p04", "lenovo lumen ryzen 7 laptop", family="exact_product")]
    constraints = [
        gold("gq-p04", GoldField.CATEGORY, "laptop"),
        gold("gq-p04", GoldField.BRAND, "Lenovo"),
        gold("gq-p04", GoldField.PROCESSOR, "AMD Ryzen 7"),
    ]
    validate_gold_constraints(queries, constraints, catalog)
    intent = ProductIntent(
        query_id="gq-p04",
        kind=ProductIntentKind.SERIES,
        value="Lumen",
        annotator_handle=HANDLE,
        recorded_at=T0,
    )
    validate_product_intents(queries, [intent], catalog)
    unknown = intent.model_copy(update={"value": "Zenbook"})
    with pytest.raises(ContractError, match="not a token"):
        validate_product_intents(queries, [unknown], catalog)


@pytest.mark.parametrize(
    ("constraints", "message"),
    [
        (
            [gold("gq-p01", GoldField.CATEGORY, "shoes"), gold("gq-p01", GoldField.RAM_GB, 8)],
            "does not apply",
        ),
        (
            [
                gold("gq-p01", GoldField.CATEGORY, "phone"),
                gold("gq-p01", GoldField.SUBCATEGORY, "running"),
            ],
            "is not a phone",
        ),
        ([gold("gq-p01", GoldField.PROCESSOR, "Intel Core i9")], "not in the catalog"),
        (
            [
                gold("gq-p01", GoldField.STORAGE_TYPE, "HDD"),
                gold("gq-p01", GoldField.STORAGE_INTERFACE, "NVME"),
            ],
            "requires storage_type SSD",
        ),
        (
            [
                gold("gq-p01", GoldField.PRICE, "50000.00", operator=GTE),
                gold("gq-p01", GoldField.PRICE, "40000.00", operator=LTE),
            ],
            "minimum price exceeds",
        ),
        (
            [gold("gq-p01", GoldField.BRAND, "HP"), gold("gq-p01", GoldField.BRAND, "Dell")],
            "one constraint per",
        ),
        ([gold("gq-p99", GoldField.BRAND, "HP")], "unknown query"),
    ],
)
def test_invalid_gold_constraint_sets(catalog, constraints, message):
    with pytest.raises(ContractError, match=message):
        validate_gold_constraints([query("gq-p01", "a query")], constraints, catalog)


def test_zero_eligible_constraints_are_allowed(catalog):
    # Gold values need not exist in the catalog (for example a 64 GB laptop).
    validate_gold_constraints(
        [query("gq-p19", "laptop 64gb ram")],
        [gold("gq-p19", GoldField.CATEGORY, "laptop"), gold("gq-p19", GoldField.RAM_GB, 64)],
        catalog,
    )


# ---- pool, cap and provenance -----------------------------------------------------------------


def test_valid_pool(catalog):
    validate_pool([query("gq-p01", "a")], pool_for("gq-p01", LAPTOPS, catalog), catalog)


def test_pool_refuses_wrong_hash_unknown_product_and_wrong_order(catalog):
    queries = [query("gq-p01", "a")]
    pool = pool_for("gq-p01", LAPTOPS, catalog)
    with pytest.raises(ContractError, match="content hash differs"):
        validate_pool(
            queries,
            [pool[0].model_copy(update={"product_content_sha256": "0" * 64}), *pool[1:]],
            catalog,
        )
    with pytest.raises(ContractError, match="unknown product"):
        validate_pool(
            queries, [*pool, pool[0].model_copy(update={"product_id": "NOPE-1"})], catalog
        )
    swapped = [
        pool[0].model_copy(update={"blind_position": 2}),
        pool[1].model_copy(update={"blind_position": 1}),
        *pool[2:],
    ]
    with pytest.raises(ContractError, match="blind positions"):
        validate_pool(queries, swapped, catalog)
    with pytest.raises(ContractError, match="duplicate pool items"):
        validate_pool(queries, [*pool, pool[0]], catalog)


def test_pool_cap_refuses_61_and_reports_source_counts(catalog):
    products = sorted(catalog.products)[:61]
    pool = pool_for("gq-p01", products, catalog)
    check_pool_cap(pool[:60])  # exactly at the cap is allowed
    provenance = [
        ProvenanceEntry(
            query_id="gq-p01",
            product_id=p,
            added_by="system_union",
            sources=(SourceHit(system=SourceSystem.DENSE_ONLY, rank=1 + i % 20),),
        )
        for i, p in enumerate(products)
    ]
    with pytest.raises(PoolCapExceeded) as raised:
        check_pool_cap(pool, provenance)
    (detail,) = raised.value.details
    assert detail.query_id == "gq-p01" and detail.pool_size == 61
    assert dict(detail.union_by_system)["dense_only"] == 61
    assert dict(detail.by_inclusion) == {"system_union": 61}
    assert "annotation export refused" in str(raised.value)
    assert len(pool) == 61  # nothing was trimmed


def _prov(product_id, added_by, rank=None):
    sources = () if rank is None else (SourceHit(system=SourceSystem.V0_LEXICAL, rank=rank),)
    return ProvenanceEntry(
        query_id="gq-p01", product_id=product_id, added_by=added_by, sources=sources
    )


def test_provenance_rules(catalog):
    laptops = sorted(p for p in catalog.products if p.startswith("SYN-LAP"))[:30]
    phone = "SYN-PHN-0001"
    constraints = [gold("gq-p01", GoldField.CATEGORY, "laptop")]
    pool = pool_for("gq-p01", [*laptops[:3], phone], catalog)
    ok = [
        _prov(laptops[0], "system_union", 1),
        _prov(laptops[1], "eligibility_completion"),
        _prov(laptops[2], "negative_same_category"),
        _prov(phone, "negative_other_category"),
    ]
    validate_provenance(pool, ok, constraints, catalog)
    with pytest.raises(ContractError, match="cover exactly"):
        validate_provenance(pool, ok[:3], constraints, catalog)
    wrong = [*ok[:3], _prov(phone, "negative_same_category")]
    with pytest.raises(ContractError, match="same-category negative"):
        validate_provenance(pool, wrong, constraints, catalog)
    many = pool_for("gq-p01", laptops[:26], catalog)
    with pytest.raises(ContractError, match="more than 25 completions"):
        validate_provenance(
            many, [_prov(p, "eligibility_completion") for p in laptops[:26]], constraints, catalog
        )
    five = pool_for("gq-p01", laptops[:5], catalog)
    with pytest.raises(ContractError, match="more than 4 negatives"):
        validate_provenance(
            five, [_prov(p, "negative_same_category") for p in laptops[:5]], constraints, catalog
        )


# ---- eligibility and judgements -----------------------------------------------------------------


def test_eligibility_checks_exactly_the_hard_constraints(catalog):
    pool = pool_for("gq-p12", ["SYN-SHO-0002"], catalog)
    constraints = [
        gold("gq-p12", GoldField.CATEGORY, "shoes"),
        gold("gq-p12", GoldField.GENDER, "women"),
        gold("gq-p12", GoldField.SUBCATEGORY, "running", hardness=Hardness.SOFT),
    ]
    hard_only = eligibility(
        "gq-p12",
        "SYN-SHO-0002",
        [
            (GoldField.CATEGORY, EQ, ConstraintStatus.SATISFIED),
            (GoldField.GENDER, EQ, ConstraintStatus.SATISFIED),
        ],
    )
    validate_eligibility(pool, constraints, [hard_only])
    with_soft = eligibility(
        "gq-p12",
        "SYN-SHO-0002",
        [
            (GoldField.CATEGORY, EQ, ConstraintStatus.SATISFIED),
            (GoldField.GENDER, EQ, ConstraintStatus.SATISFIED),
            (GoldField.SUBCATEGORY, EQ, ConstraintStatus.SATISFIED),
        ],
    )
    with pytest.raises(ContractError, match="differ from the hard constraints"):
        validate_eligibility(pool, constraints, [with_soft])
    with pytest.raises(ContractError, match="cover exactly the pool"):
        validate_eligibility(pool, constraints, [])


def test_hard_violation_is_never_relevant():
    elig = [
        eligibility("gq-p10", "P-violate", [(GoldField.PRICE, LTE, ConstraintStatus.VIOLATED)]),
        eligibility("gq-p10", "P-unknown", [(GoldField.RAM_GB, EQ, ConstraintStatus.UNKNOWN)]),
    ]
    validate_judgements_against_eligibility(
        [judgement("gq-p10", "P-violate", Label.BORDERLINE)], elig
    )
    with pytest.raises(ContractError, match="hard constraint is violated"):
        validate_judgements_against_eligibility([judgement("gq-p10", "P-violate")], elig)
    with pytest.raises(ContractError, match="needs a rationale"):
        validate_judgements_against_eligibility([judgement("gq-p10", "P-unknown")], elig)
    validate_judgements_against_eligibility(
        [judgement("gq-p10", "P-unknown", rationale="RAM not stated; title implies a match.")], elig
    )


def test_primary_round_must_cover_the_pool_once(catalog):
    pool = pool_for("gq-p01", LAPTOPS[:3], catalog)
    primaries = [judgement("gq-p01", p) for p in LAPTOPS[:3]]
    validate_primary_round(pool, primaries)
    with pytest.raises(ContractError, match="incomplete or foreign"):
        validate_primary_round(pool, primaries[:2])
    with pytest.raises(ContractError, match="expected round primary"):
        validate_primary_round(
            pool, [*primaries[:2], judgement("gq-p01", LAPTOPS[2], round_=Round.CONSISTENCY)]
        )


def test_single_annotator_is_enforced():  # D5 final
    validate_single_annotator([judgement("gq-p01", "P1")], [judgement("gq-p01", "P2")])
    with pytest.raises(ContractError, match="exactly one annotator"):
        validate_single_annotator(
            [judgement("gq-p01", "P1"), judgement("gq-p01", "P2", handle="ann-2")]
        )


# ---- consistency round ------------------------------------------------------------------------


def _consistency_setup():
    primaries = [
        judgement("gq-p01", f"P{i:02d}", Label.RELEVANT if i % 2 else Label.NOT_RELEVANT)
        for i in range(10)
    ]
    sample = build_consistency_sample(primaries, SEAL, T0)
    sampled = [(i.query_id, i.product_id) for i in sample.items]
    return primaries, sample, sampled


def test_consistency_round_after_72_hours_is_valid():
    primaries, sample, sampled = _consistency_setup()
    repeats = [judgement(q, p, round_=Round.CONSISTENCY, at=after_delay(72)) for q, p in sampled]
    validate_consistency_round(primaries, sample, repeats, SEAL, T0)


def test_consistency_round_refuses_early_foreign_and_other_handle():
    primaries, sample, sampled = _consistency_setup()
    q, p = sampled[0]
    early = after_delay(72) - timedelta(microseconds=1)
    with pytest.raises(ContractError, match="delay"):
        validate_consistency_round(
            primaries, sample, [judgement(q, p, round_=Round.CONSISTENCY, at=early)], SEAL, T0
        )
    with pytest.raises(ContractError, match="delay"):  # before the seal itself
        validate_consistency_round(
            primaries,
            sample,
            [judgement(q, p, round_=Round.CONSISTENCY, at=T0 - timedelta(hours=1))],
            SEAL,
            T0,
        )
    outside = next(j for j in primaries if (j.query_id, j.product_id) not in set(sampled))
    with pytest.raises(ContractError, match="outside the sample"):
        validate_consistency_round(
            primaries,
            sample,
            [
                judgement(
                    outside.query_id,
                    outside.product_id,
                    round_=Round.CONSISTENCY,
                    at=after_delay(80),
                )
            ],
            SEAL,
            T0,
        )
    with pytest.raises(ContractError, match="exactly one annotator"):
        validate_consistency_round(
            primaries,
            sample,
            [judgement(q, p, round_=Round.CONSISTENCY, at=after_delay(80), handle="ann-2")],
            SEAL,
            T0,
        )


def test_consistency_sample_must_match_regeneration_and_seal():
    primaries, sample, _ = _consistency_setup()
    with pytest.raises(ContractError, match="different primary seal"):
        validate_consistency_round(primaries, sample, [], "e" * 64, T0)
    with pytest.raises(ContractError, match="different seal time"):
        validate_consistency_round(primaries, sample, [], SEAL, after_delay(1))
    # Primary judgements that differ from the sealed round (one fewer) regenerate another sample.
    with pytest.raises(ContractError, match="deterministic regeneration"):
        validate_consistency_round(primaries[1:], sample, [], SEAL, T0)


# ---- adjudication and final labels -------------------------------------------------------------


def _adjudicated(label, reasons, changes, reason="Resolved after review."):
    return judgement(
        "gq-p01",
        "P1",
        label,
        round_=Round.ADJUDICATION,
        at=after_delay(100),
        review_reasons=tuple(reasons),
        changes_label=changes,
        adjudication_reason=reason if changes else None,
    )


def _change(prev, new):
    return LabelChange(
        query_id="gq-p01",
        product_id="P1",
        kind=LabelChangeKind.ADJUDICATION,
        previous_label=prev,
        new_label=new,
        reason="Resolved after review.",
        changed_by=HANDLE,
        changed_at=after_delay(100),
        from_golden_version=1,
        to_golden_version=1,
    )


def test_adjudication_of_a_disagreement_with_label_change():
    primaries = [judgement("gq-p01", "P1"), judgement("gq-p01", "P2", Label.NOT_RELEVANT)]
    repeats = [
        judgement("gq-p01", "P1", Label.BORDERLINE, round_=Round.CONSISTENCY, at=after_delay(73))
    ]
    adjudications = [_adjudicated(Label.BORDERLINE, [ReviewReason.CONSISTENCY_DISAGREEMENT], True)]
    changes = [_change(Label.RELEVANT, Label.BORDERLINE)]
    validate_adjudication_round(
        primaries, repeats, [], adjudications, changes, golden_version=1, primary_sealed_at=T0
    )
    finals = derive_final_judgements(primaries, adjudications)
    validate_final_judgements(primaries, adjudications, finals)
    with pytest.raises(ContractError, match="do not match their derivation"):
        validate_final_judgements(primaries, adjudications, [finals[1]])
    with pytest.raises(ContractError, match="exactly one adjudication LabelChange"):
        validate_adjudication_round(
            primaries, repeats, [], adjudications, [], golden_version=1, primary_sealed_at=T0
        )
    with pytest.raises(ContractError, match="does not match primary"):
        validate_adjudication_round(
            primaries,
            repeats,
            [],
            adjudications,
            [_change(Label.NOT_RELEVANT, Label.BORDERLINE)],
            golden_version=1,
            primary_sealed_at=T0,
        )


def test_adjudication_requires_a_queue_or_disagreement_and_matching_reasons():
    primaries = [judgement("gq-p01", "P1", Label.BORDERLINE)]
    queue = build_adjudication_queue(primaries, [query("gq-p01", "a")], [])
    kept = _adjudicated(Label.BORDERLINE, [ReviewReason.BORDERLINE], False)
    validate_adjudication_round(
        primaries, [], queue, [kept], [], golden_version=1, primary_sealed_at=T0
    )
    with pytest.raises(ContractError, match="neither queued nor a disagreement"):
        validate_adjudication_round(
            primaries, [], [], [kept], [], golden_version=1, primary_sealed_at=T0
        )
    unsupported = _adjudicated(Label.BORDERLINE, [ReviewReason.CATALOG_ISSUE], False)
    with pytest.raises(ContractError, match="not supported"):
        validate_adjudication_round(
            primaries, [], queue, [unsupported], [], golden_version=1, primary_sealed_at=T0
        )
    lying = _adjudicated(Label.BORDERLINE, [ReviewReason.BORDERLINE], True)
    with pytest.raises(ContractError, match="changes_label does not match"):
        validate_adjudication_round(
            primaries, [], queue, [lying], [], golden_version=1, primary_sealed_at=T0
        )
