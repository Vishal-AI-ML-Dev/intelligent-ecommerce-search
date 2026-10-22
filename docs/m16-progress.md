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
  - explicit constraints are re-checked on catalog fields: category, brand and price bounds.
    RAM, storage and storage type are not fields of the retrieved record, so they are reported
    as `not_checked`, never as passed.
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

- **Comparisons** of specific products are not a separate flow yet. Facts are shown side by
  side, but no deterministic comparison is produced.
- **The generation-provider ADR is Proposed, not Accepted.** It needs the owner-approved local
  live trial (ADR-009, "Required before any live trial") and measured quality, latency and cost.
- **Grounded recommendations against a live model** are unverified (limitations 1–3).
- RAM and storage constraints cannot be re-checked on the retrieved record (`not_checked`).
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
- The fix commit and its run are recorded below.
