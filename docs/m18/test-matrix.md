# M18 — Acceptance and test matrix

Status: **FROZEN — approved by the owner on 2026-10-24 with the M18-01 design.** The planned
tests below are unchanged. M18-02 results (2026-10-24) are recorded in the next
section.

## M18-02 results (actual, 2026-10-24)

| Run | Kind | Result |
|---|---|---|
| `tests/unit/returns/` (canonical forms, contracts, ORM allowlists and enum CHECKs) | hermetic unit | 212 passed |
| `tests/unit/catalog/test_catalog_models_metadata.py` + `tests/unit/returns/` | hermetic unit | 232 passed |
| `tests/integration/test_returns_schema.py` + `test_returns_grants.py` | database integration (scratch DBs, real per-test LOGIN roles) | 175 passed |
| Full integration, `not real_model` (run before the metadata-test edit and the format-only change) | database integration | 939 passed, 18 deselected |
| **Fresh full hermetic unit suite** | hermetic unit | **FAILED: 1 failed, 5391 passed, 4 skipped.** `test_unit_isolation.py::test_whole_unit_suite_passes_without_a_database_password` hit `subprocess.TimeoutExpired` on its fixed 1,200 s nested run. Not passed; see `handoff.md`. |
| `ruff check .` / `ruff format --check .` | lint | exit 0 / exit 0 |
| AC-14: protected-path diff; M17 SHA-256 listing | recorded command | exit 0 / exit 0 |

M18-02 coverage by AC:
- **AC-01:** database side covered.
- **AC-02:** FK/UNIQUE/CHECK direct-SQL backstops, migration round trip, and T2/T4/T6/T8
  database backstops covered. Snapshot-hash equality and T1/T3/T5/T7 are M18-03.
- **AC-03:** covered.
- **AC-04:** storage and join covered. `dataset_mismatch` is M18-03; API provenance is M18-04.
- **AC-05:** storage and Pydantic covered. Response schemas are M18-04.
- **AC-13 (3)/(4):** covered.

"Kind" uses the reporting labels required by MASTER_PLAN §28:
- hermetic unit;
- database integration (scratch pgvector database, real per-test roles where stated);
- fixture browser (mocked API);
- live browser (local stack);
- CI;
- staging.

Fixture-backed and live results are reported separately. Section references (§) are to
`contracts.md`.

| AC | Requirement | Tests (planned) | Kind | Package |
|---|---|---|---|---|
| AC-01 | Contracts versioned; migration enum literals equal Pydantic enums; canonical forms (§4.6) | contract model tests; enum-sync test; canonicalization tests (offsets, 6-digit fractions, >6 rejected, decimal forms, JSON number rejected for money, `1.0` rejected for integers, explicit nulls) | unit + integration | M18-02 |
| AC-02 | Eight entity groups stored; composite FKs per dataset version; FK to `products`; snapshot equality; one shipment per order (UNIQUE); evidence key length 152 | per-table FK/UNIQUE/CHECK tests incl. direct SQL bypassing the service; `test_head_adds_only_…` table set updated; upgrade → downgrade `0006` → upgrade leaves the 0006 schema identical (catalog introspection diff) | integration | M18-02 |
| AC-03 | Append-only; raw bytes only in `return_import_file_contents`; metadata/contents checksum bound by composite FK; grants per §2.1 | UPDATE/DELETE/TRUNCATE blocked on every M18 table; **real LOGIN roles** created per test with the grants script: `search_app` denied on contents, allowed on metadata, no INSERT anywhere; importer denied SELECT on contents and on non-listed catalog columns (e.g. `products.price`), denied UPDATE/DELETE and catalog writes; **preservation:** `search_app`'s privileges on every pre-M18 table (incl. M13/M14) are identical before and after the script; **raw-file revoke:** after a legacy blanket `GRANT SELECT ON ALL TABLES` is applied, the script removes `search_app` access to raw contents; script idempotent; no M18 default privileges | integration | M18-02 |
| AC-04 | Eight §9 provenance fields available on every record; `is_synthetic` true; immutable dataset descriptor | model tests; `dataset_mismatch` test; API record-level provenance assertions | unit + integration + API | M18-02/04 |
| AC-05 | Statement, observation, hypothesis and decision kept distinct | **exact column allowlist** per M18 table (DB introspection) and exact field allowlist per Pydantic model equal the sets declared in contracts §3; evidence-kind tests | unit + integration | M18-02 |
| AC-06 | Row-level errors; deterministic order; truncation semantics (§4.8); structural versus conflicting (§3.1) | one test per error code; CSV quoting/CRLF/BOM; duplicate JSON keys; ordering stable across runs; 199/200/201/1,000/1,001-error fixtures check `error_count`, `error_count_is_lower_bound`, `errors_truncated`, `phases_completed`; every §3.1 structural rule rejected; every §3.1 conflict example **accepted with zero errors** | unit | M18-03 |
| AC-07 | Atomic bundle and CSV set | fault injection at each phase and at the final insert; missing/unexpected part, checksum and row-count mismatch; all M18 table counts unchanged after each | integration | M18-03 |
| AC-08 | Idempotency and incremental behaviour (§4.5); format equivalence (§4.6–4.7) | every §4.5 row; JSON and CSV forms → identical rows, `record_sha256` and `records_sha256`; second form → `no_new_records` with no batch row; partial-then-rest import reaches the release `records_sha256`; `verify` reports missing/extra counts | integration | M18-03 |
| AC-09 | Limits (§4.4); no temp-file spill in validate mode | limit and limit+1 for each row of §4.4 (unit); oversized and chunked bodies via API → 413 before parsing; temp directory unchanged during validate; 429 on concurrent validate | unit + API | M18-03/04 |
| AC-10 | Privacy surfaces (§5.1), redaction G1–G6, fixture safety (§5.3) | per-pattern and property tests; one test per §5.1 row incl. hostile file names, header names, enum values, query parameters and a forced Pydantic error, asserting no raw value in response or captured logs; fixture scan test | unit + integration | M18-03/04 |
| AC-11 | Access control, rotation behaviour | route × token-case matrix; settings validation (length, distinct tokens); old token rejected after settings change and app restart (TestClient app rebuild); unset → 503; buyer routes unaffected; log capture shows no token | API (unit) | M18-04 |
| AC-12 | Filters, total ordering, cursors, qualified detail identity | same `return_id` in two dataset versions → two distinct detail paths and stable paging; ties on `requested_at` paged without loss or duplication at every page size 1..5; `malformed_cursor`, `cursor_mismatch`; limit bounds; detail content incl. snapshot, evidence presence, provenance; 404s; OpenAPI schema snapshot | integration (API) | M18-04 |
| AC-13 | No route or code path refunds, orders, bans, sanctions, or infers fraud/responsibility | (1) **route inventory:** app routes equal the pre-M18 set plus exactly the 7 §7 routes; (2) **write path:** every `/returns` route except `POST /returns/import` runs in a `READ ONLY` transaction, and a test proves a write attempt there fails; (3) **privileges:** `search_app` has no INSERT/UPDATE/DELETE anywhere; importer has no write privilege on catalog, search, listing or decision tables, and no read privilege on them except the column-level catalog SELECTs in contracts §2; (4) **schema:** AC-05 allowlists contain no decision/outcome field; (5) **service surface:** the `returns` package's public service functions equal the declared set | unit + integration | M18-02/03/04 |
| AC-14 | Generator deterministic; release checksums; scenario manifest never imported; historical artifacts unchanged | `--check` test; importer refuses `scenarios.json` as a part; at each package end `git diff --exit-code bf67d11 -- <protected paths>` (below) passes; untracked M17 outputs checked against a SHA-256 listing taken before M18-02 and kept outside the repo | unit + recorded command | M18-03 (+ every package) |
| AC-15 | Frontend states and content | Vitest: gate, unauthorized, disabled, loading, empty, error with request ID, report truncation text, list, detail, conflict side-by-side without a flag, markup/injection rendered as text, token absent from `localStorage`/`sessionStorage`; fixtures validated against the Pydantic contract | unit (Vitest) | M18-05 |
| AC-16 | Browser path import → list → detail | Playwright `fixture` (mocked). Playwright `live` on a local stack with a scratch database, **`ASSISTANT_GENERATION_PROVIDER=none` and no `GEMINI_API_KEY`**: commit mode locally, then validate-only mode with commit disabled | fixture browser; live browser | M18-06 |
| AC-17 | Existing buyer search and assistant unchanged | full hermetic unit suite; integration (`not real_model`); existing Vitest and both Playwright projects; API live suite run **as the real `search_app` role** under the new grants; assistant exercised only with generation off or a fake provider | unit, integration, browser | M18-06 |
| AC-18 | Release readiness and old-image compatibility | CI green on the release commit (no live provider); the currently deployed image tag run against a database at `0007` with the grants script applied and M18 data loaded, **with generation disabled and on an internal Docker network with no egress**: `/health`, ranked search, the assistant's generation-off path, and its own live Playwright path pass; migration duration recorded; local restore rehearsal (dump → scratch database → grants → row counts equal) | CI + local recorded runs | M18-07 |
| AC-19 | Staging deployment smoke | pre-check: staging Alembic revision read only; backup file present; migration, grants and CLI import applied; `/health`, search and assistant OK; `/returns` 401 without token, 200 with viewer token, commit 403; list and detail counts equal the CLI import report; `returns verify` equals release `records_sha256`; **app-container restart check**: the same data is served after `docker compose restart app` (database container and volume untouched; this is not a database-durability or restore test) | staging | M18-08 |

## Protected historical paths (AC-14)

Compared with `git diff --exit-code bf67d11 -- …` at the end of each package:

- `data/seed/catalog_seed_v1.jsonl`, `data/seed/catalog_seed_v1.provenance.json`, `data/labels/`
- `evals/goldens/`, `evals/weaksets/`, `evals/results/`, `evals/assistant/drafts/`,
  `evals/assistant/fixtures/`
- `docs/demo/`
- `docs/golden-dataset-m9.md`, `docs/weak-label-development-m9.md`,
  `docs/rubric-1-reviewer-copy.md`, `docs/rubric-1-reviewer-transcription.md`,
  `docs/m10-provisional-evaluation.md`, `docs/m10-human-pilot-dev-evaluation.md`,
  `docs/m17-evidence-report.md`, `docs/m17-answer-rubric.md`

**Excluded, preserved by not touching them:**
- `docs/m12-diagnostic-tooling.md` (uncommitted owner edit present before M18).
- Untracked `evals/results/m17/unsupported-claims/` and `docs/m17-handoff.md`, checked by the
  SHA-256 listing described in AC-14.

## Concurrency tests (R2; part of AC-02/AC-07/AC-08)

Two real sessions per test. A test hook pauses a commit after it takes the lock.

| # | Scenario | Expected |
|---|---|---|
| T1 | Same bundle committed twice concurrently | one `imported`, one `already_imported`; one batch row |
| T2 | Two bundles each returning quantity 1 of a quantity-1 item | one `imported`, one `quantity_exceeded`; trigger blocks a direct-SQL bypass |
| T3 | Same natural key, different content, concurrently | one `imported`, one `conflict_existing` |
| T4 | Two bundles each adding a shipment for one order | one `imported`, one `duplicate_shipment` (UNIQUE as backstop) |
| T5 | Bundle B references an order only in A; A paused under the lock | B waits; after A commits, B `imported`; after A rolls back, B `unknown_reference` |
| T6 | First-ever imports of a new dataset version concurrently | one dataset row; second compares the descriptor |
| T7 | Validate during a paused commit | validate never sees partial rows; never blocks on the lock |
| T8 | Cancelled return plus new return on the same item | allowed when the non-cancelled sum ≤ quantity |

## Not tested by design

- Real retailer or personal data, and redaction recall on real text.
- M19–M22 behaviour.
- Load beyond the single-flight import rule.
- Database-container restart or volume durability on staging (not performed without separate
  approval).

## Execution notes

- No routine test calls a live provider.
- Do not edit the repository while the full unit suite runs.
- No security restriction is bypassed for browser runs.
