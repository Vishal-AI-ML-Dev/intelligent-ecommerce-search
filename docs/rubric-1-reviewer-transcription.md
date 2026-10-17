# `rubric-1` — reviewer-facing transcription (DRAFT, awaiting approval)

**Status: draft for the project owner's approval. Not yet approved and not yet placed in any
reviewer bundle.**

## What this document is

- It collects the `rubric-1` rules for second reviewer `ann-2` (review-2). They are the same
  rules that bound primary annotator `ann-1`.
- Every rule is quoted from, or restated exactly from, a source in this repository, and each
  rule names its source.
- Nothing here is new. Where a source is silent, this document says **not specified** instead
  of filling the gap.
- `rubric-1` has no single standalone text in the repository. Its rules are spread over:
  - the approved M9 decisions (`docs/golden-dataset-m9.md`);
  - the label contracts (`evals/contracts/constants.py`, `evals/contracts/models.py`);
  - the annotation tool's validation (`evals/annotation/cli.py`, `evals/annotation/session.py`,
    `evals/contracts/validation.py`).

Source keys:

| Key | Source |
|---|---|
| GD | `docs/golden-dataset-m9.md`, "Approved decisions" (D1–D8, Session C1 decisions) |
| GDQ | `docs/golden-dataset-m9.md`, "Query-specific rules" and "Gold constraints and soft signals" |
| GDE | `docs/golden-dataset-m9.md`, "Eligibility" |
| GDC | `docs/golden-dataset-m9.md`, "Blind annotation CLI" |
| C | `evals/contracts/constants.py` (`Label`, `Relation`, `ALLOWED_RELATIONS`) |
| M | `evals/contracts/models.py` (`Judgement`, `Confidence`) |
| CLI | `evals/annotation/cli.py` (prompts, `_rationale_required`, the hard-violation refusal) |
| V | `evals/contracts/validation.py` (`validate_judgements_against_eligibility`) |
| Q | `evals/goldens/internal-search/v1/queries.jsonl`, `notes` field (frozen, confirmed by `ann-1`) |

## 1. The task

You judge one query–product pair at a time: how relevant is this product to this search query?
You see:

- the query text and its recorded interpretations;
- the query's gold constraints and soft intents;
- the product card.

Labels start unset, and nothing is prefilled or suggested (GD D4; GDC; `docs/spec.md` §6.7).

You do not see, and should not look for:

- retrieval sources, ranks or scores;
- inclusion reasons, parser output or filters;
- seller, availability, rating or review count (GD D4, D8; GDC);
- any other annotator's label;
- any machine or AI label.

## 2. Labels (GD D1–D3; C `Label`)

There are exactly three labels:

- `relevant`
- `borderline`
- `not_relevant`

Each label carries a separate **relation** facet, chosen only from the relations allowed for that
label (C `ALLOWED_RELATIONS`). The primary round numbered them in this order:

| Label | Allowed relations |
|---|---|
| `relevant` | 1 `exact_match`, 2 `satisfies_intent` |
| `borderline` | 1 `partial`, 2 `substitute` |
| `not_relevant` | 1 `complement`, 2 `off_target`, 3 `out_of_catalog` |

- "A complement is `not_relevant` with relation `complement`." (GD D1–D3)
- Scoring context only, not a labelling instruction (GD D1–D3): NDCG gains are 2 / 1 / 0;
  strict binary relevance (relevant only) is primary, lenient (relevant or borderline) is
  secondary.

**Not specified in any source:** a written definition of each relation. This covers
`exact_match` vs `satisfies_intent`, `partial` vs `substitute`, and `off_target` vs
`out_of_catalog`. Only the names and the allowed label–relation pairs exist.

## 3. Hard gold constraints

- Hard gold constraints come from the frozen query record: category, brand, ram_gb, storage_gb,
  storage_type, storage_interface, price, processor, subcategory, gender, anc, wireless,
  connectivity (GDQ).
- **Rule:** "A hard-constraint violation never becomes `relevant`" (V). The tool refuses
  `relevant` for a product that violates a hard gold constraint (GDC; CLI).
  - The tool's message reads: "relevant is not allowed: this product violates a hard gold
    constraint of the query (rubric rule). Choose borderline (substitute) or not_relevant."
    (CLI)
- Eligibility is "a pure, deterministic catalog fact. It is **not relevance** and creates no
  label" (GDE).
- Matching is literal, as follows (GDE):
  - exact brand, category, processor and integer capacities;
  - storage medium and storage interface are compared separately;
  - price bounds are inclusive;
  - "`unknown`: the attribute exists for the category but the value is missing";
  - "`not_applicable` … counts as a violation overall".
- **Rule:** "`relevant` with an unknown hard attribute needs a written rationale" (V; CLI).

## 4. Soft signals and series intents (GDQ)

- Soft-only fields are `semantic_intent` (gaming, coding, student, lightweight, premium, comfort,
  travel, office) and `price_preference` (low). They are never hard.
- `ProductIntent` is "an exact series/model intent (kinds `series`, `model_code`)". It never
  feeds eligibility.

**Not specified in any source:** how strongly a soft signal or a series intent should move a
label. For example: does a non-gaming laptop for "gaming laptop" count as `relevant`, or as
`borderline`?

## 5. Rationale

A rationale is **required** for (M `Judgement`; CLI `_rationale_required`):

- every `borderline` label;
- every `low` confidence;
- `relevant` with an unknown hard attribute.

Otherwise it is optional. A rationale must not contain an email address or a machine-specific
path (M free-text rules).

## 6. Confidence (M `Confidence`; CLI)

The values are `high`, `medium` and `low`, and the only rule is that `low` requires a rationale.

**Not specified in any source:** what each confidence level means, or when to choose it.

## 7. Flags (CLI prompts; GDC)

- `needs adjudication? [y/N]`. **Not specified:** the criteria for answering yes.
- `catalog issue (malformed record)? [y/N]`. The only definition is the parenthesis,
  "malformed record".

## 8. Query-specific rules (GD Session C1 decisions; GDQ; Q notes)

- **gq-p04 (lenovo lumen ryzen 7 laptop).**
  - "Category, brand and processor are hard. *Lumen* is a typed series intent (`ProductIntent`,
    kind `series`), not semantic intent and not a hard constraint." (GDQ)
  - "same-series laptops with other processors are not hard-eligible." (Q)
- **gq-p08 (1tb nvme laptop).** "NVMe is the storage_interface field; SSD is not stated and is
  not added." (Q; GDQ)
- **gq-p10 (hp laptop 8gb 256 ssd under 40k).** "8gb is read as RAM and 256 as storage
  capacity." (Q)
- **gq-p12 (women running shoes).**
  - "gender=women is a strict hard constraint. Unisex does not satisfy it; the rubric may still
    allow `borderline`/`substitute`." (GD Session C1 decision 3)
  - "its relevance is left to the human annotator." (Q)
  - "Comfort is not a gold requirement." (GDQ)
- **gq-p14 (sasta phone 8gb ram wala).** "*Sasta* is a soft low-price preference. No numeric
  maximum price is invented." (GDQ; Q)
- **gq-p16 (samsng phone 128gb).** "Samsng is a misspelling of Samsung; 128gb is read as
  storage capacity." (Q)
- **gq-p17 (noise cancelling headfones).** "Headfones is a misspelling of headphones." (Q)
- **gq-p18 (apple).** "Brand only, with no forced category. Apple laptops and Apple phones are
  both valid interpretations." (GDQ)
- **gq-p19 (laptop 64gb ram).** "No catalog laptop has 64 GB RAM." (Q)
- **gq-p20 (smartwatch).** "An out-of-catalog category intent. No catalog category exists, so
  no constraint is recorded, no fake category is created, and no product is hard-eligible."
  (GDQ)

## 9. Conflicts and gaps found (for the project owner, not for the reviewer)

1. **Relations, confidence levels and `needs_adjudication` have no written definitions**
   (sections 2, 6, 7). `ann-1` labelled without a written definition in the repository.
   - If definitions are now written for `ann-2`, the two annotators work from different rubric
     text, which would bias agreement.
   - Recommendation: keep these explicitly "not specified" for `ann-2` as well, and disclose
     the gap.
2. **The weight of soft signals and series intents is unspecified** (section 4). Same
   recommendation.
3. **The hard-violation message suggests only `borderline (substitute)`, but the tool also
   accepts `borderline` with `partial`.** It only refuses `relevant` (CLI `parse_compact`;
   `ALLOWED_RELATIONS`). Transcribed as-is; not resolved here.
4. **`docs/spec.md` §6.7 and `MASTER_PLAN.md` §22 say that sources and parsed constraints are
   shown.** Approved decision D4 overrides them: these are hidden (GD: "These decisions …
   override the generic M9 text").
5. **Query `notes` (Q) were not shown by the annotation CLI**, but `ann-1` confirmed them when
   freezing the pilot. Section 8 quotes them for `ann-2`.
   - **Your decision:** include them (parity with what `ann-1` knew), or remove section 8's
     Q-sourced lines (parity with what the CLI showed).
6. **What `ann-1` actually read during labelling is unknown.** This transcription reflects the
   repository sources, not a recorded reading list.
7. **Omitted on purpose:** the hard-eligible counts and product IDs (for example "Only
   `SYN-LAP-0003` satisfies the hard constraints"). They are catalog facts, not labelling rules,
   and could act as hints.
