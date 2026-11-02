# M19 Return Reason Intelligence: progress and handoff

**Status (2026-11-02):**
- **Part 1:** committed at `3b725a6` (CI green, owner-reported).
- **Part 2:** storage and complaint groups, committed on `main` after the owner's P1–P10
  decisions (commit message "feat(m19): add Part 2 ..."). An M19 staging release is blocked on
  P9/P10.
- M19 is **not accepted**, and Parts 3–4 have not started.

- **Baseline:** M18 closed with qualified acceptance at
  `e2903cc959503f2d146e1459792c4cb8db936d8f`. Its exceptions and limitations
  (`docs/m18/handoff.md`) are unchanged. M18 data, contracts, tables and buyer behaviour were not
  touched.
- **Owner-approved Part 1 scope:** typed, versioned contracts; a deterministic baseline; evidence
  references; suggested responsible areas; fixed review reasons; an offline CLI, tests and
  examples.
- **Not in Part 1:** GenAI or provider calls, clustering, DB access or migrations, API or UI
  changes, cloud or staging work.

## Part 1 deliverables

Package `src/ecommerce_search/returns/classification/`. It needs no database, network, model or
new dependency.

| Module | Content |
|---|---|
| `taxonomy.py` | `return-categories-1`: the twelve §19 categories (none merged) and the suggested areas from the §19 table |
| `contracts.py` | `return-classification-1`: the input (reason code, customer statements and inspection observations, kept apart) and the result (predictions, evidence refs, review reasons); `input_from_records` builds an input from M18 records |
| `text.py` | matching normalization on a copy (NFKC, casefold, apostrophes, runs of repeated letters) with original offsets; the bounded misspelling match |
| `rules.py` | `return-baseline-rules-1`: maps for reason codes, complaint topics and inspection conditions; English, Hinglish and Devanagari phrases; negation; conflict pairs |
| `baseline.py` | `classify()`: signals, then predictions, inspection support or contradiction, tiers and review reasons |
| `cli.py`, `__main__.py` | offline `classify` and `taxonomy` commands |

### Rules in brief
- **Reason codes:** each code maps to its §19 category. `NOT_AS_DESCRIBED` maps to
  {expectation mismatch, incorrect specification}; `DEFECTIVE_OR_NOT_WORKING` maps to
  {product-quality defect, manufacturing defect}. `OTHER` and `NOT_PROVIDED` map to nothing, so
  informative text still classifies them.
- **Complaint topics:** only `damaged` and `wrong_item` map to a category.
- **Inspections never create a prediction.** They support it (`damaged`,
  `functional_fault_confirmed`, `item_differs_from_order`) or contradict it (`as_new` or
  `used_good` against damage; `no_fault_found` against a defect). Otherwise they are neutral.
- **Keywords:** fixed phrases only. Lone misleading words ("small", "colour", "wrong", "late") are
  not rules. Matching is greedy, longest phrase first, with no reuse of tokens and no matching
  across punctuation.
- **Misspellings:** distance ≤ 1, same first letter, and only for 19 allowlisted English tokens
  of at least 6 letters. A fuzzy match needs bounded context: either an exactly matched token in
  the same phrase ("delivry late"), or a product-context word (`FUZZY_CONTEXT_WORDS`, such as
  "screen", "parcel", "box", "arrived", "samaan") up to 3 tokens away in the same clause.
  Without context it is suppressed as `fuzzy_without_context`, so "my broker said…" abstains.
  A prediction that rests only on fuzzy matches is `low`.
- **Negation:** English or Hinglish negators up to 3 tokens before a phrase, or Hinglish ones up
  to 2 tokens after it. The search stops at punctuation, a danda or "but"/"lekin"/"magar". A
  negated match is reported in `suppressed_matches` and never becomes a prediction.
- **Resolution:** every prediction is one supported concern.
  - `resolution: single_category`: `category` names it.
  - `resolution: unresolved_candidates`: `category` is `null`. The evidence supports the
    concern the candidates share (for example "a defect") but none of them. This is an
    abstention between alternatives, not several predictions, and no candidate is asserted.
- **Tiers (not probabilities):**
  - `low`: anything contradicts the prediction, or every supporting match is fuzzy;
  - `high`: at least two kinds of source agree (reason code, complaint topic, customer text,
    inspection);
  - `medium`: otherwise.

  The tier rates support for the concern as a whole. A `high` unresolved defect means strong
  support for "some defect", not for either defect category.
- **Review reasons (fixed):** `insufficient_evidence`, `low_confidence`,
  `unsupported_distinction`, `conflicting_statements`, `statement_inspection_conflict`,
  `conflicting_inspections`, `uncorroborated_item_difference`. Routing hides or rejects nothing.

### Owner decisions on Part 1 (2026-11-02)
The owner approved items 1–7 below as the `return-baseline-rules-1` baseline. They can be
revisited after the Part 3 evaluation:
1. generic defects stay unresolved;
2. inspections only support or contradict;
3. an item difference is routed as `uncorroborated_item_difference`, never as abuse;
4. abstain on insufficient evidence;
5. conflicts as corrected in `3b725a6`;
6. responsible areas split on "or" and "/";
7. the caller supplies linked complaints.

### Part 1 design choices (approved as above)
1. **No merge.** A defect is always the unresolved pair {product-quality, manufacturing}, routed
   as `unsupported_distinction`. No text rule picks one of the two, not even the customer's
   words "manufacturing defect".
2. **Ambiguous signals.** A signal with several candidates ("wrong colour", "wrong size",
   `NOT_AS_DESCRIBED`) supports any of its candidates that another signal names on its own.
   Otherwise it stays unresolved. Inspections never resolve an unresolved set.
3. **Categories the baseline never predicts.** Suspected abuse is never predicted.
   `item_differs_from_order` without a wrong-item statement is routed as
   `uncorroborated_item_difference`, with no category. Insufficient evidence is an abstention
   with a review reason, not a predicted category.
4. **Genuine conflicts are limited to:**
   - opposite size directions;
   - a negated mention in one record against the same category from another record (for
     example code `NO_LONGER_NEEDED` with "not a change of mind");
   - an inspection contradiction, or a pair of disagreeing inspections.

   Coexisting reasons are compatible. That includes change of mind with damage, a defect or a
   wrong item. Opposite size directions keep the sizing tier, because the category is agreed
   and only the direction conflicts.
5. **Suggested areas:** each §19 cell is split on "or" and "/" (so "quality / supplier" gives
   two areas).
6. **Inputs:** which support complaints belong to a return is the caller's choice. Inspection
   notes and customer reviews are not used.

### Review corrections (2026-11-02)
- **Already correct, now explicit:**
  - A defect was already one unresolved prediction with `category: null`, not two confirmed
    predictions.
  - No rule names a manufacturing defect on its own: "manufacturing defect" matches only the
    generic `text:defect:defect` rule.
  - Added: a `resolution` field (validated against `candidates` and `category`) and a
    documented meaning of the tier for unresolved concerns.
- **Changed:** change of mind together with damage, a defect or a wrong item is no longer a
  conflict. The `CONFLICTING_STATEMENT_GROUPS` rule was removed. Regression tests now cover
  compatible coexistence and genuine contradictions.
- **Changed:** fuzzy matches need bounded context (see Misspellings). Suppressed fuzzy matches
  never contradict a prediction; only negated mentions do. Positive and negative regression
  tests were added.

## Usage

```text
uv run python -m ecommerce_search.returns.classification taxonomy
uv run python -m ecommerce_search.returns.classification classify docs/m19/examples/baseline-demo-input.json
```

The demo file holds five hand-written synthetic inputs. Summarized output (2026-11-02 run, exit
0; identical categories, tiers and reasons to the 2026-10-28 run, plus `resolution`):

| Input | Result |
|---|---|
| `OTHER`, "Shoe size chhota hai, fit nahi hua" | sizing_fit, medium, areas listing/product; cites "size chhota" and "fit nahi"; no review |
| `ARRIVED_DAMAGED`, "It is not damaged, just too tight" | sizing_fit, medium; damaged_product, low, contradicted by the negated "damaged"; review: low_confidence, conflicting_statements |
| `DEFECTIVE_OR_NOT_WORKING`, "phone kaam nahi karta", inspection `functional_fault_confirmed` | `unresolved_candidates` {product_quality_defect, manufacturing_defect}, category null, high (support for "a defect"), areas quality/supplier; review: unsupported_distinction |
| `NOT_PROVIDED`, "screen came damagd, and the delivery was late" | damaged_product, low (fuzzy "damagd"); logistics_delay, medium; review: low_confidence |
| `NOT_PROVIDED`, "Please process my return" | abstained; review: insufficient_evidence |

Invalid input exits 2 with a fixed message (`the input is not valid (n error(s))`), without echoing paths or values.

## Tests and checks
- New unit tests: `tests/unit/returns/test_returns_classification_rules.py`,
  `test_returns_classification_baseline.py` and `test_returns_classification_cli.py`.
- The tests use hand-written synthetic inputs. They check rule behaviour and are **not** a
  human-reviewed evaluation set. They make no accuracy claim.
- Neither the M18 demo dataset nor `scenarios.json` was read, used or tuned on. A static test
  checks that the package doesn't reference them.
- **2026-10-28:**
  - 92 new tests passed; `tests/unit/returns` 1025 passed (run before the final +1 test);
  - `ruff check .` and `ruff format --check .` clean;
  - `pytest tests --collect-only` collected 7327 with no collection errors; no duplicate test
    basenames;
  - AC-14 protected-path diff vs `bf67d11` exit 0.
- **2026-11-02 (review corrections):** the three classification test files, 111 passed; Ruff
  clean. The collection and wider returns results above were reused, since no other file
  changed.
- The full suite and the Windows isolation wrapper were not rerun (by direction).

## Limitations
- No measured quality: there is no human-reviewed return-reason set yet (Part 3).
- The lexicon is small and hand-written. Recall on real customer text is unknown, and
  romanized Hinglish spellings beyond the listed variants are missed.
- Negation and clause handling are bounded heuristics. Sarcasm, questions and long-distance
  negation are not handled.
- The fuzzy-context rule is a bounded heuristic. A look-alike word next to a product word
  still matches ("the box broker" would count as "broken"). A real misspelling with no product
  word nearby is missed ("it was brokn" abstains unless another source supports it).
- Coexistence is treated as compatible. The baseline doesn't judge whether two customer
  reasons are plausible together; that is left to human review and later parts.
- The baseline treats every customer statement as true for matching. Conflicts are routed for
  review, not resolved.

## Part 2: storage and complaint groups (2026-11-02)

### Deliverables
| File | Content |
|---|---|
| `migrations/versions/0008_create_return_classification_history.py` | additive revision after 0007: eight append-only tables, their own trigger function `return_inference_append_only()`, no change to any earlier table |
| `src/ecommerce_search/models/return_classifications.py` | ORM models, with constraints identical to the migration (offline DDL parity test) |
| `src/ecommerce_search/returns/classification/store.py` | loads one M18 dataset version, classifies it, records the run idempotently, reads results back (`read_result`), records group runs |
| `src/ecommerce_search/returns/classification/grouping.py` | `return-grouping-1`, deterministic per-SKU rule-signature grouping |
| `src/ecommerce_search/returns/classification/grants.py` | `return-classification-grants-1`, the classifier writer role and the API role |
| `src/ecommerce_search/returns/classification/cli.py` | new `record` command (classify and group against a database) |

### Tables (all append-only; UPDATE, DELETE and TRUNCATE are blocked)
- **`return_classification_runs`:**
  - dataset, contract, taxonomy and rules versions, method, tier semantics and complaint-link
    policy;
  - input digest, counts, and a unique `run_key_sha256`.
- **`return_classifications`:** one row per return per run (FK `returns.id`): status, review
  reasons, input and result hashes.
- **`return_classification_inputs`:** the input record IDs, each with its M18 `record_sha256`.
- **`return_category_predictions`:** resolution, category, candidates, tier and suggested
  areas. A CHECK forbids `suspected_abuse` and `insufficient_evidence`.
- **`return_classification_evidence`:** supporting and contradicting refs, inspection findings
  and suppressed matches. Text evidence keeps spans only; `read_result` rebuilds
  `matched_text` from the source rows.
- **`read_result` verifies before it rebuilds.** Every recorded input must still exist in the
  run's dataset version with the recorded `record_sha256`, and every evidence ref must cite a
  recorded input. Otherwise it raises `SourceMismatchError` ("missing", "changed" or "outside
  the result's inputs") and returns nothing partial.
- **`return_complaint_group_runs`, `return_complaint_groups`, `return_complaint_group_members`:**
  per-SKU groups with `member_count`, `qualifying_member_count` and `is_pattern`. Each member
  row records `counts_toward_pattern`.
- **Versions:** they live on the run row. Every prediction reaches them through its
  classification's `run_id`.

### Writes and versions
- Recording the same versions over the same inputs again returns the stored run
  (`already_recorded`) and adds no rows. A concurrent duplicate loses on the unique key and
  reports the stored run.
- A new rules version, or changed inputs, creates a new run. Earlier runs stay as they are.
- Group runs are keyed the same way, by classification run, grouping version, method and
  minimum size.

### Grants (`return-classification-grants-1`)
- **Classifier (writer) role:**
  - `SELECT` only on `return_datasets`, `returns`, `order_items`, `return_comments`,
    `inspection_observations` and `support_complaints`;
  - `SELECT, INSERT` on the eight new tables and their identity sequences;
  - nothing else: no UPDATE or DELETE anywhere, no raw file contents, no catalog tables.
- **API role:** no privilege on the new tables.
- **Enforcement:** `verify_classification_grants` checks every public table and every
  privilege kind. `record` refuses superusers, table owners and any role that can change M18
  rows.
- The M18 grants script is unchanged.

### Grouping (`return-grouping-1`)
- **Key:** returns of one SKU are grouped by the prediction's candidate set. Sizing also
  carries a direction from its rules: `too_small`, `too_large`, `mixed` or `unspecified`.
- **No free-text similarity:** it compares which rules matched, not text or embeddings. No
  model is loaded and nothing is downloaded.
- **All members are stored.** A member *qualifies* only if its prediction is `medium` or `high`
  tier, has no contradicting evidence, and its return has no conflict review reason
  (`conflicting_statements`, `statement_inspection_conflict`, `conflicting_inspections`). This
  is conservative: a conflict anywhere on a return disqualifies all of its predictions.
- **Patterns:** a group is flagged `is_pattern` only with at least `min_pattern_size` (default
  3) qualifying members. Low-tier, contradicted and conflicting members never make a group a
  pattern. The threshold is a display threshold, not statistical significance.
- **Not patterns:** other groups are reported by member count only.
- Groups are groupings, not root causes.

### Usage
```text
python -m ecommerce_search.returns.classification.grants --database <db> --app-role <api> --classifier-role <writer>
POSTGRES_USER=<writer> ... python -m ecommerce_search.returns.classification record --dataset-id synthetic-returns --dataset-version 1
```

### Run over the 180 synthetic returns (disposable test database, 2026-11-02)
These are counts of inferred outputs on project-authored synthetic data. **They are not
accuracy**, and no labels were used.

- **Totals:** 180 returns. 157 classified, 23 abstained (all with `insufficient_evidence`),
  77 needing review.
- **Single-category predictions:**

  | Category | Count |
  |---|---|
  | change_of_mind | 30 |
  | sizing_fit | 24 |
  | damaged_product | 22 |
  | colour_image_mismatch | 16 |
  | incorrect_specification | 16 |
  | wrong_item_delivered | 10 |
  | logistics_delay | 8 |

- **Unresolved candidate sets:** {product_quality_defect, manufacturing_defect} 29;
  {expectation_mismatch, incorrect_specification} 12.
- **Review reasons:** unsupported_distinction 41, insufficient_evidence 23, low_confidence 12,
  statement_inspection_conflict 11, uncorroborated_item_difference 10, conflicting_statements 6,
  conflicting_inspections 2.
- **Prediction tiers (not probabilities):** high 71, medium 84, low 12.
- **Groups (rerun 2026-11-02 under the P7 rule):** 122 groups. 157 returns grouped, 23
  ungrouped. **6 patterns** (members / qualifying members):
  - SYN-HDP-0025 colour_image_mismatch, 8 / 7;
  - SYN-HDP-0037 damaged_product, 8 / 8;
  - SYN-LAP-0018 incorrect_specification, 8 / 8;
  - SYN-PHN-0059 product_quality_defect|manufacturing_defect, 8 / 6;
  - SYN-SHO-0050 sizing_fit:too_small, 6 / 6;
  - SYN-SHO-0037 sizing_fit:too_large, 4 / 4.

  Not patterns: 109 groups of 1 member, 6 of 2, and 1 of 4. The group of 4 is
  SYN-SHO-0037 `sizing_fit:mixed`, whose members all carry `conflicting_statements`; it was a
  pattern under the earlier size-only rule.

### Owner decisions on P1–P10 (2026-11-02)
- **P1 approved:** rule-signature grouping per product, with sizing direction kept apart.
  **Deferred:** free-text and embedding similarity. MiniLM is cached locally but unused.
- **P2 approved:** 3 is a display threshold, not statistical significance.
- **P3 approved:** a complaint is linked only by exact `order_item_id`.
  - **Ignored links:** complaints with only an `order_id`, or only a `product_id`.
  - They are never read as return evidence, and nothing marks them as ignored.
- **P4 approved:** spans only. Read-back verification of each source `record_sha256` was added
  (see Tables), with tests for a missing record, a changed checksum and an evidence ref
  outside the inputs.
- **P5 approved:** no API access before Part 4.
- **P6 approved:** versions per run.
- **P7 changed:** all members are stored, but only qualifying members (medium or high tier,
  not contradicted, not conflicting) make a pattern (see Grouping).
- **P8 approved:** the database CHECK forbidding `suspected_abuse` and
  `insufficient_evidence` in stored predictions stays; relaxing it needs a migration.
- **P9 and P10 deferred to Part 4.** They are a **known blocker for any M19 staging release**:
  - the release and restore kit (`deploy/ec2/m18-release.sh`, `restore.sh`) still expects
    revision 0007 and knows nothing of `return-classification-grants-1`;
  - no writer-role setup step or production role name exists.

  Both must be done before an M19 staging release. Nothing was deployed.

### Tests (2026-11-02, after the P4/P7 changes)
- **Targeted rerun:** 158 unit tests passed: the four classification modules plus the catalog
  metadata test.
- **Integration:** 243 passed in the new module plus the six modules with updated pins
  (`test_migrations`, `test_catalog_schema`, `test_dense_migration`, `test_search_migration`,
  `test_returns_schema`; the unit metadata pin is counted above).
  - The new module now has 15 tests: three for P4 read-back and stronger P7 database checks.
  - It also passed with deprecation warnings treated as errors.

**Earlier (first Part 2 run):**
- **Unit:**
  - `tests/unit/returns/test_returns_classification_grouping.py` (new);
  - the existing classification tests: 128 passed in total;
  - `tests/unit/returns` and `tests/unit/catalog` together: 1328 passed;
  - the catalog metadata pin was updated for the eight new tables.
- **Integration (local disposable databases):**
  - `tests/integration/test_returns_classification_integration.py` (new, 12 tests), covering:
    - grants and M18 grants still holding;
    - only the classifier may record;
    - the 180-return run, M18 rows unchanged (count and digest per table);
    - round trip of every result and its input records;
    - idempotency, append-only history and the abuse CHECK;
    - groups and the `record` CLI as the classifier;
    - a new rules version making a new run;
    - the 0008 upgrade, downgrade and re-upgrade, with no earlier table changed.
  - Affected existing modules rerun with it: `test_migrations`, `test_catalog_schema`,
    `test_dense_migration`, `test_search_migration`, `test_returns_schema`,
    `test_returns_grants`.
  - Head pins were updated to 0008. `test_returns_schema`'s M18 round trip now re-upgrades to
    0007 explicitly.
- **Not rerun:** the full suite, other integration modules and the Windows wrapper.

### Part 2 limitations
- Run counts describe synthetic data and say nothing about quality.
- Grouping is coarse: same SKU and same rule signature. Different wording of one complaint
  groups together only when the same rules matched.
- Catalog v1 has no variants, so grouping is by SKU only.
- Complaints linked only by order or by product are ignored (P3).
- No API, UI or staging release. A staging release is blocked on P9/P10 (Part 4).

## Next (not authorized)
Part 3: optional GenAI and a human-reviewed quality comparison. It needs owner approval.
