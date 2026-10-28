# Synthetic Search UI fixtures (M25A)

**Provenance: synthetic, hand-constructed test data. Not captured from any running system.**

These files are mock `POST /search/reranked` response bodies for the Search UI unit tests and the
fixture-backed Playwright smoke tests. They were generated once on 2026-10-22 from the structure
of the captured demo response `docs/demo/05-search-reranked.json`, which is left unchanged. The
query-understanding, fusion, filter and model blocks are copied from that capture. Every product
(`FIXTURE-LAP-*`), score, rank, price and latency is invented, so nothing here is a measurement,
benchmark or catalog record.

| File | Case |
|---|---|
| `synthetic-reranked-applied.json` | 5 results; reranker `applied` with depth 3, so ranks 4–5 are the unscored tail (`rerank_rank`/`rerank_score` null). One title and brand contain HTML/script text to test safe rendering; one product has no rating. |
| `synthetic-reranked-disabled.json` | 3 results in fused order; reranker `disabled`. |
| `synthetic-reranked-empty.json` | No results; reranker `skipped_no_candidates`. |
| `synthetic-reranked-no-searchable-text.json` | Query `???`; nothing ran; reranker `skipped_no_searchable_text`. |
| `synthetic-reranked-similarity-only.json` | M26A: derived from the applied fixture. Query `hello`, no applied filters, no keyword candidate (`lexical_rank` null on every result), so the similarity notice is shown. |
| `synthetic-503.json` | The fixed 503 body `{"detail": "search unavailable"}`. |

`tests/unit/test_frontend_fixture_contract.py` checks that each file is a valid response of the
existing API contract (Pydantic). `e2e/fixtures.ts` type-checks the fields the UI reads.

## Shopping assistant fixtures (M16)

**Provenance: synthetic.** Generated on 2026-10-22 by running the assistant workflow
(`ecommerce_search.assistant.workflow.answer`) with a fake retriever and a fake generator; no
search, model or generation provider was called. Product IDs (`FIXTURE-LAP-*`), prices, token
counts and the summary are invented; latencies are fixed placeholder values.

| File | Case |
|---|---|
| `synthetic-assistant-answer.json` | Answer to a 16 GB RAM request with 2 suggestions from 3 evidence records: one all verified, one whose RAM the catalog does not give (`not_verified`). Evidence carries RAM/storage values (one record has none) for the comparison table; one evidence title contains HTML text to test safe rendering. |
| `synthetic-assistant-clarify.json` | Conflicting RAM requirements; no retrieval. |
| `synthetic-assistant-no-matches.json` | Abstain: search returned nothing. |
| `synthetic-assistant-validation-failed.json` | Abstain: the draft failed validation twice (unknown product ID). |
| `synthetic-assistant-generation-off.json` | Generation disabled: labelled search results only. |
| `synthetic-assistant-generation-failed.json` | Generation failed (timeout): labelled search results only. |
| `synthetic-assistant-503.json` | The fixed 503 body `{"detail": "assistant unavailable"}`. |

`tests/unit/test_frontend_fixture_contract.py` validates them against the Pydantic
`AssistantResponse` model.

## Retailer returns fixtures (M18-05)

**Provenance: synthetic, hand-constructed test data. Not captured from any running system.**

The files in `returns/` are mock `/returns` response bodies for the retailer tab's unit tests.
They were written on 2026-10-28 from the M18 contracts (`docs/m18/contracts.md` §4.8, §7, §7.1)
and the Pydantic models; no API, database or import was run. A one-off script (kept outside the
repository) wrote them from hand-written values; its only expansion is the 200 identical-shape
error rows of the truncated report. Every ID (`FIX-*`, `FIXTURE-*`), batch UUID, hash
(`aaaa…`-style), byte size, count and time is invented. Customer, inspector and carrier
references follow the synthetic `SYN-*` / `synthetic-carrier-*` forms. The capabilities limits are
the server's published `LIMITS`. Return pages use `limit` 2 to stay small; the UI does not send
`limit`.

| File | Case |
|---|---|
| `capabilities-importer.json` / `capabilities-viewer.json` | Importer token with commit enabled / viewer token (`can_commit` false). |
| `report-valid.json` | Validate: valid, every phase completed, new/unchanged counts. |
| `report-invalid-truncated.json` | Validate: invalid, 345 errors collected, 200 returned, truncated and a lower bound; one value preview holds markup. |
| `report-invalid-lower-bound.json` | Validate: invalid after the envelope phase only; 3 errors, a lower bound, not truncated. |
| `report-imported.json` | Commit: imported, with a batch ID. |
| `report-import-invalid.json` | Commit: the 422 `invalid` body (one `conflict_existing` error). |
| `returns-page-1.json` / `returns-page-2.json` / `returns-page-empty.json` | Two pages linked by a cursor; an empty page. |
| `return-detail-conflict.json` | Customer says "arrived damaged"; the inspection says `as_new` (conflicting evidence, no flag). Comments hold markup and a prompt-injection string; every related kind is present. |
| `return-detail-not-recorded.json` | `NOT_PROVIDED`, no comments, inspections, shipment, reviews, complaints or cases; the snapshot price is seller text (`"Rs 44,999"`, §7.1 M1). |
| `batches-page.json` / `batches-page-empty.json` / `batch-detail.json` | Import history, empty history, and one CSV batch with its file metadata. |
| `error-*.json` | The fixed `{detail, code}` bodies of `unauthorized`, `retailer_access_not_configured`, `import_commit_disabled`, `cursor_mismatch`, and a 422 `invalid_parameter` with `parameters`. |

`tests/unit/test_frontend_fixture_contract.py` validates each file against its Pydantic model (exact
round trip) and checks the fixed error texts and row-error messages.
`tests/unit/returns/test_returns_fixture_privacy.py` checks that no file has any `redaction-1` match
(contracts §5.3), apart from narrowly exempted timestamps, hashes, batch UUIDs and amounts.
`e2e/fixtures.ts` type-checks the fields the UI reads.
