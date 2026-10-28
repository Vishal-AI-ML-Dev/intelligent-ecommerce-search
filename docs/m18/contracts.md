# M18 — Contracts

Status: **FROZEN — M18-01 design approved by the owner on 2026-10-24** (revision 2, with the
owner qualifications recorded in `plan.md` §8). Decision IDs refer to `plan.md` §8. Changes
after the freeze need owner approval. Owner-approved corrections since the freeze: §6.1
(2026-10-26), §6.2 and §6.3 (2026-10-27). M18-02 (storage, contracts, grants) is implemented;
its implementation boundaries are recorded in §10.

## 1. Versions and conventions

| Item | Value |
|---|---|
| Data contract | `return-data-v1` |
| Customer reason-code vocabulary | `return-reason-codes-1` |
| Redaction rules | `redaction-1` |
| Import transformation | `returns-import-1` |
| Demo dataset | `dataset_id` `synthetic-returns`, `dataset_version` `1` |
| Generator | `scripts/generate_returns_dataset.py@1` |

- **Dataset identity:**
  - `dataset_id` matches `^[a-z0-9][a-z0-9-]{0,47}$`.
  - `dataset_version` is an integer from 1 to 999,999,999.
  - A dataset version is one immutable dataset. Version 2 is a separate dataset and may reuse
    record IDs.
- **Record IDs:**
  - Pattern `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`, at most 64 characters, compared exactly
    (bytewise, `COLLATE "C"`). `|` is outside this alphabet.
  - Scope: (`dataset_id`, `dataset_version`, entity). The same ID in another dataset version
    is a different record.
  - References resolve only inside the same dataset version. References to the catalog
    (`products`, `raw_catalog_records`) are the only exception.
- **Synthetic prefixes:** `SYN-ORD-`, `SYN-OI-`, `SYN-SHP-`, `SYN-DEV-`, `SYN-RET-`, `SYN-RCM-`,
  `SYN-INS-`, `SYN-REV-`, `SYN-CMP-`, `SYN-CASE-`.
- **Actor references** (v1): `^SYN-(CUST|INSP|STAFF)-[0-9]{5}$` (§5).
- **Timestamps, decimals, integers, booleans and text:** canonical forms in §4.6.
- **Enums:** TEXT + named CHECK. A test keeps the migration's literal values equal to the
  Pydantic enums (existing M13 pattern).
- **Absent vs unknown:** a missing optional value is `null`. Enumerations that need "unknown"
  carry it explicitly. Absence of evidence is shown, never filled in.

## 2. Storage layers and permissions

| Layer | Tables | `search_app` (API) | `returns_importer` |
|---|---|---|---|
| Provenance | `return_datasets`, `return_import_batches` | SELECT | SELECT, INSERT |
| File metadata | `return_import_files` (name, entity, media type, size, SHA-256, rows) | SELECT | SELECT, INSERT |
| Raw contents (restricted) | `return_import_file_contents` (`file_pk`, `raw_bytes`, `raw_sha256`) | **none** | INSERT only (no SELECT) |
| Normalized | the eleven entity tables in §3 | SELECT | SELECT, INSERT |
| Catalog references | `products` (`product_id`); `raw_catalog_records` (`dataset_pk`, `source_key`, `raw_sha256`); `catalog_datasets` (`id`, `dataset_id`, `dataset_version`) | existing read set (§2.1) | **column-level SELECT on the listed columns only** |

**Raw contents:**
- `raw_sha256` is checked against `raw_bytes` by a CHECK constraint.
- A composite FK (`file_pk`, `raw_sha256`) references a UNIQUE (`id`, `raw_sha256`) on
  `return_import_files`, so the metadata checksum cannot disagree with the stored bytes.
- The API exposes file metadata and never raw bytes.

**Append-only:** all M18 tables block UPDATE, DELETE and TRUNCATE with triggers.

**Normalized rows:**
- Each row carries `dataset_pk`, `batch_pk`, `source_file_pk`, `source_row` (1-based data row),
  `source_record_id`, `record_sha256` and `is_synthetic`.
- The other §9 provenance fields come through the non-null dataset and batch references:
  `source_type`, `source_name`, `license`, `dataset_version`, `ingestion_timestamp` and
  `transformation_version`. The API returns all eight on every record (Option A of `plan.md`
  C4, D13).
- **Known exposure:** `record_sha256` hashes raw text and sits in a table the API can read.
  This is accepted for synthetic-only v1 and must be revisited before real data.

**Dataset and batch records:**
- **`return_datasets`** (immutable): `dataset_id`, `dataset_version`, `source_type`
  (`synthetic` only in v1), `source_name`, `source_description`, `rights_note`,
  `license_identifier`?, `authored_on`, `generator`, `is_synthetic` (true), `notes`?,
  `descriptor_sha256` (§4.6). There is no content checksum in the database; content
  verification is in §4.7.
- **`return_import_batches`:** `batch_id` (UUID v4), `dataset_pk`, `contract_version`,
  `transformation_version`, `redaction_version`, `bundle_sha256` (unique per dataset), `format`
  (`json` | `csv`), `file_count`, `total_bytes`, per-entity `received`/`new`/`unchanged`
  counts, `imported_by_label`, `imported_at`.

### 2.1 Grants (D2, approved with owner qualification 2026-10-24)

Owner qualification: preserve unrelated existing privileges, including M13/M14; remove only
dangerous blanket and raw-file access; grant required privileges explicitly. Unrelated
privilege cleanup is not approved.

One versioned, idempotent grants script runs after every migration and every restore. It takes
the role names as parameters, so tests can use real per-test roles. It:

1. **preserves** `search_app`'s existing access by granting SELECT explicitly on an enumerated
   list of every pre-M18 table (migrations 0001–0006, including the M13/M14 listing tables).
   This replaces the blanket `GRANT SELECT ON ALL TABLES` in `setup.sh` and `restore.sh` with
   an equivalent explicit list for the existing tables;
2. grants `search_app` SELECT on the M18 tables marked SELECT in §2;
3. runs `REVOKE ALL ON return_import_file_contents FROM search_app, PUBLIC`;
4. grants `returns_importer` exactly the privileges in §2, plus USAGE on the M18 identity
   sequences;
5. asserts and fails loudly unless:
   - `search_app` has no privilege on `return_import_file_contents`;
   - neither role has M18 default privileges or table ownership.

The script revokes nothing on pre-M18 tables. Changing `setup.sh`/`restore.sh` to call it is
M18-07 deploy-kit work and needs staging authorization before it is used there. The migration
role (`search_admin`) owns the schema and is used for nothing else.

## 3. Entities (`return-data-v1`)

"Ref" means the referenced ID must exist in the same bundle or already be stored in the same
dataset version. Every input row also has `is_synthetic` (required, must be `true`).
Database backstops are FK, UNIQUE, CHECK and one constraint trigger. Composite FKs on
(`dataset_pk`, parent ID) enforce references even if the service is bypassed.

**orders** — `order_id`; `customer_ref` (actor); `ordered_at`; `currency` (`INR`).

**order_items** — `order_item_id`; `order_id` (ref); `product_id` (FK to `products`);
`quantity` (1–10); `unit_price`; listing snapshot triple `catalog_dataset_id`,
`catalog_dataset_version`, `catalog_raw_sha256`.
- The snapshot triple must equal the stored `raw_catalog_records` row for
  `source_key = product_id`.
- `sku` is derived as `product_id` (D3); it is not an input. Variants are not represented in
  catalog v1.

**shipments** — `shipment_id`; `order_id` (ref; **UNIQUE per dataset: one shipment per
order**); `carrier_ref` (ID); `status` ∈ {`pending`, `in_transit`, `delivered`,
`delivery_failed`, `returned_to_origin`, `unknown`}; `shipped_at`?;
`promised_delivery_by`?; `delivered_at`? (required if and only if `delivered`).

**delivery_events** — `event_id`; `shipment_id` (ref); `occurred_at`; `event_type` ∈
{`dispatched`, `in_transit`, `out_for_delivery`, `delivered`, `delivery_attempt_failed`,
`delay_notified`, `damage_reported_by_carrier`, `returned_to_origin`}; `source` ∈ {`carrier`,
`warehouse`}. No free text.

**returns** — `return_id`; `order_item_id` (ref); `requested_at`; `quantity` (≥1);
`customer_reason_code`; `status` ∈ {`requested`, `in_transit_to_warehouse`, `received`,
`closed`, `cancelled`}; `received_at`?.
- `received` requires `received_at`. `cancelled` forbids `received_at`.
- **Quantity invariant:** the sum of `quantity` over non-cancelled returns of one order item
  must not exceed the item's quantity. Cancelled returns do not count. This is enforced by the
  service under the lock and backstopped by a deferred constraint trigger.
- **Snapshots, not histories:** a record is the exporter's state at export time. Re-sending a
  return with a changed status is `conflict_existing`. Status updates are not supported in v1
  (limitation; a later contract version could add status events).
- There are no refund, resolution, fault or responsibility fields.

**`return-reason-codes-1`** — chosen by the customer when requesting the return. It is a
statement, not a classification; mapping to §19 categories is M19.
- Codes: `SIZE_TOO_SMALL`, `SIZE_TOO_LARGE`, `NOT_AS_DESCRIBED`, `LOOKS_DIFFERENT_FROM_IMAGE`,
  `SPECIFICATION_DIFFERENT`, `DEFECTIVE_OR_NOT_WORKING`, `ARRIVED_DAMAGED`,
  `WRONG_ITEM_RECEIVED`, `ARRIVED_LATE`, `NO_LONGER_NEEDED`, `OTHER`, `NOT_PROVIDED`.

**return_comments** (`customer_statement`) — `comment_id`; `return_id` (ref); `submitted_at`;
`text` (1–2,000 code points, not whitespace-only).

**inspection_observations** (`inspection_observation`) — `observation_id`; `return_id` (ref;
the return's status must be `received` or `closed`); `observed_at`; `inspector_ref` (actor);
`condition` ∈ {`as_new`, `used_good`, `damaged`, `functional_fault_confirmed`,
`no_fault_found`, `item_differs_from_order`, `parts_missing`, `not_assessable`}; `note`?
(≤1,000).
- An observation records what was seen. It is not a decision on refund, fault or conduct.

**customer_reviews** — `review_id`; `product_id` (FK); `order_item_id`? (ref; same product);
`rating` (1–5); `submitted_at`; `text`? (≤2,000). A review is never a return event.

**support_complaints** (`customer_statement`) — `complaint_id`; at least one of `order_id` /
`order_item_id` / `product_id`, mutually consistent; `opened_at`; `channel` ∈ {`email`,
`chat`, `phone`, `other`}; `customer_topic`? ∈ {`delivery`, `damaged`, `wrong_item`,
`product_issue`, `return_process`, `other`}; `text` (1–2,000).

**correction_cases** — `case_id`; `product_id` (FK); snapshot triple (same rule as order
items); `opened_at`; `opened_by_ref` (actor); `focus_area` ∈ {`listing_text`,
`size_information`, `specification`, `imagery`, `packaging`, `product_quality`, `fulfilment`,
`logistics`, `other`}; `status` ∈ {`opened`} (D5); `note`? (≤1,000).
- `focus_area` is the area a person asked to investigate. It is not a finding.

**correction_case_evidence** — `case_id` (ref); `evidence_type` ∈ {`return`, `return_comment`,
`inspection_observation`, `customer_review`, `support_complaint`, `shipment`,
`delivery_event`}; `evidence_id` (ref of that type, same dataset version).
- The evidence must relate to the case's product: through the order item, or for shipments and
  delivery events, through an order that contains that product.
- **Natural key:** three columns, UNIQUE (`dataset_pk`, `case_id`, `evidence_type`,
  `evidence_id`).
- **`source_record_id`:** `case_id|evidence_type|evidence_id`. It is unambiguous because `|` is
  outside the ID alphabet. Maximum length 64 + 1 + 22 (`inspection_observation`) + 1 + 64 =
  **152**, so this entity's column CHECK allows 152; every other entity allows 64.

**Kept distinct, by construction:**
- *Customer statement:* reason code, comments, complaints, reviews.
- *Inspection observation:* `inspection_observations`.
- *Hypothesis:* none in M18 (M20).
- *Human decision:* none in M18 (M21).

**Exact column allowlists** (AC-05): each M18 table's column set and each response schema's
field set must equal the set declared in this section. Any extra column or field fails the test.

### 3.1 Structural invalidity versus conflicting evidence

**Rejected as structurally invalid** (the system records cannot all be true):
- broken references, or references across dataset versions;
- snapshot mismatch;
- enum, format or range errors;
- a second shipment for an order;
- non-cancelled quantity over the item quantity;
- system-time order violations: `ordered_at ≤ shipped_at ≤ delivered_at`;
  `ordered_at ≤ requested_at ≤ received_at`; `requested_at ≤ observed_at`;
  `ordered_at ≤` every comment, complaint, review and event time; nothing later than the
  import time;
- an inspection on a return that was never received;
- `received_at` present on a cancelled return;
- a review or complaint whose product disagrees with its order item.

**Accepted as valid, possibly conflicting evidence:** stored and shown side by side, never
flagged, scored or resolved by M18. Examples:
- reason code versus comment content;
- a comment saying "damaged" versus an `as_new` inspection;
- a carrier damage event versus an `as_new` inspection;
- a positive review on a returned item;
- complaint topic versus reason code;
- a `delivered` shipment with a "not received" complaint;
- two inspections that disagree.

A customer statement can be wrong; only contradictions between system records are structural.

## 4. Import contract

### 4.1 Formats

- **JSON bundle:** one UTF-8 JSON object `{contract_version, dataset{…}, orders[],
  order_items[], shipments[], delivery_events[], returns[], return_comments[],
  inspection_observations[], customer_reviews[], support_complaints[], correction_cases[],
  correction_case_evidence[]}`.
  - Absent arrays are treated as empty.
  - Unknown keys and duplicate keys are rejected; maximum nesting depth is 4.
  - Stored as one file with the canonical name `bundle.json`.
- **CSV set:** `manifest.json` (`{contract_version, dataset{…}, files:[{name, entity, sha256,
  rows}]}`) plus one `<entity>.csv` per included entity, named exactly from the allowlist.
  - CSV rules: RFC 4180, comma, `"` quoting, UTF-8 (one leading BOM tolerated), LF or CRLF.
  - The header must contain exactly the contract's columns, in any order. An empty field is
    `null` for optional columns.
- **CLI:** `python -m ecommerce_search.returns validate|import|verify <bundle.json | csv-dir>`.
  The browser and API call the same domain service.

### 4.2 CSV multi-file atomicity

- The manifest must list every uploaded part, and every listed file must be uploaded. Each
  file's SHA-256 and row count must match the manifest; any mismatch rejects the whole set.
- Validation covers the whole set, including references between files, before any write.
- The commit is one transaction (§4.3); any failure writes nothing.
- A set split across requests is not supported; each request is a complete set.

### 4.3 Validation, locking and commit

**Phase A — no transaction and no lock:**
1. Envelope and limits.
2. Encoding and parsing.
3. Field rules.
4. Uniqueness within the bundle and structural rules inside the bundle.
5. Catalog references.

**Phase B — DB-dependent checks and writes:**
- Commit mode:
  - Opens a READ COMMITTED transaction.
  - Takes `pg_advisory_xact_lock(hash("returns-import:" + dataset_id + ":" + dataset_version))`.
  - **Under the lock, re-runs every DB-dependent check:**
    - dataset descriptor equality, or creation of the dataset row;
    - whether the `bundle_sha256` already exists;
    - natural-key comparison (unchanged versus `conflict_existing`);
    - references to stored records and catalog rows;
    - the cross-table time rules against stored parents;
    - one shipment per order including stored shipments;
    - the quantity aggregate including stored non-cancelled returns;
    - the evidence-to-product relation.
  - Then inserts the batch, file metadata, file contents and new rows, and commits.
  - Every writer to a dataset version takes the same lock. FK, UNIQUE, CHECK and the deferred
    quantity trigger are the final guard if any check is missed.
- Validate mode runs the same Phase B checks in a READ ONLY, REPEATABLE READ transaction
  without the lock. Its result is advisory "as of `checked_at`"; a later commit re-checks
  everything.

### 4.4 Limits (D7) — bundle-wide

| Limit | Value | Error |
|---|---|---|
| HTTP request body (read into memory with a streaming cap; no temp-file spill) | 9 MiB | 413 `payload_too_large` |
| Total bundle bytes / any single file | 8 MiB / 5 MiB | `bundle_too_large` / `file_too_large` |
| Files per set (11 entity + manifest) | 12 | `too_many_files` |
| Multipart parts per HTTP request (amended §6.3) | 12; starting a 13th part rejects the whole request at once | 400 `malformed_upload` |
| HTTP request body read time, total (amended §6.3) | 30 s, from immediately before waiting for the first body chunk | 408 `upload_timeout` |
| `manifest.json` | 64 KiB | `file_too_large` |
| Data rows: bundle / one entity | 10,000 / 5,000 | `too_many_rows` |
| CSV columns / bytes per field | 30 / 4,096 | `too_many_columns` / `field_too_large` |
| Free text: text / note | 2,000 / 1,000 code points | `too_long` |
| Dataset text fields | 2,000 (`source_description`, `rights_note`, `notes`), 200 (others) | `too_long` |
| Error collection | stops at 1,000; returns at most 200 (§4.8) | — |
| Concurrent validate/import per process | 1 | 429 `import_busy` |

The demo dataset (about 5,300 rows, under 2 MiB) leaves about 2× headroom on rows and 4× on
bytes.

### 4.5 Idempotency and incremental imports

| Case | Status | Writes |
|---|---|---|
| Same file bytes again (same `bundle_sha256`) | `already_imported` (+ existing `batch_id`) | none |
| Same records in the other format, or any bundle whose every record is `unchanged` | `no_new_records` | none (no batch, no files) |
| Some unchanged records plus new records | `imported` | batch, all files of the request, **new rows only** |
| Any record with a stored natural key but a different `record_sha256` | `invalid` (`conflict_existing`) | none |
| Dataset block differs from the stored descriptor | `invalid` (`dataset_mismatch`) | none |
| Any of the above in validate mode | same status label | never |

- Incremental imports may add records and may reference records stored by earlier batches of
  the same dataset version.
- Records are never updated or deleted.
- v1 rejects `is_synthetic: false` and `source_type ≠ synthetic` (`not_synthetic`).

### 4.6 Canonical forms and hashing

| Type | Accepted input | Canonical form |
|---|---|---|
| Timestamp | RFC 3339 with explicit offset, at most 6 fractional digits; no leap seconds | UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always 6 digits) |
| Decimal (money) | JSON string or CSV text matching `^(0|[1-9][0-9]{0,7})(\.[0-9]{1,2})?$`; JSON numbers rejected | exactly 2 fractional digits, e.g. `33999.00` |
| Integer | JSON integer (not `1.0`); CSV `^(0|[1-9][0-9]*)$` | JSON integer |
| Boolean | JSON `true`/`false`; CSV `true`/`false` | JSON boolean |
| Text | exact decoded code points; no trimming or Unicode normalization; CSV BOM removed from the file, not from fields | JSON string |
| Optional absent | missing JSON key, JSON `null`, or empty CSV field | `null`, always present in the hashed object |

**Hashes:**
- **`record_sha256`:** SHA-256 of UTF-8 `"return-data-v1\n" + entity + "\n" + JSON(record)`.
  - JSON(record) has sorted keys, `,` and `:` separators, non-ASCII kept, and contains exactly
    the entity's contract fields (including `is_synthetic`).
  - Batch, file and row references are excluded, so CSV and JSON forms hash identically.
- **`descriptor_sha256`:** the same canonical JSON over the `dataset` block.
- **`bundle_sha256`:** SHA-256 of `"return-data-v1\n"` plus one `name \0 file_sha256 \n` line per
  stored file, sorted by canonical name. It identifies the exact request content, so it differs
  between formats.

### 4.7 Dataset content checksum

- **`records_sha256`:** SHA-256 over the sorted lines `entity \t source_record_id \t
  record_sha256 \n` (sorted by entity, then bytewise ID) for one dataset version. It is format
  independent.
- **Release side:** `provenance.json` records `records_sha256` for the release, together with
  the SHA-256 of `bundle/returns_v1.json`, every CSV, `manifest.json` and `scenarios.json`.
- **Stored side:** `returns verify` computes `records_sha256` over the stored rows.
  - After a complete import of the release, the two values must be equal.
  - After a partial or incremental import, `verify` reports the difference: counts of missing
    and extra records.

### 4.8 Report and row errors

**`ImportReport`:** `contract_version`, `mode`, `status`, `bundle_sha256`, `batch_id`?,
`checked_at`, `counts{entity:{received, new, unchanged}}`, `phases_completed`, `error_count`,
`error_count_is_lower_bound`, `errors_truncated`, `errors[]`.

**Error ordering and truncation:**
- Errors are collected in a deterministic order: phase, then canonical file name, then row,
  then field, then code.
- A file that fails phase 2 is not checked further. Phases after a failed bundle-level phase do
  not run, and `phases_completed` says which ran.
- Collection stops at the 1,000th error.
- `error_count` is the number collected.
- `error_count_is_lower_bound` is true when collection stopped or a later phase was skipped
  after errors.
- `errors` holds the first 200.
- `errors_truncated` is true when `error_count > 200`.

**`RowError`:** `file` (canonical name, or `unexpected part #n`), `entity`, `row`?, `line`?,
`pointer`?, `field`?, `code`, `message` (a fixed per-code template), `value_preview`? (§5).
- Codes: `required`, `type`, `format`, `enum`, `too_long`, `out_of_range`, `unknown_column`,
  `missing_column`, `duplicate_in_bundle`, `unknown_reference`, `unknown_product`,
  `snapshot_mismatch`, `inconsistent_reference`, `temporal_order`, `quantity_exceeded`,
  `duplicate_shipment`, `conflict_existing`, `not_synthetic`, `looks_personal`, `encoding`,
  `contract_version`, `dataset_mismatch`, `missing_file`, `unexpected_file`,
  `checksum_mismatch`, `row_count_mismatch`, plus the limit codes in §4.4.

## 5. Privacy

**The synthetic flag is a declaration, not proof.** Any upload may contain real personal
data. Every rule below applies to every upload whatever its flags say, and v1 still refuses
non-synthetic declarations.

### 5.1 Surfaces

| Surface | Behaviour |
|---|---|
| Free text (`text`, `note`) | **Redact** with `redaction-1`. Only the redacted form and per-kind counts are stored in normalized tables. |
| Actor references | **Reject** unless `^SYN-(CUST|INSP|STAFF)-[0-9]{5}$` (`format`). |
| Record IDs, `carrier_ref` | ID pattern; **reject** if any `redaction-1` pattern matches (`looks_personal`). |
| Dataset block text | Length caps; **reject** on any pattern match (`looks_personal`). Provenance is never silently altered. |
| Uploaded file names | Client file names are never stored, logged or echoed. Parts must use allowlisted names; others are reported as `unexpected part #n`. Stored names are canonical. |
| CSV header names, JSON keys, JSON pointers | Echoed only if they match `^[a-z_]{1,40}$` (keys) or are array indices; otherwise `<unrecognized>`. |
| `value_preview` | Never for free text, actor references or dataset text. Otherwise passed through `redaction-1`, then truncated to 80 characters. |
| `/returns` query and path validation | A route-scoped 422 handler returns parameter name and code only (FastAPI's default `input`/`ctx` echo is removed). Buyer routes are unchanged. |
| Exceptions | Library and Pydantic exception text is never returned or logged on `/returns` routes. It is mapped to codes; a 500 logs the exception type and request ID. |
| Logs | Event names, IDs, counts and status only. Never values, file names, tokens or bodies. |
| Validate-mode bytes | Memory only, under the body cap, discarded after the response; no temp files. |
| Commit-mode bytes | `return_import_file_contents` only (restricted). |
| Frontend | Renders only the server's safe fields; logs no bodies or tokens. |

### 5.2 `redaction-1`

Matches are non-overlapping, leftmost-longest, applied in this order:

| Kind | Pattern (summary) | Token |
|---|---|---|
| email | existing `assistant/privacy.py` `EMAIL` | `[EMAIL]` |
| upi | `handle@provider` with no dot after the provider | `[UPI]` |
| number | existing `LONG_NUMBER` (10 or more digits, optional `+` and separators) | `[NUMBER]` |
| pin | a standalone 6-digit run | `[PIN]` |

**Guarantees (bounded):**
- **G1:** no pattern matches redacted output.
- **G2:** redaction is idempotent.
- **G3:** only fixed tokens are inserted.
- **G4:** API, UI, logs, error messages and frontend fixtures carry no raw free text.
- **G5:** raw bytes are reachable only by the migration owner. Even `returns_importer` cannot
  read them.
- **G6:** the §5.1 surfaces behave as stated.

**Not guaranteed:** names, postal addresses, numbers in words or split by other characters,
spaced PIN codes, personal data in images, and patterns outside `redaction-1`. False positives
are accepted.

These limits are why real data needs a separate owner decision: authorization, a stronger
detector and retention rules (MASTER_PLAN §10 item 14).

### 5.3 Synthetic fixture safety

- **Allowlist:** every personal-data-like string in committed fixtures comes from a closed list.
  - Emails at `example.com`, `example.org` or `example.net`.
  - Phone-like numbers whose subscriber part starts with `0` (invalid for Indian mobiles).
  - Card-like `0000 0000 0000 0001`, which fails the Luhn check.
  - PIN-like `0xxxxx`; Indian PIN codes never start with 0.
  - UPI-like handles whose provider part contains `example`.
- **Scan test:** a test scans every committed M18 fixture. Raw matches must be on the
  allowlist, and redacted outputs and frontend fixtures must have zero matches.
- **No identities:** no names or addresses. Prompt-injection and markup strings are inert text.

## 6. Access control (D1, D14)

D14 (owner): the staging viewer token initially stays with the owner. Tokens and passwords are
never printed, logged, echoed in reports or committed.

| Setting | Meaning |
|---|---|
| `RETURNS_VIEWER_TOKEN` (`SecretStr`, 32–256 chars, RFC 6750 `b64token` grammar; amended §6.2) | scopes `returns:read`, `returns:validate` |
| `RETURNS_IMPORTER_TOKEN` (must differ) | adds `returns:import` |
| `RETURNS_IMPORTER_LABEL` | non-secret label recorded on batches (corrected 2026-10-26, §6.1) |
| `RETURNS_IMPORT_COMMIT_ENABLED` (default `false`) | commit path switch |
| `RETURNS_IMPORTER_DB_USER` / `_PASSWORD` | separate engine used only for commit |

**Request rules:**
- Header: `Authorization: Bearer <token>`. Comparison: constant time on SHA-256 digests.
- Parsing (§6.2): exactly one `Authorization` header; scheme `Bearer` in any case; exactly one
  space before the token. Anything else is malformed (401).
- Responses by case:

| Case | Response |
|---|---|
| No token configured | 503 `retailer_access_not_configured` on every `/returns` route |
| Missing, malformed or unknown token | 401 `unauthorized` with `WWW-Authenticate: Bearer` |
| Valid token without the needed scope | 403 `forbidden` |
| Commit flag off or importer credentials absent | 403 `import_commit_disabled` |

**Other rules:**
- Tokens are never logged or echoed.
- No cookies, so no CSRF surface; same-origin only.
- Buyer routes neither require nor accept these tokens.
- Every `/returns` response carries `Cache-Control: no-store`.

**Rotation and revocation:**
- Generate a new token (`secrets.token_urlsafe(32)`), replace it in the root-only `.env`
  (mode 600), and restart the app.
- The old token stops working at restart. There is no overlap window in v1, so holders need the
  new token.
- **Emergency revocation:** unset the token and restart. Every `/returns` route then fails
  closed with 503.
- Tokens never enter the repository or CI. Tests generate their own.

**Shared-identity limits:**
- Everyone holding a token is indistinguishable.
- Batches record the token label, not a person.
- There is no per-user audit.
- One holder cannot be revoked without rotating for all.

This is acceptable only for synthetic demo data. Real data would need per-user identity, which
is out of M18 scope.

### 6.1 Contract correction (owner decision D4, 2026-10-26, during M18-04A)

- **Original frozen row:** "`RETURNS_VIEWER_LABEL`, `RETURNS_IMPORTER_LABEL` | non-secret labels
  recorded on batches".
- **Correction:** `RETURNS_VIEWER_LABEL` is removed. The row now names `RETURNS_IMPORTER_LABEL`
  only.
- **Rationale:** batches are written only by commits, and a commit needs the `returns:import`
  scope, which the viewer token never has. A viewer label could therefore never be recorded on
  any batch. No other use of the setting existed in the contracts, code or tests (checked
  before the edit).
- **Unchanged:** importer attribution (`RETURNS_IMPORTER_LABEL` → `imported_by_label`) and every
  other provenance field.

### 6.2 Contract amendments A1 and A2 (owner approval 2026-10-27, M18-04A review)

- **A1 (token format).** Original: "`RETURNS_VIEWER_TOKEN` (`SecretStr`, ≥32 chars)".
  Amended: both tokens are 32–256 characters and match the RFC 6750 `b64token` grammar
  (letters, digits, `-` `.` `_` `~` `+` `/`, then optional trailing `=`). This is a character
  rule only; no Base64 decoding is performed or required. A setting outside it fails at startup.
  `secrets.token_urlsafe(32)` (43 characters) conforms.
- **Bearer parsing (recorded choice).** Exactly one `Authorization` header and exactly one space
  between `Bearer` and the token. This is stricter than RFC 6750's `1*SP`; other forms are
  malformed (401).
- **A2 (422 body).** Original: "The error body is `{"detail": str, "code": str}`". Amended: a
  422 `invalid_parameter` may add `parameters`, a list of `{name, code}` pairs. Names are safe
  keys and codes are fixed validation codes; submitted values are never included (§5.1).
- **Unchanged:** every other §6 and §7 rule.

### 6.3 Contract amendments C1 and C2 (owner approval 2026-10-27, M18-04B)

- **C1 (part count).** On the start of a 13th multipart part, the whole HTTP request is
  rejected with 400 `malformed_upload`. The rest of the body is neither read nor parsed, the
  import service is not called, and no partial bundle is validated or imported. `too_many_files`
  (§4.4) still applies inside the service; over HTTP, C1 is reached first.
- **C2 (body-read deadline).** Reading the HTTP request body must finish within 30 s in total,
  measured from immediately before waiting for the first body chunk (so a sender that sends no
  body bytes is covered), not per chunk. Otherwise: 408 `upload_timeout`, fixed text,
  `Cache-Control: no-store`, nothing imported. This bounds body reading only; it is not a
  validation, worker or database timeout.
- **Unchanged:** every other §4.4, §6 and §7 rule.

## 7. API contract

The error body is `{"detail": str, "code": str}` (extends the existing shape; confirmed in
M18-04). Every response carries `X-Request-ID`. A 422 `invalid_parameter` may add
`parameters`: a list of safe `{name, code}` pairs, never submitted values (amended §6.2).

**API identity:**
- A return is identified by (`dataset_id`, `dataset_version`, `return_id`).
- Summaries carry `ref{dataset_id, dataset_version, return_id}`.
- The detail path is qualified, so it is never ambiguous across dataset versions.
- Batches are identified by their UUID.

| Method and path | Scope | Success |
|---|---|---|
| `GET /returns/capabilities` | read | 200: contract version, granted scopes, `can_validate`, `can_commit`, limits, reason codes |
| `POST /returns/import/validate` | validate | 200 `ImportReport` (`valid` / `invalid` / `already_imported` / `no_new_records`); nothing stored |
| `POST /returns/import` | import | 201 `imported`; 200 `already_imported` / `no_new_records`; 422 `invalid` (same body) |
| `GET /returns/import-batches` | read | 200 page; newest first, ordered by `imported_at` desc then `batch_id` |
| `GET /returns/import-batches/{batch_id}` | read | 200 with file metadata (no bytes) / 404 `batch_not_found` |
| `GET /returns` | read | 200 page |
| `GET /returns/records/{dataset_id}/{dataset_version}/{return_id}` | read | 200 `ReturnDetail` / 404 `return_not_found` |

**List filters:** `dataset_id`, `dataset_version`, `status`, `customer_reason_code`,
`category`, `product_id`, `requested_from`, `requested_to`, `has_comment`, `has_inspection`,
`batch_id`.

**Pagination (D10):**
- **Total order:** `requested_at` desc, `dataset_id` asc, `dataset_version` asc (numeric),
  `return_id` asc (all text `COLLATE "C"`).
  - It is total because the last three columns are unique together.
- **Cursor:** opaque base64url of `{v:1, requested_at, dataset_id, dataset_version,
  return_id, filters_sha256}`.
  - A malformed cursor gives 400 `malformed_cursor`; reuse with different filters gives 400
    `cursor_mismatch`.
- **Page:** `limit` defaults to 25 (max 100); page shape `{items, next_cursor, limit}`.

**`ReturnSummary`:** `ref`, `requested_at`, `status`, `quantity`, `customer_reason_code`,
product `{product_id, sku, title, category, brand}`, `order_id`, `order_item_id`,
`comment_count`, `inspection_count`, `is_synthetic`.

**`ReturnDetail`:**
- Return facts and the order (`order_id`, `customer_ref`, `ordered_at`, `currency`).
- The order item, and the product/SKU.
- `listing_snapshot`: catalog dataset ID and version, `raw_sha256`, `matches_current_catalog`,
  and fields parsed from the snapshot line.
- `customer_statements`: reason code and redacted comments.
- `inspection_observations`.
- `shipment` with its `delivery_events`.
- `related_reviews` (same order item) and `related_complaints` (same order item or order).
- `correction_cases` (`case_id`, `focus_area`, `status`, `opened_at`, `evidence_count`).
- `evidence_presence`: counts per kind; zero is shown as "not recorded".
- `provenance`: the eight §9 fields plus `batch_id`, `source_file`, `source_row`.

The API shows conflicting evidence side by side (§3.1). It never flags, scores or resolves
conflicts, and has no customer-level aggregates.

## 8. Demo dataset and scenario design (D8, D9)

Sizes come from coverage needs. Return proportions are generator choices, not market evidence.

| Driver | Returns |
|---|---|
| 12 §19 categories × 4 evidence shapes (consistent; missing comment and/or inspection; conflicting evidence per §3.1; noisy Hinglish/misspelled text) × 2 instances | 96 |
| 6 SKU hotspots × 8 returns sharing a pattern (2 shoe sizing, 1 laptop specification claim, 1 headphone colour/image, 1 phone defect, 1 packaging damage) | 48 |
| Plain controls (change of mind, other) | 36 |
| **Total returns** | **180** |

**Category applicability:**
- Sizing/fit: shoes only.
- Specification: laptops and phones.
- Colour/image: shoes, headphones and phones.
- "Suspected abuse" (8): only inspection observations such as `item_differs_from_order`; one
  distinct customer each, never reused; generator intent "investigation only".
- "Insufficient evidence" (8): `NOT_PROVIDED` code, no comment, no inspection.

**Coverage of structural rules:**
- At least 12 returns are `cancelled`, including on items with further returns, to exercise
  the quantity rule.
- Every order has exactly one shipment.

**Overlays on existing returns** (no extra returns):
- at least 10 comments with allowlisted fake personal data;
- at least 6 prompt-injection comments;
- at least 4 markup comments;
- at least 4 near-limit comments;
- at least 4 Devanagari comments.

**Other entity sizes:**

| Entity | Count | Rationale |
|---|---|---|
| order items | 900 | about 20% return rate; every one of the 240 products in at least 2 items; each hotspot in at least 20 items |
| orders / customers | 600 / 400 | 1–3 items per order |
| shipments / delivery events | 600 / about 2,400 | one shipment per order |
| comments / inspections | about 150 / about 140 | fewer than returns because of the missing-evidence shapes |
| reviews / complaints | 150 / 60 | — |
| correction cases / evidence links | 20 / about 120 | — |

Total about 5,300 rows; the largest entity is about 2,400 against the 5,000 limit.

**Facts and time:**
- Synthetic window 2026-01-05 to 2026-06-30 at `+05:30`.
- Prices equal the catalog price in the snapshot.
- Carriers are `synthetic-carrier-a` to `-c`.
- Comments may claim a listing differs. Those are statements; catalog facts and listing history
  are never altered or invented.

**Files:** `data/seed/returns_v1/` holds `provenance.json` (§4.7 checksums),
`bundle/returns_v1.json`, `csv/` (`manifest.json` + 11 CSVs) and `scenarios.json`.
`generate_returns_dataset.py --check` reproduces every checksum.

**Scenario manifest (`scenarios.json`):**
- Per scenario: `scenario_id`, `family`, `generator_intent`, `evidence_shape`, `record_ids`,
  `listing_truth: "unknown"`, `label_status: "generator_intent_not_label"`.
- It is outside the import directories and never imported or loaded into the database.
- It is not M19/M20 ground truth.

## 9. UI contract (D11)

The third tab, "Retailer returns", lives in `frontend/src/domains/returns/`.

| Component | Behaviour |
|---|---|
| Access gate | Masked token field. The token is held in React state only and cleared on 401 or "Sign out". Unauthorized explanation. On 503, "Retailer access is not configured on this server." |
| Import panel | Driven by `/returns/capabilities`. Validate always; Import only when `can_commit`, otherwise "Imports are disabled on this server." Client-side size and count pre-checks. |
| Validation report | Status, counts, `phases_completed`, error table, and "showing 200 of N" or "at least N" per §4.8 |
| Batch history | Read-only list and file metadata |
| Returns list | §7 filters, Next and Previous (client cursor stack), "Synthetic demo data" badge |
| Return detail | §7 sections. "Not recorded" for absent evidence. Statements and observations visually separate, conflicts shown side by side without a flag. Provenance block. Snapshot with its catalog version. |
| States | Loading, empty, error with request ID, unauthorized, disabled |

Text is rendered only as React text. No AI, classification, dashboard, root-cause or approval
controls. The HTTP client gains GET, an `Authorization` header and multipart support; its
logging rule is unchanged.

## 10. M18-02 implementation boundaries (owner-directed record, 2026-10-24)

These notes clarify how M18-02 implements §1–4. They change no rule above and waive no check.

- **Catalog snapshot version:** `catalog_dataset_version` uses the catalog's existing text
  representation (`catalog_datasets.dataset_version`, `^[1-9][0-9]{0,8}$`). It is not
  converted to an integer, which lets (`catalog_dataset_id`, `catalog_dataset_version`)
  reference `catalog_datasets` by foreign key.
- **Redacted storage bound:** stored `text` and `note` allow twice the §4.4 limits (4,000 /
  2,000 code points), because `redaction-1` tokens can be longer than the text they replace.
  The raw input limits (2,000 / 1,000) are unchanged and are enforced on raw input before
  redaction.
- **Not database-enforced; mandatory importer checks (M18-03):** the database checks
  existence and dataset-version scoping, but not these rules:
  - equality of the snapshot triple with the stored `raw_catalog_records` row
    (`source_key = product_id`, `raw_sha256`);
  - the evidence-to-product relation and that the evidence target exists for its
    `evidence_type`;
  - the cross-table time rules (§3.1), and nothing later than the import time;
  - an inspection only on a `received` or `closed` return;
  - consistency of a complaint's `order_id` and `product_id` given without `order_item_id`.

  M18-03 must check all of them in Phase A and recheck them under the advisory lock in
  Phase B (§4.3).
- **Database backstops that are enforced:**
  - composite foreign keys per dataset version;
  - an item's product agrees with a review or complaint that names that item;
  - one shipment per order;
  - natural keys and the 152-character evidence key;
  - CHECKs;
  - append-only triggers;
  - the deferred quantity trigger. It serializes writers per order item and refuses
    REPEATABLE READ, so commits must use READ COMMITTED (§4.3).
- **Privileges** (`returns-grants-1`, §2.1):
  - The API role's pre-M18 privileges are preserved through an explicit table list; nothing
    pre-M18 is revoked.
  - The API role has no privilege on `return_import_file_contents`.
  - The importer has SELECT + INSERT on M18 tables, INSERT only on raw contents, and
    column-level catalog SELECT only.
  - The script additionally refuses superuser roles, members of the table owner, table
    owners, and holders of default privileges.
- **Status-dependent field errors** (mapping for the M18-03 `RowError`):

  | Case | Code |
  |---|---|
  | A status-required field is missing (`delivered_at` when `delivered`; `received_at` when `received`) | `required` |
  | The status forbids a field that is present (`delivered_at` when not `delivered`; `received_at` on `cancelled`) | `format` |
  | Row-local time order (`shipped_at > delivered_at`, `requested_at > received_at`) | `temporal_order` |
  | A complaint without `order_id`, `order_item_id` or `product_id` | `required` |
