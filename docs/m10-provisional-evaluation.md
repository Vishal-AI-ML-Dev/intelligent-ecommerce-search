# M10 provisional evaluation — analysis of the machine-only V0–V3 results

> **WARNING — PROVISIONAL DEVELOPMENT ANALYSIS. M10 IS NOT OFFICIALLY COMPLETE.**
> - **All 568 labels are machine-generated** (`weak-label-policy-1`). They are unaudited
>   deterministic weak labels.
> - **Human audit coverage is 0% (0/568).** Two started human audit decisions were abandoned,
>   archived and excluded.
> - **The results may favour V2/V3.** The labels come from the same gold constraints that V2
>   filters on, and retrieval consensus and the V3 cross-encoder shaped label confidence.
> - **This is development evidence only.** It is not Golden Dataset evidence and not held-out
>   test evidence. The 20 queries are the development pilot.
> - **M10 is not officially complete.** MASTER_PLAN M10 requires a comparison on the
>   human-reviewed Golden Dataset. Strict status when this analysis was committed (`73c67c1`):
>   strict M9 was paused at 0/568. Current status (2026-10-17): the strict
>   human-reviewed pilot/dev Golden Dataset is complete under `review-2`; the held-out test
>   dataset is deferred; official M10 is not complete. A separate development-scoped report on
>   the human-reviewed pilot/dev labels is a draft for review
>   (`docs/m10-human-pilot-dev-evaluation.md`). This analysis stays machine-label only, and the
>   two are not combined.
>
> Every conclusion below is a **machine-label development conclusion**.

## Scope and inputs

This document analyses results that already exist. It adds no code, labels, metrics, manifests
or private data. Every number comes from these committed, verified artifacts:

- `evals/weaksets/internal-search/v1/machine-only/metrics/v0.json` … `v3.json`: the per-query
  and macro `eval-1` metrics. Their summary report is
  `evals/weaksets/internal-search/v1/machine-only/reports/provisional-v0-v3.md`.
- `evals/weaksets/internal-search/v1/machine-only/proposal-summary.json`: the label counts per
  query.
- `evals/goldens/internal-search/v1/queries.jsonl`: language style and intent family.
- `evals/goldens/internal-search/v1/query_extraction.jsonl`: what qu-1 extracted for each gold
  constraint, and whether fp-1 applied it.

The versions are V0 = `v0_lexical`, V1 = `v1_hybrid`, V2 = `v2_filtered` and V3 =
`v3_reranked_d20` (the configured default rerank depth 20). V3 reranks V2's candidates. The
paired deltas and slice means below are simple arithmetic on the committed per-query values,
taken over the queries where both values are defined. No new evaluator was run.

### Verification (this session)

| Command | Result |
|---|---|
| `uv run --offline python -m evals.runners.freeze_queries verify` | `queries_frozen` `51204dae…` verified |
| `uv run --offline python -m evals.runners.build_pool verify` | `pool_frozen` `4c26628a…` verified; private provenance matches its binding and reproduces the pool |
| `uv run --offline python -m evals.weak_labels verify` | proposals `9dc1fab2…` verified: 568 proposals, 100 audit pairs, 319 flagged |
| `uv run --offline python -m evals.weak_labels verify-machine-only` | every machine-only file regenerates byte for byte; 568 `machine_unreviewed`, human audit 0/568 |
| `uv run python -m evals.annotation status` | 0 of 568 pairs judged (strict M9 untouched) |
| `uv run python -m evals.weak_labels status` | 0 of 100 audit pairs decided |
| Archive metadata check (read only; decisions not opened) | `abandoned_partial_audit`, 2 decisions, excluded from labels, metrics and statistics |

## 1. Overall (macro, machine labels)

| Metric | V0 | V1 | V2 | V3 |
|---|---|---|---|---|
| NDCG@10 (n=19) | 0.4756 | 0.7584 | **0.8449** | 0.8150 |
| Precision@10 strict (n=20) | 0.3950 | 0.5500 | **0.6300** | 0.6250 |
| MRR@10 strict (n=17) | 0.5588 | 0.8647 | **1.0000** | 0.9314 |
| Recall@10 strict (n=17, pooled) | 0.4348 | 0.6231 | **0.7358** | 0.7299 |
| CSR@10 hard | 1.0000 (n=10) | 0.6263 (n=19) | 0.8722 (n=18) | 0.8722 (n=18) |
| Irrelevant-result rate@10 | 0.0000 (n=10) | 0.3600 (n=20) | 0.2105 (n=19) | 0.2211 (n=19) |
| Zero-result rate (n=20) | 0.5000 | 0.0000 | 0.0500 | 0.0500 |

V0's perfect CSR and zero irrelevant rate are artefacts. V0 returns nothing for 10 of the 20
queries, so both values cover only the 10 queries where it returned results.

### Paired deltas (challenger − incumbent)

| Pair | NDCG@10 mean Δ (n) | Up / down / tie | Queries that drop |
|---|---|---|---|
| V1 − V0 | +0.2827 (19) | 11 / 0 / 8 | — |
| V2 − V1 | +0.0866 (19) | 4 / 1 / 14 | gq-p13 |
| V3 − V2 | **−0.0299** (19) | 2 / 4 / 13 | gq-p04, gq-p12, gq-p14, gq-p16 |
| V3 − V1 | +0.0566 (19) | 6 / 4 / 9 | gq-p04, gq-p12, gq-p13, gq-p16 |

Changes on the guard metrics:

- **V2 − V1:**
  - CSR@10 hard +0.2111 (n=18): better on 6 queries, worse on none.
  - Irrelevant-result rate −0.1158 (n=19): better on 5 queries, worse on 1 (gq-p13).
- **V3 − V2:**
  - CSR@10 hard is identical on all 18 queries, because V3 has the same candidate set.
  - Irrelevant-result rate +0.0105 (worse on gq-p12 only).
  - MRR@10 −0.0686 (gq-p14, gq-p16).

## 2. Per query (NDCG@10, and CSR@10 hard where it is informative)

| Query | Text | Style / family | V0 | V1 | V2 | V3 | Note |
|---|---|---|---|---|---|---|---|
| gq-p01 | headphones | en / broad | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p02 | dell laptop | en / brand | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p03 | adidas shoes | en / brand | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p04 | lenovo lumen ryzen 7 laptop | en / exact | 0.562 | 1.000 | 1.000 | 0.931 | CSR 0.1 in V1–V3; only 1 relevant pooled product |
| gq-p05 | phone under 20k | en / price | 0.000 | 0.294 | 1.000 | 1.000 | filter fixes it: CSR 0.4 → 1.0 |
| gq-p06 | bose headphones under 15000 | en / price | 0.000 | 0.926 | 0.926 | 0.995 | CSR 0.7 in V1–V3 (price not extracted) |
| gq-p07 | laptop 16gb ram 512gb ssd | en / spec | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p08 | 1tb nvme laptop | en / spec | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p09 | 12gb ram phone 256gb storage | en / spec | 0.911 | 0.911 | 0.911 | 0.911 | CSR 0.6 (V1) → 1.0 (V2/V3) |
| gq-p10 | hp laptop 8gb 256 ssd under 40k | en / combined | 0.000 | 0.190 | 0.760 | 0.760 | V2 returns 1 result; CSR 0 → 1.0 |
| gq-p11 | gaming laptop | en / semantic | 0.691 | 0.828 | 0.828 | 0.833 | |
| gq-p12 | women running shoes | en / spec | 0.874 | 0.952 | 0.952 | 0.874 | CSR 0.6 in V1–V3 |
| gq-p13 | 50k ke andar gaming laptop | hi / combined | 0.000 | 0.277 | 0.000 | 0.000 | 0 relevant pooled products (see §5) |
| gq-p14 | sasta phone 8gb ram wala | hi / spec | 0.000 | 0.470 | 0.768 | 0.704 | |
| gq-p15 | office ke liye laptop 16gb ram | hi / semantic | 0.000 | 0.653 | 1.000 | 1.000 | |
| gq-p16 | samsng phone 128gb | noisy / combined | 0.000 | 0.908 | 0.908 | **0.476** | CSR 0.3; largest V3 regression |
| gq-p17 | noise cancelling headfones | noisy / spec | 0.000 | 1.000 | 1.000 | 1.000 | |
| gq-p18 | apple | en / ambiguous | 1.000 | 1.000 | 1.000 | 1.000 | |
| gq-p19 | laptop 64gb ram | en / zero-eligible | 0.000 | 0.000 | 0.000 | 0.000 | V2/V3 correctly return nothing; V1 returns 20 |
| gq-p20 | smartwatch | en / out-of-catalog | — | — | — | — | V1–V3 return 20 irrelevant results |

Seven queries are saturated at 1.000 for every version that returns results. They separate
nothing.

## 3. Slices: English, Hinglish and noisy

These are macro means over the defined queries. The Hinglish (3) and noisy (2) slices are
anecdotes, not estimates.

| Slice | Queries | NDCG@10 V0 / V1 / V2 / V3 | CSR@10 hard V1 / V2 / V3 | Irrelevant@10 V1 / V2 / V3 | Zero-result V0 |
|---|---|---|---|---|---|
| English | 15 | 0.646 / 0.793 / **0.884** / 0.879 | 0.671 / 0.877 / 0.877 | 0.347 / 0.164 / 0.179 | 5/15 |
| Hinglish | 3 | 0.000 / 0.466 / **0.589** / 0.568 | 0.400 / 1.000 / 1.000 | 0.433 / 0.333 / 0.333 | 3/3 |
| Noisy | 2 | 0.000 / **0.954** / **0.954** / 0.738 | 0.650 / 0.650 / 0.650 | 0.350 / 0.350 / 0.350 | 2/2 |

- **Hinglish.**
  - V0 (lexical) returns nothing for all three Hinglish queries.
  - V2's filters help gq-p14 and gq-p15 (CSR 1.0).
  - gq-p13 is the slice's weak point in every version.
- **Noisy.**
  - V0 returns nothing for both noisy queries.
  - Filtering does not help: qu-1 does not extract the misspelled brand (`samsng`) or the
    misspelled category (`headfones`).
  - V3 loses 0.432 NDCG@10 on gq-p16.

## 4. Hard constraints, irrelevant results and zero results

- **Filtering works where qu-1 extracts the constraint.** V2 raises CSR@10 hard on gq-p05,
  gq-p09, gq-p10, gq-p13, gq-p14 and gq-p15 (to 1.0 on each). The irrelevant-result rate drops
  on gq-p05, gq-p09, gq-p10, gq-p14 and gq-p15.
- **Constraints that qu-1 does not extract** (`query_extraction.jsonl`) stay violated in
  V1–V3:

  | Query | Constraint | qu-1 status | CSR@10 hard V2 |
  |---|---|---|---|
  | gq-p04 | processor = AMD Ryzen 7 | `no_parser_field` | 0.1 |
  | gq-p06 | price ≤ 15000 | `not_extracted` | 0.7 |
  | gq-p12 | gender = women, subcategory = running | `no_parser_field` | 0.6 |
  | gq-p16 | brand = Samsung, storage = 128 GB | `not_extracted` | 0.3 |
  | gq-p17 | category = headphones; anc | `not_extracted` / `no_parser_field` | 1.0 (retrieval found headphones anyway) |

  For gq-p06, qu-1 reads a price only with a `k` suffix, `₹` or `rs`/`inr`
  (`docs/query-understanding-m6.md`), so the bare `15000` is not a price.
- **Zero results.**
  - V0 returns nothing on 8 of the 18 expected-non-empty queries: gq-p05, gq-p06, gq-p10,
    gq-p13–gq-p17.
  - V1 never returns zero results, including on the two expected-empty queries.
  - V2 and V3 return nothing only for gq-p19 (`laptop 64gb ram`). That is the correct outcome:
    no product satisfies 64 GB RAM.
- **Out of catalog.** For gq-p20 (`smartwatch`), V1–V3 return 20 results, and every one is
  labelled `not_relevant` (irrelevant-result rate 1.0). No version abstains, apart from V0's
  empty result.

## 5. Most important regressions and likely causes

1. **V3 reranker vs V2.** Net NDCG@10 −0.0299: 4 queries down, 2 up.
   - The four losses:
     - gq-p16 −0.432 (noisy);
     - gq-p12 −0.078;
     - gq-p04 −0.069;
     - gq-p14 −0.065 (Hinglish).
   - The two gains: gq-p06 +0.069 and gq-p11 +0.006.
   - **gq-p16 dominates the deficit.** Without it, the mean V3 − V2 change over the same 19
     queries is about −0.007.
   - Likely cause: the cross-encoder scores query–document text similarity. On the misspelled
     `samsng` query, and on queries whose constraints the filter cannot enforce (processor,
     gender/subcategory), it reorders candidates without regard to the unenforced constraint.
     This is a hypothesis from the per-query pattern, not a measured cause.
   - CSR is unchanged, because the candidate set is unchanged.
2. **V2 vs V1 on gq-p13** (`50k ke andar gaming laptop`): 0.277 → 0.000.
   - The machine labels contain **no** relevant pair for this query: 3 borderline and 32
     not_relevant. V1's NDCG came from borderline items.
   - V2 applies the ≤ 50k price filter (CSR 1.0), and the gaming heuristic (a dedicated GPU)
     marks the remaining laptops not relevant.
   - This is a label-policy and catalog effect, not a demonstrated filtering bug. A human label
     is needed to tell which.
3. **V1 vs V0 constraint precision.** V1 adds results to every query, but violates hard
   constraints on gq-p04, gq-p09 and gq-p12, where V0 did not. V1 has no filters by design; V2
   fixes gq-p09 only, because qu-1 has no field for processor, gender or subcategory.
4. **Unextracted constraints** (gq-p04, gq-p06, gq-p12, gq-p16) are the largest remaining
   source of constraint violations in V2/V3 (§4).
5. **No abstention** for an out-of-catalog query (gq-p20) in V1–V3.

## 6. Provisional baseline (machine-label development conclusion)

- **Provisional baseline: V2 (`v2_filtered`, no reranker).**
  - It has the best macro NDCG@10, MRR@10 and Precision@10 on the machine labels.
  - It has the same CSR as V3, and it has no expected-non-empty zero results.
  - Its zero result on gq-p19 is the correct outcome.
- **Provisional challenger: V3 (`v3_reranked_d20`).**
  - Its deficit is small (−0.030 NDCG@10) and is driven by one noisy query.
  - The labels may favour the filtered versions, and the labels were never audited.
  - V3 should therefore be re-tested on the human-reviewed Golden Dataset before any decision.
- **Nothing is changed.**
  - No configuration changes: `search_rerank_depth`, `rrf_k`, the embedding model and the
    reranker pin stay as they are.
  - MASTER_PLAN M10 requires `rrf_k`, the embedding model and the reranker pin to be
    re-decided. They cannot be re-decided here: only the four pooled configurations were run.
  - Latency was not re-measured; see `docs/search-reranked-m8.md`.

## 7. M11 priorities (machine-label development conclusions)

MASTER_PLAN M11 is the Jev Decision Provider. Its prerequisites are not met: there is no
human-reviewed Golden Dataset and no credentials. CLAUDE.md asks for deterministic logic
wherever it suffices. The priorities below are therefore ordered deterministic-first:

1. **Resume strict M9 human annotation.** It gates the official M10 report, the V2-vs-V3
   decision and M11's own prerequisite. It is the only way to confirm any finding here.
2. **Deterministic query-understanding gaps.** Evidence: gq-p04, gq-p06, gq-p12, gq-p16 and
   gq-p17. The gaps are:
   - bare-number prices with a bound word (`under 15000`);
   - brand and category misspellings (`samsng`, `headfones`);
   - missing parser fields for processor, gender, subcategory and ANC.

   Each needs a versioned parser and lexicon change with its own tests. None needs Jev.
3. **Out-of-catalog and zero-eligible handling.** Evidence: gq-p20, and V1 on gq-p19. This is
   the planned Level-2 ambiguity and out-of-catalog detection. A deterministic catalog-category
   check is the first candidate; Jev is a later option behind the typed provider.
4. **Reranker robustness on noisy and unenforced-constraint queries.** Evidence: gq-p16,
   gq-p12, gq-p04 and gq-p14. Keep V3 as the challenger and measure it on human labels before
   changing depth or the pin.
5. **Hinglish coverage.** Evidence: gq-p13 and the three-query slice. The pilot is too small to
   act on; the held-out test partition (with its Hinglish slice) is the place to measure this.

## Limitations

- **Unaudited machine labels.** Label accuracy is unknown. Every number is a development
  diagnostic, not a quality estimate.
- **Possible bias towards V2/V3.** The labels mirror the gold constraints that V2 filters on.
  Retrieval consensus and the V3 cross-encoder only lowered label confidence, but they are
  related to the systems being compared.
- **Small, non-held-out sample.** These are 20 development pilot queries: 15 English, 3
  Hinglish and 2 noisy. Seven are saturated, and slices of 1–3 queries are anecdotes.
- **Pooled evaluation.** Recall is pooled recall, biased upward where more than 25 products are
  eligible (gq-p01, gq-p11, gq-p13, gq-p15).
- **Causes are hypotheses.** They are read from per-query metrics and the qu-1 extraction
  records, not from a new failure analysis. No significance test was run.
- **Analyses not re-run.** No label-subset (confidence) analysis was re-run. The committed
  per-pair `heuristic_confidence` and `requires_human_review` fields are available for it.
- **Not measured.** Latency was not measured. `rrf_k`, the embedding model and the reranker pin
  were not re-decided.

## Status

- **Strict annotation: 0/568 (when this analysis was committed, `73c67c1`).** Strict M9 was
  paused at 0/568 primary judgements. There was no primary seal, consistency round,
  adjudication, labels freeze, provenance unsealing or held-out test partition. *Update
  2026-10-17:* the human-reviewed pilot/dev Golden Dataset is now complete under `review-2`:
  568 frozen labels, provenance unsealed. No consistency round was run, by amendment. The
  held-out test partition remains deferred. Nothing in this document changes.
- **Weak-label audit: 0/100.** No active machine label was reviewed, accepted or overridden by a
  human. A later 100-entry rapid non-blind acceptance attempt (about 98 seconds, median gap 0.35
  seconds) did not establish substantive review; it was archived and excluded and changes nothing
  here.
- **Abandoned decisions: 2, archived and excluded.** They are not in any label, metric or
  statistic.
- **Provisional M10:** this document exists. It is a development analysis on machine labels only.
- **Official M10: not complete.** When this document was written, no report on the
  human-reviewed Golden Dataset existed and `docs/MASTER_PLAN.md` was unchanged. *Update
  2026-10-17:* a development-scoped pilot/dev report is a draft for review
  (`docs/m10-human-pilot-dev-evaluation.md`). Nothing in this document changes.
