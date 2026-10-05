# Milestone 9 — Internal Golden Dataset (search relevance)

M9 builds human-reviewed relevance evidence for the frozen V0–V3 search versions:

```text
Query -> candidate product -> human relevance grade
```

M9 makes no claim that any search version is better. Milestone 10 compares V0–V3 on these labels.

## Status

| Session | Scope | State |
|---|---|---|
| B | Typed contracts, canonical serialization, manifest lifecycle, validators (`evals/contracts/`) | Committed and pushed (`15f3ac1`) |
| C1 | Pilot queries, gold constraints, eligibility evaluator, pilot validators, queries-only freeze runner | Committed and pushed (`6009fa3`). `queries_frozen` was frozen by `ann-1` and verified (see "Frozen `queries_frozen` stage") |
| C2 | V0–V3 candidate retrieval, deterministic pool, private provenance, `pool_frozen`, blind annotation CLI, primary-seal tooling, retrieval-metric evaluator | Complete and committed. `pool_frozen` was generated twice byte-identically, frozen and verified (see "Session C2") |
| Later | Human primary annotation, primary seal, 72-hour consistency round, adjudication, labels freeze, test partition, M10 metrics | Not started |

The frozen pool exists. No judgement, label, consistency sample or metric-result file exists, and
the 72-hour consistency clock has not started.

### Frozen `queries_frozen` stage

| Fact | Value |
|---|---|
| Stage | `queries_frozen` (golden version 1) |
| Confirmed and frozen by | `ann-1` (the human annotator ran the `freeze` command) |
| Frozen at (`confirmed_at` / `recorded_at`) | `2026-10-05T05:28:59.470827Z` |
| Manifest SHA-256 | `51204daec32af33f15539b746adea4e809bf027ffa21f76ffaa8a2dbafcefd67` |
| Input SHA-256 (`authoring/pilot_input.json`) | `a122f11568e8b9bb9a7e712ab5f2c6f5b42e1a36405617dbea4e351c9718e19f` |
| Partition | `pilot_dev` |
| Records | 20 queries, 51 gold constraints, 1 product intent, 52 query-extraction records, 1 authoring attestation |
| Verification | `uv run --frozen python -m evals.runners.freeze_queries verify` passed |

The recorded `authoring_attestation.json` discloses:

- `drafted_by`: `ai_assistant`. The pilot was drafted by an AI assistant.
- `confirmed_by`: `ann-1`. It was reviewed and confirmed by the human annotator.
- `blind_to_parser_output`: `false`. It was not authored blind to the Session A qu-1/fp-1
  inspection.
- `purpose`: `development_rubric_calibration`.
- `final_test_evidence`: `false`. This is development/rubric-calibration evidence, not final
  held-out test evidence.

## Approved decisions

These decisions were approved before implementation. They override the generic M9 text in
`MASTER_PLAN.md`.

- **Labels (D1–D3).** Labels are `relevant` / `borderline` / `not_relevant`, with a separate
  `relation` facet.
  - NDCG gains are 2 / 1 / 0. Strict binary relevance is primary and lenient is secondary.
  - A complement is `not_relevant` with relation `complement`.
- **Blind annotation (D4).** Sources, ranks, scores and parser output are hidden while
  labelling.
- **Single annotator (D5).** Exactly one human annotator, `ann-1`.
  - The primary round is sealed first.
  - A 72-hour-delayed intra-annotator consistency check re-labels `ceil(20% × N)` items.
  - No inter-annotator agreement is claimed. Required disclosure: *"The Golden Dataset was
    reviewed by one human annotator; no independent inter-annotator agreement is claimed."*
  - AI is never an annotator.
- **Pilot vs test (D6, D13).** The 20-query pilot is development / rubric-calibration data and
  is never final test evidence.
  - The held-out test partition is authored separately later and sealed.
  - It will have a separately reported Hinglish slice, provisionally at least 20%.
  - No pilot query and no `seen_in_development` query may enter it.
- **Gold constraints beyond fp-1 (D7).** Gold constraints may name fields fp-1 cannot filter.
  `fp1_filterable` is validated per field, never chosen freely.
- **Hidden attributes (D8).** Availability, rating, review count and seller are hidden during
  annotation.
- **Pool policy `pool-1` (D9), implemented in C2.** Depth 20, eligibility completion of at most
  25, 4 deterministic negatives, and a hard cap of 60 per query.
  - Over the cap: annotation export is refused and the pool is never trimmed.
- **Provenance (D12).** Only its checksum is committed until labels freeze.
- **Session C1 decisions (2026-10-05):**
  1. The pilot table is AI-drafted and confirmed by `ann-1`. The authoring attestation records
     this (below).
  2. Semantic intent, price preference and exact series/model intent are typed soft signals.
  3. In `women running shoes`, gender=women is a strict hard constraint. Unisex does not satisfy
     it; the rubric may still allow `borderline`/`substitute`.
  4. No contradictory-constraint query is added. The gap is disclosed below.
  5. `rubric-1` is declared at `queries_frozen`. A rubric change after freezing creates a new
     golden version; version 1 is never mutated.
  6. Eligibility completion (C2) is all-or-nothing:
     - it runs only for queries with at least one hard constraint and at most 25 products
       satisfying all of them;
     - it then adds every missing satisfied product;
     - a larger eligible set is never truncated.
  7. `seen_in_development` is true for the five texts with repository evidence (below).

## The 20-query pilot

Source: `evals/goldens/internal-search/v1/authoring/pilot_input.json` (the AI draft, with no
annotator handle or timestamp). "Hard-eligible" counts products in the committed seed catalog
(`synthetic-seed` v1, 240 products) that satisfy every hard constraint.

| ID | Query | Style | Family | Seen | Hard gold constraints | Soft / series intent | Hard-eligible |
|---|---|---|---|---|---|---|---|
| gq-p01 | headphones | english | broad_category | yes | category=headphones | — | 50 |
| gq-p02 | dell laptop | english | brand_category | yes | laptop, brand Dell | — | 12 |
| gq-p03 | adidas shoes | english | brand_category | no | shoes, brand Adidas | — | 10 |
| gq-p04 | lenovo lumen ryzen 7 laptop | english | exact_product | no | laptop, brand Lenovo, processor AMD Ryzen 7 | series=Lumen (exact series intent) | 1 (`SYN-LAP-0003`) |
| gq-p05 | phone under 20k | english | price | no | phone, price ≤ 20000.00 | — | 19 |
| gq-p06 | bose headphones under 15000 | english | price | no | headphones, brand Bose, price ≤ 15000.00 | — | 7 |
| gq-p07 | laptop 16gb ram 512gb ssd | english | specification | no | laptop, RAM 16, storage 512, storage_type SSD | — | 14 |
| gq-p08 | 1tb nvme laptop | english | specification | no | laptop, storage 1024, storage_interface NVME | — | 20 |
| gq-p09 | 12gb ram phone 256gb storage | english | specification | no | phone, RAM 12, storage 256 | — | 6 |
| gq-p10 | hp laptop 8gb 256 ssd under 40k | english | combined | yes | laptop, brand HP, RAM 8, storage 256, SSD, price ≤ 40000.00 | — | 1 |
| gq-p11 | gaming laptop | english | semantic_intent | yes | laptop | semantic_intent=gaming | 80 |
| gq-p12 | women running shoes | english | specification | no | shoes, subcategory running, gender women | — | 6 |
| gq-p13 | 50k ke andar gaming laptop | hinglish | combined | no | laptop, price ≤ 50000.00 | semantic_intent=gaming | 36 |
| gq-p14 | sasta phone 8gb ram wala | hinglish | specification | no | phone, RAM 8 | price_preference=low | 18 |
| gq-p15 | office ke liye laptop 16gb ram | hinglish | semantic_intent | no | laptop, RAM 16 | semantic_intent=office | 27 |
| gq-p16 | samsng phone 128gb | noisy | combined | no | phone, brand Samsung, storage 128 | — | 3 |
| gq-p17 | noise cancelling headfones | noisy | specification | no | headphones, anc=true | — | 20 |
| gq-p18 | apple | english | ambiguous | yes | brand Apple (no category) | — | 21 |
| gq-p19 | laptop 64gb ram | english | zero_eligible | no | laptop, RAM 64 | — | 0 |
| gq-p20 | smartwatch | english | out_of_catalog | no | none | — | n/a |

The mix is 15 English, 3 Hinglish and 2 noisy queries. Every query is `pilot_dev`.

Query-specific rules (enforced by `evals.contracts.pilot.validate_pilot`):

- **gq-p04.** Category, brand and processor are hard. *Lumen* is a typed series intent
  (`ProductIntent`, kind `series`), not semantic intent and not a hard constraint.
  - Only `SYN-LAP-0003` satisfies the hard constraints. The other three Lumen laptops (i3/i5)
    are not hard-eligible.
- **gq-p08.** The catalog represents NVMe as the existing typed laptop field
  `storage_interface` (`StorageInterface.NVME`). There is no generic `interface` field.
  - The taxonomy states that NVME *requires* an SSD medium (enforced by `LaptopSpecData`), but
    the query does not state SSD, so no `storage_type` constraint is added.
- **gq-p12.** Strict: shoes + running + women. Comfort is not a gold requirement.
- **gq-p14.** *Sasta* is a soft low-price preference. No numeric maximum price is invented.
- **gq-p18.** Brand only, with no forced category. Apple laptops and Apple phones are both valid
  interpretations.
- **gq-p20.** An out-of-catalog category intent. No catalog category exists, so no constraint is
  recorded, no fake category is created, and no product is hard-eligible. The query record's
  notes explain this.

### Disclosures

- **Authorship.** The pilot query texts and gold records were **drafted by an AI assistant** and
  are **reviewed and confirmed by the human annotator `ann-1`**.
  - They were **not authored blind** to the Session A qu-1/fp-1 inspection.
  - They are development/rubric-calibration data only and are **not final held-out test
    evidence**.
  - The recorded `authoring_attestation.json` states all of this and binds the SHA-256 of the
    exact reviewed input.
- **Hinglish coverage is limited.** The pilot has only 3 Hinglish queries (p13–p15).
- **No contradictory-constraint query.** The pilot has none. The held-out test partition must
  cover this gap when it is planned. The current contracts also refuse a minimum price above a
  maximum price.
- **Broad queries.** For gq-p01, gq-p11, gq-p13 and gq-p15 the hard-eligible set is larger than
  25, so pooled recall will be biased (`docs/spec.md` §9.2).

### `seen_in_development` evidence

The flag is true exactly where the text was used verbatim as a search query in committed M0–M8
code, tests or docs (checked by an exact quoted-string search on `15f3ac1`). Synthetic
Golden-contract fixtures in `tests/unit/golden/` do not count.

| ID | Text | Evidence |
|---|---|---|
| gq-p01 | `headphones` | search query in `tests/integration/test_reranked_retrieval.py`; tsquery input in `tests/integration/test_search_characterization.py` |
| gq-p02 | `dell laptop` | `tests/unit/search/test_decision_provider.py` |
| gq-p10 | `hp laptop 8gb 256 ssd under 40k` | `docs/MASTER_PLAN.md`, `test_decision_provider.py`, `test_query_understanding_contract.py`, `test_query_understanding_rules.py` |
| gq-p11 | `gaming laptop` | `tests/unit/search/test_reranked_api.py` |
| gq-p18 | `apple` | `tests/integration/test_reranked_retrieval.py`, `tests/integration/filter_support.py` |

## Gold constraints and soft signals

Records in `evals/contracts/models.py`:

- **`GoldConstraint`** has a field, an operator (`eq`, or `lte`/`gte` for price), a typed value,
  a hardness, `fp1_filterable`, `annotator_handle` and `recorded_at`.
  - Hard fields: category, brand, ram_gb, storage_gb, storage_type, storage_interface, price,
    processor, subcategory, gender, anc, wireless, connectivity.
- **Soft-only fields** are refused as hard and are never fp-1-filterable. Their vocabularies are
  qu-1's closed sets:
  - `semantic_intent`: gaming, coding, student, lightweight, premium, comfort, travel, office;
  - `price_preference`: low.
- **`ProductIntent`** is an exact series/model intent (kinds `series`, `model_code`). Its value
  must be a token of some catalog title. It never feeds eligibility.

### Filter support vs. qu-1 extraction

These are two separate facts:

1. `fp1_filterable` (on `GoldConstraint`, per field): fp-1 *could* enforce the field once
   supplied.
2. `QueryExtraction` (generated, `query_extraction.jsonl`): what qu-1 *actually extracted* from
   this exact query, and whether fp-1 actually applied a filter.
   - `status` is one of `extracted`, `extracted_other_value`, `not_extracted` or
     `no_parser_field`.
   - It also records `parsed_value` and `fp1_applied`.

Extraction gaps visible in the pilot (from the deterministic code; regenerated by `verify`):

- **gq-p06:** price is fp-1-filterable, but qu-1 does not parse `under 15000` → `not_extracted`,
  not applied.
- **gq-p16:** brand (`samsng`) and storage (`128gb`, a bare-capacity ambiguity) →
  `not_extracted`.
- **gq-p17:** category (`headfones`) → `not_extracted`.
- **`no_parser_field`:** processor (p04), subcategory and gender (p12), anc (p17) and the Lumen
  series intent. They are never presented as parser output.
- **Soft signals qu-1 does extract:** `gaming` (p11, p13), `office` (p15), `sasta` → low (p14).

Only qu-1's values for gold items are recorded. Extra parser output with no gold counterpart is
not recorded in C1.

## Eligibility (`evals/contracts/eligibility.py`)

A pure, deterministic catalog fact. It is **not relevance** and creates no label.

- **Only hard gold constraints take part**, whether or not they are fp-1-filterable. Soft
  signals and series intents never do.
- **Status per constraint:**
  - `satisfied` / `violated`;
  - `unknown`: the attribute exists for the category but the value is missing;
  - `not_applicable`: the attribute cannot exist for the product, e.g. RAM on shoes, or a shoe
    subcategory on a laptop. It counts as a violation overall.
- **Matching is literal:**
  - exact brand, category, processor and integer capacities;
  - storage medium and interface are compared separately;
  - price bounds are inclusive.
- **Separate overalls.** `hard_overall` and `fp1_overall` (fp-1 fields only) are computed
  separately.
- **Separate counts.** `summarize` reports unknown and not-applicable evaluations separately.
  In the committed seed, none of the pilot's hard constraints has an unknown value.

## Artifacts and the `queries_frozen` stage

Under `evals/goldens/internal-search/v1/`:

| File | Kind | Written by |
|---|---|---|
| `authoring/pilot_input.json` | AI draft (not a manifest entry; its SHA-256 is bound in the attestation) | C1 |
| `queries.jsonl` | recorded | `ann-1`'s freeze |
| `gold_constraints.jsonl` | recorded (handle `ann-1`, `recorded_at` = confirmation time) | `ann-1`'s freeze |
| `product_intents.jsonl` | recorded | `ann-1`'s freeze |
| `authoring_attestation.json` | recorded | `ann-1`'s freeze |
| `query_extraction.jsonl` | generated (no time, handle or path; byte-identical on regeneration) | `ann-1`'s freeze |
| `manifests/1-queries_frozen.json` | the stage manifest | `ann-1`'s freeze |

**Manifest contents.** The manifest binds every file's SHA-256 and the catalog dataset
identity. It also records the versions:

- `rubric-1`, `pool-1`, `blind-1`, `cs-1`;
- parser `qu-1`, lexicon `1`, filter policy `fp-1`;
- the embedding and reranker model pins.

These identity fields cannot change in later stages. The `queries_frozen` stage now has five
roles: queries, gold constraints, product intents, query extraction and authoring attestation.

**Freeze runner** (`evals/runners/freeze_queries.py`). It validates everything before writing:
- query, gold-constraint and product-intent validators against the catalog;
- the attestation;
- the regenerated extraction;
- the pilot rules.

Write safety:
- It refuses `..`, paths outside `evals/goldens/`, and symlink or junction components.
- It refuses any existing target, so nothing is ever overwritten.
- It stages and fsyncs every file, then hard-links each into place with exclusive creation,
  manifest last.
- Any failure removes everything that run created.
- It writes no pool, provenance, label or metric file.

`verify` is read-only. It re-checks the manifest, every entry's bytes, the regenerated
extraction, the input binding and every validator.

### Human confirmation

The freeze command is `ann-1`'s own statement of review. An AI assistant must never run it.

```text
uv run python -m evals.runners.freeze_queries preview
uv run python -m evals.runners.freeze_queries freeze --annotator ann-1 \
    --confirm-human-review --input-sha256 <sha256 printed by preview>
uv run python -m evals.runners.freeze_queries verify
```

`--input-sha256` binds the confirmation to the exact input bytes reviewed. A changed input is
refused.

## Protected baseline

Session C protected-path checks compare against the Session C base `15f3ac1`. C1 leaves these
unchanged: `src/`, `migrations/`, `data/seed/`, `uv.lock`, the Docker/Compose files and
`pyproject.toml`.

`558bd39` is only the M8 V3 production commit pinned by the M8 latency harness. It is not the
Golden Dataset baseline.

## Limitations

- **Single human annotator.** The pilot is development data; see the disclosures above.
- **Synthetic catalog.** The catalog is synthetic, and hard-eligibility counts describe it only.
- **AI-drafted input.** The recorded pilot is the AI draft as confirmed by `ann-1` in one step
  (one confirmation time for every gold record). It is not an independently human-authored set.
- **Symlink coverage on Windows.** The symlink-refusal test that needs a real symlink is skipped
  where the OS account cannot create one. The refusal logic is still exercised through patched
  path checks.

## Session C2: candidate pool, annotation tooling and evaluator

### Pool policy `pool-1` as implemented

`evals/contracts/pooling.py` (pure) builds each query's pool in this order. The result is
independent of input order, uses no random sampling, and creates no label.

1. **Source union.** Six sources, each called once per query at `top_k = 20`
   (`POOL_SOURCE_TOP_K = POOL_DEPTH`). Results are deduplicated by product id.

   | System | Endpoint | Score recorded (private) |
   |---|---|---|
   | `v0_lexical` | `/search` | `lexical_score` |
   | `dense_only` | `/search/dense` | `dense_score` |
   | `v1_hybrid` | `/search/hybrid` | `rrf_score` |
   | `v2_filtered` | `/search/filtered` | `rrf_score` |
   | `v3_reranked_d10` | `/search/reranked`, reranker depth 10 | `rerank_score` (null beyond the depth) |
   | `v3_reranked_d20` | `/search/reranked`, reranker depth 20 | `rerank_score` |

2. **Eligibility completion** (all-or-nothing, C1 decision 6). It runs only for a query with at
   least one hard constraint and 1–25 hard-eligible products, and adds every eligible product
   not already pooled.
   - It never runs for zero eligible, more than 25 eligible, or no hard constraint.
   - It never uses semantic intent, price preference or series intent.
3. **Deterministic negatives.** Four per query, chosen from products not yet pooled, in
   `sha256("pool-1|query_id|product_id")` order:
   - two **same-category**: in the hard gold category, violating another hard constraint;
   - two **other-category**: outside it, or any product when the query has no hard category.

   Further rules:
   - Only products whose hard status is `violated` qualify. Eligible and unknown products never
     do.
   - A query with no hard constraint (`gq-p20`) uses ordinary catalog products as
     out-of-catalog probes.
   - A short tier is filled from the other tier. Anything still missing is reported as a
     shortfall.
   - The reason a product is a negative stays private.
4. **Blind order.** `sha256("blind-1|query_id|product_id")`, independent of sources and
   scores.
5. **Cap.** At most 60 per query. Over the cap, nothing is frozen and nothing is trimmed, and
   only aggregate counts are reported (`PoolCapExceeded`).

### Contract extensions (Session C2)

- **`card-1` product card** (`evals/contracts/cards.py`). The exact annotation projection:
  - product id, title, description, category, subcategory, brand, price, currency and the
    category attributes;
  - never seller, availability, rating or review count.

  `PoolItem` gained `card_sha256` (the card hash) next to `product_content_sha256` (the
  catalog record hash). Both are re-checked against the catalog before annotation.
- **`PoolProvenance`** (role `provenance`) is now **one canonical JSON object**, replacing
  `ProvenanceEntry` JSONL. It holds:
  - the deterministic run context: queries manifest, catalog identity, approved settings,
    model pins with config hashes, component and library versions, and the committed runtime
    tree ids;
  - every source run: endpoint, search version, ranked product ids, applied and ignored
    filters, parser and filter-policy versions, dense status, and reranker mode;
  - the per-product entries: inclusion reason plus each source's rank and score.

  `run_id` is the first 16 hex digits of the context's SHA-256. It contains no time, path or
  database name.
- **`PoolReport`** (new generated role `pool_report`, added at `pool_frozen`). Per-query
  aggregate counts only.

### Frozen `pool_frozen` stage

| Fact | Value |
|---|---|
| Manifest | `manifests/2-pool_frozen.json`, `manifest_sha256` `4c26628ac45cd8dd1c31411e586df2c518c492ffbcd95145d19c1965a67e5897` |
| Previous manifest | `queries_frozen` `51204daec32af33f15539b746adea4e809bf027ffa21f76ffaa8a2dbafcefd67` |
| Public entries added | `pool.jsonl` (568 items), `eligibility.jsonl` (568 records), `pool_report.json` |
| `provenance_binding` | `fc6526f9863ede2eb48b3fe3162dfc804b7138f21e26c8c7576d70f704a078a8` |
| Private provenance | `data/processed/golden_pool/045a73ed83ebf400/provenance.json` (git-ignored; never committed before `provenance_unsealed`) |
| Determinism | two independent runs were byte-identical for the pool, eligibility, report and provenance. Each was a fresh process on a fresh scratch database, migrated, seeded and embedded. A third fresh run (`regenerate`) was also byte-identical |

The private provenance exists only on the machine that generated it. A clean checkout can verify
the public files (`verify --public-only`), but it cannot verify or unseal the provenance without
those exact bytes.

Aggregate pool composition, from `pool_report.json`. Source counts are the results returned per
source, in the order V0 / dense / V1 / V2 / V3-d10 / V3-d20:

| Query | Source results | Union | Completion | Added | Negatives same / other / shortfall | Pool |
|---|---|---|---|---|---|---|
| gq-p01 | 20/20/20/20/20/20 | 31 | over cap (50 eligible) | 0 | 0/4/0 | 35 |
| gq-p02 | 12/20/20/12/12/12 | 20 | applied (12) | 0 | 2/2/0 | 24 |
| gq-p03 | 10/20/20/10/10/10 | 20 | applied (10) | 0 | 2/2/0 | 24 |
| gq-p04 | 1/20/20/12/12/12 | 22 | applied (1) | 0 | 2/2/0 | 26 |
| gq-p05 | 0/20/20/19/19/19 | 29 | applied (19) | 0 | 2/2/0 | 33 |
| gq-p06 | 0/20/20/10/10/10 | 20 | applied (7) | 0 | 2/2/0 | 24 |
| gq-p07 | 14/20/20/14/14/14 | 28 | applied (14) | 0 | 2/2/0 | 32 |
| gq-p08 | 20/20/20/20/20/20 | 33 | applied (20) | 0 | 2/2/0 | 37 |
| gq-p09 | 6/20/20/6/6/6 | 24 | applied (6) | 0 | 2/2/0 | 28 |
| gq-p10 | 0/20/20/1/1/1 | 20 | applied (1) | 0 | 2/2/0 | 24 |
| gq-p11 | 7/20/20/20/20/20 | 22 | over cap (80 eligible) | 0 | 0/4/0 | 26 |
| gq-p12 | 6/20/20/20/20/20 | 20 | applied (6) | 0 | 2/2/0 | 24 |
| gq-p13 | 0/20/20/20/20/20 | 31 | over cap (36 eligible) | 0 | 2/2/0 | 35 |
| gq-p14 | 0/20/20/18/18/18 | 32 | applied (18) | 0 | 2/2/0 | 36 |
| gq-p15 | 0/20/20/20/20/20 | 35 | over cap (27 eligible) | 0 | 2/2/0 | 39 |
| gq-p16 | 0/20/20/20/20/20 | 20 | applied (3) | 0 | 2/2/0 | 24 |
| gq-p17 | 0/20/20/20/20/20 | 20 | applied (20) | 0 | 2/2/0 | 24 |
| gq-p18 | 20/20/20/20/20/20 | 21 | applied (21) | 0 | 0/4/0 | 25 |
| gq-p19 | 0/20/20/0/0/0 | 20 | zero eligible | 0 | 2/2/0 | 24 |
| gq-p20 | 0/20/20/20/20/20 | 20 | no hard constraints | 0 | 0/4/0 | 24 |

Every pool is within the cap (largest 39), and there were no negative shortfalls.

Completion was applicable for 14 queries but added no product: every hard-eligible product of
those queries was already in the source union. Completion's adding behaviour is covered by unit
tests on synthetic sources.

### Pool commands

```text
uv run --offline python -m evals.runners.build_pool generate     # two runs, compare, freeze
uv run --offline python -m evals.runners.build_pool verify       # includes the private provenance
uv run --offline python -m evals.runners.build_pool verify --public-only
uv run --offline python -m evals.runners.build_pool regenerate   # one fresh run, compare only
uv run --offline python -m evals.runners.build_pool report       # aggregate counts only
```

`generate` and `regenerate` need the project PostgreSQL and the pinned local model snapshots.
They use and drop `ecommerce_search_bench_<12 hex>` scratch databases only. They refuse:
- unapproved settings;
- an unverified model snapshot;
- a dirty runtime tree (`src/`, `migrations/`, `data/seed/`, `uv.lock`, `alembic.ini`);
- outbound network access.

### Blind annotation CLI (`evals/annotation/`)

```text
uv run python -m evals.annotation status
uv run python -m evals.annotation annotate --annotator ann-1
uv run python -m evals.annotation validate
```

What it reads:
- It reads only a verified `pool_frozen` stage: public files only, never the provenance.
- It re-checks every product's content and card hash, so annotation fails if a product
  changed.

What it shows:
- one pair at a time, by query and then blind position;
- the query, its interpretations, the annotator's own gold constraints and product intents,
  and the `card-1` card.

What it never shows: source, rank, score, inclusion reason, parser output, filters, seller,
availability, rating, review count or eligibility.

How labelling works:
- The annotator types and confirms the label (`r` / `b` / `n`) and its allowed relation,
  confidence, rationale, `needs_adjudication` and `catalog_issue`. Nothing is prefilled or
  suggested.
- A rationale is required for `borderline`, low confidence, and `relevant` with an unknown hard
  attribute.
- `relevant` is refused for a product that violates a hard gold constraint.

Storage:
- The working file is
  `data/processed/golden_annotation/<pool manifest prefix>/primary_judgements.jsonl`
  (git-ignored). It is bound to the pool by `binding.json`.
- It is atomically replaced after every judgement, and resuming re-validates it.
- These are refused: duplicates, foreign handles, unknown pairs, records outside the time
  window, a wrong rubric, and a binding to another pool.

Binding time:
- `binding.json` records a UTC `created_at` once, when annotation work is initialized. It is
  never changed on resume.
- Every judgement must be at or after both the query confirmation time and `created_at`;
  equality is allowed. `validate`, `seal_primary check` and `seal` all enforce this.
- Naive, non-UTC or malformed timestamps are refused.
- This protects against accidental or pre-seal backdating only. It is **not** cryptographic
  tamper-proofing: the working file and binding are ordinary local files until the annotator
  seals the round, and the seal then binds the exact bytes by SHA-256. The pool, content and
  card hash checks remain mandatory.

Stopping:
- Type `q` at any prompt to stop. End of input (EOF, for example when stdin is not a terminal)
  ends the session the same way, with exit code 0 and the message "Input ended; progress
  saved. Resume by running the annotation command again."
- Either way, every confirmed judgement is kept, a partly answered pair is never saved, and the
  lock is released. Annotation needs an interactive terminal; a non-interactive `!` command
  ends at the first prompt.

Session lock:
- `annotate` creates `annotation.lock` in the work directory with exclusive creation before
  reading any judgement. It holds only the pool manifest hash, the annotator handle, the
  process id and the UTC start time.
- A second session on the same work directory refuses to start. Separate work directories do
  not block each other.
- The lock is removed on normal exit, on a handled error and on Ctrl-C (KeyboardInterrupt).
- After a hard kill the lock remains. It is never removed automatically, and there is no
  override flag. Every later `annotate` and the seal tooling refuse, with instructions to check
  that no session is running, confirm `validate` passes, and delete the lock by hand.
- `status` and `validate` are read-only and never take the lock.

### Primary seal (tooling only; not run)

```text
uv run python -m evals.runners.seal_primary check
uv run python -m evals.runners.seal_primary seal --annotator ann-1 --confirm-human-review \
    --judgements-sha256 <sha printed by check>
```

`check` requires:
- exactly one primary judgement per pooled pair (missing, extra and duplicate records are
  refused);
- `ann-1`, the frozen rubric and valid timestamps;
- canonical bytes and unchanged content and card hashes.

`seal` is the annotator's own confirmation step. It writes `primary_judgements.jsonl`, the
deterministic `consistency_sample.json` and `adjudication_queue.jsonl`, and
`manifests/3-primary_sealed.json`. The provenance stays a binding only.

It has not been run. No consistency sample exists, and the 72-hour clock has not started.

### Retrieval-metric evaluator (`evals/metrics/retrieval.py`, `eval-1`)

**Definitions.**

| Metric | Definition |
|---|---|
| Precision@K | relevant in the top K / K (fixed denominator) |
| Recall@K | pooled: relevant in the top K / relevant judged pairs |
| MRR@K | reciprocal rank of the first relevant result, 0 if none |
| NDCG@K | gains 2 / 1 / 0; IDCG from the query's judged labels |
| CSR@K | `hard` and `fp1` separately: satisfied / returned, with unknown reported separately; plus an all-satisfied flag |
| Irrelevant-result rate@K | (`not_relevant` + unjudged) / returned |
| Zero-result | 1 when nothing was returned |
| Correct-empty | for zero-relevant queries only |

**Conventions.**
- K is 5, 10 and 20; the primary K is 10.
- Strict relevance (primary) and lenient relevance (secondary) are both always reported. There
  is no blended score.
- Unjudged results count as not relevant and are counted (`unjudged@K`).

**Zero-relevant queries.** Recall and MRR are null per definition for zero-relevant queries.
NDCG is null when the query has no relevant or borderline judged pair. Null values are never
averaged as 0.

**Aggregation.** Macro means over the defined queries, each with `n` and `excluded`.

**Serialization.** Values are 10-decimal strings, rounded half-even, inside a versioned config
block.

**Status.** It is tested only on hand-calculated synthetic fixtures (`test_golden_metrics.py`).
No real metric result exists. Milestone 10 runs the V0–V3 comparison after the labels freeze.

### Stale freeze staging

A `.freeze-staging-*` directory left behind by a hard kill is now handled as follows:
- it is ignored by git (`.gitignore`);
- every runner and the annotation store detect it at startup;
- it is refused with cleanup instructions;
- it is never deleted automatically, and never read as frozen output.

### Session C2 limitations

- **Pooled-recall bias.** For `gq-p01`, `gq-p11`, `gq-p13` and `gq-p15`, more than 25 products
  are eligible, so completion does not run and pooled recall is biased (`docs/spec.md` §9.2).
  The negatives act as a pool-miss probe.
- **Shared lists across sources.** For queries where lexical returns nothing, dense, V1, V2 and
  V3 return largely the same 20 products. Pools are therefore well below the 60 cap.
- **Scores as `repr(float)`.** Byte identity held across three runs on this machine, with the
  same libraries and CPU. It is not claimed across other hardware or library versions; the run
  context records the library versions for that reason.
- **Private provenance location.** It exists only in this working copy's ignored
  `data/processed/`, and it must be preserved until `provenance_unsealed`.
- **No integration test.** No integration test file was added. The real retrieval path was
  exercised by the actual runs above, and all unit tests use synthetic fakes.
