"""The provenance-unsealing runner on a temporary golden directory frozen through `labels_frozen`
from SYNTHETIC judgements, adjudications and joint records (the fixtures of the label-freeze
tests), with the SYNTHETIC private provenance written by the synthetic pool fixture. Nothing
here is a human decision. The real golden directory and the real private provenance are never
read or written."""

import functools
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
import test_golden_adjudication_review_2 as adjudication_tests
import test_golden_freeze_labels_review_2 as freeze_tests
from evals.contracts.canonical import ContractError, sha256_hex
from evals.contracts.manifest import Role, Stage, decode_manifest, validate_chain
from evals.runners import unseal_provenance as up
from evals.runners.golden_io import FreezeError
from test_golden_adjudication_review_2 import _tree

sealed_template = adjudication_tests.sealed_template
sealed = adjudication_tests.sealed
worked = freeze_tests.worked


@dataclass(frozen=True)
class Frozen:
    root: Path
    target: Path
    private_root: Path
    catalog: object

    def kwargs(self) -> dict:
        return {
            "allowed_root": self.root,
            "private_root": self.private_root,
            "catalog": self.catalog,
        }

    @property
    def private_file(self) -> Path:
        return next(self.private_root.glob(f"*/{up.build_pool.PROVENANCE_FILE}"))


@pytest.fixture
def frozen(worked, sealed_template, tmp_path):
    """SYNTHETIC labels freeze of the synthetic worked fixture, with a private copy of the
    synthetic provenance."""
    private_root = tmp_path / "private"
    shutil.copytree(sealed_template / "private", private_root)
    digest = freeze_tests._check(worked).inputs_sha256
    freeze_tests._freeze(worked, digest)
    return Frozen(worked.root, worked.target, private_root, worked.catalog)


def _unseal(frozen: Frozen, **overrides):
    inputs = up.check(frozen.target, **frozen.kwargs())
    arguments = {
        "confirm_unseal": True,
        "labels_frozen_sha256": inputs.manifest.manifest_sha256,
        "provenance_sha256": sha256_hex(inputs.provenance_data),
        **frozen.kwargs(),
        **overrides,
    }
    return up.unseal(frozen.target, **arguments)


def _trees(frozen: Frozen) -> tuple[dict, dict]:
    return _tree(frozen.target), _tree(frozen.private_root)


def test_check_is_read_only_and_reports_hashes_and_counts_only(frozen):
    before = _trees(frozen)
    inputs = up.check(frozen.target, **frozen.kwargs())
    assert _trees(frozen) == before
    assert inputs.manifest.stage is Stage.LABELS_FROZEN
    assert sha256_hex(inputs.provenance_data) == inputs.manifest.provenance_binding
    assert inputs.provenance_path == frozen.private_file
    lines = up.summary_lines(inputs)
    assert f"provenance_binding {inputs.manifest.provenance_binding}" in lines
    product_ids = {e.product_id for e in inputs.provenance.entries}
    assert not any(pid in line for pid in product_ids for line in lines)


def test_unseal_adds_only_the_bound_provenance_and_writes_the_manifest_last(frozen):
    golden_before, private_before = _trees(frozen)
    previous = up.check(frozen.target, **frozen.kwargs()).manifest
    order: list[str] = []

    def link(source, target):
        order.append(Path(target).relative_to(frozen.target).as_posix())
        os.link(source, target)

    manifest = _unseal(frozen, link=link)
    assert order == [up.PROVENANCE_PATH, up.UNSEALED_MANIFEST_PATH]
    after = _tree(frozen.target)
    assert {p: after[p] for p in golden_before} == golden_before  # frozen artifacts preserved
    assert set(after) - set(golden_before) == {up.PROVENANCE_PATH, up.UNSEALED_MANIFEST_PATH}
    assert _tree(frozen.private_root) == private_before  # the private file is only read
    assert (frozen.target / up.PROVENANCE_PATH).read_bytes() == frozen.private_file.read_bytes()
    assert manifest.stage is Stage.PROVENANCE_UNSEALED and manifest.review_protocol == "review-2"
    entry = manifest.entry(Role.PROVENANCE)
    assert entry.file_sha256 == manifest.provenance_binding == previous.provenance_binding
    assert manifest.labels_frozen_at == previous.labels_frozen_at
    assert manifest.previous_manifest_sha256 == previous.manifest_sha256
    assert {e for e in manifest.entries if e.role is not Role.PROVENANCE} == set(previous.entries)
    chain = [
        decode_manifest(p.read_bytes())
        for p in sorted((frozen.target / "manifests").iterdir(), key=lambda p: p.name)
    ]
    validate_chain(chain)
    assert chain[-1] == manifest
    with pytest.raises(ContractError, match="no later stage"):  # a second unseal
        _unseal(frozen)


def test_unseal_needs_explicit_authorization_and_the_reviewed_hashes(frozen):
    before = _trees(frozen)
    with pytest.raises(FreezeError, match="confirm-unseal"):
        _unseal(frozen, confirm_unseal=False)
    with pytest.raises(FreezeError, match="labels_frozen manifest differs"):
        _unseal(frozen, labels_frozen_sha256="0" * 64)
    with pytest.raises(FreezeError, match="provenance differs"):
        _unseal(frozen, provenance_sha256="0" * 64)
    assert _trees(frozen) == before


def test_a_missing_or_changed_private_provenance_is_refused(frozen):
    before = _tree(frozen.target)
    original = frozen.private_file.read_bytes()
    frozen.private_file.write_bytes(original.replace(b"\n", b" \n", 1))
    with pytest.raises(ContractError, match="no private provenance file matches"):
        up.check(frozen.target, **frozen.kwargs())
    frozen.private_file.unlink()
    with pytest.raises(ContractError, match="no private provenance file matches"):
        up.check(frozen.target, **frozen.kwargs())
    assert _tree(frozen.target) == before


def test_unsealing_before_the_labels_freeze_is_refused(sealed, sealed_template, catalog):
    root, target = sealed
    with pytest.raises(ContractError, match="expected exactly the chain through labels_frozen"):
        up.check(
            target, allowed_root=root, private_root=sealed_template / "private", catalog=catalog
        )


def test_a_changed_frozen_label_file_is_refused(frozen):
    path = frozen.target / "final_judgements.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ContractError):
        up.check(frozen.target, **frozen.kwargs())


def test_existing_targets_are_never_overwritten_and_failures_roll_back(frozen):
    stray = frozen.target / up.PROVENANCE_PATH
    stray.write_bytes(b"")
    before = _tree(frozen.target)
    with pytest.raises(FreezeError, match="refusing to overwrite"):
        _unseal(frozen)
    assert _tree(frozen.target) == before
    stray.unlink()
    before = _tree(frozen.target)

    def link(source, target):
        if Path(target).name == Path(up.UNSEALED_MANIFEST_PATH).name:
            raise OSError("synthetic failure before the manifest")
        os.link(source, target)

    with pytest.raises(OSError, match="synthetic failure"):
        _unseal(frozen, link=link)
    assert _tree(frozen.target) == before
    assert not list(frozen.target.glob(".freeze-staging-*"))


def test_cli_check_prints_hashes_and_unseal_requires_confirmation(frozen, monkeypatch, capsys):
    monkeypatch.setattr(
        up, "check", functools.partial(up.check, allowed_root=frozen.root, catalog=frozen.catalog)
    )
    before = _trees(frozen)
    args = ["--golden-dir", str(frozen.target), "--private-root", str(frozen.private_root)]
    assert up.main([*args, "check"]) == 0
    out = capsys.readouterr().out
    binding = up.check(frozen.target, private_root=frozen.private_root).manifest.provenance_binding
    assert f"provenance_sha256 {binding}" in out
    unseal = ["unseal", "--labels-frozen-sha256", "0" * 64, "--provenance-sha256", binding]
    assert up.main([*args, *unseal]) == 1
    assert "confirm-unseal" in capsys.readouterr().err
    assert _trees(frozen) == before
