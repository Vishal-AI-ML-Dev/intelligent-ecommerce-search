"""The review-2 label-freeze runner on a temporary golden directory sealed through
`second_review_sealed` from SYNTHETIC judgements (the fixture of the adjudication tests). Every
judgement, adjudication, joint record and confirmation here is a SYNTHETIC TEST FIXTURE typed
by scripted fake input, never a human decision. The real golden directory and the real work
directories are never read or written."""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import test_golden_adjudication_review_2 as adjudication_tests
import test_golden_joint_resolution_review_2 as joint_tests
from evals.annotation import adjudication as adj
from evals.annotation import joint_resolution as jr
from evals.contracts.canonical import (
    ContractError,
    decode_jsonl,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.constants import DISCLOSURE_REVIEW_2
from evals.contracts.manifest import Role, Stage, decode_manifest, validate_chain
from evals.contracts.models import (
    ConsensusAdjudication,
    FinalJudgement,
    Judgement,
    LabelChange,
    Partition,
    PartitionAssignment,
)
from evals.contracts.review_sets import derive_final_judgements_review_2
from evals.runners import freeze_labels as fl
from evals.runners.golden_io import FreezeError
from test_golden_adjudication_review_2 import (
    ADJ_T0,
    _clock,
    _correct,
    _retain,
    _scripted,
    _tree,
)

# The same SYNTHETIC golden directory sealed through `second_review_sealed`, registered here.
sealed_template = adjudication_tests.sealed_template
sealed = adjudication_tests.sealed

JOINT_T0 = ADJ_T0 + timedelta(hours=2)
FREEZE_AT = ADJ_T0 + timedelta(days=1)
NEW_PATHS = set(fl.FREEZE_PATHS.values()) | {fl.LABELS_MANIFEST_PATH}


@dataclass(frozen=True)
class Worked:
    root: Path
    target: Path
    adjudication_root: Path
    joint_root: Path
    catalog: object

    @property
    def adjudication_dir(self) -> Path:
        return next(self.adjudication_root.iterdir())

    @property
    def joint_dir(self) -> Path:
        return next(self.joint_root.iterdir())

    def kwargs(self) -> dict:
        return {
            "allowed_root": self.root,
            "catalog": self.catalog,
            "adjudication_root": self.adjudication_root,
            "joint_root": self.joint_root,
        }


def _record_adjudications(targeted, store, items, start, *, include_optional=False):
    """SYNTHETIC answers: retain most labels, correct every third."""
    answers = [a for i, item in enumerate(items) for a in (
        _correct(targeted, item) if i % 3 == 1 else _retain(targeted, item)
    )]  # fmt: skip
    return adj.annotate(
        targeted, store, annotator="ann-1", ask=_scripted(answers), say=lambda _: None,
        now=_clock(start), include_optional=include_optional,
    )  # fmt: skip


@pytest.fixture
def worked(sealed, catalog, tmp_path):
    """SYNTHETIC complete work: every mandatory targeted item adjudicated by scripted input,
    every joint-resolution pair recorded by scripted input, no optional candidate adjudicated."""
    root, target = sealed
    targeted = adj.load_targeted(target, allowed_root=root, catalog=catalog)
    assert targeted.mandatory and targeted.optional
    adjudication_root = tmp_path / "adjudication-work"
    store = adj.AdjudicationStore(targeted, adjudication_root)
    saved = _record_adjudications(targeted, store, targeted.mandatory, ADJ_T0)
    assert saved == len(targeted.mandatory)
    joint = jr.load_joint(target, allowed_root=root, catalog=catalog)
    joint_root = tmp_path / "joint-work"
    answers = [a for i, item in enumerate(joint.items) for a in (
        joint_tests._change(joint, item) if i % 2 else joint_tests._keep(joint, item)
    )]  # fmt: skip
    saved = jr.annotate(
        joint, jr.JointStore(joint, joint_root), recorded_by="ann-2", ask=_scripted(answers),
        say=lambda _: None, now=_clock(JOINT_T0),
    )  # fmt: skip
    assert saved == len(joint.items)
    return Worked(root, target, adjudication_root, joint_root, catalog)


def _check(worked: Worked) -> fl.FreezeInputs:
    return fl.check(worked.target, **worked.kwargs(), now=lambda: FREEZE_AT)


def _freeze(worked: Worked, digest: str, **overrides):
    arguments = {
        "annotator": "ann-1",
        "confirm_human_review": True,
        "inputs_sha256": digest,
        **worked.kwargs(),
        "now": lambda: FREEZE_AT,
        **overrides,
    }
    return fl.freeze(worked.target, **arguments)


def _trees(worked: Worked) -> tuple[dict, dict, dict]:
    return (_tree(worked.target), _tree(worked.adjudication_root), _tree(worked.joint_root))


# ---- check ------------------------------------------------------------------------------------


def test_check_validates_everything_and_writes_nothing(worked):
    before = _trees(worked)
    inputs = _check(worked)
    assert _trees(worked) == before
    targeted = inputs.joint.targeted
    assert len(inputs.consensus) == len(inputs.joint.items)
    assert {(j.query_id, j.product_id) for j in inputs.adjudications} == {
        (i.query_id, i.product_id) for i in targeted.mandatory
    }
    assert inputs.digests() == {
        "second_review_manifest_sha256": targeted.manifest.manifest_sha256,
        "adjudication_judgements_sha256": sha256_hex(
            (worked.adjudication_dir / adj.WORK_FILE).read_bytes()
        ),
        "adjudication_label_changes_sha256": sha256_hex(
            (worked.adjudication_dir / adj.CHANGES_FILE).read_bytes()
        ),
        "consensus_adjudications_sha256": sha256_hex(
            (worked.joint_dir / jr.RECORDS_FILE).read_bytes()
        ),
        "consensus_label_changes_sha256": sha256_hex(
            (worked.joint_dir / jr.CHANGES_FILE).read_bytes()
        ),
    }
    lines = fl.summary_lines(inputs, frozen=False)
    optional = len(targeted.optional)
    assert "unresolved: 0" in lines[3]
    assert (
        f"optional review candidates: {optional}; adjudicated: 0; not adjudicated: {optional}"
        in lines[6]
    )
    assert "to be used only once the labels are frozen" in lines[-2]
    assert DISCLOSURE_REVIEW_2 in lines[-2]
    assert fl.PARTITION_NOTE in lines[-3]


def test_check_cli_prints_counts_and_hashes_only(worked, monkeypatch, capsys):
    loader = jr.load_joint
    monkeypatch.setattr(
        fl, "load_joint", lambda golden_dir, allowed_root, catalog: loader(
            golden_dir, allowed_root=worked.root, catalog=worked.catalog
        ),
    )  # fmt: skip
    before = _trees(worked)
    roots = [
        "--golden-dir", str(worked.target),
        "--adjudication-root", str(worked.adjudication_root),
        "--joint-root", str(worked.joint_root),
    ]  # fmt: skip
    assert fl.main([*roots, "check"]) == 0
    out = capsys.readouterr().out
    assert f"inputs_sha256 {_check(worked).inputs_sha256}" in out
    assert "joint records" in out and "not adjudicated" in out
    assert fl.main([*roots, "freeze", "--annotator", "ann-1", "--inputs-sha256", "0" * 64]) == 1
    assert "confirm-human-review" in capsys.readouterr().err
    assert _trees(worked) == before


# ---- freeze -----------------------------------------------------------------------------------


def test_freeze_writes_the_labels_frozen_stage_manifest_last(worked):
    golden_before, adjudication_before, joint_before = _trees(worked)
    inputs = _check(worked)
    order: list[str] = []

    def link(source, target):
        order.append(Path(target).relative_to(worked.target).as_posix())
        os.link(source, target)

    manifest, _ = _freeze(worked, inputs.inputs_sha256, link=link)
    assert order[-1] == fl.LABELS_MANIFEST_PATH and set(order) == NEW_PATHS
    after = _tree(worked.target)
    assert {p: after[p] for p in golden_before} == golden_before  # sealed inputs preserved
    assert set(after) - set(golden_before) == NEW_PATHS
    assert (_tree(worked.adjudication_root), _tree(worked.joint_root)) == (
        adjudication_before,
        joint_before,
    )
    assert manifest.stage is Stage.LABELS_FROZEN and manifest.review_protocol == "review-2"
    assert manifest.labels_frozen_at == FREEZE_AT
    chain = [
        decode_manifest(p.read_bytes())
        for p in sorted((worked.target / "manifests").iterdir(), key=lambda p: p.name)
    ]
    validate_chain(chain)
    assert chain[-1] == manifest
    assert chain[-2].manifest_sha256 == manifest.previous_manifest_sha256

    def written(role):
        return (worked.target / fl.FREEZE_PATHS[role]).read_bytes()

    assert (
        written(Role.ADJUDICATION_JUDGEMENTS)
        == (worked.adjudication_dir / adj.WORK_FILE).read_bytes()
    )
    assert (
        written(Role.CONSENSUS_ADJUDICATIONS) == (worked.joint_dir / jr.RECORDS_FILE).read_bytes()
    )
    changes = decode_jsonl(written(Role.LABEL_CHANGES), LabelChange)
    adjudication_changes = decode_jsonl(
        (worked.adjudication_dir / adj.CHANGES_FILE).read_bytes(), LabelChange
    )
    consensus_changes = decode_jsonl((worked.joint_dir / jr.CHANGES_FILE).read_bytes(), LabelChange)
    assert sorted(changes, key=lambda c: c.sort_key()) == sorted(
        [*adjudication_changes, *consensus_changes], key=lambda c: c.sort_key()
    )
    assert adjudication_changes and consensus_changes
    primaries = decode_jsonl((worked.target / "primary_judgements.jsonl").read_bytes(), Judgement)
    finals = decode_jsonl(written(Role.FINAL_JUDGEMENTS), FinalJudgement)
    assert finals == derive_final_judgements_review_2(
        primaries,
        decode_jsonl(written(Role.ADJUDICATION_JUDGEMENTS), Judgement),
        decode_jsonl(written(Role.CONSENSUS_ADJUDICATIONS), ConsensusAdjudication),
    )
    assert len(finals) == len(primaries)
    decided = {f.decided_in for f in finals}
    assert decided == {"primary", "adjudication", "consensus"}
    optional = {(i.query_id, i.product_id) for i in inputs.joint.targeted.optional}
    assert all(f.decided_in == "primary" for f in finals if (f.query_id, f.product_id) in optional)


def test_partitions_only_restate_the_frozen_query_records(worked):
    inputs = _check(worked)
    _freeze(worked, inputs.inputs_sha256)
    partitions = decode_jsonl(
        (worked.target / fl.FREEZE_PATHS[Role.PARTITIONS]).read_bytes(), PartitionAssignment
    )
    queries = inputs.joint.targeted.sealed.queries
    assert [(p.query_id, p.partition) for p in partitions] == [
        (q.query_id, q.partition) for q in sorted(queries.values(), key=lambda q: q.query_id)
    ]
    assert {p.partition for p in partitions} == {Partition.PILOT_DEV}  # no test partition


def test_freeze_needs_the_annotator_confirmation_and_the_reviewed_inputs(worked):
    before = _trees(worked)
    digest = _check(worked).inputs_sha256
    with pytest.raises(FreezeError, match="confirm-human-review"):
        _freeze(worked, digest, confirm_human_review=False)
    with pytest.raises(FreezeError, match="only 'ann-1'"):
        _freeze(worked, digest, annotator="ann-2")
    with pytest.raises(FreezeError, match="differ from the reviewed SHA-256"):
        _freeze(worked, "0" * 64)
    assert _trees(worked) == before


def test_the_inputs_hash_binds_the_exact_work_bytes(worked):
    """A SYNTHETIC optional adjudication added after `check` changes the inputs: the reviewed
    hash no longer matches. A fresh check counts that candidate as adjudicated."""
    digest = _check(worked).inputs_sha256
    targeted = adj.load_targeted(worked.target, allowed_root=worked.root, catalog=worked.catalog)
    store = adj.AdjudicationStore(targeted, worked.adjudication_root)
    first_optional = targeted.optional[0]
    answers = _retain(targeted, first_optional) + ["q"]
    adj.annotate(
        targeted, store, annotator="ann-1", ask=_scripted(answers), say=lambda _: None,
        now=_clock(ADJ_T0 + timedelta(hours=3)), include_optional=True,
    )  # fmt: skip
    with pytest.raises(FreezeError, match="differ from the reviewed SHA-256"):
        _freeze(worked, digest)
    inputs = _check(worked)
    assert inputs.inputs_sha256 != digest
    optional = len(targeted.optional)
    assert (
        f"optional review candidates: {optional}; adjudicated: 1; not adjudicated: "
        f"{optional - 1}" in fl.summary_lines(inputs, frozen=False)[6]
    )


# ---- refusals ---------------------------------------------------------------------------------


def test_a_missing_joint_record_blocks_the_freeze(worked):
    path, changes_path = worked.joint_dir / jr.RECORDS_FILE, worked.joint_dir / jr.CHANGES_FILE
    records = decode_jsonl(path.read_bytes(), ConsensusAdjudication)
    dropped = records[-1]
    changes = decode_jsonl(changes_path.read_bytes(), LabelChange)
    changes_path.write_bytes(encode_jsonl(
        [c for c in changes if (c.query_id, c.product_id) != (dropped.query_id, dropped.product_id)]
    ))  # fmt: skip
    path.write_bytes(encode_jsonl(records[:-1]))
    before = _tree(worked.target)
    with pytest.raises(ContractError, match="no joint record"):
        _check(worked)
    assert _tree(worked.target) == before


def test_an_unresolved_mandatory_item_blocks_the_freeze(worked):
    path = worked.adjudication_dir / adj.WORK_FILE
    changes_path = worked.adjudication_dir / adj.CHANGES_FILE
    judgements = decode_jsonl(path.read_bytes(), Judgement)
    kept = [j for j in judgements if not j.changes_label][1:]
    kept += [j for j in judgements if j.changes_label]
    kept.sort(key=lambda j: j.sort_key())
    path.write_bytes(encode_jsonl(kept))
    changes = decode_jsonl(changes_path.read_bytes(), LabelChange)
    assert len(changes) == sum(j.changes_label for j in kept)  # still in sync
    with pytest.raises(ContractError, match="mandatory targeted-queue item"):
        _check(worked)


def test_out_of_sync_or_orphaned_label_changes_are_refused(worked):
    adjudication_changes = worked.adjudication_dir / adj.CHANGES_FILE
    original = adjudication_changes.read_bytes()
    assert original
    adjudication_changes.write_bytes(b"")
    with pytest.raises(FreezeError, match="does not match the adjudications"):
        _check(worked)
    adjudication_changes.write_bytes(original)
    path = worked.joint_dir / jr.RECORDS_FILE
    records = decode_jsonl(path.read_bytes(), ConsensusAdjudication)
    changed = next(r for r in records if r.changes_label)
    path.write_bytes(encode_jsonl([r for r in records if r != changed]))
    with pytest.raises(FreezeError, match="interrupted save"):
        _check(worked)


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        ("adjudication_dir", adj.WORK_FILE),
        ("adjudication_dir", adj.CHANGES_FILE),
        ("adjudication_dir", adj.BINDING_FILE),
        ("joint_dir", jr.RECORDS_FILE),
        ("joint_dir", jr.CHANGES_FILE),
        ("joint_dir", jr.BINDING_FILE),
    ],
)
def test_a_missing_work_file_is_refused(worked, directory, name):
    (getattr(worked, directory) / name).unlink()
    with pytest.raises(FreezeError, match=f"has no {name}"):
        _check(worked)


@pytest.mark.parametrize(
    ("directory", "name"), [("adjudication_dir", adj.LOCK_FILE), ("joint_dir", jr.LOCK_FILE)]
)
def test_a_session_lock_is_refused(worked, directory, name):
    (getattr(worked, directory) / name).write_text("{}\n", encoding="utf-8")
    with pytest.raises(FreezeError, match="lock exists"):
        _check(worked)


def test_existing_targets_are_never_overwritten(worked):
    digest = _check(worked).inputs_sha256
    stray = worked.target / fl.FREEZE_PATHS[Role.FINAL_JUDGEMENTS]
    stray.write_bytes(b"")
    before = _tree(worked.target)
    with pytest.raises(FreezeError, match="refusing to overwrite"):
        _freeze(worked, digest)
    assert _tree(worked.target) == before
    stray.unlink()
    _freeze(worked, digest)
    after = _tree(worked.target)
    with pytest.raises(ContractError, match="no later stage"):  # a second freeze
        _freeze(worked, digest)
    assert _tree(worked.target) == after


def test_a_failed_write_leaves_nothing_behind(worked):
    digest = _check(worked).inputs_sha256
    before = _tree(worked.target)

    def link(source, target):
        if Path(target).name == Path(fl.LABELS_MANIFEST_PATH).name:
            raise OSError("synthetic failure before the manifest")
        os.link(source, target)

    with pytest.raises(OSError, match="synthetic failure"):
        _freeze(worked, digest, link=link)
    assert _tree(worked.target) == before
    assert not list(worked.target.glob(".freeze-staging-*"))


def test_a_changed_sealed_file_is_refused(worked):
    path = worked.target / "joint_resolution_queue.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ContractError):
        _check(worked)
