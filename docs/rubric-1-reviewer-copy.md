# `rubric-1` — reviewer copy (second review, `ann-2`)

These are the `rubric-1` rules the primary annotator worked under. Every rule is quoted from,
or restated exactly from, an approved source, and names that source. Where no source gives
guidance, this copy says **not specified**: no definition has been added.

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

## 1. The task

You judge one query–product pair at a time: how relevant is this product to this search query?
You see:

- the query text and its recorded interpretations;
- the query's gold constraints and soft intents;
- the product card.

Labels start unset, and nothing is prefilled or suggested (GD D4; GDC; `docs/spec.md` §6.7).

Review independently. You do not see, and should not look for:

- retrieval sources, ranks or scores;
- inclusion reasons, parser output or filters;
- seller, availability, rating or review count (GD D4, D8; GDC);
- any other annotator's label;
- any machine or AI label or proposal;
- the project's AI-assistant conversations.

## 2. Labels (GD D1–D3; C `Label`)

There are exactly three labels:

- `relevant`
- `borderline`
- `not_relevant`

Each label carries a separate **relation** facet, chosen only from the relations allowed for that
label (C `ALLOWED_RELATIONS`). The review tool numbers them in this order:

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
label.

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

## 8. Query-specific rules (GD Session C1 decisions; GDQ)

- **gq-p04 (lenovo lumen ryzen 7 laptop).** "Category, brand and processor are hard. *Lumen* is
  a typed series intent (`ProductIntent`, kind `series`), not semantic intent and not a hard
  constraint." (GDQ)
- **gq-p08 (1tb nvme laptop).** "The catalog represents NVMe as the existing typed laptop field
  `storage_interface` (`StorageInterface.NVME`). There is no generic `interface` field." "The
  taxonomy states that NVME *requires* an SSD medium (enforced by `LaptopSpecData`), but the
  query does not state SSD, so no `storage_type` constraint is added." (GDQ)
- **gq-p12 (women running shoes).**
  - "gender=women is a strict hard constraint. Unisex does not satisfy it; the rubric may still
    allow `borderline`/`substitute`." (GD Session C1 decision 3)
  - "Strict: shoes + running + women. Comfort is not a gold requirement." (GDQ)
- **gq-p14 (sasta phone 8gb ram wala).** "*Sasta* is a soft low-price preference. No numeric
  maximum price is invented." (GDQ)
- **gq-p18 (apple).** "Brand only, with no forced category. Apple laptops and Apple phones are
  both valid interpretations." (GDQ)
