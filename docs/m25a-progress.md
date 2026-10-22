# M25A: buyer Search UI, progress

Status: **accepted by the owner on 2026-10-22, subject to the final diff review, and committed
with this record.** The existing backend unit-suite failures are recorded as follow-up debt
(below). M25A does not complete the original Milestone 25: M25B remains.
Scope: `docs/MASTER_PLAN.md` §28 "M25A scope"; stack: ADR-008.

## Delivered

- `frontend/`: one React + TypeScript + Vite codebase.
  - `src/app/`: shell (`App.tsx`, `app.css`).
  - `src/shared/http/client.ts`: same-origin JSON client with typed failures (network, HTTP
    status, invalid body) and one console line per request.
  - `src/domains/search/`: the Search page, product card, reranker status, the UI-facing
    response types (hand-written subset of `RerankedSearchResponse`) and a response shape check.
- FastAPI (EXTEND): optional `FRONTEND_DIST_DIR` setting. Unset: API-only, unchanged. Set: must
  contain `index.html` and `assets/`, or settings validation fails with a clear message. When set,
  `GET /` serves the page (with a Content-Security-Policy and `nosniff`) and `/assets/*` the
  hashed files, registered after every API router, with no catch-all route.
- The page calls only the existing `POST /search/reranked` with `{query}` (`top_k` omitted, so the
  server's configured default applies). No new search API, no search logic, no filter controls,
  no version selector, no routed search, no product detail or purchase flow.

## REUSE / EXTEND / CREATE

| Item | Decision | Reason |
|---|---|---|
| `POST /search/reranked`, `RerankedSearchResponse` | REUSE | The page's only backend; unchanged. |
| Server-side search logging | REUSE | Already logs fixed reason codes only, never query text. |
| `docs/demo/05-search-reranked.json` | REUSE (structure only) | Its trimmed results do not satisfy the full contract, so it is not used as a mock body; left unchanged. |
| `Settings`, `create_app` | EXTEND | Opt-in same-origin serving of the built page. |
| `frontend/` | CREATE | No frontend code existed. |
| `e2e/fixtures/synthetic-*.json` | CREATE | Contract-valid mock bodies; synthetic, labelled in `e2e/fixtures/README.md`. |

## Versions (pinned exactly; `frontend/package-lock.json`)

Node.js 24.22.1 (Active LTS line "Krypton", maintenance from 2026-10-20, end of life
2028-04-30, per the official Node.js release schedule; 24.22.1 is the latest 24.x security
release, 2026-10-20). Node 26 does not enter LTS until 2026-10-28, so it was not chosen. Node is
installed user-locally from the official `nodejs.org` zip (SHA-256 checked against
`SHASUMS256.txt`) and is not on the system PATH. npm 11.19.1 (bundled).

react / react-dom 19.3.1, vite 8.3.5, @vitejs/plugin-react 6.1.2, typescript 7.0.2, vitest 5.0.5,
jsdom 30.1.3, @testing-library/react 16.3.3, @testing-library/dom 10.4.2,
@testing-library/jest-dom 7.0.1, @testing-library/user-event 14.6.8, @playwright/test 1.64.1
(Chromium installed by `npx playwright install chromium`), @types/react / react-dom 19.3.0,
@types/node 24.19.2. Each is the current npm `latest` (or latest 24.x for `@types/node`) on
2026-10-22. Declared engine and peer ranges were checked with `npm view` (Vite 8 needs Node
`^20.19 || >=22.12`; Vitest 5 needs Node `^22.12 || ^24 || >=26` and Vite 6–8; jsdom 30 needs
Node `^24.15`). `npm audit` after install: 0 vulnerabilities.

## Checks (run 2026-10-22, Windows 11, local)

| Check | Kind | Result |
|---|---|---|
| `npm run typecheck` | static | PASS |
| `npm test` (Vitest) | unit | PASS: 24 tests |
| `npm run build` | build | PASS; the built `index.html` has no inline script or style |
| `npm run e2e:fixture` | **fixture-backed browser** (mocked API, page served by FastAPI) | PASS: 6/6 |
| `npm run e2e:live` | **live-stack browser** (no mocks) | PASS: 1/1 (the first attempt was not run, see below) |

Bundle note: the first fixture and live browser runs used a build made before a final
`client.ts` change (console log format only). The production build was then rebuilt. On
2026-10-22, during the final review, typecheck, Vitest (24/24), fixture Playwright (6/6) and live
Playwright (1/1) were all re-run against the current build and passed. Those are the results
recorded here.
| `pytest tests/unit/test_frontend_serving.py tests/unit/test_frontend_fixture_contract.py` (+ app/settings tests) | unit | PASS: 190 tests |
| Ruff check and format check | lint | PASS |
| Full backend unit suite (`-m "not integration and not real_model"`, run once) | unit | 4851 passed, 4 skipped, **2 failed**: both pre-existing, not caused by M25A (see "Backend suite failures") |

Fixture browser cases: ranked cards in response order with prices as decimal strings, applied
reranking with an unscored tail (`rerank_score` null), disabled reranking, both skipped
statuses (empty state), loading state, HTTP 503 with a fixed message. Each also checks that no
page error or dialog occurs, that catalog HTML is rendered as text, and that console output is
only the client's request line (no query, no body).

What the checks do **not** prove:

- The fixture contract test proves that the fixtures are valid API responses. It does not prove
  that the hand-written TypeScript types match the API. The typecheck only proves that the
  fixtures supply the fields the UI reads.
- The `dist` grep for secret-like strings is a supplemental check, not proof that the bundle
  holds no secrets. The real control is that the frontend reads no environment variables
  (`import.meta.env` is unused) and receives no credentials.

## Live-stack check

`e2e/live.spec.ts` searches the seeded query `laptop 8gb 256 ssd` through the served page. It
then asserts on the response to the browser's own request: status 200, at least one result,
reranker `applied`, and the rendered product IDs, order and prices equal to that response. It
uses only the local deterministic provider and the local cross-encoder, so it makes no
chargeable provider call (`/search/reranked` never calls Jev).

First attempt (2026-10-22): **not run.** The Docker daemon was stopped and PostgreSQL on
127.0.0.1:5432 refused connections.

Second attempt (2026-10-22, owner-authorized): Docker Desktop was started (user-local install,
no elevation), then only the existing `db` service (`docker compose up -d db`, existing
`ecommerce-search_pgdata` volume, no configuration change). The prerequisites were then
inspected read-only:

- Database `ecommerce_search` is at Alembic revision `0004`. Search needs `0001`–`0004`; `0005`
  and `0006` add only listing tables, which search does not read. No migration was run.
- 240 products, 240 `product_search_documents`, and 240 `product_embeddings` for
  `sentence-transformers/all-MiniLM-L6-v2` at `1110a243…`.
- Snapshot files are present for the pinned embedding revision (`1110a243…`) and reranker
  revision (`233902d2…`): config, tokenizer and `model.safetensors`.

`npm run e2e:live`: **PASS (1 test)**. Playwright started the existing application (uvicorn,
`FRONTEND_DIST_DIR=frontend/dist`) and stopped it afterwards. The page got a 200 for its own
search request, with at least one result and reranker `applied`. The rendered product IDs, order
and prices equalled that response. No migration, seeding, embedding, model download or data
change was made. The `db` container was left running.

## Backend suite failures (pre-existing, not caused by M25A)

1. `tests/unit/catalog/test_catalog_models_metadata.py::test_metadata_has_exactly_the_m2_and_m3_tables_and_search_columns_only_in_the_index_table`.
   **Confirmed cause:** the four tables `listing_decision_runs`, `listing_decision_proposals`,
   `listing_decision_probabilities` and `listing_decision_reasons` are registered on the shared
   metadata. They were added with migration `0006` in commit `a725bfc` (M14-5), but
   `EXPECTED_TABLES` lists only the M2–M13 tables. **Baseline:** the same test fails identically
   on `7567654` (pre-M25A). That run used an isolated `git archive` extract in a scratch
   directory and the existing virtual environment, with `PYTHONPATH` set to the extract's `src`.
   M25A changes no model or metadata.
   Smallest justified fix, not applied (separate scope): add the four M14 tables to
   `EXPECTED_TABLES` with a comment. Do this only after checking that they meet the test's other
   assertions (no JSON, vector or search columns, and no non-constraint index), and make no
   other change to those assertions.
2. `tests/unit/test_unit_isolation.py::test_whole_unit_suite_passes_without_a_database_password`.
   This test runs the same unit suite in a child process and asserts that its exit code is 0. The
   child's output was not saved, so its exact failure list is **unverified**. The child runs the
   failing test above, and that test fails deterministically on this tree, so the child cannot
   exit 0 while it fails. Whether the child also had other failures is unknown.

The full backend unit suite is therefore **not green**.

## Follow-up debt and open items

- The two backend failures above. They are follow-up debt and are not fixed in M25A: the first
  has a confirmed baseline cause (M14 scope), the second has an unverified exact cause.
- M25B (unifying and hardening the UIs) remains. M25A does not complete Milestone 25.
- Running Playwright in CI is M23A. Serving the build from the Docker image is not part of this
  step.
