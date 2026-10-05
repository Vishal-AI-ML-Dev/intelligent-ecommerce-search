"""Machine-only finalization on a SYNTHETIC temporary pool, generation and archive (fake logits,
no model, no human decision). The real golden, weak-set, audit and archive directories are only
read by the isolation checks, never written."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from evals.contracts.canonical import decode_jsonl
from evals.weak_labels import machine_only as mo
from evals.weak_labels.contracts import (
    ABANDONED_DISCLOSURE,
    MACHINE_WARNING,
    MachineDevSealed,
    MachineWeakLabel,
)
from evals.weak_labels.generation import PROPOSALS_FILE, signals_path
from pydantic import ValidationError
from weak_support import PIN, generate, weak_fixture

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_LABEL_KEYS = {
    "annotator_handle",
    "decided_at",
    "annotated_at",
    "decision",
    "decided_by",
    "human_confidence",
    "agreement",
}
HUMAN_STATISTIC_KEYS = (
    "accept_rate",
    "override_rate",
    "override_count",
    "accepted_count",
    "confusion",
    "agreement_by",
    "exact_agreement",
    '"accuracy"',
    "intra_annotator",
)


def archive(root: Path, *, count: int = 2) -> Path:
    """A SYNTHETIC abandoned-audit archive: metadata plus a decision file that must never be
    read (its content is deliberately not a valid record)."""
    folder = root / "abandoned-fixture"
    folder.mkdir(parents=True)
    (folder / "audit_decisions.jsonl").write_bytes(b'{"annotator_handle":"ann-1","bogus":1}\n')
    metadata = root / "abandoned-fixture.archive.json"
    metadata.write_text(
        json.dumps(
            {
                "status": "abandoned_partial_audit",
                "decision_count": count,
                "included_in_machine_labels": False,
                "included_in_metrics": False,
                "included_in_human_audit_statistics": False,
            }
        ),
        encoding="utf-8",
    )
    return metadata


def setup(tmp_path: Path, catalog, *, archive_count: int = 2):
    fx = weak_fixture(tmp_path, catalog)
    generate(fx)
    archive_root = tmp_path / "weak_audit_archive"
    paths = mo.MachinePaths(
        golden_dir=fx.golden_dir,
        golden_root=fx.golden_root,
        pool_private=fx.pool_private,
        weak_dir=fx.weak_dir,
        weak_root=fx.weak_root,
        signals_root=fx.signals_root,
        audit_root=fx.audit_root,
        archive_root=archive_root,
        archive_metadata=archive(archive_root, count=archive_count),
        strict_work_root=tmp_path / "golden_annotation",
    )
    return fx, paths


def in_process_runs(paths, catalog):
    def runs():
        facts, generation = mo.load_verified(paths, pin=PIN, catalog=catalog)
        return [mo.build_machine_files(facts, generation) for _ in range(2)]

    return runs


def assemble(paths, catalog, **kwargs):
    return mo.assemble_machine_only(
        paths,
        acknowledge_unaudited=kwargs.pop("acknowledge_unaudited", True),
        runs=kwargs.pop("runs", in_process_runs(paths, catalog)),
        pin=PIN,
        catalog=catalog,
        **kwargs,
    )


@pytest.fixture(scope="module")
def done(tmp_path_factory, catalog):
    fx, paths = setup(tmp_path_factory.mktemp("machine"), catalog)
    manifest = assemble(paths, catalog)
    return fx, paths, manifest


def read(paths, name: str) -> bytes:
    return (paths.machine_dir / name).read_bytes()


# ---- refusals ----------------------------------------------------------------------------------


def test_acknowledgment_flag_is_required(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)
    with pytest.raises(mo.MachineOnlyError, match="--acknowledge-unaudited"):
        assemble(paths, catalog, acknowledge_unaudited=False)
    assert not paths.machine_dir.exists()


def test_active_lock_and_active_decisions_are_refused(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)
    work = paths.audit_root / "0123456789abcdef"
    work.mkdir(parents=True)
    (work / "audit.lock").write_text("{}", encoding="utf-8")
    with pytest.raises(mo.MachineOnlyError, match="active audit lock"):
        assemble(paths, catalog)
    (work / "audit.lock").unlink()
    (work / "audit_decisions.jsonl").write_bytes(b'{"synthetic":"decision"}\n')
    with pytest.raises(mo.MachineOnlyError, match="active human audit decisions"):
        assemble(paths, catalog)
    (work / "audit_decisions.jsonl").write_bytes(b"")
    assemble(paths, catalog)  # an empty decision file is zero decisions


def test_archived_decisions_are_never_an_input(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)
    pointed = mo.MachinePaths(**{**paths.__dict__, "audit_root": paths.archive_root})
    with pytest.raises(mo.MachineOnlyError, match="never an input"):
        assemble(pointed, catalog)
    inside = mo.MachinePaths(
        **{**paths.__dict__, "audit_root": paths.archive_root / "abandoned-fixture"}
    )
    with pytest.raises(mo.MachineOnlyError, match="never an input"):
        assemble(inside, catalog)


def test_archive_metadata_must_record_two_excluded_decisions(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog, archive_count=1)
    with pytest.raises(mo.MachineOnlyError, match="two excluded"):
        assemble(paths, catalog)
    paths.archive_metadata.unlink()
    with pytest.raises(mo.MachineOnlyError, match="missing"):
        assemble(paths, catalog)


def test_strict_judgements_or_seals_are_refused(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)
    paths.strict_work_root.mkdir()
    (paths.strict_work_root / "primary_judgements.jsonl").write_bytes(b"")
    with pytest.raises(mo.MachineOnlyError, match="strict judgement"):
        assemble(paths, catalog)
    (paths.strict_work_root / "primary_judgements.jsonl").unlink()
    (paths.golden_dir / "manifests" / "3-primary_sealed.json").write_bytes(b"{}")
    with pytest.raises(mo.MachineOnlyError, match="beyond pool_frozen"):
        assemble(paths, catalog)


def test_tampered_proposals_pool_cards_or_signals_are_refused(tmp_path, catalog):
    from dataclasses import replace
    from types import MappingProxyType

    from evals.contracts.canonical import ContractError

    fx, paths = setup(tmp_path, catalog)
    targets = {
        "proposals": paths.weak_dir / PROPOSALS_FILE,
        "signals": signals_path(fx.facts, paths.signals_root),
        "pool": paths.golden_dir / "pool.jsonl",
    }
    for name, path in targets.items():
        original = path.read_bytes()
        path.write_bytes(original.replace(b'"gq-p01"', b'"gq-p02"', 1))
        with pytest.raises(ContractError):
            assemble(paths, catalog)
        path.write_bytes(original)
        assert not paths.machine_dir.exists(), name
    product_id = fx.facts.pool[0].product_id
    products = dict(catalog.products)
    products[product_id] = replace(products[product_id], title="Changed title")
    changed = replace(catalog, products=MappingProxyType(products))
    with pytest.raises(ContractError):
        assemble(paths, changed)
    assert not paths.machine_dir.exists()


def test_nondeterministic_runs_write_nothing(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)

    def runs():
        first, second = in_process_runs(paths, catalog)()
        second = dict(second)
        second[mo.REPORT_FILE] = second[mo.REPORT_FILE] + b"x"
        return [first, second]

    with pytest.raises(mo.MachineOnlyError, match=r"runs differ.*provisional-v0-v3.md"):
        assemble(paths, catalog, runs=runs)
    assert not paths.machine_dir.exists()


def test_outputs_are_never_overwritten(done, catalog):
    _, paths, _ = done
    with pytest.raises(mo.MachineOnlyError, match="already exist"):
        assemble(paths, catalog)


# ---- outputs -----------------------------------------------------------------------------------


def test_machine_only_status_paths_and_manifest(done):
    fx, paths, manifest = done
    assert paths.machine_dir == fx.weak_dir / "machine-only"
    stored = sorted(
        p.relative_to(paths.machine_dir).as_posix()
        for p in paths.machine_dir.rglob("*")
        if p.is_file()
    )
    assert stored == sorted(
        [
            "labels.jsonl",
            "manifest.json",
            "proposal-summary.json",
            "metrics/v0.json",
            "metrics/v1.json",
            "metrics/v2.json",
            "metrics/v3.json",
            "metrics/comparison.json",
            "reports/provisional-v0-v3.md",
        ]
    )
    assert manifest.status == "weak_machine_dev_sealed"
    assert manifest.label_basis == "deterministic_machine_weak_labels"
    assert manifest.policy_version == "weak-label-policy-1"
    assert manifest.human_audit_coverage == 0 and manifest.provisional is True
    assert manifest.abandoned_human_decision_count == 2
    assert manifest.abandoned_human_decisions_included is False
    assert not (fx.weak_dir / "weak_dev_sealed.json").exists()
    raw = read(paths, "manifest.json").decode()
    assert 'weak_dev_sealed"' not in raw and "assembled_at" not in raw
    for field, value in (("status", "weak_dev_sealed"), ("human_audit_coverage", 1)):
        with pytest.raises(ValidationError):
            MachineDevSealed.model_validate({**manifest.model_dump(mode="json"), field: value})
    ops = signals_path(fx.facts, paths.signals_root).with_name(mo.OPS_FILE)
    assert json.loads(ops.read_text("utf-8"))["runs_byte_identical"] is True


def test_every_label_is_machine_unreviewed_and_never_ann1(done):
    fx, paths, _ = done
    raw = read(paths, "labels.jsonl")
    labels = decode_jsonl(raw, MachineWeakLabel)
    assert len(labels) == len(fx.facts.pool)
    assert {x.label_source for x in labels} == {"machine_unreviewed"}
    assert {x.human_audited for x in labels} == {False}
    assert {x.proposal_source for x in labels} == {"weak-label-policy-1"}
    assert b"ann-1" not in raw
    for line in raw.decode().splitlines():
        assert not FORBIDDEN_LABEL_KEYS & set(json.loads(line))
    with pytest.raises(ValidationError):
        MachineWeakLabel.model_validate(
            {**labels[0].model_dump(mode="json"), "human_audited": True}
        )
    with pytest.raises(ValidationError):
        MachineWeakLabel.model_validate(
            {**labels[0].model_dump(mode="json"), "annotator_handle": "ann-1"}
        )


def test_archived_decisions_are_excluded(done):
    _, paths, _ = done
    for path in paths.machine_dir.rglob("*"):
        if path.is_file():
            assert b"bogus" not in path.read_bytes()
    summary = json.loads(read(paths, "proposal-summary.json"))
    assert summary["abandoned_human_decisions"] == {
        "count": 2,
        "excluded": True,
        "disclosure": ABANDONED_DISCLOSURE,
    }


def test_mandatory_warning_and_metric_metadata(done):
    _, paths, manifest = done
    for name in (*mo.METRIC_FILES.values(), mo.COMPARISON_FILE, mo.SUMMARY_FILE):
        data = json.loads(read(paths, name))
        assert data["warning"] == MACHINE_WARNING
        assert data["label_basis"] == "deterministic_machine_weak_labels"
        assert data["human_audit_coverage"] == 0
        assert data["abandoned_human_decision_count"] == 2
        assert data["provisional"] is True
        assert data["policy_version"] == "weak-label-policy-1"
        assert data["proposal_manifest_sha256"] == manifest.proposal_manifest_sha256
        assert data["pool_manifest_sha256"] == manifest.pool_manifest_sha256
    report = read(paths, mo.REPORT_FILE).decode()
    assert report.count(MACHINE_WARNING) >= 2
    assert MACHINE_WARNING.startswith("WARNING — 568/568 labels are deterministic")
    for version, system in mo.VERSION_SYSTEMS.items():
        data = json.loads(read(paths, mo.METRIC_FILES[version]))
        assert data["system"] == system.value and data["query_count"] == 20
        keys = {(m["metric"], m["k"], m["definition"]) for m in data["macro"]}
        for wanted in (
            ("precision", 5, "strict"),
            ("precision", 10, "lenient"),
            ("recall", 10, "strict"),
            ("reciprocal_rank", 10, "strict"),
            ("ndcg", 5, "none"),
            ("ndcg", 10, "none"),
            ("csr", 10, "hard"),
            ("csr", 10, "fp1"),
            ("irrelevant_result_rate", 10, "none"),
            ("zero_result", None, "none"),
        ):
            assert wanted in keys


def test_no_human_statistics_anywhere(done):
    _, paths, _ = done
    for path in paths.machine_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            for key in HUMAN_STATISTIC_KEYS:
                assert key not in text, (path.name, key)
            assert "override rate" not in text and "confusion matrix" not in text


def test_report_contents(done):
    _, paths, _ = done
    report = read(paths, mo.REPORT_FILE).decode()
    for needed in (
        "Total machine labels",
        "Machine-only coverage",
        "Human audit coverage",
        "Abandoned human decisions | 2, excluded",
        "requires_human_review",
        "Deterministic-rule only",
        "Model-assisted",
        "### Label counts",
        "### Relation counts",
        "### Unaudited heuristic-confidence counts",
        "## Macro V0–V3 aggregates",
        "## Per-query V0–V3 metrics",
        "## Heuristic limitations",
        "## Pooled-recall limitations",
        "## Strict Golden Dataset status",
        "0/568 primary judgements",
    ):
        assert needed in report, needed


def test_outputs_are_deterministic_and_verify(done, catalog):
    _, paths, _ = done
    facts, generation = mo.load_verified(paths, pin=PIN, catalog=catalog)
    first = mo.build_machine_files(facts, generation)
    second = mo.build_machine_files(facts, generation)
    assert first == second
    for name in mo.DETERMINISTIC_FILES:
        assert first[name] == read(paths, name)
    mo.verify_machine_only(paths, pin=PIN, catalog=catalog)


def test_tampered_machine_outputs_fail_verification(tmp_path, catalog):
    _, paths = setup(tmp_path, catalog)
    assemble(paths, catalog)
    target = paths.machine_dir / mo.REPORT_FILE
    target.write_bytes(target.read_bytes().replace(b"provisional", b"final", 1))
    with pytest.raises(mo.MachineOnlyError, match="does not match"):
        mo.verify_machine_only(paths, pin=PIN, catalog=catalog)


# ---- repository isolation ----------------------------------------------------------------------


def _hashes(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_strict_artifacts_stay_byte_identical(tmp_path, catalog):
    strict = (
        ROOT / "evals" / "goldens" / "internal-search" / "v1",
        ROOT / "data" / "processed" / "golden_annotation",
        ROOT / "data" / "processed" / "golden_pool",
        ROOT / "data" / "processed" / "weak_audit_archive",
    )
    before = {d: _hashes(d) for d in strict}
    _, paths = setup(tmp_path, catalog)
    assemble(paths, catalog)
    assert {d: _hashes(d) for d in strict} == before


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_audit_archive_is_ignored_and_never_tracked():
    def git(*args):
        executable = shutil.which("git")
        assert executable is not None
        return subprocess.run(  # noqa: S603 - fixed git arguments
            [executable, *args], cwd=ROOT, capture_output=True, text=True, check=False
        )

    for path in (
        "data/processed/weak_audit_archive/abandoned-2026-10-05-9dc1fab28fbd89e9.archive.json",
        "data/processed/weak_audit_archive/abandoned-2026-10-05-9dc1fab28fbd89e9/"
        "audit_decisions.jsonl",
        "data/processed/weak_labels/4c26628ac45cd8dd/signals.jsonl",
    ):
        assert git("check-ignore", "-q", path).returncode == 0, path
    tracked = git("ls-files", "data/processed").stdout.strip()
    staged = git("diff", "--cached", "--name-only").stdout
    assert tracked == "" and "weak_audit_archive" not in staged
