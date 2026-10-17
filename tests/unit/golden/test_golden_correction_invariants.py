"""Regression tests from the independent Session B commit review (synthetic fixtures only).

Covers LabelChange chronology, the manifest-bound post-freeze correction validator (key-set
preservation, unchanged labels, exact binding), the approved `ann-1` handle and typed errors.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from evals.contracts.canonical import CanonicalFormError, ContractError, decode_jsonl, encode_jsonl
from evals.contracts.constants import SINGLE_ANNOTATOR_HANDLE, Label, Relation
from evals.contracts.manifest import (
    STAGE_ORDER,
    Manifest,
    Role,
    Stage,
    decode_manifest,
    encode_manifest,
    validate_transition,
)
from evals.contracts.models import (
    FinalJudgement,
    Judgement,
    LabelChange,
    LabelChangeKind,
    PoolItem,
    ProvenanceEntry,
    ReviewReason,
    Round,
    SourceHit,
    SourceSystem,
)
from evals.contracts.review_sets import build_consistency_sample, derive_final_judgements
from evals.contracts.validation import (
    validate_adjudication_round,
    validate_consistency_round,
    validate_post_freeze_corrections,
    validate_provenance,
    validate_single_annotator,
)
from golden_support import HANDLE, T0, after_delay, judgement
from pydantic import ValidationError
from test_golden_manifest import PRODUCTS, _files, _manifest

# ---- shared builders ----------------------------------------------------------------------------


def _chain(
    files, golden_version=1, supersedes=None, upto=Stage.PROVENANCE_UNSEALED, frozen_at=None
):
    manifests: list[Manifest] = []
    for stage in STAGE_ORDER[: STAGE_ORDER.index(upto) + 1]:
        frozen = {}
        if frozen_at is not None and stage in (Stage.LABELS_FROZEN, Stage.PROVENANCE_UNSEALED):
            frozen = {"labels_frozen_at": frozen_at}
        manifests.append(
            _manifest(
                stage,
                files,
                manifests[-1] if manifests else None,
                golden_version=golden_version,
                supersedes_manifest_sha256=supersedes,
                **frozen,
            )
        )
    return manifests


def _decode(files, role, model):
    return decode_jsonl(files[role], model)


# v1 (from `_files`): P0..P2 relevant, P3 borderline → adjudicated not_relevant at +90 h
# (LabelChange at +90 h), P4 borderline; one consistency repeat at +80 h. Latest v1 record:
# +90 h; v1 labels_frozen_at: +100 h (the `_manifest` default). v2 is frozen at +300 h.
P4 = PRODUCTS[4]
CORRECTED_AT = after_delay(200)
V2_FROZEN_AT = after_delay(300)


def _correction(
    previous=Label.BORDERLINE,
    new=Label.RELEVANT,
    *,
    product_id=P4,
    query_id="gq-p01",
    at=CORRECTED_AT,
    by=HANDLE,
    from_version=1,
    to_version=2,
):
    return LabelChange(
        query_id=query_id,
        product_id=product_id,
        kind=LabelChangeKind.POST_FREEZE_CORRECTION,
        previous_label=previous,
        new_label=new,
        reason="Corrected after freeze.",
        changed_by=by,
        changed_at=at,
        from_golden_version=from_version,
        to_golden_version=to_version,
    )


def _v2_adjudication(product_id=P4, label=Label.RELEVANT, at=CORRECTED_AT):
    return judgement(
        "gq-p01",
        product_id,
        label,
        round_=Round.ADJUDICATION,
        at=at,
        review_reasons=(ReviewReason.BORDERLINE,),
        changes_label=True,
        adjudication_reason="Corrected after freeze.",
    )


def _versions(catalog, *, corrections=None, finals=None, adjudications=None, v1_frozen_at=None):
    """A frozen v1 chain plus a correction-only v2 chain that relabels P4 borderline → relevant."""
    v1_files = _files(catalog)
    v1 = _chain(v1_files, frozen_at=v1_frozen_at)
    primaries = _decode(v1_files, Role.PRIMARY_JUDGEMENTS, Judgement)
    v1_adjudications = _decode(v1_files, Role.ADJUDICATION_JUDGEMENTS, Judgement)
    if adjudications is None:
        adjudications = [*v1_adjudications, _v2_adjudication()]
    if finals is None:
        finals = derive_final_judgements(primaries, adjudications)
    if corrections is None:
        corrections = [_correction()]
    v2_files = {
        **v1_files,
        Role.ADJUDICATION_JUDGEMENTS: encode_jsonl(adjudications),
        Role.LABEL_CHANGES: encode_jsonl(corrections),
        Role.FINAL_JUDGEMENTS: encode_jsonl(finals),
    }
    v2 = _chain(
        v2_files, golden_version=2, supersedes=v1[-1].manifest_sha256, frozen_at=V2_FROZEN_AT
    )
    return v1[-1], v1_files, v2, v2_files


def _v1_finals(catalog):
    return _decode(_files(catalog), Role.FINAL_JUDGEMENTS, FinalJudgement)


def _relabel(finals, product_id, label, relation):
    return [
        f.model_copy(update={"label": label, "relation": relation, "decided_in": "adjudication"})
        if f.product_id == product_id
        else f
        for f in finals
    ]


# ---- 1. LabelChange chronology ------------------------------------------------------------------


def _adjudicated_disagreement(changed_at):
    primaries = [judgement("gq-p01", "P1")]
    repeats = [
        judgement("gq-p01", "P1", Label.BORDERLINE, round_=Round.CONSISTENCY, at=after_delay(73))
    ]
    adjudications = [
        judgement(
            "gq-p01",
            "P1",
            Label.BORDERLINE,
            round_=Round.ADJUDICATION,
            at=after_delay(100),
            review_reasons=(ReviewReason.CONSISTENCY_DISAGREEMENT,),
            changes_label=True,
            adjudication_reason="Near-miss substitute.",
        )
    ]
    changes = [
        LabelChange(
            query_id="gq-p01",
            product_id="P1",
            kind=LabelChangeKind.ADJUDICATION,
            previous_label=Label.RELEVANT,
            new_label=Label.BORDERLINE,
            reason="Near-miss substitute.",
            changed_by=HANDLE,
            changed_at=changed_at,
            from_golden_version=1,
            to_golden_version=1,
        )
    ]
    return primaries, repeats, [], adjudications, changes


def test_adjudication_label_change_cannot_predate_its_adjudication():
    sealed = after_delay(1)
    # After the seal and after the consistency repeat, but before the adjudication (+100 h).
    early = _adjudicated_disagreement(after_delay(100) - timedelta(microseconds=1))
    with pytest.raises(ContractError, match="before its adjudication"):
        validate_adjudication_round(*early, golden_version=1, primary_sealed_at=sealed)
    same_time = _adjudicated_disagreement(after_delay(100))
    validate_adjudication_round(*same_time, golden_version=1, primary_sealed_at=sealed)


def test_correction_cannot_predate_its_adjudication_or_the_previous_frozen_version(catalog):
    previous, previous_files, chain, files = _versions(catalog)
    validate_post_freeze_corrections(previous, previous_files, chain, files)

    before_adjudication = CORRECTED_AT - timedelta(microseconds=1)
    args = _versions(catalog, corrections=[_correction(at=before_adjudication)])
    with pytest.raises(ContractError, match="before its adjudication"):
        validate_post_freeze_corrections(*args)

    # Dated before v1's latest record (+90 h), so before the version it corrects existed.
    backdated = after_delay(89)
    args = _versions(
        catalog,
        corrections=[_correction(at=backdated)],
        adjudications=[
            *_decode(_files(catalog), Role.ADJUDICATION_JUDGEMENTS, Judgement),
            _v2_adjudication(at=backdated),
        ],
    )
    with pytest.raises(ContractError, match="predates labels_frozen_at of golden version 1"):
        validate_post_freeze_corrections(*args)


# ---- 2. correction-only key-set preservation ----------------------------------------------------


def test_correction_only_version_keeps_the_exact_key_set(catalog):
    corrected = _relabel(_v1_finals(catalog), P4, Label.RELEVANT, Relation.SATISFIES_INTENT)
    extra = FinalJudgement(
        query_id="gq-p01",
        product_id="SYN-LAP-0006",
        label=Label.RELEVANT,
        relation=Relation.SATISFIES_INTENT,
        decided_in="primary",
        rubric_version="rubric-1",
    )
    other_query = extra.model_copy(update={"query_id": "gq-p02", "product_id": PRODUCTS[0]})
    for finals, message in [
        ([*corrected, extra], r"added \[\('gq-p01', 'SYN-LAP-0006'\)\], deleted \[\]"),
        ([*corrected, other_query], r"added \[\('gq-p02', 'SYN-LAP-0001'\)\], deleted \[\]"),
        (
            [f for f in corrected if f.product_id != PRODUCTS[0]],
            r"added \[\], deleted \[\('gq-p01', 'SYN-LAP-0001'\)\]",
        ),
        ([], r"added \[\], deleted \[\('gq-p01', 'SYN-LAP-0001'\)"),
    ]:
        with pytest.raises(ContractError, match=message):
            validate_post_freeze_corrections(*_versions(catalog, finals=finals))


# ---- 3. unchanged-label corrections -------------------------------------------------------------


def test_correction_for_an_unchanged_label_is_rejected(catalog):
    # P0 is relevant in both versions; a correction for it is refused even if well-formed.
    unchanged = _correction(Label.RELEVANT, Label.BORDERLINE, product_id=PRODUCTS[0])
    args = _versions(catalog, corrections=[_correction(), unchanged])
    with pytest.raises(ContractError, match="unchanged label"):
        validate_post_freeze_corrections(*args)


# ---- 4. exact correction binding ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("corrections", "message"),
    [
        ([], "without a post-freeze correction"),
        ([_correction(), _correction()], "duplicate record key"),
        (
            [_correction(), _correction(product_id="SYN-LAP-0009")],
            "not in the superseded version",
        ),
        ([_correction(query_id="gq-p02")], "not in the superseded version"),
        ([_correction(from_version=2, to_version=3)], "from golden version 1 to 2"),
        ([_correction(by="ann-2")], "ann-1"),
        ([_correction(previous=Label.NOT_RELEVANT)], "previous_label"),
        ([_correction(new=Label.NOT_RELEVANT)], "new_label"),
    ],
)
def test_every_changed_label_has_exactly_one_matching_correction(catalog, corrections, message):
    with pytest.raises(ContractError, match=message):
        validate_post_freeze_corrections(*_versions(catalog, corrections=corrections))


def test_correction_validator_binds_the_manifests_and_their_files(catalog):
    previous, previous_files, chain, files = _versions(catalog)
    tampered = {**files, Role.FINAL_JUDGEMENTS: encode_jsonl(_v1_finals(catalog))}
    with pytest.raises(ContractError, match="do not match file_sha256"):
        validate_post_freeze_corrections(previous, previous_files, chain, tampered)
    missing = {r: b for r, b in files.items() if r is not Role.LABEL_CHANGES}
    with pytest.raises(ContractError, match="no file for label_changes"):
        validate_post_freeze_corrections(previous, previous_files, chain, missing)
    with pytest.raises(ContractError, match="labels_frozen"):
        validate_post_freeze_corrections(previous, previous_files, chain[:3], files)
    with pytest.raises(ContractError, match="terminal"):
        validate_post_freeze_corrections(chain[3], files, chain, files)
    unrelated = _chain(_files(catalog), golden_version=2, supersedes="1" * 64)
    with pytest.raises(ContractError, match="does not point"):
        validate_post_freeze_corrections(previous, previous_files, unrelated, files)
    with pytest.raises(ContractError, match="illegal stage transition"):
        validate_post_freeze_corrections(previous, previous_files, [chain[0], chain[2]], files)


# ---- E. the approved single annotator -----------------------------------------------------------


def test_the_single_annotator_is_the_approved_handle():
    assert SINGLE_ANNOTATOR_HANDLE == "ann-1"
    assert validate_single_annotator([judgement("gq-p01", "P1")]) == "ann-1"
    with pytest.raises(ContractError, match="ann-1"):
        validate_single_annotator([judgement("gq-p01", "P1", handle="ann-7")])


# ---- C. complete consistency coverage: duplicates ------------------------------------------------


def test_duplicate_consistency_judgements_are_refused():
    seal = "d" * 64
    primaries = [judgement("gq-p01", f"P{i:02d}") for i in range(10)]
    sample = build_consistency_sample(primaries, seal, T0)
    repeats = [
        judgement(i.query_id, i.product_id, round_=Round.CONSISTENCY, at=after_delay(72))
        for i in sample.items
    ]
    with pytest.raises(ContractError, match="duplicate"):
        validate_consistency_round(primaries, sample, [*repeats, repeats[0]], seal, T0)


# ---- D. typed errors, never a bare KeyError -----------------------------------------------------


def test_missing_records_raise_contract_errors_not_key_errors(catalog):
    pool = [
        PoolItem(
            query_id="gq-p01",
            product_id="NOPE-1",
            product_content_sha256="a" * 64,
            card_sha256="b" * 64,
            blind_position=1,
        )
    ]
    provenance = [
        ProvenanceEntry(
            query_id="gq-p01",
            product_id="NOPE-1",
            added_by="system_union",
            sources=(SourceHit(system=SourceSystem.V0_LEXICAL, rank=1),),
        )
    ]
    with pytest.raises(ContractError, match="unknown product"):
        validate_provenance(pool, provenance, [], catalog)
    pool_frozen = _chain(_files(catalog), upto=Stage.POOL_FROZEN)[-1]
    with pytest.raises(ContractError, match="primary_judgements"):
        pool_frozen.primary_seal_sha256()


# ---- labels_frozen_at: an explicit, hashed freeze time ------------------------------------------


def test_labels_frozen_at_is_set_exactly_from_labels_frozen(catalog):
    files = _files(catalog)
    chain = _chain(files)
    for manifest in chain:
        frozen = STAGE_ORDER.index(manifest.stage) >= STAGE_ORDER.index(Stage.LABELS_FROZEN)
        assert (manifest.labels_frozen_at is not None) is frozen
    with pytest.raises(ValidationError, match="labels_frozen_at"):  # missing at labels_frozen
        _manifest(Stage.LABELS_FROZEN, files, chain[2], labels_frozen_at=None)
    with pytest.raises(ValidationError, match="labels_frozen_at"):  # present before it
        _manifest(Stage.PRIMARY_SEALED, files, chain[1], labels_frozen_at=after_delay(100))
    with pytest.raises(ValidationError, match="labels_frozen_at"):  # before the primary seal
        _manifest(Stage.LABELS_FROZEN, files, chain[2], labels_frozen_at=T0)


def test_labels_frozen_at_cannot_change_in_a_later_stage(catalog):
    files = _files(catalog)
    chain = _chain(files)
    moved = _manifest(Stage.PROVENANCE_UNSEALED, files, chain[3], labels_frozen_at=after_delay(101))
    with pytest.raises(ContractError, match="labels_frozen_at changed"):
        validate_transition(chain[3], moved)


def test_labels_frozen_at_must_be_a_canonical_utc_timestamp(catalog):
    files = _files(catalog)
    chain = _chain(files)
    for bad in (
        datetime(2026, 10, 15, 12, 0, 0),  # naive
        datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        "2026-10-15T12:00:00Z",  # not the canonical microsecond form
        "2026-10-15 12:00:00.000000Z",
    ):
        with pytest.raises(ValidationError, match="timestamps"):
            _manifest(Stage.LABELS_FROZEN, files, chain[2], labels_frozen_at=bad)

    terminal = chain[-1]
    data = encode_manifest(terminal)
    assert decode_manifest(data) == terminal
    assert encode_manifest(decode_manifest(data)) == data
    body = json.loads(data)
    assert body["labels_frozen_at"] == "2026-10-14T16:00:00.123456Z"
    body["labels_frozen_at"] = "2026-10-14T16:00:00.123457Z"
    tampered = (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with pytest.raises(CanonicalFormError, match="manifest_sha256"):  # the field is hashed
        decode_manifest(tampered)


@pytest.mark.parametrize(
    ("role", "model", "product_id"),
    [
        (Role.PRIMARY_JUDGEMENTS, Judgement, PRODUCTS[0]),
        (Role.CONSISTENCY_JUDGEMENTS, Judgement, None),
        (Role.ADJUDICATION_JUDGEMENTS, Judgement, PRODUCTS[3]),
        (Role.LABEL_CHANGES, LabelChange, PRODUCTS[3]),
    ],
)
def test_labels_frozen_at_cannot_predate_any_included_record(catalog, role, model, product_id):
    from evals.contracts.validation import validate_labels_frozen_at

    files = _files(catalog)
    chain = _chain(files, upto=Stage.LABELS_FROZEN)
    validate_labels_frozen_at(chain[-1], files)  # +100 h covers the latest record (+90 h)
    at_latest = _chain(files, upto=Stage.LABELS_FROZEN, frozen_at=after_delay(90))
    validate_labels_frozen_at(at_latest[-1], files)  # equality is allowed

    stamp = "changed_at" if model is LabelChange else "annotated_at"
    late = [
        r.model_copy(update={stamp: after_delay(150)})
        if product_id is None or r.product_id == product_id
        else r
        for r in decode_jsonl(files[role], model)
    ]
    later_files = {**files, role: encode_jsonl(late)}
    early = _chain(later_files, upto=Stage.LABELS_FROZEN)  # frozen at +100 h, record at +150 h
    with pytest.raises(ContractError, match=f"labels_frozen_at is earlier than .*{role.value}"):
        validate_labels_frozen_at(early[-1], later_files)


def test_correction_compares_against_labels_frozen_at_not_the_latest_record(catalog):
    # v1's latest record is +90 h but it was frozen at +150 h: a correction at +120 h is after
    # every v1 record yet before v1 was frozen, so it is refused.
    frozen = after_delay(150)
    between = after_delay(120)
    v1_adjudications = _decode(_files(catalog), Role.ADJUDICATION_JUDGEMENTS, Judgement)
    args = _versions(
        catalog,
        v1_frozen_at=frozen,
        corrections=[_correction(at=between)],
        adjudications=[*v1_adjudications, _v2_adjudication(at=between)],
    )
    with pytest.raises(ContractError, match="predates labels_frozen_at of golden version 1"):
        validate_post_freeze_corrections(*args)
    boundary = _versions(  # equality with labels_frozen_at is accepted
        catalog,
        v1_frozen_at=frozen,
        corrections=[_correction(at=frozen)],
        adjudications=[*v1_adjudications, _v2_adjudication(at=frozen)],
    )
    validate_post_freeze_corrections(*boundary)


# ---- correction-only semantics: exact delta replay ----------------------------------------------


def test_applying_the_corrections_to_the_previous_finals_reproduces_the_next_finals(catalog):
    previous, previous_files, chain, files = _versions(catalog)
    v1 = {(f.query_id, f.product_id): f.label for f in _v1_finals(catalog)}
    v2 = {
        (f.query_id, f.product_id): f.label
        for f in decode_jsonl(files[Role.FINAL_JUDGEMENTS], FinalJudgement)
    }
    replayed = dict(v1)
    for c in decode_jsonl(files[Role.LABEL_CHANGES], LabelChange):
        replayed[(c.query_id, c.product_id)] = c.new_label
    assert replayed == v2 != v1
    validate_post_freeze_corrections(previous, previous_files, chain, files)


def test_next_finals_that_the_corrections_cannot_reproduce_are_refused(catalog):
    corrected = _relabel(_v1_finals(catalog), P4, Label.RELEVANT, Relation.SATISFIES_INTENT)
    # P0 keeps its label but its relation changes: a metadata-only correction, not supported.
    relation_only = [
        f.model_copy(update={"relation": Relation.EXACT_MATCH})
        if f.product_id == PRODUCTS[0]
        else f
        for f in corrected
    ]
    with pytest.raises(ContractError, match="cannot be reproduced"):
        validate_post_freeze_corrections(*_versions(catalog, finals=relation_only))
    # A rubric-version change on an unchanged pair is equally unsupported.
    rubric_only = [
        f.model_copy(update={"rubric_version": "rubric-2"}) if f.product_id == PRODUCTS[1] else f
        for f in corrected
    ]
    with pytest.raises(ContractError, match="cannot be reproduced"):
        validate_post_freeze_corrections(*_versions(catalog, finals=rubric_only))


# ---- adjudication-origin vs post-freeze LabelChange ---------------------------------------------


def test_post_freeze_correction_needs_no_new_adjudication(catalog):
    # The correction record itself is the human audit record; v2 has only v1's adjudications.
    v1_adjudications = _decode(_files(catalog), Role.ADJUDICATION_JUDGEMENTS, Judgement)
    corrected = _relabel(_v1_finals(catalog), P4, Label.RELEVANT, Relation.SATISFIES_INTENT)
    validate_post_freeze_corrections(
        *_versions(catalog, adjudications=v1_adjudications, finals=corrected)
    )
    early = _versions(
        catalog,
        adjudications=v1_adjudications,
        finals=corrected,
        corrections=[_correction(at=after_delay(99))],  # v1 frozen at +100 h
    )
    with pytest.raises(ContractError, match="predates labels_frozen_at"):
        validate_post_freeze_corrections(*early)


def test_adjudication_label_change_needs_its_adjudication():
    primaries, repeats, queue, _adjudications, changes = _adjudicated_disagreement(after_delay(100))
    with pytest.raises(ContractError, match="exactly one adjudication LabelChange"):
        validate_adjudication_round(
            primaries, repeats, queue, [], changes, golden_version=1, primary_sealed_at=T0
        )
