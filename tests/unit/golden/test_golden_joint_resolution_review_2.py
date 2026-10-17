"""The review-2 joint-resolution recording tool on a temporary golden directory sealed through
`second_review_sealed` from SYNTHETIC judgements (the fixture of the adjudication tests). Every
judgement, joint record and confirmation here is a SYNTHETIC TEST FIXTURE typed by scripted
fake input, never a human decision. The real golden directory and the real work directories are
never read or written."""

import dataclasses
from datetime import timedelta

import pytest
import test_golden_adjudication_review_2 as adjudication_tests
from evals.annotation import joint_resolution as jr
from evals.contracts.canonical import ContractError, decode_jsonl, encode_json_object
from evals.contracts.models import (
    ConsensusAdjudication,
    JointReason,
    Judgement,
    Label,
    LabelChange,
    LabelChangeKind,
    OverallStatus,
    Round,
)
from evals.contracts.validation import validate_review_2_adjudication
from test_golden_adjudication_review_2 import ADJ_T0, SECOND_SEALED_AT, _clock, _scripted, _tree

# The same SYNTHETIC golden directory sealed through `second_review_sealed`, registered here.
sealed_template = adjudication_tests.sealed_template
sealed = adjudication_tests.sealed

RATIONALE = "Synthetic joint fixture rationale."
CHANGE = "Synthetic consensus change reason."


@pytest.fixture
def joint(sealed, catalog):
    root, target = sealed
    return jr.load_joint(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(joint, tmp_path):
    return jr.JointStore(joint, tmp_path / "joint-work")


def _letter(label: Label) -> str:
    return {Label.RELEVANT: "r", Label.BORDERLINE: "b", Label.NOT_RELEVANT: "n"}[label]


def _primary(joint, item):
    return joint.targeted.primaries[(item.query_id, item.product_id)]


def _keep(joint, item) -> list[str]:
    """SYNTHETIC answers: both agree to keep the primary label."""
    return ["r", _letter(_primary(joint, item).label), "1", RATIONALE, "ann-1", "ann-2", "y"]


def _change(joint, item) -> list[str]:
    """SYNTHETIC answers: both agree on a different, never-relevant label."""
    other = "n" if _primary(joint, item).label is Label.BORDERLINE else "b"
    return ["r", other, "1", RATIONALE, CHANGE, "ann-1", "ann-2", "y"]


def _cli(monkeypatch, sealed, catalog):
    root, _ = sealed
    loader = jr.load_joint
    monkeypatch.setattr(
        jr, "load_joint", lambda golden_dir: loader(golden_dir, allowed_root=root, catalog=catalog)
    )


# ---- scope and presentation -------------------------------------------------------------------


def test_scope_is_exactly_the_sealed_joint_queue(joint):
    assert [(i.query_id, i.product_id) for i in joint.items] == sorted(joint.targeted.joint)
    reasons = {r for i in joint.items for r in i.reasons}
    assert JointReason.SECOND_REVIEW_DISAGREEMENT in reasons
    assert JointReason.SECOND_REVIEW_CATALOG_ISSUE in reasons


def test_each_pair_shows_both_sealed_judgements_and_every_reason(joint):
    queued = {(i.query_id, i.product_id) for i in joint.targeted.queue}
    overlap = next(i for i in joint.items if (i.query_id, i.product_id) in queued)
    text = jr.render_pair(joint, overlap, 1, len(joint.items))
    assert "sealed ann-1 primary judgement:" in text
    assert "sealed ann-2 second-review judgement:" in text
    assert "routed for joint resolution: " + ", ".join(r.value for r in overlap.reasons) in text
    assert "also in ann-1's targeted queue (this joint record settles it)" in text
    flagged = next(i for i in joint.items if JointReason.SECOND_REVIEW_CATALOG_ISSUE in i.reasons)
    assert "catalog data is never edited" in jr.render_pair(joint, flagged, 1, 1)


# ---- recording ----------------------------------------------------------------------------------


def test_kept_and_changed_resolutions_record_exactly_the_contract(joint, store, sealed):
    before = _tree(sealed[1])
    first, second = joint.items[0], joint.items[1]
    saved = jr.annotate(
        joint, store, recorded_by="ann-2",
        ask=_scripted(_keep(joint, first) + _change(joint, second)), say=lambda _: None,
        now=_clock(),
    )  # fmt: skip
    assert saved == 2
    records = decode_jsonl(store.path.read_bytes(), ConsensusAdjudication)
    by_pair = {(r.query_id, r.product_id): r for r in records}
    kept = by_pair[(first.query_id, first.product_id)]
    changed = by_pair[(second.query_id, second.product_id)]
    for item, record in ((first, kept), (second, changed)):
        pair = (item.query_id, item.product_id)
        assert record.reasons == item.reasons
        assert record.participants == ("ann-1", "ann-2")
        assert record.primary_label is joint.targeted.primaries[pair].label
        assert record.second_review_label is joint.second_reviews[pair].label
        assert record.rationale == RATIONALE
    assert kept.changes_label is False and changed.changes_label is True
    (change,) = decode_jsonl(store.changes_path.read_bytes(), LabelChange)
    assert change.kind is LabelChangeKind.CONSENSUS and change.changed_by == "ann-2"
    assert (change.previous_label, change.new_label) == (changed.primary_label, changed.label)
    assert change.reason == CHANGE and change.changed_at == changed.decided_at
    assert _tree(sealed[1]) == before  # no sealed record is written or changed


def test_a_skipped_pair_stays_unresolved(joint, store):
    first, second = joint.items[0], joint.items[1]
    said: list[str] = []
    saved = jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(["s", *_keep(joint, second), "q"]),
        say=said.append, now=_clock(),
    )  # fmt: skip
    assert saved == 1
    assert any("stays unresolved and blocks the label freeze" in line for line in said)
    records, _ = store.load(now=ADJ_T0 + timedelta(hours=1))
    assert {(r.query_id, r.product_id) for r in records} == {(second.query_id, second.product_id)}
    said = []
    jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(["q"]), say=said.append,
        now=_clock(ADJ_T0 + timedelta(hours=1)),
    )  # fmt: skip
    assert said[0] == f"1 of {len(joint.items)} joint-resolution pairs recorded; " + (
        f"{len(joint.items) - 1} unresolved"
    )
    assert f"pair 2 of {len(joint.items)}: {first.query_id} / {first.product_id}" in said[2]


def test_both_participants_must_confirm_and_save_needs_an_explicit_y(joint):
    item = joint.items[0]
    label = _letter(_primary(joint, item).label)
    said: list[str] = []
    answers = [
        "r", label, "1", RATIONALE, "ann-1", "ann-1",  # ann-2 did not confirm
        "r", label, "1", RATIONALE, "yes", # ann-1 did not confirm
        "r", label, "1", RATIONALE, "ann-1", "ann-2", "yes",  # not an explicit y
        "r", label, "1", RATIONALE, "ann-1", "ann-2", "y",
    ]  # fmt: skip
    result = jr.ask_joint(
        joint, item, _scripted(answers), said.append, _clock(), recorded_by="ann-1"
    )
    assert result is not None
    assert said.count("not confirmed by both participants; nothing was saved for this pair") == 2
    assert "discarded; nothing was saved for this pair" in said


def test_rationale_and_change_reason_are_required_and_free_text_is_checked(joint):
    item = joint.items[0]
    other = "n" if _primary(joint, item).label is Label.BORDERLINE else "b"
    said: list[str] = []
    answers = [
        "x", "r", "z", other, "9", "1",
        "",  # joint rationale required
        "mail a@b.co",  # free-text rules apply
        RATIONALE,
        "",  # the label changes: a change reason is required
        CHANGE, "ann-1", "ann-2", "y",
    ]  # fmt: skip
    record, change = jr.ask_joint(
        joint, item, _scripted(answers), said.append, _clock(), recorded_by="ann-1"
    )
    assert said.count("this is required here") == 2
    assert any(line.startswith("not accepted") for line in said)
    assert record.changes_label and change is not None and change.reason == CHANGE


def test_relevant_is_refused_for_a_hard_violation(joint):
    item = joint.items[0]
    pair = (item.query_id, item.product_id)
    stage = joint.targeted.sealed
    violated = dataclasses.replace(
        stage,
        eligibility={
            **stage.eligibility,
            pair: stage.eligibility[pair].model_copy(
                update={"hard_overall": OverallStatus.VIOLATED}
            ),
        },
    )
    ctx = dataclasses.replace(joint, targeted=dataclasses.replace(joint.targeted, sealed=violated))
    assert _primary(ctx, item).label is not Label.RELEVANT  # the fixture is never relevant
    said: list[str] = []
    answers = ["r", "r", *_keep(ctx, item)]  # relevant refused, then asked again from step 0
    jr.ask_joint(ctx, item, _scripted(answers), said.append, _clock(), recorded_by="ann-1")
    assert any("violates a hard gold constraint" in line for line in said)


@pytest.mark.parametrize("stop", ["q", None])
def test_stopping_mid_pair_saves_nothing_partial(joint, store, stop):
    answers = _keep(joint, joint.items[0]) + ["r", "b", "1"] + ([stop] if stop else [])
    saved = jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(answers), say=lambda _: None,
        now=_clock(),
    )  # fmt: skip
    assert saved == 1 and not store.lock_path.exists()
    records, _ = store.load(now=ADJ_T0 + timedelta(hours=1))
    assert len(records) == 1


def test_the_lock_is_exclusive_and_only_a_participant_records(joint, store):
    with pytest.raises(ContractError, match="--recorded-by"):
        jr.annotate(joint, store, recorded_by="ann-3", ask=_scripted([]), now=_clock())
    store.directory.mkdir(parents=True)
    store.lock_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="never removed automatically"):
        jr.annotate(joint, store, recorded_by="ann-1", ask=_scripted([]), now=_clock())
    assert store.lock_path.exists()


# ---- binding, validation and recovery ---------------------------------------------------------


def test_the_binding_is_tied_to_the_sealed_stage_and_window(joint, store):
    with pytest.raises(ContractError, match="created_at"):
        store.initialize(now=SECOND_SEALED_AT - timedelta(seconds=1))
        store.load(now=SECOND_SEALED_AT)
    store.binding_path.unlink(missing_ok=True)
    binding = store.initialize(now=ADJ_T0)
    assert binding.joint_resolution_queue_sha256 == (
        joint.targeted.manifest.entry(jr.Role.JOINT_RESOLUTION_QUEUE).file_sha256
    )
    assert store.initialize(now=ADJ_T0 + timedelta(days=1)) == binding  # written once
    # a canonically encoded binding for another agreement report
    tampered = binding.model_copy(update={"agreement_report_sha256": "0" * 64})
    store.binding_path.write_bytes(encode_json_object(tampered))
    with pytest.raises(ContractError, match="bound to another"):
        store.load(now=ADJ_T0)


def test_stored_records_that_break_the_contract_are_refused(joint, store):
    jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(_change(joint, joint.items[0])),
        say=lambda _: None, now=_clock(),
    )  # fmt: skip
    (record,) = decode_jsonl(store.path.read_bytes(), ConsensusAdjudication)
    (change,) = decode_jsonl(store.changes_path.read_bytes(), LabelChange)
    later = ADJ_T0 + timedelta(hours=1)
    other_reasons = tuple(sorted(set(JointReason) - set(record.reasons)))[:1]
    for update, match in (
        ({"reasons": other_reasons}, "routed reasons|disagreement reason"),
        ({"participants": ("ann-1", "ann-3")}, "both annotators"),
        ({"decided_at": SECOND_SEALED_AT}, "outside the allowed window"),
    ):
        with pytest.raises((ContractError, ValueError), match=match):
            broken = ConsensusAdjudication.model_validate(record.model_dump() | update)
            store.save([broken], [change], now=later)
    with pytest.raises(ContractError, match="exactly one consensus LabelChange"):
        store.save([record], [], now=later)
    with pytest.raises(ContractError, match="outside the consensus"):
        store.save([record], [change.model_copy(update={"changed_by": "ann-3"})], now=later)
    unrouted = next(
        p
        for p in joint.targeted.primaries
        if p not in {(i.query_id, i.product_id) for i in joint.items}
    )
    with pytest.raises(ContractError, match="not a pair of the joint-resolution queue"):
        store.save(
            [record.model_copy(update={"query_id": unrouted[0], "product_id": unrouted[1]})],
            [],
            now=later,
        )


def test_an_interrupted_save_is_detected_and_repaired(
    joint, store, capsys, monkeypatch, sealed, catalog
):
    _cli(monkeypatch, sealed, catalog)
    item = joint.items[0]
    jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(_change(joint, item)),
        say=lambda _: None, now=_clock(),
    )  # fmt: skip
    store.path.unlink()  # as if the process died after the label changes were written
    args = ["--golden-dir", str(sealed[1]), "--work-root", str(store.directory.parent)]
    assert jr.main([*args, "validate"]) == 1
    assert "interrupted save" in capsys.readouterr().err
    said: list[str] = []
    jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(["q"]), say=said.append,
        now=_clock(ADJ_T0 + timedelta(hours=1)),
    )  # fmt: skip
    assert any("removed 1 label change(s)" in line for line in said)
    assert store.changes_path.read_bytes() == b""
    assert jr.main([*args, "validate"]) == 0


def test_status_and_validate_report_counts_only(joint, store, capsys, monkeypatch, sealed, catalog):
    _cli(monkeypatch, sealed, catalog)
    args = ["--golden-dir", str(sealed[1]), "--work-root", str(store.directory.parent)]
    assert jr.main([*args, "status"]) == 0
    out = capsys.readouterr().out
    total = len(joint.items)
    assert f"0 of {total} joint-resolution pairs recorded; {total} unresolved" in out
    assert not store.directory.exists()  # status writes nothing
    assert jr.main([*args, "validate"]) == 0


# ---- the freeze gate ------------------------------------------------------------------------------


def test_complete_joint_records_pass_the_review_2_freeze_gate(joint, store):
    targeted = joint.targeted
    answers = [a for i, item in enumerate(joint.items) for a in (
        _change(joint, item) if i % 2 else _keep(joint, item)
    )]  # fmt: skip
    jr.annotate(
        joint, store, recorded_by="ann-1", ask=_scripted(answers), say=lambda _: None,
        now=_clock(),
    )  # fmt: skip
    records, changes = store.load(now=ADJ_T0 + timedelta(hours=1))
    assert len(records) == len(joint.items)
    singles = [
        Judgement(
            query_id=i.query_id,
            product_id=i.product_id,
            round=Round.ADJUDICATION,
            label=targeted.primaries[(i.query_id, i.product_id)].label,
            relation=targeted.primaries[(i.query_id, i.product_id)].relation,
            confidence=targeted.primaries[(i.query_id, i.product_id)].confidence,
            rationale="Synthetic adjudication fixture.",
            annotator_handle="ann-1",
            annotated_at=ADJ_T0,
            rubric_version=targeted.rubric_version,
            review_reasons=i.review_reasons,
            changes_label=False,
        )
        for i in targeted.mandatory
    ]

    def gate(consensus, consensus_changes):
        validate_review_2_adjudication(
            list(targeted.primaries.values()),
            list(joint.second_reviews.values()),
            list(targeted.queue),
            list(joint.items),
            singles,
            consensus,
            consensus_changes,
            targeted.golden_version,
            primary_sealed_at=targeted.manifest.primary_sealed_at,
            report=joint.report,
        )

    gate(records, changes)  # every routed pair has its joint record; mandatory items adjudicated
    # an unresolved routed pair (no record) blocks the freeze; nothing is inferred for it
    with pytest.raises(ContractError, match="unresolved disagreements and second-reviewer"):
        gate([r for r in records if not r.changes_label], [])
