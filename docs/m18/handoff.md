# M18 — Handoff

## Current state (2026-10-24, end of M18-02 session)

- **Commits:** M18-02 is committed as `feat(m18): add return data storage and contracts` on
  top of `0d9465a` (`docs(m18): freeze return data foundation design`, parent `bf67d11`). Both
  are pushed to `origin/main` for normal CI only. CI results are recorded in the follow-up
  docs commit.
- **M18-01 design:** FROZEN (owner approval 2026-10-24, Q1–Q5, `plan.md` §8). M18-02 did not
  change any frozen document other than this handoff and the test-matrix status.
- **Staging:** untouched. Deployment, staging migrations, grants, imports and credential
  changes remain **not authorized** (`plan.md` §9).
- **M18-02 status: implemented; one gate open.** All focused, M18 integration and
  full-integration tests pass. The **fresh full hermetic unit run failed** on one test: the
  recursive isolation test timed out (details below). Do not record the full unit suite as
  passed.

## What M18-02 implements

**Contracts** (`src/ecommerce_search/returns/`):
- `versions.py`: `return-data-v1`, `return-reason-codes-1`, `redaction-1`, `returns-import-1`.
- `contracts.py`: frozen Pydantic models for the dataset descriptor and the eleven entities
  (exact §3 fields). Enums, ID / dataset-ID / actor patterns, code-point text limits,
  synthetic-only (`not_synthetic`), and row-local rules. Errors use `RowError` code names as
  error types, and inputs are hidden in errors.
- `canonical.py`: §4.6 timestamp, date, money, CSV integer and boolean forms; canonical JSON;
  `record_sha256`, `descriptor_sha256`, `bundle_sha256` and `records_sha256`.

**Migration and storage** (`migrations/versions/0007_create_return_data_foundation.py`,
`src/ecommerce_search/models/returns.py`):
- 15 tables: `return_datasets`, `return_import_batches`, `return_import_files`,
  `return_import_file_contents`, plus the eleven entity tables. Literal DDL with no app imports.
  The ORM is identical to the migration (constraint-definition test).
- Composite foreign keys per dataset version for every reference. Each row is tied to its
  batch's dataset and to a file of that batch. Foreign keys to `products` and to the catalog
  dataset version.
- One shipment per order (UNIQUE), the 152-character evidence key, and natural-key UNIQUEs.
- Raw contents are bound to their metadata checksum by a composite foreign key and a CHECK on
  the bytes.
- CHECKs for enums, formats, ranges, row-local time order and status fields, `is_synthetic`,
  file-name allowlist, file and manifest sizes, and batch-count consistency.
- Append-only: UPDATE, DELETE and TRUNCATE are blocked on all 15 tables.
- Deferred quantity constraint trigger: non-cancelled sum ≤ item quantity. It serializes
  writers per order item with a two-key advisory lock (class 18) and refuses REPEATABLE READ.
- Downgrade to `0006` restores the 0006 schema exactly, and re-upgrade restores the 0007 schema.

**Permissions** (`src/ecommerce_search/returns/grants.py`, `returns-grants-1`):
- Role names are parameters. The script never creates roles, sets passwords or changes role
  attributes.
- It keeps the API role's pre-M18 access: CONNECT, USAGE, and SELECT on an explicit list of
  all 19 pre-M18 tables (including M13/M14). It revokes nothing on those tables.
- It grants SELECT on the M18 metadata and record tables, and runs
  `REVOKE ALL ON return_import_file_contents FROM <api>, PUBLIC`.
- The importer gets SELECT + INSERT on the M18 tables, INSERT only on contents, column-level
  SELECT on the catalog reference columns, and USAGE on the 14 identity sequences.
- It runs in one transaction and fails loudly with a rollback.
- CLI: `python -m ecommerce_search.returns.grants --database … --app-role … --importer-role …`.
  Deploy wiring is M18-07.

**Tests:**
- Unit: `tests/unit/returns/` (3 files, 212 tests).
- Integration: `test_returns_schema.py` and `test_returns_grants.py` (175 tests). Scratch
  databases only, with real per-test LOGIN roles.
- Support: `tests/returns_contract_sets.py` holds the literal contract sets;
  `tests/returns_samples.py` holds SYNTHETIC records; `tests/integration/returns_support.py`
  holds the direct-SQL helpers.
- Updated existing tests (head revision 0007, table sets, constraint-trigger exclusion):
  `test_migrations.py`, `test_catalog_schema.py`, `test_dense_migration.py`,
  `test_search_migration.py`, `tests/unit/catalog/test_catalog_models_metadata.py`. Every
  assertion keeps strict equality.

## Implementation boundaries (recorded at owner direction in `contracts.md` §10)

1. `catalog_dataset_version` (snapshot triple) is a text string in the catalog's own form
   (`^[1-9][0-9]{0,8}$`), so it can reference `catalog_datasets(dataset_id, dataset_version)`.
2. `descriptor_sha256` uses the record-hash formula with entity name `dataset`.
3. Row-local errors: `required` for a missing status-dependent field, `temporal_order` for
   order, and `format` for a field that the status forbids (`delivered_at` when not delivered,
   `received_at` on a cancelled return). The frozen code list has no exact code for the last
   case; M18-03 must confirm the mapping.
4. The stored redacted free text is bounded at twice the input limit (4,000 / 2,000), because
   replacement tokens can be longer than what they replace. Input limits apply to raw text on
   import.
5. **Not database-enforced:** equality of `catalog_raw_sha256` with the stored
   `raw_catalog_records` row. A foreign key would need a new UNIQUE on a pre-M18 table, which
   was not added. This rule stays in the M18-03 service (rechecked under the lock). Also
   service-level: evidence-to-product relation, cross-table time order, inspection only on a
   received return, and an order + product complaint without an item.
6. The grants script also asserts that the importer has INSERT only on contents, and that
   neither role is a superuser or a member of the table owner. These checks are stricter than
   the §2.1 list, not a new privilege.

## Commands run and actual results (2026-10-24)

| Command | Result |
|---|---|
| AC-14 SHA-256 listing of untracked M17 outputs (10 files), kept outside the repo in the session scratchpad; listing file SHA-256 `cf74568d…41adf7` | taken before any edit; re-checked at end: `sha256sum -c` exit 0 |
| `git diff --exit-code bf67d11 -- <protected paths>` | exit 0 (start and end) |
| `pytest tests/unit/returns` (final) | **212 passed** |
| `pytest tests/unit/catalog/test_catalog_models_metadata.py tests/unit/returns` | **232 passed**, exit 0 |
| `pytest tests/integration/test_returns_schema.py tests/integration/test_returns_grants.py` (after final format) | **175 passed**, exit 0 |
| Full integration `pytest tests/integration -m "integration and not real_model"` (before the metadata-test edit and the format-only change) | **939 passed, 18 deselected**, exit 0 (8 min 18 s) |
| First full unit run (`pytest tests/unit`) | exit 1: **2 failed, 5390 passed, 4 skipped** (1 h 58 min, including about 78 min of Windows Modern Standby, 17:09–18:27). The failures were (a) the M18-caused metadata table-set test (now fixed) and (b) the isolation test `TimeoutExpired`. A Ctrl+C interrupt attempt did not reach the process; the run finished on its own. |
| **Fresh full unit run** (`pytest tests/unit`, 19:02–19:54) | **exit 1: 1 failed, 5391 passed, 4 skipped** (52 min 45 s). Only failure: `tests/unit/test_unit_isolation.py::test_whole_unit_suite_passes_without_a_database_password`, `subprocess.TimeoutExpired` from its fixed `timeout=1200` on the nested hermetic run (started 19:28:24). The nested run's own summary was captured by the test and is not available. All 4 skips are symlink-permission platform skips on this Windows account. |
| `ruff check .` | exit 0 |
| `ruff format --check .` | exit 0 (486 files) |

**Isolation-test timeout analysis (no change made):** the nested run is finite (guard flag
`ECOMMERCE_SEARCH_HERMETIC_CHILD`; process depth stayed at four). It reruns about 5,400 tests,
and the outer run of the same tests took about 26 minutes on this machine, which is more than
the 1,200-second budget. Whether that comes from machine speed or suite growth is not
established. Not changed: the timeout, the test, and any skip.

**Environment:**
- Docker Desktop (user-local, already installed) and `docker compose up -d db` were started
  for the integration tests.
- Every database written was a throwaway `ecommerce_search_test_*`; the target name was checked
  first. Per-test roles were named `m18t_*`.
- End check: 0 leftover scratch databases, 0 leftover `m18t_*` roles.
- Development database read only (`alembic_version`): still `0004` at start and end. Staging
  untouched.

## AC coverage after M18-02

| AC | M18-02 coverage | Remaining |
|---|---|---|
| AC-01 | Contracts versioned; DB enum literals equal the Pydantic enums; canonical forms tested | — (M18-03 adds CSV-adapter round trips) |
| AC-02 | Every FK/UNIQUE/CHECK with direct SQL; one shipment per order; 152-char key; migration round trip with schema equality; T2/T4/T6/T8 database backstops | Snapshot-hash equality is service-only (M18-03); T1/T3/T5/T7 are service tests (M18-03) |
| AC-03 | Append-only on all 15 tables; real-role privileges; preservation; raw-file revoke; idempotency; no default privileges or ownership | AC-17/18 runs as the real `search_app` (M18-06/07) |
| AC-04 | Eight provenance fields on every record (join test); `is_synthetic` enforced; descriptor hash | `dataset_mismatch` (service, M18-03); API record provenance (M18-04) |
| AC-05 | Exact column allowlists (ORM and DB introspection); exact Pydantic field sets; no decision fields | Response-schema allowlists (M18-04) |
| AC-13 (3), (4) | Privilege matrix for both roles; schema has no decision or outcome field | (1), (2), (5) in M18-03/04 |
| AC-14 | Protected-path diff exit 0; M17 SHA-256 listing check exit 0 | Repeat at each package end |

**AC-01–AC-05 are not fully closed**, because of the pending service and API parts above and
the open full-unit gate.

## Files changed by M18-02

- **New:**
  - `src/ecommerce_search/returns/__init__.py`, `versions.py`, `contracts.py`, `canonical.py`,
    `grants.py`
  - `src/ecommerce_search/models/returns.py`
  - `migrations/versions/0007_create_return_data_foundation.py`
  - `tests/returns_contract_sets.py`, `tests/returns_samples.py`
  - `tests/unit/returns/test_returns_canonical.py`, `test_returns_contracts.py`,
    `test_returns_storage_records.py`
  - `tests/integration/returns_support.py`, `test_returns_schema.py`, `test_returns_grants.py`
- **Modified:**
  - `src/ecommerce_search/models/__init__.py`
  - `tests/integration/test_migrations.py`, `test_catalog_schema.py`, `test_dense_migration.py`,
    `test_search_migration.py`
  - `tests/unit/catalog/test_catalog_models_metadata.py`
  - `docs/m18/handoff.md`, `docs/m18/test-matrix.md`
- **Preserved untouched:** `docs/m12-diagnostic-tooling.md`, `.claude/`, `docs/m17-handoff.md`,
  `evals/results/m17/unsupported-claims/`, `.env`, and historical artifacts.

## Next task

1. **Owner decision on the isolation-test timeout gate.** It is a pre-existing 1,200-second
   budget, exceeded on this machine. Options:
   - rerun the full unit suite on an awake, idle machine, or in CI (the CI backend job is the
     isolation child itself);
   - approve a separate change to the budget.

   No change was made.
2. Owner review of the interpretations above, then review `git diff` and commit M18-02.
3. Only after that, and with approval: **M18-03 Ingestion, Privacy & Fixtures**.
