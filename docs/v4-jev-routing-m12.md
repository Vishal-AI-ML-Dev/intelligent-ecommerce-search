# Experimental V4: Jev retrieval routing (Milestone 12, rule `jr-1`)

**Status: experimental, off by default.** M12 was closed as development-scoped, partially met
(owner decision, 2026-10-19; see `docs/m12-diagnostic-tooling.md`). V3 remains the baseline, V4
stays opt-in, and the confidence gate stays off.

- V4 is implemented, and its live Jev integration was exercised (three approved calls, below).
- **No relevance improvement was demonstrated.**
- The M12 calibration (`cal-protocol-1`, partition `cal-exp1`) found no candidate gate. V4 is
  therefore an **ungated experimental soft-routing arm, not a validated confidence gate**.
- The three live queries are not a quality or performance benchmark.
- The full original M12 acceptance (`docs/MASTER_PLAN.md`, Milestone 12) is **not**
  automatically satisfied by this work.

## What V4 does

`V4 = V3 + Jev retrieval routing`, served at `GET/POST /search/routed`
(`search_version: v4_jev_routed_experimental`). V0–V3 routes, behaviour and defaults are
unchanged.

1. V3's steps run unchanged: normalization, deterministic understanding (`qu-1`), fp-1 filters.
2. Jev is asked **once** only when all of these hold:
   - `SEARCH_V4_JEV_ENABLED=true`;
   - the query has a letter or digit;
   - the parser gives no category;
   - the parser reports no category conflict. A conflict is never resolved by a model.

   The call uses the existing M11 `JevDecisionProvider`: pinned `jev-1.13.0`, retries 0, one call
   in flight, bounded by `JEV_DEADLINE_SECONDS`. It happens before any model or database work.
3. If Jev returns a valid outcome whose unique top category is not `none`, V4 adds one branch:
   - It reads the same lexical and dense sources with the **deterministic fp-1 filters plus
     Jev's category**, so every deterministic constraint (brand, RAM, storage, price) is kept.
   - The V3 sources are read unchanged, with the V3 filters, in the same read-only snapshot.
4. The four lists are fused with the unchanged RRF formula, equal weights and the configured
   `rrf_k` and `candidate_k`.
   - A product found by both the V3 sources and the branch sums its terms, so the branch acts
     as a soft boost and can add branch-only candidates.
   - The existing reranker then reranks the head exactly as in V3.
   - Jev's category is **never** a filter on the combined result and never appears in
     `applied_filters`.
5. In every other case V4 runs exactly the V3 path and returns the V3 ranking: routing off, Jev
   not asked, a handled Jev fallback (timeout, auth, rate limit and so on), `none` or a tie.
6. Jev's semantic intent is not used. It remains informational, as under fp-1.

The response is the V3 body plus:
- `jev_routing`: `experimental`, `gate: none_validated`, `status`, `jev_called`, `routing_used`,
  `routed_category`, `fallback_reason`, `model`, `jev_latency_ms`, `routed_filters`, branch hit
  counts, and `raw_top_category_probability`. The probability is recorded only and never used.
- per result: `routed_lexical_rank`, `routed_dense_rank` and `in_baseline_sources`.
- `latency_ms.jev_decision_ms`.

Failures:
- **Return V3, with `jev_routing.status = jev_fallback` and a fixed `fallback_reason`:**
  - every runtime call failure the M11 provider handles (`timeout`, `transport_error`,
    `auth_error`, `rate_limited`, `server_error`, `request_rejected`, `invalid_response`,
    `model_mismatch`, `deadline_exceeded`, `saturated`, `provider_closed`);
  - a provider that cannot be obtained: `configuration_invalid` (`JevConfigurationError`),
    `sdk_unavailable` (`ImportError`) or `provider_closed` (application shutting down).
- **Fixed 503 (`search unavailable`):**
  - every V3 failure (as in `/search/reranked`);
  - a provider result that is not a valid Jev `DecisionResult` for the query
    (`jev_provider_error`).
- **Not caught:** any other exception is a programming error.
- Keys, headers and query text are never returned or logged.

## Opt-in and start-up

- `SEARCH_V4_JEV_ENABLED` (default `false`) is independent of M11's `JEV_ENABLED`.
- When it is true, settings validation requires `TYPESAFE_API_KEY` (from the environment or
  `.env`, through `Settings` only) and `JEV_MODEL=jev-1.13.0`.
- The provider is built by the first request that needs Jev, never at start-up, and is closed
  on shutdown.
- With the flag on, `/search/routed` makes **live, paid** calls; nothing else does.

Start-up with V4 routing on:

```powershell
$env:SEARCH_V4_JEV_ENABLED = "true"   # TYPESAFE_API_KEY must already be in the environment or .env
uv run --offline uvicorn ecommerce_search.api.app:create_app --factory
```

## Offline comparison (recorded outcomes, no Jev call)

```powershell
uv run --offline python -m evals.runners.compare_v3_v4 `
  --batch data/processed/m12_diagnostic live-001 `
  --batch data/processed/m12_calibration_cal1 cal1-live-001 `
  --golden --out <new file>
```

- V3 and V4 run in one process through the real app, against the configured catalog database
  and local models, with identical settings, queries and filters.
- V4's Jev provider replays the outcomes recorded in those runs. It refuses anything
  unrecorded.
- Rows are labelled as follows:
  - `diagnostic_only`: the M12 queries. They have category labels but no relevance judgements;
    the row compares gold-category counts in the top 10, which is not relevance.
  - `judged`: the M9/M10 pilot/dev queries. NDCG@10 and strict precision@10 are reported with a
    per-query win/loss/tie, and unjudged V4 results are counted.
  - `not_compared`: a judged query on which V4 would ask Jev but no recorded outcome exists.

## Offline comparison result (2026-10-19, recorded outcomes)

Command above, run once, `--top-k 20`, with the configured local catalog database and models.
103 recorded outcomes were replayed; no Jev call was made.

| Set | Queries | Routing status | Rankings changed (top 20) | Notes |
|---|---|---|---|---|
| M12 `diagnostic_only` (40 `live-001` + 80 `cal1-live-001`) | 120 | 58 routed, 45 Jev `none`/tie, 15 deterministic category, 2 category conflict | 21, all routed (19 within the top 10) | 16 branch-only products entered a top 20; 164 products entered a top 20 in all |
| M9/M10 pilot/dev `judged` | 20 | 17 deterministic category, 3 not compared | 0 | NDCG@10: 17 ties (by construction); gq-p17, gq-p18 and gq-p20 had no recorded outcome |

- Of the 62 diagnostic queries with a single-category gold, V4's top 10 holds more gold-category
  products for 12 and the same number for 50, and never fewer. This is category agreement, not
  relevance.
- 3 of the 43 routed queries with a single-category gold were routed to a category other than
  the gold.
- Output: a scratch file outside the repository, SHA-256
  `06accfe95c7d177b27f1f3a13fb0485d5b812ae9cc94792adea652f8abe155bc`. It is not kept as evidence
  and can be regenerated with the command above, given the same database and models.

## Live comparison (2026-10-19, three approved Jev calls)

**Scope:** the three judged pilot/dev queries on which V4 can differ from V3 (gq-p17, gq-p18,
gq-p20), one Jev HTTP attempt each.

**Setup:**
- Pricing was rechecked first at https://docs.typesafe.ai/models: $0.042 per million input
  tokens, output free. The page shows no last-updated date.
- Endpoint `https://api.typesafe.ai`, model `jev-1.13.0`, retries 0, one call in flight.
- The application ran in-process (`TestClient`, no network listener). The V4 opt-in was passed
  only to that run's `Settings` object and was never persisted; it reads `false` again
  afterwards.
- A guard counted each attempt before the call and refused beyond 3 attempts or at an
  estimated $0.01. That tripwire is not a billing cap.

| Query | Jev category (raw top probability) | Routing | Ranking difference (top 20) | Judged NDCG@10 V3 / V4 |
|---|---|---|---|---|
| gq-p17 `noise cancelling headfones` | `headphones` (1.0) | used; branch 0 lexical / 50 dense hits | none: no product added or displaced | 1.0 / 1.0 (tie) |
| gq-p18 `apple` | `none` (0.76; `phone` 0.23) | not used: V3 returned | none | 1.0 / 1.0 (tie) |
| gq-p20 `smartwatch` | `none` (0.99) | not used: V3 returned | none | undefined for both (no strict-relevant judgement exists) |

- **Unjudged results:** none in either top 10, so nothing had to be treated as unjudged.
- **Attempts:** exactly 3, all HTTP 200, no fallback, no resend.
- **Usage:** 639, 634 and 637 input tokens and 141, 139 and 139 output tokens.
- **Estimated spend:** $0.00008022, from returned usage; not billing data.
- **Jev latency (provider-measured):** 1588, 342 and 319 ms. The first includes provider
  start-up.
- **Whole-request latency, V3 / V4:**
  - gq-p17: 6638 / 1999 ms. The V3 request loaded the models first, so this pair is not
    comparable.
  - gq-p18: 498 / 683 ms.
  - gq-p20: 322 / 656 ms.
  These are single, warm-or-cold, unrepeated local measurements: not latency evidence.
- **Conclusion:** on the only judged queries where V4 can differ, it changed no ranking. Jev
  answered `none` twice, and the one routed branch added nothing new.

**Evidence** (git-ignored, `data/processed/m12_v4_live_1/`, SHA-256):

| File | SHA-256 |
|---|---|
| `comparison.json` | `8d199cba598ef663a46bce8825842c46f83e4458005c355158b5e1086ff763d2` |
| `gq-p17-v3.json` | `e4f0143b850f1f4038379e3c5e80e75d2423593752260c2037dd7610f0d6b794` |
| `gq-p17-v4.json` | `6cf214f1b35ac9ae2906f1313a670ad62618cf28eb25b86f92b1c2b2bdabb2b1` |
| `gq-p18-v3.json` | `9cedecbdf7444e74ee6005fd5d2d3fb03cbc8df2fe8471c94d97a05f6cbe4434` |
| `gq-p18-v4.json` | `7366820685ac7675b229b92577104d4efa5eea976e5180f5c4079e49628c517c` |
| `gq-p20-v3.json` | `f229c2c76811572e209d743cfcec8479b704aebf124ad304d54b9ddf2ee7b74a` |
| `gq-p20-v4.json` | `3d6c5199f6ecb8b45d75ae8c3432ce86011c46acd504dd582dd263a6363e41bc` |
| `jev-outcomes.json` (request ids removed) | `be5a497e6517169e6435091e9bdb8b5ef693a9eb78e4f1da7f8787a439d41e50` |

The replayed run journals: `live-001`
`5cfbfc692445904369a277c766da0aa7e49f02e3b86fdc446f4613d5e7f49807` and `cal1-live-001`
`f847770173b6413ffb1860394a01d24cceda026b26473810fea7955bb61623f7`.

## Evaluation gaps

- **No relevance improvement is shown.** Of the 20 judged pilot/dev queries, 17 equal V3 by
  construction, and the 3 live ones tied or were undefined.
- The M12 queries have no relevance judgements. Their comparison shows ranking change and
  category agreement only.
- Every M12 query is AI-authored. Labels are by one annotator, with AI assistance. None of this
  is held-out evidence.
- Products new to V4's results would need human judgements before any relevance claim.
- No calibrated gate exists, and no medium/low confidence bands are implemented.
- There are no latency or cost measurements under load and no repeat-stability data.
