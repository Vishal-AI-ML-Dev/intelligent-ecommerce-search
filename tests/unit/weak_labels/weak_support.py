"""Builders for the weak-label tests.

Everything here is a SYNTHETIC TEST FIXTURE: a temporary `pool_frozen` stage built from fake
source responses (the real golden directory is copied read-only and never written), fake
cross-encoder logits (no model is loaded) and, where a test needs them, synthetic audit
decisions typed by the test itself. None of it is a real proposal, human decision or label.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evals.contracts.canonical import sha256_hex
from evals.contracts.catalog import CatalogIndex
from evals.contracts.models import NamedValue
from evals.contracts.pooling import assemble_pool
from evals.runners import build_pool
from evals.weak_labels.audit import AuditContext, AuditStore
from evals.weak_labels.contracts import GenerationRun
from evals.weak_labels.generation import (
    MANIFEST_FILE,
    FrozenFacts,
    RerankerPin,
    build_generation,
    load_facts,
    signals_path,
    verify_generation,
)
from evals.weak_labels.runner import store_generation
from golden_support import run_context
from pool_support import copy_queries_stage, fake_sources

PIN = RerankerPin(
    model_id="cross-encoder/ms-marco-MiniLM-L6-v2",
    revision="233902d25c440f23af6f7d6e94d2946bac0bee0a",
    config_sha256="b30e3ddda48b373d0d929ee16aeec5a2282680b927e47340c5c58c7b299bdef2",
    document_version="rd-1",
)
GENERATED_AT = datetime(2026, 10, 6, 8, 0, 0, 0, tzinfo=UTC)
AUDIT_T0 = datetime(2026, 10, 6, 9, 0, 0, 0, tzinfo=UTC)


@dataclass
class WeakFixture:
    golden_root: Path
    golden_dir: Path
    pool_private: Path
    weak_root: Path
    weak_dir: Path
    signals_root: Path
    audit_root: Path
    facts: FrozenFacts
    logits: dict


def _pinned_context(frozen, catalog: CatalogIndex):
    return run_context(
        queries_manifest_sha256=frozen.manifest.manifest_sha256,
        dataset_id=catalog.dataset_id,
        dataset_version=catalog.dataset_version,
        dataset_checksum_sha256=catalog.checksum_sha256,
        model_pins=(
            NamedValue(name="reranker_config_sha256", value=PIN.config_sha256),
            NamedValue(name="reranker_model_id", value=PIN.model_id),
            NamedValue(name="reranker_revision", value=PIN.revision),
        ),
        component_versions=(
            NamedValue(name="parser", value="qu-1"),
            NamedValue(name="rerank_document_version", value=PIN.document_version),
        ),
    )


def pair_logit(query_id: str, product_id: str) -> float:
    """A SYNTHETIC per-pair "cross-encoder logit" in [-10, 10) (hash-derived, no model)."""
    digest = hashlib.sha256(f"{query_id}|{product_id}".encode()).digest()
    return (int.from_bytes(digest[:4], "big") % 20000) / 1000 - 10


def pinned_sources(query_ids, catalog):
    """`fake_sources` with V3 scores that are a function of the pair, as a real cross-encoder's
    are (the same pair scores the same at rerank depth 10 and 20)."""
    out = []
    for source in fake_sources(query_ids, catalog):
        if source.run.reranker is not None:
            scores = tuple(
                None if score is None else repr(pair_logit(source.run.query_id, product_id))
                for product_id, score in zip(source.run.product_ids, source.scores, strict=True)
            )
            source = replace(source, scores=scores)
        out.append(source)
    return out


def fake_logits(facts: FrozenFacts) -> dict:
    """SYNTHETIC logits for every pooled pair (consistent with the fixture's V3 scores)."""
    return {(i.query_id, i.product_id): pair_logit(i.query_id, i.product_id) for i in facts.pool}


def weak_fixture(tmp_path: Path, catalog: CatalogIndex) -> WeakFixture:
    golden_root, golden_dir = copy_queries_stage(tmp_path)
    frozen = build_pool.load_frozen_queries(golden_dir, allowed_root=golden_root, catalog=catalog)
    query_ids = sorted(q.query_id for q in frozen.queries)
    build = assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        pinned_sources(query_ids, catalog),
        _pinned_context(frozen, catalog),
    )
    pool_private = tmp_path / "pool-private"
    build_pool.freeze_pool(
        golden_dir,
        build_pool.run_files(build),
        allowed_root=golden_root,
        private_root=pool_private,
        catalog=catalog,
    )
    facts = load_facts(
        golden_dir, allowed_root=golden_root, private_root=pool_private, catalog=catalog
    )
    weak_root = tmp_path / "weaksets"
    return WeakFixture(
        golden_root=golden_root,
        golden_dir=golden_dir,
        pool_private=pool_private,
        weak_root=weak_root,
        weak_dir=weak_root / "internal-search" / "v1",
        signals_root=tmp_path / "weak-private",
        audit_root=tmp_path / "weak-audit",
        facts=facts,
        logits=fake_logits(facts),
    )


def generate(fx: WeakFixture) -> dict[str, bytes]:
    """Build and store one generation into the fixture's temporary weak directory."""
    files = build_generation(fx.facts, fx.logits, PIN)
    run = GenerationRun(
        proposal_manifest_sha256=sha256_hex(files[MANIFEST_FILE]),
        generated_at=GENERATED_AT,
        max_reranker_abs_diff_vs_provenance="0.0",
        reranker_scores_checked_vs_provenance=1,
    )
    store_generation(
        files,
        run,
        weak_dir=fx.weak_dir,
        allowed_root=fx.weak_root,
        private_path=signals_path(fx.facts, fx.signals_root),
    )
    return files


def audit_context(fx: WeakFixture) -> AuditContext:
    generation = verify_generation(
        fx.facts, fx.weak_dir, allowed_root=fx.weak_root, private_root=fx.signals_root, pin=PIN
    )
    return AuditContext(facts=fx.facts, generation=generation)


def audit_store(fx: WeakFixture, context: AuditContext) -> AuditStore:
    return AuditStore(context, fx.audit_root)


def scripted(answers):
    """A SYNTHETIC answer sequence for the audit prompts (EOF after the last answer)."""
    remaining = iter(answers)

    def ask(_prompt: str) -> str:
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError from None

    return ask


class Clock:
    """A deterministic clock for audit fixtures."""

    def __init__(self, start: datetime = AUDIT_T0) -> None:
        self.current = start

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current
