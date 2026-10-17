"""Regression tests for the six M9 Session B code-review findings (synthetic fixtures only)."""

from datetime import timedelta

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.constants import Label
from evals.contracts.models import (
    LabelChange,
    LabelChangeKind,
    QueueItem,
    ReviewReason,
    Round,
)
from evals.contracts.review_sets import build_consistency_sample
from evals.contracts.validation import validate_adjudication_round, validate_consistency_round
from golden_support import HANDLE, T0, after_delay, judgement

# ---- finding 4: an adjudicated pair without a primary judgement is a contract error ----------


def test_adjudication_of_an_unjudged_pair_is_a_contract_error():
    primaries = [judgement("gq-p01", "P1")]
    queue = [
        QueueItem(
            query_id="gq-p01", product_id="P-unpooled", review_reasons=(ReviewReason.BORDERLINE,)
        )
    ]
    adjudications = [
        judgement(
            "gq-p01",
            "P-unpooled",
            Label.BORDERLINE,
            round_=Round.ADJUDICATION,
            at=after_delay(100),
            review_reasons=(ReviewReason.BORDERLINE,),
            changes_label=False,
        )
    ]
    with pytest.raises(ContractError, match="no primary judgement"):
        validate_adjudication_round(
            primaries, [], queue, adjudications, [], golden_version=1, primary_sealed_at=T0
        )


# ---- finding 2: the seal cannot be backdated before the primary labels -----------------------

SEAL = "d" * 64


def test_seal_time_cannot_predate_the_primary_judgements():
    # Labelled at T0+500h but "sealed" at T0: repeats at T0+501h would be only 1 h later.
    primaries = [judgement("gq-p01", f"P{i:02d}", at=after_delay(500)) for i in range(10)]
    sample = build_consistency_sample(primaries, SEAL, T0)
    repeats = [
        judgement(i.query_id, i.product_id, round_=Round.CONSISTENCY, at=after_delay(501))
        for i in sample.items
    ]
    with pytest.raises(ContractError, match="earlier than the latest primary judgement"):
        validate_consistency_round(primaries, sample, repeats, SEAL, T0)


# ---- finding 3: the consistency round must cover the whole sample ----------------------------


def test_consistency_round_must_repeat_every_sampled_item():
    primaries = [judgement("gq-p01", f"P{i:02d}") for i in range(10)]
    sample = build_consistency_sample(primaries, SEAL, T0)
    assert sample.actual_count == 2
    repeats = [
        judgement(i.query_id, i.product_id, round_=Round.CONSISTENCY, at=after_delay(72))
        for i in sample.items
    ]
    validate_consistency_round(primaries, sample, repeats, SEAL, T0)
    with pytest.raises(ContractError, match="missing from the consistency round"):
        validate_consistency_round(primaries, sample, repeats[:1], SEAL, T0)
    with pytest.raises(ContractError, match="missing from the consistency round"):
        validate_consistency_round(primaries, sample, [], SEAL, T0)


# ---- finding 5: one annotator and correct ordering in adjudication ---------------------------


def _disagreement(adjudicated_at=None, changed_at=None, changed_by=HANDLE):
    adjudicated_at = adjudicated_at or after_delay(100)
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
            at=adjudicated_at,
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
            changed_by=changed_by,
            changed_at=changed_at or adjudicated_at,
            from_golden_version=1,
            to_golden_version=1,
        )
    ]
    return primaries, repeats, adjudications, changes


def test_label_change_must_come_from_the_single_annotator():
    primaries, repeats, adjudications, changes = _disagreement(changed_by="ann-9")
    with pytest.raises(ContractError, match="exactly one annotator"):
        validate_adjudication_round(
            primaries, repeats, [], adjudications, changes, golden_version=1, primary_sealed_at=T0
        )


def test_adjudication_cannot_predate_the_seal_or_its_consistency_repeat():
    sealed = after_delay(1)
    ok = _disagreement()
    validate_adjudication_round(*ok[:2], [], *ok[2:], golden_version=1, primary_sealed_at=sealed)
    backdated = _disagreement(adjudicated_at=T0 - timedelta(days=30))
    with pytest.raises(ContractError, match="before the primary seal"):
        validate_adjudication_round(
            *backdated[:2], [], *backdated[2:], golden_version=1, primary_sealed_at=sealed
        )
    before_repeat = _disagreement(adjudicated_at=after_delay(72.5))  # repeat is at +73 h
    with pytest.raises(ContractError, match="before the consistency judgement"):
        validate_adjudication_round(
            *before_repeat[:2], [], *before_repeat[2:], golden_version=1, primary_sealed_at=sealed
        )
    early_change = _disagreement(changed_at=after_delay(0.5))
    with pytest.raises(ContractError, match="before the primary seal"):
        validate_adjudication_round(
            *early_change[:2], [], *early_change[2:], golden_version=1, primary_sealed_at=sealed
        )


# ---- finding 6: post-freeze corrections are checked against both versions --------------------
# Covered by the manifest-bound tests in test_golden_correction_invariants.py.


# ---- finding 1: golden version N+1 binds version N's terminal manifest ----------------------


def test_a_corrected_version_supersedes_the_previous_terminal_manifest(catalog):
    from evals.contracts.manifest import (
        STAGE_ORDER,
        Stage,
        validate_succession,
        validate_transition,
    )
    from pydantic import ValidationError
    from test_golden_manifest import _files, _manifest

    files = _files(catalog)
    v1: list = []
    for stage in STAGE_ORDER:
        v1.append(_manifest(stage, files, v1[-1] if v1 else None))
    terminal = v1[-1]
    assert terminal.supersedes_manifest_sha256 is None

    def v2_first(**overrides):
        fields = {"golden_version": 2, "supersedes_manifest_sha256": terminal.manifest_sha256}
        return _manifest(Stage.QUERIES_FROZEN, files, None, **{**fields, **overrides})

    first = v2_first()
    validate_succession(terminal, first)
    second = _manifest(
        Stage.POOL_FROZEN,
        files,
        first,
        golden_version=2,
        supersedes_manifest_sha256=terminal.manifest_sha256,
    )
    validate_transition(first, second)
    with pytest.raises(ValidationError, match="supersedes_manifest_sha256"):
        v2_first(supersedes_manifest_sha256=None)  # v2 must name what it supersedes
    with pytest.raises(ValidationError, match="supersedes_manifest_sha256"):
        _manifest(Stage.QUERIES_FROZEN, files, None, supersedes_manifest_sha256="1" * 64)  # v1
    with pytest.raises(ContractError, match="terminal"):
        validate_succession(v1[3], v2_first(supersedes_manifest_sha256=v1[3].manifest_sha256))
    with pytest.raises(ContractError, match="does not point"):
        validate_succession(terminal, v2_first(supersedes_manifest_sha256="1" * 64))
    with pytest.raises(ContractError, match="next golden version"):
        validate_succession(terminal, v2_first(golden_version=3))
    with pytest.raises(ContractError, match="first stage"):
        validate_succession(terminal, second)
    drifted = _manifest(
        Stage.POOL_FROZEN, files, first, golden_version=2, supersedes_manifest_sha256="1" * 64
    )
    with pytest.raises(ContractError, match="supersedes_manifest_sha256 changed"):
        validate_transition(first, drifted)
