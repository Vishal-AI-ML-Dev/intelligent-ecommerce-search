"""Explicit model download: the ONLY code in this project that contacts the network.

`fetch_snapshot` downloads exactly the files a local model needs, from the official Hugging Face
repository, at a full immutable commit hash, into the repository's git-ignored `models/`
directory. It then verifies the resolved snapshot path is that exact revision, refuses
repositories that would need remote code, and writes a SHA-256 manifest of every downloaded file
next to the hub folder.

What is downloaded and required is a `SnapshotProfile`: `EMBEDDING_PROFILE` (the default: the
Milestone 4 sentence-transformers embedding layout, unchanged) or `RERANKER_PROFILE` (the pinned
Milestone 8 cross-encoder, ADR-007). The reranker profile is strict: the snapshot must hold
exactly its reviewed files, each matching its reviewed SHA-256, with no link that leaves the
model's hub folder.

Cache isolation: `huggingface_hub` and `hf_xet` read their cache, token and log locations from
the environment when they are imported. Before importing them, the default downloader points
every such location (`HF_HOME`, `HF_HUB_CACHE`, `HF_ASSETS_CACHE`, `HF_XET_CACHE`,
`HF_XET_LOG_DIR`, `HF_TOKEN_PATH`, `XDG_CACHE_HOME`, ...) inside the selected models directory, so
nothing is read from or written to a user-level or shared cache. If either library was already
imported in this process its locations may already be fixed, so the fetch is refused.

The licence declared in the downloaded model card (README front matter) is reported for the
operator to verify against the official model page; it is not treated as legal advice.
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ecommerce_search.embeddings.spec import (
    check_model_id,
    check_revision,
    repo_folder_name,
    snapshot_dir,
)

# Exact file names (fnmatch patterns without wildcards), so ONNX/OpenVINO/PyTorch-bin variants
# and any Python files are never downloaded.
ALLOW_PATTERNS: tuple[str, ...] = (
    "modules.json",
    "config.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "model.safetensors",
    "1_Pooling/config.json",
    "2_Normalize/*",
    "README.md",
    "LICENSE",
)
REQUIRED: tuple[str, ...] = ("modules.json", "config.json", "model.safetensors", "README.md")

# Milestone 8 (ADR-007): the exact files of `cross-encoder/ms-marco-MiniLM-L6-v2` at its pinned
# revision that loading needs (BERT config, safetensors weights, tokenizer) plus the model card as
# licence evidence. Derived from the file listing of that immutable revision: the PyTorch-bin,
# Flax, ONNX and OpenVINO duplicates and `.gitattributes` are never downloaded. The repository has
# no `modules.json`, `LICENSE` or Python file at that revision.
RERANKER_ALLOW_PATTERNS: tuple[str, ...] = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "README.md",
)
RERANKER_REQUIRED: tuple[str, ...] = RERANKER_ALLOW_PATTERNS
# SHA-256 of every reviewed file at the pinned revision (ADR-007). `model.safetensors` equals the
# LFS SHA-256 published in the repository metadata; every other file was cross-checked against the
# size and git blob id in that metadata.
RERANKER_PINNED_SHA256: tuple[tuple[str, str], ...] = (
    ("README.md", "7c0ec39941d0d1d766ccde671f592971d82fe45b7eea550a80d1ea864ebc3baa"),
    ("config.json", "380e02c93f431831be65d99a4e7e5f67c133985bf2e77d9d4eba46847190bacc"),
    ("model.safetensors", "821d1aa69520101d6e0737f78a042ae25b19e5cb9160701909d10434f4aeb0ae"),
    ("special_tokens_map.json", "3c3507f36dff57bce437223db3b3081d1e2b52ec3e56ee55438193ecb2c94dd6"),
    ("tokenizer.json", "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
    ("tokenizer_config.json", "a5c2e5a7b1a29a0702cd28c08a399b5ecc110c263009d17f7e3b415f25905fd8"),
    ("vocab.txt", "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"),
)
MANIFEST_VERSION = 1
_FRONT_MATTER_LICENSE = re.compile(r"^license:\s*(?P<value>\S.*?)\s*$", re.MULTILINE)


class FetchError(Exception):
    """The snapshot could not be fetched or failed verification. Messages are sanitized."""


@dataclass(frozen=True)
class SnapshotProfile:
    """What one kind of model snapshot downloads and must contain.

    A `strict` profile additionally requires the snapshot to hold exactly `required` (no other
    file), every file in `pinned_sha256` to match, and every link (symbolic link or Windows
    junction) to stay inside the model's hub folder: file links only (on POSIX the hub cache
    links snapshot files to its `blobs/`), never a linked directory or snapshot folder."""

    kind: str
    allow_patterns: tuple[str, ...]
    required: tuple[str, ...]
    pinned_sha256: tuple[tuple[str, str], ...] = field(default=())
    strict: bool = False


EMBEDDING_PROFILE = SnapshotProfile("embedding", ALLOW_PATTERNS, REQUIRED)
RERANKER_PROFILE = SnapshotProfile(
    "reranker",
    RERANKER_ALLOW_PATTERNS,
    RERANKER_REQUIRED,
    pinned_sha256=RERANKER_PINNED_SHA256,
    strict=True,
)


@dataclass(frozen=True)
class FetchResult:
    model_id: str
    revision: str
    snapshot: Path
    manifest_path: Path
    files: list[dict]
    total_bytes: int
    declared_license: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def declared_license(readme: Path) -> str | None:
    """The `license:` value of the model card's YAML front matter, if present."""
    text = readme.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = _FRONT_MATTER_LICENSE.search(text[: end if end > 0 else len(text)])
    return match["value"].strip("'\"") if match else None


def _well_formed_files(files: object) -> bool:
    return isinstance(files, list) and all(
        isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("sha256"), str)
        for entry in files
    )


def file_manifest(snapshot: Path) -> list[dict]:
    """(path, bytes, sha256) of every file in the snapshot (symlinks resolved)."""
    # Sorted by the POSIX path string: Windows path comparison is case-insensitive, so sorting
    # Path objects would give a platform-dependent order.
    files = {p.relative_to(snapshot).as_posix(): p for p in snapshot.rglob("*") if p.is_file()}
    return [
        {"path": name, "bytes": files[name].stat().st_size, "sha256": sha256_file(files[name])}
        for name in sorted(files)
    ]


def _is_link(path: Path) -> bool:
    # A Windows directory junction is not a symbolic link to `is_symlink`, but is followed alike.
    return path.is_symlink() or path.is_junction()


def _refuse_unlisted_directory(error: OSError) -> None:
    raise FetchError("snapshot could not be listed; refusing")


def _verify_links(snapshot: Path) -> None:
    """Every link stays inside the model's hub folder; checked before any recursive listing.

    The model folder, `snapshots/` and the snapshot directory must be real directories, and no
    directory inside the snapshot may be a link (a linked directory could leave the folder or
    loop). File links (the POSIX hub cache links snapshot files to `blobs/`) must resolve inside
    the model folder. The walk never follows links."""
    repo_folder = snapshot.parent.parent
    try:
        if any(_is_link(p) for p in (repo_folder, snapshot.parent, snapshot)):
            raise FetchError("snapshot folder is a link; refusing")
        root = repo_folder.resolve(strict=True)
        walk = os.walk(snapshot, followlinks=False, onerror=_refuse_unlisted_directory)
        for directory, dirnames, filenames in walk:
            if any(_is_link(Path(directory, name)) for name in dirnames):
                raise FetchError("snapshot contains a linked directory; refusing")
            for name in filenames:
                path = Path(directory, name)
                if _is_link(path) and not path.resolve(strict=True).is_relative_to(root):
                    raise FetchError("snapshot contains a link outside the model folder; refusing")
    except (OSError, RuntimeError):  # a dangling or looping link
        raise FetchError("snapshot contains an unresolvable link; refusing") from None


def _verify_strict(snapshot: Path, profile: SnapshotProfile) -> None:
    """Exact file set and pinned checksums (links were checked by `_verify_links`)."""
    files = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
    extra = sorted(files - set(profile.required))
    if extra:
        raise FetchError(f"snapshot contains unexpected files: {', '.join(extra)}")
    changed = [
        name for name, digest in profile.pinned_sha256 if sha256_file(snapshot / name) != digest
    ]
    if changed:
        raise FetchError(f"snapshot files do not match their pinned SHA-256: {', '.join(changed)}")


def verify_snapshot(snapshot: Path, profile: SnapshotProfile = EMBEDDING_PROFILE) -> None:
    missing = [name for name in profile.required if not (snapshot / name).is_file()]
    if missing:
        raise FetchError(f"snapshot is missing required files: {', '.join(missing)}")
    if profile.strict:
        _verify_links(snapshot)
    if any(snapshot.rglob("*.py")):
        raise FetchError("snapshot contains Python files; remote code is never used")
    try:
        config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise FetchError("snapshot config.json is not valid JSON") from None
    except UnicodeDecodeError:
        if not profile.strict:
            raise
        raise FetchError("snapshot config.json is not valid JSON") from None
    if profile.strict and not isinstance(config, dict):
        raise FetchError("snapshot config.json is not a JSON object")
    if "auto_map" in config:
        raise FetchError("model requires remote code (auto_map); refusing")
    if profile.strict:
        _verify_strict(snapshot, profile)


# Libraries whose cache/log locations are fixed at import time.
HF_LIBRARIES: tuple[str, ...] = ("huggingface_hub", "hf_xet")


def isolated_hf_environment(models_dir: Path) -> dict[str, str]:
    """Every Hugging Face cache, token and log location, inside `models_dir` (absolute paths)."""
    root = models_dir.resolve()
    home = root / ".hf_home"
    hub = root / "hub"
    values = {
        "HF_HOME": home,
        "HF_HUB_CACHE": hub,
        "HUGGINGFACE_HUB_CACHE": hub,  # legacy name, still read by huggingface_hub
        "HF_ASSETS_CACHE": home / "assets",
        "HUGGINGFACE_ASSETS_CACHE": home / "assets",
        "HF_TOKEN_PATH": home / "token",  # no user token is read: public repositories only
        "HF_XET_CACHE": home / "xet",
        "HF_XET_LOG_DIR": home / "xet" / "logs",
        "XDG_CACHE_HOME": home / "xdg",  # fallback root used when HF_HOME is unset elsewhere
    }
    env = {name: str(path) for name, path in values.items()}
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def _import_snapshot_download() -> Callable[..., str]:
    from huggingface_hub import snapshot_download

    return snapshot_download


def isolated_download(models_dir: Path, **kwargs) -> str:
    """Download with every Hugging Face location confined to `models_dir`."""
    loaded = [name for name in HF_LIBRARIES if name in sys.modules]
    if loaded:
        raise FetchError(
            f"{', '.join(loaded)} is already imported in this process, so its cache locations "
            "may point outside the models directory; run model-fetch in a fresh process"
        )
    os.environ.update(isolated_hf_environment(models_dir))
    snapshot_download = _import_snapshot_download()
    # Sequential downloads: parallel connections were reset by the network in practice, and a
    # few small files plus one weights file gain little from parallelism.
    return snapshot_download(max_workers=1, **kwargs)


def fetch_snapshot(
    model_id: str,
    revision: str,
    models_dir: Path,
    download: Callable[..., str] | None = None,
    *,
    profile: SnapshotProfile = EMBEDDING_PROFILE,
) -> FetchResult:
    """Fetch one snapshot. `download` replaces the isolated Hugging Face downloader in tests."""
    check_model_id(model_id)
    check_revision(revision)
    expected = snapshot_dir(models_dir, model_id, revision)
    request = {
        "repo_id": model_id,
        "revision": revision,
        "cache_dir": str((models_dir / "hub").resolve()),
        "allow_patterns": list(profile.allow_patterns),
    }
    try:
        if download is None:
            resolved = Path(isolated_download(models_dir, **request))
        else:
            resolved = Path(download(**request))
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"download failed ({type(exc).__name__})") from None
    if resolved.resolve() != expected.resolve():
        raise FetchError("the downloaded snapshot is not the requested revision")
    verify_snapshot(resolved, profile)
    files = file_manifest(resolved)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "model_id": model_id,
        "revision": revision,
        "allow_patterns": list(profile.allow_patterns),
        "declared_license": declared_license(resolved / "README.md"),
        "files": files,
    }
    manifest_dir = models_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{repo_folder_name(model_id)}@{revision}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return FetchResult(
        model_id=model_id,
        revision=revision,
        snapshot=resolved,
        manifest_path=manifest_path,
        files=files,
        total_bytes=sum(f["bytes"] for f in files),
        declared_license=manifest["declared_license"],
    )


MISSING_PROBLEM = "snapshot or manifest is missing (run model-fetch)"


def verify_manifest(
    models_dir: Path,
    model_id: str,
    revision: str,
    *,
    profile: SnapshotProfile | None = None,
) -> list[str]:
    """Offline re-check of a fetched snapshot against its manifest. Returns problems found.

    Without `profile` this is the Milestone 4 check, unchanged. With a profile, an unreadable or
    malformed manifest is reported instead of raised, the manifest must record that profile's
    allow patterns, and the snapshot must pass `verify_snapshot` for it (a strict profile: exact
    file set, pinned SHA-256, confined links) before the per-file comparison."""
    manifest_path = models_dir / "manifests" / f"{repo_folder_name(model_id)}@{revision}.json"
    snapshot = snapshot_dir(models_dir, model_id, revision)
    if not manifest_path.is_file() or not snapshot.is_dir():
        return [MISSING_PROBLEM]
    if profile is None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return ["manifest is not valid JSON"]
        if not isinstance(manifest, dict) or not _well_formed_files(manifest.get("files")):
            return ["manifest is malformed"]
    if manifest.get("model_id") != model_id or manifest.get("revision") != revision:
        return ["manifest does not describe this model and revision"]
    if profile is not None:
        if manifest.get("allow_patterns") != list(profile.allow_patterns):
            return [f"manifest was not written for the {profile.kind} profile"]
        try:
            verify_snapshot(snapshot, profile)
        except FetchError as exc:
            return [str(exc)]
    recorded = {f["path"]: f for f in manifest["files"]}
    actual = {f["path"]: f for f in file_manifest(snapshot)}
    problems = [f"missing file {p}" for p in sorted(set(recorded) - set(actual))]
    problems += [f"unexpected file {p}" for p in sorted(set(actual) - set(recorded))]
    problems += [
        f"changed file {p}"
        for p in sorted(set(recorded) & set(actual))
        if recorded[p]["sha256"] != actual[p]["sha256"]
    ]
    return problems
