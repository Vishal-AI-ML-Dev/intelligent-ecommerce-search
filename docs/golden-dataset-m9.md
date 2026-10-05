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
| C1 | Pilot queries, gold constraints, eligibility evaluator, pilot validators, queries-only freeze runner | Complete, uncommitted. `queries_frozen` was frozen by `ann-1` and verified (see "Frozen `queries_frozen` stage") |
| C2 | Runtime retrieval, deterministic candidate pool, private provenance, `pool_frozen` | Not started |
| Later | Annotation tool, labels, consistency round, metrics, test partition | Not started |

No candidate pool, provenance, judgement, label or metric file exists.

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

C2 (candidate pooling) has not started.

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
