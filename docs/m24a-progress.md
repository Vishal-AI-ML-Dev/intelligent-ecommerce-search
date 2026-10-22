# M24A — Observability for the search path and the Search page (progress)

Status: **COMPLETE** (2026-10-22): acceptance tests passed locally and in CI on `main` at
`d392472` (run below). Scope (§28 of
`MASTER_PLAN.md`): Milestone 24's requirements applied to the existing search path and the M25A
page. No new dependency, vendor, dashboard or telemetry stack; API paths and response bodies are
unchanged.

## Reused, not rebuilt

- Stage timings: every search endpoint already measures and returns `latency_ms` per stage.
- Fixed-reason-code error logs in the search endpoints (never query text).
- P50/P95/P99: the existing offline `scripts/benchmark_*.py` reports.
- The frontend's one console line per request (`frontend/src/shared/http/client.ts`).

## Added

`src/ecommerce_search/api/observability.py`, registered in `api/app.py`:

- **Request ID.** An incoming `X-Request-ID` is accepted only if it is 8–64 characters of
  `[A-Za-z0-9-]`; otherwise a UUID4 is generated. It is returned on every HTTP response,
  including 404, 422 and unhandled 500s. The ID is held in a context variable set for the request
  and reset afterwards, so concurrent requests never share one (it also reaches sync endpoints in
  the threadpool).
- **Unhandled 500.** If no response has started, the middleware sends Starlette's usual body
  (`Internal Server Error`, `text/plain; charset=utf-8`) plus the ID header, then re-raises, so
  the server's own exception handling is unchanged. No exception text reaches the client.
- **Exactly one completion log per request** (`ecommerce_search.request`, JSON):
  `event=request_completed`, `request_id`, `method` (standard methods, otherwise `OTHER`),
  `route` (the route template, `/assets/{path}` for the built UI's assets, `unmatched` when no
  route matched), `status` (null only if no response started, for example a cancelled request)
  and `duration_ms`. Never the URL, query string, body, headers or client address.
- **Request ID on every app log line.** Logging is configured as before (only when the root
  logger has no handler), with `request_id=<id>` (or `-` outside a request) added to the format,
  so the existing error logs carry it.
- **uvicorn access log.** A filter on `uvicorn.access` removes the query string and client address
  (`GET /search?q=...` would otherwise write the search text). A unit test pins the uvicorn format
  string the filter relies on.

`api/reranked.py`: one additional `reranked_search_completed` JSON line per successful reranked
search, separate from the completion line, with only existing values: reranker status and enabled
flag (the fallback state), dense status, `top_k`, candidate, reranked and result counts, and the
existing `latency_ms` stages. Never the query, tsquery, derived filters or product data.

`frontend/src/shared/http/client.ts`: each request sends a fresh `X-Request-ID`
(`crypto.randomUUID()`, or `getRandomValues` outside a secure context). The console line adds
`request_id`: the ID the server returned when a response arrived (if it matches the same pattern),
otherwise the generated one (network failure).

## Acceptance tests

- `tests/unit/test_observability.py` (25): valid IDs echoed; invalid, oversized and empty IDs
  replaced; generated IDs unique; sync endpoints see the ID; 40 concurrent async and threadpool
  requests keep their own IDs and the context is empty afterwards; one completion line per
  request with route templates only (path parameters, unknown paths and asset file names never
  logged); error logs carry the ID; 422 bodies unchanged; unhandled 500 has the plain body, the
  header and no detail in the response or logs; unusual methods use a fixed label; API paths
  unchanged; access-log lines lose query string and client address; the uvicorn format is pinned.
- `tests/unit/search/test_reranked_api.py` (+8): the summary is logged once per request for
  `applied`, `disabled`, `skipped_no_candidates` and `skipped_no_searchable_text`, by GET and
  POST, matches the response's values and timings, and the formatted output contains no query
  text.
- `frontend/src/shared/http/client.test.ts` (+6): header sent, server ID logged, generated ID
  logged when the header is missing or invalid or no response arrives, a fresh ID per request,
  and the non-secure-context fallback; request bodies are still never logged.

Privacy checks inspect the actual formatted output of the production handler and formatter, not
only record messages.

## Local verification (2026-10-22, Windows)

| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check` | PASS |
| Focused backend: observability, app, frontend serving, health, all search API test files, isolation and decision-provider probes | 615 passed (before the asset-label fix); then observability, serving and app 36 passed |
| `npm run typecheck` / `npm test` | PASS / 30 passed |
| `npm run build` + `npm run e2e:fixture` | PASS / 6 passed |

The full unit and integration suites were not repeated locally; CI runs them.

## Remaining for M24B

Jev and token/cost telemetry, confidence and review metrics, logs for the other domains,
percentiles computed from logs rather than the offline benchmarks, and correlation of request IDs
in uvicorn's own access and error lines.

## CI run

Run `39741772078` on push of `d392472`:
https://github.com/Vishal-AI-ML-Dev/intelligent-ecommerce-search/actions/runs/39741772078

| Job | Result | Evidence from the job log |
|---|---|---|
| `backend` | success (423 s) | Ruff passed; unit suite 4,886 passed, 4 skipped (the visible M23A skips), 780 deselected |
| `integration` | success (189 s) | 762 passed, 18 deselected (`real_model`) |
| `frontend` | success (207 s) | typecheck; Vitest 30 passed; build |
| `e2e-fixture` | success (66 s) | 6 passed |
