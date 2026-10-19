# Backend portfolio demo

A local walkthrough of the search backend in Swagger UI, plus a short portfolio summary. The
example responses in [`demo/`](demo/) were recorded once from the local stack on 2026-10-19.

Ground rules for presenting it:

- **Catalog.** The catalog is **240 synthetic products** (80 laptops, 60 phones, 50 shoes,
  50 headphones). It is not real marketplace data.
- **Timings.** The timings shown come from single requests. They are not a benchmark.
- **Reranking.** Reranking changes the order of results. An improvement in quality is **not**
  established.
- **Jev.** Jev is **not** integrated into default search. The provider exists (M11) but is off by
  default and no endpoint uses it.
- **M12.** The M12 diagnostic batch is complete, but **the M12 milestone is not**.

## Portfolio summary

**Problem.** Marketplace search has to cope with three kinds of query:

- exact specs (`laptop 8gb 256 ssd`);
- vague intent (`noise cancelling headphones`);
- Hinglish or misspelled text.

It must not silently drop a buyer's hard constraints. Each part should be measurable and
explainable, and the core must run without any remote model.

**Architecture.**

- FastAPI service over PostgreSQL 16 with pgvector. The schema is managed by Alembic, and
  settings are typed with Pydantic.
- **V0 lexical:** PostgreSQL full-text search (`/search`).
- **Dense:** local `sentence-transformers/all-MiniLM-L6-v2` embeddings (pinned revision,
  384 dimensions, cosine) stored in pgvector (`/search/dense`).
- **V1 hybrid:** lexical and dense results fused with Reciprocal Rank Fusion
  (`rrf_k` = 100, provisional) (`/search/hybrid`).
- **V2 filtered:** a deterministic query parser (`qu-1`) feeds an explicit filter policy
  (`fp-1`).
  - Filters run in SQL before retrieval limits.
  - Every response reports `applied_filters` and `ignored_constraints`.
  - Nothing is silently relaxed. (`/search/filtered`)
- **V3 reranked:** the V2 candidates, with the top 20 rescored by a local cross-encoder
  (`cross-encoder/ms-marco-MiniLM-L6-v2`, pinned revision) (`/search/reranked`).
- Model snapshots are fetched once into a git-ignored `models/` directory. Serving and
  embedding then run offline.

**Implemented features.**

- **Data:** validated, idempotent catalog ingestion with provenance and quality checks.
- **Freshness:** explicit `reindex` and `embed` commands, and freshness/status checks for the
  search documents and the embeddings.
- **Responses:** per-stage latency breakdowns in every response, and fixed `503` error
  responses with no internal details.
- **Evaluation:** a human-reviewed Golden Dataset workflow (M9) and a V0–V3 evaluation (M10).
- **Jev provider (M11):** a typed, isolated decision provider. It is off by default.
- **M12 diagnostic tooling:** offline tooling with blind labelling, sealing and a dry-run mode.

**Evaluation evidence (development only).** The source is
[`m10-human-pilot-dev-evaluation.md`](m10-human-pilot-dev-evaluation.md): 20 human-reviewed
pilot/dev queries, 568 frozen labels, the synthetic catalog, and no held-out set.

- **V2 filtering vs V1 hybrid:** strict Precision@10 0.655 vs 0.565, and hard-constraint
  satisfaction 0.872 vs 0.626.
- **V3 reranking (depth 20) did not improve on V2:** NDCG@10 0.866 vs 0.875. No reranker is
  approved for development scope.
- **V0 lexical** returned nothing for 10 of the 20 queries, including every Hinglish and
  misspelled query.
- **M12 diagnostic `live-001`:** one run over 40 AI-authored development queries with one
  annotator. It shows a promising category-coverage signal, but no gate or retrieval improvement
  is established. Details are in [`m12-diagnostic-tooling.md`](m12-diagnostic-tooling.md).

**Limitations.**

- The catalog is synthetic.
- The evaluation queries are development/pilot data only, with no held-out test set and no
  significance testing.
- Relevance was labelled by one primary annotator.
- `rrf_k` and the embedding model are provisional.
- Lexical search is strict AND, so Hinglish and price phrases often leave only dense candidates.
- There is no frontend (that is M25) and no cloud deployment (M26).

## Start the stack

Prerequisites:

- Docker Desktop;
- the project's `.venv` (`uv sync`);
- a local `.env` copied from `.env.example`, with a local-only `POSTGRES_PASSWORD`;
- both model snapshots in `models/`. If they are missing, see "Dense search" and "Reranked
  search" in the README. Fetching them is the only step that needs the network.

Run the commands from the repository root. The examples use Git Bash. In PowerShell, set the
variables with `$env:NAME = "value"` instead of the inline `NAME=value` prefix.

```bash
# 1. Database only (the API runs on the host)
docker compose up -d db

# 2. One-time setup on an empty database. These commands WRITE to the named database.
uv run alembic upgrade head
uv run python -m ecommerce_search.catalog ingest --database ecommerce_search
HF_HUB_OFFLINE=1 uv run python -m ecommerce_search.search embed --database ecommerce_search

# 3. Read-only checks: both should report state=CURRENT
uv run python -m ecommerce_search.search status --database ecommerce_search
uv run python -m ecommerce_search.search embed-status --database ecommerce_search --require-current

# 4. API
HF_HUB_OFFLINE=1 uv run uvicorn ecommerce_search.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

- **Health check:** open <http://127.0.0.1:8000/health>. Expect `"status": "ok"` and pgvector
  `0.8.6`.
- **Containerised API:** `docker compose up -d --build` also works, but it builds the API image
  first. Data setup still runs from the host either way.
- **Stopping:** stop uvicorn with Ctrl+C, then run `docker compose stop db`. Do not add `-v`
  unless you mean to delete the demo database.

## Warm up before presenting

Models load lazily on the first request that needs them:

- on the recorded run, the first dense call spent about 9.9 s loading the embedding model;
- the first reranked call spent about 0.6 s loading the cross-encoder.

Send one throwaway request to each before presenting:

```bash
curl -s -o /dev/null "http://127.0.0.1:8000/search/dense?q=warm+up&top_k=1"
curl -s -o /dev/null "http://127.0.0.1:8000/search/reranked?q=warm+up&top_k=1"
```

## Five-minute Swagger walkthrough

Open <http://127.0.0.1:8000/docs>. For each step, expand the `POST` endpoint, click
**Try it out**, paste the body and click **Execute**. Recorded responses are in `demo/`.

**1. Lexical baseline: `POST /search`** (about 1 minute)

```json
{"query": "hp laptop", "top_k": 5}
```

- Point out `search_version: v0_lexical` and the generated `tsquery` (`'hp' & 'laptop'`).
- All 5 results are HP laptops.
- Lexical search is exact and fast, about 10 ms on the server for this one request.
- It is also strict: every term must match. Recorded response:
  [`demo/01-search-lexical.json`](demo/01-search-lexical.json).

**2. Semantic retrieval: `POST /search/dense`** (about 1 minute)

```json
{"query": "noise cancelling headphones", "top_k": 5}
```

- The results are over-ear headphones with active noise cancellation, found by embedding
  similarity (`dense_score`) rather than exact terms.
- Show the `latency_ms` breakdown: `model_load_ms` is `null` once the model is warm.
- Recorded response (taken cold, so it includes `model_load_ms`):
  [`demo/02-search-dense.json`](demo/02-search-dense.json).

**3. Structured filters: `POST /search/filtered`** (about 1.5 minutes)

```json
{"query": "laptop 8gb 256 ssd", "top_k": 5}
```

- **`query_understanding`:** the deterministic parser (`qu-1`) extracted `category=laptop`,
  `ram_gb=8`, `storage_gb=256` and `storage_type=SSD`, with matched spans.
- **`applied_filters`:** all four were applied under `filter_policy: fp-1`, and
  `ignored_constraints` is empty. Every result is an 8 GB / 256 GB SSD laptop.
- **`lexical_hit_count` is 0:**
  - strict-AND full-text search found nothing for this phrasing;
  - all 15 candidates came from dense retrieval inside the filters;
  - this is a known limitation, shown honestly rather than hidden.
- About 35 ms on the server for this one request. Recorded response:
  [`demo/04-search-filtered.json`](demo/04-search-filtered.json).

**4. Reranking: `POST /search/reranked`, same query** (about 1.5 minutes)

```json
{"query": "laptop 8gb 256 ssd", "top_k": 5}
```

- **Same filters and the same 15 candidates.** The `reranker` block shows `status: applied`,
  the pinned model, `rerank_depth: 20` and `reranked_count: 15`.
- **The order changed.** In the recorded runs, only `SYN-LAP-0037` appears in both top-5
  lists: it is rank 2 in the filtered results and rank 5 in the reranked results. Every result
  in both lists still satisfies all four filters.
- **Say plainly that a different order is not a better order.** On the human-reviewed M10
  pilot/dev set, reranking at depth 20 did not improve on V2.
- **Cost:** this one request took about 1,011 ms on the server. That includes about 609 ms to
  load the reranker for the first time and about 355 ms of reranking. It is a single
  observation, not a benchmark. The M10 benchmark covers warm latency.
- Recorded response: [`demo/05-search-reranked.json`](demo/05-search-reranked.json).

**Optional: hybrid fusion, `POST /search/hybrid`**

```json
{"query": "apple phone", "top_k": 5}
```

- Shows RRF fusion metadata (`fusion`, `lexical_hit_count`, `dense_hit_count`, `overlap_count`).
- Recorded response: [`demo/03-search-hybrid.json`](demo/03-search-hybrid.json).

**Wrap-up** (about 30 seconds): point to the evaluation evidence and limitations in the
portfolio summary above.

## About the example files

- **Capture:** each file in `demo/` is a single recorded `POST` response with `top_k=5`, taken
  on 2026-10-19 from the local stack.
- **Trimming:** each file has an `_example` header. Results are trimmed to id, title, brand,
  category, price and scores.
- **Contents:** no credentials, hostnames, usernames or file paths. The product data is
  synthetic.
- **Not test fixtures or evidence:** the files are illustrations. They are not test fixtures
  or evaluation evidence.
