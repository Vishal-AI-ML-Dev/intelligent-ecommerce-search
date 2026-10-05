"""Primary-seal tooling on a temporary frozen pool. Every judgement is a SYNTHETIC TEST FIXTURE;
the real golden directory is never sealed and no consistency clock is started for real data."""

from datetime import timedelta

import pytest
from evals.annotation.session import WorkStore, load_pool
from evals.contracts.canonical import ContractError, encode_jsonl, sha256_hex
from evals.contracts.manifest import Role, Stage, stage_roles
from evals.contracts.models import consistency_target
from evals.runners import seal_primary
from evals.runners.golden_io import FreezeError
from pool_support import LABEL_T0, frozen_pool, synthetic_primaries

NOW = LABEL_T0 + timedelta(days=2)


@pytest.fixture
def setup(tmp_path, catalog):
    root, target, private, work = frozen_pool(tmp_path, catalog)
    context = load_pool(target, allowed_root=root, catalog=catalog)
    return root, target, private, work, context


def _complete(context, work):
    store = WorkStore(context, work)
    store.initialize(now=LABEL_T0)
    judgements = synthetic_primaries(context.items)
    store.save(judgements, now=NOW)
    return store, judgements


def test_a_complete_round_passes_the_check(setup):
    _, _, _, work, context = setup
    store, judgements = _complete(context, work)
    checked = seal_primary.check_primary(
        context, store.path.read_bytes(), now=NOW, binding_created_at=LABEL_T0
    )
    assert len(checked) == len(context.items) == len(judgements)


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate", "foreign"])
def test_incomplete_or_foreign_rounds_are_refused(setup, problem):
    _, _, _, _, context = setup
    judgements = synthetic_primaries(context.items)
    if problem == "missing":
        data = encode_jsonl(judgements[1:])
    elif problem == "extra":
        extra = judgements[0].model_copy(update={"product_id": "SYN-XXX-0001"})
        data = encode_jsonl([*judgements, extra])
    elif problem == "duplicate":
        lines = encode_jsonl(judgements).split(b"\n")
        data = b"\n".join([lines[0], *lines])
    else:
        data = encode_jsonl([judgements[0].model_copy(update={"annotator_handle": "ann-2"})])
    with pytest.raises(ContractError):
        seal_primary.check_primary(context, data, now=NOW, binding_created_at=LABEL_T0)


def _seal(setup, **overrides):
    root, target, _, work, context = setup
    store = WorkStore(context, work)
    kwargs = dict(
        annotator="ann-1",
        confirm_human_review=True,
        judgements_sha256=sha256_hex(store.path.read_bytes()) if store.path.exists() else "0" * 64,
        work_root=work,
        allowed_root=root,
        catalog=context.catalog,
        now=lambda: NOW,
    )
    kwargs.update(overrides)
    return seal_primary.seal(target, **kwargs)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confirm_human_review": False}, "confirm-human-review"),
        ({"annotator": "ann-2"}, "only 'ann-1'"),
        ({"judgements_sha256": "0" * 64}, "reviewed SHA-256"),
    ],
)
def test_the_seal_needs_the_annotators_own_confirmation(setup, overrides, message):
    _, target, _, work, context = setup
    _complete(context, work)
    with pytest.raises(FreezeError, match=message):
        _seal(setup, **overrides)
    assert not (target / seal_primary.SEALED_MANIFEST_PATH).exists()


def test_an_incomplete_round_cannot_be_sealed(setup):
    _, target, _, work, context = setup
    partial = WorkStore(context, work)
    partial.initialize(now=LABEL_T0)
    partial.save(synthetic_primaries(context.items[:3]), now=NOW)
    with pytest.raises(ContractError, match="incomplete"):
        _seal(setup)
    assert not (target / seal_primary.SEALED_MANIFEST_PATH).exists()


def test_sealing_writes_primary_sealed_and_keeps_provenance_private(setup):
    _, target, private, work, context = setup
    _, judgements = _complete(context, work)
    manifest = _seal(setup)
    assert manifest.stage is Stage.PRIMARY_SEALED
    assert {e.role for e in manifest.entries} == stage_roles(Stage.PRIMARY_SEALED)
    assert Role.PROVENANCE not in {e.role for e in manifest.entries}
    assert manifest.provenance_binding == context.pool_manifest.provenance_binding
    assert manifest.previous_manifest_sha256 == context.pool_manifest.manifest_sha256
    assert manifest.primary_sealed_at == NOW >= max(j.annotated_at for j in judgements)
    assert manifest.labels_frozen_at is None
    sample = (target / "consistency_sample.json").read_text(encoding="utf-8")
    assert f'"target_count":{consistency_target(len(judgements))}' in sample
    assert '"label"' not in sample.split('"items"')[1]  # no previous labels in the sample
    assert not any("provenance" in p.name for p in target.rglob("*"))
    with pytest.raises(ContractError, match="already sealed"):
        _seal(setup)
