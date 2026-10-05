"""Builders for the C2 pool, annotation and seal tests.

The frozen `queries_frozen` artifacts are copied read-only into a pytest temporary directory;
the real golden directory is never written. Source responses here are SYNTHETIC: deterministic
fake ranked lists, not retrieval output. Every judgement built here is a SYNTHETIC TEST
FIXTURE, never a human label.
"""

import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import Label, Relation
from evals.contracts.models import (
    SOURCE_ORDER,
    Confidence,
    Judgement,
    RerankerMode,
    Round,
    SourceRun,
    SourceSystem,
)
from evals.contracts.pooling import FetchedSource
from evals.runners import build_pool
from golden_support import run_context

ROOT = Path(__file__).resolve().parents[3]
REAL_DIR = ROOT / "evals" / "goldens" / "internal-search" / "v1"
QUERIES_STAGE_FILES = (
    "authoring/pilot_input.json",
    "queries.jsonl",
    "gold_constraints.jsonl",
    "product_intents.jsonl",
    "query_extraction.jsonl",
    "authoring_attestation.json",
    "manifests/1-queries_frozen.json",
)
# After the real queries were confirmed (2026-10-05T05:28:59Z); fixture judgement times.
LABEL_T0 = datetime(2026, 10, 6, 9, 0, 0, 0, tzinfo=UTC)


def copy_queries_stage(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "goldens"
    target = root / "internal-search" / "v1"
    for relative in QUERIES_STAGE_FILES:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL_DIR / relative, destination)
    return root, target


def _order(*parts: str) -> str:
    return hashlib.sha256("|".join(("fake-source", *parts)).encode()).hexdigest()


def fake_sources(
    query_ids: list[str],
    catalog: CatalogIndex,
    *,
    spread: int = 2,
    length: int = 20,
    disjoint: bool = False,
) -> list[FetchedSource]:
    """SYNTHETIC source responses: per query one fixed pseudo-random catalog order; source i
    returns `length` products starting at offset `i * spread` (or disjoint windows)."""
    out = []
    for query_id in query_ids:
        ordering = sorted(catalog.products, key=lambda p: _order(query_id, p))
        for index, system in enumerate(SOURCE_ORDER):
            start = index * (length if disjoint else spread)
            ids = tuple(ordering[start : start + length])
            v3 = system in (SourceSystem.V3_RERANKED_D10, SourceSystem.V3_RERANKED_D20)
            run = SourceRun(
                query_id=query_id,
                system=system,
                endpoint="/search/reranked" if v3 else "/search",
                search_version="v3_reranked" if v3 else "v0_lexical",
                top_k=20,
                product_ids=ids,
                reranker=RerankerMode(enabled=True, depth=10, status="applied", reranked_count=10)
                if v3
                else None,
            )
            scores = tuple(
                f"{0.5 + i / 1000:.3f}" if not v3 or i < 10 else None for i in range(len(ids))
            )
            out.append(FetchedSource(run=run, scores=scores))
    return out


def context_for(frozen: build_pool.FrozenQueries, catalog: CatalogIndex):
    return run_context(
        queries_manifest_sha256=frozen.manifest.manifest_sha256,
        dataset_id=catalog.dataset_id,
        dataset_version=catalog.dataset_version,
        dataset_checksum_sha256=catalog.checksum_sha256,
    )


def synthetic_label(index: int) -> tuple[Label, Relation, str | None]:
    cycle = [
        (Label.NOT_RELEVANT, Relation.OFF_TARGET, None),
        (Label.BORDERLINE, Relation.SUBSTITUTE, "Synthetic fixture rationale."),
        (Label.NOT_RELEVANT, Relation.COMPLEMENT, None),
    ]
    return cycle[index % len(cycle)]


def synthetic_primaries(items, *, handle: str = "ann-1", start: datetime = LABEL_T0):
    """SYNTHETIC primary judgements for every pooled item (never relevant, so the hard
    constraint rule always holds)."""
    out = []
    for index, item in enumerate(items):
        label, relation, rationale = synthetic_label(index)
        out.append(
            Judgement(
                query_id=item.query_id,
                product_id=item.product_id,
                round=Round.PRIMARY,
                label=label,
                relation=relation,
                confidence=Confidence.HIGH,
                rationale=rationale,
                annotator_handle=handle,
                annotated_at=start + timedelta(seconds=index),
                rubric_version="rubric-1",
            )
        )
    return out


def frozen_pool(tmp_path: Path, catalog: CatalogIndex):
    """A temporary golden directory frozen through `pool_frozen` from SYNTHETIC sources.
    Returns (allowed_root, golden_dir, private_root, work_root)."""
    from evals.contracts.pooling import assemble_pool

    root, target = copy_queries_stage(tmp_path)
    frozen = build_pool.load_frozen_queries(target, allowed_root=root, catalog=catalog)
    query_ids = sorted(q.query_id for q in frozen.queries)
    build = assemble_pool(
        frozen.queries,
        frozen.constraints,
        catalog,
        fake_sources(query_ids, catalog),
        context_for(frozen, catalog),
    )
    private = tmp_path / "private"
    build_pool.freeze_pool(
        target,
        build_pool.run_files(build),
        allowed_root=root,
        private_root=private,
        catalog=catalog,
    )
    return root, target, private, tmp_path / "work"
