# M18 — Return Data Foundation: plan

Status: **FROZEN — M18-01 design approved by the owner on 2026-10-24** (revision 2, which
resolves review items R1–R6, with the owner qualifications in §8). Changes after the freeze
need owner approval. `docs/MASTER_PLAN.md` stays authoritative. Of the edits in §7, only
C1–C3 have been applied (docs only, uncommitted).

Companion documents (no duplication between them):

- `docs/m18/contracts.md` — data, import, privacy, access, API and UI contracts; dataset design.
- `docs/m18/test-matrix.md` — AC-01–AC-19 mapped to tests.
- `docs/m18/handoff.md` — current state and next task.
- `docs/m18/release.md` — written in M18-07 (release, migration, recovery runbook).

## 1. Goal and boundary

A usable Return Data Foundation: storage, CSV/JSON ingestion, redaction, provenance, a protected
backend API, a minimal retailer UI, tests, and deployment to the existing EC2 staging stack.

Milestone text (MASTER_PLAN §28, Milestone 18): typed, versioned contracts and storage for orders
and order items; returns and return reason codes; customer-written return comments; reviews and
support complaints; delivery/fulfilment signals; product/SKU linkage; listing versions; correction
cases. PII redaction, provenance fields, validation, CSV/JSON ingestion, a separate linked
dataset with every synthetic record marked, raw evidence preserved separately from normalized
values.

Out of scope (owning milestone): reason classification, responsible-area prediction, clustering
(M19); hypotheses and evidence retrieval (M20); proposals, approval queue, human decisions,
corrective actions, `/reviews/*` (M21); outcomes and rates (M22); real retailer data; any refund,
sanction, ban, fraud or responsibility inference.

## 2. Reuse decisions (REUSE / EXTEND / CREATE)

| Need | Decision | Reason |
|---|---|---|
| Products, SKU link | REUSE `products` by `product_id` | Catalog v1 is the only product source. SKU = `product_id` (1:1); catalog has no variants (D3). |
| Listing snapshot | REUSE `raw_catalog_records` (exact line + SHA-256, `source_key` = `product_id`) through a typed snapshot reference | Real snapshot, no copy, no invented history. M13 `listing_versions` is seller-submitted listings with no product link and requires `seller_id` (16/240 products have none) (D4). |
| Dataset provenance | EXTEND the `catalog_datasets` pattern into `return_datasets` | Same fields and rules; separate table so return data never mixes with catalog datasets. |
| Append-only raw storage | REUSE the 0005/0006 trigger + CHECK + contract-sync-test pattern | Proven in M13/M14. |
| Intake limits | REUSE the `normalization/intake.py` approach (strict UTF-8, byte caps, typed reasons) | Consistent errors. |
| PII screening | EXTEND `assistant/privacy.py` patterns into a versioned redactor `returns/redaction.py` | Existing code detects only; M18 needs replacement and spans. The M16 screen is left unchanged. |
| Logging, request IDs | REUSE `api/observability.py` | Logs carry IDs and counts, never text. |
| Secrets, flags | REUSE `Settings` + `SecretStr` | Tokens, importer credentials. |
| Ingestion service | CREATE `src/ecommerce_search/returns/` (MASTER_PLAN §24 path) | No existing service handles multi-entity linked imports; the catalog ingest is single-file. |
| API | CREATE `api/returns.py` router; REUSE dependencies and FastAPI's `{"detail": ...}` error envelope, extended with a structured `code` | New domain. |
| Frontend | EXTEND the `App.tsx` tab shell with `domains/returns`; EXTEND `shared/http/client.ts` (GET, `Authorization` header, multipart) | No router exists; a third tab avoids server path changes. |
| Tests, CI | REUSE scratch DB / migrator fixtures, Vitest, Playwright `fixture`/`live`, the four CI jobs | No new CI job planned. |
| Staging | EXTEND `deploy/ec2` (migrate step, explicit grants, importer one-off) | `update.sh` never migrates; `setup.sh` and `restore.sh` grant `SELECT ON ALL TABLES`, which would expose the raw-file table (§5). |

## 3. Work packages

Each package ends with: HEAD, changed files, completed AC IDs, actual test results, blockers,
exact next task (recorded in `handoff.md`).

| Package | Delivers | ACs closed |
|---|---|---|
| M18-01 Contracts, Dataset & Acceptance Design | These documents; owner decisions; MASTER_PLAN edit proposal | — (design gate) |
| M18-02 Storage & Migrations | `returns/` contracts (Pydantic + enums), ORM, migration `0007`, versioned grants script, role/privilege integration tests | AC-01–05 |
| M18-03 Ingestion, Privacy & Fixtures | Domain import service, JSON + CSV adapters, CLI, redactor, generator + committed dataset | AC-06–10, AC-13 (domain), AC-14 |
| M18-04 Protected Backend API | Auth dependency, router, limits, pagination, OpenAPI | AC-09–13 (API) |
| M18-05 Retailer Frontend | `domains/returns`, client extensions, fixtures validated against Pydantic | AC-15 |
| M18-06 Integration & Browser Verification | Fixture and live Playwright import → list → detail; full regression | AC-16, AC-17 |
| M18-07 Release Readiness | CI evidence, old-image compatibility test, deploy-kit changes, `release.md`, approval request | AC-18 (+ AC-19 rehearsal) |
| M18-08 Staging Deployment & Smoke | Only after explicit approval | AC-19 |
| M18-09 Acceptance & Handoff | Evidence-to-AC table, MASTER_PLAN status row (if approved), limitations | owner acceptance |

M18-06 and M18-07 may run in one session if nothing fails; scope is unchanged either way.

## 4. Local versus staging workflows

| Aspect | Local development | Staging demo (D12; deployment not yet authorized) |
|---|---|---|
| Retailer tokens configured | viewer + importer | viewer only |
| `RETURNS_IMPORT_COMMIT_ENABLED` | `true` | `false` |
| Importer DB credentials in app env | yes (`returns_importer`) | **no** (absent from the app container) |
| Browser: validate a bundle | yes | yes, token-gated, nothing stored (no file, no batch row) |
| Browser: commit an import | yes | no; UI shows "Imports are disabled on this server" |
| How data arrives | browser commit or CLI | CLI in a one-off container as `returns_importer`, run during deployment |
| Returns list/detail, batch history | yes | yes, read-only |
| Destructive controls | none exist | none exist |

Commit therefore needs three independent conditions: importer token, commit flag, importer DB
credentials. Staging has none of them in the app container. Validation on staging only reads
(reference and idempotency lookups) as `search_app`.

## 5. Database roles (D2)

| Role | Used by | Privileges (full table: `contracts.md` §2) |
|---|---|---|
| `search_admin` (exists) | migrations and one-off setup only | schema owner; never used for routine imports |
| `search_app` (exists) | API | Existing privileges preserved: SELECT on every pre-M18 table (including M13/M14), granted from an explicit list. Plus SELECT on M18 normalized tables and batch and file **metadata**. No privilege on `return_import_file_contents`. No INSERT anywhere. |
| `returns_importer` (new) | CLI import (local and staging one-off); local API commit through a separate engine | SELECT + INSERT on M18 tables; **INSERT only** on raw contents; **column-level SELECT** on `products.product_id`, `raw_catalog_records(dataset_pk, source_key, raw_sha256)`, `catalog_datasets(id, dataset_id, dataset_version)`; USAGE on M18 sequences. No UPDATE/DELETE/TRUNCATE; no catalog, search, listing or decision writes. |

**Grants script:**
- One versioned, idempotent script, parameterized by role names (`contracts.md` §2.1).
- It grants the existing pre-M18 access explicitly, so nothing unrelated changes. It grants the
  M18 privileges, and it revokes only raw-contents access from `search_app` and PUBLIC.
  Unrelated privilege cleanup is not approved.
- It runs after every migration and every restore.
- Replacing the blanket `GRANT SELECT ON ALL TABLES` in `setup.sh` and `restore.sh` with the
  script is M18-07 work.
- Tests use real LOGIN roles (AC-03, AC-17).

**Credentials:** migration (`POSTGRES_ADMIN_PASSWORD`) and importer
(`RETURNS_IMPORTER_DB_PASSWORD`) credentials are separate `.env` entries. The app service
receives neither on staging.

## 6. Release and recovery outline (detail in `release.md`, M18-07)

Order on staging, after approval: CI green on the release commit → read-only check of the
staging Alembic revision (expected `0006`, unverified) → `backup.sh` → load image → one-off
`alembic upgrade head` as `search_admin` → grants script → one-off import as `returns_importer`
→ swap app image → smoke (AC-19).

- **Application rollback:** `update.sh` with the previous tag. This is considered safe only
  after the old-image compatibility test (AC-18) passes; additive is not assumed to mean safe.
  - The test runs the previous image against a database at `0007`, with the new grants and M18
    data, generation disabled and no network egress.
  - It must pass health, search, the assistant's generation-off path and its own live
    Playwright path.
  - Known hazard: the old image's `setup` profile would fail on the unknown revision `0007`; it
    must never be run after the migration.
- **Grants:** the script preserves `search_app`'s pre-M18 privileges (AC-03 preservation test).
  AC-17 and AC-18 still run the API as the real role under the new grants.
- **Database recovery:** `restore.sh` from the pre-deploy dump (destroys post-backup writes), or
  `alembic downgrade 0006` (drops M18 tables and their data). Both need explicit owner
  approval at the time. M18-07 rehearses a restore locally (dump → scratch database → grants →
  counts). The staging smoke checks only that data survives an app-container restart; that is
  not a database durability or restore test.

## 7. MASTER_PLAN conflicts and minimal edits (C1–C3 applied 2026-10-24, uncommitted; C7 pending M18-09; others need no edit)

None of these is applied. Each needs an owner decision; no gate is waived.

| # | Location (exact text) | Conflict | Proposed minimal edit |
|---|---|---|---|
| C1 | §28 dependency table, M18 row: "Return data source with recorded provenance and rights; PII redaction \| Data source not chosen" | Owner brief chooses a project-authored synthetic linked dataset over catalog v1. | Replace "Data source not chosen" with "Data source chosen (owner, <date>): project-authored synthetic linked dataset over catalog v1, every record `is_synthetic`; real retailer data not authorized (§10 item 14 unchanged). Details: `docs/m18/plan.md`." |
| C2 | §9: "Return-intelligence entities (M18) are typed, versioned contracts: … correction cases and proposed corrections; human decisions; outcomes." | Milestone 18 text lists only "correction cases"; proposals and decisions are M21, outcomes M22; CLAUDE.md forbids placeholder code. | After the list add: "M18 implements products/SKU linkage through correction cases. Proposed corrections and human decisions are introduced with their own versioned contracts in M21, outcomes in M22." |
| C3 | §10 item 7: the Return Intelligence dataset "contains: … proposed corrections and human decisions; measured or simulated outcomes, each marked as such." | Same split; the M18 dataset cannot contain M21/M22 records without inventing them. | Append: "Proposed corrections and human decisions are added by M21, outcomes by M22." |
| C4 | §9: "Every record carries these provenance fields: source_type, source_name, license, source_record_id, dataset_version, ingestion_timestamp, transformation_version, is_synthetic" | Design stores `source_record_id` and `is_synthetic` per row and the other six through non-null dataset and batch references (normalized), exposing all eight on every API record. | **Option A (recommended, no edit):** treat a non-null FK as "carries"; state it in `contracts.md`. **Option B:** store all eight columns on every row (literal, redundant, CHECK-consistent). **Owner approved Option A (D13, 2026-10-24).** |
| C5 | Milestone 18: "storage for … listing versions" | Design reuses `raw_catalog_records` as the versioned snapshot store via a typed reference, adds no new listing table, and does not use M13 `listing_versions`. | No edit. **Owner accepted (Q4, 2026-10-24):** immutable raw-catalog references are the v1 listing snapshots; no duplicate table. |
| C6 | §9: "products and SKU/variant linkage" | Catalog v1 has no variants. | No edit. Contract records "variant: not represented in catalog v1" as a limitation. |
| C7 | §"Repository status", M26A row: "Scoped staging amendment (owner, 2026-10-22): for the M26A staging demo only, one Free-plan EC2 …" | Deploying M18 to that instance extends its scope (M16 already runs there). | Add to the M18 status row on acceptance: "Deployed to the M26A single-EC2 staging demo under owner approval of <date>; no new infrastructure; zero out-of-pocket rule unchanged." |
| C8 | §23 indicative `POST /returns/import` | Design splits validate and commit paths and uses a qualified detail path `/returns/records/{dataset_id}/{dataset_version}/{return_id}`. | No edit; §23 paths are indicative. |

Not conflicts (noted for clarity): §23 `/reviews/*` belongs to M21, so customer reviews use the
name `customer_reviews`; the existing `catalog_reviews` is M2 catalog-quality review.

## 8. Owner decisions (recorded 2026-10-24)

The owner accepted the revision-2 recommended design with qualifications Q1–Q5:
- **Q1 (D14):** the viewer token stays with the owner initially. Secrets are never printed or
  committed.
- **Q2 (C7):** the existing-staging scope is agreed in principle (§9). This does **not**
  authorize deployment, credential changes or staging migrations now.
- **Q3 (D2):** preserve unrelated existing privileges, including M13/M14. Remove dangerous
  blanket and raw-file access. Grant required privileges explicitly. Unrelated privilege
  cleanup is not approved.
- **Q4 (C5/D4):** immutable raw-catalog references are accepted as the v1 listing snapshots. No
  fabricated history and no duplicate snapshot table.
- **Q5:** the §10 v1 limitations are accepted: add-only records and status snapshots,
  synthetic-only raw hashes, shared-token identity, and app-restart-only staging persistence
  evidence.

| ID | Decision | Status |
|---|---|---|
| D1 | Two static bearer tokens (viewer, importer) from `SecretStr` settings; fail closed when unset; rotation and revocation by `.env` + restart, no overlap window; shared identity (`contracts.md` §6) | APPROVED |
| D2 | `returns_importer` (column-level catalog SELECT, INSERT-only raw contents); metadata/contents split; grants script preserving pre-M18 privileges and revoking raw access, tested with real roles (§5, `contracts.md` §2.1) | APPROVED with Q3 |
| D3 | SKU = `product_id`, no variant key | APPROVED |
| D4 | Typed reference to `raw_catalog_records` as the listing snapshot; no fabricated history | APPROVED (Q4) |
| D5 | Imported correction cases, status `opened` only, focus area + evidence links (152-char composite key); no hypothesis, proposal, decision or transition | APPROVED |
| D6 | Phase A unlocked. Phase B rechecks and writes under a per-dataset-version advisory lock, with DB backstops. Add-only; snapshot records (status change = conflict); cancelled returns excluded from quantity; one shipment per order; synthetic-only. | APPROVED (Q5) |
| D7 | Limits per `contracts.md` §4.4; no temp-file spill; truncation semantics §4.8 | APPROVED |
| D8 | Coverage-derived dataset, about 5,300 rows (`contracts.md` §8) | APPROVED |
| D9 | `scenarios.json` never imported; release `records_sha256`; `returns verify` | APPROVED |
| D10 | Qualified detail path; total cursor order; `limit` 25/100 | APPROVED |
| D11 | Third tab "Retailer returns" | APPROVED |
| D12 | Staging validate-only and read-only; CLI import as `returns_importer` at deploy | APPROVED as design; execution needs §9 authorization |
| D13 | Provenance Option A (C4) | APPROVED |
| D14 | Viewer token held by the owner initially | APPROVED (Q1) |
| D15 | C1–C3 applied to MASTER_PLAN in the working tree (docs only, uncommitted, 2026-10-24); C7 status line at M18-09 | C1–C3 APPLIED; C7 PENDING |

## 9. Staging scope authorization record

| Item | State (2026-10-24) |
|---|---|
| M18 may target the existing M26A single-EC2 staging stack (no new infrastructure; zero out-of-pocket rule unchanged) | Agreed in principle by the owner (Q2) |
| Deployment, image change on staging | **Not authorized** |
| Staging migration, grants change, CLI import on staging | **Not authorized** |
| Credential creation or change (tokens, `returns_importer` password, `.env`) | **Not authorized** |

Before M18-08, a separate explicit owner authorization must be recorded here (date and scope).
No M18 deployment has happened.

## 10. Known limitations (design level)

- One synthetic catalog version: no "listing changed since order" history can be shown.
- No variants; SKU equals product.
- Token auth has no per-person identity. Batch records show a token label, not a person, and
  one holder cannot be revoked alone.
- The redactor is pattern-based with bounded guarantees (`contracts.md` §5). It does not detect
  names or addresses. A synthetic flag does not prove an upload holds no personal data, so all
  privacy rules apply to every upload, and v1 still accepts synthetic declarations only.
- Records are snapshots: status changes cannot be imported in v1.
- `record_sha256` of raw text is readable by the API role (accepted for synthetic-only v1).
- Staging persistence evidence covers only an app-container restart, not database durability.
- These limitations were accepted by the owner on 2026-10-24 (Q5).
- Scenario intents are generator intent, not labels, and are not M19/M20 ground truth.
