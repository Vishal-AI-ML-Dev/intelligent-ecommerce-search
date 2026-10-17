"""The human-reviewed pilot/dev evaluation runner on a temporary golden directory frozen through
`provenance_unsealed` from SYNTHETIC judgements, adjudications, joint records and source runs
(the fixtures of the label-freeze and unsealing tests). Nothing here is a human decision or a
real metric. The real golden directory and `evals/results/` are never read or written."""

import functools
import json
import os
import shutil
from pathlib import Path

import pytest
import test_golden_adjudication_review_2 as adjudication_tests
import test_golden_freeze_labels_review_2 as freeze_tests
import test_golden_joint_resolution_review_2 as joint_tests
from evals.annotation import adjudication as adj
from evals.annotation import joint_resolution as jr
from evals.contracts.canonical import ContractError, decode_jsonl, encode_json_object, sha256_hex
from evals.contracts.manifest import Stage
from evals.contracts.models import FinalJudgement, SourceSystem
from evals.metrics.retrieval import EvaluationResult, evaluate_run
from evals.runners import evaluate_human_pilot_dev as hpd
from evals.runners import unseal_provenance as up
from evals.runners.golden_io import FreezeError
from test_golden_adjudication_review_2 import ADJ_T0, _clock, _scripted, _tree

sealed_template = adjudication_tests.sealed_template

EVALUATED = {system.value for system, _ in hpd.SYSTEMS}
OUTPUT_FILES = {f"metrics/{s}.json" for s in EVALUATED} | {hpd.SUMMARY_PATH, hpd.RECORD_PATH}


@pytest.fixture(scope="module")
def unsealed_template(sealed_template, catalog, tmp_path_factory):
    """SYNTHETIC chain through `provenance_unsealed`: the label-freeze tests' scripted work,
    frozen and unsealed once per module."""
    tmp = tmp_path_factory.mktemp("human-pilot-dev")
    shutil.copytree(sealed_template / "goldens", tmp / "goldens")
    root = tmp / "goldens"
    target = root / "internal-search" / "v1"
    targeted = adj.load_targeted(target, allowed_root=root, catalog=catalog)
    adjudication_root = tmp / "adjudication-work"
    store = adj.AdjudicationStore(targeted, adjudication_root)
    freeze_tests._record_adjudications(targeted, store, targeted.mandatory, ADJ_T0)
    joint = jr.load_joint(target, allowed_root=root, catalog=catalog)
    joint_root = tmp / "joint-work"
    answers = [a for i, item in enumerate(joint.items) for a in (
        joint_tests._change(joint, item) if i % 2 else joint_tests._keep(joint, item)
    )]  # fmt: skip
    jr.annotate(
        joint, jr.JointStore(joint, joint_root), recorded_by="ann-2", ask=_scripted(answers),
        say=lambda _: None, now=_clock(freeze_tests.JOINT_T0),
    )  # fmt: skip
    worked = freeze_tests.Worked(root, target, adjudication_root, joint_root, catalog)
    freeze_tests._freeze(worked, freeze_tests._check(worked).inputs_sha256)
    private_root = tmp / "private"
    shutil.copytree(sealed_template / "private", private_root)
    inputs = up.check(target, allowed_root=root, private_root=private_root, catalog=catalog)
    up.unseal(
        target,
        confirm_unseal=True,
        labels_frozen_sha256=inputs.manifest.manifest_sha256,
        provenance_sha256=sha256_hex(inputs.provenance_data),
        allowed_root=root,
        private_root=private_root,
        catalog=catalog,
    )
    return root


@pytest.fixture
def golden(unsealed_template, tmp_path):
    shutil.copytree(unsealed_template, tmp_path / "goldens")
    root = tmp_path / "goldens"
    return root, root / "internal-search" / "v1"


@pytest.fixture
def results(tmp_path):
    root = tmp_path / "results"
    return root, root / "internal-search" / "v1" / "human-pilot-dev"


def _in_process_builds(catalog):
    """Two in-process builds standing in for the fresh processes (the real fresh-process path
    is exercised by its own test)."""

    def builds(golden_dir: Path, allowed_root: Path) -> list[dict[str, bytes]]:
        out = []
        for _ in range(2):
            inputs = hpd.load_inputs(golden_dir, allowed_root=allowed_root, catalog=catalog)
            out.append(hpd.build_outputs(inputs, hpd.current_source_state(inputs)))
        return out

    return builds


def _hashes(golden_dir: Path) -> tuple[str, str]:
    labels = json.loads((golden_dir / up.freeze_labels.LABELS_MANIFEST_PATH).read_bytes())
    unsealed = json.loads((golden_dir / up.UNSEALED_MANIFEST_PATH).read_bytes())
    return labels["manifest_sha256"], unsealed["manifest_sha256"]


def _run(golden, results, catalog, **overrides):
    root, target = golden
    results_root, out_dir = results
    labels_sha, unsealed_sha = _hashes(target)
    arguments = {
        "labels_frozen_sha256": labels_sha,
        "provenance_unsealed_sha256": unsealed_sha,
        "out_dir": out_dir,
        "results_root": results_root,
        "allowed_root": root,
        "catalog": catalog,
        "fresh_builds": _in_process_builds(catalog),
        **overrides,
    }
    return hpd.run(target, **arguments)


# ---- check ----------------------------------------------------------------------------------


def test_check_verifies_the_chain_and_writes_nothing(golden, results, catalog):
    root, target = golden
    results_root, out_dir = results
    before = _tree(target)
    inputs = hpd.check(
        target, out_dir=out_dir, results_root=results_root, allowed_root=root, catalog=catalog
    )
    assert _tree(target) == before
    assert not results_root.exists()
    assert inputs.unsealed.stage is Stage.PROVENANCE_UNSEALED
    assert inputs.unsealed.previous_manifest_sha256 == inputs.labels_frozen.manifest_sha256
    lines = hpd.check_lines(inputs)
    assert f"provenance_unsealed_sha256 {inputs.unsealed.manifest_sha256}" in lines
    assert any(line.startswith("queries 20 (pilot_dev 20, test 0)") for line in lines)
    product_ids = {e.product_id for e in inputs.provenance.entries}
    assert not any(pid in line for pid in product_ids for line in lines)


def test_a_chain_without_provenance_unsealed_is_refused(golden, results, catalog):
    root, target = golden
    results_root, out_dir = results
    (target / up.UNSEALED_MANIFEST_PATH).unlink()
    (target / up.PROVENANCE_PATH).unlink()
    with pytest.raises(ContractError, match="chain through provenance_unsealed"):
        hpd.check(
            target, out_dir=out_dir, results_root=results_root, allowed_root=root, catalog=catalog
        )


@pytest.mark.parametrize("relative", ["final_judgements.jsonl", "eligibility.jsonl"])
def test_a_changed_label_or_eligibility_file_is_refused(golden, results, catalog, relative):
    root, target = golden
    results_root, out_dir = results
    path = target / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ContractError):
        hpd.check(
            target, out_dir=out_dir, results_root=results_root, allowed_root=root, catalog=catalog
        )


def test_a_changed_unsealed_provenance_is_refused(golden, results, catalog):
    root, target = golden
    results_root, out_dir = results
    path = target / up.PROVENANCE_PATH
    path.write_bytes(path.read_bytes().replace(b"\n", b" \n", 1))
    with pytest.raises(ContractError):
        hpd.check(
            target, out_dir=out_dir, results_root=results_root, allowed_root=root, catalog=catalog
        )


# ---- destination ----------------------------------------------------------------------------


def test_destinations_must_be_new_and_inside_the_results_root(tmp_path):
    results_root = tmp_path / "results"
    with pytest.raises(FreezeError, match="must be inside"):
        hpd.safe_out_dir(tmp_path / "elsewhere", results_root)
    with pytest.raises(FreezeError, match="'..'"):
        hpd.safe_out_dir(results_root / "a" / ".." / "b", results_root)
    with pytest.raises(FreezeError, match="below the results root"):
        hpd.safe_out_dir(results_root, results_root)
    existing = results_root / "done"
    existing.mkdir(parents=True)
    with pytest.raises(FreezeError, match="already exists"):
        hpd.safe_out_dir(existing, results_root)
    assert hpd.safe_out_dir(results_root / "new", results_root) == results_root / "new"
    assert hpd.DEFAULT_OUT_DIR.relative_to(hpd.RESULTS_ROOT).as_posix() == (
        "internal-search/v1/human-pilot-dev"
    )


def test_run_refuses_an_existing_destination_and_unreviewed_hashes(golden, results, catalog):
    _, target = golden
    results_root, out_dir = results
    before = _tree(target)
    with pytest.raises(FreezeError, match="labels_frozen manifest differs"):
        _run(golden, results, catalog, labels_frozen_sha256="0" * 64)
    with pytest.raises(FreezeError, match="provenance_unsealed manifest differs"):
        _run(golden, results, catalog, provenance_unsealed_sha256="0" * 64)
    assert not results_root.exists()
    out_dir.mkdir(parents=True)
    (out_dir / "earlier.json").write_bytes(b"{}\n")
    with pytest.raises(FreezeError, match="already exists"):
        _run(golden, results, catalog)
    assert [p.name for p in out_dir.iterdir()] == ["earlier.json"]
    assert (out_dir / "earlier.json").read_bytes() == b"{}\n"
    assert _tree(target) == before


def test_differing_builds_are_refused_and_nothing_is_written(golden, results, catalog):
    results_root, _ = results

    def differing(golden_dir, allowed_root):
        builds = _in_process_builds(catalog)(golden_dir, allowed_root)
        builds[1][hpd.SUMMARY_PATH] += b" "
        return builds

    with pytest.raises(FreezeError, match="not byte-identical"):
        _run(golden, results, catalog, fresh_builds=differing)
    assert not results_root.exists()


def test_a_failed_write_rolls_back(golden, results, catalog):
    _, out_dir = results

    def link(source, target):
        if Path(target).name == hpd.RECORD_PATH:
            raise OSError("synthetic failure before the run record")
        os.link(source, target)

    with pytest.raises(OSError, match="synthetic failure"):
        _run(golden, results, catalog, link=link)
    assert not out_dir.exists()
    assert not list(out_dir.parent.glob(".freeze-staging-*"))


# ---- run and verify -------------------------------------------------------------------------


def test_run_scores_exactly_the_recorded_lists_with_the_frozen_labels(golden, results, catalog):
    root, target = golden
    _, out_dir = results
    golden_before = _tree(target)
    order: list[str] = []

    def link(source, target_path):
        order.append(Path(target_path).name)
        os.link(source, target_path)

    files = _run(golden, results, catalog, link=link)
    assert _tree(target) == golden_before  # the golden directory is only read
    assert set(files) == OUTPUT_FILES
    assert order[-1] == hpd.RECORD_PATH  # the run record is written last
    assert {p: (out_dir / p).read_bytes() for p in files} == files

    inputs = hpd.load_inputs(target, allowed_root=root, catalog=catalog)
    finals = decode_jsonl((target / "final_judgements.jsonl").read_bytes(), FinalJudgement)
    for system, _ in hpd.SYSTEMS:
        runs = {r.query_id: r.product_ids for r in inputs.provenance.source_runs
                if r.system is system}  # fmt: skip
        expected = evaluate_run(
            system.value,
            runs,
            finals,
            eligibility=lambda q, p: inputs.eligibility[(q, p)],
            golden_manifest_sha256=inputs.unsealed.manifest_sha256,
        )
        assert files[f"metrics/{system.value}.json"] == encode_json_object(expected)
        result = EvaluationResult.model_validate_json(files[f"metrics/{system.value}.json"])
        assert result.config.k_values == (5, 10, 20) and result.config.primary_k == 10
        assert {(m.metric, m.definition) for m in result.macro} >= {
            ("precision", "strict"), ("precision", "lenient"), ("recall", "strict"),
            ("reciprocal_rank", "lenient"), ("ndcg", "none"), ("csr", "hard"),
            ("irrelevant_result_rate", "none"), ("unjudged", "none"), ("zero_result", "none"),
        }  # fmt: skip
    assert "metrics/dense_only.json" not in files

    summary = json.loads(files[hpd.SUMMARY_PATH])
    assert [s["system"] for s in summary["systems"]] == [s.value for s, _ in hpd.SYSTEMS]
    for system in summary["systems"]:
        assert system["unjudged_total"] == {"5": 0, "10": 0, "20": 0}  # every list was pooled
        assert all({"n", "excluded"} <= set(v) for v in system["primary"])

    record = json.loads(files[hpd.RECORD_PATH])
    labels_sha, unsealed_sha = _hashes(target)
    assert record["golden"]["labels_frozen_manifest_sha256"] == labels_sha
    assert record["golden"]["provenance_unsealed_manifest_sha256"] == unsealed_sha
    assert record["golden"]["final_judgements_sha256"] == sha256_hex(
        (target / "final_judgements.jsonl").read_bytes()
    )
    assert record["golden"]["provenance_sha256"] == record["golden"]["provenance_binding"]
    assert record["golden"]["partitions"] == {"pilot_dev": 20, "test": 0}
    assert record["evaluator"]["source_sha256"] == sha256_hex(
        (hpd.ROOT / hpd.EVALUATOR_SOURCE).read_bytes()
    )
    assert record["retrieval_runs"]["provenance_run_id"] == inputs.provenance.run_id
    assert record["retrieval_runs"]["not_evaluated"] == ["dense_only"]
    assert record["outputs"] == {p: sha256_hex(files[p]) for p in OUTPUT_FILES - {hpd.RECORD_PATH}}
    source = record["evaluation_source"]
    assert {f["path"] for f in source["code_files"]} == set(hpd.SOURCE_PATHS)
    assert {g["git_state"] for g in source["golden_files"]} == {"outside_repository"}
    assert any("golden chain files were not committed" in s for s in source["statements"])
    disclosures = " ".join(record["disclosures"])
    for phrase in (
        "not held-out test evidence",
        "Not official M10",
        "No retrieval was re-run",
        "Pooled recall",
        "V3-d10 at K=20",
        "rrf_k and the embedding model",
    ):
        assert phrase in disclosures  # fmt: skip


def test_the_source_state_never_claims_an_uncommitted_file_is_committed(golden, catalog):
    root, target = golden
    inputs = hpd.load_inputs(target, allowed_root=root, catalog=catalog)
    state = hpd.current_source_state(inputs)
    for item in state["code_files"]:
        assert item["sha256"] == sha256_hex((hpd.ROOT / item["path"]).read_bytes())
        actual = hpd._git("status", "--porcelain", "--untracked-files=all", "--", item["path"])
        assert (item["git_state"] == "committed_unchanged") == (not actual.strip())
    if not state["working_tree_clean"]:
        assert any("working tree was not clean" in s for s in state["statements"])


def test_verify_regenerates_every_output_and_detects_changes(golden, results, catalog):
    root, target = golden
    _, out_dir = results
    _run(golden, results, catalog)
    record = hpd.verify(target, out_dir=out_dir, allowed_root=root, catalog=catalog)
    assert record["evaluation_version"] == hpd.EVALUATION_VERSION

    metrics = out_dir / "metrics" / f"{SourceSystem.V2_FILTERED.value}.json"
    original = metrics.read_bytes()
    metrics.write_bytes(original.replace(b'"query_count":20', b'"query_count":19'))
    with pytest.raises(FreezeError, match="do not regenerate"):
        hpd.verify(target, out_dir=out_dir, allowed_root=root, catalog=catalog)
    metrics.write_bytes(original)

    extra = out_dir / "metrics" / "dense_only.json"
    extra.write_bytes(b"{}\n")
    with pytest.raises(FreezeError, match="unexpected or missing"):
        hpd.verify(target, out_dir=out_dir, allowed_root=root, catalog=catalog)
    extra.unlink()

    path = out_dir / hpd.RECORD_PATH
    data = json.loads(path.read_bytes())
    data["evaluator"]["source_sha256"] = "0" * 64
    path.write_bytes(hpd._dump(data))
    with pytest.raises(FreezeError, match="evaluator source changed"):
        hpd.verify(target, out_dir=out_dir, allowed_root=root, catalog=catalog)


def test_fresh_process_builds_are_byte_identical_to_the_in_process_build(golden, catalog):
    root, target = golden
    inputs = hpd.load_inputs(target, allowed_root=root, catalog=catalog)
    in_process = hpd.build_outputs(inputs, hpd.current_source_state(inputs))
    builds = hpd.fresh_process_builds(target, root)
    assert len(builds) == 2
    for build in builds:
        hpd.compare_builds(in_process, build)
    assert set(in_process) == OUTPUT_FILES


# ---- CLI ------------------------------------------------------------------------------------


def test_cli_check_prints_hashes_and_run_requires_the_reviewed_hashes(
    golden, results, catalog, monkeypatch, capsys
):
    root, target = golden
    results_root, out_dir = results
    monkeypatch.setattr(
        hpd, "check", functools.partial(hpd.check, results_root=results_root, catalog=catalog)
    )
    monkeypatch.setattr(
        hpd, "run", functools.partial(hpd.run, results_root=results_root, catalog=catalog)
    )
    before = _tree(target)
    args = ["--golden-dir", str(target), "--out-dir", str(out_dir), "--allowed-root", str(root)]
    assert hpd.main([*args, "check"]) == 0
    out = capsys.readouterr().out
    _, unsealed_sha = _hashes(target)
    assert f"provenance_unsealed_sha256 {unsealed_sha}" in out
    assert "no metric computed, nothing written" in out
    wrong = ["run", "--labels-frozen-sha256", "0" * 64, "--provenance-unsealed-sha256", "0" * 64]
    assert hpd.main([*args, *wrong]) == 1
    assert "labels_frozen manifest differs" in capsys.readouterr().err
    assert hpd.main([*args, "verify"]) == 1
    assert "no written evaluation" in capsys.readouterr().err
    assert _tree(target) == before
    assert not results_root.exists()
