# M16 progress: grounded shopping assistant (first slice)

**Status: IN PROGRESS.** The first slice is implemented and tested with mocked generation only.
M16 is not complete: the acceptance gaps are listed below. Mocked tests are not live-provider
validation.

## Owner decisions (2026-10-22)

- M10's reduced-scope (pilot/dev) closure is accepted as the prerequisite **for M16 development
  only**. Every M10 limitation stays in force and no gate is waived (`docs/MASTER_PLAN.md`,
  M10 row).
- A plain Python workflow, without LangGraph: the flow is fixed and linear with one repair, and
  §4 lists LangGraph as a non-goal without a measured need.
- Generation provider: the Gemini Developer API free tier is the proposed optional provider
  (ADR-009, Proposed). An earlier Anthropic direction was replaced the same day; the drafted
  Anthropic adapter and SDK were removed before any commit and never called.
- Generation is off by default, including on the staging demo. No paid calls, no API-key setup,
  no model downloads, no live calls, no deployment.

## Delivered flow

```text
POST /assistant/answer {message}
 -> normalize (search query rules; 422 on invalid text)
 -> deterministic query understanding
 -> conflict / ambiguity / nothing understood?  -> clarify (no retrieval, no generation)
 -> V3 reranked search via run_reranked_search (top ASSISTANT_EVIDENCE_K = 5)  -- 503 if search fails
 -> no results?                -> abstain (no_matching_products), no generation
 -> generation off?            -> generation_unavailable: "No generated answer is available ...",
                                  products labelled as ordinary search results
 -> generate (call 1)          -> provider failure? generation_unavailable (failure code)
 -> deterministic validation   -> pass: answer | fail: repair once (call 2) -> pass or abstain
```

- **Search reuse:** the private route helper `_reranked_search` was renamed to the public
  `run_reranked_search`, with no behaviour change. It is the single V3 entry point for both
  routes and for the assistant's `RerankedSearchRetriever` adapter (`api/assistant.py`). The
  assistant domain (`src/ecommerce_search/assistant/`) depends only on its own
  `CatalogRetriever` protocol and has no HTTP dependency.
- **Generation output is narrow:** the model may only choose `answer` or `abstain`, select up
  to 3 retrieved product IDs, name 1–4 catalog fields per selection, and write a summary of at
  most 400 characters. Facts shown for a selection are rendered by the server from the catalog
  record. The summary is labelled "Generated summary (unverified)".
- **Deterministic checks:**
  - every selected ID must be retrieved, and IDs must not repeat;
  - `answer` needs products and `abstain` must have none;
  - the summary may not contain digits, price, offer, budget, availability or delivery words,
    links or markup;
  - explicit constraints are re-checked on catalog fields: category, brand, price bounds, and
    (since slice 2) RAM, storage capacity, storage type and storage interface by exact equality,
    as the search filter policy applies them. A field the catalog record does not give is
    `not_verified`: never passed, never inferred from title or description text. A
    recommendation with any `not_verified` check is `verification: not_all_verified`, and the UI
    says "Not a verified match".

## Slice 2 (2026-10-22): RAM/storage evidence and comparison

- **Catalog fields:** authoritative typed fields already exist: `laptop_specs` and
  `phone_specs` have `ram_gb` and `storage_gb`, and `laptop_specs` also has `storage_type` and
  `storage_interface`, all nullable. The retriever adapter now reads them for exactly the
  retrieved product IDs with the new `read_candidate_records` (`search/reranked.py`). It is a
  by-ID read in its own read-only REPEATABLE READ snapshot that reuses the existing
  candidate-row read and record validation. No search runs, and no other product is read. The
  read is separate from the search snapshot, so a catalog change between the two reads is
  possible but unlikely. A failed or invalid read is a 503, never a guess.
- **Evidence and prompt:** `EvidenceProduct` and the model's evidence record carry the four
  fields (null when not given). Selections may cite them. Versions: `asst-policy-2`,
  `asst-evidence-2`.
- **Comparison:** the user selects 2–3 retrieved evidence products in the current response,
  and the UI shows a side-by-side table built only from those catalog values (`comparison.ts`).
  Missing values show as "not given". A "lowest" price or "highest" rating/RAM/storage note is
  shown only when at least two known values differ, and never across currencies. No request is
  sent, nothing is retrieved again, and nothing is generated. It works with generation off.
- **Live smoke tooling (not run):** `scripts/m16_live_smoke.py` has a hard budget of 5
  generation requests including repairs, synthetic records only, stop on the first provider
  failure, and a dry run by default. It sends requests only with `--confirm-live`.
- **Bounds:** at most 2 generation calls; SDK retries off; 20 s per-request timeout (not a
  wall-clock bound); no repair after 30 s; `max_output_tokens` = 2048.
- **Telemetry:** one `assistant_answer_completed` log line with codes, counts, token counts
  (null = unknown) and timings, plus the existing request ID. The message, evidence, summary,
  prompt and keys are never logged. A cost estimate is produced only from owner-supplied prices.
  Otherwise the response reports `unknown_pricing_not_configured` or `unknown_usage`, never zero.
- **UI:** a "Shopping assistant" tab next to Search (WAI-ARIA tabs; both panels stay mounted). It
  has loading, error (fixed messages), clarify, answer, abstain and generation-unavailable
  states. Evidence items are numbered E1…En and linked from each suggestion. All catalog and
  generated text is rendered as React text only.

## Files

Created:
- `src/ecommerce_search/assistant/`: `__init__.py`, `models.py`, `clarify.py`, `prompt.py`,
  `retrieval.py`, `validate.py`, `workflow.py`
- `src/ecommerce_search/generation/`: `__init__.py`, `provider.py`, `gemini_provider.py`
- `src/ecommerce_search/api/assistant.py`
- `tests/unit/assistant/`: `test_assistant_workflow.py`, `test_assistant_api.py`,
  `test_gemini_generation_provider.py`
- `frontend/src/domains/assistant/`: `AssistantPage.tsx`, `AssistantPage.test.tsx`, `api.ts`,
  `types.ts`
- `frontend/e2e/assistant-fixture.spec.ts`
- `frontend/e2e/fixtures/synthetic-assistant-*.json` (7 files)
- `docs/decisions/ADR-009-generation-provider.md`, `docs/m16-progress.md`

Changed:
- `src/ecommerce_search/api/reranked.py`: the rename above, plus a docstring
- `src/ecommerce_search/api/app.py`: assistant router; optional provider built lazily
- `src/ecommerce_search/api/dependencies.py`: `get_generation_provider`
- `src/ecommerce_search/config/settings.py`: assistant settings, `GEMINI_API_KEY` as `SecretStr`
- `pyproject.toml`, `uv.lock`: `google-genai==2.32.0`
- `.env.example`: assistant settings (provider `none`, empty key)
- `tests/unit/conftest.py`: isolates `ASSISTANT_`, `GEMINI_` and `GOOGLE_` variables
- `tests/unit/search/test_reranked_api.py`, `tests/unit/search/test_filtered_api.py`: renamed
  helper; route lists include `/assistant/answer` (the frozen V0–V3 contract digests are
  unchanged)
- `tests/unit/test_settings.py`: the secret-field set includes `gemini_api_key`
- `tests/unit/test_frontend_fixture_contract.py`: assistant fixtures checked against the
  Pydantic contract
- `frontend/src/app/App.tsx`, `frontend/src/app/app.css`, `frontend/e2e/fixtures.ts`,
  `frontend/e2e/fixtures/README.md`, `frontend/playwright.config.ts`
- `docs/MASTER_PLAN.md`: status of M16

## Local verification (2026-10-22, Windows)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | All files formatted |
| `uv run python -m pytest tests/unit/assistant tests/unit/test_settings.py tests/unit/test_frontend_fixture_contract.py tests/unit/test_compose_config.py tests/unit/test_app.py -q -p no:cacheprovider` | 304 passed |
| `uv run python -m pytest tests/unit/search/test_reranked_api.py tests/unit/test_settings.py -q -p no:cacheprovider -m "not integration and not real_model"` | 378 passed (run before the Gemini switch; the reranked module was not changed afterwards) |
| `npm run typecheck` | passed |
| `npm test` (Vitest) | 57 passed (3 files) |
| `npm run build` | built |
| `npm run e2e:fixture` (Playwright, mocked API) | 12 passed (6 Search + 6 assistant) |
| Slice 2: `uv run ruff check .`; `uv run ruff format --check .` | All checks passed; 433 files formatted |
| Slice 2: unit tests for assistant, reranked sources/API, filtered, hybrid, settings, app, compose and the fixture contract | 787 passed |
| Slice 2: assistant tests after adding the smoke script | 95 passed |
| Slice 2: `uv run python -m pytest tests/integration/test_assistant_catalog_facts.py -m integration` (local pgvector container; throwaway database) | 2 passed |
| Slice 2: `npm run typecheck`; `npm test`; `npm run build`; `npm run e2e:fixture` | passed; 65 passed (4 files); built; 13 passed |
| Slice 2: `uv run python scripts/m16_live_smoke.py` (dry run) | plan printed, `key_configured: false`, no request |
| After the CI fix: `uv run python -m pytest tests/unit/search/test_filtered_api.py tests/unit/search/test_hybrid_api.py tests/unit/search/test_reranked_api.py tests/unit/assistant tests/unit/test_settings.py tests/unit/test_app.py tests/unit/test_compose_config.py tests/unit/test_frontend_fixture_contract.py -q -p no:cacheprovider -m "not integration and not real_model"` | 755 passed |

The full local unit suite was not run. CI runs the broad regression; see "CI" below.

## Limitations (honest)

1. **No live provider call has been made.** Whether `gemini-3.5-flash-lite` accepts the
   structured-output request, how it behaves, its latency, its token usage and its free-tier
   limits are all unverified.
2. **Validation passing is not faithfulness.** Checks prove that IDs are retrieved, that cited
   fields come from the catalog, that checkable constraints hold, and that the summary has no
   numbers, prices or availability wording. They do not prove that the remaining summary words
   are supported. A test documents this explicitly (`test_validation_passing_does_not_require_supported_wording`).
3. **Prompt-injection resistance is not shown.** Catalog text is JSON-escaped (`<`, `>`, `&`)
   inside a delimited data block, and mocked tests show that a model following injected text is
   rejected when it cites a non-retrieved ID. Neither shows live-model robustness. M17 owns
   that evaluation.
4. The summary rules are conservative and may reject acceptable text (for example any use of
   "budget" or "free"). Such a draft is repaired once, then the assistant abstains. False-refusal
   rates are unmeasured.
5. Clarification is decided by the deterministic parser only. A request with no recognized term
   (for example an unknown product type) is asked to clarify rather than searched.
6. Single turn only; no conversation history.
7. Free-tier content is used by Google to improve its products (ADR-009), so the provider must
   not receive real customer data.
8. The synthetic UI fixtures were generated from the Pydantic models with fake retriever and
   generator objects; latencies in them are fixed placeholder values.

## Remaining acceptance gaps (MASTER_PLAN Milestone 16)

- ~~Comparisons~~: done in slice 2 as a deterministic comparison of selected retrieved
  products. Comparison requested in the message text (for example "compare A and B") is not
  parsed.
- ~~RAM/storage re-check~~: done in slice 2 on typed catalog fields; missing fields stay
  `not_verified`.
- **The generation-provider ADR is Proposed, not Accepted.** The local live smoke trial (max 5
  requests) waits for the owner to confirm a free-tier key. Even after it, quality, latency and
  cost are M17 measurements.
- **Grounded recommendations against a live model** are unverified (limitations 1–3).
- **M17 (not M16):** the human-reviewed answer set; faithfulness, relevance, citation, unsupported-claim,
  constraint, clarification, abstention and false-refusal metrics; prompt-injection and
  malicious-listing suites; measured latency, tokens and cost.

## CI

- Run 39811150311 (commit `c58937e`): **failure.** Frontend, fixture Playwright and pgvector
  integration jobs passed. The backend hermetic unit job failed with 3 failed, 4977 passed,
  4 skipped. Two causes, both introduced by this milestone:
  1. A new settings rule (`assistant_evidence_k` <= `search_candidate_k`) made previously valid
     search configurations invalid (two hybrid/filtered tests use a small candidate limit). The
     rule was removed; the evidence depth is now capped at `search_candidate_k` when used.
  2. `test_filtered_api.py` has its own route-list assertion, which now includes
     `/assistant/answer`. The frozen-contract digest was unchanged.
- Run 39813357381 (commit `503cb1d`, the fix): **success.** All 4 jobs passed: backend lint +
  hermetic unit suite, frontend typecheck/unit/build, pgvector integration suite, and fixture
  Playwright against the FastAPI-served build. CI never calls a live generation provider.

## Local live smoke trial (2026-10-22, owner-approved)

Approval: at most 5 generation requests in total including repairs, synthetic records only,
bounded tokens and timeouts, no paid fallback, stop on any error.

- **Key:** `GEMINI_API_KEY` is in the git-ignored `.env`. Detection was confirmed by the dry run
  (`key_configured: true`). No part of the key was printed, logged or committed.
- **Free tier:** the API does not expose whether billing is attached. The free-tier status rests
  on the owner's check in Google AI Studio. No billing change was made.
- **Model access:** one metadata request (`models.get`, no generation, outside the 5-request
  budget) succeeded. `models/gemini-3.5-flash-lite`, version `3.5-flash-lite-07-2026`, supports
  `generateContent`, has a 1,048,576-token input limit and a 65,536-token output limit, and has
  thinking enabled.
- **Trial** (`uv run python scripts/m16_live_smoke.py --confirm-live`, exit 1): **2 of 5
  requests sent; stopped on a provider error.**
  - Request 1 (case "dell laptop with 16gb ram under rs 50000"): returned without a provider
    failure, and its text parsed into the `GroundedDraft` schema (otherwise the adapter would
    have reported `invalid_response` and the trial would have stopped there). The draft then
    failed deterministic validation, so the workflow sent one repair.
  - Request 2 (the repair): the provider returned a 5xx error (`server_error`). The trial
    stopped as required: no retry, case 2 (synthetic injection-titled record) did not run.
- **Not recorded, because the script lost them:** the failed check codes of request 1, its token
  counts and both requests' latencies. The script recorded only per-case results, and the
  failing repair ended the case. The script now logs every request (failure code, parsed or
  not, draft outcome and IDs, tokens, latency, and the repair's check codes). That change is
  covered by a mocked test; no further request was sent.
- **What this shows:** the key, the model access and one structured-output round trip work
  (`response_mime_type` + `response_json_schema` with this model). **What it does not show:**
  that drafts usually pass validation (the one observed draft did not), output quality,
  faithfulness, injection resistance, latency or cost. This is not M17 evaluation.
- **Open:** another trial needs new owner approval. The cause of the 5xx is unknown.
