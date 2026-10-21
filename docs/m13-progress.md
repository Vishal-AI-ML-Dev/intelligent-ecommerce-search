# Milestone 13 — Listing Normalization: progress record

Status: **complete for the approved scope, on synthetic inputs and scratch databases only
(M13-4 closure, 2026-10-21), with one verification caveat:** the full unit suite reported 2
failures in M10 runner tests, which passed on a targeted rerun with the working tree static;
their cause is not proven (see "M13-4"). M13-1 (Contracts & Rules), M13-2 (Normalization & Checks, including its
corrective batch), M13-3 (Persistence & CLI) and M13-4 (Integration & Closure) are done. Rules
version `listing-norm-1` is released with digest
`3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db`. Nothing is committed.
No accuracy on real seller listings, search improvement, production readiness or
development-database deployment is claimed. M12 stays closed and M27 stays excluded.

## Approved scope

1. Preserve raw seller listing input unchanged.
2. Give each listing an identity and versions, linked to the raw input and to the
   normalization-rule version.
3. Deterministically normalize the supported brand aliases, whitespace, units and storage
   representations.
4. Produce attribute candidates with source evidence and the rule version.
5. Check category/attribute compatibility.
6. Detect conflicting values without choosing a winner.
7. Keep missing, unsupported and uncertain values explicit. Never invent product facts, and
   never rewrite uncertain model names.

Out of scope: Jev or other LLM integration, M14 listing decisions, M15 policy-risk triage,
frontend, return intelligence, orders/payments, external datasets, new manual labelling, new
infrastructure and unrelated refactors.

## Approved decisions

| # | Decision |
|---|---|
| D1 | Command-line interface first. No HTTP endpoint in M13. |
| D2 | Identity is `seller_id` + `seller_listing_ref`: exact key names, bounded non-empty strings, validated as given and never trimmed or casefolded. |
| D3 | Seller `rating`/`review_count` stay in the raw input and are flagged `unsupported_attribute`. They are never treated as verified catalog facts. |
| D4 | "Never raises" covers expected listing-value problems only. Invalid intake is rejected explicitly. Programming errors (for example a wrong argument type) and operational failures are not caught. |
| D5 | Duplicate JSON keys are rejected. Keys that collide after trim + casefold are rejected, never silently overwritten. |
| D6 | Resubmitting the same raw input consecutively is idempotent. A→B→A keeps three versions (M13-3). |
| D7 | Versioning and regression tests cover the reused M2 normalization behaviour, not only the M13 tables. |
| D8 | Offline normalization needs no database settings, `.env`, network access or model loading. |
| D9 | Work in fixed batches: M13-1 Contracts & Rules, M13-2 Normalization & Checks, M13-3 Persistence & CLI, M13-4 Integration & Closure. |

## Contracts fixed in M13-1

**Raw input** (`normalization/intake.py`):
- One raw listing is the exact `bytes` of one JSON object: 1 to 65,536 bytes, strict UTF-8, no
  byte-order mark.
- Values must be scalars: string, number, `true`/`false` or `null`. NaN/Infinity, nested
  objects or arrays, lone surrogates, blank keys, duplicate keys and colliding keys are each
  rejected with a named `IntakeReason`. Nesting too deep for the decoder is reported as
  `invalid_json`.
- Numbers keep their exact source literal (`JsonNumber`).
- File rule (implemented in M13-3, `normalization/cli.py`): a file holds exactly one raw listing
  on one line. A single final LF or CRLF is not part of the listing; any other line break is
  rejected. There is no bulk import.

**Two different hashes:**
- `raw_sha256`: SHA-256 of the exact raw bytes. No decoding, trimming or re-encoding, so key
  order changes it.
- `output_sha256`: SHA-256 of `rules.canonical_json` (sorted keys, no extra whitespace, UTF-8
  without ASCII escaping) applied to exactly `rules_version`, `rules_digest`,
  `taxonomy_version`, `candidates` and `findings`. Candidates and findings are sorted into a
  canonical order on construction. The hash excludes raw bytes, raw hash, identity, version
  numbers, database ids and timestamps. Raw inputs that differ only in key order therefore
  share an output hash but not a raw hash, and a test asserts both.

**Output** (`normalization/models.py`):
- Frozen, strict and extra fields forbidden.
- `AttributeCandidate`:
  - `basis` is `seller_field` (the seller's own value), or `title`/`description` (an
    unverified text extraction).
  - Evidence is the exact raw key, the exact raw text, and for text bases a code-point span.
  - `rule_id` must be registered and must apply to that field and basis.
  - Only `normalized` carries a value, and the value must be canonical for its field (type,
    unit, exact decimal places, enum membership, collapsed whitespace). `unsupported`,
    `uncertain` and `invalid` keep raw evidence only.
- `Finding` has a closed set of kinds, each with its own field rules.
- `ListingNormalization` invariants:
  - There is no resolved or winning value.
  - `value_conflict` findings are required for, and only for, fields with two or more
    different normalized values.
  - "Missing" cannot be reported for a field the seller supplied.
  - Duplicate candidates and findings are rejected.
- `verify_evidence(raw)` checks that every quote and span matches the raw listing.

**Rules** (`normalization/rules.py`), `RULES_VERSION = "listing-norm-1"`:
- Field contracts are derived from the M2 core fields and the M2 per-category attribute
  models.
- The registry has 17 rules. The M13 storage-term table is included, and a bare interface
  term never implies SSD.
- The M2 brand aliases are read, never extended.
- `rules_as_dict()` embeds every relevant M2 table, the M2 hard bounds, and a behaviour
  fingerprint: the outputs of M2 `parse_decimal_measure`, `enum_parser` and `clean_text` on
  fixed probes.
- `rules_digest()` is pinned in a test. Monkeypatching an M2 table, M2 parser code, the M2
  aliases, the taxonomy version or an M13 table each changes the digest.
- Pinned digest after M13-1: `ea480531aebc0c5167fc50464f1f92925ec82d2ca30af0a70d3924ecfa55499a`.
  Superseded in M13-2 (see below).
- `listing-norm-1` stays unreleased until M13 closure (M13-4). Until then, rule changes update
  the pinned digest and are recorded here. After release, any behaviour change needs an
  explicit versioning decision. Passing the finite behaviour guard does not prove that no
  version bump is needed. Since M13-3, the store also refuses to record changed output under a
  `rules_version` it has already stored for a listing version (see M13-3).

## Fixed acceptance checklist (status after M13-3)

| # | Criterion | Status |
|---|---|---|
| 1 | Raw input preserved | **Done (synthetic)**. Pure path tested. Storage keeps the exact `bytea` with a database-checked `raw_sha256`; append-only is enforced by triggers. Verified on scratch PostgreSQL |
| 2 | Listing versions and rule versions traceable | **Done (synthetic)**. Identity versions, A→B→A, and runs recording rules version, digest, taxonomy version and output hash. Verified on scratch PostgreSQL |
| 3 | Supported normalization deterministic and repeatable | **Done (synthetic)**. Pure normalizer and offline CLI tested. Idempotent reruns verified on scratch PostgreSQL |
| 4 | Candidates carry source evidence | **Done for the pure normalizer**: every produced candidate and keyed finding passes `verify_evidence`; text candidates carry code-point spans (tested, including non-ASCII and astral characters) |
| 5 | Compatibility and contradiction cases tested | **Done (synthetic)**: conflicts, NVME/HDD and wired/wireless contradictions, incompatible attributes/subcategory/brand, unknown category (tested) |
| 6 | Unsupported/missing values handled without invention | **Done (synthetic)**: missing, null, blank, unsupported, uncertain and invalid are each explicit; no default or inferred value (tested) |
| 7 | Existing search behaviour preserved | Holding: no catalog/search source file changed. The M13 tables are separate (unit guard updated). Catalog/search table contents are unchanged by submit/show on a seeded scratch database (verified). Full suite: M13-4 |

"Done" here means the specified behaviour is implemented and covered by synthetic unit tests. It
is not a measure of accuracy on real listings and not a measured search improvement.

## M13-1 files

Added:
- `src/ecommerce_search/normalization/__init__.py`
- `src/ecommerce_search/normalization/intake.py`
- `src/ecommerce_search/normalization/models.py`
- `src/ecommerce_search/normalization/rules.py`
- `tests/unit/normalization/test_listing_intake.py`
- `tests/unit/normalization/test_listing_contracts.py`
- `tests/unit/normalization/test_listing_rules.py`
- `tests/unit/normalization/test_listing_offline.py`
- `docs/m13-progress.md`

No existing file was changed.

## M13-1 commands and results (2026-10-21)

| Command | Result |
|---|---|
| `uv run --offline pytest tests/unit/normalization -q -p no:cacheprovider` | 120 passed |
| `uv run --offline ruff check src/ecommerce_search/normalization tests/unit/normalization` | All checks passed (after fixing 7 findings: line length, loop-variable binding, one Yoda comparison) |
| `uv run --offline ruff format --check src/ecommerce_search/normalization tests/unit/normalization` | 8 files already formatted (after `ruff format` reformatted 4 new files) |
| `uv run --offline pytest tests/unit/catalog/test_catalog_taxonomy_schemas.py tests/unit/catalog/test_catalog_quality_rules.py tests/unit/search/test_query_understanding_rules.py tests/unit/search/test_query_understanding_contract.py -q -p no:cacheprovider` | 342 passed |

The full unit suite, integration tests and repository-wide Ruff were not run in this batch.

## M13-2 Normalization & Checks (2026-10-21)

`normalizer.normalize_listing(RawListing) -> ListingNormalization` is pure. It does no I/O and
needs no settings, database, network or model.

**Structured seller fields** (`normalization/fields.py`, reusing M2 `parse_decimal_measure`,
`enum_parser`, `clean_text` and `BRAND_LOOKUP`):
- Text fields collapse whitespace only. Model and series names are otherwise unchanged, and the
  brand is never read from the title.
- Brand: a known brand or M2 alias gets the canonical spelling. An unknown brand is
  `unsupported` and kept as given. No alias was added.
- Closed vocabularies (category, availability, storage, size system, gender, connectivity)
  match case- and whitespace-insensitively. Other terms are `unsupported`. There are no
  synonyms, so `In Stock` is not `in_stock`.
- Currency must be three ASCII letters and is uppercased; anything else is `invalid`.
- Measures use the M2 unit tables and precision, then the M2 hard bounds (`MEASURE_BOUNDS`).
  Since the M13-2 corrective batch, the M2 parser runs in a local exact decimal context (see
  below).
  - Unknown unit or unrecognised text → `unsupported`.
  - These are `invalid`:
    - too many decimal places, including a unit conversion with a non-integral result (for
      example `0.00048828125TB`);
    - out of bounds;
    - an unrepresentable JSON number (for example `1e999999999` or `1e-999999999`).
- `storage_type`:
  - The M13 combined terms (`NVMe SSD`, `SATA SSD`, …) give the medium plus the interface
    (`storage.term`).
  - A bare interface term (`NVMe`, `SATA`) is `uncertain`. No SSD is derived and no interface
    candidate is produced.
- `wireless`/`anc`: only JSON `true`/`false`. A string is `unsupported`; a number is `invalid`.
- A wrong JSON type for a field is `invalid`.

**Empty values (contract extension).** A supported key that is present with `null` or a blank
string now produces the finding `empty_value` (field plus exact `source_key`).
- It is a supplied key without a valid value, so it is never reported as `missing_*`.
- `verify_evidence` checks that the raw value really is null or blank.
- The model invariant treats an `empty_value` field as supplied.
- "Missing" now means only that the key is absent.

**Unsupported keys.** Identity keys are skipped. Each of these produces `unsupported_attribute`
with the exact raw key and is kept in the raw input only:
- `rating` and `review_count` (D3);
- catalog identity/provenance keys;
- unknown keys.

**Title/description extraction** (`normalization/extraction.py`):
- Only `TEXT_PATTERNS` is read:
  - `<N> GB RAM`;
  - `<N> GB|TB <storage term>`, where the term is SSD, HDD, NVMe, or a combined NVMe/SATA SSD
    term.
- Evidence is the exact matched raw text, with offsets counted in code points.
- Each pattern has bounded quantifiers and no nesting. A maximum-size (64 KiB) listing is
  tested.
- Digits are ASCII only; full-width digits and units are not read.
- A qualifier makes the match `uncertain`, and the evidence includes the qualifier. The
  qualifiers are `up to`, `upto`, `upgradable/upgradeable to`, `expandable (up) to`,
  `max(imum)` and `support(s)`.
- These are never read: alternatives such as `8GB/16GB RAM`, decimal RAM, leading zeros, a
  unit with no RAM/storage term, and any category.
- A bare `NVMe` gives an interface candidate only.
- Seller text is data. Instruction-like text has no effect beyond the same patterns: an
  injected `64GB RAM` becomes an unverified description candidate plus a conflict, never an
  override.

**Checks** (`normalization/normalizer.py`):
- `value_conflict`: raised for every field with two or more different normalized values, from
  any mix of seller field, title and description. The message lists each value with its
  bases. No winner is chosen.
- `cross_field_contradiction`: from `rules.CONTRADICTIONS`, which mirrors the M2 validators
  only:
  - NVME with a non-SSD medium (HDD);
  - `wired` with `wireless=true`;
  - `bluetooth`/`wireless_2_4ghz` with `wireless=false`.

  It applies across seller and text candidates. NVME without any medium is not a
  contradiction, and SATA with HDD is not one either.
- `missing_required`: a required key is absent.
- These checks run only when the seller's category normalized:
  - `incompatible_attribute`: a supplied attribute key is not in the category model. This
    includes empty or invalid values and the interface derived from `storage_type`. Text
    candidates are not flagged.
  - `incompatible_subcategory`;
  - `brand_category_mismatch` (known brands only);
  - `missing_expected`.
- Otherwise exactly one `compatibility_unchecked` finding is emitted, and none of the four
  findings above.
  - It states why: the category is absent, null/blank, not a string, or not in the taxonomy.
  - It says that the attribute, subcategory, brand-category and expected-field checks were not
    performed.

**Versioning.** `listing-norm-1` stays unreleased, since nothing has been persisted. The pinned
digest changed transparently:
- before (M13-1): `ea480531aebc0c5167fc50464f1f92925ec82d2ca30af0a70d3924ecfa55499a`
- after (M13-2): `ca0a679f46bedd5076efc3846dd865660b85714b9ce9ef08020a3720dccd776e`

What changed in the digest:
- Added: `text_patterns`, `text_storage_terms`, `storage_type_uncertain_terms`,
  `measure_bounds`, `precision_probe_dp`, `empty_value`, `text_extraction` and
  `contradictions`.
- Updated: the descriptions of `storage.term`, `title.ram_pattern` and
  `title.storage_pattern`.
- Unchanged: the registry still has 17 rules, and the M2 tables are untouched.

New digest-mutation tests cover text patterns, text storage terms, measure bounds and
contradictions. `MEASURE_FIELDS` and `MEASURE_PARSERS` were renamed from private to public;
the dumped content is unchanged by the rename.

A regression pin of the output hash for one fixed synthetic listing (`ac4d8471…24d80`) was
produced by this implementation and checked by eye. It guards against change; it is not an
independent oracle. (Both pins were updated again in the corrective batch below.)

**Offline verification.** `test_listing_offline.py` now runs the normalizer on synthetic
listings in an isolated subprocess: `python -I`, a temporary working directory, and database,
model and provider environment variables removed.
- Before anything is imported, the child replaces the socket connect, bind, sendto and
  name-lookup entry points with functions that record the attempt and raise.
- The test asserts:
  - zero network attempts;
  - no database, HTTP, model, search or dotenv module loaded;
  - identical output hashes across two child processes and the parent process. `-I` ignores
    PYTHONHASHSEED, so each child has its own random hash seed.
- A second test confirms that the guard itself refuses a connection.

## M13-2 files

Added:
- `src/ecommerce_search/normalization/fields.py`
- `src/ecommerce_search/normalization/extraction.py`
- `src/ecommerce_search/normalization/normalizer.py`
- `tests/unit/normalization/test_listing_normalizer.py`
- `tests/unit/normalization/test_listing_extraction.py`
- `tests/unit/normalization/test_listing_checks.py`

Changed (all uncommitted M13-1 files):
- `src/ecommerce_search/normalization/rules.py`: new tables, patterns and digest entries; the
  measure tables were made public.
- `src/ecommerce_search/normalization/models.py`: the `empty_value` kind, its contract and
  invariant, `is_empty_value`, and the evidence check.
- `tests/unit/normalization/test_listing_rules.py`: the new pin, plus new table and mutation
  tests.
- `tests/unit/normalization/test_listing_contracts.py`: `empty_value` contract tests.
- `tests/unit/normalization/test_listing_offline.py`: network refusal and the normalizer run.
- `docs/m13-progress.md`

No catalog, taxonomy, M2 parser, search, M12 or `.claude/` file was changed.

## M13-2 commands and results (2026-10-21)

| Command | Result |
|---|---|
| `uv run --offline pytest tests/unit/normalization -q -p no:cacheprovider` | First run: 253 passed, 1 failed. The output-hash pin was left unset on purpose until the output was checked by eye. After pinning: **254 passed** |
| `uv run --offline ruff format src/ecommerce_search/normalization tests/unit/normalization` | 5 files reformatted |
| `uv run --offline ruff check src/ecommerce_search/normalization tests/unit/normalization` | First run: 4 findings (two E741 `O` aliases, S311 for the seeded test shuffle, one SIM300). Fixed by renaming, a justified `noqa`, and `--fix`. Final: **All checks passed** |
| `uv run --offline ruff format --check src/ecommerce_search/normalization tests/unit/normalization` | 14 files already formatted |
| `uv run --offline pytest tests/unit/catalog/test_catalog_taxonomy_schemas.py tests/unit/catalog/test_catalog_quality_rules.py tests/unit/search/test_query_understanding_rules.py tests/unit/search/test_query_understanding_contract.py -q -p no:cacheprovider` | **342 passed** |

The full unit suite, integration tests and repository-wide Ruff were not run in this batch.

## M13-2 corrective batch (2026-10-21)

An independent verification found two defects before M13-3. The verification compared
hand-written expected outcomes with normalizer output, using a scratchpad script outside the
repository: 37 probes passed, 2 failed and 4 were unspecified.

**1. Decimal precision loss (fixed in M13 only).**
- Root cause: M2 `parse_decimal_measure` multiplies by the unit multiplier in the current
  decimal context (default 28 digits) before its precision check. A value such as
  `"15.600000000000000000000000000001"` was rounded to `15.6` and normalized, and so was the
  JSON number `16.00000000000000000000000000001` for `ram_gb`. This contradicted "excess
  precision is not normalized".
- Fix: `fields.measure_outcome` now runs the unchanged M2 parser inside
  `decimal.localcontext(...)`. The context has precision `rules.MEASURE_DECIMAL_PREC`
  (2 × 65,536 + 64) and traps `Inexact`, `InvalidOperation`, `Overflow` and `DivisionByZero`.
  Every intake-bounded value is therefore computed exactly or raises; nothing is silently
  rounded.
- Scope:
  - This is the only M13 numeric path. It covers every seller measure (price, RAM, storage,
    screen, weight, battery mAh and hours, shoe size) and the title/description extraction.
  - Booleans are still rejected before parsing.
  - The global decimal context, M2, catalog and search code are unchanged.
- Outcome rule: a string that M2 accepts at `dp = len(string)` but not at the field precision
  is `invalid`; a string M2 does not recognise is `unsupported`. The fixed 12-decimal-place
  probe is gone. As a result, long digit strings (for example 60,000 nines) are now
  recognised, then `invalid` by bounds; they were previously `unsupported`.
- New literal-expected tests are in `tests/unit/normalization/test_listing_precision.py`
  (40 parametrized cases plus 3 further tests). They cover:
  - the two probes;
  - long valid and invalid decimals, both as strings and as JSON numbers;
  - trailing zeros;
  - TB→GB multiplication, including integral and non-integral results and the 65,536 GB bound;
  - price precision, bounds, the ₹ prefix and JSON exponents;
  - extreme exponents;
  - booleans;
  - that unknown units stay `unsupported`;
  - extraction;
  - that the global context is unchanged.

**2. Versioning protection, stated accurately.**
- *Rule/configuration digest* (`rules_digest()`): covers the M13 tables and patterns, the M2
  tables and the M2 parser-probe fingerprint. It does **not** cover M13's own code. The
  `rules.py` docstring previously claimed otherwise and has been corrected.
- *Behaviour-regression coverage*: the new `test_listing_behaviour_guard.py` holds 11
  synthetic listings with literal expected candidates and findings. It covers:
  - the currency format;
  - failure outcomes (unsupported, invalid and uncertain);
  - precision;
  - qualifier extraction and spans;
  - contradiction and conflict messages;
  - `empty_value` and `compatibility_unchecked`;
  - unsupported-key messages;
  - category checks.

  It is test-only and not part of `rules_digest()` or `output_sha256()`, so it is not
  circular. Five code-only mutations are each shown to leave the rules digest unchanged while
  the guard reports mismatches: the inexact (pre-fix) measure parser, enum outcome, lenient
  currency, message format, and the unchecked-message text.
- *Not covered*: any code change that does not alter one of the probed outcomes. No
  qualifier-specific mutation is demonstrated, because qualifier handling is inline code and
  only the probes cover it.
- *Implementation identity*: none recorded. No source hashing or runtime inspection was added.

**Pins (listing-norm-1 still unreleased; nothing persisted):**
- Rules digest:
  - before: `ca0a679f46bedd5076efc3846dd865660b85714b9ce9ef08020a3720dccd776e`
  - after: `3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db`
  - Why: `precision_probe_dp` was replaced by `measure_arithmetic` (precision, traps and
    outcome classes).
- Fixed-listing output hash:
  - before: `ac4d8471…24d80`
  - after: `30640df1fd36215f6099a60e17ed7a657933bad2dbb852686b7e7b8e1844d04a`
  - Why: only the embedded `rules_digest` changed. Substituting the old digest reproduces the
    old hash, so the candidates and findings for that listing are unchanged.

**Independent probes after the fix:**
- The original 43 probes were re-run with unchanged expectations (checked by diff against a
  backup). Both P14 failures now pass.
- 15 new probes were added, covering units, trailing zeros, price, booleans, context and
  extraction.
- Total: **54 PASS, 0 FAIL, 4 UNSPECIFIED**. The unspecified probes, with what they observed:
  - P8b, size with a null `size_system`: `empty_value` only.
  - P8c, size with an unsupported `size_system`: only the `unsupported` candidate.
  - P9b, "optional 32GB RAM": a normalized candidate.
  - P9d, "8GB / 16GB RAM": a normalized `16GB RAM` candidate.

**Files changed:**
- `src/ecommerce_search/normalization/fields.py`
- `src/ecommerce_search/normalization/rules.py`
- `tests/unit/normalization/test_listing_rules.py`: the digest pin, and a test tying
  `MEASURE_DECIMAL_PREC` to the intake limit
- `tests/unit/normalization/test_listing_normalizer.py`: the output pin
- `docs/m13-progress.md`

Files added:
- `tests/unit/normalization/test_listing_precision.py`
- `tests/unit/normalization/test_listing_behaviour_guard.py`

No catalog, search, M12 or `.claude/` file was changed.

| Command | Result |
|---|---|
| `uv run --offline pytest tests/unit/normalization -q -p no:cacheprovider` | First run: 301 passed, 2 failed (the rules-digest and output-hash pins, as expected after the digest change). After updating the pins: **304 passed** |
| `uv run --offline ruff format src/ecommerce_search/normalization tests/unit/normalization` | 3 files reformatted |
| `uv run --offline ruff check src/ecommerce_search/normalization tests/unit/normalization` | All checks passed |
| `uv run --offline ruff format --check src/ecommerce_search/normalization tests/unit/normalization` | 16 files already formatted |
| `uv run --offline pytest tests/unit/catalog/test_catalog_taxonomy_schemas.py tests/unit/catalog/test_catalog_quality_rules.py tests/unit/search/test_query_understanding_rules.py tests/unit/search/test_query_understanding_contract.py -q -p no:cacheprovider` | **342 passed** |
| `uv run --offline python <scratchpad>/verify_m13.py` | 54 PASS, 0 FAIL, 4 UNSPECIFIED |

The full unit suite, integration tests and repository-wide Ruff were not run.

## Limitations

- All inputs are synthetic and hand-built. They establish specified behaviour only. They make
  no claim about normalization accuracy on real listings or about any search improvement.
- Extraction is deliberately narrow: RAM and storage patterns only.
  - Screen size, battery, processor and other mentions in text are not extracted.
  - Phrasing outside the patterns is not read at all, rather than being reported as
    uncertain. Examples: `8GB/16GB`, full-width digits, more spaces than the patterns allow.
- The qualifier list for `uncertain` is a small fixed English list. Other hedges are extracted
  as normalized title/description candidates and can produce a conflict. For example,
  "optional 32GB RAM" gives `32GB RAM` → 32. Such candidates are never verified
  specifications.
- Only the unspaced `8GB/16GB RAM` alternative is suppressed. The spaced form `8GB / 16GB RAM`
  yields a normalized `16GB RAM` → 16 candidate.
- Measure precision (fixed in the corrective batch): values are exact for anything intake
  admits. Work is bounded by `MEASURE_DECIMAL_PREC` (131,136 digits).
  - A JSON exponent within that bound (for example `1e100000`) is expanded, then rejected by
    the hard bounds.
  - A larger exponent is rejected by the decimal context without being expanded.
  - Both outcomes are `invalid`.
- Contradictions cover only the two M2 validator rules.
- Shoe size and size system: M2 requires both together. M13 covers this only when the partner
  key is absent (`missing_expected size` or `size_system`). When the partner is present but
  null, blank, invalid or unsupported, the problem is recorded on that key (`empty_value` or
  the candidate outcome), but no finding links it to the other field. No pairing policy has
  been added.
- `rules_digest()` is a rule/configuration digest only. M13's own code is guarded by the
  behaviour-regression tests, which detect only changes to the outcomes they probe (see the
  corrective batch below).
- Findings carry no rule id. They are traced through the field, related field, exact key and a
  deterministic message.
- The M2 fingerprint still covers fixed probes only.
- The network guard patches the Python socket API in the child process. It is not an OS-level
  sandbox, so native code that bypasses `socket` would not be caught. No such code is imported.
- Reading back output produced under an older rules version: resolved in M13-3. Stored runs
  are read as recorded and checked against their own hashes (see M13-3).

## M13-3 Persistence & CLI (2026-10-21)

### Checklist

| Item | Status |
|---|---|
| ORM models + schema-only migration 0005 | Done. Model/migration DDL equivalence test passes (offline SQL rendering) |
| Atomic, versioned persistence (`normalization/store.py`) | Done. Verified on scratch PostgreSQL |
| `normalize --file F` (offline, deterministic) | Done. Unit-tested, including a clean-process run with network refused and no database/settings modules loaded |
| `submit --database NAME --file F` | Done. Unit-tested before database access; end-to-end on scratch PostgreSQL |
| `show --database NAME --seller S --ref R [--version N]` (read-only) | Done. Unit-tested failure paths; end-to-end on scratch PostgreSQL |
| Guard tests updated deliberately | Done (exact-table set, head revision, migration SQL range) |
| Integration execution | **Done**: 86 passed, 0 failed, 0 skipped (see "M13-3 database verification") |

### Migration identity

- Revision `0005` (`down_revision = "0004"`), file
  `migrations/versions/0005_create_listing_normalization_history.py`. The new head is `0005`.
- Schema only: it creates four tables, two trigger functions and nine triggers. It inserts no
  rows. Its DDL is literal, with no application imports.
- Downgrade drops the four tables (and their triggers) and both functions. That destroys
  listing history, so run it only on a database you control. DROP does not fire the
  append-only triggers, so downgrade and scratch `DROP DATABASE` cleanup both still work.

### Schema

| Table | Holds | Key constraints |
|---|---|---|
| `listing_versions` | `seller_id`, `seller_listing_ref`, `version_number`, exact `raw_bytes` (`bytea`), `raw_sha256`, `submitted_at` | unique (identity, version); identity length 1–128; version ≥ 1; 1–65,536 bytes; `raw_sha256 = encode(sha256(raw_bytes), 'hex')` |
| `listing_normalization_runs` | version FK, `rules_version`, `rules_digest`, `taxonomy_version`, `output_sha256`, candidate/finding counts, `created_at` | unique (version, `rules_version`); hash formats; non-blank versions |
| `listing_candidates` | run FK, `ordinal` and every `AttributeCandidate` field as recorded | unique (run, ordinal); `basis`/`outcome`/`value_type` CHECKs from the M13 enums; span shape; a value only when `normalized` |
| `listing_findings` | run FK, `ordinal` and every `Finding` field as recorded | unique (run, ordinal); `kind` CHECK lists all ten finding kinds |

- Triggers:
  - `listing_history_append_only()`: blocks UPDATE, DELETE and TRUNCATE on all four tables.
  - `listing_versions_check_sequence()`: per identity, the first version must be 1, versions
    must be consecutive, and a new version must not repeat the previous raw input.
- Constraints only. There are no JSON columns, no extra indexes, and no foreign key to the
  catalog, search or embedding tables.

### Persistence behaviour (`submit_listing`)

- Normalization runs first, as a pure step: `normalize_listing` plus `verify_evidence`.
- Then one transaction:
  1. `pg_advisory_xact_lock(listing_lock_key(identity))`. The key is the first 8 bytes of
     SHA-256 over `b"listing-normalization:" + canonical_json([seller_id, seller_listing_ref])`,
     never Python's `hash()`.
  2. Read the identity's latest version. Raw bytes identical to it: reuse that version.
     Otherwise insert version n+1 (A→B→A gives 1, 2, 3).
  3. Look up a run for (version, current `rules_version`).
     - None: insert the run, its candidates and its findings.
     - Present: the rules digest, taxonomy version and output hash must be identical
       (idempotent). Otherwise raise `NormalizationMismatch` ("needs a new rules version"),
       and nothing is written.
- Any failure rolls back the whole submission.
- If a listing table is missing, `NotMigrated` asks for `alembic upgrade head`; nothing is
  written.
- The unique constraints back the lock if something bypasses it.
- A new `rules_version` on an unchanged raw input adds a second run and keeps the old one.

### Historical reads (`load_version`, `read_listing`, `verify_integrity`)

- Stored rows come back as plain frozen records (`StoredCandidate`, `StoredFinding`,
  `StoredRun`, `StoredVersion`). They are not re-validated against today's rule registry or
  contracts, so a run recorded under a retired rule id or field still reads.
- `read_listing` runs in a `SET TRANSACTION READ ONLY` transaction.
- Integrity checks:
  - the raw bytes match `raw_sha256`;
  - the raw input still passes intake, and its identity equals the stored identity;
  - each run's row counts match, and its ordinals are contiguous from 0;
  - each run's rows, rebuilt in stored order, reproduce its `output_sha256`. They are rebuilt
    into exactly the structure the output hash covers.
- Old rules are never re-run. A historical digest is not compared with today's digest.
  `show` reports `same_as_current_rules` for information only.

### CLI (`python -m ecommerce_search.normalization`)

- `normalize`:
  - offline;
  - prints sorted, indented UTF-8 JSON of identity, `raw_sha256`, `output_sha256` and the
    normalization;
  - imports no database, settings or store module (tested in a clean `python -I` process).
- `submit`:
  - intake runs before any settings or database access;
  - prints `target database: NAME` before writing;
  - there is no default database.
- `show`: read-only. `--version N` must be a positive integer; the default is the latest.
- Expected failures print one `error:` line and exit 1. Bad arguments exit 2.

### M13-3 files

Added:
- `src/ecommerce_search/models/listings.py`
- `migrations/versions/0005_create_listing_normalization_history.py`
- `src/ecommerce_search/normalization/store.py`
- `src/ecommerce_search/normalization/cli.py`
- `src/ecommerce_search/normalization/__main__.py`
- `tests/unit/normalization/test_listing_cli.py`
- `tests/unit/normalization/test_listing_store_records.py`
- `tests/integration/test_listing_persistence.py`

Changed:
- `src/ecommerce_search/models/__init__.py`: registers the four models.
- `tests/unit/catalog/test_catalog_models_metadata.py`: the exact table set now includes the
  four M13 tables. The no-JSON, vector-column and index rules are unchanged.
- `tests/integration/test_catalog_schema.py`: the offline migration SQL range is now
  `0001:0005`.
- `tests/integration/test_dense_migration.py` and `tests/integration/test_search_migration.py`:
  the head is now `0005`. The explicit `0003`/`0004` steps are unchanged.
- `docs/m13-progress.md`

Unchanged: catalog, taxonomy, M2 parsers, search, embeddings, API, migrations 0001–0004, the M12
proposal (`docs/m12-diagnostic-tooling.md`, already modified before this batch) and `.claude/`.

### M13-3 commands and results (2026-10-21)

| Command | Result |
|---|---|
| `uv run --offline python -m ecommerce_search.normalization normalize --file <scratchpad>/one.json` (synthetic) | Printed the normalization JSON; a two-line file gave `error: … exactly one listing on one line …`, exit 1 |
| `uv run --offline pytest tests/unit/normalization/test_listing_cli.py tests/unit/normalization/test_listing_store_records.py -q -p no:cacheprovider` | First run: crashed on Windows (a 65 KB parametrize id overflowed `PYTEST_CURRENT_TEST`). Fixed with explicit ids. Then **55 passed** |
| `uv run --offline pytest tests/unit/normalization tests/unit/catalog/test_catalog_models_metadata.py tests/unit/test_alembic_env.py -q -p no:cacheprovider` | **380 passed** |
| `uv run --offline pytest "tests/integration/test_catalog_schema.py::test_model_and_migration_constraint_definitions_are_identical" -q -p no:cacheprovider` | **1 passed** (offline SQL rendering of 0001:0005; no database connection) |
| `uv run --offline ruff check` + `ruff format --check` on all M13-3 source, migration and test files | All checks passed; 32 files already formatted (after `ruff format` and two S608 fixes) |
| Integration tests (first pass) | Not run: PostgreSQL at 127.0.0.1:5432 refused connections (Docker daemon not running). Executed later; see "M13-3 database verification" |

### M13-3 database verification (2026-10-21)

**Readiness (read-only checks first):**
- Docker daemon reachable (server 29.8.2).
- The existing container `ecommerce-search-db-1` (local image `pgvector/pgvector:0.8.6-pg16`)
  was stopped (`Exited (255)`). It was started with `docker start ecommerce-search-db-1`: no
  pull, rebuild, compose change or volume change.
- The container reported `healthy`. Server: PostgreSQL 16.15.
- Databases before the run: `ecommerce_search`, `postgres`, `template0`, `template1`. No
  leftover scratch databases.

**Isolation, checked before running:**
- Every database the four test modules write to comes from the `tests/integration/conftest.py`
  fixtures:
  - `scratch_database` and `migrated_engine`: a new `ecommerce_search_test_<12 hex>` database,
    dropped at teardown with `DROP DATABASE IF EXISTS … WITH (FORCE)`;
  - `writable_seeded_engine`: its own module-scoped scratch database with the same cleanup.
- The `postgres` maintenance database is used only for CREATE/DROP DATABASE of those names.
- `Migrator` always passes an explicit scratch name to Alembic.
- `test_model_and_migration_constraint_definitions_are_identical` renders SQL offline and does
  not connect.
- A grep of the four modules and their helpers found no engine or CLI `--database` target other
  than these scratch names.
- `test_listing_persistence.py` additionally asserts the `ecommerce_search_test_` prefix before
  each write.
- No connection string, credential or `.env` content was printed.
- Development database snapshot (read-only `psql` in the container), before and after:

  | Check | Before | After |
  |---|---|---|
  | Alembic revision | `0004` | `0004` |
  | Public tables | 11 | 11 |
  | `listing*` tables | 0 | 0 |
  | products / search documents / embeddings | 240 / 240 / 240 | 240 / 240 / 240 |

  The development database was not migrated, ingested into, truncated or written.

**Commands and results:**

| Command | Result |
|---|---|
| `uv run --offline pytest tests/integration/test_listing_persistence.py tests/integration/test_catalog_schema.py tests/integration/test_dense_migration.py tests/integration/test_search_migration.py -q -p no:cacheprovider -rA` (for per-test lines) | **86 passed**, 0 failed, 0 skipped, in 69.3 s |
| The same command exactly as requested, without `-rA` | **86 passed**, 0 failed, 0 skipped, in 70.2 s |

Per-module counts: `test_listing_persistence.py` 23, `test_catalog_schema.py` 37,
`test_dense_migration.py` 20, `test_search_migration.py` 7. No models were loaded and no
providers were called.

**Scratch cleanup:** after both runs the server lists only `ecommerce_search`, `postgres`,
`template0` and `template1`. Every scratch database was dropped. The container was left
running.

**Real-database evidence, by requirement** (all in `test_listing_persistence.py` unless
noted; each test passed):

- **Consecutive resubmission idempotency:**
  `test_consecutive_identical_submission_is_idempotent`. The second submit returns version 1,
  `version_created=False` and `run_created=False`, and the row-content digests of all four
  listing tables are unchanged. The two concurrent tests below also cover this under load.
- **A→B→A version history:**
  `test_a_b_a_creates_three_versions_and_key_order_is_a_new_version`. Versions are [1, 2, 3],
  v3 bytes equal v1 bytes, and the latest is 3. A key-order-only variant becomes v4 with the
  same output hash. `test_identity_is_exact` shows that case-different identities are
  separate.
- **Concurrency:**
  - `test_concurrent_different_submissions_get_consecutive_versions`: 6 submissions all wait
    on the held advisory lock (confirmed through `pg_locks`). They are then released and get
    versions [1..6], with 6 runs.
  - `test_concurrent_identical_submissions_store_one_version`: 6 identical overlapping
    submissions give exactly 1 version and 1 run, one creator and one output hash.
  - `test_database_enforces_version_sequence_uniqueness_and_raw_hash`: with the sequence
    trigger disabled, a duplicate version number is still rejected by
    `uq_listing_versions_identity_version`.
- **Complete transaction rollback:** `test_a_failure_leaves_no_partial_submission`, 3 cases.
  It injects a failure after `_add_run`, `_add_candidates` or `_add_findings` have flushed
  rows, for both a new version of an existing listing and a new listing. All four tables
  have identical row-content digests afterwards.
  `test_changed_output_under_the_same_rules_version_fails_loudly` also shows
  `NormalizationMismatch` with nothing written (changed findings, and a changed digest).
- **Append-only enforcement:**
  - `test_history_is_append_only`: UPDATE, DELETE and TRUNCATE … CASCADE are rejected on each
    of the four tables ("append-only listing history"), and the contents are unchanged.
  - `test_database_enforces_version_sequence_uniqueness_and_raw_hash`: a first version ≠ 1, a
    gap, a repeated previous raw input, a wrong raw hash and empty bytes are each rejected.
  - `test_downgrade_and_scratch_cleanup_still_work_with_stored_history`: with history stored,
    downgrade to `0004` and to base both succeed and remove the tables and functions;
    upgrading again works.
- **Stored-result integrity checks:**
  - `test_integrity_failures_are_detected_on_read`, 3 cases with triggers disabled only inside
    the scratch database: a tampered candidate value, a deleted finding and a changed run
    digest. Each raises `IntegrityFailure`, and `show` exits 1 with no stdout.
  - `test_raw_tampering_is_blocked_by_the_database_and_detected_on_read`: changed raw bytes
    are rejected by `ck_listing_versions_raw_sha256_matches_bytes`. Bytes and hash replaced
    together are detected as an identity mismatch.
  - `test_read_is_as_recorded_for_a_historical_run`: a run under a synthetic older rules
    version, with a retired rule id and field, reads as recorded and passes integrity.
    Normalization is patched to fail if called, and it was not called.
  - `test_submit_stores_exact_raw_bytes_and_every_record_faithfully`: exact non-ASCII and
    astral bytes, every finding kind and outcome stored, and the reloaded records equal the
    contract dump and hash.
  - `test_show_cli_reads_latest_and_specific_versions`: latest, `--version 1`, a missing
    version and a missing listing; `show` leaves the tables unchanged.
- **Catalog/search isolation:**
  `test_submit_and_show_never_touch_catalog_or_search_tables`. On a seeded scratch database
  (240 products), it compares row counts and full row-text digests of all ten
  catalog/search/embedding tables before and after six submits plus `show`; they are equal.
  `test_a_database_below_0005_refuses_cleanly` shows that a `0004` database refuses with
  `NotMigrated`, stays at `0004`, and the CLI exits 1.
- **Migration/model compatibility:**
  - `test_catalog_schema.py`:
    - `test_migrated_schema_matches_model_metadata`: autogenerate compare reports no
      differences at head `0005`;
    - `test_constraint_and_index_names_match_models_and_limits`;
    - `test_only_constraint_backing_indexes_exist_plus_the_m3_gin_index`: no extra indexes and
      no JSON columns;
    - `test_model_and_migration_constraint_definitions_are_identical`: SQL `0001:0005`;
    - `test_upgrade_downgrade_round_trip_preserves_pgvector`.
  - `test_dense_migration.py` and `test_search_migration.py`: the historical `0002`/`0003`/
    `0004` round trips are unchanged, and head is now `0005`.

**Failures and fixes:** none. No test, assertion or migration was changed during this
verification.

### M13-3 limitations

- Verified on synthetic listings and on scratch databases only. This says nothing about volume,
  real seller data or production concurrency. The concurrency tests use 6 overlapping
  submissions on one local PostgreSQL 16 server.
- The offline Alembic SQL rendering (in the DDL-equivalence test and one manual render of
  0004:0005) goes through `migrations/env.py`, which loads project `Settings`. That can read
  `.env` by design. Its contents were not opened or printed.
- Integrity verification uses today's intake parser to confirm that the stored raw identity
  matches. A future intake change that rejects old raw input would show up as an integrity
  failure and would need a deliberate decision.
- Pre-release digest changes: a scratch database holding `listing-norm-1` runs refuses changed
  output under that same version. This is intended. No persistent database holds M13 data.
- Lock keys are 64-bit. A collision between two identities only serializes them; it cannot
  merge their histories.
- `show` prints `raw_text` decoded from the stored bytes. The exact bytes are only in the
  database and `raw_sha256`.
- One listing per `submit`, by design. There is no bulk import and no HTTP endpoint.

## M13-4 Integration & Closure (2026-10-21)

### Change-set review

The complete M13 change set (tracked diffs and untracked M13 files) was reviewed against the
approved scope. It contains only M13 code, migration `0005`, M13 tests, the four deliberate
guard-test updates (table set, head `0005`, migration SQL range) and documentation. No catalog,
search, embedding, API, M2 parser or migration 0001–0004 file changed. The M12 proposal
(`docs/m12-diagnostic-tooling.md`) and `.claude/` were not touched in M13-4. The rules digest is
unchanged since the M13-2 corrective batch:
`3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db` (`RULES_VERSION =
"listing-norm-1"`, 17 rules).

### Boundary details

- **CLI final newline:** the file is read in binary mode; exactly one final LF or CRLF is
  framing and is removed; every other byte (including leading/trailing spaces and tabs) is the
  listing and is stored and hashed. A lone CR or any other line break is rejected. This matches
  the M13-1 file rule; semantics unchanged. Direct API use (`parse_raw_listing`) does no
  framing, so `L
` via the API is a different raw listing from the same file via the CLI.
  Documented in `docs/listing-normalization-m13.md` §3.1.
- **Historical reads:** `verify_integrity` uses today's `intake.parse_raw_listing` (identity
  check) and today's `rules.canonical_json` (output-hash check) on stored data. A future change
  to either could make intact history fail its integrity check; it needs a deliberate decision.
  Documented in §3.2.

### Commands and results

| Command | Result |
|---|---|
| Read-only dev DB snapshot (psql in the container, `default_transaction_read_only=on`), before | revision `0004`; 11 public tables; 0 `listing*` tables; products/search documents/embeddings 240/240/240; databases `ecommerce_search,postgres,template0,template1` |
| `uv run --offline ruff check .` | All checks passed |
| `uv run --offline ruff format --check .` | 353 files already formatted |
| `uv run --offline pytest tests/integration/test_listing_persistence.py tests/integration/test_catalog_schema.py tests/integration/test_dense_migration.py tests/integration/test_search_migration.py -q -p no:cacheprovider -rfEs` | **86 passed**, 0 failed, 0 skipped, 87.39 s (scratch databases via the conftest fixtures) |
| `uv run --offline python <scratchpad>/m13_cli_smoke.py` | **15 passed, 0 failed**. Scratch database `ecommerce_search_test_<hex>` created, migrated to head, dropped; 0 scratch databases left. Covered: `normalize` with LF / no ending / CRLF (same `raw_sha256` = SHA-256 of the listing bytes), padded spaces/tab preserved and hashed, lone CR rejected, byte-identical reruns; `submit` A → v1 created; repeated submit A (CRLF file) → v1 unchanged, run unchanged; changed B → v2 created; `show` latest (v2, raw = B, `value_conflict` present), `show --version 1` (raw = A, output hash = `normalize`'s), `--version 3` exit 1, `--version 0` exit 2; 2 versions and 2 runs stored |
| `uv run --offline pytest tests/unit -q -p no:cacheprovider 2>&1 \| tail -15` | pytest summary: **2 failed, 4,365 passed, 4 skipped in 1,423.66 s**. Background pipeline exit 0 is `tail`'s exit status; pytest's own exit code was not captured. Only the last 15 lines were kept |
| Read-only dev DB snapshot, after | identical to before |
| `uv run --offline ruff check .` / `ruff format --check .` (again, after the docs were final) | All checks passed / 354 files already formatted (exit 0 each). The count is one higher than the first run (353). Explained by a read-only check: with this configuration Ruff 0.16.9 `format --check .` also formats Markdown files (its verbose output lists `docs/listing-normalization-m13.md` as included and formatted), and `--extend-exclude docs/listing-normalization-m13.md` gives 353 again. That file was created after the first run. No Python file changed |

Skips (4, the known OS limitation): `tests/unit/golden/test_golden_freeze_queries.py:221` and
`tests/unit/search/test_model_fetch_profiles.py:252/266/396`, symlinks cannot be created by this
OS account.

**Failures (2) in the full suite:**
- `tests/unit/golden/test_golden_evaluate_human_pilot_dev.py::test_run_scores_exactly_the_recorded_lists_with_the_frozen_labels`
- `tests/unit/golden/test_golden_evaluate_human_pilot_dev.py::test_fresh_process_builds_are_byte_identical_to_the_in_process_build`

Captured error: `EvaluationError: two builds are not byte-identical: ['run_record.json']`. Only
the run record differed, not any metrics file. The M10 runner records live `git status` in the
run record (`working_tree_clean`, `uncommitted_path_count`, per-file `git_state`) and compares an
in-process build with fresh-process builds made moments later. During this suite run the working
tree was changing: M13-4 documentation files were being created and edited concurrently. The
leading hypothesis is therefore that the uncommitted-path count changed between builds. **This
is not verified** (the full failure output was not captured).

**Targeted rerun (approved by the owner; working tree kept static, no edits during the run):**

```
set -o pipefail
uv run --offline pytest tests/unit/golden/test_golden_evaluate_human_pilot_dev.py -p no:cacheprovider -rA 2>&1 | tee <scratchpad>/m10-runner.log
pytest_rc=${PIPESTATUS[0]}
```

Result: **14 passed** in 108.17 s, **`pytest_exit=0`** (pytest's own exit code), full log kept
outside the repository. Both earlier failures passed. The failures were therefore **not
reproduced with the tree static**. Concurrent documentation edits remain the leading but
**unproven** explanation. The full unit suite was not rerun, so it is **not** recorded as
entirely passing: the evidence is the original full run (4,365 passed, 2 failed, 4 skipped)
plus this targeted rerun of the failing file.

### Acceptance status

| # | Criterion | Final status |
|---|---|---|
| 1 | Raw input preserved | Met (synthetic): M13-3 database tests; smoke test stored bytes = listing bytes |
| 2 | Listing and rule versions traceable | Met (synthetic): A→B→A test; smoke test v1/v2 with runs |
| 3 | Supported normalization deterministic and repeatable | Met (synthetic): unit tests; byte-identical smoke reruns; idempotent resubmission |
| 4 | Candidates carry source evidence | Met: `verify_evidence` unit tests |
| 5 | Compatibility and contradiction cases tested | Met (synthetic): unit tests |
| 6 | Unsupported/missing values handled without invention | Met (synthetic): unit tests |
| 7 | Existing search behaviour preserved | Met with caveat: no catalog/search source changed; isolation test on a seeded scratch database; 86 integration tests passed; full unit suite 4,365 passed / 2 failed / 4 skipped, the 2 failures (M10 runner, not M13 code) not reproduced on a static-tree rerun (14 passed, exit 0) |

**Verdict: M13 is complete for the approved scope**, on synthetic inputs and scratch databases
only, with the criterion-7 verification caveat above. `listing-norm-1` is released with digest
`3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db`; after release, any
behaviour change needs a new rules version. No real-seller accuracy, search improvement,
production readiness or development-database deployment is claimed. Nothing is committed. M14
has not started.
