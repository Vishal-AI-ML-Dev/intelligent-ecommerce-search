# M10 human-reviewed pilot/dev evaluation — V0–V3 on frozen human labels

> **APPROVED BY THE OWNER (2026-10-17). DEVELOPMENT-SCOPED M10 CLOSURE UNDER AN EXPLICIT
> SCOPE AMENDMENT; NOT A CLAIM THAT ALL ORIGINAL M10 REQUIREMENTS WERE MET.** See "M10 scope
> amendment and closure" below for the five named limitations and deferred items.
> - The 20 queries are the **pilot/dev** partition: development / rubric-calibration data on a
>   **synthetic 240-product catalog**. There are **zero held-out test queries**; no held-out
>   performance claim is made or permitted.
> - The ranked lists are **recorded retrieval runs** (the pool provenance, recorded before
>   labelling), scored with the newly frozen human labels. No retrieval was re-run.
> - **Fresh latency was measured for the `/search/reranked` arms only** (disabled, depth 10,
>   depth 20) on a **different, 17-query** benchmark set, in a local, uncommitted snapshot
>   (§9). The "disabled" arm runs the V2 pipeline through `/search/reranked`, not
>   `/search/filtered`. **V0 and V1 fresh latency was not measured.**
> - `rrf_k` and the embedding model are **not re-decided**; the evaluated arms cannot support
>   that decision (see §10).
> - The machine-label provisional analysis (`docs/m10-provisional-evaluation.md`) is separate
>   historical evidence. It is not combined with, or compared against, these results.

## M10 scope amendment and closure (owner, 2026-10-17)

The owner approved this report and closed M10 as **development-scoped (pilot/dev)** through an
explicit scope amendment. The amendment narrows what M10 closure means; it does not claim that
every original requirement in `docs/MASTER_PLAN.md` ("Re-decide the provisional `rrf_k`,
embedding model, and reranker pins", latency for V0–V3) was fulfilled.

Named limitations and deferred items, approved as part of the closure:

1. **`rrf_k` = 100 and the current embedding model remain provisional.** Neither was
   comparatively re-decided; only one value of each was evaluated (§10).
2. **Fresh latency coverage excludes V0, V1 and `/search/filtered`.** Only the three
   `/search/reranked` arms were measured, on a different 17-query set (§9.1).
3. **The latency run is not a clean latency baseline.** It includes connected-standby
   contamination (four stalled requests) and unexplained between-run variation (§9.3–9.4).
4. **Quality scores use recorded actual retrieval runs, not fresh runs** (§2).
5. **Held-out evidence remains deferred.** No held-out or production claims are made (§1).

The no-reranker decision stays **development-scoped**; the configuration is unchanged
(`reranker_enabled` true, depth 20).

**M16 prerequisite.** M16 requires "M10 complete". This development-scoped closure does **not**
by itself satisfy that prerequisite. Whether it does is a separate owner decision, to be made
when M16 is planned; it is not assumed here.

## Executive summary

**What was measured.** Four search versions were scored on 20 development queries using 568
human relevance labels frozen under the `review-2` protocol:

- V0 = lexical full-text search;
- V1 = lexical + dense (embeddings) fused with Reciprocal Rank Fusion;
- V2 = V1 + structured filters from deterministic query understanding;
- V3 = V2 + a cross-encoder reranker (depth 20), plus a secondary arm at depth 10.

**What the numbers show (20 pilot/dev queries, K = 10).**

- **Filtering (V2) gives the best strict relevance and much better constraint compliance than
  V1.**
  - Strict Precision@10: 0.655 vs 0.565.
  - Strict MRR@10: 0.951 vs 0.817.
  - Hard-constraint satisfaction (CSR@10 hard): 0.872 vs 0.626.
- **Hybrid without filters (V1) scores highest on graded and lenient metrics.**
  - NDCG@10: 0.894 vs 0.875.
  - Lenient Precision@10: 0.915 vs 0.825.
  - Those V1 scores come largely from long lists of *borderline* products, many of which break
    a hard constraint of the query.
- **Reranking (V3-d20) did not improve on V2 here.**
  - NDCG@10 is 0.866 vs 0.875: 3 queries up, 2 down, 14 tied.
  - Strict MRR@10 is 0.889 vs 0.951; two queries (a misspelled query and a Hinglish query) lost
    their top-ranked relevant product.
- **Lexical only (V0) returned nothing for 10 of 20 queries.** Those 10 include every Hinglish
  query and every misspelled query.

**What the latency benchmark shows (17 M5 benchmark queries, a different set; §9).** Per-run
warm P50 of `request_ms` (in-process; across the 3 runs):

- reranker disabled: 24.0–26.1 ms;
- depth 10: 192.4–266.3 ms;
- depth 20: 348.8–384.9 ms.

So the reranker added roughly 170–240 ms (depth 10) and 325–360 ms (depth 20) to the warm P50
in each run. All 33,669 requests returned 200. Four requests stalled across connected-standby
episodes (34.5 s to 256.7 s); they are kept in the statistics, as the protocol requires.

**What this does and does not mean.** On this small development set, the evidence favours
keeping structured filtering. It gives no support for the reranker as configured, which also
costs about 8–15 times the warm P50 latency of the disabled arm. **No reranker is approved
for development scope only** (owner, 2026-10-17); the configuration is unchanged (§10). These
are development observations:

- 19–20 queries per average and no significance test;
- labels from one primary annotator, with a second human reviewing only a sample;
- a synthetic catalog;
- no held-out test set.

They are not a production-quality estimate, a held-out result or an official M10 completion.

## 1. Problem and scope

Milestone 10 (`docs/MASTER_PLAN.md`) asks for a comparison of V0–V3 from actual runs on the
human-reviewed internal Golden Dataset, including configuration, quality, latency, failure cases,
limitations and a measured recommendation, and a re-decision of the provisional `rrf_k`,
embedding model and reranker pins (or no reranker). This document covers the **quality** part
on the human-reviewed **pilot/dev** dataset only:

- **Covered:** strict and lenient relevance, graded relevance, constraint satisfaction,
  irrelevant and zero results, and per-query changes for V0, V1, V2, V3-d20 and V3-d10.
- **Latency (§9):** covered for the three `/search/reranked` arms only, measured on a different
  17-query set. Not covered for V0, V1 or `/search/filtered`.
- **Not covered:**
  - held-out evidence (the held-out test dataset is deferred);
  - `rrf_k` and embedding-model re-decisions (§10);
  - any alternative reranker model.

The owner's scope designation (2026-10-17) applies: *"M9 human-reviewed pilot/dev Golden Dataset
complete under review-2, with disclosed limitations. Separately authored held-out test dataset
remains deferred; no held-out performance claims are permitted."*

## 2. Systems and exact bindings

All five arms come from one recorded pool run (provenance `run_id` `045a73ed83ebf400`). Every
arm returned at most 20 products per query (`source_top_k` 20) through the in-process FastAPI
application. Values are copied from `run_record.json`.

| Arm | System id | Endpoint | Configuration |
|---|---|---|---|
| V0 | `v0_lexical` | `/search` | PostgreSQL FTS |
| V1 | `v1_hybrid` | `/search/hybrid` | lexical_k 50, dense_k 50, candidate_k 50, RRF `rrf_k` 100 (`rrf_k_status` provisional) |
| V2 | `v2_filtered` | `/search/filtered` | V1 + parser `qu-1`, lexicon `1`, filter policy `fp-1` |
| V3 | `v3_reranked_d20` | `/search/reranked` | V2 candidates + cross-encoder, rerank depth 20 (configured default) |
| V3-d10 (secondary) | `v3_reranked_d10` | `/search/reranked` | same, rerank depth 10 |

The pooling-only source `dense_only` was not evaluated.

| Binding | Value |
|---|---|
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` @ `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, config `6d5cfc238e11c60053e8227b5206d295ef4f06b3fcfbd8d359c2bbb96efb743b` |
| Reranker model | `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a`, config `b30e3ddda48b373d0d929ee16aeec5a2282680b927e47340c5c58c7b299bdef2` |
| Component versions | document `1`, embedding text `1`, rerank document `rd-1`, parser `qu-1`, lexicon `1`, filter policy `fp-1` |
| Other settings | `search_default_top_k` 10, `search_max_query_length` 200, `embedding_batch_size` 32, `reranker_batch_size` 16, `reranker_enabled` true, `search_rerank_depth` 20 (V3-d10 arm: 10) |
| Recorded library versions | torch `2.14.1+cpu`, sentence-transformers `6.1.0`, transformers `5.18.0`, tokenizers `0.23.2`, fastapi `0.142.2`, sqlalchemy `2.1.1`, psycopg `3.3.6`, pgvector (Python) `0.5.0` |
| Retrieval configuration SHA-256 (canonical run context) | `045a73ed83ebf4002b609cef18adc765940975a0a585555ff1d155410e7c0f0e` |
| Runtime trees at retrieval | `src` `52260b30…`, `migrations` `0efcdb7c…`, `data/seed` `3f3cd824…`, `uv.lock` `cd92faf5…`, `alembic.ini` `e643da5b…` (recorded as equal to HEAD `8c57bdb` at evaluation time) |
| Catalog | `synthetic-seed` version `1`, checksum `2c1c5fa43eec5f7346df40c17e57c07f6594ab1833c5d9d4c862e6d2e8bbbbc8` |

## 3. Dataset and review protocol

| Item | Value |
|---|---|
| Golden | `internal-search` version 1, review protocol `review-2`, rubric `rubric-1` |
| Queries | 20 `pilot_dev`, 0 `test`. By language style: 15 English, 3 Hinglish, 2 noisy |
| Final labels | 568 pooled pairs: 224 relevant, 261 borderline, 83 not relevant |
| How labels were decided | 471 primary (ann-1), 76 adjudication (ann-1), 21 joint consensus (ann-1 + ann-2) |
| Second review | ann-2 re-labelled the 114-pair cs-1 sample blind to ann-1's labels |
| Agreement (cs-1 only, `iaa-1`) | three-label observed 0.868, Cohen's κ 0.803; strict binary 0.982, κ 0.960; lenient binary 0.868, κ 0.698 |
| Optional candidates | 218 optional review candidates were not re-adjudicated; they keep their sealed primary labels |
| Pool | `pool-1`: union of the six sources' top-20 lists; every evaluated list lies inside it |

Two cautions apply to these labels:

- **Agreement is not accuracy.** The agreement figures describe the label-stratified cs-1
  sample only. That sample over-represents rarer labels, so the figures are not population
  estimates and do not prove the labels are correct.
- **Intra-annotator consistency was not measured,** by the `review-2` amendment.

**Disclosed label limitations** (from `docs/golden-dataset-m9.md`):

- ann-1 had seen machine label proposals for this pool, so the primary round was not fully blind.
- ann-1 received AI-assistant explanations of the rubric during primary annotation.
- ann-2's blindness and independence are procedural (a separate reviewer bundle), not
  cryptographic, and cannot be verified technically.
- The pilot has no contradictory-constraint query.
- The catalog is synthetic.

## 4. Metric definitions and how to read the tables

The metrics are `eval-1` (`evals/metrics/retrieval.py`), unchanged:

- **K values:** 5, 10 and 20; the primary K is 10.
- **Strict relevance** (primary) counts only `relevant`. **Lenient** counts `relevant` or
  `borderline`.
- **NDCG** uses graded gains: relevant 2, borderline 1, not relevant 0.

Each cell below is `value (n/excluded)`: the macro mean over the `n` queries where the metric is
defined, and the number of queries excluded as undefined. Undefined values are never averaged as
0. The cases that matter here:

- **Recall and MRR are undefined for queries with no relevant judged pair.** Under the strict
  definition that is gq-p19 (`laptop 64gb ram`) and gq-p20 (`smartwatch`); under the lenient
  definition, gq-p20 only.
- **NDCG is undefined when a query has no relevant *or* borderline pair.** That is gq-p20 only.
  gq-p19 has 15 borderline pairs, so its NDCG is defined. An empty answer there scores 0.
- **CSR and the irrelevant-result rate are undefined for a query that returned nothing.** CSR is
  also undefined for a query without hard constraints (gq-p20).
  - So V0's CSR and irrelevant rate cover only the 10 queries where it returned results.
  - V2/V3's cover 19 queries for the irrelevant rate and 18 for CSR.
- **Precision@K always divides by K.** A list shorter than K is penalised even when every
  returned product is relevant.
- **Recall is pooled.** Its denominator counts only the relevant pairs in the judged pool, so it
  is biased upward wherever more products are relevant than were pooled.

**An empty answer is not a ranking.** The zero-result rate counts empty answers. `correct_empty`
scores an empty answer only on *zero-relevant* queries:

- Strict: 2 queries (gq-p19 and gq-p20).
- Lenient: 1 query (gq-p20; gq-p19 has borderline pairs).

The ranking metrics then score an empty answer as 0 wherever they are defined (precision, and
NDCG on gq-p19), or exclude it (CSR, irrelevant rate). Read the ranking metrics and the
empty-answer metrics together, not as one score.

**Unjudged results** count as not relevant and are reported separately. Every evaluated list was
pooled, so unjudged@5/10/20 is 0 for every arm; this was computed, not assumed. It says nothing
about products outside the pool.

## 5. Results at K = 10

### 5.1 Measured macro values

| Metric | V0 | V1 | V2 | V3-d20 | V3-d10 (secondary) |
|---|---|---|---|---|---|
| Precision@10 strict | 0.4000 (20/0) | 0.5650 (20/0) | **0.6550** (20/0) | 0.6500 (20/0) | **0.6550** (20/0) |
| Precision@10 lenient | 0.4000 (20/0) | **0.9150** (20/0) | 0.8250 (20/0) | 0.8250 (20/0) | 0.8250 (20/0) |
| Recall@10 strict (pooled) | 0.3998 (18/2) | 0.5809 (18/2) | **0.7114** (18/2) | 0.7059 (18/2) | **0.7114** (18/2) |
| Recall@10 lenient (pooled) | 0.1733 (19/1) | **0.3929** (19/1) | 0.3441 (19/1) | 0.3441 (19/1) | 0.3441 (19/1) |
| MRR@10 strict | 0.5556 (18/2) | 0.8167 (18/2) | **0.9514** (18/2) | 0.8889 (18/2) | 0.8907 (18/2) |
| MRR@10 lenient | 0.5263 (19/1) | **1.0000** (19/1) | 0.9474 (19/1) | 0.9474 (19/1) | 0.9474 (19/1) |
| NDCG@10 (graded) | 0.4656 (19/1) | **0.8942** (19/1) | 0.8753 (19/1) | 0.8655 (19/1) | 0.8665 (19/1) |
| CSR@10 hard | 1.0000 (10/10) | 0.6263 (19/1) | 0.8722 (18/2) | 0.8722 (18/2) | 0.8722 (18/2) |
| CSR@10 hard, all results satisfied | 1.0000 (10/10) | 0.4211 (19/1) | 0.7778 (18/2) | 0.7778 (18/2) | 0.7778 (18/2) |
| CSR@10 fp1-filterable | 1.0000 (10/10) | 0.6737 (19/1) | 0.9444 (18/2) | 0.9444 (18/2) | 0.9444 (18/2) |
| CSR@10 unknown (hard / fp1) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Irrelevant-result rate@10 | 0.0000 (10/10) | 0.0850 (20/0) | 0.0632 (19/1) | 0.0632 (19/1) | 0.0632 (19/1) |
| Unjudged@10 | 0 (20/0) | 0 (20/0) | 0 (20/0) | 0 (20/0) | 0 (20/0) |
| Zero-result rate | 0.5000 (20/0) | 0.0000 (20/0) | 0.0500 (20/0) | 0.0500 (20/0) | 0.0500 (20/0) |
| Correct-empty, strict (zero-relevant queries) | 1.0 (2/18) | 0.0 (2/18) | 0.5 (2/18) | 0.5 (2/18) | 0.5 (2/18) |
| Correct-empty, lenient | 1.0 (1/19) | 0.0 (1/19) | 0.0 (1/19) | 0.0 (1/19) | 0.0 (1/19) |

Returned results in the top 10, summed over queries: V0 80, V1 200, V2/V3 177. Empty answers:
V0 10 queries, V1 none, V2/V3 one (gq-p19).

### 5.2 Observed equalities between V2 and the V3 arms

These are equalities **observed** in the recorded lists, not properties guaranteed by design for
V3-d20.

- **V3-d20 vs V2.**
  - Both arms return the same candidate set for every query.
  - Their top-10 membership differs on 15 of 20 queries (1 to 5 products swapped). It is
    identical on gq-p03, p06, p09, p10 and p19, which returned 10 or fewer results.
  - Despite the swaps, CSR@10 hard was equal on all 18 defined queries, and lenient
    Precision@10 was equal on all 20.
  - Strict Precision@10 differed on one query (gq-p14: 0.6 → 0.5).
  - Different swaps could change CSR@10 or precision on other data.
- **V3-d10 vs V2.** In the recorded lists, V3-d10's top 10 has the same membership as V2's on
  all 20 queries, and ranks 11–20 are identical to V2's. This is consistent with head-only
  reranking (`docs/search-reranked-m8.md`). Its K=10 set-based metrics (precision, recall, CSR,
  irrelevant rate) therefore matched V2; only rank-sensitive metrics (MRR, NDCG) moved.

## 6. Paired query-level changes

Each Δ is challenger minus incumbent, taken over the queries where both values are defined.

| Pair | NDCG@10 mean Δ (n) | Up / down / tie | Lower on | Strict MRR@10 Δ (n=18) | Strict P@10 Δ (n=20) | CSR@10 hard Δ |
|---|---|---|---|---|---|---|
| V1 − V0 | +0.4286 (19) | 13 / 0 / 6 | — | +0.2611 | +0.1650 | −0.1700 (n=10; lower on p04, p09, p12) |
| V2 − V1 | −0.0189 (19) | 4 / 3 / 12 | p09, p10, p19 | +0.1347 | +0.0900 (5 up, 0 down) | +0.2111 (n=18; 6 up, 0 down) |
| V3-d20 − V2 | −0.0098 (19) | 3 / 2 / 14 | p14, p16 | −0.0625 | −0.0050 (p14) | 0 (n=18, all tied) |
| V3-d10 − V2 | −0.0088 (19) | 2 / 2 / 15 | p14, p16 | −0.0606 | 0 | 0 |
| V3-d10 − V3-d20 | +0.0010 (19) | 3 / 1 / 15 | p12 | +0.0019 | +0.0050 | 0 |

Six queries score NDCG@10 = 1.000 in every arm and separate nothing: gq-p01, p02, p03, p07, p08
and p18. gq-p11 and p17 are 1.000 for V1–V3.

### 6.1 Per-query values (NDCG@10 / strict P@10 / CSR@10 hard)

`—` means undefined. Each cell lists the three values in the order V0, V1, V2, V3-d20, V3-d10.
Labels are relevant / borderline / not relevant.

| Query | Text | Labels R/B/N | NDCG@10 | Strict P@10 | CSR@10 hard |
|---|---|---|---|---|---|
| gq-p04 | `lenovo lumen ryzen 7 laptop` | 1/25/0 | 0.361, 1.000, 1.000, 1.000, 1.000 | 0.1 in all | 1.0, 0.1, 0.1, 0.1, 0.1 |
| gq-p05 | `phone under 20k` | 19/10/4 | 0.000, 0.608, 1.000, 1.000, 1.000 | 0.0, 0.4, 1.0, 1.0, 1.0 | —, 0.4, 1.0, 1.0, 1.0 |
| gq-p06 | `bose headphones under 15000` | 7/15/2 | 0.000, 0.967, 0.967, 0.998, 0.998 | 0.0, 0.7, 0.7, 0.7, 0.7 | —, 0.7, 0.7, 0.7, 0.7 |
| gq-p09 | `12gb ram phone 256gb storage` | 6/20/2 | 0.842, 1.000, 0.842, 0.842, 0.842 | 0.6 in all | 1.0, 0.6, 1.0, 1.0, 1.0 |
| gq-p10 | `hp laptop 8gb 256 ssd under 40k` | 1/21/2 | 0.000, 0.820, 0.361, 0.361, 0.361 | 0.0, 0.0, 0.1, 0.1, 0.1 | —, 0.0, 1.0, 1.0, 1.0 |
| gq-p12 | `women running shoes` | 6/9/9 | 0.842, 0.917, 0.917, 0.921, 0.917 | 0.6 in all | 1.0, 0.6, 0.6, 0.6, 0.6 |
| gq-p13 | `50k ke andar gaming laptop` | 3/29/3 | 0.000, 0.681, 0.728, 0.734, 0.739 | 0.0, 0.0, 0.1, 0.1, 0.1 | —, 0.5, 1.0, 1.0, 1.0 |
| gq-p14 | `sasta phone 8gb ram wala` | 10/22/4 | 0.000, 0.642, 0.816, 0.740, 0.748 | 0.0, 0.2, 0.6, 0.5, 0.6 | —, 0.3, 1.0, 1.0, 1.0 |
| gq-p15 | `office ke liye laptop 16gb ram` | 20/17/2 | 0.000, 0.689, 1.000, 1.000, 1.000 | 0.0, 0.4, 1.0, 1.0, 1.0 | —, 0.4, 1.0, 1.0, 1.0 |
| gq-p16 | `samsng phone 128gb` | 3/20/1 | 0.000, 1.000, 1.000, 0.848, 0.859 | 0.0, 0.3, 0.3, 0.3, 0.3 | —, 0.3, 0.3, 0.3, 0.3 |
| gq-p17 | `noise cancelling headfones` | 20/2/2 | 0.000, 1.000, 1.000, 1.000, 1.000 | 0.0, 1.0, 1.0, 1.0, 1.0 | —, 1.0, 1.0, 1.0, 1.0 |
| gq-p19 | `laptop 64gb ram` (zero eligible) | 0/15/9 | 0.000, 0.665, 0.000, 0.000, 0.000 | 0.0 in all | —, 0.0, —, —, — |
| gq-p20 | `smartwatch` (out of catalog) | 0/0/24 | — in all | 0.0 in all | — in all (no hard constraint) |

### 6.2 Representative failures (measured facts)

- **Out-of-catalog query, gq-p20 (`smartwatch`).** V1, V2 and both V3 arms return 20 products,
  every one labelled not relevant (irrelevant-result rate 1.0). Only V0 returns nothing.
- **Zero-eligible query, gq-p19 (`laptop 64gb ram`).**
  - V2 and V3 return nothing; strict correct-empty is 1.
  - V1 returns 20 products, 40% of its top 10 labelled not relevant, and none satisfying the
    hard constraints.
  - Graded NDCG still prefers V1 (0.665 vs 0), because the query has borderline pairs.
- **Constraint violations that survive filtering.** CSR@10 hard stays below 1 in V1–V3 on:
  - gq-p04: 0.1;
  - gq-p06: 0.7;
  - gq-p12: 0.6;
  - gq-p16: 0.3.
- **Reranker regressions.**
  - gq-p16 (`samsng phone 128gb`): the first relevant product moves from rank 1 (V2) to rank 3
    (both V3 arms); NDCG@10 1.000 → 0.848 (d20).
  - gq-p14 (`sasta phone 8gb ram wala`): the first relevant product moves from rank 1 to rank 2;
    NDCG@10 0.816 → 0.740; strict P@10 0.6 → 0.5 (d20 only).
- **Few relevant results, ranked low.** On gq-p13 (`50k ke andar gaming laptop`) the 3 relevant
  products rank first at position 8 (V2), 6 (V3-d20) and 5 (V3-d10). V1 has none in its top 10;
  its first appears at rank 18.
- **Lexical misses.** V0 returns nothing for gq-p05, p06, p10, p13–p17, p19 and p20. Those
  include all 3 Hinglish and both noisy queries.

### 6.3 Recorded query-understanding outputs for these failures

The parser's extraction status is **recorded** in `query_extraction.jsonl` (qu-1, lexicon 1) and
in the filters V2 actually applied (`applied_filters` in the provenance):

| Query | Gold constraint | Recorded qu-1 status | Applied by V2 |
|---|---|---|---|
| gq-p04 | processor = AMD Ryzen 7 | `no_parser_field` | no |
| gq-p06 | price ≤ 15000 | `not_extracted` | no (category, brand only) |
| gq-p12 | gender = women; subcategory = running | `no_parser_field` | no (category only) |
| gq-p16 | brand = Samsung; storage = 128 GB | `not_extracted` | no (category only) |
| gq-p17 | category = headphones; ANC = true | `not_extracted`; `no_parser_field` | no filter applied |

**Hypothesis, not demonstrated:** the remaining constraint violations on gq-p04, p06, p12 and p16
are caused by these unenforced constraints. The extraction statuses are recorded facts. No
experiment here re-ran these queries with the constraints enforced, so the causal link is not
demonstrated. Likewise, the explanation for the reranker regressions on gq-p14 and gq-p16 is a
hypothesis: the cross-encoder scores text similarity and does not see unenforced constraints or
misspellings. It was not tested.

## 7. Supporting results at K = 5 and K = 20

| Metric | V0 | V1 | V2 | V3-d20 | V3-d10 |
|---|---|---|---|---|---|
| Precision@5 strict | 0.4600 (20/0) | 0.6100 (20/0) | 0.7100 (20/0) | 0.7100 (20/0) | 0.7100 (20/0) |
| Precision@5 lenient | 0.4600 (20/0) | 0.9300 (20/0) | 0.8600 (20/0) | 0.8600 (20/0) | 0.8600 (20/0) |
| Recall@5 strict | 0.2689 (18/2) | 0.3762 (18/2) | 0.4629 (18/2) | 0.4417 (18/2) | 0.4547 (18/2) |
| MRR@5 strict | 0.5556 (18/2) | 0.8167 (18/2) | 0.9444 (18/2) | 0.8796 (18/2) | 0.8907 (18/2) |
| NDCG@5 | 0.5003 (19/1) | 0.8842 (19/1) | 0.8829 (19/1) | 0.8700 (19/1) | 0.8692 (19/1) |
| CSR@5 hard | 1.0000 (10/10) | 0.6737 (19/1) | 0.9111 (18/2) | 0.9111 (18/2) | 0.9111 (18/2) |
| CSR@5 hard, all satisfied | 1.0000 (10/10) | 0.5263 (19/1) | 0.8333 (18/2) | 0.8889 (18/2) | 0.8889 (18/2) |
| Irrelevant-result rate@5 | 0.0000 (10/10) | 0.0700 (20/0) | 0.0526 (19/1) | 0.0526 (19/1) | 0.0526 (19/1) |
| Precision@20 strict | 0.2900 (20/0) | 0.4375 (20/0) | 0.5275 (20/0) | 0.5275 (20/0) | 0.5275 (20/0) |
| Precision@20 lenient | 0.2900 (20/0) | 0.9100 (20/0) | 0.6925 (20/0) | 0.6925 (20/0) | 0.6925 (20/0) |
| Recall@20 strict (pooled) | 0.4971 (18/2) | 0.8226 (18/2) | 0.9749 (18/2) | 0.9749 (18/2) | 0.9749 (18/2) |
| Recall@20 lenient (pooled) | 0.2422 (19/1) | 0.7826 (19/1) | 0.5725 (19/1) | 0.5725 (19/1) | 0.5725 (19/1) |
| MRR@20 strict | 0.5556 (18/2) | 0.8248 (18/2) | 0.9514 (18/2) | 0.8889 (18/2) | 0.8907 (18/2) |
| NDCG@20 | 0.4019 (19/1) | 0.9159 (19/1) | 0.8213 (19/1) | 0.8149 (19/1) | 0.8147 (19/1) |
| CSR@20 hard | 1.0000 (10/10) | 0.4921 (19/1) | 0.8463 (18/2) | 0.8463 (18/2) | 0.8463 (18/2) |
| Irrelevant-result rate@20 | 0.0000 (10/10) | 0.0900 (20/0) | 0.0658 (19/1) | 0.0658 (19/1) | 0.0658 (19/1) |
| Unjudged@5 / @20 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

How to read the K = 20 rows:

- **Lists of 20 are the maximum.** Every arm returned at most 20 products, so K = 20 covers whole
  lists; V2/V3 returned 302 results in total vs V1's 400.
- **V3-d10@20 mixes reranked and unreranked results.** Ranks 11–20 of V3-d10 were not reranked
  and keep the fused V2 order.
- **Strict recall@20 is pooled.** V2's 0.9749 is not a catalog-wide recall.

**Slices** (macro NDCG@10 / strict P@10; descriptive only):

| Slice | n | V0 | V1 | V2 | V3-d20 | V3-d10 |
|---|---|---|---|---|---|---|
| English | 15 | 0.632 / 0.533 | 0.927 / 0.627 | 0.863 / 0.673 | 0.866 / 0.673 | 0.866 / 0.673 |
| Hinglish | 3 | 0.000 / 0.000 | 0.671 / 0.200 | 0.848 / 0.567 | 0.825 / 0.533 | 0.829 / 0.567 |
| Noisy | 2 | 0.000 / 0.000 | 1.000 / 0.650 | 1.000 / 0.650 | 0.924 / 0.650 | 0.929 / 0.650 |

English NDCG covers 14 queries (gq-p20 is undefined). The Hinglish (3) and noisy (2) slices are
anecdotes, not estimates.

## 8. Trade-offs: strict relevance, graded relevance and constraint compliance

**Measured facts.**

- V2 is best or tied-best on strict Precision@10, strict Recall@10 and strict MRR@10, and has
  higher CSR@10 hard than V1 (0.8722 vs 0.6263; 6 queries up, none down).
- V1 is best on NDCG@10 and on every lenient metric.
- On the three queries where V2's NDCG@10 is below V1's (p09, p10, p19), V2 returned fewer
  results (6, 1 and 0). On those three queries:
  - V1's CSR@10 hard is 0.6, 0.0 and 0.0;
  - V2's is 1.0, 1.0 and undefined (empty answer).

**Interpretation.**

- **NDCG rewards constraint-violating products.** Graded NDCG gives gain 1 to borderline
  products, including products that violate a hard constraint of the query (the rubric allows
  `borderline` there). It therefore rewards V1's long, unfiltered lists. It also scores V2's
  correct empty answer on gq-p19 as 0.
- **Strict relevance plus constraint compliance favour V2.** They are the closer proxy for
  "returns what the buyer asked for".
- **Neither view is a single truth.** The table reports both. Which one should drive a
  configuration decision is a product judgement for the owner, not a measured result.

## 9. Fresh latency measurement (reranked endpoint, local snapshot)

### 9.1 What was measured, and what was not

The M8 harness (`scripts/benchmark_reranked.py`) ran in its new, explicit **local-snapshot**
mode on 2026-10-17, from 05:42:13 to 07:48:30 UTC. The owner reported exit code 0 and
`--verify` "OK: summary recomputes exactly from raw records". The figures below were read from
the saved artifacts. Separately, the per-run, per-arm P50/P95/P99 of `request_ms` were recomputed
from the raw timed samples with the nearest-rank method; all 9 run/arm groups matched the saved
summary exactly.

- **Endpoint.** All three arms call `GET /search/reranked` with top_k 10.
  - **disabled** = reranker off. It runs the V2 pipeline (qu-1 parsing, fp-1 filters, hybrid
    retrieval, RRF) through `/search/reranked`. **It is not a measurement of
    `/search/filtered`**, and its latency is attributed to V2 only with that caveat.
  - **depth10** and **depth20** = V3 with the cross-encoder reranking the top 10 or 20
    candidates.
- **Not measured.** V0 (`/search`) and V1 (`/search/hybrid`) fresh latency. The M5 hybrid
  harness refuses the current tree (29 guarded files differ from its pin), and the M3 harness
  uses a different, 12-query set. Neither was run.
- **Different query set.** The benchmark uses the **17 committed M5 benchmark queries**
  (query-set SHA-256 `ea1df08db89a8bceb0e5b6437f18e75a30aed997fb70d727d080b441ebbd78d3`), not
  the 20 pilot/dev Golden queries scored in §5–§8. No query text is identical between the two
  sets. Latency and quality therefore come from **different queries** and are juxtaposed in
  §9.6, not joined per query.
- **No quality is measured here.** The harness's descriptive rank facts are position overlaps,
  not relevance.

### 9.2 Protocol, counts and failures

- **Protocol** (unchanged from M8; protocol SHA-256
  `9212b75221e165e7fec98bbb388a75188c8fb5cb837202bd42bfeed237f75165`):
  - 3 runs, each in a fresh process with its own scratch database;
  - a fresh application per arm, with the arm order rotated (run 1: disabled, depth10, depth20;
    run 2: depth10, depth20, disabled; run 3: depth20, disabled, depth10);
  - per arm, 1 cold request (query index 0), then for each query 20 warm-ups and 200 timed
    requests.
- **Statistics.** Nearest-rank percentiles over timed samples only, with no exclusions and no
  retries. Per-run, per-arm figures are primary; pooled figures are secondary.
- **Counts.** 33,669 sample records: 9 cold, 3,060 warm-up and 30,600 timed (3,400 per run and
  arm). All 33,669 returned HTTP 200, and there were **no failed requests**. Every timed
  `request_ms` and `total_ms` value is present (0 nulls in 3,400 per run and arm).
- **Determinism.** For all three arms, the rankings and the scores were bitwise identical across
  the three runs on all 17 queries.
- **Run durations.** 2,906 s, 2,457 s and 2,211 s; the scratch database was dropped after each.

### 9.3 Warm (timed) latency per run, `request_ms`

`request_ms` is an in-process FastAPI `TestClient` GET, ending before `response.json()`. It is
**not network latency**. `total_ms` is the API's own handler-entry-to-result-model time. Values
are in milliseconds.

| Run | Arm | n | P50 | P95 | P99 | Max | `total_ms` P50 / P95 / P99 |
|---|---|---|---|---|---|---|---|
| 1 | disabled | 3400 | 26.070 | 31.441 | 35.080 | 249.210 | 22.221 / 27.095 / 30.609 |
| 2 | disabled | 3400 | 23.983 | 28.299 | 30.989 | 269.387 | 20.419 / 24.281 / 26.442 |
| 3 | disabled | 3400 | 24.279 | 28.500 | 31.075 | 35.215 | 20.662 / 24.418 / 26.945 |
| 1 | depth10 | 3400 | 266.331 | 348.083 | 409.696 | 626.760 | 261.194 / 342.268 / 404.696 |
| 2 | depth10 | 3400 | 228.926 | 327.049 | 370.888 | 529.418 | 224.294 / 321.870 / 364.992 |
| 3 | depth10 | 3400 | 192.378 | 248.298 | 279.463 | 34532.055 | 187.892 / 243.403 / 274.006 |
| 1 | depth20 | 3400 | 384.947 | 668.466 | 753.377 | 256717.689 | 380.215 / 663.278 / 748.074 |
| 2 | depth20 | 3400 | 352.579 | 471.132 | 511.844 | 184372.928 | 347.914 / 466.509 / 506.560 |
| 3 | depth20 | 3400 | 348.784 | 468.201 | 505.712 | 53064.262 | 344.108 / 463.572 / 501.093 |

**Per-run variation** (range of the three per-run values, with max/min ratio):

| Arm | P50 | P95 | P99 |
|---|---|---|---|
| disabled | 23.983–26.070 (1.09×) | 28.299–31.441 (1.11×) | 30.989–35.080 (1.13×) |
| depth10 | 192.378–266.331 (1.38×) | 248.298–348.083 (1.40×) | 279.463–409.696 (1.47×) |
| depth20 | 348.784–384.947 (1.10×) | 468.201–668.466 (1.43×) | 505.712–753.377 (1.49×) |

**Reranker increment over disabled, per run** (P50 difference within the same run):

- depth10: +240.3, +204.9 and +168.1 ms;
- depth20: +358.9, +328.6 and +324.5 ms.

The reranking stage itself (`reranking_ms`) had per-run P50 of 222.6, 189.1 and 155.6 ms at
depth 10, and 346.9, 314.7 and 311.1 ms at depth 20.

**Pooled (secondary)** over the 10,200 timed requests per arm, P50 / P95 / P99:

- disabled: 24.644 / 29.837 / 33.249;
- depth10: 225.564 / 322.608 / 380.770;
- depth20: 360.483 / 603.345 / 697.402.

The per-run variation for the reranked arms is large: depth 10's P50 fell from run to run, and
run 1's depth-20 P95 is about 1.4 times the other two runs'. Run 1's depth-20 arm had 901 timed
requests above 500 ms, against 66 and 40 in runs 2 and 3. The cause was not investigated and is
not established; machine power and thermal state, query order and the standby episodes in §9.4
are all candidates. The runs are not interchangeable repeats of a controlled condition.

### 9.4 Connected-standby stalls (disclosed, not excluded)

The harness recorded eight Windows Kernel-Power events: four pairs of 506 (entering connected
standby) and 507 (exiting). The machine was on AC power throughout, and the battery went from 72%
to 100%. Each standby window contains exactly one timed request longer than 1 s, and those four
are the only timed requests above 1 s:

| Standby window (UTC) | Duration | Run / arm | Stalled request `request_ms` |
|---|---|---|---|
| 06:23:30.5 → 06:28:04.9 | 274.3 s | 1 / depth20 | 256,717.7 |
| 07:04:42.9 → 07:08:13.1 | 210.3 s | 2 / depth20 | 184,372.9 |
| 07:26:49.6 → 07:28:00.5 | 70.9 s | 3 / depth20 | 53,064.3 |
| 07:38:02.1 → 07:38:42.9 | 40.8 s | 3 / depth10 | 34,532.1 |

The protocol forbids exclusions, so these samples stay in every statistic. They dominate the
**maxima and means** (for example, run 1 depth-20 mean 481.4 ms against P50 384.9 ms). A
single sample out of 3,400 moves a nearest-rank P50, P95 or P99 by at most one rank, so the
percentiles above are essentially unaffected by the stalls themselves. The stalls do not
explain the between-run variation. The M8 run recorded no power events.

### 9.5 Cold requests and memory

The cold request is the first request of each arm, in a fresh application. It includes loading
the embedding model and, for reranked arms, the reranker.

| Run | Arm (position) | `request_ms` | `model_load_ms` | `reranker_load_ms` |
|---|---|---|---|---|
| 1 | disabled (1st) | 278.576 | 156.425 | — |
| 1 | depth10 (2nd) | 991.471 | 168.827 | 472.372 |
| 1 | depth20 (3rd) | 1427.320 | 256.663 | 488.773 |
| 2 | depth10 (1st) | 729.425 | 127.031 | 351.112 |
| 2 | depth20 (2nd) | 996.768 | 171.739 | 364.583 |
| 2 | disabled (3rd) | 245.467 | 155.224 | — |
| 3 | depth20 (1st) | 1027.301 | 124.446 | 353.119 |
| 3 | disabled (2nd) | 251.604 | 170.183 | — |
| 3 | depth10 (3rd) | 921.851 | 182.164 | 389.324 |

Cold `request_ms` ranges: disabled 245.5–278.6 ms, depth10 729.4–991.5 ms, depth20
996.8–1427.3 ms. With one cold request per run and arm, these are three observations each, not
distributions.

The process peak resident set (lifetime to date, so not attributable per arm) reached 1,053.6,
1,015.0 and 1,104.4 MiB in runs 1–3.

### 9.6 Quality versus latency (different query sets)

The quality columns come from the 20 pilot/dev Golden queries (§5). The latency columns come
from the 17 M5 benchmark queries (§9.3). The two sets share no identical query text. Because the
rows juxtapose two measurements, they do not show a per-query trade-off.

| Arm | NDCG@10 | Strict MRR@10 | Strict P@10 | CSR@10 hard | Warm P50 (per-run range) | Warm P95 (per-run range) | Cold `request_ms` |
|---|---|---|---|---|---|---|---|
| V0 lexical | 0.4656 | 0.5556 | 0.4000 | 1.0000 (10/10) | not measured | not measured | not measured |
| V1 hybrid | 0.8942 | 0.8167 | 0.5650 | 0.6263 | not measured | not measured | not measured |
| V2 filtered | 0.8753 | 0.9514 | 0.6550 | 0.8722 | 24.0–26.1 ms\* | 28.3–31.4 ms\* | 245–279 ms\* |
| V3-d10 (secondary) | 0.8665 | 0.8907 | 0.6550 | 0.8722 | 192.4–266.3 ms | 248.3–348.1 ms | 729–991 ms |
| V3-d20 (configured) | 0.8655 | 0.8889 | 0.6500 | 0.8722 | 348.8–384.9 ms | 468.2–668.5 ms | 997–1427 ms |

\* Measured as the "disabled" arm of `/search/reranked`, not via `/search/filtered`.

### 9.7 Artifacts, bindings and environment

Outputs are in `data/processed/rerank_benchmark_m10_local/`, experiment
`rerank-m10-local-3eb932df4804`:

| File | SHA-256 |
|---|---|
| `rerank-m10-local-3eb932df4804.json` (summary) | `171b97cae8ecac6a36c0808a59ce91f8af0f0d9e453f6fb0a5b608a004ff0973` |
| `rerank-m10-local-3eb932df4804.samples.jsonl` (raw; 33,690 records per the summary) | `9b27a321baa7117658a9b57aed5855e1ad18ac3535c9ab9c86e55fa3d6f56af5` |
| `rerank-m10-local-3eb932df4804.md` (generated report) | `7977210791d74e3e9aca1771e162c88c0bdaa50ac9fb7b848b509c3014d45869` |

The raw-sample hash recorded inside the summary equals the file's hash. The M8 artifacts in
`data/processed/rerank_benchmark/` are unchanged (`ab19c052…`, `681fbf76…`, `e76fd924…`).

| Binding | Value |
|---|---|
| Mode | `local_snapshot`. **Not reproducible from HEAD.** |
| `binding_sha256` | `3eb932df4804de749a635f05dc703334f2b11c3bcf779a24e91de8fbdcf978bc` |
| Source tree SHA-256 (96 fingerprinted files) | `7d65d749f43c63b44469cbcef22113495db9aae66be6ce2735f01ca745253914` |
| Harness HEAD / production commit | `8c57bdb` / `558bd39` |
| Accepted guarded difference | `pyproject.toml` only: production blob `231a9865…`, HEAD and working tree `edfaa1f5…` (committed since production; no uncommitted change) |
| Working tree | dirty: 63 uncommitted paths, 62 of them outside the fingerprinted source. The harness itself (`scripts/benchmark_reranked.py`, blob `ad5906f1…`) is uncommitted. |
| Models | as in §2 (same embedding and reranker revisions and config hashes) |
| Settings | lexical_k 50, dense_k 50, candidate_k 50, `rrf_k` 100 (provisional), top_k 10; rerank depth 10 or 20 |
| Catalog | `synthetic-seed` version 1, 240 products, checksum `2c1c5fa4…` |

**Environment.**

- **Machine:** Windows 11 (10.0.26300), x86-64, Intel64 Family 6 Model 140, 8 logical CPUs,
  torch threads 4.
- **Python and libraries:** Python 3.12.10; torch `2.14.1+cpu`, sentence-transformers `6.1.0`,
  transformers `5.18.0`, tokenizers `0.23.2`, fastapi `0.142.2`, psutil `7.2.2`.
- **Database:** PostgreSQL 16.15 with pgvector 0.8.6 (Docker), shared_buffers 128MB, jit on.
- **Microsoft Visual C++ 2015–2022 Redistributable (x64): 14.44.35211.0**, installed
  2026-10-17. `msvcp140.dll`, `vcruntime140.dll` and `vcruntime140_1.dll` in System32 are all
  14.44.35211.0.
  - The binding and the benchmark header do **not** capture the system runtime. It is recorded
    here from the registry and file versions.
  - An earlier attempt failed (`EmbedderUnavailable: load_failed`). An older VC++ 14.25
    `msvcp140.dll` stopped `torch` from loading (`WinError 1114` on `c10.dll`). That attempt
    wrote no artifacts, and this runtime update fixed it.

**Historical M8 figures are not comparable as a before/after.** The M8 run (same 17 queries,
committed production code) used Python 3.12.3 and an unrecorded VC++ runtime. It ran under
different machine conditions and recorded no standby events. Its pooled warm P50s were 33.8,
265.9 and 523.8 ms. Differences from this run are not attributed to any code or configuration
change.

**Commands** (PowerShell). `--verify` is read-only. The run was executed by the owner; the
second command is the prepared form of the run command, not a transcript of the owner's
session, and it now refuses because the output directory exists.

```powershell
$env:HF_HUB_OFFLINE="1"
.venv\Scripts\python.exe scripts\benchmark_reranked.py --verify data\processed\rerank_benchmark_m10_local\rerank-m10-local-3eb932df4804.json
.venv\Scripts\python.exe scripts\benchmark_reranked.py --execute --local-snapshot `
  --accept-guarded-change pyproject.toml=edfaa1f5600355160897abceb38756c0a576a869 `
  --binding-sha256 3eb932df4804de749a635f05dc703334f2b11c3bcf779a24e91de8fbdcf978bc `
  --output-dir data/processed/rerank_benchmark_m10_local
```

## 10. Provisional, development-scoped interpretation

These conclusions come from the pilot/dev data only:

1. **Keep structured filtering (V2) as the development baseline.** It leads on strict
   relevance and constraint compliance on these 20 queries.
2. **The reranker as configured is not supported by this data.**
   - Model `ms-marco-MiniLM-L6-v2`, at depth 20 or 10.
   - Its net effect on strict MRR@10 is −0.06 relative to V2, driven by gq-p14 and gq-p16.
   - Its NDCG@10 changes are small (−0.010 at depth 20, −0.009 at depth 10), and split 3 up / 2
     down and 2 up / 2 down.
   - **Latency (§9, different 17-query set):** the reranker added roughly 170–240 ms (depth
     10) and 325–360 ms (depth 20) to the per-run warm P50 of the disabled arm (24–26 ms), and
     0.7–1.4 s cold requests.
   - **Development-scoped recommendation: no reranker** (`reranker_enabled` false; the V2
     pipeline). On this evidence the reranker adds substantial latency and gives no measured
     quality gain on the pilot/dev queries. If the owner keeps a reranker for other reasons,
     depth 10 costs less than depth 20 here, with equal or slightly better quality on these 20
     queries (NDCG@10 +0.001, strict MRR@10 +0.002 vs d20). That difference is too small to rank
     the two.
   - **Owner decision (2026-10-17): no reranker approved for development scope only;
     configuration unchanged.** The configured default is still `reranker_enabled` true with
     depth 20. Changing the configuration, and any production, held-out or official M10
     decision, are separate and not made here. The approval rests on 20 development queries for
     quality and a different 17-query set for latency, with no significance test and no
     held-out evidence.
3. **Development priorities suggested by the failures,** all to be confirmed separately:
   - deterministic parser coverage for the recorded unextracted constraints (§6.3);
   - out-of-catalog and zero-eligible handling (gq-p20; V1 on gq-p19);
   - reranker robustness on misspelled and partly-enforced queries.

What these results do **not** support:

- **Statistical significance.** None is claimed; no test was run, and the averages cover 18–20
  queries.
- **Production quality or impact.** The catalog is synthetic and the queries are pilot/dev.
- **Held-out performance.** There are zero test queries.
- **Superiority beyond these five arms.** Only one `rrf_k` (100), one embedding model and one
  reranker model were evaluated.
- **An `rrf_k` or embedding-model re-decision.** Other values would retrieve products outside
  the judged pool. `eval-1` counts unjudged results as not relevant, so a fair comparison needs
  a pool extension and new human labels, or a separately approved protocol.

## 11. Remaining M10 requirements

| Requirement (MASTER_PLAN M10) | Status |
|---|---|
| V0–V3 quality on the human-reviewed Golden Dataset | Done for pilot/dev only (this document). No held-out partition exists. |
| Configuration and failure cases | Documented here (§2, §6). |
| Latency | **Partly done** (§9): fresh measurements for the `/search/reranked` arms (disabled, depth 10, depth 20) on the 17-query M5 set, in an uncommitted local snapshot, with standby stalls and large between-run variation disclosed. **V0 and V1 fresh latency not measured**; `/search/filtered` not measured directly. Accepted at closure as named limitations 2 and 3. |
| Measured recommendation | Development-scoped only (§10): keep filtering; no reranker, approved for development scope only (2026-10-17). Configuration unchanged. |
| Re-decide `rrf_k` | **Not performed** (one value evaluated). `rrf_k` = 100 remains provisional (named limitation 1). A comparative re-decision would need a new, approved protocol with pooled human labels. |
| Re-decide the embedding model | **Not performed** (one model evaluated). The current model remains provisional (named limitation 1). Same requirement. |
| Re-decide the reranker pin (or no reranker) | No reranker approved for development scope only (2026-10-17; §10), from off / d10 / d20 with one model. Configuration unchanged; no alternative model evaluated; any configuration, production or official decision is outstanding. |
| Closure | **Closed, development-scoped (pilot/dev), under the owner-approved scope amendment of 2026-10-17** with five named limitations and deferred items (see "M10 scope amendment and closure"). Not a claim that all original requirements were met. |
| M16 prerequisite ("M10 complete") | **Not satisfied by this closure alone.** Separate owner decision when M16 is planned. |
| Source provenance | The relevant code (review-2 tooling, the quality evaluator and the modified latency harness), the golden chain and the quality results are now committed locally in C1–C5, ending at `d55ba40955dea2ab839087755d40b45168d524f3` (not pushed). Both runs still used uncommitted sources at run time: the evaluation ran on a dirty working tree, and the latency run from an uncommitted local snapshot (§9.7, §12). Committing preserves the recorded bytes; it does not make either run a clean-tree run. The latency artifacts remain git-ignored under `data/processed/` and are not committed. |

## 12. Reproduction, outputs and source hashes

**Outputs** (`evals/results/internal-search/v1/human-pilot-dev/`; evaluation `hpd-1`,
evaluator `eval-1`):

| File | SHA-256 |
|---|---|
| `run_record.json` | `7ea689032ac6a0e57801f9d93a9cfa6d80c00a1c5cf5a8c5e2783d73ee32a052` |
| `summary.json` | `b7311102b8003e04fe3dbf14a1a3a024214987069304e9e90cd67f4ef59771f3` |
| `metrics/v0_lexical.json` | `abab581f40960bd4b1dc64ad10651e45ef8f2c35d9ac045dc3821df863c28258` |
| `metrics/v1_hybrid.json` | `61a38d0adfe131906fa31e7e79c18e75b8d63b5cb7352121bd89ea0936e459e9` |
| `metrics/v2_filtered.json` | `b18c60fec080670399e6b79b4bdfe6eae998ff0dd1e306744d570c56b5d8f5cd` |
| `metrics/v3_reranked_d20.json` | `4a47ced6f23a6a576dae41b1f5373326de54ab494cbb369377fbdda114d7dbee` |
| `metrics/v3_reranked_d10.json` | `6d270698e4e3b68bbcce710159eb2362d5f134fae6c775a0c63e59ab2fb78c4b` |

**Label and provenance bindings:**

| Artifact | SHA-256 |
|---|---|
| `labels_frozen` manifest | `16358129b12bc094660bca739aff4e8a5edc3c9e080cd5d868a19549d2e2136c` |
| `provenance_unsealed` manifest | `413b72edd507bf34a3302297b05180a392f4c1a28715451de5296beaafa43002` |
| `final_judgements.jsonl` (568) | `42cd3a6a67465069e149f7ad1c54a1d92a1af1cb49f52198b8a2ecdb68ce50bd` |
| `eligibility.jsonl` (568) | `3505da68df2f7cbe562504416774c5b013b7335ce96c66efe788c5927ed8aff5` |
| `partitions.jsonl` | `033735f19e56f56de89733f7687d191380377fc97ed61d5061ec255550c24162` |
| `provenance.json` (= `provenance_binding`) | `fc6526f9863ede2eb48b3fe3162dfc804b7138f21e26c8c7576d70f704a078a8` |

**Evaluation source at run time** (recorded in `run_record.json`, `evaluation_source`):

| File | Git state | SHA-256 |
|---|---|---|
| `evals/metrics/retrieval.py` (evaluator) | committed, unchanged | `fe0bf6a9b0bc12d6a4dfc7b0775669931679010b7298ac08945931ee617b8de8` |
| `evals/runners/evaluate_human_pilot_dev.py` | untracked | `f26d8daa80135e64a3a1a80abbeb6428816fb086f3ac2ddea91a9fd00c030cc5` |
| `evals/runners/unseal_provenance.py` | untracked | `a676a32524d2b8e60bafa0f851b2555ac6d641ead2f8398e59781214c56cd8d1` |
| `evals/contracts/canonical.py` | committed, unchanged | `eb2d26cb6efdefc506207a7e1d06e11c07e80de3a9377e988c3406e54940b45a` |
| `evals/contracts/constants.py` | modified | `a1c8657121fc461e9f7a8b1495bfcfb75072540e85df106435e379bae8b2748e` |
| `evals/contracts/manifest.py` | modified | `42564b12101ca67b2dc61d10c6b6a5b5e24d11ad51c4ee36605ad8f4ce18afe1` |
| `evals/contracts/models.py` | modified | `a290d60398a36682e56e0f227473a796967b2251e92f72d3c5d0646c576098a5` |

**Uncommitted-source limitation.** The evaluation ran at HEAD `8c57bdb` with a dirty working tree
(53 uncommitted paths):

- The runner and part of the contract code were uncommitted, so HEAD alone does not reproduce
  this evaluation. The code is identified by the per-file SHA-256 values above, not by a commit.
- The golden labels, provenance and manifests 5–6 were also uncommitted. They are identified by
  their hashes.
- The retrieval runtime trees recorded with the pool run equal HEAD's.

**Commands** (PowerShell, offline):

```powershell
$env:HF_HUB_OFFLINE="1"
# Re-verify the written outputs: regenerates every file from the verified chain, byte for byte.
.venv\Scripts\python.exe -m evals.runners.evaluate_human_pilot_dev verify
# The run that produced them (historical; it refuses now because the destination exists):
.venv\Scripts\python.exe -m evals.runners.evaluate_human_pilot_dev run `
  --labels-frozen-sha256 16358129b12bc094660bca739aff4e8a5edc3c9e080cd5d868a19549d2e2136c `
  --provenance-unsealed-sha256 413b72edd507bf34a3302297b05180a392f4c1a28715451de5296beaafa43002
```

`check` refuses an existing destination, so it can no longer be run against the default output
directory. `verify` is the reproduction check for the written results:

- It re-verifies the review-2 chain through `provenance_unsealed`, including an offline pool
  re-assembly.
- It requires the evaluator source hash to match the recorded value.
- It compares every regenerated file with the written bytes.
- It carries the recorded `evaluation_source` block as recorded, without re-checking it.

**Line-ending dependency (disclosed, not fixed).** The source hashes are SHA-256 values of the
working-tree bytes, and those bytes depend on the checkout.

- The evaluator `evals/metrics/retrieval.py` is committed with LF line endings, and the
  recorded hash `fe0bf6a9…` equals both the committed bytes and the current working copy (LF).
  This machine's Git configuration (`core.autocrlf` true) would convert it to CRLF on a fresh
  checkout. The working bytes would then differ from the recorded hash, so `verify`'s
  evaluator-hash check would refuse there even though the committed content is unchanged.
- The other `evaluation_source` hashes were taken from LF files that were uncommitted at run
  time. Their stored bytes will equal the recorded bytes; a CRLF re-checkout would make the
  working files differ from the recorded hashes in the same way.
- The result files are protected: `.gitattributes` stores and checks out `evals/results/**`
  with LF, matching the written bytes, as it already does for `evals/goldens/**`.

`run` built the outputs once in-process and twice in fresh processes, and wrote them only if all
three were byte-identical.

The owner reported that `run` and `verify` succeeded. The values in this document were read from
the written outputs, after confirming that `run_record.json` hashes to the value above and that
every output file matches its hash in the run record.

## 13. Limitations (consolidated)

- **Data scope.** 20 pilot/dev queries (15 English, 3 Hinglish, 2 noisy) on a synthetic
  240-product catalog. There is no held-out test partition and no contradictory-constraint
  query.
- **Recorded runs.** The quality figures come from previously recorded runs re-scored with new
  labels, not fresh retrieval runs.
- **Latency.**
  - Measured only for the `/search/reranked` arms, on a different 17-query set, in-process (not
    network).
  - Measured on one laptop, from an uncommitted local snapshot that HEAD cannot reproduce.
  - Four requests stalled across connected-standby episodes and stay in the statistics.
  - Between-run variation is large and its cause is not established.
  - The system VC++ runtime is outside the binding.
  - V0, V1 and `/search/filtered` were not measured.
- **Labels.**
  - One primary annotator, who had seen machine proposals and received AI rubric explanations.
  - Second-human review of the cs-1 sample only, with procedural blindness.
  - Agreement figures describe that sample only and are not accuracy.
  - 218 optional candidates were not re-adjudicated.
  - Intra-annotator consistency was not measured.
- **Metrics.**
  - Recall is pooled.
  - Precision@K penalises short lists.
  - Graded NDCG credits constraint-violating borderline products.
  - Several metrics exclude empty answers or zero-relevant queries (see the `n/excluded` columns).
  - V3-d10@20 mixes reranked and unreranked ranks.
  - Unjudged = 0 is guaranteed by pooling, not evidence about unpooled products.
- **Inference.** No significance test was run. Slices of 2–3 queries are anecdotes. Causes in
  §6.3 are hypotheses.
- **Configuration.** Only the five recorded arms were evaluated. There is no `rrf_k`,
  embedding-model or alternative-reranker comparison.
- **Source provenance.** The evaluation code and golden chain were uncommitted at run time (§12).
  Recorded source hashes depend on the checkout's line endings (§12).
- **Separate history.** The machine-label provisional analysis is separate historical evidence
  and is not combined with these results.

## Status

- Human-reviewed pilot/dev quality evaluation: **outputs written and verified (reported by the
  owner)**; this report is **approved by the owner (2026-10-17)**.
- Fresh latency: **measured for the `/search/reranked` arms** (local snapshot; owner-reported
  run and verify succeeded; artifacts hashed in §9.7). V0/V1: **not measured**.
- Reranker: **no reranker approved for development scope only** (2026-10-17); configuration
  unchanged.
- `rrf_k` / embedding model: **not re-decided**; both remain provisional.
- Held-out test evaluation: **not possible** (dataset deferred).
- M10: **closed, development-scoped (pilot/dev), under the owner-approved scope amendment
  (2026-10-17)**, with five named limitations and deferred items. Not a claim that all original
  M10 requirements were met.
- M16's "M10 complete" prerequisite: **not satisfied by this closure alone**; separate owner
  decision. M11: not started.
