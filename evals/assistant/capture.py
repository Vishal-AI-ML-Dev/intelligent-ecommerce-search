"""Frozen V3 evidence capture for a draft eval set (Milestone 17, Step B).

Runs the assistant's own V3 retriever adapter (`RerankedSearchRetriever`) once per clean message
against the local database, with the locally cached embedder and reranker snapshots (loaded
offline; nothing is downloaded) and the deterministic decision provider, at the assistant's
configured `evidence_k`. Every database transaction is read-only. No generation provider is
built or called.
"""

import subprocess
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ecommerce_search.api.assistant import RerankedSearchRetriever, limits_from
from ecommerce_search.config import Settings
from ecommerce_search.decision import DeterministicDecisionProvider
from ecommerce_search.embeddings.sentence_transformers_provider import (
    SentenceTransformerEmbedder,
)
from ecommerce_search.reranking.cross_encoder_provider import CrossEncoderReranker
from ecommerce_search.search.query import normalize_query
from evals.assistant.draft import Capture, CaptureRun

EXTRAS_SQL = text(
    "SELECT product_id, wireless, anc, NULL AS gender, NULL AS color FROM headphone_specs "
    "WHERE product_id = ANY(:ids) UNION ALL "
    "SELECT product_id, NULL, NULL, gender, color FROM shoe_specs WHERE product_id = ANY(:ids)"
)
DATASET_SQL = text(
    "SELECT dataset_id, dataset_version, is_synthetic, record_count, checksum_sha256 "
    "FROM catalog_datasets ORDER BY id"
)


def _git_head() -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, as every harness here
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def capture(messages: dict[str, str], settings: Settings | None = None) -> CaptureRun:
    """`messages` maps clean case_id -> message."""
    settings = settings or Settings()
    models_dir = settings.resolved_models_dir()
    if models_dir is None:
        raise RuntimeError("no local models directory")
    engine = create_engine(
        settings.database_url(),
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    embedder = SentenceTransformerEmbedder(
        settings.embedding_spec(), models_dir, batch_size=settings.embedding_batch_size
    )
    reranker = CrossEncoderReranker(
        settings.reranker_spec(), models_dir, batch_size=settings.reranker_batch_size
    )
    top_k = limits_from(settings).evidence_k
    captured_at = datetime.now(UTC).replace(microsecond=0)
    try:
        with Session(engine) as session:
            retriever = RerankedSearchRetriever(
                session, settings, embedder, reranker, DeterministicDecisionProvider()
            )
            captures: dict[str, Capture] = {}
            for case_id, message in messages.items():
                query = normalize_query(message, settings.search_max_query_length)
                got = retriever.retrieve(query, top_k)
                captures[case_id] = Capture(got.search_version, got.products)
            ids = sorted({p.product_id for c in captures.values() for p in c.evidence})
            extras: dict[str, dict[str, Any]] = {
                row.product_id: {
                    "wireless": row.wireless,
                    "anc": row.anc,
                    "gender": row.gender,
                    "color": row.color,
                }
                for row in session.execute(EXTRAS_SQL, {"ids": ids})
            }
            datasets = [dict(row._mapping) for row in session.execute(DATASET_SQL)]
            alembic = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    if len(datasets) != 1:
        raise RuntimeError(f"expected one catalog dataset, found {len(datasets)}")
    environment = {
        "dataset": datasets[0],
        "alembic_version": alembic,
        "embedding_model": f"{settings.embedding_model_id}@{settings.embedding_model_revision}",
        "reranker_model": f"{settings.reranker_model_id}@{settings.reranker_model_revision}",
        "reranker_enabled": settings.reranker_enabled,
        "rerank_depth": settings.search_rerank_depth,
        "search_candidate_k": settings.search_candidate_k,
        "git_head": _git_head(),
        "decision_provider": "deterministic",
        "generation_provider": "none (not built)",
    }
    return CaptureRun(captured_at, top_k, captures, extras, environment)
