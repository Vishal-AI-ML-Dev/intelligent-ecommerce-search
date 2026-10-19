# Milestone 12 — Jev V4 experiment: tooling, runs and report

## Status

**M12 is closed as development-scoped, partially met (owner decision, 2026-10-19).**
- The original Milestone 12 requirements in `docs/MASTER_PLAN.md` are unchanged and are **not**
  all fulfilled.
- See [Owner decision](#owner-decision-2026-10-19) for the exact met, unmet, deferred and
  optional items, and the [M12 experiment report](#m12-experiment-report-2026-10-19) for the
  evidence.

- **Batches.** Both are git-ignored under `data/processed/` and were sealed before any live call.
  - Diagnostic `m12_diagnostic`: 40 queries, `dq-001`–`dq-040`, written by Notion AI
    (`author_tool: notion-ai`).
  - Calibration `m12_calibration_cal1`: 80 queries, written by Claude in Claude Code
    (`author_tool: claude-code`), with category-only labels (`qd-cat-1`).
  - All labels are by one human annotator, `ann-1`, query text only, with AI-assisted rule
    clarification and at least one AI-assisted label confirmation. The cal1 pre-seal review was
    also AI-assisted. This is not fully unaided annotation.
- **Runs (owner-approved).**
  - `dry-run-001`: mocked and synthetic, not Jev output.
  - `live-001`: the 40 diagnostic queries.
  - `cal1-live-001`: the 80 calibration queries.
  - Three live V3/V4 comparison queries.
  - No other live call is approved.
- **Built.** The diagnostic and gate tooling, the `qd-report-1` report, category-only annotation,
  and an experimental, ungated, opt-in V4 at `/search/routed` (`docs/v4-jev-routing-m12.md`).
- **Unchanged.** `jq-1`, `fp-1`, the V0–V3 endpoints and every default. The confidence gate is
  off, and no threshold is frozen.
- **Not built:**
  - hard filters or `fp-2`, and any gate applied to search;
  - a frozen threshold, a fitted calibration or approved acceptance criteria;
  - main-set authoring;
  - a bootstrap or McNemar framework;
  - counterfactual retrieval.

All M12 data is **development evidence only, never held-out evidence**.

Code: `evals/query_decisions/` (`contracts`, `labeling`, `runner`, `evaluate`, `cli`; category
gate tooling in `partitions`, `gate`, `category_metrics`, `acceptance`, `gate_cli`; `reporting`),
`src/ecommerce_search/search/routed.py`, `src/ecommerce_search/api/routed.py` and
`evals/runners/compare_v3_v4.py`.
Tests: `tests/unit/query_decisions/` and `tests/unit/search/test_routed_api.py` /
`test_compare_v3_v4.py` (synthetic fixtures, fully mocked, no network).

## Workflow

The work directory defaults to the git-ignored `data/processed/m12_diagnostic/`. The commands
are listed in order; each one is described below.

1. Author `queries.jsonl` by hand.
2. `uv run python -m evals.query_decisions label --annotator ann-1`
3. `uv run python -m evals.query_decisions seal`
4. `uv run python -m evals.query_decisions run --run-id dry-1 --max-attempts 40`
5. A live run: only with separate owner approval for that run (see below).
6. `uv run python -m evals.query_decisions evaluate --run-id <id> --out <new file>`

**1. Queries (`queries.jsonl`).** Written by the query author: UTF-8 JSONL, one object per
line, schema `qd-2`. Any other `schema_version` is refused with a clear error; there is no
migration. For example:

```json
{"schema_version": "qd-2", "dataset_id": "m12-diag-dev", "query_id": "dq-001", "text": "hp laptop 16gb ram", "source": "human_authored", "author_handle": "ann-1", "author_tool": null, "language_style": "english", "stratum": "explicit_category"}
```

- `query_id` is `dq-NNN`.
- `text` must already be normalized as the search API does it: single spaces, trimmed, at most
  200 characters, no control or format characters.
- `source` records authorship only. It is separate from annotation: labels always come from
  the human annotator in step 2, whatever the source.

  | `source` | `author_handle` | `author_tool` |
  |---|---|---|
  | `human_authored` | required (`ann-<n>`) | must be `null` |
  | `ai_authored` | must be `null` | required (a tool name such as `notion-ai`; never an `ann-<n>` handle) |
  | `synthetic_test_fixture` (software tests only) | must be `null` | must be `null` |

  - **AI-authored queries** are development data only, never held-out evidence.
  - **Synthetic test fixtures** are always refused in live mode, and every report over them is
    marked synthetic.
- `language_style` is `english` or `hinglish`.
- `stratum` is a free slug such as `explicit_category`, `misspelled_category`, `intent_only`,
  `out_of_catalog`, `ambiguous` or `noisy`.

**2. Blind labelling.** The session shows the query id, progress and the query text only. The
labelling module imports no parser, provider, runner or evaluator; a test enforces this. An invalid
`--annotator` handle is refused before the lock or any prompt. Answers:

- category: `laptop`, `phone`, `shoes`, `headphones` or `none`; `ambiguous:<a>,<b>[,...]`; or
  `unclear`.
- intent: `gaming`, `coding`, `student`, `lightweight`, `premium`, `comfort`, `travel`,
  `office` or `none`; `multi:<a>,<b>[,...]` when several intents are stated;
  `ambiguous:<a>,<b>[,...]`; or `unclear`.

`none` is a decision, not an abstention. Each label is saved atomically before the next query,
and `q` stops the session without saving anything partial. One annotator per `labels.jsonl`.

**3. Seal.** `seal.json` binds the canonical hashes of the queries and labels before any call.
It is written once. Afterwards labelling is refused, and any change to either file is refused by
the runner and the evaluator.

The seal also records how many queries each source contributed, and they must add up to the
query count. `synthetic` is true exactly when a synthetic fixture is sealed; an AI-authored
query does not make the batch synthetic. On every load the counts are checked against the sealed
queries, and a mismatch is refused. The seal's `annotator_handle` must also equal the handle on
the sealed labels. This check covers the handle only: `sealed_at` and `seal.json` itself are not
hashed, so the seal is not tamper-proof.

**4. Dry run.** The real provider runs against an in-memory transport that returns one fixed,
synthetic, valid `none`/`none` answer with zero tokens. No network, no key, and the output is
not Jev output.

**5. Live run: requires separate owner approval for each run.** One run (`live-001`) has been
approved and run. The command is:

```
run --run-id <id> --max-attempts N --live
    --price-input-usd-per-million P --price-output-usd-per-million P
    --price-checked-on YYYY-MM-DD --max-estimated-usd X [--approve-ai-authored]
```

- **Check order (tested).** The CLI first loads and verifies the sealed batch, then refuses
  the run in this order (`live_refusal`):
  1. any of the two prices, the check date or the tripwire is missing;
  2. the batch contains a synthetic fixture. This is refused even with `--approve-ai-authored`;
  3. the batch has AI-authored queries and `--approve-ai-authored` is absent;
  4. `--approve-ai-authored` is given but the batch has no AI-authored queries.

  Only after those checks does it construct `Settings`, which reads `.env` and the key. Tests confirm that rejected input never
  constructs `Settings`, a provider factory or a provider, and creates no run directory.
  `run_diagnostic` repeats the checks as a second line of defence.
- **Model pin.** Only `jev-1.13.0` is approved for live calls.
  - A different `JEV_MODEL` in the settings is refused before any provider is built.
  - A live run header with another model is refused before `run.json` or any dispatch is
    written.
  - The settings' own model validation is unchanged.
- The provider is built from `Settings` with retries forced to 0 and calls in flight to 1. All
  other Jev settings are copied unchanged, and the settings object itself is not modified (both
  tested with fake settings and a spy provider). It does not need `JEV_ENABLED` and changes no
  setting or file.
- **`--approve-ai-authored`** records the operator's decision in `run.json`
  (`ai_authored_approved`, schema `qd-run-2`).
  - **It does not authorize paid calls.** Every live run still needs separate owner approval.
    Only `live-001` was approved.
  - A restart with a different value is refused.
  - The flag is refused for dry runs.
- The prices are supplied by the operator together with the date they were checked. Nothing in
  the code hard-codes a price.

**6. Evaluate.** Writes an offline JSON report. It never overwrites an existing file.

## Runner guarantees (`runner.py`)

- **Call settings:** `max_retries=0` and `max_in_flight=1` are fixed in `run.json`, and calls
  are sequential, so one dispatch is at most one HTTP attempt.
- **Dispatch before the call:** a `dispatch` event is appended and fsynced before the provider
  is called, then the `result`. A dispatch without a result is an attempt with an unknown
  outcome.
- **No resends:** each query is dispatched at most once, and there is no resend path.
  `max_attempts` may not exceed the number of queries.
- **Fixed settings:** `run.json` fixes the mode, cap, prices and seal binding. Restarting with
  different values is refused.
- **One invocation per run:** `runs/<run-id>/run.lock` is created exclusively before `run.json`
  and the journal are read, and held until the last journal line is written. A second
  invocation of the same run refuses before it builds a provider or dispatches, and changes
  nothing. The lock is removed on normal and error exits.
  - A lock left by a hard kill is never removed automatically. Check that no invocation is
    running and review the journal (for example, for a dispatch without a result) before
    deleting it by hand.
  - The lock covers one run id. A different run id over the same batch is a separate run and
    sends the queries again.
- **Stop conditions:**
  - the attempt cap;
  - the estimated-spend tripwire;
  - any `auth_error`, `model_mismatch`, `request_rejected`, `saturated` or `provider_closed`;
  - 3 consecutive transient fallbacks;
  - `invalid_response` above 2% once there are at least 20 results (so at 20 results, a single
    invalid response stops the run);
  - the API key appearing in a serialized result (that result is dropped);
  - an unexpected exception (only its type name is recorded);
  - an interruption.
- **Resuming:** after a stop or an unknown outcome, the run refuses to continue until it is
  started with `--resume-approved`. That approval is itself journalled.
- **The estimated spend is not a billing cap.** It is computed from returned token counts.
  Failed or cancelled requests may be billed without returning usage, and no provider-side
  spend limit is known.
- **Torn or corrupt journal.** If the journal is corrupt or its last line is incomplete, the run
  stops and refuses to continue.
  - Do not edit or truncate the journal, and do not resume the run.
  - Affected queries are never resent automatically.
  - Keep the journal as evidence; any recovery needs a separate, reviewed decision.
  - No recovery tooling exists.
- **Audit limitation.** A resume approval is journalled with a timestamp only, not with who
  approved it or why. Record the approval decision separately.
- **Clean CLI errors.** These print one fixed error line, exit with code 2, and show no input or
  traceback:
  - an unknown run id passed to `evaluate`;
  - `JevConfigurationError` while the provider is built. This happens before any dispatch.

  Other unexpected exceptions are not swallowed.
- **Redacted output:** the journal holds query ids and validated `JevOutcome`s only. The console
  prints counts only.

## Verification (2026-10-18)

- **Focused tests:**
  `uv run --offline pytest tests/unit/query_decisions -p no:cacheprovider -rA --basetemp <fresh scratchpad folder>`
  gave **74 passed, exit code 0**. Every test uses synthetic fixtures and mocks; none touches
  the network, `.env` or a real key.
- **After fixing review findings 1–4:** the same focused command, with a new temp folder, gave
  **81 passed, exit code 0**. Ruff was clean.
- **After adding `qd-2` (AI-authored sources):** the same focused command, with a new temp
  folder, gave **109 passed, exit code 0**. Ruff was clean.
- **After adding the AI-authored end-to-end test:** the same focused command, with a new temp
  folder, gave **110 passed, exit code 0**, and Ruff was clean. This was the last run before
  commit `9f605a9` and is the count in its commit message. The source is the saved session
  transcript; the pytest log itself was a temporary file.
- **Ruff:** `uv run --offline ruff check .` passed, and `ruff format --check` reported the new
  files as already formatted.
- **Not run:** the full unit suite and the integration tests.
- **Earlier setup errors, cause unconfirmed.** One earlier combined run (the new tests plus
  `test_jev_provider.py`, `test_decision_provider.py`, `test_filter_policy.py` and
  `test_settings.py`) reported **23 setup errors** in `test_settings.py`.
  - The traceback is unavailable: the output was truncated and not saved.
  - The 23 errors match exactly the 23 tests in that file that use `tmp_path`.
  - pytest's shared temp folder (`pytest-of-<user>`) was found recreated with restarted
    numbering, which suggests something deleted it during the run.
  - What deleted it is unknown, and nothing implicates the M12 code.
  - Later runs of the same combination passed.
- **No real data and no live calls as of 2026-10-18.** The batch, seal and runs came later;
  see [live-001](#live-001-2026-10-19). The tests were not re-run for that run or for this
  documentation.

## Diagnostic report (`evaluate.py`)

- **Deterministic decisions** are recomputed **at evaluation time** from the sealed text. They
  are not recorded during the run.
  - The report records the parser and lexicon version labels (`deterministic.parser_versions`,
    `deterministic.lexicon_versions`).
  - These are declared constants, not proof that the parser source is identical to an earlier
    evaluation.
- **Jev predictions** are the unique top label, which may be `none`. Otherwise Jev abstains, and
  the reason is recorded as `tie`, `fallback`, `unknown` or `not_attempted`.
- **Report format `qd-diagnostic-2`** (current). `live-001/report.json` was written in
  `qd-diagnostic-1` and is kept unchanged; see "Report format versions" below.
- **Scoring** is defined in the `evaluate` module docstring:
  - `correct`, `wrong` and `predicted_none` (a missed decision) against a single label;
  - `correct_none` and `wrong_commit` against gold `none`;
  - `commit_in_candidates`, `commit_outside_candidates` and `none_on_ambiguous` (no label
    committed) against an ambiguous gold;
  - `in_set`, `out_of_set` and `predicted_none` (a missed decision) against a multi-intent gold;
  - `excluded_unclear` for unclear golds.
- **Reported for each question:**
  - gold kinds;
  - deterministic outcome counts, which show missing and wrong deterministic decisions;
  - Jev outcomes and abstention reasons;
  - Jev validity and fallbacks, and the run's token totals;
  - D/J disagreements, with the outcome pairs and query ids;
  - where the deterministic parser abstains: `deterministic_abstains_total` (every such
    query) and `deterministic_abstains_scored` (unclear golds excluded), and Jev's outcomes and
    correct and incorrect fills over the scored ones only.
- **Sources.**
  - The report shows how many queries each source contributed (`query_sources`), any AI tools
    named (`ai_author_tools`), and `source` on every row.
  - `evidence_notes` always states that the report is development evidence only, never
    held-out evidence.
  - It adds a note when there are AI-authored queries or synthetic fixtures.
- **Counts only.** There are no intervals, thresholds or retrieval metrics.
- **What deterministic-first gating can change.** It could change only the queries where the
  parser abstains, so it improves missing-decision coverage. It cannot correct a wrong
  deterministic decision.

### Report format versions

| Version | Change |
|---|---|
| `qd-diagnostic-1` | Original. `predicted_none` also covered `none` on an ambiguous gold. `deterministic_abstains` and `jev_on_deterministic_abstains` included unclear golds (as `excluded_unclear`); `correct_fills` and `incorrect_fills` did not. |
| `qd-diagnostic-2` | `none` on an ambiguous gold is `none_on_ambiguous`. `deterministic_abstains` is replaced by `deterministic_abstains_total` and `deterministic_abstains_scored`; `jev_on_deterministic_abstains` covers scored queries only. |

The `live-001` figures below were derived from the `qd-diagnostic-1` report with unclear golds
excluded and the ambiguous cases separated by hand, so they already follow the
`qd-diagnostic-2` definitions. The saved report was not regenerated.

## live-001 (2026-10-19)

One owner-approved live diagnostic run over the sealed batch. Everything below comes from the
saved `run.json`, `journal.jsonl` and `report.json` in
`data/processed/m12_diagnostic/runs/live-001/` (git-ignored). No further API call was made to
write it.

**Evidence scope.**
- The 40 queries are AI-authored (Notion AI).
- One human annotator, `ann-1`, labelled them with AI-assisted clarification of the rules and
  answer syntax.
- Labels were sealed before the run and were not changed after it.
- This is a development diagnostic only. It is **not held-out evidence**, and the report says
  so (`evidence_scope`, `evidence_notes`; `synthetic: false`).

### Evidence file hashes (recorded 2026-10-19)

SHA-256 of the saved files as they are on disk, under `data/processed/m12_diagnostic/`
(git-ignored, not committed). These hashes let a later reader detect a change to those bytes;
they do not prove when the files were written or who wrote them.

| File | Bytes | SHA-256 |
|---|---|---|
| `queries.jsonl` | 10,329 | `6f0c9ae30486fed33ab6e607e495813ebe6c1e8efd82da81736d816bb16e7d0a` |
| `labels.jsonl` | 16,276 | `d805dce2053220d32e1a8b9357faa91305813cf7e399dfb866c2ca0c53c2f4e0` |
| `seal.json` | 492 | `b1afc71566869baa7c3a20cbfc8af65e5169a4d850d21a6f28e8175059b53965` |
| `runs/live-001/run.json` | 494 | `f400570c3dad7abe1b7029480e0d853877e21e4abc27857d3c0e65d8f7604d51` |
| `runs/live-001/journal.jsonl` | 35,880 | `5cfbfc692445904369a277c766da0aa7e49f02e3b86fdc446f4613d5e7f49807` |
| `runs/live-001/report.json` (`qd-diagnostic-1`) | 27,634 | `012b621e27d21f92e6a89d11c3cfd59b6e56dd6ddb2f515d02abb70b3ea83877` |

### Run settings and bindings

| Item | Value (from `run.json` unless noted) |
|---|---|
| Run id, mode | `live-001`, `live` (`qd-run-2`) |
| Model | `jev-1.13.0` requested; `jev-1.13.0` returned on all 40 results (journal) |
| Endpoint | `https://api.typesafe.ai`, checked just before dispatch as a true/false comparison in a separate settings load (no values printed); the run loads settings itself, and the URL is not stored in `run.json` |
| Attempt cap, retries, in flight | 40, 0, 1 |
| AI-authored approval | `ai_authored_approved: true` (operator decision; not itself a paid-call approval) |
| Prices | $0.042 per million input tokens, $0 per million output tokens; checked 2026-10-19 at https://docs.typesafe.ai/models (the page shows no last-updated date) |
| Estimated-spend tripwire | $0.05 (an estimate from returned usage, not a billing cap) |
| Question set | `jq-1` |
| Queries hash (canonical) | `480ce1ca0cd354b2494b1652272a23e4d6ddb720197d8fc3aef0dd1567e11211` |
| Labels hash | `d805dce2053220d32e1a8b9357faa91305813cf7e399dfb866c2ca0c53c2f4e0` |
| `queries.jsonl` file SHA-256 | `6f0c9ae30486fed33ab6e607e495813ebe6c1e8efd82da81736d816bb16e7d0a` |
| Seal | 40 queries (40 AI-authored, 0 human-authored, 0 synthetic fixtures), annotator `ann-1`, sealed 2026-10-19T02:57:30Z |

The attempt timeout, retry budget and deadline were taken from the settings defaults at run time.
They are not written to `run.json`, so they are not recorded here.

### Run outcome

| Measure | Value |
|---|---|
| Attempts / results / valid results | 40 / 40 / 40 |
| Fallbacks / unknown outcomes / not attempted | 0 / 0 / 0 |
| Stop condition | none; the journal ends with `complete` |
| Jev abstentions (no unique top label) | 0 |
| Tokens | 25,801 input, 5,596 output |
| Estimated cost | $0.001084 (25,801 × $0.042 per million; an estimate, not billing data) |
| Per-attempt latency (`latency_ms` measured by the provider client, n = 40) | min 296 ms, median 325 ms, mean 365 ms, p95 442 ms, max 1,190 ms (the first attempt). The p95 here is a linear interpolation between order statistics (it matches Python's `statistics.quantiles(method="inclusive")`, 441.8 ms; the method was not recorded when this row was written). The `qd-report-1` report uses nearest rank instead and gives p95 418.9 ms; its p99 at n = 40 is the maximum, not a tail estimate. |
| Wall clock, first dispatch to `complete` | 19.7 s, sequential |

One sequential run of 40 calls is not a latency benchmark and says nothing about latency under
load.

### Scoring definitions used below

- **D** is the deterministic parser, recomputed at evaluation time (`qu-1`, lexicon `1`).
  **J** is Jev's unique top label.
- **Unclear golds are excluded** from every denominator: 5 for category and 6 for intent. The
  denominators are therefore 35 for category and 34 for intent.
- **Fill:** a query where D abstains and J's answer is scored.
  - **Correct fill:** J outcome `correct` (single-label gold, same label) or `in_set`
    (multi-intent gold, J picked one of the stated intents).
  - **Incorrect fill:** J outcome `wrong` (single-label gold, another label), `wrong_commit`
    (gold `none`, J committed to a label), `commit_in_candidates` or
    `commit_outside_candidates` (ambiguous gold, J committed) or `out_of_set` (multi-intent
    gold, J outside the set).
  - **Missed fill:** a single-label gold where J answered `none` (`predicted_none`). This is
    neither a correct nor an incorrect fill.
  - J `none` on gold `none` (`correct_none`) and J `none` on an ambiguous gold are not fills.
    No label is committed.
- **Filter-outcome view (hypothetical).** This shows what a label would mean as a search
  constraint. On gold `none`, a D abstention applies no constraint, which is the correct
  no-filter behaviour, even though the scorer counts it as `abstain`. Nothing here was applied
  to search: `fp-1` and every endpoint are unchanged.

**Zero incorrect fills, checked.** In every `per_query` row where D returned no decision, J's
outcome was checked against the incorrect-fill set above. The count was 0 for category (20
scored D abstentions) and 0 for intent (28 scored D abstentions). That matches `incorrect_fills`
in the report.

### Category (35 scored queries)

| Gold | n | D | J |
|---|---|---|---|
| Single category | 30 | 15 correct, 15 abstain, 0 wrong | 28 correct, 2 `none`, 0 wrong |
| `none` | 3 | 3 abstain (correct no-filter) | 3 `correct_none` |
| `ambiguous` | 2 | 2 abstain | 2 `none` (no commit) |

- **Fills (D abstains, 20 scored):** of the 15 single-category golds, 13 were correct fills, 2
  were missed fills and none were incorrect. J answered `none` on all 3 gold-`none` queries and
  both ambiguous queries.
- **Both decided:** 15 queries, all single-category. D and J agreed on all 15 and both were
  correct.
- **Filter-outcome view:**
  - D alone gives 15 correct filters, 3 correct no-filters, 15 missed single-category filters,
    2 ambiguous queries without a filter and 0 wrong filters.
  - D first with J on D's abstentions would give 28 correct filters, 3 correct no-filters, 2
    missed, 2 ambiguous without a filter and 0 wrong filters.

### Semantic intent (34 scored queries)

| Gold | n | D | J |
|---|---|---|---|
| Single intent | 11 | 4 correct, 1 wrong, 6 abstain | 7 correct, 1 wrong, 3 `none` |
| `none` | 18 | 17 abstain (correct no-filter), 1 `wrong_commit` | 17 `correct_none`, 1 `wrong_commit` |
| `multi` | 5 | 5 abstain | 5 `in_set` |

- **Fills (D abstains, 28 scored):**
  - Of the 6 single-intent golds, 4 were correct fills and 2 were missed fills.
  - All 5 multi-intent golds were `in_set` fills.
  - All 17 gold-`none` queries got `correct_none`.
  - There were no incorrect fills.
- **Both decided (6):** D was correct on 4, `wrong` on 1 and `wrong_commit` on 1. J agreed with D
  on 5 of them, including the same `wrong` and `wrong_commit`. The one disagreement is `dq-027`,
  where D was correct and J answered `none`.
- **Wrong commits are not fills.** J's single `wrong` and single `wrong_commit` were both on
  queries D had already decided, with the same label. Deterministic-first gating would not have
  consulted J there and could not have corrected them.
- **Single-intent correctness and multi-intent selection are separate measures.** An `in_set`
  answer chose one of the stated intents. It does not capture all of them and is not counted as
  single-intent correctness.
- **Filter-outcome view:**
  - D alone gives 4 correct single-intent filters, 17 correct no-filters, 2 wrong filters (1
    `wrong`, 1 `wrong_commit`), 6 missed single-intent filters and 5 multi-intent queries
    without a filter.
  - D first with J on D's abstentions would give 8 correct single-intent filters, 5 multi-intent
    in-set selections, 17 correct no-filters, 2 missed, and the same 2 wrong filters.

### Conclusion

- **Signal.** This development batch shows a promising category-coverage signal. Where the
  parser abstained, Jev filled 13 of 15 single-category queries correctly with no incorrect
  fills.
- **Not established.** These are counts from one run over 40 AI-authored queries labelled by
  one annotator, with no intervals or repeats. The 13 correct fills are not a reliability
  guarantee. The run establishes no:
  - search-relevance improvement (no retrieval was run);
  - calibrated gate or threshold;
  - production reliability;
  - repeat stability (each query was sent once).
- **Unchanged.** M12 remains in progress. `jq-1`, `fp-1`, every endpoint, `.env` and the
  default search behaviour are unchanged.

## Category-only gate tooling (offline)

Library functions only (no CLI yet). Every value that the draft proposal below suggests (sizes,
threshold grid, acceptance floors) is **not** built in: thresholds and criteria are supplied by
the operator and frozen, and none has a default.

- **Partitions (`partitions.py`, `qd-part-1`).** A calibration or evaluation partition selects
  query ids from sealed batches and is written once to `partitions/<id>.json`.
  - Members are bound by their batch's queries and labels hashes, query id and the SHA-256 of the
    normalized text. No query text or label is copied.
  - `screening="parser_abstains_on_category"` admits only queries where `qu-1` gives no
    category. It is checked again when members are resolved, so a parser change that breaks it
    is refused.
  - `check_disjoint` refuses a calibration and evaluation pair that shares a member or a
    normalized text, including across batches.
  - Resolving members needs exactly the partition's source batches. Each member must be found
    unchanged, and runs must belong to their batch.
  - A partition must be sealed strictly after every source batch seal, checked when it is built
    and again when its members are resolved. This compares recorded timestamps only.
- **Shadow gate (`gate.py`, `cg-shadow-1`).** A pure function over the deterministic parse and a
  recorded `JevOutcome`. The parser's category always wins. Otherwise Jev's unique top category
  is accepted when it is not `none` and its raw probability is at least the frozen threshold.
  - A Jev `none`, a tie, a fallback, a missing outcome or a lower probability applies nothing.
  - It returns a `GateDecision` record only. It reads and builds no `FilterSpec`, nothing in
    `src/` imports it, and `fp-1` still refuses any Jev-provider decision (tested).
  - The raw probability is used as returned. It is not a calibrated gate confidence (FR-DEC-5).
- **Frozen threshold (`qd-threshold-1`).** Written once to `thresholds/<id>.json`. It binds the
  calibration partition's file SHA-256, the model, the question set, the threshold and the
  acceptance criteria. Loading refuses a calibration partition whose bytes changed.
- **Metrics (`category_metrics.py`).** For the deterministic parser, Jev and the shadow gate:
  outcome counts, single-category accuracy and coverage, a confusion matrix, per-category
  precision, recall and F1, macro F1, ambiguous-gold outcomes, and unclear golds counted only.
  `threshold_sweep` scores only thresholds the caller supplies; it selects nothing.
- **Acceptance (`acceptance.py`, `qd-acceptance-1`).** Scores one frozen threshold on one
  evaluation partition with exact one-sided Clopper–Pearson bounds.
  - It refuses overlapping partitions, a run whose model or question set differs from the
    threshold, and any evaluation member dispatched at or before the threshold was frozen.
  - Outcomes: `insufficient_evidence` (missing, incomplete or blocked run; run-issue or unclear
    rate above its maximum; too few accepted fills; no coverage denominator), then `fail`
    (wrong-commit upper bound or coverage outside its criterion), else `pass`.
  - A pass means only a candidate soft gate for a later retrieval experiment on that sampled
    design. It is not a production decision, a hard filter or held-out evidence.
  - `confirm_acceptance` records each confirmation before writing its report
    (`consumption/<content-sha256>.json`, then `acceptance/<threshold-id>.json`, each all or
    nothing). Evaluation data is identified by the SHA-256 of its canonical partition members,
    not by partition id or path, so a renamed or re-created partition is the same data.
  - Refused: evaluation data, any member or any normalized text already consumed by a different
    confirmation (a different threshold file, content or run hashes); a threshold already used
    for a different confirmation; a report without its record; a stored report that differs
    from the recomputed one.
  - An identical repeat writes nothing and returns the stored report. An interruption between
    the record and the report is completed by an identical repeat. An interruption inside a
    write leaves a staging directory that blocks every later write in the root until a person
    inspects it.
  - **Enforcement boundary:** records live in the work root. Reuse is detected only among
    confirmations recorded in the same root; scoring a copy in another root, or after deleting a
    record, is not detected. Beyond that root the one-confirmation rule is procedural.
- **Verification (2026-10-19):** focused M12 tests (`pytest tests/unit/query_decisions`)
  149 passed, exit 0 (synthetic and mocked only). Ruff on the new modules and tests: clean
  after `ruff format` fixed one over-long line; the tests were not re-run after that
  formatting-only change. Not run: the full unit suite and integration tests.

## Offline metrics report (`report`, `qd-report-1`)

`python -m evals.query_decisions report --run-id ID [--out FILE] [--bins N]` builds a report for
one run over one sealed batch (`reporting.py`). It makes no call, contains no timestamps (same
inputs, same bytes) and writes only `--out`, which it never overwrites. `evaluate`
(`qd-diagnostic-2`) is unchanged, and the saved `live-001` files were not regenerated.

- **Run:** attempts, results, valid results, fallbacks by reason, unknown outcomes and
  unattempted queries, each counted separately.
- **Category and intent metrics** for the deterministic parser and for Jev. Unclear golds are
  counted, never scored. `none` is a decision; an abstention is not.
  - Intent keeps single intent (correct, wrong, `predicted_none`, abstain), gold `none`
    (`correct_none`, `wrong_commit`, abstain), multi-intent (`in_set`, `out_of_set`,
    `predicted_none`, abstain) and ambiguous golds apart, with a confusion matrix and
    per-intent precision, recall and F1.
- **Raw category-probability diagnostics.** These describe raw provider probabilities, **not
  calibrated confidence**.
  - Eligible: single-label golds (a category or `none`) with a valid outcome. Ambiguous,
    multi and unclear golds, fallbacks, unknown outcomes and unattempted queries are excluded
    and counted; no score is invented for them.
  - Brier score: the mean over eligible queries of the sum over the five labels of
    (p - y)^2; range 0 to 2.
  - Reliability: confidence is the highest raw probability; queries with a tied top are left
    out of the bins and counted. `--bins` equal-width bins (default 10, a reporting choice).
    ECE is the sum over non-empty bins of n_bin / n times |accuracy - mean probability|.
- **Latency:** provider-measured `latency_ms` of recorded outcomes, for all results and for
  valid results: n, min, max, mean and p50/p90/p95/p99 by the nearest-rank method
  (1-based rank ceil(q * n)). Attempts without an outcome have no timing and are counted. One
  sequential run is not a latency benchmark.
- **Usage and cost:** returned token counts and an estimate from the prices in `run.json`; not
  billing data. Attempts with unknown usage are counted. The per-1,000-attempt figure counts
  them at zero (a lower estimate), and a second figure covers attempts with returned usage
  only. A run without recorded prices has no cost estimate.

## Gate-tooling commands (offline walkthrough)

All four command groups need an explicit `--root` (no default) for `partitions/`,
`thresholds/`, `consumption/` and `acceptance/`. Batches are sealed work directories. Every
value below is a placeholder; none is an approved parameter, and no acceptance criterion has a
default.

```text
# 1. Partitions (write-once; screening re-checked whenever members are resolved)
python -m evals.query_decisions partition create --root R --id cal-1 --role calibration \
    --screening parser_abstains_on_category --source CAL_BATCH dq-001,dq-002
python -m evals.query_decisions partition create --root R --id eval-1 --role evaluation \
    --screening parser_abstains_on_category --source EVAL_BATCH dq-001,dq-002,dq-003
python -m evals.query_decisions partition show --root R --id eval-1
python -m evals.query_decisions partition check --root R --calibration cal-1 --evaluation eval-1

# 2. Sweep supplied thresholds on calibration data only (an evaluation partition is refused)
python -m evals.query_decisions sweep --root R --calibration cal-1 --batch CAL_BATCH RUN \
    --threshold T1 --threshold T2 --out sweep.json

# 3. Freeze one threshold with every acceptance criterion (all required, write-once)
python -m evals.query_decisions freeze --root R --id t-1 --calibration cal-1 --threshold T \
    --model jev-1.13.0 --confidence-level C --wrong-commit-definition strict|lenient \
    --max-wrong-commit-upper-bound U --min-coverage V --min-accepted-fills N \
    --max-unknown-or-fallback-rate X --max-unclear-rate Y [--selection-note TEXT]

# 4. Run the evaluation batch only after the freeze, then score and record it once
python -m evals.query_decisions accept --root R --threshold t-1 --evaluation eval-1 \
    --batch EVAL_BATCH RUN
```

- The sweep selects nothing. The selection rule and its values are the owner's decision.
- `accept` refuses an evaluation member dispatched before the threshold was frozen, and
  applies the consumption safeguards above. An identical repeat writes nothing.
- Refusals print one `error:` line and exit with code 2; a missing required argument is an
  argparse usage error (also code 2).
- These commands make no call and read no key. The runs themselves use `run`, which needs
  separate approval for any live call.


## Category-only annotation (`qd-cat-1`)

For category calibration, a batch can be labelled for the category only:

```text
python -m evals.query_decisions --work-dir DIR label --annotator ann-1 --category-only
python -m evals.query_decisions --work-dir DIR seal
```

- The session asks for the category, an optional note and the save confirmation only. The
  view is unchanged: query id, progress and query text. It shows no parser output, stratum,
  authoring note or proposed answer.
- Labels are `CategoryOnlyLabel` records (`schema_version` `qd-cat-1`,
  `annotation_mode` `category_only`). Intent is the explicit marker
  `semantic_intent: "not_annotated"`. It is never written or read as `none`, `unclear` or a
  label from any other source. The queries file stays `qd-2`.
- One label file holds one mode. Resuming it in the other mode is refused before any prompt,
  and a file that mixes modes is refused when read.
- `seal` writes a `CategoryOnlySeal` (`qd-cat-1`, `annotation_mode: category_only`).
  `load_sealed` refuses a seal whose mode does not match its labels.
- Category evaluation is unchanged. In `evaluate`, intent cells have gold kind `not_annotated`
  and outcome `excluded_not_annotated`, and they are left out of the fills. In `report`,
  `intent_metrics` counts them in `not_annotated_excluded` and scores nothing. Both reports gain
  `annotation_mode: category_only`. Partitions and the gate use the category gold and work
  unchanged; such members have no intent gold.
- `qd-2` labels and seals are unchanged byte for byte. For live-001, `evaluate` and `report`
  give the same SHA-256 under the code before and after this change (`d463d614…` and
  `39cc7c35…`; the latter is the saved `metrics-report-v1.json`). The saved `report.json` is
  `qd-diagnostic-1`, so it predates the current evaluator and was not expected to match.

## M12 experiment report (2026-10-19)

Consolidated from existing evidence only. Every M12 query is AI-authored: `notion-ai` for the
40-query diagnostic batch and `claude-code` for the 80-query cal1 batch. Category labels are by
one human annotator (`ann-1`) with AI-assisted rule clarification, at least one AI-assisted
label confirmation and an AI-assisted pre-seal review of cal1. All of it is development data,
never held-out evidence.

### Implemented

- **Diagnostic tooling** (`f2d33f1`, `9f605a9`, `03d683c`):
  - sealed blind labelling;
  - the budgeted, journalled live runner (retries 0, one call in flight, spend tripwire);
  - the `qd-diagnostic-2` evaluator.
- **Offline gate tooling** (`2712037`, `93dbc32`):
  - partitions with chronology checks;
  - the pure `cg-shadow-1` category gate, threshold sweep and write-once frozen threshold;
  - Clopper–Pearson acceptance scoring with a consumption record.
- **Reproducible report** `qd-report-1` (`931414d`): category and intent metrics, Brier and ECE
  of raw probabilities, nearest-rank latency, and cost estimated from returned usage.
- **Category-only annotation** `qd-cat-1` (`ad2f735`).
- **Experimental V4 and comparison tooling** (`2fa1d56`): `/search/routed` and
  `evals.runners.compare_v3_v4` (see `docs/v4-jev-routing-m12.md`).

### Experiments performed and results

**1. Diagnostic run `live-001`:** 40 queries, 40/40 valid outcomes; report
`metrics-report-v1.json`.
- Category on 30 single-category golds:
  - deterministic: 15 correct, 15 abstain, 0 wrong; accuracy 0.500;
  - Jev: 28 correct, 2 predicted `none`, 0 wrong; accuracy 0.933, macro F1 0.972.
- Intent on 11 single-intent golds: deterministic accuracy 0.364, Jev 0.636.
- Raw category probabilities (33 eligible): Brier 0.139, ECE 0.097.
- Jev latency, nearest rank: p50 325 ms, p95 419 ms.
- Estimated $0.001084 in total, or $0.027 per 1,000 attempts.

**2. Calibration run `cal1-live-001`:** 80 queries, 80/80 valid outcomes; report
`metrics-report-v1.json` (`b87ab416bfa104f7752f8e815e51cb6420e0907f321f5f08ac73a3ab5c516451`).
- These queries were screened so the parser gives no category, so the deterministic parser
  abstained on all 49 scored queries.
- Jev on 32 single-category golds: 27 correct, 3 wrong, 2 predicted `none`; accuracy 0.844,
  macro F1 0.806.
- Jev also made 5 wrong commits on gold `none` and 1 commit inside an ambiguous gold's
  candidates. 31 golds were `unclear` and excluded.
- Intent was not annotated: all 80 queries are counted as not annotated, and no intent metric
  is computed.
- Raw category probabilities (48 eligible): Brier 0.339, ECE 0.184. 40 of 48 fall in the
  (0.9, 1.0] bin, with accuracy 0.850 there.
- Jev latency: p50 326 ms, p95 382 ms. With n = 80 the nearest-rank p99 is the maximum, 600 ms.
- Estimated $0.002156 in total, or $0.027 per 1,000 attempts.

**3. Calibration sweep:** partition `cal-exp1` (69 members), protocol `cal-protocol-1`.
- **No candidate gate.** At every pre-registered threshold from 0.50 to 0.99, the one-sided 95%
  Clopper–Pearson upper bound on strict wrong fills was 0.245 to 0.299, against the required
  ≤ 0.10.
- Even at 0.99, 5 of 37 accepted fills were wrong.
- As pre-registered: no threshold was frozen, and no evaluation partition was built or run.

**4. V3-versus-V4 comparisons** (details and hashes in `docs/v4-jev-routing-m12.md`):
- **Offline, recorded outcomes:**
  - 120 M12 queries without relevance judgements: 58 routed, 21 rankings changed.
  - Category agreement in the top 10 was higher for 12 queries and equal for 50; this is not
    relevance.
  - 20 judged pilot/dev queries: 17 equal by construction, so NDCG@10 ties.
- **Live, three approved Jev calls** on gq-p17, gq-p18 and gq-p20:
  - Jev routed one query (`headphones`) and returned `none` twice;
  - no ranking changed: NDCG@10 tied twice and was undefined once;
  - estimated $0.00008.

### Negative and inconclusive results

These comparisons and measurements were performed. Their outcome was negative or inconclusive:

- **Calibration: no candidate gate qualified** (the sweep above).
- **V3 versus V4: no relevance improvement was demonstrated.**
  - On the judged queries, every comparable result was a tie or undefined.
  - The 21 rank changes on unjudged queries are not relevance evidence.
  - The Milestone 12 acceptance text asks for the comparison and the measurements, not for an
    improvement. A measured benefit is the condition for enabling Jev in the default/V4
    configuration (FR-DEC-4 in `docs/spec.md`; `docs/architecture.md`), and that condition is
    not met.
- **Raw probabilities are not reliable confidence.** Two separate saved figures happen to share
  n = 40:
  - **Sweep, partition `cal-exp1`** (69 members: 49 cal1 and 20 diagnostic). At threshold 0.90,
    an accepted fill is a unique top category other than `none` with raw probability ≥ 0.90.
    There were 40 fills, of which 5 were wrong (12.5%) under the strict definition: another
    category, gold `none`, or any commit on an ambiguous gold. The lenient count is also 5.
  - **Reliability bin, cal1 report only.** The 48 eligible queries have a single-label gold
    (a category or `none`). The bin holds the predictions whose unique top label, `none`
    included, has raw probability > 0.9. There were 40 such predictions, of which 6 were
    incorrect (accuracy 0.850): 5 category commits and 1 `none` prediction. These are not all
    fills.

### Planned confidence-gated V4: not realized

The plan's `V4 = V3 + confidence-gated Jev decisions` was not built, because calibration found no
qualifying gate. The experimental V4 that exists is a different, ungated arm (next section).

### Not performed

- **Independent confirmation was not performed.** No frozen threshold was evaluated on a
  separate, held-out evaluation partition; no threshold qualified for it.
- The optional general-LLM comparison (optional in the plan).
- End-to-end V4 latency P50/P95/P99 under load, and repeat-stability runs.
- A judged retrieval comparison on queries where V4 changes the ranking. No existing judgements
  cover those queries.
- Model-derived hard filters. Correctly not implemented, because no threshold is validated.

### Planned V4 versus implemented V4

The plan defines `V4 = V3 + confidence-gated Jev decisions`. The implemented V4 is different. It
is an **ungated, experimental soft-routing arm**:
- Jev's category adds one candidate branch, merged by RRF and reranked.
- Nothing is gated, and no confidence band is implemented.
- It is off by default (`SEARCH_V4_JEV_ENABLED`).

The planned confidence-gated V4 was not built, because calibration found no qualifying gate.

### Recommendation

- Retain V3 as the existing baseline.
- Keep V4 opt-in and experimental.
- Do not enable a confidence gate.

### Owner decision (2026-10-19)

**Option A approved: M12 is closed as development-scoped, partially met.**
- V3 remains the unchanged baseline.
- V4 stays opt-in and experimental (`SEARCH_V4_JEV_ENABLED` defaults to `false`).
- The confidence gate stays off.
- The original requirements are preserved as written. M13 has not started.

Against the original Milestone 12 text:

| Requirement | Status |
|---|---|
| Create human-reviewed decision data | **Partly met.** 120 queries, labels by one human annotator, sealed before the calls. The queries are AI-authored, annotation was AI-assisted, there was no second reviewer, and it is development data only. |
| Compare deterministic | **Met** (development data). |
| Compare an optional LLM | **Optional; not done.** |
| Compare Jev | **Met** (development data, two live runs). |
| Compare confidence-gated Jev | **Unmet.** No gate qualified, so there was nothing to compare. |
| Measure classification | **Met** (development data). Category on both runs; intent on `live-001` only. |
| Measure calibration | **Met as a measurement.** Brier score, ECE and the sweep. **Negative result:** no candidate gate. |
| Measure coverage | **Met** (sweep coverage and abstention counts). |
| Measure latency | **Partly met.** Per-call Jev latency on both runs, and three single V4 request timings. There is no end-to-end V4 P50/P95/P99 and no measurement under load. |
| Measure cost | **Met as an estimate** from returned usage at a dated published price; not billing data. |
| Measure end-to-end retrieval metrics | **Performed; no improvement demonstrated.** On the 20 judged pilot/dev queries, 17 are identical by construction and the 3 live ones tied or were undefined. |
| "This is V4" (V3 + confidence-gated Jev) | **Not realized as planned.** An ungated, experimental soft-routing V4 exists instead. |

Deferred, not performed:
- independent held-out confirmation with a frozen threshold;
- enabling Jev in the default/V4 configuration (FR-DEC-4 requires a measured benefit, which was
  not shown);
- model-derived hard filters;
- V4 latency under load and repeat stability;
- a judged comparison on queries where V4 changes the ranking.
