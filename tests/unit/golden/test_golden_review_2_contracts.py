"""review-2 amendment contracts: legacy (review-1) compatibility, the protocol-branched manifest
lifecycle, the inter-annotator agreement report, the blind second-review round and joint
consensus adjudication. Every judgement and consensus record here is a SYNTHETIC TEST FIXTURE,
never a human label; no real golden artifact is written."""

from datetime import timedelta
from pathlib import Path

import pytest
from evals.contracts.canonical import ContractError, encode_json_object, encode_jsonl, sha256_hex
from evals.contracts.constants import AGREEMENT_SAMPLE_CAVEAT, LABEL_ORDER, Label, Relation
from evals.contracts.manifest import (
    REVIEW_2_STAGE_ORDER,
    STAGE_ORDER,
    Manifest,
    Role,
    Stage,
    build_manifest,
    decode_manifest,
    encode_manifest,
    entry_for,
    stage_roles,
    validate_chain,
    validate_transition,
)
from evals.contracts.models import (
    AgreementReport,
    ConsensusAdjudication,
    ConstraintStatus,
    GoldField,
    JointReason,
    LabelChange,
    LabelChangeKind,
    QueueItem,
    ReviewReason,
    Round,
)
from evals.contracts.review_sets import (
    REVIEW_2_MANDATORY_REASONS,
    REVIEW_2_OPTIONAL_REASONS,
    build_adjudication_queue,
    build_consistency_sample,
    build_joint_resolution_queue,
    derive_final_judgements_review_2,
    second_review_disagreements,
    split_review_2_targeted,
)
from evals.contracts.validation import (
    validate_final_judgements_review_2,
    validate_labels_frozen_at,
    validate_review_2_adjudication,
    validate_second_review_round,
)
from evals.metrics.agreement import build_agreement_report
from golden_support import after_delay, eligibility, judgement, query
from pydantic import ValidationError
from test_golden_manifest import PATHS, VERSIONS, _files

from ecommerce_search.filtering.policy import FilterOperator

REAL_MANIFESTS = Path(__file__).resolve().parents[3] / "evals/goldens/internal-search/v1/manifests"
PRIMARY_SEALED_AT = after_delay(1)
SECOND_SEALED_AT = after_delay(10)
REVIEW_PATHS = PATHS | {
    Role.AGREEMENT_REPORT: "v1/agreement_report.json",
}

# ---- legacy (review-1) compatibility ------------------------------------------------------------


def test_the_committed_manifests_keep_their_exact_bytes_and_hashes():
    expected = {
        "1-queries_frozen.json": "51204daec32af33f15539b746adea4e809bf027ffa21f76ffaa8a2dbafcefd67",
        "2-pool_frozen.json": None,
    }
    for name in expected:
        data = (REAL_MANIFESTS / name).read_bytes()
        manifest = decode_manifest(data)
        assert encode_manifest(manifest) == data
        assert manifest.review_protocol is None
        assert b"review_protocol" not in data
    queries = decode_manifest((REAL_MANIFESTS / "1-queries_frozen.json").read_bytes())
    assert queries.manifest_sha256 == expected["1-queries_frozen.json"]
    pool = decode_manifest((REAL_MANIFESTS / "2-pool_frozen.json").read_bytes())
    validate_transition(queries, pool)


def test_the_review_1_lifecycle_and_role_sets_are_unchanged():
    assert [s.value for s in STAGE_ORDER] == [
        "queries_frozen",
        "pool_frozen",
        "primary_sealed",
        "labels_frozen",
        "provenance_unsealed",
    ]
    assert stage_roles(Stage.PRIMARY_SEALED) == stage_roles(Stage.PRIMARY_SEALED, "review-2")
    assert {r.value for r in stage_roles(Stage.LABELS_FROZEN)} - {
        r.value for r in stage_roles(Stage.PRIMARY_SEALED)
    } == {
        "consistency_judgements",
        "adjudication_judgements",
        "label_changes",
        "partitions",
        "final_judgements",
    }
    with pytest.raises(ContractError, match="not part of review protocol"):
        stage_roles(Stage.SECOND_REVIEW_SEALED)


# ---- protocol-branched lifecycle ----------------------------------------------------------------


def _review_2_files(catalog) -> dict[Role, bytes]:
    """`_files` plus SYNTHETIC review-2 records: the one sampled pair is re-labelled
    not_relevant (a disagreement), resolved by a consensus record that keeps the primary."""
    files = _files(catalog)
    from evals.contracts.canonical import decode_json_object, decode_jsonl
    from evals.contracts.models import ConsistencySample, Judgement

    primaries = decode_jsonl(files[Role.PRIMARY_JUDGEMENTS], Judgement)
    sample = decode_json_object(files[Role.CONSISTENCY_SAMPLE], ConsistencySample)
    item = sample.items[0]
    primary = next(
        j for j in primaries if (j.query_id, j.product_id) == (item.query_id, item.product_id)
    )
    second = [
        judgement(
            item.query_id,
            item.product_id,
            Label.NOT_RELEVANT,
            round_=Round.SECOND_REVIEW,
            handle="ann-2",
            at=after_delay(5),
        )
    ]
    second_bytes = encode_jsonl(second)
    report = build_agreement_report(
        primaries,
        sample,
        second,
        primary_annotator="ann-1",
        second_annotator="ann-2",
        second_review_sha256=sha256_hex(second_bytes),
        second_review_sealed_at=SECOND_SEALED_AT,
    )
    consensus = [
        ConsensusAdjudication(
            query_id=item.query_id,
            product_id=item.product_id,
            reasons=(JointReason.SECOND_REVIEW_DISAGREEMENT,),
            primary_label=primary.label,
            second_review_label=Label.NOT_RELEVANT,
            label=primary.label,
            relation=primary.relation,
            rationale="Both annotators agreed the primary label holds.",
            participants=("ann-1", "ann-2"),
            decided_at=after_delay(20),
            rubric_version="rubric-1",
            changes_label=False,
        )
    ]
    files |= {
        Role.SECOND_REVIEW_JUDGEMENTS: second_bytes,
        Role.AGREEMENT_REPORT: encode_json_object(report),
        Role.CONSENSUS_ADJUDICATIONS: encode_jsonl(consensus),
        Role.JOINT_RESOLUTION_QUEUE: encode_jsonl(build_joint_resolution_queue(primaries, second)),
        Role.ADJUDICATION_JUDGEMENTS: encode_jsonl([]),
        Role.LABEL_CHANGES: encode_jsonl([]),
        Role.FINAL_JUDGEMENTS: encode_jsonl(
            derive_final_judgements_review_2(primaries, [], consensus)
        ),
    }
    return files


def _manifest(stage, files, previous, protocol="review-2", **overrides) -> Manifest:
    rank = REVIEW_2_STAGE_ORDER.index(stage)
    fields = dict(
        golden_version=1,
        stage=stage,
        dataset_id="synthetic-seed",
        dataset_version="1",
        dataset_checksum_sha256="2c1c5fa43eec5f7346df40c17e57c07f6594ab1833c5d9d4c862e6d2e8bbbbc8",
        versions=VERSIONS,
        entries=tuple(
            sorted(
                (
                    entry_for(r, REVIEW_PATHS[r], files[r])
                    for r in stage_roles(stage, protocol if rank >= 3 else None)
                ),
                key=lambda e: e.path,
            )
        ),
        provenance_binding=sha256_hex(files[Role.PROVENANCE]) if rank >= 1 else None,
        primary_sealed_at=PRIMARY_SEALED_AT if rank >= 2 else None,
        labels_frozen_at=after_delay(100) if rank >= 4 else None,
        previous_manifest_sha256=previous.manifest_sha256 if previous else None,
        review_protocol=protocol if rank >= 3 else None,
    )
    fields.update(overrides)
    return build_manifest(**fields)


@pytest.fixture
def review_2_files(catalog):
    return _review_2_files(catalog)


@pytest.fixture
def review_2_chain(review_2_files):
    manifests: list[Manifest] = []
    for stage in REVIEW_2_STAGE_ORDER:
        manifests.append(_manifest(stage, review_2_files, manifests[-1] if manifests else None))
    return manifests


def test_a_review_2_chain_is_valid_and_carries_the_protocol(review_2_chain, review_2_files):
    validate_chain(review_2_chain)
    assert [m.review_protocol for m in review_2_chain] == [None] * 3 + ["review-2"] * 3
    labels_frozen = review_2_chain[4]
    roles = {e.role for e in labels_frozen.entries}
    assert Role.CONSISTENCY_JUDGEMENTS not in roles
    assert {
        Role.SECOND_REVIEW_JUDGEMENTS,
        Role.AGREEMENT_REPORT,
        Role.CONSENSUS_ADJUDICATIONS,
    } <= roles
    assert Role.CONSISTENCY_SAMPLE in roles  # the sealed cs-1 sample is the second-review sample
    data = encode_manifest(labels_frozen)
    assert b'"review_protocol":"review-2"' in data
    assert decode_manifest(data) == labels_frozen
    assert validate_labels_frozen_at(labels_frozen, review_2_files) == after_delay(100)


def test_the_primary_seal_is_shared_by_both_protocols(review_2_chain, review_2_files):
    from test_golden_manifest import _manifest as review_1_manifest

    sealed = review_2_chain[2]
    assert sealed.review_protocol is None and b"review_protocol" not in encode_manifest(sealed)
    review_1_next = review_1_manifest(Stage.LABELS_FROZEN, review_2_files, sealed)
    validate_transition(sealed, review_1_next)  # review-1 continues unchanged from the same seal
    validate_transition(sealed, review_2_chain[3])


def test_review_2_transitions_cannot_skip_or_switch(review_2_chain, review_2_files):
    sealed, second, frozen = review_2_chain[2], review_2_chain[3], review_2_chain[4]
    with pytest.raises(ContractError, match="review_protocol can change only|illegal stage"):
        validate_transition(sealed, frozen)  # skips second_review_sealed (protocol switch too)
    with pytest.raises(ContractError, match="illegal stage transition"):
        validate_transition(second, review_2_chain[5])  # skips labels_frozen within review-2
    with pytest.raises(ContractError, match="illegal stage transition"):
        validate_transition(frozen, second)  # backwards
    review_1_frozen = _manifest(
        Stage.LABELS_FROZEN,
        review_2_files,
        second,
        protocol=None,
        entries=tuple(
            sorted(
                (
                    entry_for(r, REVIEW_PATHS[r], review_2_files[r])
                    for r in stage_roles(Stage.LABELS_FROZEN)
                ),
                key=lambda e: e.path,
            )
        ),
        review_protocol=None,
    )
    with pytest.raises(ContractError, match="review_protocol can change only"):
        validate_transition(second, review_1_frozen)


def test_the_protocol_field_is_bound_to_the_stage(review_2_files, review_2_chain):
    with pytest.raises(ValidationError, match="requires review_protocol review-2"):
        _manifest(
            Stage.SECOND_REVIEW_SEALED, review_2_files, review_2_chain[2], review_protocol=None
        )
    with pytest.raises(ValidationError, match="from second_review_sealed onwards"):
        _manifest(
            Stage.PRIMARY_SEALED, review_2_files, review_2_chain[1], review_protocol="review-2"
        )
    with pytest.raises(ValidationError, match="requires exactly the roles"):
        _manifest(
            Stage.LABELS_FROZEN,
            review_2_files,
            review_2_chain[3],
            entries=tuple(
                sorted(
                    (
                        entry_for(r, REVIEW_PATHS[r], review_2_files[r])
                        for r in stage_roles(Stage.LABELS_FROZEN)
                    ),
                    key=lambda e: e.path,
                )
            ),
        )


# ---- agreement (hand-calculated) ----------------------------------------------------------------

QUERIES = [f"gq-p0{i}" for i in range(1, 6)]
PRODUCTS = [f"SYN-LAP-{i:04d}" for i in range(1, 11)]


def _population():
    """50 SYNTHETIC primaries: exactly 2 not_relevant, the rest alternating relevant/borderline.
    cs-1 then samples 10 items: 4 relevant, 4 borderline, 2 not_relevant (round-robin)."""
    pairs = [(q, p) for q in QUERIES for p in PRODUCTS]
    primaries = []
    for index, (q, p) in enumerate(pairs):
        if index in (0, 1):
            label = Label.NOT_RELEVANT
        else:
            label = Label.RELEVANT if index % 2 == 0 else Label.BORDERLINE
        primaries.append(judgement(q, p, label, at=after_delay(0.5)))
    seal = sha256_hex(encode_jsonl(primaries))
    sample = build_consistency_sample(primaries, seal, PRIMARY_SEALED_AT)
    elig = [
        eligibility(q, p, [(GoldField.CATEGORY, FilterOperator.EQ, ConstraintStatus.SATISFIED)])
        for q, p in pairs
    ]
    return primaries, seal, sample, elig


# Second-review labels per primary stratum, in the sample's blind order, chosen so that the
# confusion matrix is R:[3,1,0] B:[1,2,1] N:[0,0,2].
SECOND = {
    Label.RELEVANT: [Label.RELEVANT, Label.RELEVANT, Label.RELEVANT, Label.BORDERLINE],
    Label.BORDERLINE: [Label.RELEVANT, Label.BORDERLINE, Label.BORDERLINE, Label.NOT_RELEVANT],
    Label.NOT_RELEVANT: [Label.NOT_RELEVANT, Label.NOT_RELEVANT],
}


def _second_reviews(primaries, sample, *, handle="ann-2", at=None):
    by_pair = {(j.query_id, j.product_id): j for j in primaries}
    used = {label: 0 for label in LABEL_ORDER}
    out = []
    for item in sample.items:
        primary = by_pair[(item.query_id, item.product_id)].label
        label = SECOND[primary][used[primary]]
        used[primary] += 1
        out.append(
            judgement(
                item.query_id,
                item.product_id,
                label,
                round_=Round.SECOND_REVIEW,
                handle=handle,
                at=at or after_delay(5),
            )
        )
    return out


@pytest.fixture
def population():
    primaries, seal, sample, elig = _population()
    return primaries, seal, sample, elig, _second_reviews(primaries, sample)


def _report(primaries, sample, second):
    return build_agreement_report(
        primaries,
        sample,
        second,
        primary_annotator="ann-1",
        second_annotator="ann-2",
        second_review_sha256=sha256_hex(encode_jsonl(second)),
        second_review_sealed_at=SECOND_SEALED_AT,
    )


def test_the_sample_has_the_expected_strata(population):
    primaries, _, sample, _, _ = population
    by_pair = {(j.query_id, j.product_id): j.label for j in primaries}
    counts = {label: 0 for label in LABEL_ORDER}
    for item in sample.items:
        counts[by_pair[(item.query_id, item.product_id)]] += 1
    assert sample.actual_count == 10
    assert counts == {Label.RELEVANT: 4, Label.BORDERLINE: 4, Label.NOT_RELEVANT: 2}


def test_agreement_matches_hand_calculation(population):
    primaries, _, sample, _, second = population
    report = _report(primaries, sample, second)
    assert report.confusion == ((3, 1, 0), (1, 2, 1), (0, 0, 2))
    assert report.item_count == 10 and report.disagreement_count == 3
    # Three labels: p_o = 7/10; p_e = (4·4 + 4·3 + 2·3)/100 = 0.34; κ = 0.36/0.66.
    assert report.three_label.model_dump() == {
        "agreed_count": 7,
        "observed_agreement": "0.7000000000",
        "expected_agreement": "0.3400000000",
        "cohen_kappa": "0.5454545455",
    }
    # Strict (relevant vs rest): p_o = 0.8; p_e = 0.4·0.4 + 0.6·0.6 = 0.52; κ = 0.28/0.48.
    assert report.strict_binary.model_dump() == {
        "agreed_count": 8,
        "observed_agreement": "0.8000000000",
        "expected_agreement": "0.5200000000",
        "cohen_kappa": "0.5833333333",
    }
    # Lenient (relevant|borderline vs not_relevant): p_o = 0.9; p_e = 0.8·0.7 + 0.2·0.3 = 0.62.
    assert report.lenient_binary.model_dump() == {
        "agreed_count": 9,
        "observed_agreement": "0.9000000000",
        "expected_agreement": "0.6200000000",
        "cohen_kappa": "0.7368421053",
    }
    assert [
        (s.primary_label, s.item_count, s.agreed_count, s.observed_agreement) for s in report.strata
    ] == [
        (Label.RELEVANT, 4, 3, "0.7500000000"),
        (Label.BORDERLINE, 4, 2, "0.5000000000"),
        (Label.NOT_RELEVANT, 2, 2, "1.0000000000"),
    ]
    assert report.statistic == "inter_annotator_agreement"
    assert report.intra_annotator_consistency == "not_measured"
    assert report.sample_caveat == AGREEMENT_SAMPLE_CAVEAT
    assert "relation" not in encode_json_object(report).decode()  # relation facet omitted
    assert encode_json_object(_report(primaries, sample, second)) == encode_json_object(report)


def test_kappa_is_null_when_chance_agreement_is_one():
    from evals.metrics.agreement import _statistics

    degenerate = _statistics([(Label.NOT_RELEVANT, Label.NOT_RELEVANT)] * 2, LABEL_ORDER)
    assert degenerate.observed_agreement == "1.0000000000"
    assert degenerate.expected_agreement == "1.0000000000"
    assert degenerate.cohen_kappa is None
    empty = _statistics([], LABEL_ORDER)
    assert (empty.observed_agreement, empty.cohen_kappa) == (None, None)


def test_an_agreement_report_refuses_inconsistent_figures(population):
    primaries, _, sample, _, second = population
    body = _report(primaries, sample, second).model_dump()
    with pytest.raises(ValidationError, match="two different annotators"):
        AgreementReport.model_validate({**body, "second_annotator": "ann-1"})
    with pytest.raises(ValidationError, match="disagreement_count"):
        AgreementReport.model_validate({**body, "disagreement_count": 0})
    with pytest.raises(ValidationError, match="sampling caveat"):
        AgreementReport.model_validate({**body, "sample_caveat": "A population estimate."})
    with pytest.raises(ValidationError):
        AgreementReport.model_validate({**body, "intra_annotator_consistency": "0.9"})


# ---- the blind second-review round --------------------------------------------------------------


def _validate_round(primaries, seal, sample, elig, second, sealed_at=SECOND_SEALED_AT):
    validate_second_review_round(
        primaries, sample, second, elig, seal, PRIMARY_SEALED_AT, sealed_at
    )


def test_a_complete_second_review_passes(population):
    primaries, seal, sample, elig, second = population
    _validate_round(primaries, seal, sample, elig, second)
    assert len(second_review_disagreements(primaries, second)) == 3


def test_the_second_review_is_ann_2_only_never_the_primary_annotator(population):
    primaries, seal, sample, elig, _ = population
    with pytest.raises(ContractError, match="second reviewer 'ann-2'"):
        _validate_round(
            primaries, seal, sample, elig, _second_reviews(primaries, sample, handle="ann-1")
        )
    with pytest.raises(ContractError, match="second reviewer 'ann-2'"):
        _validate_round(
            primaries, seal, sample, elig, _second_reviews(primaries, sample, handle="ann-3")
        )


@pytest.mark.parametrize(
    "problem", ["missing", "outside", "consistency_round", "before_seal", "after_seal"]
)
def test_second_review_round_refusals(population, problem):
    primaries, seal, sample, elig, second = population
    sealed_at = SECOND_SEALED_AT
    if problem == "missing":
        second = second[1:]
        match = "missing from the second review"
    elif problem == "outside":
        sampled = {(i.query_id, i.product_id) for i in sample.items}
        q, p = next(
            (j.query_id, j.product_id)
            for j in primaries
            if (j.query_id, j.product_id) not in sampled
        )
        second = [
            *second,
            judgement(
                q, p, Label.RELEVANT, round_=Round.SECOND_REVIEW, handle="ann-2", at=after_delay(5)
            ),
        ]
        match = "outside the sample"
    elif problem == "consistency_round":
        second = [j.model_copy(update={"round": Round.CONSISTENCY}) for j in second]
        match = "expected round second_review"
    elif problem == "before_seal":
        second = _second_reviews(primaries, sample, at=PRIMARY_SEALED_AT - timedelta(seconds=1))
        match = "before the primary seal"
    else:
        sealed_at = after_delay(4)
        match = "after its seal"
    with pytest.raises(ContractError, match=match):
        _validate_round(primaries, seal, sample, elig, second, sealed_at)


def test_relevant_is_refused_for_a_hard_violation_in_the_second_review(population):
    primaries, seal, sample, elig, second = population
    target = next(j for j in second if j.label is Label.RELEVANT)
    violated = [
        eligibility(
            e.query_id,
            e.product_id,
            [(GoldField.CATEGORY, FilterOperator.EQ, ConstraintStatus.VIOLATED)],
        )
        if (e.query_id, e.product_id) == (target.query_id, target.product_id)
        else e
        for e in elig
    ]
    with pytest.raises(ContractError, match="hard constraint is violated"):
        _validate_round(primaries, seal, sample, violated, second)


def test_no_72_hour_delay_applies_to_the_second_review(population):
    primaries, seal, sample, elig, _ = population
    soon = _second_reviews(primaries, sample, at=PRIMARY_SEALED_AT + timedelta(minutes=1))
    _validate_round(primaries, seal, sample, elig, soon)


# ---- joint consensus adjudication ---------------------------------------------------------------


def _consensus(primaries, second, *, keep_primary=True, participants=("ann-1", "ann-2"), at=None):
    """One SYNTHETIC joint record per routed pair, resolving exactly its routed reasons."""
    primary = {(j.query_id, j.product_id): j for j in primaries}
    reviewed = {(j.query_id, j.product_id): j for j in second}
    out = []
    for item in build_joint_resolution_queue(primaries, second):
        pair = (item.query_id, item.product_id)
        j = reviewed[pair]
        label = primary[pair].label if keep_primary else j.label
        out.append(
            ConsensusAdjudication(
                query_id=pair[0],
                product_id=pair[1],
                reasons=item.reasons,
                primary_label=primary[pair].label,
                second_review_label=j.label,
                label=label,
                relation=primary[pair].relation if keep_primary else j.relation,
                rationale="Discussed jointly; synthetic fixture.",
                participants=participants,
                decided_at=at or after_delay(20),
                rubric_version="rubric-1",
                changes_label=label is not primary[pair].label,
            )
        )
    return out


def _changes(consensus, *, by="ann-2", at=None):
    return [
        LabelChange(
            query_id=c.query_id,
            product_id=c.product_id,
            kind=LabelChangeKind.CONSENSUS,
            previous_label=c.primary_label,
            new_label=c.label,
            reason="Joint consensus of both annotators.",
            changed_by=by,
            changed_at=at or after_delay(21),
            from_golden_version=1,
            to_golden_version=1,
        )
        for c in consensus
        if c.changes_label
    ]


def _adjudicate(
    primaries, second, consensus, changes=(), adjudications=(), queue=(), joint_queue=None
):
    report = _report(primaries, _population()[2], second)
    validate_review_2_adjudication(
        primaries,
        second,
        list(queue),
        build_joint_resolution_queue(primaries, second) if joint_queue is None else joint_queue,
        list(adjudications),
        consensus,
        list(changes),
        1,
        primary_sealed_at=PRIMARY_SEALED_AT,
        report=report,
    )


def test_every_disagreement_resolved_by_consensus_passes(population):
    primaries, _, _, _, second = population
    kept = _consensus(primaries, second)
    _adjudicate(primaries, second, kept)
    changed = _consensus(primaries, second, keep_primary=False)
    _adjudicate(primaries, second, changed, _changes(changed))
    finals = derive_final_judgements_review_2(primaries, [], changed)
    validate_final_judgements_review_2(primaries, [], changed, finals)
    decided = {(f.query_id, f.product_id): f for f in finals if f.decided_in == "consensus"}
    assert len(decided) == 3 and len(finals) == len(primaries)


def test_an_unresolved_disagreement_blocks_the_freeze(population):
    primaries, _, _, _, second = population
    with pytest.raises(ContractError, match="unresolved disagreements and second-reviewer"):
        _adjudicate(primaries, second, _consensus(primaries, second)[1:])
    with pytest.raises(ContractError, match="unresolved"):
        _adjudicate(primaries, second, [])


def test_joint_records_match_their_reasons_and_need_two_people(population):
    primaries, _, _, _, second = population
    with pytest.raises(ValidationError, match="disagreement reason must match"):
        ConsensusAdjudication(
            query_id="gq-p01",
            product_id="SYN-LAP-0001",
            reasons=(JointReason.SECOND_REVIEW_DISAGREEMENT,),
            primary_label=Label.RELEVANT,
            second_review_label=Label.RELEVANT,
            label=Label.RELEVANT,
            relation=Relation.EXACT_MATCH,
            rationale="Fixture.",
            participants=("ann-1", "ann-2"),
            decided_at=after_delay(20),
            rubric_version="rubric-1",
            changes_label=False,
        )
    with pytest.raises(ValidationError, match="reasons must be non-empty"):
        ConsensusAdjudication(
            query_id="gq-p01",
            product_id="SYN-LAP-0001",
            reasons=(),
            primary_label=Label.RELEVANT,
            second_review_label=Label.RELEVANT,
            label=Label.RELEVANT,
            relation=Relation.EXACT_MATCH,
            rationale="Fixture.",
            participants=("ann-1", "ann-2"),
            decided_at=after_delay(20),
            rubric_version="rubric-1",
            changes_label=False,
        )
    with pytest.raises(ValidationError, match="two distinct handles"):
        _consensus(primaries, second, participants=("ann-1", "ann-1"))
    with pytest.raises(ContractError, match="name both annotators"):
        _adjudicate(
            primaries, second, _consensus(primaries, second, participants=("ann-1", "ann-3"))
        )


def test_consensus_timing_and_label_changes_are_checked(population):
    primaries, _, _, _, second = population
    with pytest.raises(ContractError, match="before the second-review seal"):
        _adjudicate(primaries, second, _consensus(primaries, second, at=after_delay(9)))
    changed = _consensus(primaries, second, keep_primary=False)
    with pytest.raises(ContractError, match="exactly one CONSENSUS LabelChange"):
        _adjudicate(primaries, second, changed)
    with pytest.raises(ContractError, match="outside the consensus"):
        _adjudicate(primaries, second, changed, _changes(changed, by="ann-3"))
    with pytest.raises(ContractError, match="before its consensus"):
        _adjudicate(primaries, second, changed, _changes(changed, at=after_delay(19)))


def test_a_disagreement_cannot_be_adjudicated_by_one_annotator(population):
    primaries, _, _, elig, second = population
    primary = {(j.query_id, j.product_id): j for j in primaries}
    # A borderline primary is in the targeted queue, so only the overlap rule can refuse it.
    pair = next(
        p
        for p in sorted(second_review_disagreements(primaries, second))
        if primary[p].label is Label.BORDERLINE
    )
    queue = build_adjudication_queue(primaries, [query(q, f"fixture {q}") for q in QUERIES], elig)
    assert any((i.query_id, i.product_id) == pair for i in queue)
    single = judgement(
        pair[0],
        pair[1],
        Label.BORDERLINE,
        relation=primary[pair].relation,
        round_=Round.ADJUDICATION,
        at=after_delay(30),
        review_reasons=(ReviewReason.BORDERLINE,),
        changes_label=False,
    )
    with pytest.raises(ContractError, match="decided by joint consensus, not one annotator"):
        _adjudicate(
            primaries, second, _consensus(primaries, second), adjudications=[single], queue=queue
        )


# ---- second-reviewer flags: routed and jointly resolved, also when labels agree ----------------


def _flagged(population, **flags):
    """The fixture's second review with flags set on one pair whose labels AGREE."""
    primaries, _, _, _, second = population
    primary = {(j.query_id, j.product_id): j.label for j in primaries}
    index = next(i for i, j in enumerate(second) if j.label is primary[(j.query_id, j.product_id)])
    second = list(second)
    second[index] = second[index].model_copy(update=flags)
    return primaries, second, (second[index].query_id, second[index].product_id)


def test_flags_on_agreeing_labels_are_routed_with_both_flags_preserved(population):
    primaries, second, pair = _flagged(population, needs_adjudication=True, catalog_issue=True)
    queue = {
        (i.query_id, i.product_id): i.reasons
        for i in build_joint_resolution_queue(primaries, second)
    }
    assert queue[pair] == (
        JointReason.SECOND_REVIEW_CATALOG_ISSUE,
        JointReason.SECOND_REVIEW_NEEDS_ADJUDICATION,
    )
    assert JointReason.SECOND_REVIEW_DISAGREEMENT not in queue[pair]
    disagreements = second_review_disagreements(primaries, second)
    assert all(JointReason.SECOND_REVIEW_DISAGREEMENT in queue[p] for p in disagreements)
    assert len(queue) == len(disagreements) + 1
    assert "label" not in encode_jsonl(build_joint_resolution_queue(primaries, second)).decode()


@pytest.mark.parametrize("flag", ["needs_adjudication", "catalog_issue"])
def test_an_unresolved_flag_on_agreeing_labels_blocks_the_freeze(population, flag):
    primaries, second, pair = _flagged(population, **{flag: True})
    records = _consensus(primaries, second)
    flagged = next(r for r in records if (r.query_id, r.product_id) == pair)
    assert flagged.changes_label is False and flagged.primary_label is flagged.second_review_label
    _adjudicate(primaries, second, records)  # resolved jointly, label unchanged: passes
    without = [r for r in records if (r.query_id, r.product_id) != pair]
    with pytest.raises(ContractError, match="second-reviewer flags block the label freeze"):
        _adjudicate(primaries, second, without)


def test_a_joint_record_must_resolve_exactly_the_routed_reasons(population):
    primaries, second, pair = _flagged(population, needs_adjudication=True, catalog_issue=True)
    records = [
        r.model_copy(update={"reasons": (JointReason.SECOND_REVIEW_NEEDS_ADJUDICATION,)})
        if (r.query_id, r.product_id) == pair
        else r
        for r in _consensus(primaries, second)
    ]
    with pytest.raises(ContractError, match="exactly its routed reasons"):
        _adjudicate(primaries, second, records)


def test_a_tampered_joint_queue_is_refused(population):
    primaries, second, _ = _flagged(population, catalog_issue=True)
    queue = build_joint_resolution_queue(primaries, second)
    with pytest.raises(ContractError, match="does not match its regeneration"):
        _adjudicate(primaries, second, _consensus(primaries, second), joint_queue=queue[1:])


def test_a_routed_flag_cannot_be_decided_by_one_annotator(population):
    primaries, second, pair = _flagged(population, needs_adjudication=True)
    primary = {(j.query_id, j.product_id): j for j in primaries}
    single = judgement(
        pair[0],
        pair[1],
        primary[pair].label,
        relation=primary[pair].relation,
        round_=Round.ADJUDICATION,
        at=after_delay(30),
        review_reasons=(ReviewReason.LOW_CONFIDENCE,),
        changes_label=False,
    )
    with pytest.raises(ContractError, match="decided by joint consensus, not one annotator"):
        _adjudicate(primaries, second, _consensus(primaries, second), adjudications=[single])


def _scoped_queue(primaries, second):
    """A SYNTHETIC targeted queue built directly (not from flags) so that every scope case is
    present: one routed pair (also in the joint queue), two optional review candidates, and
    mandatory items for each mandatory reason, alone and combined with an optional reason."""
    routed = [(i.query_id, i.product_id) for i in build_joint_resolution_queue(primaries, second)]
    sampled = {(j.query_id, j.product_id) for j in second}
    free = [
        (j.query_id, j.product_id) for j in primaries if (j.query_id, j.product_id) not in sampled
    ]
    R = ReviewReason
    cases = [
        (routed[0], (R.BORDERLINE,)),  # in both queues: settled by its joint record
        (free[0], (R.BORDERLINE,)),  # optional
        (free[1], (R.AMBIGUOUS_QUERY, R.BORDERLINE)),  # optional
        (free[2], (R.NEEDS_ADJUDICATION,)),  # mandatory
        (free[3], (R.BORDERLINE, R.CATALOG_ISSUE)),  # mandatory despite an optional reason
        (free[4], (R.LOW_CONFIDENCE,)),  # mandatory
        (free[5], (R.AMBIGUOUS_QUERY, R.HARD_CONSTRAINT_UNKNOWN)),  # mandatory
    ]
    return [
        QueueItem(query_id=q, product_id=p, review_reasons=tuple(sorted(reasons)))
        for (q, p), reasons in sorted(cases)
    ]


def _singles(primaries, items, at=None):
    primary = {(j.query_id, j.product_id): j for j in primaries}
    return [
        judgement(
            i.query_id,
            i.product_id,
            primary[(i.query_id, i.product_id)].label,
            relation=primary[(i.query_id, i.product_id)].relation,
            round_=Round.ADJUDICATION,
            at=at or after_delay(30),
            review_reasons=i.review_reasons,
            changes_label=False,
        )
        for i in items
    ]


def test_the_scope_split_places_every_targeted_item_exactly_once(population):
    primaries, _, _, _, second = population
    queue = _scoped_queue(primaries, second)
    joint = build_joint_resolution_queue(primaries, second)
    mandatory, optional = split_review_2_targeted(queue, joint)
    pairs = lambda items: [(i.query_id, i.product_id) for i in items]  # noqa: E731
    routed = set(pairs(joint))
    assert not set(pairs(mandatory)) & set(pairs(optional))
    assert set(pairs(mandatory)) | set(pairs(optional)) == set(pairs(queue)) - routed
    assert len(pairs(queue)) == len(mandatory) + len(optional) + len(set(pairs(queue)) & routed)
    assert len(mandatory) == 4 and len(optional) == 2
    assert all(set(i.review_reasons) <= REVIEW_2_OPTIONAL_REASONS for i in optional)
    assert all(set(i.review_reasons) & REVIEW_2_MANDATORY_REASONS for i in mandatory)
    # the two reason sets partition every reason a targeted-queue item can carry
    assert not REVIEW_2_OPTIONAL_REASONS & REVIEW_2_MANDATORY_REASONS
    assert (
        set(ReviewReason) - {ReviewReason.CONSISTENCY_DISAGREEMENT}
        == REVIEW_2_OPTIONAL_REASONS | REVIEW_2_MANDATORY_REASONS
    )


def test_an_unresolved_mandatory_item_blocks_the_freeze(population):
    primaries, _, _, _, second = population
    queue = _scoped_queue(primaries, second)
    mandatory, _ = split_review_2_targeted(queue, build_joint_resolution_queue(primaries, second))
    consensus = _consensus(primaries, second)
    with pytest.raises(ContractError, match="4 mandatory targeted-queue item.* unresolved"):
        _adjudicate(primaries, second, consensus, queue=queue)
    with pytest.raises(ContractError, match="1 mandatory targeted-queue item.* unresolved"):
        _adjudicate(
            primaries, second, consensus, adjudications=_singles(primaries, mandatory[1:]),
            queue=queue,
        )  # fmt: skip
    # every mandatory item resolved by ann-1, the routed pair by its joint record, and the
    # optional candidates left alone: the freeze gate passes
    _adjudicate(
        primaries, second, consensus, adjudications=_singles(primaries, mandatory), queue=queue
    )
    # an unresolved joint record still blocks, whatever the targeted queue holds
    with pytest.raises(ContractError, match="unresolved disagreements and second-reviewer"):
        _adjudicate(
            primaries, second, consensus[1:], adjudications=_singles(primaries, mandatory),
            queue=queue,
        )  # fmt: skip


def test_optional_candidates_keep_their_sealed_primary_label(population):
    primaries, _, _, _, second = population
    queue = _scoped_queue(primaries, second)
    mandatory, optional = split_review_2_targeted(
        queue, build_joint_resolution_queue(primaries, second)
    )
    consensus = _consensus(primaries, second)
    singles = _singles(primaries, mandatory)
    finals = derive_final_judgements_review_2(primaries, singles, consensus)
    validate_final_judgements_review_2(primaries, singles, consensus, finals)
    by_pair = {(f.query_id, f.product_id): f for f in finals}
    primary = {(j.query_id, j.product_id): j for j in primaries}
    for item in optional:
        pair = (item.query_id, item.product_id)
        assert by_pair[pair].decided_in == "primary"  # never reported as resolved
        assert (by_pair[pair].label, by_pair[pair].relation) == (
            primary[pair].label,
            primary[pair].relation,
        )
    assert all(by_pair[(i.query_id, i.product_id)].decided_in == "adjudication" for i in mandatory)
    # an optional candidate may still be adjudicated, exactly as in review-1
    extra = _singles(primaries, optional[:1])
    _adjudicate(primaries, second, consensus, adjudications=singles + extra, queue=queue)
    finals = derive_final_judgements_review_2(primaries, singles + extra, consensus)
    pair = (optional[0].query_id, optional[0].product_id)
    assert {(f.query_id, f.product_id): f for f in finals}[pair].decided_in == "adjudication"


def test_the_review_2_disclosure_states_the_approved_resolution_policy():
    from evals.contracts.constants import DISCLOSURE_REVIEW_2, DISCLOSURE_SINGLE_ANNOTATOR

    for phrase in (
        "inter-annotator agreement is reported on that label-stratified sample only",
        "is not proof of label accuracy",
        "Delayed intra-annotator consistency was not measured",
        "Second-review disagreements and pairs ann-2 flagged for adjudication or a catalog issue "
        "were resolved jointly by both annotators",
        "a pair in both queues was resolved by one joint record that also settles its "
        "targeted-queue item",
        "Items only in ann-1's targeted queue that carried ann-1's needs-adjudication or "
        "catalog-issue flag, low confidence or an unknown hard-constraint attribute were "
        "resolved by ann-1",
        "Items selected only because the primary label was borderline or the query was "
        "ambiguous were optional review candidates: any that ann-1 did not adjudicate keep their "
        "sealed primary label and were not re-reviewed",
        "Every required item was resolved before the labels were frozen",
        "not cryptographic isolation",
    ):
        assert phrase in DISCLOSURE_REVIEW_2
    assert "intra-annotator consistency" not in DISCLOSURE_REVIEW_2.replace(
        "Delayed intra-annotator consistency was not measured", ""
    )
    assert DISCLOSURE_SINGLE_ANNOTATOR == (  # review-1 text unchanged
        "The Golden Dataset was reviewed by one human annotator; "
        "no independent inter-annotator agreement is claimed."
    )
