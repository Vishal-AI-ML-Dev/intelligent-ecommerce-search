"""The queries-only freeze runner (Session C1). Every freeze here runs in a pytest temporary
directory with a fixed fixture time; the real pilot artifact is never frozen by these tests."""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from evals.contracts.canonical import ContractError, decode_json_object, decode_jsonl, sha256_hex
from evals.contracts.manifest import Role, Stage, decode_manifest, stage_roles
from evals.contracts.models import AuthoringAttestation, GoldConstraint
from evals.runners import freeze_queries as fq

ROOT = Path(__file__).resolve().parents[3]
REAL_DIR = ROOT / "evals" / "goldens" / "internal-search" / "v1"
INPUT = REAL_DIR / "authoring" / "pilot_input.json"
NOW = datetime(2026, 10, 12, 9, 30, 0, 654321, tzinfo=UTC)
EXPECTED = {
    "authoring/pilot_input.json",
    "queries.jsonl",
    "gold_constraints.jsonl",
    "product_intents.jsonl",
    "query_extraction.jsonl",
    "authoring_attestation.json",
    "manifests/1-queries_frozen.json",
}


def _tree(directory: Path) -> set[str]:
    return {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}


@pytest.fixture
def golden(tmp_path) -> tuple[Path, Path]:
    root = tmp_path / "goldens"
    target = root / "internal-search" / "v1"
    (target / "authoring").mkdir(parents=True)
    shutil.copyfile(INPUT, target / "authoring" / "pilot_input.json")
    return root, target


def _freeze(golden, catalog, **overrides):
    root, target = golden
    kwargs = dict(
        annotator="ann-1",
        confirm_human_review=True,
        input_sha256=sha256_hex(INPUT.read_bytes()),
        allowed_root=root,
        catalog=catalog,
        now=lambda: NOW,
    )
    kwargs.update(overrides)
    return fq.freeze(target, **kwargs)


def test_freeze_writes_exactly_the_queries_frozen_stage(golden, catalog):
    root, target = golden
    manifest = _freeze(golden, catalog)
    assert _tree(target) == EXPECTED  # no pool, provenance, label, metric or staging file
    assert manifest.stage is Stage.QUERIES_FROZEN and manifest.provenance_binding is None
    assert {e.role for e in manifest.entries} == stage_roles(Stage.QUERIES_FROZEN)
    assert manifest.versions.rubric == "rubric-1" and manifest.versions.parser == "qu-1"
    on_disk = decode_manifest((target / fq.MANIFEST_PATH).read_bytes())
    assert on_disk == manifest
    assert fq.verify(target, allowed_root=root, catalog=catalog) == manifest


def test_recorded_confirmation_bytes_are_preserved(golden, catalog):
    root, target = golden
    _freeze(golden, catalog)
    gold_bytes = (target / "gold_constraints.jsonl").read_bytes()
    constraints = decode_jsonl(gold_bytes, GoldConstraint)
    assert {(c.annotator_handle, c.recorded_at) for c in constraints} == {("ann-1", NOW)}
    attestation = decode_json_object(
        (target / "authoring_attestation.json").read_bytes(), AuthoringAttestation
    )
    assert attestation.confirmed_at == NOW
    assert attestation.input_sha256 == sha256_hex(INPUT.read_bytes())
    before = {p: ((target / p).read_bytes(), (target / p).stat().st_mtime_ns) for p in EXPECTED}
    fq.verify(target, allowed_root=root, catalog=catalog)
    after = {p: ((target / p).read_bytes(), (target / p).stat().st_mtime_ns) for p in EXPECTED}
    assert after == before  # verify is read-only


def test_repeat_runs_are_byte_identical(tmp_path, catalog):
    outputs = []
    for name in ("a", "b"):
        root = tmp_path / name / "goldens"
        target = root / "internal-search" / "v1"
        (target / "authoring").mkdir(parents=True)
        shutil.copyfile(INPUT, target / "authoring" / "pilot_input.json")
        _freeze((root, target), catalog)
        outputs.append({p: (target / p).read_bytes() for p in EXPECTED})
    assert outputs[0] == outputs[1]
    # The generated extraction does not depend on the confirmation time at all.
    later = tmp_path / "c" / "goldens"
    target = later / "internal-search" / "v1"
    (target / "authoring").mkdir(parents=True)
    shutil.copyfile(INPUT, target / "authoring" / "pilot_input.json")
    _freeze((later, target), catalog, now=lambda: datetime(2027, 1, 1, tzinfo=UTC))
    assert (target / "query_extraction.jsonl").read_bytes() == outputs[0]["query_extraction.jsonl"]
    assert (target / "gold_constraints.jsonl").read_bytes() != outputs[0]["gold_constraints.jsonl"]


def test_no_overwrite_after_freeze(golden, catalog):
    _freeze(golden, catalog)
    with pytest.raises(fq.FreezeError, match="overwrite"):
        _freeze(golden, catalog)


def test_any_preexisting_target_blocks_the_whole_freeze(golden, catalog):
    _, target = golden
    (target / "queries.jsonl").write_bytes(b"")
    with pytest.raises(fq.FreezeError, match="overwrite"):
        _freeze(golden, catalog)
    assert _tree(target) == {"authoring/pilot_input.json", "queries.jsonl"}


@pytest.mark.parametrize("fail_at", [1, 3, 6])
def test_a_failure_part_way_leaves_nothing_behind(golden, catalog, fail_at):
    _, target = golden
    calls = []

    def flaky(source, destination):
        calls.append(destination)
        if len(calls) == fail_at:
            raise OSError("simulated failure")
        os.link(source, destination)

    with pytest.raises(OSError, match="simulated"):
        _freeze(golden, catalog, link=flaky)
    assert {p.relative_to(target).as_posix() for p in target.rglob("*")} == {
        "authoring",
        "authoring/pilot_input.json",
    }


def test_the_manifest_is_linked_last(golden, catalog):
    order = []

    def recording(source, destination):
        order.append(destination.relative_to(golden[1]).as_posix())
        os.link(source, destination)

    _freeze(golden, catalog, link=recording)
    assert order[-1] == fq.MANIFEST_PATH and len(order) == 6


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confirm_human_review": False}, "confirm-human-review"),
        ({"annotator": "ann-2"}, "only 'ann-1'"),
        ({"input_sha256": "0" * 64}, "reviewed SHA-256"),
    ],
)
def test_confirmation_cannot_be_skipped_or_borrowed(golden, catalog, overrides, message):
    with pytest.raises(fq.FreezeError, match=message):
        _freeze(golden, catalog, **overrides)
    assert _tree(golden[1]) == {"authoring/pilot_input.json"}


def test_unsafe_golden_directories_are_refused(golden, tmp_path, catalog):
    root, target = golden
    with pytest.raises(fq.FreezeError, match=r"\.\."):
        fq.safe_golden_dir(target / ".." / "v1", root)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(fq.FreezeError, match="inside"):
        fq.safe_golden_dir(outside, root)
    with pytest.raises(fq.FreezeError, match="below the allowed root"):
        fq.safe_golden_dir(root, root)
    with pytest.raises(fq.FreezeError, match="does not exist"):
        fq.safe_golden_dir(root / "missing", root)
    assert fq.safe_golden_dir(target, root) == target


def test_symlinked_paths_are_refused(golden, monkeypatch):
    root, target = golden
    linked = target.parent

    original = Path.is_symlink

    def fake_is_symlink(path):
        return path == linked or original(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    with pytest.raises(fq.FreezeError, match="symlinks"):
        fq.safe_golden_dir(target, root)


def test_a_symlinked_input_or_target_is_refused(golden, catalog, monkeypatch):
    _, target = golden
    original = Path.is_symlink
    for relative in (fq.INPUT_PATH, "queries.jsonl"):
        monkeypatch.setattr(
            Path, "is_symlink", lambda p, r=relative: p == target / r or original(p)
        )
        with pytest.raises(fq.FreezeError, match="symlinks"):
            _freeze(golden, catalog)
    assert _tree(target) == {"authoring/pilot_input.json"}


def test_a_real_symlink_is_refused_where_the_os_allows_one(tmp_path):
    root = tmp_path / "goldens"
    real = tmp_path / "real-v1"
    real.mkdir()
    (root / "internal-search").mkdir(parents=True)
    try:
        os.symlink(real, root / "internal-search" / "v1", target_is_directory=True)
    except OSError:
        pytest.skip("this OS account cannot create symlinks")
    with pytest.raises(fq.FreezeError, match="symlinks"):
        fq.safe_golden_dir(root / "internal-search" / "v1", root)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b"\xef\xbb\xbf" + b, "no BOM"),
        (lambda b: b.replace(b"\n", b"\r\n"), "LF"),
        (
            lambda b: b.replace(
                b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,'
            ),
            "duplicate",
        ),
        (
            lambda b: b.replace(
                b'"rubric_version": "rubric-1"', b'"rubric_version": "rubric-1", "extra": 1'
            ),
            "invalid",
        ),
        (lambda b: b.replace(b'"text": "dell laptop"', b'"text": "dell  laptop"'), "records"),
    ],
)
def test_a_malformed_input_is_refused_before_writing(golden, catalog, mutate, message):
    _, target = golden
    path = target / fq.INPUT_PATH
    path.write_bytes(mutate(path.read_bytes()))
    with pytest.raises(fq.FreezeError, match=message):
        _freeze(golden, catalog, input_sha256=sha256_hex(path.read_bytes()))
    assert _tree(target) == {"authoring/pilot_input.json"}


def test_an_input_that_breaks_a_pilot_rule_is_refused(golden, catalog):
    _, target = golden
    path = target / fq.INPUT_PATH
    path.write_bytes(path.read_bytes().replace(b'"value": "NVME"', b'"value": "SATA"'))
    with pytest.raises(ContractError, match="gq-p08"):
        _freeze(golden, catalog, input_sha256=sha256_hex(path.read_bytes()))
    assert _tree(target) == {"authoring/pilot_input.json"}


def test_verify_detects_tampering(golden, catalog):
    root, target = golden
    _freeze(golden, catalog)
    extraction = target / "query_extraction.jsonl"
    original = extraction.read_bytes()
    extraction.write_bytes(original.replace(b'"fp1_applied":true', b'"fp1_applied":false', 1))
    with pytest.raises(ContractError, match="file_sha256"):
        fq.verify(target, allowed_root=root, catalog=catalog)
    extraction.write_bytes(original)
    path = target / fq.INPUT_PATH
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(fq.FreezeError, match="changed after it was confirmed"):
        fq.verify(target, allowed_root=root, catalog=catalog)


def test_cli_freeze_without_confirmation_writes_nothing(capsys):
    before = _tree(REAL_DIR)
    code = fq.main(["freeze", "--annotator", "ann-1", "--input-sha256", "0" * 64])
    assert code == 1 and "confirm-human-review" in capsys.readouterr().err
    assert _tree(REAL_DIR) == before


def test_preview_writes_nothing(capsys):
    before = _tree(REAL_DIR)
    assert fq.main(["preview"]) == 0
    out = capsys.readouterr().out
    assert f"input_sha256: {sha256_hex(INPUT.read_bytes())}" in out
    assert "| gq-p20 | smartwatch |" in out
    assert _tree(REAL_DIR) == before


def test_the_golden_directory_holds_no_pool_provenance_label_or_metric_file():
    """The queries stage never creates later-stage artifacts."""
    for name in ("pool.jsonl", "eligibility.jsonl", "provenance.jsonl"):
        assert not (REAL_DIR / name).exists()
    assert not list(REAL_DIR.rglob("*judgement*")) and not list(REAL_DIR.rglob("*metric*"))
    assert Role.POOL not in stage_roles(Stage.QUERIES_FROZEN)
