# Milestone 14 — Jev Listing Decisions: progress record

Status: **in progress. M14-2 (Jev listing provider) is implemented with mocked tests only and is
uncommitted (2026-10-21).** M14-1 is committed as `be2cdf3`. M14 is not complete. No routing,
dataset preparation, live call or evaluation exists. M14-3 has not started.

## Batches

| Batch | Scope | State |
|---|---|---|
| M14-0 | External dataset coverage probes and source selection (scratch only, no repo edits) | Done (previous session) |
| M14-1 | Offline contracts, `jl-1` questions/state builder, provider Protocol, deterministic baseline | Done, committed `be2cdf3` |
| M14-2 | Jev listing provider adapter (mocked transport only) | **Implemented, mocked tests pass, uncommitted** |
| M14-3 and later | Routing, data preparation, evaluation | Not started; each needs approval |

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

## M14-2 implementation (2026-10-21)

| File | Change |
|---|---|
| `src/ecommerce_search/decision/jev_listing_provider.py` | New: `JevListingDecisionProvider`, which implements `ListingDecisionProvider` (`decide(state) -> ListingDecisionOutcome`) |
| `src/ecommerce_search/decision/jev_provider.py` | Behaviour-preserving refactor: the submit-and-wait step of `_outcome` moved into `_send`, which raises a new private `_CallFallback(reason, request_id)`; `_Call.query` renamed `_Call.state` |
| `tests/unit/listing_decisions/test_jev_listing_provider.py` | New: 45 mocked tests |

Not changed: `decision/__init__.py`, settings and config, `listing_models.py`,
`listing_questions.py` (so the `jl-1` digest is unchanged), `listing_provider.py`, the M12
proposal, and the M11 tests. Nothing builds the provider from settings, and nothing calls it.

### Design

- **Two `system_one` Choice calls per listing:**
  - **Call A** goes over `model_inputs(state)["listing_text"]`, which is exactly the raw title
    and description. It asks `category` and `subcategory`.
  - **Call B** goes over `model_inputs(state)["listing_claims"]`. It asks one
    `attribute_support[<ordinal>]` question per asked claim. Its input is the same title and
    description plus the asked claims only, each as `{field, value}`. The text is included
    because a support question is answered against it; this is the input shape `jl-1` pinned in
    M14-1.
  - Call B is skipped when the state asks no claim.
- **Unchanged `jl-1` wording:** instructions and criteria come from `listing_questions.questions`.
  The adapter adds no wording.
- **Reuse of M11:** the adapter wraps a `JevDecisionProvider`. It reuses that provider's event
  loop, client, retry policy, admission (`max_in_flight`), error-to-reason mapping, and `_choice`
  validation. `_choice` checks closed label sets, the probability-sum tolerance and the
  `TOP_TIE_TOLERANCE` tie rule. None of this logic was copied.
- **Deadline:** `deadline_seconds` bounds the whole `decide()` call. Call B gets only the time
  left after call A.
- **All-or-nothing validation:**
  - Each response must answer exactly its call's questions. An unasked, omitted or unknown
    ordinal, a missing answer, or a question from the other call is rejected.
  - The response must name the configured model, otherwise `model_mismatch`.
  - Every answer must use exactly its closed label set: no out-of-set choice, and no missing or
    extra label.
  - Token counts must be non-negative integers.
  - The built outcome is re-checked with `validate_outcome`.
  - Any failure in either call (HTTP or transport error, deadline, saturation, closed provider,
    invalid response) returns a `jev` outcome with a `fallback_reason` and **no proposal**. Valid
    answers from call A are discarded when call B fails.
- **Labels:**
  - A label is set only for a unique top label that is not `none` (category and subcategory).
  - A tie within `TOP_TIE_TOLERANCE`, or a `none` answer, carries no label.
  - Support labels are `supported`, `contradicted` and `not_stated`. A support tie carries no
    label.
- **Ordering:** `attribute_support` follows the state's ascending ordinal order, whatever order
  the response used.
- **Confidence:** `provider_confidence` is the provider's own number. `gate_confidence` stays
  `None`. No calibration is claimed.
- **Telemetry:**
  - Token counts are summed over the calls made. A total is `None` unless every call reported
    its count.
  - `request_id` holds the request ids joined by `,` in call order, because the M14-1 contract
    has a single field.
- **Logging:** `jev_listing_decision` logs labels, probabilities, claim ordinals and
  `state_sha256`. It never logs the key, listing text, claim values or exception text.

### Commands and results (2026-10-21, `uv run --offline`)

| Command | Result |
|---|---|
| `pytest tests/unit/search/test_jev_provider.py tests/unit/search/test_decision_provider.py tests/unit/search/test_query_understanding_contract.py` (after the `jev_provider.py` refactor, before any new code) | **138 passed** |
| `ruff format` / `ruff check` (the two source files) | 1 file reformatted; **All checks passed** |
| `pytest tests/unit/listing_decisions/test_jev_listing_provider.py` (first run) | 43 passed, 1 failed. An invalid token count in call A was only detected after call B had been sent. Fixed: usage is now validated per response, so call B is never made after an invalid call A. |
| `pytest tests/unit/listing_decisions/test_jev_listing_provider.py` | **44 passed**; then **45 passed** after adding the shared-deadline test |
| `pytest tests/unit/listing_decisions tests/unit/normalization tests/unit/search/test_decision_provider.py tests/unit/search/test_jev_provider.py tests/unit/search/test_query_understanding_contract.py` | **603 passed** in 19.73 s (the M14-1 set of 558 plus 45 new). This includes the unchanged M11 Jev tests and the M14-1 offline subprocess check. |
| `ruff check` / `ruff format --check` (the 3 touched Python files) | **All checks passed**; **3 files already formatted** |

**Not run:**
- `tests/unit/test_unit_isolation.py` (the whole-unit-suite subprocess run, about 15 minutes);
- integration tests;
- any live or Jev call;
- any database operation.

### What the tests cover

- **Request payloads:**
  - call A: the exact state, questions, instructions and criteria;
  - call B: the exact claims, one support question per asked claim, and the support labels.
- **What is never sent:**
  - omitted and numeric candidates;
  - hidden target metadata (`Occasion`, category path), seller identity, price and seller
    category, which appear in no request.
- **Zero claims:** a listing with no asked claim makes one call only.
- **Invalid answers:**
  - an out-of-set category or subcategory, or an extra or missing label;
  - an invalid support label;
  - an answer for an omitted, unknown or unasked ordinal, or a missing asked claim;
  - questions answered in the wrong call;
  - invalid token counts;
  - a model mismatch on either call;
  - unparseable bodies.
- **Ties and `none`:** the category tie boundaries at the M11 tolerance (equal, within, beyond);
  subcategory and support ties; `none` answers.
- **Order:** the output order is the same whatever order the response used.
- **Failures:** HTTP failures on call A (call B skipped) and on call B (call A's answers
  discarded), transport errors, a closed provider (nothing sent), and the shared deadline.
- **Logs and reprs:** the key and listing text never appear.

### M14-2 limitations

- **Mocked only:**
  - No real model has seen a `jl-1` question.
  - Response shapes are the SDK's documented shapes replayed through `httpx2.MockTransport`,
    not observed live responses.
  - Answer quality, latency and cost are unmeasured.
- **Number of calls:** two calls per listing doubles admission slots, latency and request count
  compared with one call. A single-call shape was not evaluated.
- **Private reuse:** the adapter uses the private M11 internals `_send`, `_choice`,
  `_CallFallback` and `_InvalidResponse` within the `decision` package. Changes to those
  internals now affect both providers.
- **`request_id` is a joined string:** the field holds `,`-joined ids, not a single request id.
- **Inherited from M11:** the probability-sum and tie tolerances (`1e-3`, `1e-9`) remain
  provisional.
- **Not built:** prompt-injection resistance is not demonstrated, and no routing, persistence or
  evaluation exists.

## Next batch (needs explicit approval)

M14-3 is not started. Its scope (routing, data preparation or evaluation) needs explicit owner
approval. No live calls are allowed without a separate approval covering the cap, prices and
tripwire.
