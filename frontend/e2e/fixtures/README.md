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
