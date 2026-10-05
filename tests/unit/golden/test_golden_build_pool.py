"""Freezing and verifying `pool_frozen` in a temporary golden directory. The queries stage is a
copy of the frozen artifacts; the pool comes from SYNTHETIC source responses. The real golden
directory and the real private provenance store are never written."""

import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest
from evals.contracts.canonical import ContractError, sha256_hex
from evals.contracts.manifest import Role, Stage, decode_manifest, stage_roles
from evals.contracts.pooling import assemble_pool
from evals.contracts.validation import PoolCapExceeded
from evals.runners import build_pool, freeze_queries
from evals.runners.golden_io import FreezeError, safe_child, write_exclusive
from pool_support import context_for, copy_queries_stage, fake_sources


@pytest.fixture
def golden(tmp_path):
    root, target = copy_queries_stage(tmp_path)
    return root, target, tmp_path / "private"


def _files(golden, catalog, **source_options) -> dict[str, bytes]:
    root, target, _ = golden
    frozen = build_pool.load_frozen_queries(target, allowed_root=root, catalog=catalog)
    query_ids = sorted(q.query_id for q in frozen.queries)
    build = assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        fake_sources(query_ids, catalog, **source_options),
        context_for(frozen, catalog),
    )
    return build_pool.run_files(build)


def _freeze(golden, catalog, files=None, **kwargs):
    root, target, private = golden
    return build_pool.freeze_pool(
        target,
        files or _files(golden, catalog),
        allowed_root=root,
        private_root=private,
        catalog=catalog,
        **kwargs,
    )


def _tree(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}


def test_freeze_writes_the_public_stage_and_binds_the_private_provenance(golden, catalog):
    root, target, private = golden
    before = _tree(target)
    files = _files(golden, catalog)
    manifest, provenance_path = _freeze(golden, catalog, files)
    assert _tree(target) - before == {
        "pool.jsonl",
        "eligibility.jsonl",
        "pool_report.json",
        "manifests/2-pool_frozen.json",
    }
    assert manifest.stage is Stage.POOL_FROZEN
    assert {e.role for e in manifest.entries} == stage_roles(Stage.POOL_FROZEN)
    assert Role.PROVENANCE not in {e.role for e in manifest.entries}
    assert manifest.provenance_binding == sha256_hex(files[build_pool.PROVENANCE_FILE])
    assert manifest.primary_sealed_at is None and manifest.labels_frozen_at is None
    queries_manifest = decode_manifest((target / freeze_queries.MANIFEST_PATH).read_bytes())
    assert manifest.previous_manifest_sha256 == queries_manifest.manifest_sha256
    assert provenance_path.read_bytes() == files[build_pool.PROVENANCE_FILE]
    assert provenance_path.parent.parent == private
    # The provenance never lands in the golden directory.
    assert not any("provenance" in p for p in _tree(target))
    result = build_pool.verify_pool(
        target, allowed_root=root, private_root=private, catalog=catalog
    )
    assert result.provenance_path == provenance_path
    public = build_pool.verify_pool(
        target, allowed_root=root, public_only=True, private_root=private, catalog=catalog
    )
    assert public.provenance_path is None


def test_nothing_is_overwritten(golden, catalog):
    root, target, private = golden
    _freeze(golden, catalog)
    snapshot = {p: (target / p).read_bytes() for p in _tree(target)}
    with pytest.raises(FreezeError, match="overwrite"):
        _freeze(golden, catalog)
    assert {p: (target / p).read_bytes() for p in _tree(target)} == snapshot


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_a_failure_part_way_leaves_nothing_behind(golden, catalog, fail_at):
    _, target, private = golden
    before = _tree(target)
    calls = []

    def flaky(source, destination):
        calls.append(destination)
        if len(calls) == fail_at + 1:  # the first link is the private provenance
            raise OSError("simulated failure")
        os.link(source, destination)

    with pytest.raises(OSError, match="simulated"):
        _freeze(golden, catalog, link=flaky)
    assert _tree(target) == before
    assert _tree(private) == set()


def test_a_failure_writing_the_provenance_writes_nothing(golden, catalog):
    _, target, private = golden
    before = _tree(target)

    def broken(source, destination):
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        _freeze(golden, catalog, link=broken)
    assert _tree(target) == before and _tree(private) == set()


def test_the_manifest_is_linked_last(golden, catalog):
    order = []

    def recording(source, destination):
        order.append(destination.name)
        os.link(source, destination)

    _freeze(golden, catalog, link=recording)
    assert order[0] == build_pool.PROVENANCE_FILE
    assert order[-1] == "2-pool_frozen.json" and len(order) == 5


def test_a_pool_over_the_cap_freezes_nothing(golden, catalog):
    _, target, private = golden
    before = _tree(target)
    with pytest.raises(PoolCapExceeded):
        _freeze(golden, catalog, _files(golden, catalog, disjoint=True))
    assert _tree(target) == before and _tree(private) == set()


def test_runs_must_be_byte_identical(golden, catalog):
    first = _files(golden, catalog)
    build_pool.compare_runs(first, dict(first))
    second = dict(first)
    lines = second["pool.jsonl"].split(b"\n")
    lines[3] = lines[3].replace(b'"blind_position":', b'"blind_position": ')
    second["pool.jsonl"] = b"\n".join(lines)
    with pytest.raises(build_pool.DeterminismError, match=r"pool\.jsonl line 4") as raised:
        build_pool.compare_runs(first, second)
    assert "SYN-" not in str(raised.value)
    with pytest.raises(build_pool.DeterminismError, match="missing"):
        build_pool.compare_runs(first, {k: v for k, v in first.items() if k != "provenance.json"})


def test_run_files_that_disagree_with_their_provenance_are_refused(golden, catalog):
    files = _files(golden, catalog)
    other = _files(golden, catalog, spread=3)
    mixed = {**files, "pool_report.json": other["pool_report.json"]}
    with pytest.raises(ContractError):
        _freeze(golden, catalog, mixed)


@pytest.mark.parametrize("target_file", ["pool.jsonl", "eligibility.jsonl", "pool_report.json"])
def test_tampered_public_files_fail_verification(golden, catalog, target_file):
    root, target, private = golden
    _freeze(golden, catalog)
    path = target / target_file
    data = bytearray(path.read_bytes())
    data[-3] = ord("9") if data[-3] != ord("9") else ord("8")
    path.write_bytes(bytes(data))
    with pytest.raises(ContractError):
        build_pool.verify_pool(target, allowed_root=root, private_root=private, catalog=catalog)


def test_tampered_or_missing_provenance_fails_the_binding(golden, catalog):
    root, target, private = golden
    _, provenance_path = _freeze(golden, catalog)
    original = provenance_path.read_bytes()
    provenance_path.write_bytes(original.replace(b'"rank":1', b'"rank":2', 1))
    with pytest.raises(ContractError, match="provenance"):
        build_pool.verify_pool(target, allowed_root=root, private_root=private, catalog=catalog)
    with pytest.raises(ContractError, match="provenance_binding"):
        build_pool.verify_pool(
            target,
            allowed_root=root,
            private_root=private,
            provenance_path=provenance_path,
            catalog=catalog,
        )
    provenance_path.unlink()
    with pytest.raises(ContractError, match="no private provenance"):
        build_pool.verify_pool(target, allowed_root=root, private_root=private, catalog=catalog)
    # The public files still verify on their own.
    build_pool.verify_pool(
        target, allowed_root=root, private_root=private, public_only=True, catalog=catalog
    )


def test_a_changed_catalog_product_fails_verification(golden, catalog):
    root, target, private = golden
    _freeze(golden, catalog)
    pooled = (target / "pool.jsonl").read_text(encoding="utf-8").split('"product_id":"')[1]
    product_id = pooled.split('"')[0]
    product = catalog.products[product_id]
    products = dict(catalog.products)
    products[product_id] = replace(product, description=(product.description or "") + " more")
    mutated = replace(catalog, products=MappingProxyType(products))
    with pytest.raises(ContractError, match="card hash differs"):
        build_pool.verify_pool(
            target, allowed_root=root, private_root=private, public_only=True, catalog=mutated
        )


def test_a_changed_queries_stage_is_refused(golden, catalog):
    root, target, _ = golden
    files = _files(golden, catalog)
    queries = target / "queries.jsonl"
    queries.write_bytes(queries.read_bytes().replace(b"headphones", b"headphone", 1))
    with pytest.raises(ContractError):
        _freeze(golden, catalog, files)


def test_stale_staging_is_detected_and_never_deleted(golden, catalog):
    root, target, private = golden
    stale = target / ".freeze-staging-abc123"
    stale.mkdir()
    (stale / "00").write_bytes(b"partial")
    with pytest.raises(FreezeError, match="stale freeze staging") as raised:
        _freeze(golden, catalog)
    assert "delete these staging directories by hand" in str(raised.value)
    assert (stale / "00").read_bytes() == b"partial"
    with pytest.raises(FreezeError, match="stale freeze staging"):
        freeze_queries.verify(target, allowed_root=root, catalog=catalog)
    with pytest.raises(FreezeError, match="stale freeze staging"):
        build_pool.verify_pool(target, allowed_root=root, private_root=private, catalog=catalog)
    with pytest.raises(FreezeError, match="stale freeze staging"):
        write_exclusive(target, {"x.json": b"{}\n"})
    assert not (target / "x.json").exists()


def test_stale_staging_in_the_private_store_is_refused(golden, catalog):
    _, target, private = golden
    (private / ".freeze-staging-zz").mkdir(parents=True)
    with pytest.raises(FreezeError, match="stale freeze staging"):
        _freeze(golden, catalog)
    assert not (target / build_pool.POOL_MANIFEST_PATH).exists()


def test_traversal_and_links_are_refused(golden, catalog, monkeypatch):
    root, target, private = golden
    with pytest.raises(FreezeError, match=r"\.\."):
        build_pool.verify_pool(target / ".." / "v1", allowed_root=root, catalog=catalog)
    with pytest.raises(FreezeError, match="unsafe"):
        safe_child(target, "../escape.json")
    with pytest.raises(FreezeError, match="unsafe"):
        write_exclusive(target, {"../escape.json": b"x"})
    original = Path.is_symlink
    linked = target / "pool.jsonl"
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == linked or original(p))
    with pytest.raises(FreezeError, match="symlinks"):
        _freeze(golden, catalog)
    monkeypatch.setattr(Path, "is_symlink", original)
    original_junction = Path.is_junction
    monkeypatch.setattr(
        Path, "is_junction", lambda p: p == target / "manifests" or original_junction(p)
    )
    with pytest.raises(FreezeError, match="junctions"):
        _freeze(golden, catalog)
    monkeypatch.setattr(Path, "is_junction", original_junction)
    assert not (target / build_pool.POOL_MANIFEST_PATH).exists()
    assert _tree(private) == set()


def test_report_text_has_aggregates_only(golden, catalog):
    files = _files(golden, catalog)
    text = build_pool.report_text(build_pool.decode_run(files).report)
    assert "gq-p01" in text and "SYN-" not in text and "score" not in text
