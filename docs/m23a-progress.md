# M23A — Baseline CI (progress)

Status: **COMPLETE** (2026-10-22): all four required GitHub Actions jobs passed on `main` at
`c9b6e6d` (run below). Scope (§28 of `MASTER_PLAN.md`): Milestone 23's requirements applied to the existing
core search and the M25A page. It automates the tests M25A already passes.

## What was added

`.github/workflows/ci.yml`, one workflow, four jobs on standard `ubuntu-latest` runners:

| Job | Runs | Needs |
|---|---|---|
| `backend` | `uv sync --frozen`; `ruff check .`; `ruff format --check .`; hermetic unit suite | No database, no model snapshot, no provider |
| `integration` | `pytest tests/integration -m "integration and not real_model"` | Throwaway `pgvector/pgvector:0.8.6-pg16` service container (same image as `docker-compose.yml`); scratch databases only |
| `frontend` | `npm ci`; `npm run typecheck`; `npm test`; `npm run build` | Node 24.22.1, `package-lock.json` |
| `e2e-fixture` | `npm run build`; `npx playwright install --with-deps chromium`; `npm run e2e:fixture` | The built page served by FastAPI (`playwright.config.ts`); no database service, no models |

Controls: triggers are `push` to `main`, `pull_request` to `main` and `workflow_dispatch` (no
`pull_request_target`); `permissions: contents: read`; checkout with `persist-credentials: false`;
no secrets used; concurrency cancels superseded runs per ref; timeouts 30/30/15/20 minutes. Actions
are pinned to commit SHAs of official releases, resolved with `git ls-remote` on 2026-10-22
(lightweight tags): `actions/checkout` v7.0.1, `actions/setup-node` v7.1.0,
`actions/upload-artifact` v7.0.2, `astral-sh/setup-uv` v10.3.1. uv is pinned to the local 0.11.12.

The only artifact is `frontend/test-results/`, uploaded on `e2e-fixture` failure, kept 7 days.
Playwright trace and screenshots are off in the config, so it holds only test output over synthetic
fixtures. No `.env`, environment dump, model or cache is uploaded. The GitHub cache holds only the
uv and npm download caches.

Passwords in the workflow are placeholders: `ci-ephemeral-not-a-secret` for the per-run service
container, `ci-placeholder-no-database` for the fixture job, where settings require a non-empty
value but nothing connects. They protect no real database.

## Test debt resolved

1. **Metadata expected-table test** (`tests/unit/catalog/test_catalog_models_metadata.py`). M14
   migration `0006` added four append-only tables (`listing_decision_runs`, `_proposals`,
   `_probabilities`, `_reasons`) without updating the expected set. Before adding them, each was
   checked against every rule in the file: no JSON, tsvector or vector column; no non-constraint
   index; every constraint named `pk_/uq_/fk_/ck_` and at most 63 characters. All four satisfied
   them. The set was extended; no rule was changed. File: 20 passed.
2. **Recursive child-suite test** (`tests/unit/test_unit_isolation.py`). The child command was run
   once with its exact sanitization in a clean worktree (HEAD `2c6c6dd` plus the M23A changes, no
   `.env`, no `models/`), output saved: **4,852 passed, 5 skipped, 780 deselected, exit 0
   (13 min 58 s, Windows)**. The earlier failure's output was never saved, so its cause is not
   proven; it is consistent with the metadata failure reproducing inside the child, and no other
   failure appeared after that fix.

## The recursive test in CI (visible skip)

`test_whole_unit_suite_passes_without_a_database_password` reruns the entire unit suite in a
subprocess, which would double the `backend` job. The `backend` job is instead that child: it
removes the same variable prefixes (`POSTGRES_`, `DB_`, `APP_ENV`, `LOG_LEVEL`, `EMBEDDING_`,
`SEARCH_`, `RERANKER_`), sets `POSTGRES_PASSWORD=""` and `ECOMMERCE_SEARCH_HERMETIC_CHILD=1`, and
runs the same command (`-m "not integration and not real_model" -q -p no:cacheprovider`, plus
`-ra`). The checkout has no `.env`. The recursive test therefore reports
`SKIPPED ... already inside the hermetic child` in the job log; its assurance comes from the job's
own pass. Locally the test is unchanged and still runs the child.

## Local verification (2026-10-22, Windows)

All in the clean worktree with the M23A changes:

| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check .` | PASS / 410 files already formatted |
| Hermetic unit suite (above) | 4,852 passed, 5 skipped (4 Windows symlink skips, 1 recursive-test skip) |
| `npm ci` / `typecheck` / `npm test` / `build` | 0 vulnerabilities / PASS / 24 passed / PASS |
| `npm run e2e:fixture` | 6 passed, with `POSTGRES_PORT=1` (closed), no `.env`, Jev disabled |

Caveats: Windows Application Control blocked the worktree's newly created `uvicorn.exe`, so the
fixture run and Ruff used the main checkout's existing `.venv` with `UV_NO_SYNC=1` (backend `src/`
is unchanged by M23A). In that run the app resolved the main checkout's `models/` directory; the
embedder and reranker are created unloaded and the fixture tests send no request to the API, while
CI has no `models/` at all. The integration job was **not** run locally (it would migrate scratch
databases on the development server); its first evidence is the GitHub run.

## Costs

Billing checked by the owner on 2026-10-22: GitHub Free, 0/2,000 included Actions minutes,
0/0.5 GB storage, $0 billable. Standard runners only; no paid runners, budgets or billing changes.
Each run downloads the locked Python dependencies (including the CPU-only torch wheel), npm
packages, Chromium with its system packages and the pgvector image. Measured on the first run: `backend` 669 s, `frontend`
321 s, `e2e-fixture` 92 s, `integration` 204 s (torch download 187.2 MiB per Python job). With
per-job rounding up to whole minutes that is about 24 runner-minutes per push (computed from job
times, not read from the billing page), against 2,000 included minutes per month.

## Out of scope (M23B)

`real_model` tests (need fetched model snapshots), the live Playwright project and any Jev or
generation-provider check (local only; routine CI never calls them), evaluation smoke tests,
provider contract tests, configurable thresholds, the other domains and M25B UIs, and branch
protection.

## GitHub run

Run `39738936822` on push of `c9b6e6d`, standard `ubuntu-latest` runners:
https://github.com/Vishal-AI-ML-Dev/intelligent-ecommerce-search/actions/runs/39738936822

| Job | Result | Evidence from the job log |
|---|---|---|
| `backend` | success (669 s) | Ruff: all checks passed, format check passed; unit suite 4,853 passed, 4 skipped, 780 deselected |
| `integration` | success (204 s) | 762 passed, 18 deselected (`real_model`) |
| `frontend` | success (321 s) | typecheck; Vitest 24 passed; build |
| `e2e-fixture` | success (92 s) | Playwright fixture project 6 passed |

Visible skips in `backend`: `test_unit_isolation.py:25` "already inside the hermetic child" (by
design, above) and 3 in `test_model_fetch_profiles.py:363` ("could not import '_winapi'", Windows-only
tests on Linux). The run was observed through read-only Actions API calls.
