# M18 — Handoff

## Current state (2026-10-26, end of the M18-03C session)

- **HEAD at start:** `4d495c8a5a72aa70e1b0c1def32bd74c52032c25` (M18-03B), as expected.
  - **CI on `4d495c8`:** green **as confirmed by the owner**. This session could not open the
    private run, so this is owner-confirmed, not independently verified.
- **M18-03C (Synthetic Dataset & Verification): implemented and focused-verified.** After the
  review checkpoint below, the owner authorized one scoped commit and a normal push for CI.
  - That commit, `4821bbd`, **failed CI during test collection**, as reported by the owner.
  - The cause was a duplicate test-module basename. A corrective commit renames one file; see
    "CI failure on `4821bbd` and corrective fix" below.
  - The corrective SHA and its CI result are reported in the session summary, not in this file.
  - The full unit suite, the isolation wrapper and the full integration suite were not run.
  - M18 is not accepted, and M18-04 has not started.
- Unrelated working-tree items are unchanged: `docs/m12-diagnostic-tooling.md` (modified),
  untracked `.claude/`, `docs/m17-handoff.md` and `evals/results/m17/unsupported-claims/`.
- **Not done:**
  - no dev or staging writes, migrations or grant changes (the development database was read
    only and is still at `0004`);
  - no installs, provider calls, deployments or AWS actions;
  - no API, frontend, classifier, root-cause or approval work.

  The already-running local Docker database was used for scratch-database tests only.
- The local full-unit-suite isolation timeout from M18-02 is still **FAILED** (not rerun). This
  is separate from CI evidence.

### What M18-03C delivers

**Generator:** `scripts/generate_returns_dataset.py@1` (contracts §1, §8).
- It is deterministic: no randomness, clock or network. Variation comes from SHA-256 of fixed
  keys. `--check` regenerates every file in memory, compares the bytes, and reports extra files.
- It reads the committed catalog through the existing `load_catalog` and checks it against
  its provenance. It uses:
  - the actual product IDs;
  - the SHA-256 of each raw catalog line;
  - the normalized catalog price (`unit_price` equals it).

  No catalog fact is altered or invented.
- Record hashes and `records_sha256` come from the existing `contracts`/`canonical` modules,
  so a change there also fails `--check`. Unlike the self-contained catalog generator, this one
  imports the application.
- Record IDs are numbered in hash-shuffled order where an ID could otherwise reveal its
  scenario (returns, reviews, complaints, cases).

**Release:** `data/seed/returns_v1/`, 15 files, LF line ends only.
- `.gitattributes` gains `data/seed/returns_v1/** text eol=lf`, so checkouts with
  `autocrlf=true` keep the checksums.
- `bundle/returns_v1.json`: 1.1 MB, one record per line.
- `csv/`: `manifest.json` plus 11 CSVs, every field quoted, LF line ends.
- `provenance.json` records:
  - `records_sha256`
    `497cebbd99fed434d2759be1a1ec8790d4d242e5a95b31e74c5347a76d9763ca`;
  - the descriptor hash and both `bundle_sha256` values (JSON and CSV);
  - per-entity counts and the catalog checksum;
  - the SHA-256 of every other file.
- `scenarios.json`: 112 scenarios, outside both import directories.
  - Every entry has `listing_truth: "unknown"` and
    `label_status: "generator_intent_not_label"`.
  - It is generator intent: not labels, and not M19/M20 ground truth.
- Dataset descriptor: `synthetic-returns` v1, `source_type` synthetic, `authored_on`
  2026-10-26, no licence identifier. Every record has `is_synthetic: true`.

**Generated counts** (5,313 records):

| Entity | Count | §8 design |
|---|---|---|
| orders / customers | 600 / 400 | 600 / 400 |
| order items | 900 (every product ≥ 2; each hotspot product 20) | 900 |
| shipments / delivery events | 600 / 2,365 | 600 / about 2,400 |
| returns | 180 = 96 category × shape + 48 hotspot + 36 controls; 12 cancelled | 180 |
| return comments | 161 (133 base + 28 overlays) | about 150 |
| inspection observations | 145 | about 140 |
| customer reviews / support complaints | 150 / 60 | 150 / 60 |
| correction cases / evidence links | 20 / 132 | 20 / about 120 |

**`returns verify`:** `src/ecommerce_search/returns/verify.py` (§4.1, §4.7), and the CLI
command `python -m ecommerce_search.returns verify <bundle.json | csv-dir>`.
- **Release side:** the given upload, through the existing Phase A (`validate_upload`).
- **Stored side:** read in one READ ONLY, REPEATABLE READ transaction: the dataset row, then
  `source_record_id` and `record_sha256` of each entity.
  - It reads no raw contents, catalog table or free text, and takes no lock.
  - It runs as the API role or as the importer.
- **`VerifyReport`:**
  - `status`: `match`, `mismatch` or `invalid`;
  - `dataset_stored` and `descriptor_matches`;
  - the expected and stored `records_sha256`;
  - per entity: `expected`, `stored`, `missing`, `extra` and `changed`.
- **CLI exit codes:** 0 `match`; 1 `mismatch` or `invalid`; 2 an error (`VerifyStorageError`,
  fixed message). No importer label is needed.
- **Surface:** `service.__all__` (the pinned import surface) is unchanged. Verify has its own
  pinned surface, and the forbidden-name check now covers it.

### Interpretations made in M18-03C (frozen text unchanged; owner review requested)

1. **Insufficient evidence (8):** every instance has `NOT_PROVIDED`, no comment and no
   inspection (the §8 rule). The four shapes can therefore differ only through other records:
   - `conflicting`: a support complaint with topic `damaged`;
   - `noisy_text`: a Hinglish complaint;
   - `consistent` and `missing_evidence` have the same record shape (return, order and
     shipment only).
2. **Suspected abuse (8):** the inspection observation (`item_differs_from_order`) is the only
   signal, so every instance keeps it.
   - `missing_evidence` means no comment.
   - `conflicting` adds a second, disagreeing inspection (`as_new`).
   - The 8 customers each have exactly one order and no complaint.
   - No correction case is opened.
3. **Overlays** are extra comments, not rewrites of existing comments.
   - They go only on existing, non-cancelled returns that already have a comment.
   - Nothing is added to a missing-comment or insufficient-evidence return, and no extra
     return is created.
   - Counts: 10 allowlisted personal-data-like, 6 prompt-injection, 4 markup, 4 near-limit
     (2,000, 2,000, 1,999 and 1,998 code points), 4 Devanagari.
4. **Correction cases (20):**
   - one per hotspot (6);
   - 14 on category returns: instance 1 of `consistent` for the 9 categories that have a focus
     area, plus instance 1 of `conflicting` for the first 5 of them;
   - none for change of mind, suspected abuse or insufficient evidence;
   - `opened_at` falls after the evidence (2026-06-20 to 2026-06-24).
5. **"About" sizes:** the generated values are listed above, and tests pin them exactly. The
   tests also check that they are within 15% of the §8 "about" values. That 15% band is an
   M18-03C test choice, not a previously approved rule.
6. **Scenario families:**
   - Drivers: `category_evidence_shape` (96), `sku_hotspot` (6), `plain_control` (2).
   - `structural_rule` (3) and `text_overlay` (5) reference records that are already in a
     driver. The three structural rules are cancellation chains, cancelled returns on their
     own, and two returns on one quantity-2 item.
   - `record_ids` maps each entity to its sorted IDs; evidence uses `case|type|id`.
7. **Undelivered orders:** 26 of the latest 36 orders (2026-06-15 to 2026-06-24) are
   `in_transit`, `pending`, `delivery_failed`, `returned_to_origin` or `unknown`. None is
   returned.
8. **Verify semantics:**
   - `changed` means the same natural key, stored with a different `record_sha256`.
   - `match` also requires an equal stored descriptor.
   - A dataset version that is not stored gives `mismatch` with `dataset_stored: false`, and
     every record is `missing`.
9. **§5.3 scan scope:**
   - It covers the release files and `tests/returns_samples.py`.
   - Exemptions are narrow in field and form (narrowed at the review checkpoint below). A
     value is skipped only in two cases:
     - it is 64 lowercase hex digits under a named SHA-256 field, or inside the provenance
       `files` and `bundle_sha256` maps;
     - it is a canonical amount under `unit_price`.

     Object keys are scanned.
   - Hostile strings inside test code are not fixtures; they were reviewed in M18-03A.
   - There are no frontend fixtures yet (M18-05).

### Commands run and actual results (2026-10-26, M18-03C)

| Command | Result |
|---|---|
| `git rev-parse HEAD`; `git status --short` | `4d495c8a5a72…`; only the four unrelated items |
| Dev DB `alembic_version` (read only); scratch databases; `m18t_*` roles | `0004`; 0; 0 (before and after the integration runs) |
| `uv run python scripts/generate_returns_dataset.py` | wrote 15 files, 5,313 records |
| `uv run python scripts/generate_returns_dataset.py --check` | exit 0: 15 files regenerate byte-for-byte |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` | **510 passed**: the earlier 458, plus 28 dataset, 16 privacy and 8 verify tests. The CLI command-set test was rewritten. |
| `uv run pytest tests/unit/catalog/test_catalog_seed_loader_report.py tests/unit/search/test_benchmark_provenance.py -q -p no:cacheprovider` | **32 passed** (`.gitattributes` and seed checks) |
| `uv run pytest tests/integration/test_returns_dataset.py -q -p no:cacheprovider` | **10 passed** (16.9 s) |
| `uv run pytest tests/integration/test_returns_import.py -q -p no:cacheprovider` (CLI changed) | **80 passed** (28.0 s) |
| `uv run python -m ecommerce_search.returns --help` | exit 0; commands `validate`, `import`, `verify` |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 509 files already formatted |
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |
| AC-14 M17 listing: `sha256sum -c` of the M18-02 `ac14-m17-untracked.sha256` | listing SHA-256 starts with `cf74568d`; 10 OK, exit 0. The 10 listed paths are `docs/m17-handoff.md` plus 9 files under `evals/results/m17/unsupported-claims/`; "9 files present" counted only that directory (reconciled at the checkpoint below). |

**Development notes:** on their first run, two new dataset tests failed because of mistakes
in the tests, which were then fixed:
- the dataset `notes` legitimately name `scenarios.json`;
- a 13th CSV part also triggers `too_many_files`.

Separately, the generator was changed to keep the generic `return_process` complaints off
suspected-abuse and insufficient-evidence returns, and the release was regenerated after that.

**Not run:**
- the full unit suite and the recursive isolation wrapper (the local timeout stays **FAILED**);
- the full integration suite;
- CI on these changes (not yet pushed when this table was recorded).

### Files changed by M18-03C (in the scoped M18-03C commit)

- **New:**
  - `scripts/generate_returns_dataset.py`
  - `data/seed/returns_v1/` (15 generated files)
  - `src/ecommerce_search/returns/verify.py`
  - `tests/returns_release.py`
  - `tests/unit/returns/test_returns_dataset.py`, `test_returns_fixture_privacy.py`,
    `test_returns_verify.py`
  - `tests/integration/test_returns_dataset.py`, renamed by the corrective commit to
    `tests/integration/test_returns_dataset_integration.py`. Command rows above that name the
    old path record what was run at the time.
- **Modified:**
  - `src/ecommerce_search/returns/cli.py` (adds `verify`)
  - `tests/unit/returns/test_returns_cli.py` (the command-set pin now includes `verify`)
  - `tests/unit/returns/test_returns_import_service.py` (the forbidden-name check covers
    `verify`)
  - `.gitattributes`
  - `docs/m18/handoff.md`, `docs/m18/test-matrix.md`

### Remaining gaps

- **ACs not closed.** AC-06 to AC-10, AC-13 and AC-14 have domain and fixture evidence. They
  still need M18-04, the full regression and CI. The M18-04 parts are:
  - API 413 and 429, and the temp-file check;
  - response schemas and the route inventory;
  - the API privacy surfaces.
- **Phase B memory** is still not measured. The release imports within seconds in the
  integration tests, but no memory or time measurement was made.
- **CLI choices** that the contract does not fix: the `verify` exit codes and its JSON shape.
- The local isolation-test timeout decision is still open.

### Review checkpoint (2026-10-26, one bounded pass before the M18-03C commit)

**1. Scenario coverage is generator intent, not measured diversity.**
- The 96 category × shape slots are **96 generated examples, not 96 distinct evidence
  patterns**.
- A one-off scratch count gave **48 distinct structural signatures** across them. It is not a
  test and not an independent assessment. The signature is the reason code, the number of base
  comments, the inspection conditions, the complaint topics, the review ratings, and the
  delay/damage event types; text content and product are ignored.
- Per category this is 3 to 6 signatures. Two cases are identical by construction:
  - **Insufficient evidence:** `consistent` and `missing_evidence` are identical (return, order
    and shipment only, `NOT_PROVIDED`; interpretation 1).
  - **Change of mind:** `conflicting` differs from `consistent` only in comment text.
- Scenario diversity has **not** been independently assessed.
- `generator_intent` is what the generator meant to build:
  - it is not a label, not human-reviewed and not an independent evaluation;
  - "suspected abuse (investigation only)" is **not a misconduct label** and says nothing about
    any customer;
  - no generated record carries an intent.
- No data was regenerated to add coverage.

**2. Trust boundary of `returns verify`.**
- **What it compares:** the release's recomputed `record_sha256` values with the
  `record_sha256` **column stored with each row** (keyed by `source_record_id`). It does
  **not** recompute hashes from the stored business-field values.
- **Contract check:**
  - Frozen §4.7 says verify "computes `records_sha256` over the stored rows".
  - `records_sha256` is defined over the lines `entity \t source_record_id \t record_sha256`,
    so the stored `record_sha256` is its defined input.
  - Field-level recomputation is not required. It would also be impossible for the five
    free-text entities: stored text is redacted, while `record_sha256` covers the raw text,
    which sits only in the restricted contents that neither the API role nor the importer can
    read (§2, §5.1).
  - **No contract gap was found and nothing was weakened.**
- **Consequence:** verify proves that the stored record set and its recorded hashes equal the
  release. It does not prove that the stored business columns still correspond to those
  hashes. Protections against such drift:
  - the append-only triggers (UPDATE/DELETE/TRUNCATE blocked);
  - the importer computes `record_sha256` and writes it with the row in one transaction;
  - the grants (no UPDATE for either role).

  A schema owner bypassing the triggers would not be detected by verify. A field-level check
  would be new semantics and needs owner approval.

**3. Fixture-scan exemptions narrowed (fix).**
- **Finding:** the earlier filter skipped every value under any key matching
  `.*sha256|unit_price|files`, whatever its form. That included the whole manifest `files`
  list (file names and entities), and object keys were never scanned. This was broader than
  hash/money.
- **Fix:**
  - an exemption needs both the named field and the form (64-hex hash, canonical amount);
  - keys are scanned;
  - a new test, `test_the_exemptions_are_narrow_in_field_and_form`, shows that
    personal-data-like text under exempt names, hash or money forms in other fields, and keys
    are all still flagged.
- The full release still passes the scan.

**4. M17 listing reconciled.**
- The original M18-02 listing (`ac14-m17-untracked.sha256`, its own SHA-256 starting with
  `cf74568d`) names exactly 10 paths:
  - `docs/m17-handoff.md`;
  - 3 files each under `abe-v1-live-002`, `-003` and `-004` in
    `evals/results/m17/unsupported-claims/`.
- All 10 exist, and `sha256sum -c` reports 10 OK. "9 files present" counted only the
  directory.
- No new baseline was made.

**5. Wording corrected.**
- Test and generator text no longer calls the release "committed" before the commit.
- The 15% band is labelled as an M18-03C test choice.
- Generated counts are kept apart from scenario diversity (item 1).
- No generator, schema, CLI or data semantics changed: `--check` still reproduces
  `records_sha256` `497cebbd…63ca`.

**Checkpoint commands and actual results:**

| Command | Result |
|---|---|
| `uv run pytest tests/unit/returns/test_returns_fixture_privacy.py -q -p no:cacheprovider` | first runs: 2 failed, then 1 failed (bugs in the new test helper and in one test input; fixed). Final: **17 passed** |
| `uv run python scripts/generate_returns_dataset.py --check` | exit 0, same `records_sha256` |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` | **511 passed** (adds the exemption-boundary test) |
| `uv run pytest tests/integration/test_returns_dataset.py -q -p no:cacheprovider` | **10 passed** (renames only) |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 509 files already formatted |
| AC-14 protected-path diff; M17 listing | exit 0; 10 OK, exit 0 |
| Dev DB `alembic_version`; scratch databases; `m18t_*` roles | `0004`; 0; 0 |

The full unit suite, the isolation wrapper and the full integration suite were not run. The
local isolation timeout stays **FAILED**, separate from CI.

### CI failure on `4821bbd` and corrective fix (2026-10-26)

- **Failure (reported by the owner; this session cannot open the private run):** CI on
  `4821bbd` failed during collection with pytest's "import file mismatch".
  - `tests/integration/test_returns_dataset.py` and `tests/unit/returns/test_returns_dataset.py`
    have the same module basename.
  - The test directories have no `__init__.py`, and pytest's default import mode puts both
    under the name `test_returns_dataset`.
  - The hermetic unit job collects the whole `tests/` tree and filters by marker only, so it
    collects both files.
- **Why the local checks missed it:** M18-03C ran the two files in separate pytest
  invocations, never in one combined collection.
- **Fix:** `git mv` of the integration file to
  `tests/integration/test_returns_dataset_integration.py`, plus the one reference to it in the
  `tests/unit/returns/test_returns_verify.py` docstring.
  - Unchanged: test assertions, the dataset, application code, the pytest import mode, the
    package structure and the CI selection.
  - A stale, git-ignored `.pyc` of the old name was deleted from
    `tests/integration/__pycache__`.

| Command | Result |
|---|---|
| Hermetic collection exactly as the CI job runs it (`POSTGRES_*`, `DB_*`, `APP_ENV*`, `LOG_LEVEL*`, `EMBEDDING_*`, `SEARCH_*` and `RERANKER_*` unset; `ECOMMERCE_SEARCH_HERMETIC_CHILD=1 POSTGRES_PASSWORD=""`; `uv run --frozen python -m pytest -m "not integration and not real_model" -q -p no:cacheprovider -ra --collect-only`), **before** the rename | reproduced: 1 error, `import file mismatch` on `tests/unit/returns/test_returns_dataset.py` (5667/6714 collected) |
| Same command, **after** the rename | exit 0: 5695/6742 tests collected (1047 deselected), no errors |
| `uv run --frozen python -m pytest tests/integration -m "integration and not real_model" -q -p no:cacheprovider --collect-only` (with a placeholder `POSTGRES_PASSWORD`; collection only, no connection) | 1029/1047 collected (18 deselected), no errors |
| Duplicate `test_*.py` basenames anywhere under `tests/` | none |
| `uv run pytest tests/integration/test_returns_dataset_integration.py -q -p no:cacheprovider` | **10 passed** |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` | **511 passed** |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 509 files already formatted |

The Windows outer isolation wrapper was not launched; its local timeout stays **FAILED**. Local
results are not CI results.

### Next task

1. CI on the corrective SHA (the owner checks it; the private run is not accessible here).
2. With separate approval: **M18-04 Protected Backend API**.

## State at the end of the M18-03B session (2026-10-26)

- **HEAD at start:** `797c6e40461ea35af242c2e4dc3fd65cf3186e81` (M18-03A), as expected.
  M18-03A is pushed, and CI run 40215398031 succeeded on that SHA. The 2026-10-25 section
  below said "not pushed"; that statement was stale and is corrected there.
- **M18-03B (Atomic Import & CLI): implemented and focused-verified.** After the review
  checkpoint, the owner authorized one scoped commit and a normal push for CI. The SHA and
  the CI result are reported in the session summary, not in this file. M18-03C has not
  started. M18-03 as a whole is not
  complete, and M18 is not accepted.
- Unrelated working-tree items are unchanged: `docs/m12-diagnostic-tooling.md` (modified),
  untracked `.claude/`, `docs/m17-handoff.md` and `evals/results/m17/unsupported-claims/`.
- **Not done in this session:**
  - no dev or staging migrations, writes or grant changes; the development database was read
    only and is still at `0004`;
  - no deployment, provider calls, AWS actions, installs, commits or pushes;
  - Docker Desktop (already installed, user-local) and `docker compose up -d db` were started
    for the scratch-database tests.
- The local full-unit-suite isolation timeout from M18-02 is still **FAILED**. It was not
  rerun, and its timeout and skips were not changed. This is separate from the successful CI
  evidence (runs 40103962367 and 40215398031), which does not cover the Windows outer wrapper.

### What M18-03B implements

**`returns/service.py`** — the domain import service. Public: `validate_import`,
`commit_import`, `ImportReport`, `ImportRoleError`, `ImportStorageError`.

- **Phase A, steps 1–4:** the existing `validate_upload`, unchanged. A run with errors stops
  there, with no database access.
- **Phase A, step 5:** catalog references, in a short read-only transaction.
  - `unknown_product`: a `product_id` of an order item, case, review or complaint is not in
    `products`.
  - `snapshot_mismatch`: no `raw_catalog_records` row of that product, in that catalog
    dataset version, has that `raw_sha256`.
  - It reads only the importer's column-level catalog grants.
- **Phase B, in order:**
  1. Dataset descriptor: `dataset_mismatch`, one error per differing field, no values.
  2. Stored natural keys: `unchanged` versus `conflict_existing`.
  3. `bundle_sha256` already stored: `already_imported`, with the existing `batch_id`.
  4. Catalog references again.
  5. `check_stored`: the §3.1 rules over the bundle together with every stored record of the
     dataset version.
- **Validate mode:** Phase B in a `READ ONLY`, `REPEATABLE READ` transaction, without the
  lock. It never writes.
- **Commit mode:** one `READ COMMITTED` transaction.
  1. `require_importer_role` runs first.
  2. Then `pg_advisory_xact_lock` with a SHA-256-derived key of
     `returns-import:<dataset_id>:<dataset_version>`, the existing lock-key pattern.
  3. Every Phase B check re-runs under the lock.
  4. Only for `imported` does it write: the dataset row (first import only), the batch, the
     metadata and contents of every file of the request, and **new rows only**. Contents are
     inserted without RETURNING.
- **Failure handling:** a database refusal raises `ImportStorageError` with a fixed message,
  no cause chain and no library text. The transaction rolls back.
- **Test hook:** `checkpoint(name, conn)`, used for fault injection and to pause a commit
  under the lock.
- **Role check** (`require_importer_role`) refuses:
  - a superuser or a role with `BYPASSRLS`;
  - an owner or a member of an M18 table owner;
  - any column-level SELECT on raw contents;
  - a role missing INSERT on any M18 table, or SELECT on any M18 table except raw contents;
  - UPDATE, DELETE or TRUNCATE on any M18 or catalog table;
  - INSERT on a catalog table.

**`returns/validation.py`** (refactor; Phase A behaviour unchanged, 390 M18-03A tests pass)
- `_Structure` takes an optional stored-record view (`check_stored`).
- With every record visible, it adds:
  - `unknown_reference` for every reference field and evidence target;
  - stored shipments (`duplicate_shipment`);
  - the stored non-cancelled quantity (cancelled returns excluded, repeated returns not
    counted twice);
  - inspections on stored returns that were not received;
  - time order against stored parents;
  - the order + product complaint rule (§10);
  - every evidence-to-product relation, including through shipments and delivery events.
- `record_error` and `REFERENCES` are factored out.

**CLI** (`returns/cli.py`, `returns/__main__.py`):
- `python -m ecommerce_search.returns validate|import <bundle.json | csv-dir>`, with no other
  flags, per §4.1.
- **Input:**
  - A file argument becomes part `bundle.json`; its local name is not used.
  - A directory gives one part per regular file, by file name. Subdirectories are refused.
  - Each file is read with a cap of 5 MiB + 1 byte.
- **Output:** the `ImportReport` as JSON on stdout.
  - Exit 0: `valid`, `imported`, `already_imported` or `no_new_records`.
  - Exit 1: `invalid`.
  - Exit 2: a usage, input, settings, role or database error, with a fixed message.
- Messages never contain paths, values, credentials or connection strings.
- `verify` (§4.1/§4.7) is **not** implemented; see the gaps below.

**Settings** (owner-approved narrow change, 2026-10-26): `RETURNS_IMPORTER_LABEL`
(`returns_importer_label`).
- It is optional. An empty value means unset.
- A set value must be 1–64 characters of `[A-Za-z0-9._:-]`, starting with a letter or digit.
- It is refused if any `redaction-1` pattern matches (`returns/labels.py`). The error names the
  field only.
- `.env.example` documents it, and the unit-test isolation prefix list gains `RETURNS_`.
- No other `RETURNS_*` setting was added (M18-04).

### Configuration decision (owner choice, 2026-10-26)

- The CLI connects with the **standard POSTGRES_* settings**. A CLI import must run with the
  scoped `returns_importer` credentials in its environment, as the deploy one-off container
  does (plan D12).
- `import` requires `RETURNS_IMPORTER_LABEL`.
  - A missing or invalid label fails before input is read or a connection is made.
  - There is no silent default.
  - The label is supplied provenance, not an authenticated personal identity.
- **There is no fallback to other credentials.** With admin credentials (superuser) or the API
  role, `import` stops before any write with `ImportRoleError` (`superuser` /
  `missing_privilege`). This is tested through the CLI.
- `validate` uses the same POSTGRES_* connection in a read-only transaction.
- `RETURNS_IMPORT_COMMIT_ENABLED` and the `RETURNS_IMPORTER_DB_*` engine remain API settings
  for M18-04 and were not added. The main app's credentials and grants are unchanged.

### Interpretations made in M18-03B (frozen text unchanged; owner accepted these where compatible)

1. **Phase names:**
   - step 5 is `catalog_references`, and Phase B is `stored_data`;
   - a phase is listed when all of its checks ran, even if it found errors (the same rule as
     M18-03A);
   - Phase B is not listed after `dataset_mismatch` or `already_imported`.
2. **Stopping:** every phase that reports errors stops the run.
   - A Phase A error means no database access, and a step-5 error means Phase B is skipped.
   - `error_count_is_lower_bound` is then true.
   - Errors from a completed Phase B give a lower bound of false.
3. **Report values:**
   - `mode` is `validate` or `commit`.
   - `counts[entity].new` and `.unchanged` are null until the natural-key comparison has run.
   - On an `invalid` Phase B, conflicting records count as neither.
4. **Error fields:**
   - `snapshot_mismatch` is reported on field `catalog_raw_sha256`. A wrong catalog dataset ID
     or version also gives `snapshot_mismatch`.
   - `dataset_mismatch` is a dataset-level error (`entity` null) on `bundle.json` or
     `manifest.json`, with pointer `/dataset/<field>`.
5. **Evidence via a complaint with only `order_id`** (owner condition): it relates to the case
   only when the case's product is actually among the referenced order's items, stored or in
   the bundle. Merely having an `order_id` is not enough.
6. **`already_imported`:** it is decided after the descriptor check. Its counts are the
   natural-key comparison (all `unchanged` for the same bytes).

### Commands run and actual results (2026-10-26)

| Command | Result |
|---|---|
| `git rev-parse HEAD`; `git status --short` | `797c6e40461e…`; only the four unrelated items |
| `uv run pytest tests/unit/returns -q` (after the refactor, before the new tests) | **390 passed** |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` (final) | **455 passed** (390 + 48 service + 17 CLI) |
| `uv run pytest tests/unit/test_settings.py tests/unit/test_compose_config.py tests/unit/test_engine.py tests/unit/catalog/test_catalog_models_metadata.py -q` | **223 passed** |
| `uv run pytest tests/integration/test_returns_import.py -q` (first run, `-x`) | 1 failed, 7 passed: a test bug (a comment bundle without its parents). The failure output also printed the per-module test roles' generated passwords through the fixture's dataclass repr. Those were throwaway `m18t_*` roles, dropped at teardown, on a local scratch server. **Fix:** the field is now `repr=False`. |
| `uv run pytest tests/integration/test_returns_import.py -q` (final) | **78 passed** (36.9 s) |
| `uv run pytest tests/integration/test_returns_schema.py tests/integration/test_returns_grants.py -q` | **175 passed** |
| `uv run python -m ecommerce_search.returns --help` | exit 0; commands `validate`, `import` only |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 502 files already formatted |
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |
| AC-14 M17 listing: `sha256sum -c` of the M18-02 `ac14-m17-untracked.sha256` | listing SHA-256 starts with `cf74568d`; 10 OK, exit 0; 9 files under `unsupported-claims/` |
| Development database (read only) / leftovers | `alembic_version` `0004` at start and end; 0 scratch databases and 0 `m18t_*` roles left |

**Not run:**
- the full unit suite and the recursive isolation wrapper (the M18-02 local timeout stays
  **FAILED**);
- the full integration suite;
- CI on these changes (not yet pushed when this table was recorded).

### Files changed by M18-03B (in the scoped M18-03B commit)

- **New:**
  - `src/ecommerce_search/returns/service.py`, `cli.py`, `__main__.py`, `labels.py`
  - `tests/unit/returns/test_returns_import_service.py`, `tests/unit/returns/test_returns_cli.py`
  - `tests/integration/test_returns_import.py`
- **Modified:**
  - `src/ecommerce_search/returns/validation.py`
  - `src/ecommerce_search/config/settings.py` (`returns_importer_label`)
  - `tests/unit/conftest.py` (isolates `RETURNS_*`)
  - `.env.example`
  - `docs/m18/handoff.md`, `docs/m18/test-matrix.md`

### Review checkpoint (2026-10-26, one bounded pass before the M18-03B commit)

**1. AC-13 (5) service surface.**
- `service.__all__` now declares the whole public surface: `validate_import`, `commit_import`
  (the only writer), `ImportReport`, `EntityCounts`, `ImportRoleError`, `ImportStorageError`,
  `import_lock_key` and `require_importer_role`.
- New unit tests pin it exactly:
  - `__all__`, and the public callables defined in the module, both equal the literal set;
  - the CLI reaches the domain only through `validate_import` / `commit_import`, the report
    and the two errors;
  - no public callable in any `returns` module has a refund, ban, sanction, approval,
    rejection, fraud, penalty, suspension or responsibility name.

**2. Test-credential disclosure (incident record; no secret values reproduced here).**
- **What happened:** during the first M18-03B integration run, a failing assertion made
  pytest print the module fixture's dataclass repr. The repr included the generated
  passwords of the two per-module test LOGIN roles.
- **Scope:**
  - The output went only to this local session's terminal. Nothing was committed, logged to
    a file or sent to CI.
  - The admin engine URL in the same repr was masked by SQLAlchemy (`***`).
  - Only the two temporary roles' passwords were exposed. They were generated with
    `secrets.token_urlsafe(24)` per module run, on the local Docker server.
- **Verification (2026-10-26):** by name, both exposed roles are absent from `pg_roles` (0
  rows), and their scratch database is absent (0 rows). No `m18t_*` role remains.
- **Remediation:**
  - The password field is now `field(repr=False)`.
  - New regression tests check that:
    - the fixture's repr/str and every engine repr show neither role passwords nor the
      admin password, and that SQLAlchemy masks URLs;
    - a failed connection gives an `ImportStorageError` whose formatted traceback, repr and
      str carry no password, connection string or role name, with no cause and the context
      suppressed;
    - the CLI's stdout and stderr for the same failure carry none of these either.

**3. Role checks, rollback, and lock/recheck.** No contradiction with contracts §2, §4.3 or
§10 was found.
- `require_importer_role` runs inside the commit transaction before the lock, and is stricter
  than the §2 list but grants nothing new. Its refusal is raised unwrapped, and the
  transaction rolls back.
- `pg_advisory_xact_lock` is taken before any Phase B read. Every recheck runs after it, in
  the same READ COMMITTED transaction, as does the dataset-row creation. These are the
  descriptor, bundle, natural keys, catalog, references, time order, shipments, quantity and
  evidence checks.
- A non-`valid` outcome rolls back explicitly, and any exception rolls back through the
  context manager. Both are covered by the checkpoint fault-injection, T1–T8 and role tests.
- Contract §4.3 calls step 5 "no transaction": the step-5 catalog read runs in its own short
  read-only transaction, which is never the Phase B transaction and never holds the lock. This
  is read as consistent with the contract.

**4. Scope records.**
- `returns verify` is **assigned to M18-03C** (owner direction, 2026-10-26). AC-08 stays
  **partial** until then.
- The Phase B memory limit stays open (below).

**Checkpoint commands and actual results:**

| Command | Result |
|---|---|
| Read-only check of `pg_roles` for the two exposed role names, any `m18t_*` role, and the scratch database | 0 / 0 / 0 |
| `uv run pytest tests/unit/returns -q -p no:cacheprovider` | **458 passed** (adds 3 surface tests) |
| `uv run pytest tests/integration/test_returns_import.py -q -p no:cacheprovider` | **80 passed** (adds 2 diagnostics tests), 40.9 s |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 502 files already formatted |

The full unit suite, the isolation wrapper and the full integration suite were not run. The
local Windows isolation timeout stays **FAILED**.

### Remaining gaps

- **`returns verify`** (stored `records_sha256`, missing and extra counts) is **assigned to
  M18-03C**. AC-08 is **partial** until then. The `records_sha256` equality itself is tested
  directly against stored rows.
- **Phase B memory (unmeasured):** Phase B loads every stored record of the dataset version
  (key columns only) into memory. This is expected to be fine at demo scale (about 5,300
  rows), but memory and time were **not measured**, at that scale or for larger dataset
  versions.
- **Not covered here:**
  - API single-flight (429), 413 and the temp-file check (M18-04);
  - the §5.3 fixture scan (M18-03C);
  - the generator and dataset (M18-03C).
- **CLI choices** that the contract does not fix: the exit codes, and JSON on stdout.

### Next task

1. The scoped M18-03B commit and push (owner-authorized at the review checkpoint), then CI on
   the pushed SHA.
2. With separate approval: **M18-03C**, the generator, the committed dataset, the §5.3 fixture
   scan, and `returns verify` (assigned there).
3. The local isolation-test timeout decision is still open.

## State at the end of the M18-03A session (2026-10-25)

- **HEAD at start:** `5a04ece` (M18-02 implementation `797bc75`), as expected.
- **Commit:** M18-03A is commit `797c6e4` on `main`, on top of `5a04ece`. *Correction
  (2026-10-26): it was pushed, and CI run 40215398031 succeeded on that SHA. The earlier
  "not pushed" text was stale.*
- **M18-03A (Validation & Privacy): implemented, focused tests pass.** M18-03B and M18-03C
  have not started. M18-03 as a whole is **not** complete, and M18 is not accepted.
- Unrelated working-tree items are unchanged: `docs/m12-diagnostic-tooling.md` (modified),
  untracked `.claude/`, `docs/m17-handoff.md` and `evals/results/m17/unsupported-claims/`.
- No database, staging, provider, AWS, install, migration, commit or push actions were taken.
- The local full-unit-suite isolation timeout from M18-02 is still recorded as **FAILED**. It
  was not rerun, and the test, its timeout and its skips were not changed.

### What M18-03A implements (`src/ecommerce_search/returns/`, in memory, no database)

- **`redaction.py`** (`redaction-1`):
  - Kinds run in order: email (reuses `assistant/privacy.py` `EMAIL`), upi, number (reuses
    `LONG_NUMBER`), and pin (a standalone 6-digit run). Each kind uses a fixed token.
  - Passes repeat until no pattern matches, which gives G1–G3. Returns per-kind counts.
  - `matches_any()` drives `looks_personal`.
  - `safe_preview()` redacts, truncates to 80 code points, then redacts again.
  - The M16 screen is not changed. This is pattern redaction, not comprehensive PII detection.
- **`errors.py`:**
  - The `RowError` model, with one fixed message template per code (no values).
  - Safe key echo: a contract name, or `^[a-z_]{1,40}$`, otherwise `<unrecognized>`.
  - JSON pointers built from safe keys and indices; `unexpected part #n` for parts with
    unrecognized names.
  - A deterministic collector: sorts, stops at 1,000 errors, returns 200.
- **`bundle.py`** (Phase A steps 1–2):
  - Part-name allowlist and format choice; §4.4 byte, file, row, column and field limits.
  - Strict UTF-8; a BOM is tolerated for CSV only.
  - JSON: duplicate keys, NaN and depth > 4 are rejected.
  - A hand-written strict RFC 4180 CSV reader (LF/CRLF, bare CR and stray quotes rejected,
    line numbers kept) and exact headers.
  - Manifest validation, including listing, checksum and row-count checks against the parts.
  - Computes `bundle_sha256`. File bytes are kept in memory only.
- **`validation.py`** (Phase A steps 3–4, `validate_upload(parts, checked_at=…)`):
  - Field rules through the existing M18-02 models. CSV integers and booleans go through
    `canonical.py`. Pydantic text is never used.
  - `looks_personal` screening of record IDs, `carrier_ref` and dataset text.
  - Free text is redacted with per-kind counts. `record_sha256` still covers the raw text.
- **In-bundle structural rules** (§3.1):
  - natural-key duplicates;
  - one shipment per order;
  - the non-cancelled quantity rule (with the item's quantity, or the maximum of 10 when the
    item is not in the bundle);
  - cross-record time order, and nothing later than `checked_at`;
  - an inspection only on a `received`/`closed` return;
  - review, complaint and evidence product consistency.
- **What the result contains:**
  - `PhaseAReport`: `phases_completed` (`envelope`, `parsing`, `fields`, `bundle_structure`),
    a fixed `deferred_checks` list, the counts, the truncation flags and the errors.
  - It is not an `ImportReport`. It has no status and no new/unchanged counts.
  - Validated records and the dataset descriptor are returned only when there are no errors.
- **What it cannot show:** a report with no errors does not mean the upload is valid, or that a
  commit will succeed. Not run here (`deferred_checks`, M18-03B):
  - catalog references (step 5: `unknown_product`, `snapshot_mismatch`);
  - dataset descriptor equality;
  - `already_imported`;
  - stored natural keys (`conflict_existing`);
  - every reference not found inside the bundle;
  - stored time, shipment, quantity and evidence rules.
- **Conflicting evidence:** the §3.1 examples are accepted with zero errors.

### Interpretations made in M18-03A (owner-approved 2026-10-25 with conditions; frozen text unchanged)

**Owner conditions:**
- the interpretations must be compatible with the frozen contracts;
- `phases_completed` lists only completed phases;
- shipment and delivery-event product linkage stays explicitly deferred;
- contract-name echo uses a fixed allowlist;
- lower-bound and truncation semantics stay distinct.

The review result is below.

1. **Phase names** for `phases_completed`: `envelope`, `parsing`, `fields`, `bundle_structure`.
   Step 5 is reported only as the deferred check `catalog_references`.
2. **Code mapping:**
   - An inspection on a return that is not `received`/`closed` → `inconsistent_reference`
     (`return_id`).
   - The §10 mapping for a status-forbidden field (`format`) is kept as M18-02 implemented it.
     §10 asks M18-03 to confirm it; this is that request.
3. **Parse-level codes:**
   - JSON syntax errors, duplicate keys and depth > 4 → `format`. A non-object top level →
     `type`.
   - A duplicate manifest file name → `duplicate_in_bundle`. An empty manifest `files` →
     `required`.
   - U+0000 and lone surrogates → `encoding`, because PostgreSQL text cannot store them.
4. **Import-time bound:** applies to every record timestamp except `promised_delivery_by`,
   which is a promise and may be in the future.
5. **Echo rule:** contract field names are always echoed, even when they fall outside
   `^[a-z_]{1,40}$` (for example `catalog_raw_sha256`). Other keys follow the regex.
6. **`RowError`:**
   - `entity` is null for bundle-, manifest- and dataset-level errors.
   - `value_preview` is set only for `enum`/`format`/`type`/`out_of_range` on non-sensitive
     string fields. It is never set for `looks_personal`, free text, actor references,
     dataset text or manifest entries.
7. **Ordering:**
   - Inside `bundle.json`, entity (alphabetical) is a tie-break between file and row. Line
     and pointer break any remaining ties.
   - Bundle-level `too_many_files` and `bundle_too_large` are reported on the anchor file
     (`bundle.json` or `manifest.json`).
8. **Lower bound:** `error_count_is_lower_bound` is also true when a file or row was excluded
   from later steps after an error. Any step-3 row error therefore makes it true.
9. **Set consistency:** manifest errors and set-consistency errors (`missing_file`,
   `unexpected_file`, `checksum_mismatch`) stop the run before any CSV is parsed. A CSV that
   fails to parse, or whose row count differs, is excluded, and the other files continue.
10. **Evidence linked through a shipment or delivery event** is never rejected inside the
    bundle. The order's stored items may contain the product, so the check is deferred.

### Commit-checkpoint review (2026-10-25; one bounded pass over the four modules and tests)

- **Frozen contracts:** no contradiction found. The interpretations fill gaps the contracts
  leave open:
  - nullable `entity`;
  - the entity tie-break inside the single `bundle.json`;
  - extra lower-bound cases (§4.8 says when the flag is true, not "only when");
  - redacting again after truncation.
- **`phases_completed`:** a phase is appended only after all of its checks ran.
  - `envelope` always completes.
  - `parsing` is omitted after a bundle-level stop.
  - `fields` and `bundle_structure` are appended only when they ran. `bundle_structure` is
    skipped when the 1,000-error collector is already full.
- **Deferred linkage:** shipment and delivery-event evidence is never decided in-bundle
  (`_evidence_product` returns None). **Fix:** the `DEFERRED_CHECKS` comment on
  `stored_evidence_relations` now says this explicitly.
- **Echo allowlist:** `KNOWN_KEYS` is a frozenset built at import from the contract models plus
  the envelope keys. **Fix:** a new test pins it to the literal sets in
  `tests/returns_contract_sets.py`, so any drift fails.
- **Lower bound versus truncation:** these are computed separately.
  - `errors_truncated` means `error_count > 200`.
  - `error_count_is_lower_bound` means the collector stopped, or a phase, file or row was
    skipped after errors.
  - The 201 and 1,000 cases test truncated = true with lower bound = false.
- **Privacy paths:**
  - Messages are fixed templates.
  - Previews are never produced for free text, actor references, dataset text, manifest
    entries or `looks_personal`.
  - JSON errors carry only line numbers.
  - No library exception text is kept, and nothing is logged.
- **Bounded parsing:**
  - The byte caps are checked before decoding.
  - JSON depth is found by an iterative walk, with `RecursionError` and over-long integers
    caught.
  - The CSV reader is linear (`find` and regex), and it stops after 5,001 rows.
- **Redaction:**
  - It terminates: every replacement removes at least one `@` or digit, and tokens contain
    neither.
  - It is idempotent: output has no matches, so another pass changes nothing.

- **Fixture safety (§5.3):** the redaction test used a realistic name-and-address string.
  **Fix:** it is now `Firstname Lastname, 12 Example Road`. The redaction tests were rerun:
  18 passed. The remaining personal-data-like test strings are allowlist forms (`example.com`,
  subscriber numbers starting with 0, `0xxxxx` PINs, `okexample` UPI handles).

### Commands run and actual results (2026-10-25)

| Command | Result |
|---|---|
| `git rev-parse --short HEAD`; `git status --short` | `5a04ece`; only the four pre-existing unrelated items |
| `uv run pytest tests/unit/returns -q` (before review) | **389 passed** (212 M18-02 + 177 new), 3.06 s |
| `uv run pytest tests/unit/returns -q` (after the review fixes) | **390 passed** (adds the `KNOWN_KEYS` pin), 3.20 s |
| `uv run ruff check .` (final) | All checks passed |
| `uv run ruff format --check .` (final) | 495 files already formatted |
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |

Not run:
- The full unit suite, the nested isolation test, and the integration suite (none touches the
  database).
- (Resolved at the commit checkpoint.) **AC-14 M17 listing:** the original M18-02 listing
  was recovered from that session's scratchpad (`ac14-m17-untracked.sha256`).
  - Its own SHA-256 equals the recorded `cf74568d…41adf7`.
  - `sha256sum -c` gave exit 0, with all 10 files OK.
  - `evals/results/m17/unsupported-claims/` holds exactly the 9 listed files.
  - No new baseline was created.

### Files changed by M18-03A (committed as `797c6e4`)

- New source: `src/ecommerce_search/returns/redaction.py`, `errors.py`, `bundle.py`,
  `validation.py`
- New tests:
  - `tests/returns_uploads.py` (synthetic upload builders);
  - `tests/unit/returns/test_returns_redaction.py`, `test_returns_bundle_parsing.py`,
    `test_returns_validation.py`, `test_returns_error_privacy.py`.
- Docs: `docs/m18/handoff.md`, `docs/m18/test-matrix.md`

### Next task (as recorded at the end of M18-03A; superseded by the M18-03B section above)

1. Owner decision on pushing the local M18-03A commit (since pushed; CI 40215398031 green).
2. With separate approval: **M18-03B**, the domain import service.
   - Phase A step 5 (catalog references), then Phase B: validate as READ ONLY / REPEATABLE
     READ; commit as READ COMMITTED under the advisory lock, re-running every check in
     `DEFERRED_CHECKS`.
   - The `ImportReport` (status, new/unchanged counts), idempotency (§4.5), and the
     AC-07/AC-08 and T1–T8 integration tests.
   - It must reuse `validate_upload` and must not treat a Phase A result as a validity
     guarantee.
3. The local isolation-test timeout decision is still open (see below).

## State at the end of M18-02 (2026-10-24)

- **Commits:** M18-02 is `797bc75` (`feat(m18): add return data storage and contracts`), on
  top of `0d9465a` (`docs(m18): freeze return data foundation design`, parent `bf67d11`). Both
  were pushed to `origin/main` (fast-forward `bf67d11..797bc75`) for normal CI only. This
  handoff update is a docs-only follow-up commit.
- **CI on `797bc75`: run 40103962367, all 4 jobs succeeded** (details below).
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

## CI results for `797bc75` (run 40103962367, push event, conclusion success)

| Job | Result |
|---|---|
| backend lint + hermetic unit suite | success. `ruff check .`: all checks passed; `ruff format --check .`: 485 files already formatted. The hermetic step clears the `POSTGRES_*`, `DB_*`, `APP_ENV*`, `LOG_LEVEL*`, `EMBEDDING_*`, `SEARCH_*` and `RERANKER_*` variables, sets `ECOMMERCE_SEARCH_HERMETIC_CHILD=1` and `POSTGRES_PASSWORD=""`, and runs `uv run --frozen python -m pytest -m "not integration and not real_model" -q -p no:cacheprovider -ra`: **5392 passed, 4 skipped, 957 deselected** (10 min 49 s). Skips: 3 in `test_model_fetch_profiles.py` (no `_winapi` on Linux) and 1 in `test_unit_isolation.py` ("already inside the hermetic child"). |
| database integration suite (pgvector) | success: `pytest tests/integration -m "integration and not real_model"`, **939 passed, 18 deselected** (4 min 40 s) |
| frontend typecheck, unit tests, build | success |
| fixture Playwright against the FastAPI-served build | success |

**Limits of this evidence:**
- CI runs the isolation test's child directly; the recursive wrapper test skips there by design.
- A green CI run does **not** mean the Windows outer isolation wrapper passed, and does not
  fix its 1,200-second timeout. The local outer result stays **FAILED** (5391 passed, 4
  skipped, 1 isolation subprocess timeout).
- CI shows that the same hermetic tests pass on Linux.

## Next task as recorded at the end of M18-02 (item 2 superseded by M18-03A above)

1. **Owner decision on the local isolation-test timeout.** It is a pre-existing 1,200-second
   budget, exceeded on this machine. Options:
   - rerun the full unit suite on an awake, idle machine, or in CI (the CI backend job is the
     isolation child itself);
   - approve a separate change to the budget.

   No change was made.
2. With owner approval: **M18-03 Ingestion, Privacy & Fixtures**. The mandatory importer
   checks and the error-code mapping are in `contracts.md` §10.
