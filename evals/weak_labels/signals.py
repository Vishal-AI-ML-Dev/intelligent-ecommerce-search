"""Recompute the pinned local cross-encoder logits for every frozen pool pair. Offline only.

The process first refuses outbound network access and forces hub offline mode, then verifies
both pinned model snapshots offline (`pool_sources.verify_model_pins`) and loads the reranker
from its explicit local snapshot. Each query is scored once, against the `rd-1` documents of
its pooled products in product-id order, with the production scoring path
(`reranking.provider.score_documents`). No remote model, API or Jev is used.
"""

from evals.weak_labels.generation import FrozenFacts, Pair, WeakLabelError


def score_pool(facts: FrozenFacts) -> dict[Pair, float]:  # pragma: no cover - loads the model
    from ecommerce_search.embeddings.spec import repository_models_dir
    from ecommerce_search.ingestion.loader import load_catalog
    from ecommerce_search.reranking.cross_encoder_provider import CrossEncoderReranker
    from ecommerce_search.reranking.documents import build_rerank_documents
    from ecommerce_search.reranking.provider import score_documents
    from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS
    from evals.contracts.catalog import DEFAULT_CATALOG
    from evals.runners import pool_sources

    pool_sources.install_network_guard()
    pool_sources.verify_model_pins()
    models_dir = repository_models_dir()
    if models_dir is None:
        raise WeakLabelError("no local models directory is known (not a source checkout)")
    catalog = load_catalog(DEFAULT_CATALOG)
    if catalog.checksum_sha256 != facts.catalog.checksum_sha256:
        raise WeakLabelError("the catalog changed since the facts were verified")
    records = {line.record.product_id: line.record for line in catalog.lines}
    reranker = CrossEncoderReranker(RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID], models_dir)
    reranker.load()
    queries = {q.query_id: q.text for q in facts.queries}
    by_query: dict[str, list[str]] = {}
    for item in facts.pool:
        by_query.setdefault(item.query_id, []).append(item.product_id)
    logits: dict[Pair, float] = {}
    for query_id in sorted(by_query):
        product_ids = sorted(by_query[query_id])
        documents = build_rerank_documents(product_ids, records)
        scores = score_documents(reranker, queries[query_id], [d.text for d in documents])
        for product_id, score in zip(product_ids, scores, strict=True):
            logits[(query_id, product_id)] = score
    return logits
