# Milestone 12 — diagnostic batch tooling (offline only)

## Status

**M12 is in progress.** Approved scope (owner, 2026-10-18): **minimal offline tooling for a
40-query diagnostic batch** (`m12-diag-dev`). This is not the M12 experiment.
- **Batch (2026-10-18/19).** 40 queries, `dq-001`–`dq-040`, all written by Notion AI
  (`source: ai_authored`, `author_tool: notion-ai`; no underlying model is recorded).
  - Labelled by one human annotator, `ann-1`, query text only. The annotator had AI-assisted
    clarification of the labelling rules and answer syntax, so this is not fully unaided
    annotation.
  - Labels were sealed before any live call and have not changed since.
  - The files are git-ignored under `data/processed/m12_diagnostic/`.
- **Runs.** One mocked dry run (`dry-run-001`, synthetic, not Jev output) and one
  owner-approved live run (`live-001`, 2026-10-19). See [live-001](#live-001-2026-10-19). No
  other live diagnostic call is approved.
- **Unchanged:** `jq-1`, `fp-1`, every endpoint, `.env`, the reranker settings and the default
  search behaviour.
- **Not built:**
  - hard filters or `fp-2`;
  - gates, thresholds or fitted calibration;
  - main-set authoring;
  - a bootstrap or McNemar framework;
  - counterfactual retrieval.

The diagnostic batch is **development evidence only, never held-out evidence**. Any change to the
question set, the rubric or the gate design is allowed only at this stage. The later evaluation
partition is authored and sealed separately.

Code: `evals/query_decisions/` (`contracts`, `labeling`, `runner`, `evaluate`, `cli`).
Tests: `tests/unit/query_decisions/` (synthetic fixtures only, fully mocked, no network).

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
| Per-attempt latency (`latency_ms` measured by the provider client, n = 40) | min 296 ms, median 325 ms, mean 365 ms, p95 442 ms, max 1,190 ms (the first attempt) |
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
