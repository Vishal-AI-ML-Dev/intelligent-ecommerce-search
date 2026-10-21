# Milestone 14 — Jev Listing Decisions: progress record

Status: **in progress. M14-1 (Contracts & Questions) is implemented and uncommitted (2026-10-21).**
M14 is not complete. No provider adapter, routing, dataset preparation, live call or evaluation
exists. Nothing is staged or committed.

## Batches

| Batch | Scope | State |
|---|---|---|
| M14-0 | External dataset coverage probes and source selection (scratch only, no repo edits) | Done (previous session) |
| M14-1 | Offline contracts, `jl-1` questions/state builder, provider Protocol, deterministic baseline | **Implemented, uncommitted** |
| M14-2 | Jev listing provider adapter | Not started; needs approval |
| later | Routing, data preparation, evaluation | Not started; each needs approval |

## Approved scope (owner, 2026-10-21)

- Use the selected external sources provisionally, for development only:
  - Flipkart (PromptCloud, Kaggle) for shoes and headphones;
  - Smartphones (Kaggle `harshwardhanpj/smartphones`), validated phone rows only;
  - Laptop Price (Kaggle `sumanbera19/laptop-price-dataset`) for laptops.
- Selecting these datasets **does not declare their upstream rights cleared**. Every declared
  licence (CC BY-SA 4.0, MIT, CC0) was declared by an uploader or crawler. Upstream rights are
  unverified, and private or development use does not resolve them.
- Raw rows and derived datasets stay **outside git**.
- No new manual annotation and no LLM-as-judge.
- ESCI is not used for M14. It stays recorded as an optional, separate future search-relevance
  track.
- M14-1 has no dataset preparation, downloads, installs, Jev calls or database writes.

## Evaluation amendment (recorded; scope-limited)

> **External source-label evaluation replaces new human-labelled evaluation for the approved
> M14 development scope only.**

- Labels come from the recorded external sources and are reported as **external source labels**.
  They are never reported as human-reviewed gold.
- The original human-reviewed requirements are **unmet**, not retrospectively satisfied:
  - MASTER_PLAN M14 "Evaluate on human-reviewed data";
  - the dataset registry's "human" ground truth for the listing decision set;
  - spec §8 (human decision-evaluation labels);
  - spec §13.4 (listing intelligence definition of done).
- Tasks without suitable external labels remain **unmeasured**:
  - phone and laptop subcategory;
  - running shoes;
  - text support for every category;
  - ambiguity;
  - route correctness;
  - calibration and thresholds.
- Unchanged:
  - safety rules;
  - human-only final rejection;
  - no automatic seller action;
  - provider confidence is not gate confidence;
  - prompt-injection and safety tests;
  - future human-review workflows (spec §6.7 annotation tool, M21 review workflow).
- **M14 is not complete merely because this amendment is recorded.**

## Corrections to the M14-0 planning notes (recorded, not yet applied)

1. **"Laptop" in an original title is valid evidence, not automatically label leakage.** The
   seller wrote it. Leakage means target metadata (seller category fields, category paths,
   `Occasion`, evaluation labels) reaching model input. It does not mean words that occur naturally
   in the seller's own title or description.
2. **HDD-only laptops are not inherently invalid.** The M14-0 counting script excluded HDD-only
   or blank-storage laptop rows as format failures. That exclusion rule is **flagged for correction
   during data preparation**: limits of a source adapter must be kept separate from source records
   that are genuinely invalid. The retained count from M14-0 (894 laptops) is therefore
   provisional. The dataset was not changed in M14-1.

## M14-1 implementation

New files only. `decision/__init__.py`, settings, the shared `DecisionProvider` and the M11 Jev
provider are untouched. The new modules are imported by their full path.

| File | Contents |
|---|---|
| `src/ecommerce_search/decision/listing_models.py` | Frozen contracts: `M13RunReference`, `ListingText`, `ClaimReference`, `AttributeQuestion`, `OmittedCandidate`, `ListingDecisionState` (with `state_sha256`), the category, subcategory and attribute-support proposals, `ListingDecisionOutcome` and `validate_outcome` |
| `src/ecommerce_search/decision/listing_questions.py` | The `jl-1` question set as plain data (no SDK), `question_set_digest`, `build_listing_state`, `verify_listing_state`, `questions`, `model_inputs` |
| `src/ecommerce_search/decision/listing_provider.py` | The `ListingDecisionProvider` Protocol, `DeterministicListingDecisionProvider` (no model, no proposals) and `decide_listing` |
| `tests/unit/listing_decisions/` | Synthetic fixtures and four test modules (61 tests) |

### Contract decisions

- **Labels:**
  - category: `laptop`, `phone`, `shoes`, `headphones`, `none`. `none` explicitly covers
    accessories.
  - subcategory: the 12 taxonomy names plus `none`, flat across categories. The names are unique
    across categories.
  - attribute support: `supported`, `contradicted`, `not_stated`. Text that does not state a value
    is `not_stated`, never `contradicted`. The criteria text says this explicitly.
- **Proposals:**
  - Each proposal keeps `raw_choice`, `top_labels` (in label order),
    `provider_probabilities` and `provider_confidence`.
  - `label` is set exactly when the top label is unique and is not `none`.
  - A tie, or a `none` answer, never carries a label.
  - `gate_confidence` is typed `None` and cannot be set.
- **Outcome:**
  - `deterministic`: no proposals and no model telemetry.
  - `jev`: either every proposal (category, subcategory and one support answer per asked claim)
    or a `fallback_reason` with no proposal. The fallback reasons reuse M11's
    `JevFallbackReason`.
  - `validate_outcome` requires the same `state_sha256` and exactly the asked ordinals. An unknown
    ordinal, or an existing ordinal that was omitted, is rejected.
- **Claim identity:** each attribute question names one existing M13 candidate by:
  - its **stable ordinal**: its index in the canonical `ListingNormalization.candidates` order;
  - its **field** and **canonical value**;
  - its **source reference**: `basis=seller_field`, the exact raw key, the exact raw text and the
    rule id.

  The claim is what is being checked, not the expected answer. No expected support label exists
  anywhere in the state, and the builder accepts no labels or targets.
- **Model-visible inputs** (`model_inputs`):
  - `listing_text`, used by the category and subcategory questions, contains exactly `title` and
    `description`. It never includes seller category or subcategory, other structured fields,
    unknown keys such as `Occasion` or category paths, identity, or evaluation targets.
  - `listing_claims`, used by the attribute-support questions, contains the same text plus
    `claims`, keyed by question id. Each claim has `field` and `value` only.
- **Untrusted seller text:**
  - Instructions are fixed text and never contain seller data. Seller-derived values travel only
    as JSON string values in the inputs.
  - This framing is a **precaution**. It is not proof that any real model resists prompt
    injection, and no real model was tested.
- **No truncation:**
  - Title and description are the raw values under the keys M13 read them from: exact, not
    trimmed, whitespace not collapsed, never truncated. Intake already bounds a listing to 65,536
    bytes.
  - An absent, empty (null or blank) or non-text value is recorded as `absent` / `empty` /
    `not_text` and shown to the model as null.

### Candidate ordering, allowlist, cap and omissions

- **Order:** candidates are visited in ascending ordinal, which is the canonical M13 order.
  Questions and omissions are both kept in ascending ordinal.
- **Exact non-numeric allowlist (`jl-1`):** `anc`, `brand`, `color`, `connectivity`, `gender`,
  `gpu`, `material`, `operating_system`, `processor`, `storage_interface`, `storage_type`,
  `wireless`.
- **Deliberately not asked:**
  - numeric fields: integer or decimal value types;
  - `title` and `description`: they are the evidence;
  - `category` and `subcategory`: decision targets;
  - `price`, `currency` and `availability`;
  - `camera`: numeric specifications;
  - `size_system`: the unit system of the numeric shoe size.
- **Cap:** `MAX_ATTRIBUTE_QUESTIONS = 8`. The lowest eligible ordinals are asked.
- **Omissions:** every other candidate is listed in `omitted_candidates` with the first reason
  that matches, in this order:
  1. `text_extraction`: read from the title or description, so it quotes the evidence;
  2. `not_normalized`;
  3. `numeric_field`;
  4. `not_allowlisted`;
  5. `question_cap`.

  The state validator requires every candidate to be asked or omitted exactly once, and
  `question_cap` only after the cap is reached. **An omitted candidate was not assessed.**
- **Pinned digest:** `jl-1` question-set digest `89bf43fed5799c1b8adbaa67ed30566a442fff1721aac4ea8c9fbea135724390`.
  It covers the wording, labels, allowlist, cap, ordering and input shapes. The subcategory
  criteria wording is **provisional**: the taxonomy defines only the names.

## Commands and results

All commands were run on 2026-10-21 with `uv run --offline`. Only the files above were changed.

| Command | Result |
|---|---|
| `pytest tests/unit/listing_decisions` (first run) | 59 passed, 2 failed. The `jl-1` digest pin was still a placeholder, and one test assertion was too broad: it matched the one-character seller id `"s"` inside English words. The pin was set and the assertion tightened. |
| `pytest tests/unit/listing_decisions` | **61 passed** |
| `ruff format` (new files) | 4 files reformatted |
| `ruff check` (new files) | 1 finding (SIM102 in `validate_outcome`), fixed; then **All checks passed** |
| `ruff format --check` (new files) | **8 files already formatted** |
| `pytest tests/unit/listing_decisions tests/unit/normalization tests/unit/search/test_decision_provider.py tests/unit/search/test_jev_provider.py tests/unit/search/test_query_understanding_contract.py` | **558 passed** in 20.61 s |
| The same modules plus `tests/unit/test_unit_isolation.py` | **560 passed** in 882.28 s, exit 0. This includes `test_whole_unit_suite_passes_without_a_database_password`, which runs the whole unit suite (`-m "not integration and not real_model"`) in a subprocess; that run passed. No repository file was edited while it ran. |

**Offline check (`test_listing_decision_offline.py`):**
- It runs the `jl-1` pipeline in a clean `-I` subprocess with socket entry points refused.
- It found no network attempts.
- It found none of these modules loaded:
  - `typesafe_sdk`, which is installed in `.venv`, so this check is meaningful;
  - HTTP clients, SQLAlchemy and psycopg;
  - settings, database, API, search, embedding, reranking and model modules.
- State hashes and outcomes match across two processes and the in-process run.

**Not run:** integration tests, live or Jev calls, and database operations.

## Limitations

- **Offline contracts only:**
  - No provider adapter, prompt execution, routing or evaluation exists.
  - No real model has seen a `jl-1` question.
  - The question wording is untested against any model.
- **Unmeasured:** the subcategory criteria are provisional wording, not validated definitions.
- **Number of model calls:** whether the two inputs mean two model calls per listing (or another
  shape) is decided in M14-2. The contracts do not fix it.
- **Prompt injection:** resistance is not demonstrated. The tests only check that instructions
  are fixed and that seller text is passed as data.
- **Unvalidated choices:** the cap of 8 and the allowlist are engineering choices, not measured.
- **Data still to prepare:**
  - The HDD-only laptop exclusion rule needs correction during data preparation.
  - All M14-0 retained counts remain provisional until that preparation is approved.

## Next batch (needs explicit approval)

M14-2: Jev listing provider adapter.
- Map `jl-1` `QuestionSpec`s and `model_inputs` to the SDK, with mocked transport only.
- Fall back to the deterministic baseline.
- No live calls without a separate approval covering cap, prices and tripwire.

M14-2 has not started.
