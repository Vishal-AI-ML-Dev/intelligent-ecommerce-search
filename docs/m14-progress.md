# Milestone 14 — Jev Listing Decisions: progress record

Status: **in progress. M14-4 (offline evaluation tooling) is implemented and uncommitted
(2026-10-21).** M14-1 is committed as `be2cdf3`, M14-2 as `d77a099` and M14-3 as `5a9cc2f`. M14
is not complete. No dataset preparation, runner, live call or real evaluation exists. M14-5 has
not started.

## Batches

| Batch | Scope | State |
|---|---|---|
| M14-0 | External dataset coverage probes and source selection (scratch only, no repo edits) | Done (previous session) |
| M14-1 | Offline contracts, `jl-1` questions/state builder, provider Protocol, deterministic baseline | Done, committed `be2cdf3` |
| M14-2 | Jev listing provider adapter (mocked transport only) | Done, committed `d77a099` |
| M14-3 | Deterministic routing policy `lr-1` (owner-approved rules, Option A) | Done, committed `5a9cc2f` |
| M14-4 | Offline evaluator: `le-1` contract, measurability, metrics, corruption checks, `evaluate` CLI | **Implemented, synthetic-fixture tests pass, uncommitted** |
| M14-5 and later | Persistence (optional), dataset preparation, live runner, live evaluation | Not started; each needs approval |

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

## M14-3 approved routing rules: `lr-1` (owner, 2026-10-21, Option A)

No approved rule table existed before this batch. The repository specification only limits
automated outcomes to three routes (spec §6.6 FR-LST-4, §8) and says "low-risk, high-confidence
cases may be auto-accepted". The M14 plan left open whether `auto_accept` is reachable. The owner
approved the following rules:

1. **`auto_accept` is unreachable in `lr-1`.** Risk triage does not exist yet (M15), and no
   confidence threshold is justified (`gate_confidence` is always `None`). A clean listing gets
   `accept_with_warning` with reason `risk.not_assessed`. That route is **a technical routing
   result, not permission to publish, and not a claim that the listing is safe.**
2. **The final route is the strictest of:**
   - the `accept_with_warning` baseline;
   - the deterministic findings;
   - the Jev escalations.

   Jev never lowers a route, and never downgrades `manual_review`. No confidence or probability
   is read.
3. **Jev failure or invalid response:** the deterministic route is kept, and the stable reason
   `jev.fallback.<reason>` is added. Under Option A the route is already at least
   `accept_with_warning`.
4. **Jev disabled** (the deterministic provider): deterministic routing only.
5. **No rejection route exists.** Final rejection is a human decision only.

Route order: `auto_accept` < `accept_with_warning` < `manual_review`.

| Signal | Reason code | Route |
|---|---|---|
| Always (risk not assessed) | `risk.not_assessed` | `accept_with_warning` |
| M13 `value_conflict`, `cross_field_contradiction`, `incompatible_attribute`, `incompatible_subcategory`, `brand_category_mismatch`, `missing_required` | `m13.<kind>` (subject: field) | `manual_review` |
| M13 `unsupported_attribute`, `compatibility_unchecked`, `missing_expected`, `empty_value` | `m13.<kind>` (subject: field) | `accept_with_warning` |
| M13 candidate `invalid` | `m13.candidate_invalid` (subject: field) | `manual_review` |
| M13 candidate `uncertain` / `unsupported` | `m13.candidate_uncertain` / `m13.candidate_unsupported` (subject: field) | `accept_with_warning` |
| Eligible claim omitted because of the `jl-1` question cap | `jl1.question_cap` (subject: question id) | `accept_with_warning` |
| Jev category label differs from the seller's normalized category | `jev.category_disagrees_with_seller` | `manual_review` |
| Jev category and subcategory labels are incompatible | `jev.incompatible_category_subcategory` | `manual_review` |
| A claim answered `contradicted` (unique top label) | `jev.claim_contradicted` (subject: question id) | `manual_review` |
| Jev tie on category / subcategory | `jev.category_tie` / `jev.subcategory_tie` | `accept_with_warning` |
| Jev `none` on category / subcategory | `jev.category_none` / `jev.subcategory_none` | `accept_with_warning` |
| Tie on a support answer | `jev.claim_tie` (subject: question id) | `accept_with_warning` |
| `supported` or `not_stated` | (none) | no escalation |
| Jev fallback | `jev.fallback.<JevFallbackReason>` | `accept_with_warning` |

**Pinned `lr-1` policy digest:** `7d67e7115531516cfcef159b3b904084e066130d77e7ff309ae0f273c6764a14`.

## M14-3 implementation (2026-10-21)

| File | Change |
|---|---|
| `src/ecommerce_search/decision/listing_routing.py` | New. It holds:<br>• `Route` (three values, no rejection) and `RoutingReason`;<br>• `RoutingDecision`, which records the route, the deterministic route, the reasons, the provider, the fallback reason and the policy version and digest;<br>• `route_listing(normalization, state, outcome)`, `policy_as_dict` and `policy_digest`. |
| `tests/unit/listing_decisions/test_listing_routing.py` | New: 65 tests |
| `docs/m14-progress.md` | The approved rules above, this record, and M14-2's commit status corrected to `d77a099` |

Nothing else changed: no contracts, provider, settings, `decision/__init__.py`, persistence, CLI
or evaluation code.

- **Reason codes are data-free.**
  - Each reason carries its code, its own route and a subject.
  - The subject is a canonical field name, a question id, or nothing. It is never seller text or
    a raw key.
  - Reasons are deduplicated and kept in canonical `(code, subject)` order.
- **The decision contract checks itself.** `RoutingDecision` rejects any decision where:
  - the route is `auto_accept`;
  - the route is not the strictest of its reasons;
  - the route is lower than the deterministic route;
  - `risk.not_assessed` is missing;
  - the reasons are out of order;
  - a fallback reason appears without a Jev provider.
- **Inputs are bound before routing.** `route_listing` raises `RoutingInputMismatch` and produces
  no route when:
  - the normalization does not match the state's M13 reference (output hash, rules, taxonomy,
    candidate count);
  - or the outcome does not answer the state (`validate_outcome`).

  A mismatched input is never turned into a route.
- **Every M13 kind is mapped.** Import fails if an M13 finding kind or candidate outcome is
  unmapped.

### Commands and results (2026-10-21, `uv run --offline`)

| Command | Result |
|---|---|
| `pytest tests/unit/listing_decisions/test_listing_routing.py` | **65 passed** in 1.32 s. Before this run, three fixture assumptions were checked against M13 output and corrected in the tests (a `-5` price is `unsupported`, not `invalid`; an empty `operating_system` on headphones also yields an incompatibility; a qualifier such as "up to" makes a text candidate `uncertain`). |
| `ruff format` (the 2 new files) | 2 files left unchanged |
| `ruff check` (the 2 new files) | 2 × S311 (seeded `random.Random` in the property tests); suppressed with the repository's existing `# noqa: S311 - seeded test data, not security` convention; then **All checks passed** |
| `ruff format --check` (the 2 new files) | **2 files already formatted** |
| `pytest tests/unit/listing_decisions tests/unit/search/test_jev_provider.py tests/unit/search/test_decision_provider.py` | **277 passed** in 18.64 s |
| Review fix: the confidence property test varied only the category's confidence. It now varies confidence and probability mass on every proposal (category, subcategory and each support answer), built through validation. Then `ruff check` / `ruff format --check` and `pytest tests/unit/listing_decisions/test_listing_routing.py` | **All checks passed**; **2 files already formatted**; **65 passed** in 1.67 s |

**Not run (as instructed):**
- the full unit suite and `tests/unit/test_unit_isolation.py`;
- integration tests;
- any live or Jev call;
- any database operation.

### What the tests cover

- **The approved rules:**
  - the pinned `lr-1` digest;
  - exactly three routes, none of them a rejection;
  - the M13 and Jev signal tables, asserted literally.
- **The baseline:** a clean listing gets `accept_with_warning` with only `risk.not_assessed`,
  with or without fully agreeing Jev.
- **Each signal class on real M13 output:**
  - manual-review findings and warning-level findings;
  - `invalid`, `unsupported` and `uncertain` candidates;
  - claims omitted because of the question cap.
- **Each Jev escalation:**
  - category disagreement, an incompatible pair, a contradicted claim;
  - category and subcategory ties and `none` answers;
  - support ties, including a tie that involves `contradicted`;
  - `not_stated`, which does not escalate.
- **Fallback:** every `JevFallbackReason`, on a clean and a manual-review listing, keeps the
  deterministic route and adds its reason.
- **Disabled Jev:** deterministic routing only.
- **Input binding:** mismatched inputs are rejected.
- **The decision contract:** inconsistent decisions are rejected, including `auto_accept`, a
  lowered route and `reject`.
- **Determinism:** reasons are in canonical order, and routing the same inputs repeatedly, or
  rebuilding them, gives the same decision.
- **Seeded property tests:**
  - 300 random Jev outcomes on each of 4 fixtures: the route is never `auto_accept` and never
    lower than the deterministic route, and no deterministic reason is dropped;
  - every category × subcategory × support label (5 × 13 × 3) on a one-claim listing;
  - confidence never changes the route.
- **Offline:** importing `listing_routing` loads no SDK, HTTP, database, FastAPI or settings
  module.

### M14-3 limitations

- **No listing can reach `auto_accept`**, by design (Option A). The automation rate is therefore
  zero. That is expected, not a finding.
- **The rule table is owner-approved, not measured.** Routing accuracy, unsafe auto-accept rate
  and unnecessary review rate stay unmeasured: there are no expected-route labels (see the
  evaluation amendment).
- **The seller-disagreement check is narrow:**
  - it compares the Jev category with the seller's normalized `seller_field` category only;
  - a seller subcategory that differs from Jev's is not an escalation in the approved table;
  - an absent seller category is handled by the M13 `missing_required` finding instead.
- **No integration:** routing is not persisted, not wired to any CLI or endpoint, and not used by
  `decide_listing`.

## M14-4 implementation: offline evaluator (2026-10-21)

### Owner decisions applied

1. **Seller fields:** the source or mapped category, and any available subcategory, are the
   seller fields M13 reads. On clean items the evaluator checks that each normalized seller
   value equals its label. A category label of `none` (a non-product) is not checked. Tests check
   that `jl-1` model inputs contain neither these seller fields nor any label, origin field or
   mapping version, unless the word occurs in the seller's own title or description.
2. **`sports` vs `running`, fixed before any prediction is read:**
   - Each subcategory label carries its scoring policy, set by the mapping:
     `exact` or `excluded_ambiguous_mapping`.
   - Only `sports` may be excluded.
   - An excluded label never enters any denominator, whatever is predicted, and the excluded
     count is reported. A test shows the subcategory metrics are identical for `sports`,
     `running`, `casual` and `none` predictions on the excluded item.
   - **No coarse sports/running metric is reported:** the taxonomy defines no such group. It
     appears in the registry as unmeasured.
3. **Offline evaluator only. No runner.** A live runner remains a separate approval dependency
   before M14-7.
4. **No probability diagnostics or Brier scores.**
5. **Dev split by default.** Evaluating the test split needs `allow_test_split` / `--allow-test-split`,
   and the report records it. Only synthetic fixtures were evaluated in this batch.

### Files

| File | Contents |
|---|---|
| `evals/listing_decisions/contracts.py` | The `le-1` set contract (detailed below), plus `set_sha256`, `load_set` and `dump_set` |
| `evals/listing_decisions/measurability.py` | The `le-measure-1` registry: measured cells, and unmeasured cells with their reasons |
| `evals/listing_decisions/metrics.py` | Label-based task metrics, a separate deterministic-baseline section, and descriptive counts |
| `evals/listing_decisions/corruption.py` | Pair verification, route transitions, attributable detection and the injection non-degradation check |
| `evals/listing_decisions/evaluate.py` | The offline evaluator (report `le-report-1`): raw-hash and outcome binding, seller-field check, split policy, deterministic report bytes |
| `evals/listing_decisions/cli.py`, `__main__.py`, `__init__.py` | `python -m evals.listing_decisions evaluate` |
| `tests/unit/listing_eval/` | Synthetic fixtures (`listing_eval_support.py`, `conftest.py` with a network guard) and four test modules: 69 tests |
| `docs/m14-progress.md` | This record; M14-3's commit status corrected to `5a9cc2f` |

**What the `le-1` contract holds:**
- **Source provenance:** the file SHA-256s, the declared licence and who declared it, and the
  rights caveat.
- **Label kinds:** `source`, `mapped` or `synthetic_corruption`. There is **no human-reviewed
  kind**.
- **Measurability limits:** a `running`, laptop or phone subcategory label is refused.
- **Splits and twins:** a group never crosses splits, and a corrupted item and its twin share
  source, group and split.

No product code, settings, migration, dataset generator or runner was added or changed.

### Metrics

- **Label-based metrics** cover Jev against external labels, on clean items of the evaluated
  split, per source and pooled. The pooled figure carries a warning that sources differ in
  format.
  - **Counts:** `eligible`, `attempted`, `not_attempted`, abstentions (`tie` and `fallback` by
    reason), `predictions`, `correct`, `wrong` and `predicted_none`.
  - **Ratios:**
    - `overall_accuracy` = correct / eligible;
    - `answered_accuracy` = correct / predictions;
    - `coverage` = predictions / eligible.

    Each ratio records its numerator and denominator. A zero denominator gives `null` with a
    reason.
  - **Per label:** precision, recall and F1, each `null` with a reason when undefined, plus a
    confusion matrix.
- **Deterministic baseline:** reported separately. It shows 0 predictions and coverage 0 of
  eligible, and its accuracy is `null`, with the reason that it makes no proposal and is not an
  equivalent predictor.
- **Descriptive counts:** reported apart from the label-based metrics, with no correctness
  implied:
  - route and reason-code counts per arm;
  - Jev tie, `none` and fallback counts;
  - support-label counts;
  - the number of titles containing their category word (disclosed as evidence, not leakage).
- **Unmeasured:** every unmeasured task-quality metric is `null` with its reason, never zero:
  - route correctness, unsafe auto-accept rate and unnecessary review rate;
  - ambiguity;
  - attribute text support;
  - calibration;
  - laptop, phone and running subcategories;
  - a coarse sports/running metric.

### Corruption checks (`corrupt-1` pairs; robustness only)

**Pair verification:**
- Each pair is checked structurally first. Seller identity keys aside, the corrupted listing may
  differ from its twin only under `target_key`.
- The twin's value there must equal `original_value`, and the rule's own condition must hold:
  - `category_swap` changes the category;
  - `out_of_set_subcategory` sets a value outside the taxonomy;
  - `attribute_swap` changes a product attribute whose original value appears in the twin's
    text;
  - `instruction_injection` appends to the description.
- A pair that fails is refused, never reinterpreted.

**Reported per rule and arm, each separately:**
- clean→corrupt route transitions;
- `raised`, `unchanged` and `lowered` counts, recorded for every rule;
- `clean_manual_review`;
- attributable detection.

**Attributable detection:**
- **What counts:** the corrupt decision must carry the rule's exact code, with a subject tied to
  the changed key, and the clean twin's decision must not.
- **Per-rule codes:**
  - `category_swap`: Jev arm only, `jev.category_disagrees_with_seller`. There is no
    deterministic signal, so that arm reports detection as `null` with a reason.
  - `attribute_swap`: `m13.value_conflict` on the key's fields; on the Jev arm also
    `jev.claim_contradicted` on the claims read from that key. If no such claim was asked, the
    pair is `not_assessable`.
  - `out_of_set_subcategory`: `m13.incompatible_subcategory`, `m13.candidate_unsupported` or
    `m13.candidate_invalid` on `subcategory`.
- **What never counts:** a reason the twin already had (counted as `present_in_clean_twin`), and
  any other manual-review reason.

**`instruction_injection` is a non-degradation check only.** It is not evidence that the injection
was detected or resisted, and its detection is `null` with that reason.
- It is checked only for pairs whose M13 evidence is preserved: every candidate and finding is
  unchanged apart from the description's own seller-field candidate.
- For those pairs a lowered route fails the check (`failed_checks`, CLI exit 1).
- Pairs whose injected text adds evidence are counted as `evidence_not_preserved` and not
  checked. The fixture includes one: an injected "64GB RAM" claim creates a `value_conflict`.
- A lowered route for any other rule is recorded, not failed.

### Binding

- Every raw file must match its SHA-256 name and item.
- The set must match its `set_sha256`.
- A recorded outcome (`le-outcomes-1`) must name a known item, at most once, and come from
  `jev`. It must be recorded for this set and this `jl-1` digest, and must answer exactly the
  recomputed state (`state_sha256`, question set version and asked ordinals, via
  `validate_outcome`).
- Any mismatch refuses the whole evaluation (CLI exit 2, nothing written).
- Reports contain no timestamps, so the same inputs give byte-identical reports (tested across
  two fixture directories).

### Commands and results (2026-10-21, `uv run --offline`)

| Command | Result |
|---|---|
| `ruff format evals/listing_decisions` | 5 files reformatted |
| `ruff check evals/listing_decisions` | **All checks passed** |
| `pytest tests/unit/listing_eval` (first full run) | **52 passed** (contracts, metrics, corruption). Before the run, fixture assumptions were checked against real M13 output: brands were added, because a missing brand gives `missing_required`, which made every clean route `manual_review`. Two corruption tests were also corrected for fixture mistakes: an "original not in text" case that was not reachable, and a lowered injection route caused by the fixture. |
| `pytest tests/unit/listing_eval` (after adding the end-to-end and CLI tests, and pinning refusal messages) | **69 passed** in 2.50 s |
| `ruff format` (package and tests) | 2 files reformatted |
| `ruff check` (package and tests) | 5 × I001: `evals` is sorted as third-party under the project config; fixed with `ruff check --fix` |
| `ruff check` / `ruff format --check` (package and tests) | **All checks passed**; **14 files already formatted** |
| `pytest tests/unit/listing_eval tests/unit/listing_decisions` | **240 passed** in 6.23 s |
| After removing an unused test helper: `ruff check` / `ruff format --check tests/unit/listing_eval`, `pytest tests/unit/listing_eval` | **All checks passed**; **6 files already formatted**; **69 passed** in 2.58 s |
| Review fix: removed an assertion in the leakage test that checked nothing (and its now-unused import). Then `ruff check` / `ruff format --check` (package and tests), `pytest tests/unit/listing_eval/test_listing_eval_contracts.py` | **All checks passed**; **14 files already formatted**; **25 passed** in 0.26 s |

**Not run (as instructed):**
- the full unit suite and `tests/unit/test_unit_isolation.py`;
- integration tests;
- any live or Jev call, download or database operation.

### M14-4 limitations

- **Synthetic fixtures only:**
  - No external data has been prepared; that is M14-6.
  - No report over real data exists, so no metric value exists yet.
  - The fixture counts prove only the arithmetic.
- **No runner:** recorded Jev outcomes can only come from a future, separately approved runner.
- **`set_sha256` detects accidental edits only;** it is not a signature.
- **Seller-field check:** it relies on M13's normalized seller category. A source category M13
  cannot normalize would be refused, not repaired.
- **Attributable detection is narrow by design:**
  - `category_swap` has no deterministic signal;
  - `attribute_swap` on a field M13 does not extract from text has no deterministic signal;
  - a swapped claim that was not asked is `not_assessable`.
- **The injection check is non-degradation only.** It covers only pairs whose evidence is
  preserved, and makes no claim of detection or robustness.
- **Unchanged from the evaluation amendment:** the label basis remains external source labels.
  The human-reviewed requirements, route correctness, ambiguity and calibration stay unmet or
  unmeasured.

## Next batch (needs explicit approval)

1. Review and commit M14-4.
2. Then, each needing approval:
   - M14-5: persistence (optional);
   - M14-6: dataset preparation (adapters, mapping, `corrupt-1` generator, frozen split);
   - a live runner, which is an approval dependency before M14-7;
   - M14-7: live evaluation.

M14-5 has not started. No live calls are allowed without a separate approval covering the cap,
prices and tripwire.
