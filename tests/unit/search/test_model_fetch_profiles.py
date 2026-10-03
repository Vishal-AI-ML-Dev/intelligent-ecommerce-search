"""Milestone 8 Session C: snapshot profiles, the strict reranker profile, and the
`model-fetch --kind` / `model-verify` command line. The downloader is always a local fake or a
guard that fails the test: no network is ever used. The unchanged Milestone 4 embedding tests in
`test_model_fetch.py` remain the primary proof of embedding backward compatibility."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from ecommerce_search.embeddings import fetch
from ecommerce_search.embeddings.fetch import (
    ALLOW_PATTERNS,
    EMBEDDING_PROFILE,
    MISSING_PROBLEM,
    REQUIRED,
    RERANKER_ALLOW_PATTERNS,
    RERANKER_PINNED_SHA256,
    RERANKER_PROFILE,
    RERANKER_REQUIRED,
    FetchError,
    SnapshotProfile,
    fetch_snapshot,
    verify_manifest,
    verify_snapshot,
)
from ecommerce_search.embeddings.spec import ALL_MINILM_L6_V2, snapshot_dir
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2
from ecommerce_search.search import cli
from ecommerce_search.search.cli import build_parser, main

pytestmark = pytest.mark.usefixtures("no_socket_connect")

MODEL = "org/fake"
REV = "0123456789abcdef0123456789abcdef01234567"
README = "---\nlicense: apache-2.0\n---\n# Fake\n"
RERANKER_FILES = {
    "config.json": json.dumps({"architectures": ["BertForSequenceClassification"]}),
    "model.safetensors": "weights",
    "tokenizer.json": "{}",
    "tokenizer_config.json": "{}",
    "special_tokens_map.json": "{}",
    "vocab.txt": "[PAD]\n",
    "README.md": README,
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# A strict test profile with the reranker's file set, pinned to the fake contents above.
STRICT = SnapshotProfile(
    "reranker",
    RERANKER_ALLOW_PATTERNS,
    RERANKER_REQUIRED,
    pinned_sha256=tuple(sorted((name, sha(text)) for name, text in RERANKER_FILES.items())),
    strict=True,
)


def fake_download(files: dict[str, str]):
    calls: list[dict] = []

    def download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["cache_dir"]) / "models--org--fake" / "snapshots" / REV
        for name, content in files.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            # Exact bytes: no newline translation, so the pinned hashes hold on Windows too.
            (target / name).write_bytes(content.encode("utf-8"))
        return str(target)

    return download, calls


@pytest.fixture
def no_download(monkeypatch):
    """Any attempt to reach the real downloader fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("the network-capable downloader must not be called")

    monkeypatch.setattr(fetch, "isolated_download", refuse)
    monkeypatch.setattr(fetch, "_import_snapshot_download", refuse)


# ---- profiles --------------------------------------------------------------------------------


def test_embedding_profile_is_the_milestone_4_layout():
    assert EMBEDDING_PROFILE.kind == "embedding"
    assert EMBEDDING_PROFILE.allow_patterns == ALLOW_PATTERNS
    assert EMBEDDING_PROFILE.required == REQUIRED
    assert EMBEDDING_PROFILE.pinned_sha256 == () and EMBEDDING_PROFILE.strict is False


def test_reranker_profile_is_exactly_the_seven_reviewed_files():
    expected = {
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "README.md",
    }
    assert RERANKER_PROFILE.kind == "reranker" and RERANKER_PROFILE.strict
    assert len(RERANKER_ALLOW_PATTERNS) == 7 and set(RERANKER_ALLOW_PATTERNS) == expected
    assert set(RERANKER_REQUIRED) == expected
    assert not any(ch in p for p in RERANKER_ALLOW_PATTERNS for ch in "*?[")
    for excluded in (".gitattributes", "pytorch_model.bin", "flax_model.msgpack", "modules.json"):
        assert excluded not in RERANKER_ALLOW_PATTERNS
    assert not any(p.startswith(("onnx/", "openvino/")) or p.endswith(".py") for p in expected)
    pins = dict(RERANKER_PINNED_SHA256)
    assert set(pins) == expected and len(RERANKER_PINNED_SHA256) == 7
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in pins.values())
    # The weights pin is the LFS SHA-256 from the repository metadata (ADR-007).
    assert pins["model.safetensors"] == (
        "821d1aa69520101d6e0737f78a042ae25b19e5cb9160701909d10434f4aeb0ae"
    )


# ---- embedding backward compatibility --------------------------------------------------------


def test_default_fetch_request_and_manifest_are_unchanged(tmp_path):
    download, calls = fake_download(
        {"modules.json": "[]", "config.json": "{}", "model.safetensors": "w", "README.md": README}
    )
    result = fetch_snapshot(MODEL, REV, tmp_path, download=download)
    (call,) = calls
    assert set(call) == {"repo_id", "revision", "cache_dir", "allow_patterns"}
    assert call["allow_patterns"] == list(ALLOW_PATTERNS)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "manifest_version",
        "model_id",
        "revision",
        "allow_patterns",
        "declared_license",
        "files",
    }
    assert manifest["allow_patterns"] == list(ALLOW_PATTERNS)
    assert verify_manifest(tmp_path, MODEL, REV) == []


def test_default_verify_manifest_still_raises_on_an_unreadable_manifest(tmp_path):
    download, _ = fake_download(
        {"modules.json": "[]", "config.json": "{}", "model.safetensors": "w", "README.md": README}
    )
    result = fetch_snapshot(MODEL, REV, tmp_path, download=download)
    result.manifest_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        verify_manifest(tmp_path, MODEL, REV)  # Milestone 4 behaviour, unchanged


def test_default_verify_snapshot_tolerates_extra_files(tmp_path):
    for name, text in {
        "modules.json": "[]",
        "config.json": "{}",
        "model.safetensors": "w",
        "README.md": README,
        "extra.txt": "x",
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    verify_snapshot(tmp_path)  # non-strict: no exception


def test_default_verify_snapshot_still_raises_on_invalid_utf8_config(tmp_path):
    for name in REQUIRED:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "config.json").write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        verify_snapshot(tmp_path)


# ---- strict reranker profile -----------------------------------------------------------------


def test_strict_fetch_passes_the_profile_patterns_and_writes_a_verifiable_manifest(tmp_path):
    download, calls = fake_download(RERANKER_FILES)
    result = fetch_snapshot(MODEL, REV, tmp_path, download=download, profile=STRICT)
    assert calls[0]["allow_patterns"] == list(RERANKER_ALLOW_PATTERNS)
    assert len(calls) == 1  # one download call, never repeated
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["allow_patterns"] == list(RERANKER_ALLOW_PATTERNS)
    assert [f["path"] for f in manifest["files"]] == sorted(RERANKER_FILES)
    assert all(f["sha256"] == sha(RERANKER_FILES[f["path"]]) for f in manifest["files"])
    assert result.declared_license == "apache-2.0"
    assert verify_manifest(tmp_path, MODEL, REV, profile=STRICT) == []


def test_the_real_reranker_profile_requests_only_the_reviewed_files(tmp_path):
    download, calls = fake_download(RERANKER_FILES)
    with pytest.raises(FetchError, match="pinned SHA-256"):  # fake contents are not the pins
        fetch_snapshot(MODEL, REV, tmp_path, download=download, profile=RERANKER_PROFILE)
    assert calls[0]["allow_patterns"] == list(RERANKER_ALLOW_PATTERNS)
    assert not (tmp_path / "manifests").exists()  # nothing recorded for a refused snapshot


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra.bin": "x"}, "unexpected files: extra.bin"),
        ({"onnx/model.onnx": "x"}, "unexpected files: onnx/model.onnx"),
        ({"model.safetensors": "tampered"}, "pinned SHA-256: model.safetensors"),
        ({"vocab.txt": "other"}, "pinned SHA-256: vocab.txt"),
        ({"config.json": "[]"}, "not a JSON object"),
        ({"config.json": json.dumps({"auto_map": {"A": "b.C"}})}, "remote code"),
        ({"modeling_x.py": "print(1)"}, "Python files"),
    ],
)
def test_strict_profile_refuses_altered_or_extra_files(tmp_path, change, message):
    download, _ = fake_download({**RERANKER_FILES, **change})
    with pytest.raises(FetchError, match=re.escape(message)):
        fetch_snapshot(MODEL, REV, tmp_path, download=download, profile=STRICT)


def test_strict_profile_refuses_a_missing_file(tmp_path):
    files = dict(RERANKER_FILES)
    del files["tokenizer.json"]
    download, _ = fake_download(files)
    with pytest.raises(FetchError, match="missing required files: tokenizer.json"):
        fetch_snapshot(MODEL, REV, tmp_path, download=download, profile=STRICT)


def test_strict_profile_refuses_invalid_utf8_config_with_a_sanitized_error(tmp_path):
    for name, text in RERANKER_FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "config.json").write_bytes(b"\xff\xfe")
    with pytest.raises(FetchError, match="not valid JSON"):
        verify_snapshot(tmp_path, STRICT)


def _write_strict_snapshot(models_dir: Path) -> Path:
    download, _ = fake_download(RERANKER_FILES)
    return fetch_snapshot(MODEL, REV, models_dir, download=download, profile=STRICT).snapshot


def test_strict_profile_refuses_a_link_outside_the_model_folder(tmp_path):
    snapshot = _write_strict_snapshot(tmp_path / "models")
    outside = tmp_path / "outside.bin"
    outside.write_text("weights", encoding="utf-8")
    (snapshot / "model.safetensors").unlink()
    try:
        os.symlink(outside, snapshot / "model.safetensors")
    except OSError:
        pytest.skip("this platform does not allow creating symbolic links")
    problems = verify_manifest(tmp_path / "models", MODEL, REV, profile=STRICT)
    assert problems == ["snapshot contains a link outside the model folder; refusing"]


def test_strict_profile_accepts_links_into_the_hub_blobs(tmp_path):
    snapshot = _write_strict_snapshot(tmp_path / "models")
    blob = snapshot.parent.parent / "blobs" / "abc"
    blob.parent.mkdir(parents=True)
    blob.write_text("weights", encoding="utf-8")
    (snapshot / "model.safetensors").unlink()
    try:
        os.symlink(blob, snapshot / "model.safetensors")
    except OSError:
        pytest.skip("this platform does not allow creating symbolic links")
    assert verify_manifest(tmp_path / "models", MODEL, REV, profile=STRICT) == []


@pytest.mark.parametrize(
    ("target", "problem"),
    [
        ("outside", "snapshot contains a link outside the model folder; refusing"),
        ("blobs", None),
        ("dangling", "snapshot contains an unresolvable link; refusing"),
    ],
)
def test_link_confinement_logic_on_any_platform(tmp_path, monkeypatch, target, problem):
    """Simulated links (real ones need privileges on Windows): `model.safetensors` reports itself
    as a symbolic link whose target is outside the model folder, in its blobs, or missing."""
    models = tmp_path / "models"
    snapshot = _write_strict_snapshot(models)
    real_resolve = Path.resolve
    link = snapshot / "model.safetensors"

    def is_symlink(self):
        return self == link

    def resolve(self, strict=False):
        if self != link:
            return real_resolve(self, strict=strict)
        if target == "dangling":
            raise FileNotFoundError(str(self))
        if target == "outside":
            return tmp_path / "outside.bin"
        return real_resolve(snapshot.parent.parent, strict=False) / "blobs" / "abc"

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(Path, "resolve", resolve)
    problems = verify_manifest(models, MODEL, REV, profile=STRICT)
    assert problems == ([problem] if problem else [])


def test_a_looping_file_link_is_unresolvable_on_any_platform(tmp_path, monkeypatch):
    # Python 3.12 reports a symbolic-link loop from `resolve(strict=True)` as RuntimeError.
    models = tmp_path / "models"
    link = _write_strict_snapshot(models) / "model.safetensors"
    real_resolve = Path.resolve

    def resolve(self, strict=False):
        if self == link:
            raise RuntimeError("Symlink loop")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert verify_manifest(models, MODEL, REV, profile=STRICT) == [
        "snapshot contains an unresolvable link; refusing"
    ]


def _no_recursive_listing(monkeypatch):
    """A refused link must be detected before any recursive listing could follow it."""
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: pytest.fail("listed before link check"))


@pytest.mark.parametrize("level", ["model folder", "snapshots", "snapshot"])
def test_a_linked_snapshot_folder_is_refused_on_any_platform(tmp_path, monkeypatch, level):
    """Simulated: the snapshot directory, or a folder above it inside the model folder, reports
    itself as a link (it could point anywhere, so links below it would prove nothing)."""
    models = tmp_path / "models"
    snapshot = _write_strict_snapshot(models)
    linked = {"model folder": snapshot.parent.parent, "snapshots": snapshot.parent}.get(
        level, snapshot
    )
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked)
    _no_recursive_listing(monkeypatch)
    with pytest.raises(FetchError, match="^snapshot folder is a link; refusing$"):
        verify_snapshot(snapshot, STRICT)


def test_a_linked_directory_inside_the_snapshot_is_refused_on_any_platform(tmp_path, monkeypatch):
    """Simulated: a nested directory reports itself as a link (to outside, or a loop)."""
    models = tmp_path / "models"
    snapshot = _write_strict_snapshot(models)
    nested = snapshot / "sub"
    nested.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == nested)
    _no_recursive_listing(monkeypatch)
    assert verify_manifest(models, MODEL, REV, profile=STRICT) == [
        "snapshot contains a linked directory; refusing"
    ]


def test_an_empty_real_subdirectory_is_not_a_link(tmp_path):
    models = tmp_path / "models"
    (_write_strict_snapshot(models) / "sub").mkdir()
    assert verify_manifest(models, MODEL, REV, profile=STRICT) == []


def _junction(link: Path, target: Path) -> None:
    """A real Windows directory junction (needs no privilege, unlike a symbolic link)."""
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("directory junctions are a Windows feature")
    winapi.CreateJunction(str(target), str(link))
    assert link.is_junction() and not link.is_symlink()


@pytest.mark.parametrize("case", ["snapshot", "nested outside", "nested loop"])
def test_real_windows_junctions_are_refused(tmp_path, case):
    models = tmp_path / "models"
    snapshot = _write_strict_snapshot(models)
    outside = tmp_path / "outside"
    outside.mkdir()
    if case == "snapshot":
        # The snapshot folder itself points outside, holding byte-identical files.
        for path in snapshot.iterdir():
            (outside / path.name).write_bytes(path.read_bytes())
            path.unlink()
        snapshot.rmdir()
        _junction(snapshot, outside)
        expected = "snapshot folder is a link; refusing"
    else:
        _junction(snapshot / "sub", outside if case == "nested outside" else snapshot)
        expected = "snapshot contains a linked directory; refusing"
    assert verify_manifest(models, MODEL, REV, profile=STRICT) == [expected]


def test_a_real_symbolic_link_to_a_directory_is_refused(tmp_path):
    models = tmp_path / "models"
    snapshot = _write_strict_snapshot(models)
    try:
        os.symlink(snapshot, snapshot / "loop", target_is_directory=True)
    except OSError:
        pytest.skip("this platform does not allow creating symbolic links")
    assert verify_manifest(models, MODEL, REV, profile=STRICT) == [
        "snapshot contains a linked directory; refusing"
    ]


@pytest.mark.parametrize(
    ("manifest_text", "problem"),
    [
        ("{broken", "manifest is not valid JSON"),
        ("[]", "manifest is malformed"),
        ('{"model_id": "org/fake"}', "manifest is malformed"),
        ('{"files": [{"path": 1}]}', "manifest is malformed"),
        ('{"files": [], "model_id": "org/other", "revision": "x"}', "manifest does not describe"),
    ],
)
def test_profile_verification_reports_malformed_manifests_without_raising(
    tmp_path, manifest_text, problem
):
    _write_strict_snapshot(tmp_path)
    manifest_path = next((tmp_path / "manifests").iterdir())
    manifest_path.write_text(manifest_text, encoding="utf-8")
    problems = verify_manifest(tmp_path, MODEL, REV, profile=STRICT)
    assert len(problems) == 1 and problems[0].startswith(problem)
    assert str(tmp_path) not in problems[0]


def test_profile_verification_requires_the_manifest_of_that_profile(tmp_path):
    _write_strict_snapshot(tmp_path)
    assert verify_manifest(tmp_path, MODEL, REV, profile=EMBEDDING_PROFILE) == [
        "manifest was not written for the embedding profile"
    ]


def test_profile_verification_detects_tampering_after_fetch(tmp_path):
    snapshot = _write_strict_snapshot(tmp_path)
    (snapshot / "model.safetensors").write_text("tampered", encoding="utf-8")
    assert verify_manifest(tmp_path, MODEL, REV, profile=STRICT) == [
        "snapshot files do not match their pinned SHA-256: model.safetensors"
    ]
    assert verify_manifest(tmp_path / "empty", MODEL, REV, profile=STRICT) == [MISSING_PROBLEM]


# ---- command line ----------------------------------------------------------------------------

RERANKER_ARGS = [
    "--model-id",
    MS_MARCO_MINILM_L6_V2.model_id,
    "--revision",
    MS_MARCO_MINILM_L6_V2.revision,
]


def test_parser_assigns_each_model_command_its_handler():
    parser = build_parser()
    fetch_args = parser.parse_args(["model-fetch", "--model-id", MODEL, "--revision", REV])
    assert fetch_args.func is cli.cmd_model_fetch and fetch_args.kind == "embedding"
    assert fetch_args.models_dir is None
    verify_args = parser.parse_args(["model-verify"])
    assert verify_args.func is cli.cmd_model_verify and verify_args.kind == "embedding"
    assert parser.parse_args(["model-verify", "--kind", "reranker"]).kind == "reranker"
    with pytest.raises(SystemExit):
        parser.parse_args(["model-verify", "--kind", "other"])
    with pytest.raises(SystemExit):
        parser.parse_args(["model-fetch", "--kind", "reranker"])  # id and revision still required


def test_help_no_longer_says_embedding_only(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())  # argparse wraps help lines
    assert "model-verify" in out and "embedding or reranker" in out


def test_model_fetch_embedding_default_calls_fetch_exactly_as_before(tmp_path, monkeypatch, capsys):
    received = []

    def fake_fetch(*args, **kwargs):
        received.append((args, kwargs))
        raise FetchError("stop here (test)")

    monkeypatch.setattr(cli, "fetch_snapshot", fake_fetch)
    for argv in (
        ["model-fetch", "--model-id", MODEL, "--revision", REV, "--models-dir", str(tmp_path)],
        [
            "model-fetch",
            "--kind",
            "embedding",
            "--model-id",
            MODEL,
            "--revision",
            REV,
            "--models-dir",
            str(tmp_path),
        ],
    ):
        assert main(argv) == 1
    assert received == [((MODEL, REV, tmp_path), {})] * 2
    assert "model kind" not in capsys.readouterr().out  # embedding output unchanged


def test_model_fetch_reranker_uses_the_strict_profile_once(tmp_path, monkeypatch, capsys):
    received = []

    def fake_fetch(*args, **kwargs):
        received.append((args, kwargs))
        raise FetchError("stop here (test)")

    monkeypatch.setattr(cli, "fetch_snapshot", fake_fetch)
    argv = ["model-fetch", "--kind", "reranker", *RERANKER_ARGS, "--models-dir", str(tmp_path)]
    assert main(argv) == 1
    spec = MS_MARCO_MINILM_L6_V2
    assert received == [((spec.model_id, spec.revision, tmp_path), {"profile": RERANKER_PROFILE})]
    out = capsys.readouterr().out
    assert "model kind: reranker" in out and f"downloading {spec.model_id}" in out


@pytest.mark.parametrize(
    ("model_id", "revision"),
    [
        (MODEL, REV),
        (MS_MARCO_MINILM_L6_V2.model_id, "main"),
        (MS_MARCO_MINILM_L6_V2.model_id, "f" * 40),
        ("cross-encoder/ms-marco-MiniLM-L12-v2", MS_MARCO_MINILM_L6_V2.revision),
        (ALL_MINILM_L6_V2.model_id, ALL_MINILM_L6_V2.revision),
    ],
)
def test_model_fetch_reranker_refuses_anything_but_the_pin_before_any_work(
    tmp_path, monkeypatch, capsys, no_download, model_id, revision
):
    monkeypatch.setattr(cli, "fetch_snapshot", lambda *a, **k: pytest.fail("fetch was called"))
    monkeypatch.setattr(cli, "repository_models_dir", lambda: pytest.fail("models dir resolved"))
    argv = ["model-fetch", "--kind", "reranker", "--model-id", model_id, "--revision", revision]
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert "network" not in captured.out
    assert "registry" in captured.err


def _verify_cli(monkeypatch, problems_by_profile, config_problems=()):
    calls = []

    def fake_verify(models_dir, model_id, revision, *, profile=None):
        calls.append((models_dir, model_id, revision, profile))
        return list(problems_by_profile.get(profile.kind if profile else None, []))

    monkeypatch.setattr(cli, "verify_manifest", fake_verify)
    monkeypatch.setattr(
        "ecommerce_search.reranking.cross_encoder_provider.snapshot_config_problems",
        lambda models_dir, spec: list(config_problems),
    )
    monkeypatch.setattr(cli, "fetch_snapshot", lambda *a, **k: pytest.fail("fetch was called"))
    return calls


def test_model_verify_embedding_is_the_default(tmp_path, monkeypatch, capsys, no_download):
    calls = _verify_cli(monkeypatch, {})
    assert main(["model-verify", "--models-dir", str(tmp_path)]) == 0
    spec = ALL_MINILM_L6_V2
    assert calls == [(tmp_path, spec.model_id, spec.revision, EMBEDDING_PROFILE)]
    out = capsys.readouterr().out
    assert "verify: ok" in out and spec.config_sha256() in out


def test_model_verify_reranker_checks_manifest_then_config(
    tmp_path, monkeypatch, capsys, no_download
):
    calls = _verify_cli(monkeypatch, {})
    assert main(["model-verify", "--kind", "reranker", "--models-dir", str(tmp_path)]) == 0
    spec = MS_MARCO_MINILM_L6_V2
    assert calls == [(tmp_path, spec.model_id, spec.revision, RERANKER_PROFILE)]
    out = capsys.readouterr().out
    assert f"{spec.model_id}@{spec.revision}" in out and spec.config_sha256() in out


@pytest.mark.parametrize(
    ("problems", "config_problems", "expected"),
    [
        ({"reranker": ["changed file model.safetensors"]}, (), "changed file model.safetensors"),
        ({}, ("model architecture differs from the reviewed architecture",), "architecture"),
    ],
)
def test_model_verify_reranker_failures_exit_1(
    tmp_path, monkeypatch, capsys, no_download, problems, config_problems, expected
):
    _verify_cli(monkeypatch, problems, config_problems)
    assert main(["model-verify", "--kind", "reranker", "--models-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert expected in err and "failed verification" in err


def test_model_verify_is_offline_on_real_files(tmp_path, capsys, no_download):
    # Nothing fetched: both kinds report the missing snapshot and exit 1, never downloading.
    for kind in ("embedding", "reranker"):
        assert main(["model-verify", "--kind", kind, "--models-dir", str(tmp_path)]) == 1
        assert MISSING_PROBLEM in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_model_verify_embedding_reports_a_malformed_manifest_sanitized(tmp_path, capsys):
    spec = ALL_MINILM_L6_V2
    snapshot_dir(tmp_path, spec.model_id, spec.revision).mkdir(parents=True)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    name = f"models--sentence-transformers--all-MiniLM-L6-v2@{spec.revision}.json"
    (manifests / name).write_text("{broken", encoding="utf-8")
    assert main(["model-verify", "--models-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "manifest is not valid JSON" in err and "Traceback" not in err


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
@pytest.mark.parametrize("error", [PermissionError, UnicodeDecodeError])
def test_model_verify_unreadable_snapshot_is_sanitized(tmp_path, monkeypatch, capsys, kind, error):
    secret_path = str(tmp_path / "private" / "model.safetensors")

    def unreadable(*args, **kwargs):
        if error is UnicodeDecodeError:
            raise UnicodeDecodeError("utf-8", secret_path.encode(), 0, 1, "invalid")
        raise PermissionError(13, "denied", secret_path)

    monkeypatch.setattr(cli, "verify_manifest", unreadable)
    assert main(["model-verify", "--kind", kind, "--models-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert f"{kind} snapshot could not be read" in err
    assert secret_path not in err and "Traceback" not in err and "denied" not in err


def test_model_verify_without_a_checkout_needs_an_explicit_dir(monkeypatch, capsys):
    monkeypatch.setattr(cli, "repository_models_dir", lambda: None)
    assert main(["model-verify", "--kind", "reranker"]) == 1
    assert "pass --models-dir" in capsys.readouterr().err
