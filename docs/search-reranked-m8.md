# Milestone 8: cross-encoder reranking (V3)

- Status: Milestone 8 implemented. `GET`/`POST /search/reranked`, `search_version =
  "v3_reranked"`. V3 = the frozen V2 candidate generation (M6 `qu-1` query understanding, filter
  policy `fp-1`, filtered lexical and dense retrieval, unchanged RRF with `rrf_k = 100`,
  `rrf_k_status = "provisional"`) followed by head-only reranking with a local cross-encoder.
- Model: `cross-encoder/ms-marco-MiniLM-L6-v2` at revision
  `233902d25c440f23af6f7d6e94d2946bac0bee0a` (declared licence `apache-2.0`), a **provisional
  compatibility pin**, not a quality selection
  ([ADR-007](decisions/ADR-007-reranker-model-selection.md)). Reranker document `rd-1` =
  embedding text version 1.
- Related: `docs/spec.md` FR-RET-5, FR-API-2, §10 and §14 (Q3), `docs/architecture.md` §3.1,
  `docs/search-filtered-m7.md` (the unchanged V2 candidate generation), ADR-002, ADR-006, ADR-007.
- **No relevance or quality claim is made, and no configuration is selected.** The catalog is
  the **synthetic 240-product** seed (80 laptops, 60 phones, 50 shoes, 50 headphones). The tests,
  the demo and the benchmark below show contract conformance, failure behaviour, determinism,
  latency and neutral rank-movement facts. They do not show that reranking returns better
  products than V2, nor that any depth is preferable. The master plan asks Milestone 8 to compare
  no reranker, depth 10 and depth 20 on quality **and** latency; only latency could be measured,
  because no human-reviewed labels exist. The quality comparison and the reranker re-decision
  belong to Milestone 10 on the Golden Dataset (Milestone 9).

## 1. Scope and relationship to earlier versions

| Endpoint | Version | M8 change |
|---|---|---|
| `GET /health` | — | none |
| `GET`/`POST /search` | V0 `v0_lexical` | none |
| `GET`/`POST /search/dense` | `dense_only` (component) | none |
| `GET`/`POST /search/hybrid` | V1 `v1_hybrid` | none |
| `GET`/`POST /search/filtered` | V2 `v2_filtered` | none |
| `GET`/`POST /search/reranked` | V3 `v3_reranked` | **new, additive** |

* The OpenAPI operations of the five older routes and every component they reference are
  byte-identical to the Milestone 7 tree (canonical-JSON SHA-256
  `3f776a588dbfab595369e18d9b8a50bca6ac8d3184054e7cbf8b05acd9d68934` for both `21dfe09` and the
  M8 head; also pinned by `test_frozen_operations_and_components_are_byte_identical_to_head`).
* V3 changes nothing before fusion: query normalization, `qu-1`, `fp-1`, the filtered SQL, the
  snapshot transaction, `fuse_rrf`, `lexical_k = dense_k = candidate_k = 50` and `rrf_k = 100`
  (provisional, ADR-002) are V2's. The candidate list, its counts and every source and RRF rank
  and score equal `/search/filtered` for the same query.
* **No migration, index or persistence.** Reranking stores nothing (no table, column or cached
  score); the migration head is still `0004`. No dependency, `uv.lock` or Dockerfile change
  (`pyproject.toml` changed only the `real_model` pytest marker description).

## 2. Request flow (shared GET/POST path, `src/ecommerce_search/api/reranked.py`)

```text
normalize the query and validate top_k (1..SEARCH_CANDIDATE_K, default 10)   -> 422 on failure
 -> deterministic parse + DecisionProvider + validation (query_understanding_ms)
    any failure: fixed 503, reason query_understanding_failed
 -> derive_filters(decision) under fp-1, pure (filter_translation_ms)
    any failure: fixed 503, reason filter_policy_failed
 -> no letter or digit: 200, no results, no model, no SQL (status skipped_no_searchable_text)
 -> load the embedding model if needed, token check, encode the query (no DB connection held)
 -> one REPEATABLE READ READ ONLY transaction:
      SNAPSHOT_SQL, filtered lexical SQL (LIMIT 50), filtered dense SQL (LIMIT 50)
      enabled and the source union is non-empty: the catalog records of the WHOLE source union
      (products + the spec rows of each product's own category, constant SQL, bound id array)
 -> transaction commits (nothing is written); the connection is released
 -> enabled: rebuild and validate the records (CatalogRecord)   -> 503 document_build_failed
 -> unchanged RRF over the two filtered lists, keep candidate_k = 50
 -> disabled, or no candidates: return the RRF order (status disabled / skipped_no_candidates)
 -> enabled with candidates:
      reranker spec equals settings?                            -> 503 reranker config_mismatch
      load once (offline manifest + strict profile check)        -> 503 reranker <reason>
      build rd-1 documents for the head = first min(depth, candidate_count) candidates
      count every (query, document) pair with the model tokenizer, no truncation; > 512 tokens
        -> 503 reranker input_too_long
      score the head once (raw logits) and reorder it; the tail keeps its RRF order
 -> return the first top_k (status applied)
```

* **Transaction boundary.** `read_reranked_sources` (`src/ecommerce_search/search/reranked.py`)
  runs exactly the V2 reads and, when enabled, the candidate-record reads in one snapshot, and
  refuses a session that already has an open transaction. It commits on success, rolls back on
  error and releases the connection either way. Fusion, record validation, `rd-1` building and
  all reranker work run after the transaction has ended: **no database connection is checked out
  while either model loads, counts tokens or scores** (asserted by the integration tests, the
  real-model test, the benchmark probe and the demo in §8.2).
* **Disabled is exactly V2.** With `RERANKER_ENABLED=false` the route calls the same
  `read_filtered_sources` as `/search/filtered`, reads no catalog record and never touches the
  reranker; the response is the V2 body plus the V3-only fields (§5).

## 3. Reranking semantics (`src/ecommerce_search/reranking/`)

* **Head only.** With depth `d` (`SEARCH_RERANK_DEPTH`, default 20) and `n` fused candidates,
  the first `min(d, n)` candidates in RRF order are scored, once, in one provider call.
* **Ordering** (`rerank_head`, pure and deterministic): `rerank_score` descending, then the
  original `rrf_rank` ascending (equal scores keep RRF order), then `product_id` (unreachable
  guard). The output is always a permutation of the fused list.
* **Unscored tail.** Candidates beyond the depth follow the head unchanged, in RRF order, with
  `rerank_rank = null` and `rerank_score = null`. A `top_k` above the depth therefore exposes
  an unscored tail; with the defaults (`top_k = 10`, depth 20) it does not.
* **Three ranks.** `rank` is the 1-based position in the response; `rrf_rank` the position in the
  fused list; `rerank_rank` the position within the scored head (on the head `rank =
  rerank_rank`, on the tail `rank = rrf_rank`).
* **Scores are raw logits** (`score_type = "logit"`, `torch.nn.Identity` activation, never a
  sigmoid): uncalibrated, **not probabilities**, comparable only within one query and one model
  revision. There is no threshold: a low score never removes or hides a product.
* **`rd-1`** (`documents.py`) is, by definition, embedding text version 1 of the product
  (`build_embedding_text`), byte-identical: title; brand and category; category-specific
  specifications; description. It never contains raw seller lines, provenance, prices, ratings,
  seller ids or evaluation labels. If `EMBEDDING_TEXT_VERSION` changes, building `rd-1` refuses
  to run until a new reranker document version is reviewed.
* **No truncation.** Pairs are counted with the model's own tokenizer (special tokens included,
  `truncation=False`); a pair over 512 tokens is refused (`input_too_long`) before inference.
* **Unchanged upstream.** `qu-1` and `fp-1` are untouched: reranking never adds, removes or
  relaxes a filter, and the query text reaching both sources and the reranker is the same
  normalized string.

## 4. Model, offline workflow and lifecycle

| Item | Value |
|---|---|
| Model | `cross-encoder/ms-marco-MiniLM-L6-v2` (`MS_MARCO_MINILM_L6_V2`, only entry of `RERANKER_MODELS`) |
| Revision | `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| Declared licence | `apache-2.0` (model card front matter at that revision; not legal advice) |
| Output | one label, raw logit; maximum pair length 512 tokens; CPU |
| Configuration hash | `b30e3ddda48b373d0d929ee16aeec5a2282680b927e47340c5c58c7b299bdef2` |
| Snapshot | seven files, 91,819,431 bytes, every SHA-256 pinned in code (`RERANKER_PINNED_SHA256`) |

Both local snapshots are required for an enabled V3 with candidates: the embedding snapshot
(ADR-006) and the reranker snapshot (ADR-007). Neither is committed or baked into an image; both
live under the git-ignored `models/` directory.

```bash
# network, once per snapshot (the only commands that contact the network)
uv run python -m ecommerce_search.search model-fetch --model-id sentence-transformers/all-MiniLM-L6-v2 --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
uv run python -m ecommerce_search.search model-fetch --kind reranker --model-id cross-encoder/ms-marco-MiniLM-L6-v2 --revision 233902d25c440f23af6f7d6e94d2946bac0bee0a
# offline re-checks
uv run python -m ecommerce_search.search model-verify --kind embedding
uv run python -m ecommerce_search.search model-verify --kind reranker
```

* `model-fetch --kind reranker` accepts only the registry entry at its pinned revision and
  refuses anything else before any network access. `--kind embedding` is the default and its
  behaviour is the Milestone 4 behaviour.
* `model-verify --kind reranker` is offline: manifest, strict profile (exact seven-file set,
  pinned SHA-256, no link leaving the model folder) and the snapshot configuration (reviewed
  architecture, one label, 512-token tokenizer and position limits, no remote code).
* **Lifecycle.** `create_app` builds one `CrossEncoderReranker` per app, unloaded: construction
  reads no file and imports no torch, sentence-transformers or transformers. The first request
  that has candidates to score calls `load()`, which re-verifies the snapshot offline, forces hub
  offline mode and loads with `local_files_only=True`, `trust_remote_code=False`, on CPU, in
  evaluation mode. A load lock loads at most once; an inference lock serializes token counting
  and scoring. A failed load is not cached, so a later request retries after `model-fetch`.
  Request-time search never uses the network.

## 5. API contract (`RerankedSearchResponse`)

```bash
curl "http://127.0.0.1:8000/search/reranked?q=noise+cancelling+headphones&top_k=5"
curl -X POST http://127.0.0.1:8000/search/reranked -H "Content-Type: application/json" -d '{"query": "laptop 8gb 256 ssd", "top_k": 5}'
```

The request is exactly V2's: GET `q`, `top_k`; POST `RerankedSearchRequest` with `query` and
`top_k` only (`extra="forbid"`). `top_k` is 1..`SEARCH_CANDIDATE_K`, default 10.

Fields shared with V2: `query`, `document_version`, `tsquery`, `embedding_model_id`,
`embedding_model_revision`, `embedding_dimension`, `embedding_text_version`, `distance_metric`,
`fusion`, `dense_status`, `lexical_hit_count`, `dense_hit_count`, `overlap_count`, `fused_count`,
`candidate_count`, `top_k`, `result_count`, `results`, `filter_policy`, `applied_filters`,
`ignored_constraints`, `query_understanding` (`usage: "filter_source"`).

V3-specific fields:

* `search_version`: `"v3_reranked"`.
* `reranker`: `enabled`, `status`, `model_id`, `model_revision`, `document_version` (`"rd-1"`),
  `max_length` (512), `rerank_depth`, `reranked_count` (`min(rerank_depth, candidate_count)`
  when applied, else 0) and `score_type` (`"logit"`).
* Per result (`RerankedSearchResult`, the V2 result plus): `rrf_rank`, `rerank_rank`,
  `rerank_score`.
* `latency_ms` (`RerankedSearchLatency`): V2's `lexical_ms`, `model_load_ms`,
  `query_embedding_ms`, `vector_ms`, `rrf_ms`, `query_understanding_ms`, `filter_translation_ms`
  and `total_ms`, plus `document_read_ms` (record SQL inside the snapshot plus rebuild and
  validation after it), `reranker_load_ms` (only on the request that loaded the reranker),
  `rerank_document_ms` and `reranking_ms` (pair-length check, scoring and reordering). A stage
  that did not run is `null`.

**`reranker.status`.**

| Status | When | Results |
|---|---|---|
| `applied` | enabled, searchable text, at least one candidate | head reranked, tail in RRF order |
| `disabled` | `RERANKER_ENABLED=false` and searchable text (also with zero candidates) | V2 order; `rerank_*` null, `rrf_rank = rank` |
| `skipped_no_candidates` | enabled, no product satisfies the filters | `[]`; the reranker is not loaded |
| `skipped_no_searchable_text` | the query has no letter or digit (enabled or disabled) | `[]`; no model, no SQL |

**No silent fallback.** When the enabled reranker cannot load or score, the response is the fixed
503; the V2 order is never returned instead and nothing is retried. Disabled is a configured
mode, not a fallback. Because the reranker is touched only when there are candidates, an enabled
V3 with a missing reranker snapshot still answers `200` for zero-candidate and
punctuation-only queries.

**Status codes.**

| Status | When |
|---|---|
| `422` | blank, over-long or unsafe query; `top_k` outside 1..`SEARCH_CANDIDATE_K`; unknown POST field; query over the embedding model's token limit (standard validation body, never echoing the input) |
| `503` `{"detail": "search unavailable"}` | query understanding (`query_understanding_failed`) or filter translation (`filter_policy_failed`) failed; embedding model unavailable (`snapshot_missing`, `load_failed`, `dimension_mismatch`, `config_mismatch`, `invalid_output`, `encode_failed`); missing table (`table_missing`) or any other SQLAlchemy error (`database_error`); a candidate record missing, duplicated or no longer valid (`document_build_failed`); reranker unavailable (`reranker snapshot_missing`, `load_failed`, `config_mismatch`, `input_too_long`, `invalid_output`, `score_failed`) |
| `500` | any other, unexpected exception (for example a programming error in a provider, the record reader or the ranking code, or a malformed source list rejected by `fuse_rrf`): the framework's generic `Internal Server Error`, not disguised as 503; the transaction is rolled back or already ended and the connection released |

The server log records one line, `reranked search unavailable: <reason>` or `reranked search
unavailable: reranker <reason>`, plus a fixed operator hint for a missing snapshot or table
(for the reranker: run `model-fetch --kind reranker`). It never contains query or document
text, SQL, paths, credentials, library exception text or a traceback. The reranker
`config_mismatch` before loading fires only when an injected reranker's spec differs from the
settings; `create_app` always builds it from the settings.

## 6. Settings and Compose

| Setting | Default | Validation |
|---|---|---|
| `RERANKER_ENABLED` | `true` | only a boolean or the words `true`/`false` (any case); no `1`/`0`, `yes`/`no` |
| `RERANKER_MODEL_ID` | `cross-encoder/ms-marco-MiniLM-L6-v2` | must be a registry entry (exact string) |
| `RERANKER_MODEL_REVISION` | `233902d25c440f23af6f7d6e94d2946bac0bee0a` | must equal that entry's pin |
| `RERANKER_BATCH_SIZE` | `16` | integer 1–256 (no bool, float or decimal coercion) |
| `SEARCH_RERANK_DEPTH` | `20` | integer 1–1000 and at most `SEARCH_CANDIDATE_K` |

* The depth bound is validated **even when reranking is disabled**, so every accepted
  configuration is valid as a whole. A deployment that sets `SEARCH_CANDIDATE_K` below 20 must
  also lower `SEARCH_RERANK_DEPTH`, or settings validation fails for every endpoint.
* The reranker snapshot is read from the same models directory as the embedding model
  (`EMBEDDING_MODELS_DIR`, default the repository `models/`).
* `docker-compose.yml` forwards the five settings, with these defaults, to the `api` service only;
  the reranker uses the existing read-only `./models:/models:ro` mount. `.env.example` documents
  them. Without a fetched reranker snapshot an enabled `/search/reranked` returns the fixed 503
  whenever it has candidates to rerank, and `/health` and the V0–V2 routes keep working.

## 7. Latency benchmark (Session G, latency only)

**Scope.** Latency, cold model loads, determinism, process memory and neutral rank-movement
facts of three configurations of `/search/reranked`. **No relevance or quality measure exists
in it, and it selects no configuration.** "Lower measured latency" below is a measurement on one
machine, not a recommendation.

### 7.1 Attempts: one invalid, one valid

1. **First attempt: invalid and discarded.** Started 2026-10-03T16:48:16Z. A user-initiated
   system sleep at 2026-10-03T17:54:55Z interrupted it during run 2. It produced no summary,
   report or exit code. Its temporary run-1 record file and log were hashed for the record
   (Session G), and its scratch database `ecommerce_search_bench_811829f87d6b` was dropped at
   2026-10-04T03:57:48Z. **No figure from it is used anywhere.**
2. **Single approved rerun: valid.** The same committed harness (`5d86f44`), launched through a
   wrapper that held a system keep-awake request, ran 2026-10-04T04:01:26Z–06:36:21Z and exited
   `0`. The harness window is 04:01:29Z–06:36:04Z; run durations were 2,878.34 s, 3,086.688 s
   and 3,304.776 s. AC power was online at start (battery 86 %) and end (100 %), and the
   harness recorded no power event in the window. All three scratch databases were dropped.

Because the experiment id is derived from the harness commit, both attempts would share it.
Session H confirmed that the artifacts hold only rerun data: every one of the 33,669 sample
timestamps lies between 2026-10-04T04:02:26.335Z and 06:35:59.564Z, record sequences are
contiguous per run, and no attempt-1 file remains on disk.

### 7.2 Protocol

* Harness `scripts/benchmark_reranked.py` at `5d86f44b83a657d1c6aa4c8042f9b582acd67a7a`;
  production commit `558bd39e69cc503410beb7efcb648fac169708b2` (every runtime-affecting path
  identical, checked by the harness); clean tree.
* **Queries:** the 17 committed M5 hybrid benchmark queries in committed order (query-set
  SHA-256 `ea1df08db89a8bceb0e5b6437f18e75a30aed997fb70d727d080b441ebbd78d3`).
* **Arms** (in-process `TestClient` `GET /search/reranked`, `top_k = 10`, candidate_k 50,
  `rrf_k = 100`): `disabled` (`RERANKER_ENABLED=false`), `depth10` and `depth20`.
* **Runs:** 3, each a fresh subprocess on a fresh scratch database (migrated to `0004`, the
  frozen 240-product seed, 240 current embeddings from the pinned model). Arm order rotated:
  run 1 disabled, depth10, depth20; run 2 depth10, depth20, disabled; run 3 depth20, disabled,
  depth10. Each arm gets a fresh app (fresh embedder, fresh unloaded reranker).
* **Per arm:** 1 cold request (the first query), then per query 20 untimed warm-ups and 200
  timed requests: 3,741 requests. Totals: 33,669 requests (9 cold, 3,060 warm-up, 30,600
  timed), **all status 200**. No sample was excluded, retried or treated as an outlier.
  Percentiles are nearest-rank over timed samples.
* **Models:** embedding `sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`,
  reranker `cross-encoder/ms-marco-MiniLM-L6-v2@233902d25c440f23af6f7d6e94d2946bac0bee0a`; both
  manifests verified with no problem; offline mode forced and non-loopback sockets refused.
* **Host:** one Windows 11 laptop (`10.0.26300`), Intel64 Family 6 Model 140, 8 logical CPUs,
  Python 3.12.3, torch `2.14.1+cpu` (4 threads), sentence-transformers 6.1.0, transformers 5.18.0,
  tokenizers 0.23.2, FastAPI 0.142.2, psutil 7.2.2; PostgreSQL 16.15 with pgvector 0.8.6 in the
  Compose container (`shared_buffers` 128MB, `work_mem` 4MB, `jit` on).
* **Metric meanings.** `request_ms` is the in-process `TestClient` call, not network latency.
  `serialization_ms` is the harness timing `model_dump_json()` of the parsed response
  separately. Every other field is the API's own `latency_ms`. The torch import and the OS file
  cache are shared within a run (warmed by the embedding setup), so cold loads measure building
  each model from the local snapshot into a fresh provider, not a cold interpreter.

### 7.3 Artifacts and verification

Git-ignored, under `data/processed/rerank_benchmark/`:

| File | Bytes | SHA-256 |
|---|---:|---|
| `rerank-m8-latency-5d86f44b83a6.samples.jsonl` | 108,013,145 | `e76fd924b7d4bffd8cfe1820e42c49e06ce52afa88d85389d69825cc3eda7375` |
| `rerank-m8-latency-5d86f44b83a6.json` | 1,272,097 | `ab19c0521eadb3584f5551136b611ce83d8fd0cbe847fb7baaa6cd8c1175221a` |
| `rerank-m8-latency-5d86f44b83a6.md` | 14,837 | `681fbf763e266fa3b8ad1f0a3a48a3d4780bf3ee22e0f89dd1c6319f193ee22a` |

The raw file holds one header line and 33,690 records (3 run, 9 arm-start, 9 arm-end and
33,669 request records).

* `uv run --offline python scripts/benchmark_reranked.py --verify data/processed/rerank_benchmark/rerank-m8-latency-5d86f44b83a6.json`
  recomputes every figure from the raw records and refuses missing, duplicated, reordered or
  altered records, other settings, other model pins or another query checksum. It printed
  `OK: summary recomputes exactly from raw records` in Session G and again in Session H.
* Session G recomputed the summary independently: 1,093,620 value comparisons, 0 mismatches.
* Session H recomputed it again with separate code (its own nearest-rank and rank-movement
  implementations, results compared directly rather than through the harness hashes): 14,290
  comparisons of whole statistic blocks and facts, 0 mismatches. Every table in this section
  was generated by that code from the raw records and checked against the summary.

### 7.4 Timed latency per run and arm (ms)

`request_ms` (arm position in the run in brackets):

| Run | Arm (position) | n | min | mean | p50 | p95 | p99 | max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | disabled (1) | 3,400 | 20.444 | 30.288 | 29.048 | 40.840 | 47.985 | 297.464 |
| 1 | depth10 (2) | 3,400 | 132.274 | 253.818 | 256.935 | 328.367 | 371.087 | 969.590 |
| 1 | depth20 (3) | 3,400 | 138.735 | 466.706 | 484.260 | 638.622 | 728.500 | 1001.977 |
| 2 | depth10 (1) | 3,400 | 146.945 | 272.888 | 270.400 | 360.529 | 423.839 | 696.992 |
| 2 | depth20 (2) | 3,400 | 147.262 | 494.064 | 511.026 | 668.786 | 816.071 | 1522.583 |
| 2 | disabled (3) | 3,400 | 22.262 | 33.921 | 32.969 | 42.759 | 53.586 | 364.926 |
| 3 | depth20 (1) | 3,400 | 167.009 | 545.250 | 574.370 | 734.368 | 821.953 | 1079.892 |
| 3 | disabled (2) | 3,400 | 23.720 | 40.936 | 40.487 | 50.020 | 64.244 | 158.653 |
| 3 | depth10 (3) | 3,400 | 130.659 | 286.710 | 271.637 | 440.067 | 520.509 | 1302.579 |

Stage timings, p50 / p95 (`null`: the stage never runs in that arm):

| Run | Arm | `total_ms` | `query_embedding_ms` | `lexical_ms` | `vector_ms` | `document_read_ms` | `rrf_ms` | `rerank_document_ms` | `reranking_ms` |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | disabled | 24.631 / 35.006 | 11.932 / 20.795 | 3.201 / 4.723 | 3.274 / 4.842 | null | 0.621 / 1.371 | null | null |
| 1 | depth10 | 251.898 / 323.161 | 11.290 / 19.529 | 3.417 / 5.028 | 3.474 / 5.209 | 11.289 / 18.215 | 0.614 / 1.387 | 0.217 / 0.470 | 214.210 / 282.167 |
| 1 | depth20 | 478.817 / 632.898 | 11.990 / 21.535 | 3.471 / 5.258 | 3.575 / 5.341 | 11.609 / 18.684 | 0.623 / 1.446 | 0.380 / 0.848 | 437.701 / 589.652 |
| 2 | depth10 | 265.240 / 355.787 | 12.614 / 22.131 | 3.215 / 5.172 | 3.588 / 5.439 | 11.840 / 19.238 | 0.652 / 1.483 | 0.232 / 0.487 | 226.200 / 313.735 |
| 2 | depth20 | 505.702 / 662.105 | 13.333 / 23.362 | 3.181 / 5.143 | 3.633 / 5.598 | 11.933 / 20.243 | 0.647 / 1.585 | 0.397 / 0.910 | 464.740 / 615.435 |
| 2 | disabled | 28.072 / 36.951 | 15.296 / 22.356 | 3.005 / 4.574 | 3.347 / 4.921 | null | 0.617 / 1.471 | null | null |
| 3 | depth20 | 568.365 / 728.358 | 16.115 / 24.737 | 3.362 / 5.453 | 3.720 / 5.757 | 12.965 / 20.489 | 0.761 / 1.700 | 0.453 / 0.951 | 523.713 / 677.828 |
| 3 | disabled | 34.560 / 42.951 | 19.896 / 25.348 | 3.403 / 5.550 | 3.940 / 5.923 | null | 0.689 / 1.633 | null | null |
| 3 | depth10 | 266.231 / 432.690 | 12.826 / 23.378 | 3.306 / 5.306 | 3.697 / 5.773 | 11.979 / 22.072 | 0.657 / 1.695 | 0.243 / 0.566 | 227.569 / 377.006 |

Per-run figures are primary. Pooled over the three runs (secondary):

| Arm | metric | n | p50 | p95 | p99 | max |
|---|---|---:|---:|---:|---:|---:|
| disabled | `request_ms` | 10,200 | 33.813 | 46.515 | 56.561 | 364.926 |
| disabled | `total_ms` | 10,200 | 28.766 | 40.156 | 49.001 | 357.916 |
| disabled | `reranking_ms` | 0 | null | null | null | null |
| depth10 | `request_ms` | 10,200 | 265.867 | 384.440 | 470.506 | 1302.579 |
| depth10 | `total_ms` | 10,200 | 260.727 | 378.669 | 464.035 | 1292.703 |
| depth10 | `reranking_ms` | 10,200 | 222.261 | 330.107 | 401.611 | 1014.128 |
| depth20 | `request_ms` | 10,200 | 523.830 | 696.661 | 803.672 | 1522.583 |
| depth20 | `total_ms` | 10,200 | 518.508 | 690.369 | 796.732 | 1514.327 |
| depth20 | `reranking_ms` | 10,200 | 476.599 | 639.931 | 739.161 | 1448.620 |

Observations (measurements only): in every run, by `request_ms` min, mean, p50, p95 and p99,
`disabled` has the lowest measured latency and `depth10` lower measured latency than `depth20`
(the maximum does not follow this order in run 3); by p50, `reranking_ms` is the largest stage
of both enabled arms; `document_read_ms` (reading and validating the records of the whole source union)
is present in both enabled arms and absent when disabled. `depth20` scored 10, 12, 15 or 20
candidates per query (some queries have fewer than 20 candidates); `depth10` always scored 10.

### 7.5 Cold requests and memory

Cold request of each arm (ms):

| Run | Arm | Position | `request_ms` | `total_ms` | `model_load_ms` | `reranker_load_ms` |
|---:|---|---:|---:|---:|---:|---:|
| 1 | disabled | 1 | 488.446 | 313.181 | 208.611 | null |
| 1 | depth10 | 2 | 1181.715 | 1150.213 | 248.327 | 504.413 |
| 1 | depth20 | 3 | 1353.449 | 1328.939 | 189.928 | 543.401 |
| 2 | depth10 | 1 | 1125.727 | 1040.523 | 259.992 | 500.131 |
| 2 | depth20 | 2 | 1525.384 | 1496.449 | 253.658 | 585.528 |
| 2 | disabled | 3 | 337.620 | 317.041 | 256.763 | null |
| 3 | depth20 | 1 | 1416.047 | 1365.243 | 233.885 | 529.501 |
| 3 | disabled | 2 | 394.482 | 367.699 | 299.372 | null |
| 3 | depth10 | 3 | 1421.298 | 1377.664 | 356.745 | 640.143 |

`reranker_load_ms` includes the offline manifest and pinned-hash re-verification of the
snapshot. These are one sample per arm and run, inside a process whose libraries were already
imported.

Process memory (psutil 7.2.2, a locked project dependency; peak is Windows `PeakWorkingSetSize`,
process-lifetime and monotonic, so it includes the setup step and cannot be attributed to one
arm):

| Run | Point | RSS (MiB) | Peak RSS to date (MiB) |
|---:|---|---:|---:|
| 1 | after setup | 642.1 | 673.1 |
| 1 | end of disabled | 630.2 | 741.4 |
| 1 | end of depth10 | 790.3 | 891.3 |
| 1 | end of depth20 | 704.3 | 1027.8 |
| 2 | after setup | 640.1 | 675.6 |
| 2 | end of depth10 | 765.0 | 846.7 |
| 2 | end of depth20 | 685.0 | 1014.0 |
| 2 | end of disabled | 593.1 | 1014.0 |
| 3 | after setup | 602.0 | 672.5 |
| 3 | end of depth20 | 646.7 | 871.3 |
| 3 | end of disabled | 646.4 | 871.3 |
| 3 | end of depth10 | 700.0 | 885.0 |

### 7.6 Determinism and neutral rank facts

* **Determinism.** Within every arm of every run, all requests for a query returned the same
  ranking and bitwise-identical scores (cold, warm-up and timed). Across the three runs (three
  processes, three fresh scratch databases) each arm's rankings and scores were identical for
  all 17 queries. This is one machine and one library set; it is not asserted across machines
  or library versions.
* **Contract facts held on every request:** the candidate facts (counts, fusion block, applied
  and ignored constraints, understanding) and every fused candidate's RRF score and source ranks
  were identical across the three arms; the disabled arm never loaded, counted or scored with the
  reranker; and no database connection was checked out during any reranker call.
* **Rank facts** (top 10 of each query versus another arm, summed over the 17 queries; identical
  in all three runs). These describe positions only. A change of position is neither an
  improvement nor a regression.

| Pair (top 10, 17 queries) | Overlap sum | Same position | Changed positions | Identical order |
|---|---:|---:|---:|---:|
| disabled vs depth10 | 170 / 170 | 28 | 142 | 0 / 17 |
| disabled vs depth20 | 110 / 170 | 12 | 158 | 0 / 17 |
| depth10 vs depth20 | 110 / 170 | 41 | 129 | 2 / 17 |

With `top_k = 10`, `depth10` reorders exactly the ten V2 results, so its set always equals
`disabled`'s. `depth20` scores RRF positions 1–20, and in each run 60 of its 170 top-10 places
were taken by candidates from RRF positions 11–20.

### 7.7 Run-to-run drift and host effects

Latency drifted upward from run to run, including in stages the reranker does not touch. The
`disabled` arm's `request_ms` p50 was 29.048, 32.969 and 40.487 ms in runs 1–3 and its
`query_embedding_ms` p50 11.932, 15.296 and 19.896 ms; `depth20`'s `reranking_ms` p50 was
437.701, 464.740 and 523.713 ms. Arm order was rotated, so the drift follows the run, not the arm
position. Isolated spikes also occur in stages unrelated to reranking (for example a
`vector_ms` maximum of 335.495 ms and a `rrf_ms` maximum of 261.075 ms in `disabled` arms). The
recorded data do not explain these effects: CPU temperature and frequency, background processes
and OS scheduling were not measured. The largest gap between consecutive sample timestamps was
1.354 s, 1.526 s and 1.422 s in runs 1–3. No sample was excluded. The absolute values are
specific to this laptop and must not be read as a latency budget or as production latency.

### 7.8 What the benchmark does not show

* No relevance, quality, Constraint Satisfaction Rate or zero-result-rate figure: none exists
  before the human-reviewed Golden Dataset.
* No winner, recommended depth or optimal configuration. The latency differences say what each
  configuration costs on this machine, not which one to use.
* Not network or production latency: in-process `TestClient`, one laptop, a synthetic
  240-product catalog, 17 queries.
* **Milestone 10 must make the quality and configuration decision** (no reranker, depth 10,
  depth 20, or another model) on the Golden Dataset, using latency figures like these only as
  cost information.

## 8. Evidence

### 8.1 Commits and tests

Commits on `main`:

* `08fffab09333271f9fe648992e82f2114c17728d` `feat(m8): add reranker contract, documents and deterministic ranking`
* `957f7edb0cf06b5651c32d74f2ff04d71f5a90f4` `feat(m8): pin provisional cross-encoder reranker and offline provider`
* `558bd39e69cc503410beb7efcb648fac169708b2` `feat(m8): expose V3 reranked search`
* `5d86f44b83a657d1c6aa4c8042f9b582acd67a7a` `test(m8): add reranker latency benchmark`

Tests (developer-written; no relevance is asserted anywhere):

* Pure contract: `tests/unit/search/test_reranker_spec.py`, `test_reranker_provider.py`,
  `test_rerank_documents.py`, `test_rerank_ranking.py`, `test_reranking_isolation.py`.
* Provider and snapshot tooling: `test_cross_encoder_provider.py`,
  `test_model_fetch_profiles.py`.
* API and retrieval: `tests/unit/search/test_reranked_api.py`, `test_reranked_sources.py`,
  `tests/integration/test_reranked_retrieval.py`, `test_reranked_api_integration.py`
  (independent oracles, disabled = V2, zero matches, unscored tail, determinism, 422/503/500
  boundaries, rollback and release, the union-record failure, no connection during reranker
  work).
* Settings and Compose: `tests/unit/test_settings.py`, `tests/unit/test_compose_config.py`.
* Real model, offline: `tests/integration/test_real_model_reranker.py` (pinned snapshot loads
  offline, raw logits, determinism within a process, every seed `rd-1` pair within 512 tokens,
  corrupt or missing copies refused, the V3 API on a scratch database).
* Benchmark harness: `tests/unit/search/test_benchmark_reranked.py`.

Validation **reused** from earlier sessions (not re-run in Session H, which changed no
executable line): Session F on the production tree `558bd39`: 2,879 unit tests
(`not integration and not real_model`), 717 integration tests (`integration and not
real_model`) and 18 `real_model` tests passed offline. Session G on `5d86f44`: 2,945 unit tests
passed and the committed `--verify` succeeded.

### 8.2 Scratch-database demo (Session H)

A unique scratch database (`ecommerce_search_demo_<12 hex>`, never the development database)
was migrated to `0004`, seeded with the 240 products and given 240 current embeddings from the
pinned embedding model. Both canonical snapshots were used offline (hub offline variables set,
non-loopback sockets refused; none was attempted). 204 checks passed, 0 failed, and the database
was dropped. This is contract and determinism evidence, **not relevance**.

| Case | Query | Status | Candidates | Lexical / dense | Reranked | `applied_filters` | `ignored_constraints` |
|---|---|---|---:|---|---:|---|---|
| English | `noise cancelling headphones` | `applied` | 50 | 0 / 50 | 20 | category `headphones` | — |
| Hinglish | `coding ke liye laptop` | `applied` | 50 | 0 / 50 | 20 | category `laptop` | `semantic_intent` informational_only |
| Numeric filter | `laptop 8gb 256 ssd` | `applied` | 15 | 0 / 15 | 15 | category `laptop`, ram `8`, storage `256`, type `SSD` | — |
| Combined filter | `hp laptop 8gb ram 256gb ssd under 40k` | `applied` | 1 | 0 / 1 | 1 | category, brand `HP`, ram, storage, type, max `40000.00` | — |
| Conflict | `above 30k under 20k` | `applied` | 50 | 0 / 50 | 20 | — | `min_price`, `max_price` conflict |
| Ambiguity | `16gb ram laptop ₹40,000` | `applied` | 50 | 0 / 50 | 20 | category `laptop` | `ram_gb` ambiguous_family |
| Informational only | `sasta phone` | `applied` | 50 | 0 / 50 | 20 | category `phone` | `price_preference` informational_only |
| Impossible | `nike laptop` | `skipped_no_candidates` | 0 | 0 / 0 | 0 | category `laptop`, brand `Nike` | — |
| Punctuation | `!!!` | `skipped_no_searchable_text` | 0 | — | 0 | — | — |

For every query (`top_k = 50`):

* GET and POST bodies were equal apart from `latency_ms`, and a repeated GET was identical.
* Ordering the V3 results by `rrf_rank` reproduced the `/search/filtered` body exactly (same
  products, RRF and source ranks and scores, counts, fusion block, filters, ignored constraints
  and understanding).
* Head scores were finite Python floats in descending order with `rerank_rank` 1..head and
  `rrf_rank` a permutation of 1..head; the tail had null `rerank_*` and consecutive RRF ranks;
  `reranked_count = min(20, candidate_count)`.
* With `RERANKER_ENABLED=false`, the body with the V3-only fields removed (`reranker`, the three
  per-result fields, version and latency) equalled the V2 body; GET/POST matched; no record was
  read and the reranker was never loaded.
* 233 model calls were observed (`load`, `count_pair_tokens` and `score` on the reranker; `load`
  and `embed_query` on the embedder); the pool had 0 connections checked out during every one.

With an empty reranker models directory: GET and POST `/search/reranked` for a query with
candidates returned the fixed 503 (no path in the body; the log held only `reranked search
unavailable: reranker snapshot_missing` plus the fixed `model-fetch --kind reranker` hint);
`nike laptop` and `!!!` still returned 200 (`skipped_no_candidates`,
`skipped_no_searchable_text`); `/health`, `/search`, `/search/dense`, `/search/hybrid` and
`/search/filtered` returned 200; no connection was left checked out.

## 9. Limitations

* **No quality evidence.** The reranker is a compatibility pin trained on English MS MARCO web
  passages, not on e-commerce products, and unevaluated on this catalog; it may reorder worse than
  V2. Domain mismatch (specification-heavy titles, units, brands, Indian price contexts) is
  expected; Hinglish and other non-English queries are out of its scope.
* **Union-record failure.** An enabled V3 reads and validates the catalog records of the whole
  source union (up to `lexical_k + dense_k` products), not only the reranked head. One missing,
  duplicated or invalid record anywhere in that union makes the request a 503
  (`document_build_failed`), while `/search/filtered` still serves the same query. The disabled
  mode reads no record.
* **Latency.** On this laptop an enabled request spent most of its time in CPU cross-encoder
  scoring (§7.4). The first request of a process also pays both model loads. No latency budget is
  set.
* **Logits only.** Scores are uncalibrated logits; they must not be thresholded, shown as
  probabilities or turned into filters or rejections.
* **512-token refusal.** Pairs are refused, not truncated. The longest measured seed `rd-1` pair
  is 338 tokens with a 200-character worst-case query (ADR-007); raising
  `SEARCH_MAX_QUERY_LENGTH` far above its default could produce refused pairs.
* **Inherited V2 limitations.** Strict-AND lexical text (most demo queries got no lexical
  candidates), missing or stale embeddings shortening the dense list, no dense threshold, the M6
  lexicon's coverage, and the provisional `rrf_k = 100`.
* **Determinism scope.** Verified within a process and across three processes on one machine;
  not across machines, CPUs or library versions.
