# M18 — Acceptance and test matrix

Status: **FROZEN — approved by the owner on 2026-10-24 with the M18-01 design.** The planned
tests below are unchanged. Actual results are recorded per package (M18-02, M18-03A,
M18-03B, M18-03C) in the next sections.

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
| CI run 40103962367 on `797bc75` (backend, integration, frontend, e2e-fixture) | CI | all success. Hermetic child: 5392 passed, 4 skipped, 957 deselected. Integration: 939 passed, 18 deselected. The Windows outer isolation wrapper is not covered by CI. |

M18-02 coverage by AC:
- **AC-01:** database side covered.
- **AC-02:** FK/UNIQUE/CHECK direct-SQL backstops, migration round trip, and T2/T4/T6/T8
  database backstops covered. Snapshot-hash equality and T1/T3/T5/T7 are M18-03.
- **AC-03:** covered.
- **AC-04:** storage and join covered. `dataset_mismatch` is M18-03; API provenance is M18-04.
- **AC-05:** storage and Pydantic covered. Response schemas are M18-04.
- **AC-13 (3)/(4):** covered.

## M18-03A results (actual, 2026-10-25; committed `797c6e4`, pushed, CI run 40215398031 success)

M18-03A covers validation and privacy only (Phase A steps 1–4, in memory). Its tests are
hermetic unit tests in `tests/unit/returns/`.

| Run | Kind | Result |
|---|---|---|
| `uv run pytest tests/unit/returns -q` (M18-02's 212 + 178 new; after the commit-checkpoint review fixes) | hermetic unit | **390 passed** |
| `uv run ruff check .` / `uv run ruff format --check .` | lint | all checks passed / 495 files formatted |
| AC-14 protected-path diff against `bf67d11` | recorded command | exit 0 |
| AC-14 M17 SHA-256 listing | recorded command | exit 0 at the M18-03A commit checkpoint: the original M18-02 listing was recovered from the M18-02 session scratchpad (`ac14-m17-untracked.sha256`); its own SHA-256 equals the recorded `cf74568d…41adf7`; `sha256sum -c` reported all 10 files OK; and the directory holds no extra files |
| Full unit suite, nested isolation test, integration suite | — | **not run** (not authorized in this package). The M18-02 local isolation timeout stays FAILED. |

New tests:
- `test_returns_redaction.py` (18): each `redaction-1` kind and token, order and counts, text
  outside the patterns left unchanged, G1–G3 on 3,000 seeded random strings, the 2× storage
  bound, and preview truncation with re-redaction.
- `test_returns_bundle_parsing.py` (55):
  - envelope: unexpected, repeated and hostile part names; `scenarios.json` refused; missing
    anchor;
  - §4.4 limits at the limit and limit + 1: files, file / manifest / bundle bytes, entity and
    bundle rows, CSV columns, CSV field bytes;
  - JSON failures: duplicate keys, BOM, invalid UTF-8, NaN, depth 4/5, U+0000 and lone
    surrogates;
  - CSV: BOM, LF/CRLF equivalence, quoting, syntax errors with line, ragged rows, headers;
  - manifest entries and set consistency; `bundle_sha256`.
- `test_returns_validation.py` (70):
  - JSON/CSV equivalence of records, `record_sha256` and `records_sha256`;
  - field rules, with each code; the status-field mapping;
  - `looks_personal` on IDs, `carrier_ref` and dataset text;
  - every in-bundle §3.1 structural rule;
  - deferred references not reported;
  - all seven §3.1 conflict examples accepted with zero errors;
  - 199/200/201/1,000/1,001-error counts, lower bound and truncation;
  - deterministic ordering.
- `test_returns_error_privacy.py` (35): hostile values on 23 surfaces (file names, keys,
  headers, manifest, IDs, enums, timestamps, money, integers, actor refs, free text, dataset
  text, version, syntax errors). None appear in the report or in captured logs; validation
  emits no log records. Also covers the preview rules, the code-template set, and safe
  key/pointer echo, and pins `KNOWN_KEYS` to the literal contract name sets.

**AC status after M18-03A.** Partial only; no AC is closed by M18-03A alone.
- **AC-06:** the unit side is covered for Phase A steps 1–4. The phase-5/Phase B codes are
  M18-03B.
- **AC-09:** the domain limits are covered. API 413/429 and the temp-file check are M18-04.
- **AC-10:** domain redaction and error privacy are covered. The fixture scan (§5.3) needs the
  M18-03C dataset; API surfaces are M18-04.
- **Not started:** AC-07 and AC-08 (except format equivalence of records), which are M18-03B.
- **Concurrency T1–T8:** the M18-03B service-level tests are pending. The M18-02 database
  backstop evidence for T2/T4/T6/T8 stays as recorded above.

## M18-03B results (actual, 2026-10-26; in the scoped M18-03B commit)

M18-03B adds the import service (Phase A step 5, Phase B, commit), the `ImportReport` and the
`validate`/`import` CLI.

| Run | Kind | Result |
|---|---|---|
| `uv run pytest tests/unit/returns -q` (390 M18-02/03A + 48 service + 17 CLI) | hermetic unit | **455 passed** |
| `tests/unit/test_settings.py`, `test_compose_config.py`, `test_engine.py`, `catalog/test_catalog_models_metadata.py` | hermetic unit | **223 passed** |
| `tests/integration/test_returns_import.py` (one scratch database per module; real per-module `m18t_*` LOGIN roles with `returns-grants-1`; every import runs as the importer role) | database integration | **78 passed** (the first run had 1 test bug, fixed; see `handoff.md`) |
| `tests/integration/test_returns_schema.py` + `test_returns_grants.py` | database integration | **175 passed** |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 502 files formatted |
| AC-14 protected-path diff; M17 SHA-256 listing | recorded command | exit 0 / exit 0 (10 OK) |
| Review checkpoint: `tests/unit/returns` (adds 3 AC-13 (5) surface tests) | hermetic unit | **458 passed** |
| Review checkpoint: `tests/integration/test_returns_import.py` (adds 2 credential-diagnostics regression tests) | database integration | **80 passed** |
| Review checkpoint: `ruff check .` / `ruff format --check .` | lint | all checks passed / 502 files formatted |
| Full unit suite, nested isolation test, full integration suite, CI | — | **not run**. The M18-02 local isolation timeout stays **FAILED**; CI success on `797bc75`/`797c6e4` does not cover the Windows outer wrapper. |

**What the new tests cover:**

- **AC-07 (atomicity):**
  - Fault injection at every commit checkpoint (`phase_a`, `catalog`, `locked`, `checked`,
    `dataset`, `batch`, `files`, each `rows:<entity>`, `before_commit`), for JSON and CSV.
    All 15 M18 table counts are unchanged afterwards, and the same upload then imports.
  - Missing, unexpected, checksum and row-count CSV set errors write nothing.
  - A check missed by the service (patched out) is stopped by the deferred quantity trigger,
    as a sanitized `ImportStorageError`, and nothing is written.
- **AC-08 (idempotency and formats):**
  - `imported`, then the same bytes give `already_imported` with the same `batch_id`, in both
    modes.
  - The other format gives `no_new_records`, with no batch and no files.
  - JSON and CSV give identical stored rows and an identical `records_sha256`, equal to the
    release-side computation.
  - A partial import then the rest reaches the full `records_sha256`.
  - Incremental references to stored records work.
  - `conflict_existing` and `dataset_mismatch` write nothing, in both modes.
  - Stored text is redacted with counts, and raw bytes are kept only in contents.
  - **Not covered:** `returns verify` (assigned to M18-03C), so its missing and extra counts are
    still open.
- **AC-06 (phase 5 and Phase B codes):**
  - `unknown_product` and `snapshot_mismatch` (hash and catalog version), `unknown_reference`
    (every reference field and evidence target), and `conflict_existing`.
  - `dataset_mismatch`, stored `duplicate_shipment`, stored `quantity_exceeded`, an inspection
    on a stored return that was not received, and time order against stored parents.
  - The order + product complaint rule, and evidence relations through shipments, delivery
    events and order-only complaints.
  - Conflicting evidence against stored records is accepted.
  - `phases_completed` and lower-bound semantics.
- **Permissions and transaction modes:**
  - Commit as the owner (superuser) or the API role raises `ImportRoleError` and writes
    nothing.
  - Validate works as the API role and as the importer.
  - Validate is `REPEATABLE READ`, read-only, with no advisory lock.
  - Commit is `READ COMMITTED`, under the dataset-version lock, as the importer role.
- **CLI:**
  - Unit tests: input mapping, the read cap, exit codes, the required label, and messages
    without paths, values or credentials.
  - Integration: `validate`/`import` as the importer (`imported`, `already_imported`,
    `no_new_records`, with the label stored).
  - No fallback: admin and API-role credentials are refused before any write, and a missing
    label is refused before connecting.

**Concurrency T1–T8 (service level, two real sessions):** all pass. The test hook pauses the
first commit under the lock, and the test confirms the second session waits on the advisory
lock before releasing it.
- T1: `imported` + `already_imported`, with one batch.
- T2: `quantity_exceeded`.
- T3: `conflict_existing`.
- T4: `duplicate_shipment`.
- T5: commit gives `imported`; rollback gives `unknown_reference`.
- T6: one dataset row; a different descriptor gives `dataset_mismatch`.
- T7: validate during a paused commit (rows inserted, not committed) sees nothing and returns
  promptly.
- T8: a cancelled return plus a new one are both imported.

**AC status after M18-03B.** Partial; no AC is closed.
- AC-06: the domain side is covered.
- AC-07: covered at the service level.
- AC-08: partial; `verify` is assigned to M18-03C.
- AC-13 (5): the service surface is pinned (unit). AC-13 as a whole still needs M18-04.
- AC-09/10/11/12: API parts are M18-04.

## M18-03C results (actual, 2026-10-26; in the scoped M18-03C commit after the review checkpoint)

M18-03C adds:
- the deterministic generator;
- the generated release files `data/seed/returns_v1/` (5,313 records);
- the §5.3 fixture scan;
- `returns verify`.

Details are in `handoff.md`.

| Run | Kind | Result |
|---|---|---|
| `uv run python scripts/generate_returns_dataset.py --check` | recorded command | exit 0: 15 files regenerate byte-for-byte |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` (458 earlier + 28 dataset + 16 privacy + 8 verify) | hermetic unit | **510 passed** |
| `tests/unit/catalog/test_catalog_seed_loader_report.py` + `tests/unit/search/test_benchmark_provenance.py` | hermetic unit | **32 passed** |
| `tests/integration/test_returns_dataset.py` (scratch database with the real 240-product catalog ingested; real per-module `m18t_*` roles with `returns-grants-1`; imports as the importer, verify as the API role) | database integration | **10 passed** |
| `tests/integration/test_returns_import.py` (rerun because `cli.py` changed) | database integration | **80 passed** |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 509 files formatted |
| AC-14 protected-path diff; M17 SHA-256 listing | recorded command | exit 0 / exit 0 (10 OK) |
| Review checkpoint: `tests/unit/returns` (adds the exemption-boundary test) | hermetic unit | **511 passed** |
| Review checkpoint: `tests/integration/test_returns_dataset.py` (renames only) | database integration | **10 passed** |
| Review checkpoint: `--check`; Ruff; AC-14 protected diff; M17 listing | recorded command / lint | exit 0; all checks passed and 509 files formatted; exit 0; 10 OK |
| Full unit suite, nested isolation test, full integration suite | — | **not run**. The M18-02 local isolation timeout stays **FAILED**. CI on `4d495c8` is green as confirmed by the owner (not independently verified) and does not cover these changes. |
| **CI on `4821bbd`** (M18-03C commit) | CI | **FAILED during collection**, as reported by the owner (not observed here): "import file mismatch". The integration and unit `test_returns_dataset.py` files share a module basename, and the hermetic job collects both. |
| Corrective: CI-equivalent hermetic `--collect-only` (same unset prefixes, `ECOMMERCE_SEARCH_HERMETIC_CHILD=1`, empty `POSTGRES_PASSWORD`, same pytest options) | recorded command | before the rename: the same error, reproduced locally; after the rename to `tests/integration/test_returns_dataset_integration.py`: exit 0, 5695/6742 collected |
| Corrective: integration-job `--collect-only` | recorded command | 1029/1047 collected, no errors |
| Corrective: `tests/integration/test_returns_dataset_integration.py` / `tests/unit/returns` / Ruff | database integration / hermetic unit / lint | **10 passed** / **511 passed** / clean, 509 files formatted |

The rows above that name `tests/integration/test_returns_dataset.py` record runs made before
the rename. The tests themselves are unchanged; only the file name changed.

**What the new tests cover:**

- **AC-14 (generator and release):**
  - Two generations are byte-identical, and they equal the release files on disk exactly (no
    extra files).
  - `--check` exits 0.
  - Files contain no CR, and `.gitattributes` pins LF.
  - `provenance.json` holds the SHA-256 of every file, both `bundle_sha256` values, the
    descriptor hash, the counts and `records_sha256`.
  - `scenarios.json` is refused as an import part:
    - in Phase A, for the JSON and the CSV form;
    - through the service commit, alone or next to the bundle, with no writes;
    - through a CLI import of a CSV directory that holds it, with no writes.
- **Format equivalence (AC-08):**
  - JSON and CSV give identical `(source_record_id, record_sha256)` sets, the same descriptor
    and the release `records_sha256`.
  - Their `bundle_sha256` values differ.
- **References and coverage (§8):**
  - With nothing stored, `check_stored` reports no error. That covers every reference, time
    order, one shipment per order, the quantity rule and every evidence relation.
  - Exact generated entity counts. The "about" sizes are also checked to be within 15%; that
    band is an M18-03C test choice, not an approved rule.
  - These are generated counts, not assessed diversity: the 96 category × shape slots give 48
    distinct structural signatures (scratch count; see `handoff.md`, review checkpoint).
    Scenario diversity was not independently assessed, and `generator_intent` is not a label.
  - Every record is synthetic, with the synthetic ID prefixes.
  - Catalog links: actual products, raw-line hashes and catalog prices; every product in at
    least 2 items.
  - 400 customers; 1–3 items per order.
  - Every timestamp falls inside the window, at +05:30.
  - Every return belongs to exactly one driver scenario (96 / 48 / 36), and the categories
    cover 12 × 4 × 2.
  - Category applicability holds, and there are 6 hotspots of 8 returns on products with 20
    items each.
  - Suspected abuse and insufficient evidence follow the §8 rules.
  - At least 12 cancelled returns, with cancellation chains, and the quantity rule holds.
  - The overlay minimums are met, on existing returns only.
- **Scenarios kept separate:**
  - No scenario ID, intent or label text appears in any record.
  - `scenarios.json` is not an import shape and sits outside both import directories.
- **AC-10 (§5.3 fixture scan):**
  - Every `redaction-1` match in the release and in `tests/returns_samples.py` is on the
    closed allowlist.
  - The scan is not vacuous: all four kinds are present.
  - The allowlist rejects values outside it.
  - IDs, actor refs, carriers and dataset text have no match.
  - Redacted free text has no match.
  - Exemptions (hash and money) are narrow in field and form, and object keys are scanned
    (boundary test added at the review checkpoint).
- **AC-08 (`returns verify`):** it compares release hashes with the stored `record_sha256`
  metadata. It does not recompute from stored business fields; that matches frozen §4.7 (see
  the trust boundary in `handoff.md`).
  - **Full import:** after the JSON release is imported as the importer, verify gives `match`,
    with expected = stored = the provenance `records_sha256`.
  - **Idempotency:** the same bytes again give `already_imported` with the same batch; the CSV
    form gives `no_new_records`; nothing is written.
  - **Partial, then the rest:**
    - after the parents only, verify reports `missing` = the counts of the other 7 entities;
    - after the rest is imported (referencing the stored parents), verify gives `match`, and
      the stored `records_sha256` equals the release value.
  - **Extra and changed:**
    - one extra stored review gives `extra = 1`;
    - a changed review in the release gives `changed = 1`.
  - **Descriptor and absent version:** a different descriptor gives `descriptor_matches:
    false`; an unstored version gives everything `missing`.
  - **Read only:**
    - verify runs in a READ ONLY, REPEATABLE READ transaction, as the API role and as the
      importer;
    - all 15 M18 table counts are unchanged;
    - an invalid release never reaches the database.
  - **CLI:** `verify` on the release bundle and on the release CSV directory both give
    `match` (exit 0).
  - Stored comment text is redacted, with non-zero counts, and every stored row is synthetic.

**AC status after M18-03C.** Partial. No AC is marked complete, because the API parts, the
full regression and CI are still open.
- **AC-08:** the domain side is now covered, including `verify`.
- **AC-10:** the domain side and the fixture scan are covered; the API surfaces are M18-04.
- **AC-14:** the generator, `--check`, the release checksums and the `scenarios.json` refusal
  are covered. The protected-path and M17 checks repeat at every package end.
- **AC-06 and AC-07:** the domain side is covered, as after M18-03B.
- **AC-09, AC-11, AC-12 and AC-13:** still need M18-04.

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

**Status (2026-10-26):**
- The M18-03B service-level T1–T8 tests pass (`tests/integration/test_returns_import.py`;
  see the M18-03B results above).
- The M18-02 database backstop evidence for T2/T4/T6/T8 stays as recorded in the M18-02
  results.

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
