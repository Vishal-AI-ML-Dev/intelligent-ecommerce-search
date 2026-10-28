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

## M18-05 results (actual, 2026-10-28; in the scoped M18-05 commit)

| Check | Kind | Result |
|---|---|---|
| `npm run typecheck` | frontend | exit 0 |
| `npm test` (Vitest, whole frontend) | frontend unit | **140 passed** in 6 files: returns page 29 and returns API 25 (new), HTTP client 29 (13 existing + 16 new), search 21, assistant 31, comparison 5 (unchanged); 0 `act` warnings |
| `npm run build` | frontend | exit 0 |
| `tests/unit/test_frontend_fixture_contract.py` + `tests/unit/returns/test_returns_fixture_privacy.py` + `tests/unit/test_frontend_serving.py` | hermetic unit | **89 passed** |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 520 files already formatted |
| `git diff --check` on changed tracked files; trailing whitespace, CR and final newline on new files | recorded command | exit 0 / none |
| Secrets scan of every changed and new file | recorded command | one hit, the `type="password"` attribute of the token input; no token, password or key values |
| AC-14 protected-path diff against `bf67d11`; `git diff -- data/` | recorded command | exit 0 / exit 0 |
| AC-14 M17 SHA-256 listing | recorded command | **not checked**: still missing (see `handoff.md`) |

**AC-15 coverage (Vitest):** gate success, 401 explanation with request ID, 503 not configured,
empty token not sent; sign-out and 401-during-session clear the token and data; a response that
arrives after sign-out is dropped; token absent from `localStorage`, `sessionStorage`, cookies,
the DOM and console output; every request goes to a `/returns` path with the token; disabled
import (`can_commit` false) and no upload without `can_validate`; loading; empty list and empty
history; error with request ID; 422 parameter names without values; "Showing 200 of at least
345" and "at least N" texts; Next/Previous with the applied filters and cursor; detail with
statements and observations side by side and no flag; markup and injection rendered as text;
"Not recorded" for absent evidence; provenance blocks; batch history and file metadata; import
201, 200 and 422 reports; client-side pre-checks; the third app tab. Fixtures are validated
against the Pydantic models (`test_frontend_fixture_contract.py`), and the committed vocabulary
equals `ReturnStatus`, `ReasonCode` and `Category` exactly.

**Not run (by scope):** Playwright (AC-16, M18-06), any database or live API, the full Python
suite, the Windows isolation wrapper (still **FAILED**), the CI-equivalent `--collect-only` (no
new Python test module was added; two existing modules were extended).

### M18-05 pre-commit review results (actual, 2026-10-28)

| Check | Kind | Result |
|---|---|---|
| `npx vitest run src/domains/returns src/shared/http` | frontend unit | 92 passed (4 files) |
| Session-isolation and hook guard tests against a mutant without the abort check | recorded mutant run | 4 failed / 5 passed, as intended; file restored (SHA-256 prefix `1090ee56ae10761b` unchanged) |
| `npm run typecheck` / `npm run build` | frontend | exit 0 / exit 0 |
| `npm test` | frontend unit | **149 passed** in 7 files (+5 session isolation, +4 `useReturnsRequests`); 0 `act` warnings |

AC-15 additions: a late old-session response (data, 500 or 401) cannot repopulate, overwrite or
sign out a new session; timestamps are labelled UTC. Finding R1 (token-path dot segments; not
reachable from current call sites) was fixed with owner approval:

| Check | Kind | Result |
|---|---|---|
| `npx vitest run src/shared/http src/domains/returns` (R1 tests) | frontend unit | 119 passed (after one test-expectation correction, see `handoff.md`) |
| `npm run typecheck` / `npm run build` | frontend | exit 0 / exit 0 |
| `npm test` | frontend unit | **176 passed** in 7 files (+27 client routing cases); 0 `act` warnings |

## M18-04C results (actual, 2026-10-28; uncommitted, awaiting diff, schema and OpenAPI review)

| Check | Kind | Result |
|---|---|---|
| `tests/unit/returns/test_returns_api_read.py` (new) | hermetic unit | **122 passed** |
| Focused regression: `tests/unit/returns` + settings, app, observability, dependencies, engine, compose, frontend serving, `test_filtered_api.py`, `test_reranked_api.py`, assistant API | hermetic unit | **1489 passed** |
| `tests/integration/test_returns_api_read_integration.py` (new, 24 tests) | database integration | **NOT RUN**: no existing disposable scratch database was available (Docker engine not running) and creating one was not authorized; `--collect-only` only (24 collected) |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 517 files already formatted |
| CI-equivalent hermetic `--collect-only` | recorded command | 6030/7101 collected (1071 deselected), no errors |
| Integration-job `--collect-only` (placeholder password, no connection) | recorded command | 1053/1071 collected (18 deselected), no errors |
| Duplicate `test_*.py` basenames | recorded command | 0 |
| AC-14 protected-path diff against `bf67d11` | recorded command | exit 0 |
| AC-14 M17 SHA-256 listing | recorded command | **not checked**: the listing is still missing (see `handoff.md`); no fresh baseline was taken, since it would not prove the earlier state |

**What the unit tests cover (with a faked read layer):**
- **AC-11 (read routes):** 503 with no tokens configured, 401 for missing, unknown, wrong-scheme
  and empty tokens on all four routes; both tokens can read; access is checked before
  parameters.
- **AC-10 / E1 / E4:** 13 invalid list parameters and 6 malformed path parameters give 422 with
  the name and code only, never the value. Database errors on every route give a safe 500 that
  logs only the type. A broken snapshot line gives a 500, never a `null`.
- **E3:** the filter hash excludes `limit`, `cursor` and absent filters, and normalizes offsets
  to UTC. The hashed values are the ones the SQL binds, and no value appears in the SQL text.
- **Cursors:** round trip with one encoding per position; 21 malformed shapes are refused
  (padding, key order, extra key, `v`, bool or string versions, non-canonical timestamps, bad
  IDs, upper-case hash, non-ASCII, non-object); filter mismatch; batch cursors refuse return
  cursors and non-canonical UUIDs.
- **Through the routes:**
  - a next cursor comes from the last item, and the last page has none;
  - a page-size change keeps the cursor valid;
  - reuse with other filters gives 400 `cursor_mismatch`, logged by code only and never
    echoed.
- **AC-13 (2) / D7:**
  - each read function, against a recording engine, runs REPEATABLE READ, then
    `SET TRANSACTION READ ONLY`, then SELECTs only, then rolls back;
  - capabilities opens no snapshot;
  - the read module never names `return_import_file_contents` or `raw_bytes`.
- **AC-05:** exact field allowlists for the 22 response models, no decision, score or flag
  field names, and the `/returns` OpenAPI snapshot.
- **AC-13 (1):** the route inventory is the pre-M18 set plus exactly the 7 §7 routes (the
  access-test pin and both buyer path-set pins).

**What the integration tests would cover (not run):**
- **AC-12:**
  - ties across dataset IDs, across versions 1/2/10 and inside one version;
  - paging at sizes 1–5 and with mixed sizes, compared with an order computed independently in
    Python;
  - each filter against independent SQL, plus `requested_from` inclusive and `requested_to`
    exclusive;
  - cursor mismatch, malformed cursors and offset-equivalent timestamps.
- **Detail:** the same `return_id` in two versions gives two details. Content, snapshot and
  provenance are compared with stored rows, and every return of one version serves a detail as
  the real API role.
- **Batches and 404s:** batch order and file metadata only; the 404s.
- **AC-13 (2):** a read snapshot is `repeatable read` with `transaction_read_only = on`, and a
  write attempt as the superuser fails with SQLSTATE 25006.

### M18-04B API import integration (2026-10-28)

| Check | Kind | Result |
|---|---|---|
| `tests/integration/test_returns_api_import_integration.py`: local commit and idempotent repeat, validate-only (2 tokens), scope-before-gate, commit flag off, read-only role rejected, wrong password, Settings equality boundary | database integration (scratch DB, real `m18t_*` roles) | **9 passed** (first run: 1 failed, a test-helper bug) |
| Cleanup and secret scan (database/role lists; server log and pytest output) | recorded command | lists identical; 0 secret hits |

- **AC-07/AC-08 through the API:** the import is atomic and idempotent (the repeat writes
  nothing).
- **AC-09:** the API path is now live.
- **AC-11:** the commit gate is checked on a live database.
- **AC-13 (2):** validate writes nothing as the API role.
- **AC-13 (3):** least privilege is enforced at commit; a read-only role is refused with
  `missing_privilege`. Admin/superuser rejection **through the API is untested**; it is
  covered only at the service and CLI level (M18-03B).

### Catalog ingest timestamp correction (2026-10-28; outside M18 feature scope)

| Check | Kind | Result |
|---|---|---|
| Lock-order regression for `ck_products_updated_not_before_created` (forced order, no sleeps), original code | database integration | 1 failed (`IngestRefused`), reproducing the CI failure |
| Same regression with `updated_at = GREATEST(statement_timestamp(), created_at)` | database integration | passed; 3/3 reruns passed |
| `tests/integration/test_catalog_ingestion.py` | database integration | **37 passed** |
| Scratch cleanup (database and role lists before = after) | recorded command | identical |

The regression asserts that v2 succeeds, that SYN-LAP-0001 is version 2 with price 777777,
that `created_at` is unchanged, and that `updated_at >= created_at`. The existing concurrent
test is unchanged. Timestamp semantics: on update, `updated_at` is now the update statement's
time (never before `created_at`); insert defaults are unchanged.

### M18-04C corrective: slot ownership under cancellation (2026-10-28)

| Check | Kind | Result |
|---|---|---|
| Cancelled-caller race test, rewritten with explicit events (CI failure on `2b5e221`) | hermetic unit | 30/30 runs passed; it fails against a mutant that frees the slot on cancellation |
| Slot ownership: cancellation before dispatch, queued-job cancellation (deterministic gate in the real anyio worker), running-worker cancellation, worker exception, normal completion | hermetic unit | 20/20 runs passed. Against HEAD's `run_in_slot`, the queued case fails (work ran without the slot, double release) |
| Upload + access test files | hermetic unit | **218 passed** |

Each case checks that work never runs without owning the slot, that the slot is released
exactly once with no leak, and that abandoned queued work does not execute. This is a
controlled unit reproduction; it was not observed on a server.

### M18-04C first database run (2026-10-28)

| Check | Kind | Result |
|---|---|---|
| `tests/integration/test_returns_api_read_integration.py` | database integration (scratch DB, real `m18t_*` roles) | **29 passed** |
| Affected unit tests (incl. summary provenance and the hardened role helper) | hermetic unit | **766 passed** |
| Cleanup: database and role lists before = after | recorded command | identical |
| Server log and pytest output scanned for `SCRAM-SHA-256`, `CREATE ROLE`, `PASSWORD` | recorded command | 0 in each |

- **AC-12:** ties (across dataset IDs, versions 1/2/10/20, and inside one version), paging at
  sizes 1–5 and mixed sizes, every filter, the `requested_from`/`requested_to` bounds, cursor
  errors, qualified detail identity, 404s and batch order all pass against independent SQL.
- **AC-04:** summary and detail provenance, and every nested record's provenance, equal the
  stored references. In the split-batch version, comments carry the second batch and their
  return the first.
- **AC-13 (2):** a read snapshot is `repeatable read` with `read_only = on`, and a write fails
  with 25006.
- **I1 / M1:** I1 holds for every return of version 1 (with same-product negatives); raw prices
  keep their JSON type.
- **Roles:** stored as SCRAM, the fixture roles authenticate, a wrong password gives 28P01, and a
  real duplicate `CREATE ROLE` gives a sanitized `RoleSetupError` (42710) with no secret on any
  surface.

### M18-04C review corrections (2026-10-28)

| Check | Kind | Result |
|---|---|---|
| Affected unit tests (`test_returns_api_read.py`, new `test_returns_roles.py`, `test_returns_api_access.py`, `test_returns_api_upload.py`, `test_filtered_api.py`, `test_reranked_api.py`) | hermetic unit | **762 passed** (first run: 2 failed, see `handoff.md`) |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 519 files formatted |
| Integration additions: per-record provenance (split batches), I1 positive and negative, M1 raw price | database integration | **NOT RUN** (no database) |

- **AC-04:**
  - unit: nested records require their own `provenance`, built only from their own columns,
    and the queries join their own references;
  - integration (not run): a comment's batch differs from its return's batch, and every nested
    record equals its stored provenance.
  - The summary is pinned to the frozen §7 list (reported conflict).
- **AC-05:** the allowlists now include each nested record's `provenance`,
  `ListingSnapshot.price`, `BatchEntityCounts` and the error bodies.
- **AC-10 / I4:** repeated known parameters give 422 `repeated` without values; unknown
  parameters are ignored; access is checked first.
- **OpenAPI:** the read routes document only the safe error bodies, and the 04A/04B responses
  and the `ImportReport` component are unchanged.
- **Fixture secret safety:** RFC 7677 vector; the password is never in the SQL text; on failure
  it is absent from the message, `repr`, traceback, SQLAlchemy logs, stdout and stderr, and
  nothing is chained.

**AC status after M18-04C (implementation).** Partial; no AC is closed. AC-12, AC-04 (API
provenance) and AC-13 (2) for reads need the integration run. The hermetic parts of AC-05,
AC-10, AC-11 and AC-13 (1) are covered.

## M18-04B results (actual, 2026-10-27; uncommitted, awaiting diff review)

All evidence is **hermetic unit**. The service is a recording fake, except where the real
service is called on uploads that fail Phase A, with a database sentinel that fails if used.
There is no database-integration or browser evidence.

- `test_returns_api_upload.py` + `test_returns_api_access.py`: **198 passed**, after one
  test-bug fix (the first run was 1 failed, 197 passed). Focused regression: **1352 passed**.
- **Coverage:**
  - Reader: parts by field name with file names ignored; parts split across 7-byte chunks; a
    missing closing boundary and an empty body give 400.
  - Part count: 12 parts accepted; the 13th → 400 with later chunks unread.
  - Size: a streaming 413 stops reading, and the epilogue is counted.
  - Time: the total-time 408 is checked with fast chunks.
  - Headers: a disconnect, 11 invalid part-header forms and identity encodings.
  - Request level: 415 media types, missing or overlong boundary, declared-length checks.
  - Slot: released on success and failure; held through cancellation until the worker
    finishes; released if cancellation comes before the worker starts.
  - Routes: the access, gate and check order, all before any body read; status mapping
    201/200/200/422; the importer engine and label used for commit; 503/403/500 mappings with
    the slot released; chunked over-cap upload; unknown and repeated names reported by the
    real service without echo; no names, bodies or tokens in app logs.
  - No temporary file and no `request.form` for a 3 MiB part (AC-09, partial); no route
    declares `Form`/`File`; OpenAPI documents the multipart body.
- **Review checkpoint (2026-10-27):** C1/C2 approved (`contracts.md` §6.3). Library
  logging fixed and tested with malformed uploads and captured logs (root handler, pytest
  capture, stderr). Huge `Content-Length` gives 413, not 500. Tests added for a deadline with no
  body bytes and for 405 on the import routes. `test_returns_api_upload.py` +
  `test_returns_api_access.py` → **204 passed**; `tests/unit/returns` + app + observability →
  **743 passed** (hermetic unit).
- **Repeated-name fix (2026-10-28):** repeated, case-variant and `name`+`name*` parameters give
  400; `name*` alone is still 400; quoted file-name text containing `; name=` is still accepted.
  Upload + access tests → **213 passed** (after one attempt blocked by a Windows Application
  Control DLL policy; the retry ran unchanged).

## M18-04A results (actual, 2026-10-26; uncommitted, awaiting owner review)

M18-04A adds retailer access control, the commit gate, the lazy importer engine, safe
`/returns` errors with `no-store`, and `GET /returns/capabilities`. All evidence is **hermetic
unit (API through TestClient, no database)**. There is no database-integration or browser
evidence in 04A.

| Run | Kind | Result |
|---|---|---|
| `tests/unit/returns/test_returns_api_access.py` (first run) | hermetic unit | 1 failed, 97 passed (test bug: assertions read the test client's own URL log; fixed) |
| `tests/unit/returns/test_returns_api_access.py` | hermetic unit | **122 passed** |
| Focused set before the pin update (access file, settings, app, observability, dependencies, engine, compose, `tests/unit/search`, assistant API) | hermetic unit | 2 failed, 3126 passed, 3 skipped (the two app path-set pins; updated) |
| **Final focused set:** `tests/unit/returns` + settings, app, observability, dependencies, engine, compose, frontend serving, `tests/unit/search`, assistant API | hermetic unit | **3647 passed, 3 skipped** |
| `ruff check .` / `ruff format --check .` | lint | all checks passed / 511 files formatted |
| CI-equivalent hermetic `--collect-only`; integration `--collect-only`; duplicate basenames | recorded command | 5817/6864, no errors; 1029/1047, no errors; 0 |
| AC-14 protected-path diff; M17 SHA-256 listing | recorded command | exit 0 / 10 OK |
| Full unit suite, isolation wrapper, integration suite, CI | — | **not run**; local isolation timeout stays **FAILED** |

**What the new tests cover:**
- **AC-11 (access):**
  - configuration cases: none (503, even with a token), viewer only, importer only, both;
  - 16 malformed, unknown or oversized `Authorization` values (including 100,000 characters),
    non-ASCII bytes, repeated headers, case-insensitive scheme, tokens in cookies or query
    parameters ignored → 401 with `WWW-Authenticate: Bearer`;
  - viewer and importer scopes; 403 for a missing scope (test-only route on the production
    route class);
  - only digests kept; both digests compared for every token; an unset slot never matches;
  - rotation (rebuilt app: old token 401, new 200) and emergency revocation (503);
  - settings: lengths 31/32/43/256/257, invalid characters, equal tokens (init and env),
    both-or-neither DB credentials, user ≠ `POSTGRES_USER`, role-name form, strict boolean;
    no token or password in any validation error, `repr` or URL string.
- **Commit gate:** all 16 combinations of the four conditions; only all four give
  `can_commit` for the importer; the viewer never can; the importer token alone never can; the
  engine holder exists only with the gate open, builds nothing at startup, builds one separate
  engine (importer user, one connection) on first use and is disposed at shutdown.
- **AC-10 (API surfaces in 04A):** 422 carries parameter names and codes only (hostile values
  absent from response and app logs); a forced Pydantic error and a library error with a
  connection-string-like message become a safe 500 that logs only the type; `HTTPException`
  detail never echoed; denials logged by code only; no token in any log.
- **Capabilities:** exact field set, exact §4.4 limits, reason codes; no token, password, user,
  label, host or gate detail; `no-store`; no cookie.
- **Unmatched paths:** `/returns`, `/returns/`, unknown and hostile paths, slash redirects →
  safe 404 with `no-store` and no `Location`; wrong methods → safe 405 with `Allow`; HEAD gets
  `no-store`; `/returnsx` keeps the buyer 404.
- **Buyer isolation (AC-17, partial):** with and without retailer tokens configured, and with
  and without `Authorization` headers, buyer responses (FastAPI 422 shape, 404, 405) are
  identical and carry no `no-store` or `WWW-Authenticate`.
- **AC-13 (1), partial:** OpenAPI paths equal the pre-M18 set plus `/returns/capabilities`
  only. The final 7-route inventory is 04C.

**Review checkpoint (2026-10-27).** One fix: a Starlette `HTTPException` on a `/returns`
route now maps to the fixed `request_failed` shape instead of a 500 (new case in
`test_http_exception_details_are_not_echoed`). After it: the access file (123 tests) plus
`tests/unit/test_observability.py` → **148 passed** (hermetic unit); Ruff clean, 511 files
formatted. The whole-stack logging review is a configuration read (uvicorn access-log filter
covered by the existing M24A unit tests; Caddy and AWS not run). Contract amendments A1 (token
syntax and cap) and A2 (422 `parameters`) were approved and applied as `contracts.md` §6.2
(documentation only; the tests already cover both). CI result: see `handoff.md`.

**AC status after M18-04A.** Partial; no AC is closed. AC-11 is covered for capabilities and
the access dependency; the full 7-route matrix is 04C. AC-09, AC-12 and the rest of AC-10/13
need 04B/04C.

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
