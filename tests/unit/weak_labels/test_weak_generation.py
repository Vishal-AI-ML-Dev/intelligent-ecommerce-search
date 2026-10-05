"""Proposal generation on a SYNTHETIC temporary pool with fake logits (no model, no network).
Nothing here is a real proposal; the real golden and weak-set directories are never written."""

import json

import pytest
from evals.contracts.canonical import decode_jsonl, sha256_hex
from evals.contracts.constants import Label
from evals.contracts.models import OverallStatus
from evals.weak_labels.contracts import (
    PROPOSER_HANDLE,
    ProposalManifest,
    WeakLabelProposal,
    WeakSignal,
)
from evals.weak_labels.generation import (
    MANIFEST_FILE,
    PROPOSALS_FILE,
    QUEUE_FILE,
    RUN_FILE,
    SIGNALS_FILE,
    RerankerPin,
    WeakLabelError,
    build_generation,
    files_from_signals,
    safe_weak_dir,
    signals_path,
    verify_generation,
)
from evals.weak_labels.runner import DeterminismError, compare_runs
from weak_support import PIN, generate, weak_fixture

PRIVATE_KEYS = {
    "reranker_logit",
    "reranker_rank_in_pool",
    "source_hits",
    "added_by",
    "rank",
    "score",
}


@pytest.fixture(scope="module")
def fx(tmp_path_factory, catalog):
    return weak_fixture(tmp_path_factory.mktemp("weakgen"), catalog)


@pytest.fixture(scope="module")
def stored(tmp_path_factory, catalog):
    fixture = weak_fixture(tmp_path_factory.mktemp("weakstored"), catalog)
    generate(fixture)
    return fixture


def test_generation_is_deterministic_and_covers_the_pool(fx):
    first = build_generation(fx.facts, fx.logits, PIN)
    second = build_generation(fx.facts, dict(reversed(list(fx.logits.items()))), PIN)
    assert first == second
    compare_runs(first, second)
    proposals = decode_jsonl(first[PROPOSALS_FILE], WeakLabelProposal)
    assert {(p.query_id, p.product_id) for p in proposals} == {
        (i.query_id, i.product_id) for i in fx.facts.pool
    }


def test_compare_runs_reports_file_and_line_only(fx):
    first = build_generation(fx.facts, fx.logits, PIN)
    changed = dict(first)
    changed[PROPOSALS_FILE] = first[PROPOSALS_FILE].replace(b"high", b"HIGH", 1)
    with pytest.raises(DeterminismError, match=r"proposals.jsonl line \d+"):
        compare_runs(first, changed)
    with pytest.raises(DeterminismError, match="missing"):
        compare_runs(first, {k: v for k, v in first.items() if k != SIGNALS_FILE})


def test_every_proposal_is_machine_authored_and_never_ann1(fx):
    files = build_generation(fx.facts, fx.logits, PIN)
    for line in files[PROPOSALS_FILE].decode().splitlines():
        record = json.loads(line)
        assert record["proposal_source"] == PROPOSER_HANDLE == "weak-label-policy-1"
        assert "ann-1" not in line
        assert not PRIVATE_KEYS & set(record)
        assert record["weak_set_id"] == "weak_dev_v1"


def test_hard_violations_are_never_proposed_relevant(fx):
    files = build_generation(fx.facts, fx.logits, PIN)
    for p in decode_jsonl(files[PROPOSALS_FILE], WeakLabelProposal):
        if p.hard_constraint_status is OverallStatus.VIOLATED:
            assert p.proposed_label is not Label.RELEVANT


def test_logits_must_reproduce_the_provenance_reranker_scores(fx):
    pair = next(
        (e.query_id, e.product_id)
        for e in fx.facts.provenance.entries
        if any(h.system.value.startswith("v3") and h.score for h in e.sources)
    )
    drifted = {**fx.logits, pair: fx.logits[pair] + 0.01}
    with pytest.raises(WeakLabelError, match="do not reproduce"):
        build_generation(fx.facts, drifted, PIN)
    missing = {k: v for k, v in fx.logits.items() if k != pair}
    with pytest.raises(WeakLabelError):
        build_generation(fx.facts, missing, PIN)


def test_reranker_pin_must_match_the_provenance(fx):
    other = RerankerPin(PIN.model_id, "0" * 40, PIN.config_sha256, PIN.document_version)
    with pytest.raises(WeakLabelError, match="pinned reranker"):
        build_generation(fx.facts, fx.logits, other)


def test_manifest_binds_files_signals_and_versions(fx):
    files = build_generation(fx.facts, fx.logits, PIN)
    manifest = ProposalManifest.model_validate_json(files[MANIFEST_FILE])
    assert manifest.signals_binding == sha256_hex(files[SIGNALS_FILE])
    assert {f.path: f.sha256 for f in manifest.files} == {
        PROPOSALS_FILE: sha256_hex(files[PROPOSALS_FILE]),
        QUEUE_FILE: sha256_hex(files[QUEUE_FILE]),
    }
    assert manifest.pool_manifest_sha256 == fx.facts.pool_manifest.manifest_sha256
    assert manifest.provenance_binding == fx.facts.pool_manifest.provenance_binding
    assert manifest.proposal_count == len(fx.facts.pool) and manifest.audit_count == 100
    assert manifest.policy_version == "weak-label-policy-1"
    assert manifest.audit_policy_version == "weak-audit-1"
    assert "generated_at" not in files[MANIFEST_FILE].decode()


def test_stored_generation_verifies_and_regenerates_byte_for_byte(stored):
    generation = verify_generation(
        stored.facts,
        stored.weak_dir,
        allowed_root=stored.weak_root,
        private_root=stored.signals_root,
        pin=PIN,
    )
    assert len(generation.proposals) == len(stored.facts.pool)
    assert (stored.weak_dir / RUN_FILE).is_file()
    signals = decode_jsonl(signals_path(stored.facts, stored.signals_root).read_bytes(), WeakSignal)
    regenerated = files_from_signals(stored.facts, signals, PIN)
    assert regenerated[PROPOSALS_FILE] == (stored.weak_dir / PROPOSALS_FILE).read_bytes()


def test_stored_generation_is_never_overwritten(stored):
    with pytest.raises(WeakLabelError, match="already exist"):
        generate(stored)


def test_tampered_public_or_private_files_fail_closed(tmp_path, catalog):
    fixture = weak_fixture(tmp_path, catalog)
    generate(fixture)

    def verify():
        verify_generation(
            fixture.facts,
            fixture.weak_dir,
            allowed_root=fixture.weak_root,
            private_root=fixture.signals_root,
            pin=PIN,
        )

    path = fixture.weak_dir / PROPOSALS_FILE
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"high"', b'"low"', 1))
    with pytest.raises(WeakLabelError, match="does not match"):
        verify()
    path.write_bytes(original)
    verify()
    private = signals_path(fixture.facts, fixture.signals_root)
    private.write_bytes(private.read_bytes().replace(b'"pool_size":', b'"pool_size":1', 1))
    with pytest.raises(WeakLabelError, match="signals"):
        verify()


def test_weak_artifacts_never_go_into_the_strict_golden_directory(fx):
    from evals.runners import build_pool

    with pytest.raises(WeakLabelError, match="strict Golden"):
        safe_weak_dir(build_pool.DEFAULT_GOLDEN_DIR, build_pool.ALLOWED_ROOT)
