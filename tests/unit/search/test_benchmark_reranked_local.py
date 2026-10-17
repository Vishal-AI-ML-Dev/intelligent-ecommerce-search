"""scripts/benchmark_reranked.py local-snapshot mode (M10): guarded-path accounting, the binding,
the output directory, child arguments, header verification and the unchanged M8 mode.

Pure tests only: no database, no model, no network (strict socket guard). Git is faked except
for the read-only `check_output_dir` checks against the real repository's ignore rules."""

import argparse
import json
from pathlib import Path

import pytest
import test_benchmark_reranked as m8

bench = m8.bench
ROOT = m8.ROOT
HEAD = m8.HEAD
MODELS = m8.MODELS
BLOB_A = "a" * 40
BLOB_B = "b" * 40
BLOB_P = "c" * 40

pytestmark = pytest.mark.usefixtures("no_socket_connect")


def local_git(
    *, committed=b"", status=b"", tree_status=None, ancestor=True, blobs=None, working=None
):
    """A fake git for the local-snapshot checks. `status` answers the guarded-path status (with a
    `--` pathspec); `tree_status` the whole-tree status (defaults to `status`). `blobs` maps
    `REV:path` to a blob hash (missing means absent); `working` maps a path to its working-tree
    blob hash."""
    blobs = blobs or {}
    working = working or {}

    def git(root, *args, stdin=None):
        if args[0] == "rev-parse" and args[1] == "HEAD":
            return HEAD.encode() + b"\n"
        if args[0] == "rev-parse":
            if args[1] not in blobs:
                raise bench.GitError("absent")
            return blobs[args[1]].encode() + b"\n"
        if args[0] == "merge-base":
            if not ancestor:
                raise bench.GitError("not an ancestor")
            return b""
        if args[0] == "diff":
            return committed
        if args[0] == "status":
            whole_tree = "--" not in args
            return tree_status if whole_tree and tree_status is not None else status
        if args[0] == "hash-object":
            return working[args[-1]].encode() + b"\n"
        raise AssertionError(args)

    return git


def fingerprint(tree="f" * 64):
    return lambda root: {"source_tree_sha256": tree, "git_working_tree_dirty": True}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


# ---- declarations and git parsing ------------------------------------------------------------


def test_accepted_declarations_are_parsed_strictly():
    assert bench.parse_accepted(None) == {}
    assert bench.parse_accepted([f"pyproject.toml={BLOB_A}", "src/old.py=deleted"]) == {
        "pyproject.toml": BLOB_A,
        "src/old.py": "deleted",
    }
    for bad in ("pyproject.toml", f"={BLOB_A}", "pyproject.toml=abc", "x=DELETED"):
        with pytest.raises(bench.ProvenanceError, match="PATH="):
            bench.parse_accepted([bad])
    with pytest.raises(bench.ProvenanceError, match="declared twice"):
        bench.parse_accepted([f"x={BLOB_A}", f"x={BLOB_B}"])


def test_porcelain_entries_include_both_sides_of_a_rename():
    data = b" M src/a.py\0?? src/new.py\0R  src/b.py\0src/old.py\0 D Dockerfile\0"
    assert bench._porcelain_paths(data) == [
        "src/a.py",
        "src/new.py",
        "src/b.py",
        "src/old.py",
        "Dockerfile",
    ]
    assert bench._porcelain_paths(b"") == []


# ---- guarded-path accounting -----------------------------------------------------------------


def test_every_guarded_difference_is_reported_with_its_blobs(repo):
    git = local_git(
        committed=b"pyproject.toml\n",
        status=b" M src/app.py\0 D src/gone.py\0",
        blobs={
            f"{bench.PRODUCTION_COMMIT}:pyproject.toml": BLOB_P,
            "HEAD:pyproject.toml": BLOB_A,
            f"{bench.PRODUCTION_COMMIT}:src/app.py": BLOB_P,
            "HEAD:src/app.py": BLOB_P,
            f"{bench.PRODUCTION_COMMIT}:src/gone.py": BLOB_P,
            "HEAD:src/gone.py": BLOB_P,
        },
        working={"pyproject.toml": BLOB_A, "src/app.py": BLOB_B},
    )
    assert bench.guarded_differences(repo, git) == [
        {
            "path": "pyproject.toml",
            "production_blob": BLOB_P,
            "head_blob": BLOB_A,
            "working_blob": BLOB_A,
            "committed_since_production": True,
            "uncommitted": False,
        },
        {
            "path": "src/app.py",
            "production_blob": BLOB_P,
            "head_blob": BLOB_P,
            "working_blob": BLOB_B,
            "committed_since_production": False,
            "uncommitted": True,
        },
        {
            "path": "src/gone.py",
            "production_blob": BLOB_P,
            "head_blob": BLOB_P,
            "working_blob": "deleted",
            "committed_since_production": False,
            "uncommitted": True,
        },
    ]


def test_local_snapshot_needs_every_difference_declared_exactly(repo):
    git = local_git(
        committed=b"pyproject.toml\n",
        blobs={"HEAD:pyproject.toml": BLOB_A},
        working={"pyproject.toml": BLOB_A},
    )
    with pytest.raises(
        bench.ProvenanceError, match=f"undeclared guarded difference: --accept-guarded-change "
        f"pyproject.toml={BLOB_A}"
    ):  # fmt: skip
        bench.check_local_snapshot(repo, {}, git, fingerprint())
    with pytest.raises(bench.ProvenanceError, match="is now a+, not the declared b+"):
        bench.check_local_snapshot(repo, {"pyproject.toml": BLOB_B}, git, fingerprint())
    with pytest.raises(bench.ProvenanceError, match="does not differ from the production commit"):
        bench.check_local_snapshot(
            repo, {"pyproject.toml": BLOB_A, "uv.lock": BLOB_B}, git, fingerprint()
        )
    with pytest.raises(bench.ProvenanceError, match="not an ancestor"):
        bench.check_local_snapshot(
            repo, {"pyproject.toml": BLOB_A}, local_git(ancestor=False), fingerprint()
        )


def test_local_snapshot_records_the_fingerprint_and_uncommitted_paths(repo):
    git = local_git(
        committed=b"pyproject.toml\n",
        tree_status=b"?? evals/new.py\0 M docs/x.md\0 M scripts/benchmark_reranked.py\0",
        blobs={"HEAD:pyproject.toml": BLOB_A},
        working={"pyproject.toml": BLOB_A},
    )
    out = bench.check_local_snapshot(repo, {"pyproject.toml": BLOB_A}, git, fingerprint("1" * 64))
    assert out["mode"] == bench.LOCAL_MODE
    assert out["harness_head"] == HEAD
    assert out["production_commit"] == bench.PRODUCTION_COMMIT
    assert out["guarded"] == list(bench.GUARDED_PATHS)
    assert [d["path"] for d in out["guarded_differences"]] == ["pyproject.toml"]
    assert out["source_tree_sha256"] == "1" * 64
    # 3 uncommitted paths, 2 of them outside the fingerprinted source (scripts/ is inside).
    assert out["uncommitted_paths"] == 3
    assert out["uncommitted_paths_outside_source"] == 2


def test_the_m8_mode_still_refuses_any_dirty_tree_or_guarded_difference():
    with pytest.raises(bench.ProvenanceError, match="not clean"):
        bench.check_provenance(ROOT, git=m8.fake_git(status=b"?? evals/new.py\n"))
    with pytest.raises(bench.ProvenanceError, match="runtime-affecting"):
        bench.check_provenance(ROOT, git=m8.fake_git(changed=b"pyproject.toml\n"))


# ---- preflight and binding -------------------------------------------------------------------


@pytest.fixture
def stubbed(monkeypatch):
    """Preflight with every external check stubbed; records which provenance check ran."""
    calls = []
    settings = {k: v for k, v in bench.BASE_SETTINGS.items() if k != "embedding_models_dir"}
    state = {"tree": "1" * 64, "diffs": [{"path": "pyproject.toml", "working_blob": BLOB_A}]}

    def local(root, accepted):
        calls.append(("local", dict(accepted)))
        return {
            "mode": bench.LOCAL_MODE,
            "harness_head": HEAD,
            "production_commit": bench.PRODUCTION_COMMIT,
            "guarded": list(bench.GUARDED_PATHS),
            "guarded_differences": state["diffs"],
            "source_tree_sha256": state["tree"],
            "source_working_tree_dirty": True,
            "uncommitted_paths": 53,
            "uncommitted_paths_outside_source": 50,
        }

    def m8_provenance(root):
        calls.append(("m8", None))
        return {"harness_head": HEAD, "production_commit": bench.PRODUCTION_COMMIT}

    monkeypatch.setattr(bench, "check_provenance", m8_provenance)
    monkeypatch.setattr(bench, "check_local_snapshot", local)
    monkeypatch.setattr(bench, "check_base_settings", lambda s, status: settings)
    monkeypatch.setattr(bench, "pinned_models", lambda: MODELS)
    monkeypatch.setattr(bench, "peak_rss_bytes", lambda process: 1)
    monkeypatch.setattr(bench.hybrid, "check_protocol_identity", lambda: None)
    return calls, state


def test_m8_preflight_is_unchanged_and_carries_no_binding(stubbed):
    calls, _ = stubbed
    out = bench.preflight(object())
    assert calls == [("m8", None)]
    assert "binding" not in out["provenance"] and "mode" not in out["provenance"]


def test_local_preflight_binds_and_refuses_another_binding(stubbed):
    calls, state = stubbed
    first = bench.preflight(object(), bench.LocalSnapshot({"pyproject.toml": BLOB_A}))
    assert calls == [("local", {"pyproject.toml": BLOB_A})]
    provenance = first["provenance"]
    digest = provenance["binding_sha256"]
    assert digest == bench.binding_sha256(provenance["binding"])
    binding = provenance["binding"]
    assert binding["source_tree_sha256"] == "1" * 64
    assert binding["guarded_differences"] == state["diffs"]
    assert binding["models"] == MODELS
    assert binding["libraries"] == bench.library_versions()
    assert binding["protocol_sha256"] == bench.sha256_text(bench.dumps(bench.PREDECLARED))
    assert "uncommitted_paths" not in binding  # recorded in the provenance, not bound

    reviewed = bench.LocalSnapshot({"pyproject.toml": BLOB_A}, digest)
    assert bench.preflight(object(), reviewed)["provenance"]["binding_sha256"] == digest
    state["tree"] = "2" * 64  # any source change changes the binding
    with pytest.raises(bench.ProvenanceError, match="not the reviewed"):
        bench.preflight(object(), reviewed)
    state["tree"] = "1" * 64
    state["diffs"] = [{"path": "pyproject.toml", "working_blob": BLOB_B}]
    with pytest.raises(bench.ProvenanceError, match="not the reviewed"):
        bench.preflight(object(), reviewed)


def test_local_experiment_id_is_derived_from_the_binding():
    digest = "0123456789abcdef" * 4
    assert bench.local_experiment_id(digest) == "rerank-m10-local-0123456789ab"
    assert bench.LOCAL_EXPERIMENT_ID_RE.fullmatch(bench.local_experiment_id(digest))
    assert not bench.EXPERIMENT_ID_RE.fullmatch(bench.local_experiment_id(digest))
    with pytest.raises(bench.BenchmarkError):
        bench.local_experiment_id("abc")


# ---- output directory ------------------------------------------------------------------------


def test_local_output_must_be_new_ignored_and_not_the_m8_directory(tmp_path):
    with pytest.raises(bench.BenchmarkError, match="M8 result directory is preserved"):
        bench.check_local_output_dir(ROOT, bench.OUTPUT_DIR)
    with pytest.raises(bench.BenchmarkError, match="M8 result directory is preserved"):
        bench.check_local_output_dir(ROOT, bench.OUTPUT_DIR / "m10")
    with pytest.raises(bench.BenchmarkError, match="inside data/processed"):
        bench.check_local_output_dir(ROOT, tmp_path / "elsewhere")
    with pytest.raises(bench.BenchmarkError, match="inside data/processed"):
        bench.check_local_output_dir(ROOT, ROOT / "data" / "processed")
    new = ROOT / "data" / "processed" / "rerank_benchmark_m10_unit_test_absent"
    assert not new.exists()
    assert bench.check_local_output_dir(ROOT, new) == new.resolve()
    assert not new.exists()  # the check creates nothing


def test_a_non_empty_local_output_directory_is_refused(tmp_path, monkeypatch):
    out = tmp_path / "data" / "processed" / "m10"
    out.mkdir(parents=True)
    (out / "earlier.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bench, "check_output_dir", lambda root, path, git=None: path.resolve())
    monkeypatch.setattr(bench, "OUTPUT_DIR", tmp_path / "data" / "processed" / "rerank_benchmark")
    with pytest.raises(bench.BenchmarkError, match="already contains files"):
        bench.check_local_output_dir(tmp_path, out)


# ---- CLI arguments and child processes -------------------------------------------------------


def _args(*argv):
    return bench.build_parser().parse_args(list(argv))


def test_local_options_exist_only_in_the_local_mode():
    for argv in (
        ("--execute", "--binding-sha256", "0" * 64),
        ("--execute", "--output-dir", "data/processed/x"),
        ("--execute", "--accept-guarded-change", f"pyproject.toml={BLOB_A}"),
        ("--verify", "x.json", "--local-snapshot"),
        ("--print-protocol", "--local-snapshot"),
        ("--execute", "--local-snapshot", "--output-dir", "data/processed/x"),
        ("--execute", "--local-snapshot", "--binding-sha256", "0" * 64),
        ("--preflight", "--local-snapshot"),
    ):
        with pytest.raises(bench.BenchmarkError):
            bench.check_mode_args(_args(*argv))
    bench.check_mode_args(_args("--execute"))  # the M8 mode, unchanged
    bench.check_mode_args(_args("--preflight"))
    bench.check_mode_args(
        _args("--preflight", "--local-snapshot", "--output-dir", "data/processed/x")
    )
    bench.check_mode_args(
        _args(
            "--execute", "--local-snapshot", "--binding-sha256", "0" * 64,
            "--output-dir", "data/processed/x",
            "--accept-guarded-change", f"pyproject.toml={BLOB_A}",
        )
    )  # fmt: skip


def test_children_receive_and_check_the_reviewed_local_binding(tmp_path):
    digest = "0123456789abcdef" * 4
    local = bench.LocalSnapshot({"uv.lock": BLOB_B, "pyproject.toml": BLOB_A}, digest)
    extra = bench.local_child_args(local, digest)
    assert extra == [
        "--local-snapshot", "--binding-sha256", digest,
        "--accept-guarded-change", f"pyproject.toml={BLOB_A}",
        "--accept-guarded-change", f"uv.lock={BLOB_B}",
    ]  # fmt: skip
    assert bench.local_child_args(None, None) == []
    parsed = _args("--child", *extra)
    assert bench.local_from_args(parsed) == bench.LocalSnapshot(
        {"pyproject.toml": BLOB_A, "uv.lock": BLOB_B}, digest
    )
    values = vars(m8.child_args(tmp_path))
    child = argparse.Namespace(
        **{**values, "experiment_id": bench.local_experiment_id(digest)},
        local_snapshot=True,
        binding_sha256=digest,
    )
    bench.check_child_args(child, "ecommerce_search")
    child.experiment_id = "rerank-m8-latency-0123456789ab"
    with pytest.raises(bench.BenchmarkError, match="reviewed binding"):
        bench.check_child_args(child, "ecommerce_search")
    bench.check_child_args(m8.child_args(tmp_path), "ecommerce_search")  # M8 child unchanged


# ---- local headers and verification ----------------------------------------------------------


def make_local_header():
    header = m8.make_header()
    header["libraries"] = bench.library_versions()
    header["source"] = {"source_tree_sha256": "1" * 64}
    provenance = {
        "mode": bench.LOCAL_MODE,
        "harness_head": HEAD,
        "production_commit": bench.PRODUCTION_COMMIT,
        "guarded": list(bench.GUARDED_PATHS),
        "guarded_differences": [{"path": "pyproject.toml", "working_blob": BLOB_A}],
        "source_tree_sha256": "1" * 64,
        "source_working_tree_dirty": True,
        "uncommitted_paths": 53,
        "uncommitted_paths_outside_source": 50,
    }
    binding = bench.local_binding(
        provenance, header["query_identity"], header["base_settings"], MODELS
    )
    digest = bench.binding_sha256(binding)
    header["provenance"] = {**provenance, "binding": binding, "binding_sha256": digest}
    header["experiment_id"] = bench.local_experiment_id(digest)
    return header


@pytest.fixture(scope="module")
def local_artifacts(tmp_path_factory):
    header = make_local_header()
    records = m8.make_records(header)
    out = tmp_path_factory.mktemp("local-artifacts")
    paths = bench.artifact_paths(out, header["experiment_id"])
    raw_sha = bench.write_raw(paths, header, records)
    bench.write_summary(paths, header, records, raw_sha, {"note": "synthetic"})
    return paths


def test_local_artifacts_verify_and_the_report_states_the_snapshot(local_artifacts):
    assert bench.verify_artifacts(local_artifacts["summary"], MODELS) == []
    report = local_artifacts["report"].read_text(encoding="utf-8")
    assert "Local snapshot (not reproducible from HEAD)" in report
    assert "['pyproject.toml']" in report


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h["provenance"]["binding"].update(source_tree_sha256="9" * 64), "hash"),
        (lambda h: h.update(experiment_id="rerank-m10-local-000000000000"), "derived from"),
        (lambda h: h["source"].update(source_tree_sha256="9" * 64), "source fingerprint"),
        (lambda h: h["provenance"].update(guarded_differences=[]), "guarded differences"),
        (lambda h: h.update(libraries={"torch": "0"}), "library versions"),
        (lambda h: h["provenance"].update(production_commit="0" * 40), "production commit"),
    ],
)
def test_a_tampered_local_header_is_refused(mutate, message):
    header = make_local_header()
    assert bench.check_header(header, MODELS) == []
    mutate(header)
    problems = bench.check_header(header, MODELS)
    assert any(message in p for p in problems), problems


def test_an_m8_header_never_passes_as_local_or_vice_versa():
    m8_header = m8.make_header()
    assert bench.check_header(m8_header, MODELS) == []
    m8_header["provenance"]["mode"] = bench.LOCAL_MODE
    assert bench.check_header(m8_header, MODELS) != []
    local = make_local_header()
    del local["provenance"]["mode"]
    assert any("not a reranker benchmark id" in p for p in bench.check_header(local, MODELS))


def test_the_m8_artifacts_and_protocol_are_untouched():
    assert bench.OUTPUT_DIR == ROOT / "data" / "processed" / "rerank_benchmark"
    assert bench.PRODUCTION_COMMIT == "558bd39e69cc503410beb7efcb648fac169708b2"
    assert bench.PREDECLARED["experiment"] == "M8 Session G latency-only V3 reranker benchmark"
    assert json.loads(json.dumps(bench.PREDECLARED)) == bench.PREDECLARED
    assert isinstance(bench.ROOT, Path)
