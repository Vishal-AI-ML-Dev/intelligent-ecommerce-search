# Milestone 12 — diagnostic batch tooling (offline only)

## Status

Approved scope (owner, 2026-10-18): **minimal offline tooling for a 40-query diagnostic batch**
(`m12-diag-dev`). This is not the M12 experiment.
- **Nothing has run.** No query file exists, no label exists, and no live Jev call was made.
- **Query authorship.** The schema is `qd-2`, which supports AI-authored development queries.
  - 10 draft queries were written by Notion AI (`author_tool: notion-ai`; no underlying model
    is recorded).
  - They exist only in the planning conversation. They have not been imported, and no file
    holds them.
  - Who writes the remaining queries, and who labels all of them, is still open.
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
5. A live run: not approved (see below).
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
queries, and a mismatch is refused.

**4. Dry run.** The real provider runs against an in-memory transport that returns one fixed,
synthetic, valid `none`/`none` answer with zero tokens. No network, no key, and the output is
not Jev output.

**5. Live run: requires separate owner approval and has never been run.** The command would be:

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
  - **It does not authorize paid calls.** Every live run still needs separate owner approval,
    and no live diagnostic run is approved.
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
- **No real data and no live calls yet.** There are no real diagnostic queries, labels, seal
  or runs, and no live diagnostic call has been made.

## Diagnostic report (`evaluate.py`)

- **Deterministic decisions** are recomputed **at evaluation time** from the sealed text. They
  are not recorded during the run.
  - The report records the parser and lexicon version labels (`deterministic.parser_versions`,
    `deterministic.lexicon_versions`).
  - These are declared constants, not proof that the parser source is identical to an earlier
    evaluation.
- **Jev predictions** are the unique top label, which may be `none`. Otherwise Jev abstains, and
  the reason is recorded as `tie`, `fallback`, `unknown` or `not_attempted`.
- **Scoring** is defined in the `evaluate` module docstring:
  - `correct`, `wrong` and `predicted_none` against a single label;
  - `correct_none` and `wrong_commit` against gold `none`;
  - `commit_in_candidates`, `commit_outside_candidates` and `predicted_none` against an
    ambiguous gold;
  - `in_set`, `out_of_set` and `predicted_none` against a multi-intent gold;
  - `excluded_unclear` for unclear golds.
- **Reported for each question:**
  - gold kinds;
  - deterministic outcome counts, which show missing and wrong deterministic decisions;
  - Jev outcomes and abstention reasons;
  - Jev validity and fallbacks, and the run's token totals;
  - D/J disagreements, with the outcome pairs and query ids;
  - Jev's outcomes where the deterministic parser abstains (correct and incorrect fills).
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
