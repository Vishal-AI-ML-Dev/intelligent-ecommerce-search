# Milestone 13 — Listing normalization

## Status

**M13: complete for the approved scope, on synthetic inputs and scratch databases only**
(M13-4 closure, 2026-10-21), with one verification caveat (section 6). Rules version
**`listing-norm-1`** is released with rules digest
`3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db`.

This is a statement about specified, tested behaviour. It is **not** a claim of:

- normalization accuracy on real seller listings (every input used is synthetic and
  hand-built);
- any search-quality improvement (M13 does not feed the catalog or search);
- production readiness, volume or production concurrency;
- a deployment: the development database was never migrated to `0005` and holds no M13 data.

M12 stays closed. M27 stays excluded. M14 has not started.

The batch-by-batch record, every command run and every result is in `docs/m13-progress.md`.

| Item | Status |
|---|---|
| Raw intake contract | Implemented and unit-tested (`normalization/intake.py`) |
| Deterministic normalizer and checks | Implemented and unit-tested; pure, offline (`normalizer.py`, `fields.py`, `extraction.py`, `rules.py`, `models.py`) |
| Append-only versioned persistence | Implemented; verified on disposable scratch PostgreSQL databases (`store.py`, `models/listings.py`, migration `0005`) |
| Command line | `normalize` (offline), `submit`, `show` (read-only). No HTTP endpoint (owner decision D1) |
| Real seller data, accuracy, search impact | **Not measured.** Out of scope |

## 1. What it does

```text
one-line file --(CLI file rule)--> raw listing bytes
  -> intake contract (accept exactly, or reject with a named reason)
  -> pure deterministic normalization under listing-norm-1
       attribute candidates (with exact source evidence and rule id)
       findings (conflicts, contradictions, compatibility, missing, empty, unsupported)
  -> optional: append-only storage of the exact raw bytes, the listing version and the run
```

- Raw seller input is kept exactly as received; nothing is repaired or overwritten.
- Normalization never chooses a winning value, never invents a value and never rewrites
  uncertain model names. Missing, empty, unsupported, uncertain and invalid values are each
  explicit.
- Brand aliases come from the M2 tables, read-only; none were added.
- Text extraction from title/description is limited to RAM and storage patterns and produces
  unverified candidates only.
- Nothing reads or writes the catalog, search or embedding tables.

## 2. Contracts

**Raw listing (intake).** One raw listing is the exact `bytes` of one JSON object: 1 to 65,536
bytes, strict UTF-8, no byte-order mark, scalar values only. Duplicate keys, keys that collide
after trim + casefold, blank keys, NaN/Infinity, nested values and lone surrogates are rejected
with a named `IntakeReason`. Identity is the exact `seller_id` + `seller_listing_ref` (bounded,
never trimmed or casefolded). Numbers keep their exact source literal.

**Hashes.**
- `raw_sha256`: SHA-256 of the exact raw listing bytes. Key order or whitespace changes it.
- `output_sha256`: SHA-256 of the canonical JSON of `rules_version`, `rules_digest`,
  `taxonomy_version`, `candidates` and `findings`. It excludes raw bytes, identity, version
  numbers, ids and timestamps.

**Rules.** `RULES_VERSION = "listing-norm-1"`, 17 registered rules. `rules_digest()` covers the
M13 tables and patterns, the relevant M2 tables, M2 hard bounds and a fixed-probe fingerprint
of the M2 parsers. It does **not** cover M13's own code; that is guarded by literal-expected
behaviour-regression tests (`tests/unit/normalization/test_listing_behaviour_guard.py`), which
detect only changes to the outcomes they probe.

## 3. Boundary details (checked in M13-4)

### 3.1 CLI input and the optional final newline

`read_listing_file` (`normalization/cli.py`) opens the file in binary mode (no newline
translation, no decoding) and applies exactly this framing:

| File content | Raw listing bytes (preserved, stored and hashed) | Result |
|---|---|---|
| `L` (no line end) | `L` | accepted if `L` passes intake |
| `L` + `\n` | `L` | identical to the line above: same `raw_sha256`, same version |
| `L` + `\r\n` | `L` | identical to the lines above |
| `L` + `\r` | — | rejected (a line break other than the single final LF/CRLF) |
| `L` + `\n\n`, two listings, or pretty-printed JSON | — | rejected; there is no bulk import |
| `L` with leading/trailing spaces or tabs | `L` exactly, spaces and tabs included | preserved and hashed; a different `raw_sha256` from the unpadded form |
| more than 65,536 + 2 bytes | — | rejected as `too_large` |

Only one final LF or CRLF is file framing; every other byte, including insignificant JSON
whitespace, is part of the listing. This matches the raw-input contract fixed in M13-1
("a single final LF or CRLF is not part of the listing") and is unchanged in M13-4. Unit tests:
`test_one_final_line_terminator_is_not_part_of_the_listing` and the rejection cases in
`tests/unit/normalization/test_listing_cli.py`; the M13-4 smoke test also confirms it
end-to-end (section 6).

Consequence to be aware of: the framing belongs to the CLI only. The Python API
(`parse_raw_listing(data)`) treats `data` as the listing exactly, so `L\n` passed directly to the
API would be a different raw listing (different `raw_sha256`, and a new version if submitted)
from the same file read by the CLI. The CLI is the only supported entry point in M13.

### 3.2 Historical reads still depend on today's intake parser

`show` reads stored rows as recorded and never re-runs old rules, re-validates against today's
rule registry, or compares a historical rules digest with today's. Two checks in
`store.verify_integrity` do, however, run current code on historical data:

1. **Identity check:** the stored raw bytes are parsed with **today's**
   `intake.parse_raw_listing` and the identity it returns must equal the stored identity. If a
   future intake change rejected input that an older intake accepted, `show` would report that
   version as an integrity failure (`stored raw input does not pass intake (<reason>)`) and exit
   1, even though the stored data is intact.
2. **Output-hash check:** stored rows are re-encoded with **today's** `rules.canonical_json`
   (sorted keys, compact separators, UTF-8 without ASCII escaping) to reproduce
   `output_sha256`. A change to that encoding would make every stored run fail the check.

Neither has happened: both functions are unchanged since `listing-norm-1` was fixed. Any future
change to either needs a deliberate decision (for example keeping the old function for
historical verification). This is tested only on synthetic data
(`test_read_is_as_recorded_for_a_historical_run`, `test_integrity_failures_are_detected_on_read`,
`test_raw_tampering_is_blocked_by_the_database_and_detected_on_read`).

## 4. Storage (migration `0005`)

Four append-only tables: `listing_versions` (exact `bytea` raw input with a database-checked
`raw_sha256`), `listing_normalization_runs`, `listing_candidates`, `listing_findings`. Triggers
block UPDATE, DELETE and TRUNCATE and enforce consecutive versions starting at 1 that never
repeat the previous raw input. No JSON columns, no extra indexes, no foreign key to catalog,
search or embedding tables.

- Resubmitting the latest raw input is idempotent; A→B→A gives three versions.
- A run is unique per (version, `rules_version`). Re-normalizing under the same rules version
  must reproduce the recorded digest, taxonomy version and output hash, or `submit` fails with
  `NormalizationMismatch` and writes nothing. Changed behaviour needs a new rules version.
- One transaction per submission, serialized per identity by an advisory lock keyed from
  SHA-256.
- Downgrade from `0005` drops listing history; run it only on a database you control.

## 5. Usage

```text
uv run python -m ecommerce_search.normalization normalize --file listing.json
uv run python -m ecommerce_search.normalization submit --database NAME --file listing.json
uv run python -m ecommerce_search.normalization show --database NAME --seller S --ref R [--version N]
```

`submit` and `show` have no default database and need the target migrated to head
(`0005`). Expected failures print one `error:` line and exit 1; bad arguments exit 2.

## 6. Verification evidence

Summary of the M13-4 final checks (details and earlier batches in `docs/m13-progress.md`):

| Check (2026-10-21) | Result |
|---|---|
| `uv run --offline pytest tests/unit -q -p no:cacheprovider 2>&1 \| tail -15` | pytest summary: **2 failed, 4,365 passed, 4 skipped** in 1,423.66 s. Pipeline exit 0 is `tail`'s; pytest's own exit code was **not captured**. Failures: two M10 runner tests (`run_record.json` differed between builds) |
| Targeted rerun of `tests/unit/golden/test_golden_evaluate_human_pilot_dev.py`, working tree static, `pipefail` + `PIPESTATUS` | **14 passed**, pytest exit code **0**. The two failures were not reproduced; their cause is not proven. The full suite was not rerun and is not recorded as entirely passing |
| M13 persistence + migration/schema integration tests (4 modules) on scratch databases | **86 passed**, 0 failed, 0 skipped |
| `uv run --offline ruff check .` / `ruff format --check .` | All checks passed / 353 files already formatted |
| End-to-end CLI smoke test on one scratch database (scratchpad script) | **15 passed, 0 failed**; scratch database dropped |

## 7. Limitations

- Synthetic inputs and scratch databases only. No real seller data, no accuracy measurement, no
  search impact, no volume or production-concurrency testing (6 concurrent submissions on one
  local PostgreSQL 16 server).
- Extraction is narrow (RAM and storage patterns); phrasing outside the patterns is not read.
  The `uncertain` qualifier list is a small fixed English list, so other hedges (for example
  "optional 32GB RAM") give normalized, unverified text candidates. The spaced alternative
  `8GB / 16GB RAM` yields a `16GB RAM` candidate.
- Contradictions cover only the two M2 validator rules. Shoe size/size-system pairing is
  reported only when the partner key is absent.
- `rules_digest()` covers rules and configuration, not M13 code; the behaviour guard covers
  only its probes. Findings carry no rule id.
- Historical integrity checks depend on today's intake parser and canonical JSON encoding
  (section 3.2).
- The CLI's final-newline framing differs from direct API use (section 3.1).
- `show` prints `raw_text` decoded from the stored bytes; the exact bytes are only in the
  database and `raw_sha256`.
- The offline network guard patches the Python socket API only; it is not an OS sandbox.
- One listing per `submit`; no bulk import, no HTTP endpoint. The indicative
  `POST /listings/normalize` path in `docs/MASTER_PLAN.md` was not built (owner decision D1).
- Not deployed: the development database is still at revision `0004`.
