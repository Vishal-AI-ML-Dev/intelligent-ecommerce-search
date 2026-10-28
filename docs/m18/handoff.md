# M18 — Handoff

## M18-09 Acceptance & Handoff (2026-10-28; owner qualified acceptance FINAL)

**Owner decision (2026-10-28):** the implemented M18 capabilities are accepted **for an
explicitly limited scope only**:
- project-authored synthetic data;
- local development;
- the current single-EC2 staging demo.

This is a **qualified acceptance**. It is **not**:
- production certification;
- validation with real retailer data;
- proof of business impact.

It does not state that every original acceptance check passed. The exceptions and limitations
below stay open.

**CI (owner-confirmed, not independently verified):**
- `7c87532` (M18-06): all required jobs green.
- `e5fa151` (M18-07): run 40695893009, green.
- `134d1cb` (M18-08 evidence): all required jobs green.

### Delivered functionality
- **Return data foundation:**
  - migration `0007` (15 append-only tables, raw files kept apart from normalized records);
  - contract `return-data-v1` with JSON and CSV forms giving equal records;
  - redaction and per-record provenance;
  - the synthetic dataset `returns_v1` (180 returns plus related records).
- **Import:**
  - command-line tool and API: validate, import, verify;
  - atomic and idempotent, with limits and concurrency handling;
  - a scoped `returns_importer` role; `search_app` cannot read raw files.
- **Protected retailer API:** seven `/returns` routes behind bearer tokens, with paging, detail
  and batch history. No refund, ban, fraud or sanction path.
- **Retailer UI:** the "Retailer returns" tab, including the refresh-after-import correction.
- **Release kit:**
  - `m18-release.sh` (preflight, backup, migrate, roles as SCRAM verifiers, grants gate, import
    and verify, app swap);
  - restore with grants by revision;
  - Caddy log query stripping;
  - the `release.md` runbook.

### Evidence by environment
- **Local:**
  - unit, integration and Vitest suites as recorded per package;
  - fixture and live Playwright;
  - the buyer live check as the real `search_app` role;
  - the release rehearsal;
  - the old image on `0007` with no egress;
  - both restore paths with counts and privileges compared;
  - the Caddy marker test with a negative control.
- **Staging (M18-08):**
  - read-only baseline;
  - release to `0007`, grants, import and `verify` `match`;
  - the AC-19 auth matrix; 180 returns = the import report;
  - an app-container restart;
  - public buyer health and search;
  - internal assistant check with 0 provider calls;
  - a passive Caddy log count;
  - backups before and after.
- **Not verified on staging:**
  - database durability and restore;
  - `alembic downgrade`;
  - a browser run;
  - opening a return detail page.

### Owner exception granted: AC-14 historical checksum listing (2026-10-28)
AC-14 (test matrix) requires, **at each package end**, both:
1. the protected-path diff against `bf67d11`. This **passed (exit 0)** at every package end,
   including M18-08.
2. a check of the untracked M17 outputs against the SHA-256 listing taken before M18-02 and kept
   outside the repo. This **passed** at M18-02, M18-03A, M18-03B and M18-03C (10 files OK). The
   listing (`cf74568d…41adf7`) was deleted with its temporary directory on 2026-10-27, so this
   check **was not run for M18-04A through M18-08**.

**Status:** the historical checksum verification is **unavailable, so AC-14 is not fully
verified** for those packages. Since then the M17 paths have been preserved by not touching
them, but no checksum proves that.

**Owner decision:**
- An explicit exception covers **only** the unavailable historical checksum checks for M18-04A
  through M18-08.
- The original listing was lost, and continuity **cannot now be verified**. "Untouched" is
  agent-reported, not checksum-proven.
- The check is **not** recorded as passed, and no replacement historical baseline was created.
- The exception does not weaken future preservation requirements. The protected-path diff part of
  AC-14 passed throughout.

### Other open limitations (recorded, not waived)
1. The three older test fixtures put role passwords in plaintext SQL.
2. The Windows outer isolation wrapper is **FAILED** (1200 s timeout), recorded separately. By
   owner decision (2026-10-28), the agreed CI hermetic-suite evidence satisfies AC-17's
   hermetic-unit requirement. Tests, skips and timeouts are unchanged.
3. Admin or superuser importer rejection is untested at the API level (covered at service and
   CLI level).
4. The SHA of the historical catalog-failure CI run is unconfirmed.
5. Caddy's HTTP→HTTPS redirect echoes the path and query.
6. The ECS/RDS `setup.sh` log-guard behaviour is unverified (the script fails closed).
7. `alembic downgrade 0006` was not rehearsed.
8. Staging database durability and restore were not exercised (the restart check covers the app
   container only).
9. The deployed image `sha256:1fec521b…029c` is a local working-tree build, not a committed or CI
   build. Its lockfile-pinned package downloads were unauthorized, then accepted.
10. The old image's identity rests on a matching image ID, not a registry digest.
11. The live assistant is untested; the approved criteria do not require it.
12. No browser run against staging.
13. No real retailer or personal data was used; redaction recall on real text is unmeasured.

### Not done by this step
- `MASTER_PLAN.md`: only an M18 status row was added (owner-authorized). Plan §7 C7 and no other
  MASTER_PLAN change was applied.
- `release.md`'s status line updated to the qualified acceptance.
- M19 not started.

## M18-08 Staging Release & Smoke (2026-10-28; owner-approved; docs committed 134d1cb, CI green per owner)

- **Release:** commit `e5fa151cb13ef567235a0c446e6ab8b3b5785d32` (CI run 40695893009 green,
  owner-confirmed).
- **Deployed:** the rehearsed candidate (owner option B), image ID
  `sha256:1fec521b044a6c3db5347eadcb561081a32c4db1d00d163fa236c39da817029c`. This is a local
  working-tree build at `7c87532` whose image inputs equal `e5fa151`. Its lockfile-pinned package
  downloads were unauthorized and later accepted. **Not a committed or CI build.**
- Full evidence is in `release.md` §7.

**Phase A (read-only):**
- staging `0006`, 19 tables;
- app image ID `ecaeb633…` (equal to the local AC-18 image ID; there is no registry digest);
- all 8 server kit files equal `e057d00`;
- no `RETURNS_*` keys; 6.0 GB free;
- public baseline OK.

**Phase B (14:00–14:26 UTC):**
- Removed two unreferenced old image tags (plain `rmi`, no prune): **2451 MiB reclaimed**.
- Pre-release backup exported (SHA-256 matched); previous kit and `.env` preserved in
  `pre-m18/` (root, 700).
- `e5fa151` kit and returns files verified (11/11); credentials generated on the server and never
  printed.
- Archive verified: 617 MB, uploaded in 21.6 min.
- **`m18-release.sh` exit 0 in 68 s:** migration, roles and grants took 9 s; import `imported`
  (batch `f3715c76…`); verify `match` (`497cebbd…`).
- Caddy recreated in 2 s; the certificate volume was kept.

**AC-19 smoke:**
- `/returns`: 401 without a token, 401 with a wrong token; viewer 200 with `can_commit: false`;
  viewer commit 403; 180 returns listed = import report; verify `match`.
- **The same results after `docker compose restart app`** (healthy in 9 s).
- Public `/health` and ranked search OK (10 results, `applied`).
- Internal assistant: `skipped`, **0 provider calls**. No public assistant request.
- Caddy log: 0 URIs with a query.
- Raw return files unreadable by `search_app` and by the importer.
- Post-release backup exported (SHA-256 matched).
- Run artifacts cleaned up; 6.1 GB free.
- No rollback needed.

**AC status:**
- **AC-19:** every listed check passed on staging. The restart check is an app-container restart
  only, not a database durability or restore test.
- **AC-18:** local rehearsal plus CI on `e5fa151`.
- **Final M18 acceptance:** not given (owner).

**Open, unchanged:**
- the three older fixtures' plaintext SQL passwords;
- the missing AC-14 M17 checksum listing;
- the Windows isolation timeout (FAILED);
- untested API-level admin-role rejection;
- the unconfirmed SHA of the historical catalog-failure run;
- the Caddy redirect echo;
- the ECS/RDS guard behaviour (unverified);
- `alembic downgrade` not rehearsed;
- the deployed artifact's provenance (above);
- staging database durability and restore not exercised.

**Next:** owner review. Committing these docs needs approval. M18-09 acceptance is separate.

## M18-07 Release Readiness (2026-10-28; committed e5fa151; CI run 40695893009 green per owner)

**M18-06 closure (recorded at owner direction):**
- `7c87532bdc35f860cf766bbeead4ff4292b07e7e` was committed and pushed, and the owner confirmed
  every required CI job GREEN on it (not independently verified).
- M18-06 evidence is closed within its documented scope:
  - **AC-16:** fixture browser 20/33 and live browser 2+2, local.
  - **AC-17:** the CI hermetic unit, integration, frontend and e2e-fixture jobs on `7c87532`,
    plus the local buyer live check as the real `search_app` role under the new grants. The
    live assistant is untested; the approved criteria do not require it.
- This is not M18 acceptance. The existing limitations stay open.

**Approved scope:** the M18-07 proposal with owner decisions D1–D7. The runbook and all
rehearsal evidence are in `release.md`.

### Changed files
- `deploy/ec2/compose.yaml`: app returns settings (viewer only, commit fixed off), and the
  `m18` one-offs `grants` and `returns-import`.
- `deploy/ec2/m18-release.sh`: new.
- `deploy/ec2/setup-db.sh`, `restore.sh`, `backup.sh`, `update.sh`, `Caddyfile`.
- `infra/staging/setup.sh`: shared; Terraform unchanged.
- `docker-compose.yml`: `RETURNS_*` forwarded to `api` only, empty or `false` by default.
- `tests/unit/test_compose_config.py`: 6 new pins.
- `docs/m18/release.md` (new), this file, `test-matrix.md`, `deploy/ec2/README.md`.

### Decisions applied
- **D1:** minimal shared `setup.sh` change. ECS still runs it with no argument (full setup).
- **D2:** SCRAM verifiers for both roles, log guards first, fail-closed. No `tests/` import in
  the image.
- **D3:** checksum-verified, read-only bundle mount.
- **D4:** the query is stripped from Caddy logs, verified with markers and a negative control;
  the redirect limitation is kept.
- **D5:** the old image was present (not rebuilt).
- **D6:** revision and grants preflight inside `m18-release.sh`; `update.sh` is documented as
  not a substitute.
- **D7:** no AWS or staging access.

### Unauthorized downloads (recorded transparently)
- The candidate `docker build` (image `sha256:1fec521b…029c`) downloaded 76 Python and 108 npm
  packages, all pinned by the unchanged lockfiles, plus the pinned base layers. The owner's
  instruction forbade package downloads. I stopped and reported it.
- The owner then accepted continued use of this one artifact (option 1), with no further
  downloads. No rebuild, pull, install or download happened afterwards.

### Kit defect found and fixed
- `m18-release.sh` first compared the raw bundle file hash with `provenance.bundle_sha256.json`,
  which is the contract §4.6 derived hash. The preflight refused (fail-closed).
- It now computes the §4.6 form; verified to reproduce `d7a9f535…1ffb`.

### Results

| Check | Result |
|---|---|
| Pre-checks | lockfiles, manifests, Dockerfile, `.dockerignore` and every image input path equal HEAD `7c87532`; all four images, Playwright Chromium and the baked models present; no local `m26a-search` resources |
| `pytest tests/unit/test_compose_config.py` | **35 passed** |
| `ruff check` / `ruff format --check` (test file) | passed / formatted |
| `bash -n` on every kit script | passed |
| `docker compose config --quiet` (staging with a dummy env file and profiles `setup` and `m18`; root) | OK / OK |
| `caddy validate` | Valid configuration |
| Caddy marker test (real `caddy:2.11.6`, upstream unreachable) | 3 error entries; `uri` without query; `Authorization` and `Cookie` `REDACTED`; markers query/auth/cookie 0/0/0; HEAD Caddyfile negative control: query marker 1 |
| Rehearsal attempts 1–3 | stopped before any destructive step: two driver path bugs, then the kit defect above. Teardown clean each time. |
| **Rehearsal attempt 4** | **all phases passed** (table in `release.md` §5): old kit at `0006` with no egress; release in 7 s (migration, roles and grants); idempotent re-run; AC-19 checks; **AC-18 old image on `0007`** (health, search, assistant off, `/returns` 404, live spec 1 passed); scratch restore 34/34 counts and 985/985 privilege rows equal, raw files unreadable; `restore.sh` for `0007` and for the pre-release dump (`0006`, 0 M18 tables left, old app passes) |
| Cleanup | containers, volumes and networks equal to before; dev `ecommerce-search-db-1` untouched (still up) |
| Secret scan | 0 hits in 35 log files |

### AC status after M18-07
- **AC-18:** locally rehearsed and passed.
  - Old-image compatibility on `0007` with the grants, M18 data, no egress and generation off.
  - Migration duration recorded (7 s, including roles and grants).
  - Local restore rehearsal: counts and privileges equal.
  - **Still needed:** CI green on the release commit (not yet committed). The rehearsal used a
    local working-tree candidate and a local old image (see limits).
- **AC-19:** the steps were rehearsed locally only. The real check is M18-08 on staging, after
  approval.

### Still open
- the three older fixtures' plaintext SQL passwords;
- the missing historical AC-14 M17 checksum listing;
- the Windows isolation timeout (FAILED);
- untested API-level admin-role rejection;
- the unconfirmed SHA of the historical catalog-failure CI run;
- the Caddy HTTP→HTTPS redirect echo;
- the ECS/RDS `setup.sh` guard behaviour (unverified);
- the staging revision and deployed image digest (unverified);
- the candidate image is not a committed or CI build.

Closed by M18-07, locally: Compose `RETURNS_*` wiring, and Caddy query logging (except the redirect).

**Next:** owner review of the diff and `release.md`, then a commit decision. M18-08 needs separate approval.

## M18-06 Integration & Browser Verification (2026-10-28; committed 7c87532; CI green per owner)

Approved scope: the M18-06 proposal of 2026-10-28 with owner corrections P1–P5. HEAD is still
`464ab0f3c1b167fe0a6560868d3a351981d39927`. **No backend, client, UI, dependency, CI-workflow or
deploy-kit change.**

**Changed files:**
- New: `frontend/e2e/returns-fixture.spec.ts`, `frontend/e2e/returns-live.spec.ts`.
- Modified:
  - `frontend/playwright.config.ts`: the returns fixture spec joins the `fixture` project, plus a
    new local-only `returns-live` project;
  - `frontend/package.json`: new script `e2e:returns-live`;
  - `README.md`, `docs/m18/test-matrix.md`, this file.

### M18-06 follow-ups (2026-10-28; owner-approved; uncommitted)

**U1 fix: the list and history refresh after an import.**
- `SignedIn` keeps an import generation counter. `ImportPanel` raises it, through the new prop
  `onImported`, only when a report has status `imported`. The statuses `valid`,
  `already_imported`, `no_new_records` and `invalid` and every error store nothing, so they
  trigger nothing.
- `ReturnsList` and `BatchHistory` take a `refreshKey`. If a section has already loaded, it
  reloads page 1: the list keeps the user's applied filters, and the history reloads its first
  page. The stale cursor stack is dropped. A section that was never shown still loads fresh when
  first shown.
- The reload goes through the existing `useReturnsRequests` guard, so the same-key abort,
  unmount abort and session keying are unchanged. There is no page reload.
- New Vitest cases (3) in `ReturnsPage.test.tsx`:
  - after `imported`, the list reloads as `/returns?status=received` at page 1 (from page 2),
    with the filter kept, and the history reloads;
  - after `valid`, `already_imported` and 422 `invalid`, nothing reloads;
  - a never-opened history is not loaded.
- **Mutant check:** against the HEAD versions of the four UI files, the two positive cases fail.
  The "nothing reloads" case passes on both, as intended. My versions were restored afterwards.
- `returns-live.spec.ts` no longer clicks Apply filters. After the import, history shows 1 batch
  and the list shows rows without Apply filters or Refresh, and the full 180-row paging count is
  unchanged.

**AC-17 real-role live check.**
- **Requirement:** AC-17 (test matrix) requires the "API live suite run **as the real
  `search_app` role** under the new grants", along with the full hermetic unit suite,
  integration (`not real_model`), existing Vitest and both Playwright projects.
- **Helper:** in `tests/returns_roles.py`, `create_login_role` and its `m18t_*` allowlist are
  unchanged. A new `create_absent_search_app_role` accepts only the exact name `search_app`. On
  the guarded raw cursor it first runs a constant `pg_roles` lookup and refuses if the role
  exists, before any `CREATE`. Otherwise it creates the role with
  `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT` and a SCRAM verifier.
- **Helper unit tests (4 new):**
  - the generic helper still refuses `search_app`;
  - an existing role is refused with no `CREATE`, `ALTER` or `DROP` sent;
  - when absent, the role is created with a verifier only;
  - a bad password form is refused.
  - The file has 13 passed.
- **Run (scratchpad driver, mode `search_app`):**
  1. Fresh `pg_roles` check: absent.
  2. Created the role with the helper, plus `COMMENT ON ROLE search_app IS 'm18-06 run-owned
     <hex>'` as the ownership mark. Attributes: super, createdb, createrole, inherit,
     replication and bypassrls all false; login true.
  3. Grants applied only in the run-owned scratch database (`returns-grants-1`, app role
     `search_app`, throwaway importer).
  4. `live` (existing buyer test) as `search_app`: **1 passed**.
  5. Teardown: dropped the database, then the importer. `search_app` was dropped only after its
     comment matched the run's mark.
  6. Database and role lists before and after: equal (`ecommerce_search` only).
- `search_app` had the default PUBLIC `CONNECT` on the cluster's other databases while it
  existed, and no table privileges there. No pre-existing object or privilege was changed.

**U1 live rerun (mode `returns`, throwaway roles):** `@commit` **2 passed**, `@validate-only`
**2 passed**. Cleanup was verified and the lists were equal.

**Secrets:** 0 hits in 14 scanned files across both runs, and 0 in the raw captured output
before redaction. The Postgres log since 12:00 UTC has no role, password, verifier or
`search_app` text.

| Command | Result |
|---|---|
| `npx vitest run src/domains/returns` | first run: 1 failed (test-side ambiguous "Page 1" match), then **66 passed** |
| `npm test` | **179 passed** |
| `npm run typecheck` / `npm run build` | exit 0 / exit 0 |
| `npm run e2e:fixture` (rebuilt bundle) | **33 passed** |
| `pytest` fixture contract, fixture privacy, frontend serving, `test_returns_roles.py` | **102 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 520 files formatted |
| driver `returns` / driver `search_app` | 2+2 passed / 1 passed; cleanup verified |
| `git diff --check`; AC-14 diff vs `bf67d11`; `git diff -- data/` | exit 0 / exit 0 / exit 0 |

**AC status:**
- **AC-16:** fixture (20 + 33 project) and live (`@commit` 2, `@validate-only` 2) pass with the
  U1 fix. Pending owner acceptance.
- **AC-17: NOT complete.**
  - Covered: buyer live as the real `search_app` under the new grants (1 passed), Vitest 179, the
    fixture project 33, and the generation-off setting.
  - Not covered: the full hermetic unit suite and `not real_model` integration were not rerun by
    owner direction; the last green CI is owner-reported, not verified.
  - The existing live suite has no assistant test, so the assistant was not exercised live.

### Fixture browser (mocked API): 20 tests, all passed

A catch-all route lets only `/` and `/assets/*` reach the server. Buyer search, assistant and
`/assistant/status` calls get fixed fixture answers. Anything else fails the test.

**Every test asserts:**
- no page error, dialog or unexpected request;
- zero `securitypolicyviolation` events (listener installed before any page script);
- console lines limited to the client's `[api]` logs and Chromium's non-2xx note, with no token
  in any of them.

| Group | What is checked |
|---|---|
| F7 CSP | `/` carries the exact `FRONTEND_PAGE_HEADERS` CSP, `nosniff` and `no-referrer`. A bare `GET /returns` is answered by the API (401/503, JSON, `no-store`, no CSP), never by the page. |
| F1 gate | `type=password` field. The token goes only to `/returns` routes, as `Bearer`. The input is cleared. 401 shows the fixed text plus the sent request ID, and the server `detail` is not shown. 503 shows the fixed not-configured text. |
| F2 permissions | Viewer: Validate is shown, Import is absent, plus the disabled text. Importer: Import is shown. A server 403 shows the fixed text. |
| F3 uploads (`setInputFiles` on the real input) | JSON is sent as part and filename `bundle.json`, whatever the local name. A CSV set keeps its part names. Multipart content type. 422 `invalid` from import shown as a report. Truncation and lower-bound wording. The pre-check stops an empty, oversized or manifest-less upload with no request. |
| F4 paging/detail/history | Next sends page 1's cursor; Previous resends no cursor. Filters are trimmed into query parameters. Empty, `cursor_mismatch` and `invalid_parameter` states (parameter list shown, `detail` not). Detail columns are side by side, with no conflict/fraud/score wording. Provenance is open for the return and collapsed (then openable) on nested records. "Not recorded". Synthetic badge, "(UTC)". Batch list → batch detail. Empty history. |
| F5 hostile content | Runtime copies of validated fixtures carry hostile text (image/onerror, script, `javascript:` link, template injection, RTL override and zero-width characters) in comments, a batch file name, a report message and a value preview. All of it displays as literal text; no `img`, `script` or `javascript:` link element exists, and nothing ran. A hostile 401 `detail` is not displayed; the fixed text is. |
| F6 token/session | The token is absent from `localStorage`, `sessionStorage`, `document.cookie`, browser cookies, the URL, the DOM and input values. IndexedDB database count and Cache Storage key count are both 0 (existence check only; there is no database or cache to hold contents). Buyer search, assistant and status requests made while signed in carry no `Authorization`. Sign-out, a later 401 and a reload drop the session. A held old-session response, answered after sign-out and a second sign-in, changes nothing, and every later request carries only the new token. |

`setInputFiles` does not exercise the OS file picker. No test covers password-manager behaviour.

### Live browser (local stack, no mocks): all passed on the second attempt

A scratchpad driver (not in the repo) does the following:
- Positively identifies the server: `system_identifier` 7697502985140146214.
- Records the database and role lists before and after.
- Creates, per run, one database `ecommerce_search_test_m18e2e_<hex>` and two roles
  `m18t_liveapp_<hex>` / `m18t_liveimp_<hex>`. The roles come from `tests/returns_roles.py`
  `create_login_role` (SCRAM verifier, raw cursor, session log guards).
- Prepares the scratch database:
  - migrates it to `0007` in process (as `tests/integration/conftest.py` does);
  - runs `catalog ingest` (240 records), then `search embed` and `embed-status --require-current` (240/240, local pinned snapshot, `HF_HUB_OFFLINE=1`);
  - applies `returns.grants` (`returns-grants-1`, 68 statements).
- Runs each Playwright pass on a free port. Each run gets its own environment (`RETURNS_*` removed from the inherited environment, then set per mode).
  - Playwright 1.64.1 merges `process.env` into the webServer environment. With
    `reuseExistingServer: false` it refuses a busy port, so no unrelated server can be reused.
  - The API connected as the throwaway app role.
- Teardown in `finally`: the server port is checked closed after each run; the database is dropped `WITH (FORCE)`; both roles are dropped; the engine is disposed.

| Run | Result |
|---|---|
| `@commit` | **2 passed**: fresh database. Validate gives `valid` with no batch. Import gives 201 `imported` with returns new = 180. Paging the whole list gives 180 unique `synthetic-returns v1` rows. Detail shows the badge, provenance with the batch, and both columns. History has 1 batch, with file `bundle.json`. Re-import gives 200 `already_imported` with the same batch. The CSV set validates as `no_new_records`; still 1 batch. Viewer: data visible, no Import control, 1 batch. A wrong token gets the fixed 401 text. Every `/returns` request carried exactly the signed-in token; no other request carried any. |
| `@validate-only` | **2 passed**: viewer scopes `returns:read, returns:validate`. Disabled text shown, no Import control. Validate gives `already_imported` and the batch count is unchanged (1). The list still has 180. A direct `POST /returns/import` with the viewer token gets 403, `no-store`. |
| `live` (buyer) | **1 passed**: 200, results > 0, reranker `applied`, as the throwaway app role under the new grants. |

**First attempt:**
- `@commit` failed on one assertion: 0 of 180 rows after the import (finding U1).
- `@validate-only` passed.
- The buyer run did not start: the driver crashed printing a non-cp1252 character, after teardown.

The test was changed to reload with Apply filters, and the driver to UTF-8 output. All runs were then repeated on new owned resources.

**Cleanup and secrets (both attempts):**
- Database and role lists are equal before and after (`ecommerce_search`, `postgres`,
  `template0`, `template1`; role `ecommerce_search`).
- Development database not touched; it is still at `0004`.
- No `.env` write, no download, no provider call. Generation was `none` and `GEMINI_API_KEY` was empty.
- The `db` container was already running and was left running.
- Secret scan over 10 files (each run's `test-results`, the captured Playwright, server and CLI output) found 0 occurrences of the 5 run secrets (tokens and role passwords). The raw captured output also held 0 before redaction. Values were never printed.
- Postgres server log since the run: no role, password or verifier text.
- Recording: trace, screenshot and video stayed off; no HAR and no `storageState`. Tokens were
  typed with `keyboard.insertText` and compared as booleans.

### Findings

- **U1 (FIXED in the follow-up below):** after an import, the Returns section kept the page it
  loaded when it was first shown. It says "No returns match these filters." until the user presses Apply filters.
  - This is the documented design ("each loads when first shown"), and contracts §9 is silent, so
    it was not treated as a runtime defect.
  - Smallest fix, if wanted: reload the list from page 1 when an import returns `imported`. One
    callback from `ImportPanel` to `SignedIn`, plus a Vitest case.

### Coverage

- **AC-16:** fixture and live browser paths pass. Closed on local evidence, pending owner
  acceptance.
- **AC-17: NOT complete.**
  1. **Blocker at the time:** the real `search_app` role does not exist on the local cluster. Per
     P2, it was not created and not substituted. The throwaway-role buyer run is supporting
     evidence only. Resolved for the live check by the follow-up below; the other gaps remain.
  2. Per P4, the full hermetic unit and integration suites were not rerun. The last green CI is
     owner-reported and not independently verified.
  3. Vitest 176, the fixture project (33) and buyer live (1) passed.

### Commands and results

| Command | Result |
|---|---|
| `npm run typecheck` (after each spec edit) | exit 0 |
| `npm run build` | exit 0 |
| `npx playwright test --project=fixture returns-fixture` | runs 1–3 failed: 19, then 8, then 4 failed. Causes were test-side only: an unmocked `/assistant/status`, and the ambiguous names `Import` vs `Import history` and the section vs upload `Import`. Final: **20 passed** |
| `npm run e2e:fixture` | **33 passed**, exit 0 |
| `npm test` (Vitest) | **176 passed** |
| `pytest tests/unit/test_frontend_fixture_contract.py tests/unit/returns/test_returns_fixture_privacy.py tests/unit/test_frontend_serving.py` | **89 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 520 files formatted |
| live driver attempt 1 / attempt 2 | see above / all 5 tests passed, cleanup verified |
| `git diff --check`; AC-14 protected-path diff vs `bf67d11`; `git diff -- data/` | exit 0 / exit 0 / exit 0 |

Not run: the full Python unit suite, the integration suite, the Windows isolation wrapper (still **FAILED**), CI, staging.

**Still open, unchanged:**
- the three older fixtures' plaintext SQL passwords;
- the missing historical AC-14 M17 checksum listing;
- the Windows isolation timeout (FAILED);
- untested API-level admin-role rejection;
- Compose `RETURNS_*` wiring;
- Caddy query-log exposure;
- the unconfirmed SHA of the historical catalog-failure CI run.

## M18-05 Retailer UI (2026-10-28; in the scoped M18-05 commit after the owner-authorized review checkpoint)

Approved scope: the M18-05 proposal of 2026-10-28, with owner decisions (1) hand-constructed
synthetic fixtures validated against the actual Pydantic models, provenance in the fixtures
README, and (2) a committed vocabulary file with a test of exact agreement with the backend enums
and the catalog taxonomy. **No backend, contract or dependency change.** HEAD is still
`24079f011e4618ab944d621cb883705153697d39`.

**What was built (contracts §9, D11):** a third tab, "Retailer returns"
(`frontend/src/domains/returns/`).
- **Access gate:** a masked token field. Sign-in calls `GET /returns/capabilities`. The input is
  cleared on submit. 401 gives the unauthorized explanation with the request ID; 503 gives
  "Retailer access is not configured on this server."
- **Sections:** Returns (the §7 filters, Next/Previous over a client cursor stack, the "Synthetic
  demo data" badge, detail), Import (JSON bundle or CSV set; Validate always when `can_validate`;
  Import only when `can_commit`, otherwise "Imports are disabled on this server."; advisory
  pre-checks from the published limits), and Import history (batches, then one batch's file
  metadata).
- **Detail:** statements and observations in separate columns side by side, with no flag or
  score; "Not recorded" for absent evidence; listing snapshot with its catalog version; full
  provenance for the return and collapsible provenance on every nested record.
- **Token handling:** React state only. Sign-out or any 401 drops the session; the signed-in
  view is keyed by the session, so it unmounts, pending requests are aborted, and late
  responses are dropped. Nothing is written to storage, cookies, the URL or logs.
- **HTTP client (`shared/http/client.ts`), additive only:** `getJson`/`postJson` take optional
  `RequestOptions`; `postForm` is new. `authorization` is refused for any path that is not a
  same-origin `/returns` path. `logPath` logs a fixed route template, so query values and record
  IDs are never logged. `detailedErrors` adds the request ID, the safe `code` and 422
  `parameters` (each checked against `[a-z_]{1,40}`); `bodyStatuses` returns the import route's
  422 `invalid` report as data. Without options, behaviour is unchanged (the existing 13 client
  tests pass with unmodified bodies; only their import line gained `getJson` and `postForm`).
- **UI text:** fixed client messages per error code; the server's `detail` and every uploaded or
  stored text are rendered as React text only.
- `vite.config.ts`: the dev-server proxy also forwards `/returns` (dev server only).

**Interpretations to review:**
- A JSON bundle is always sent as the part `bundle.json` (§4.1 canonical name), whatever the
  local file is called; CSV-set parts keep their file names, and the server judges them.
- The Import tab hides the Import button when `can_commit` is false and shows the §9 text for
  every reason (missing scope or closed gate), as §9 states.
- Without `can_validate`, the panel shows "Your access token does not allow uploads." (no current
  token lacks it).
- Timestamps are shown as the server's canonical UTC strings, without local conversion.

| Command | Result |
|---|---|
| `npm run typecheck` | exit 0 |
| `npm test` | 140 passed (6 files), 0 `act` warnings |
| `npm run build` | exit 0 |
| `pytest tests/unit/test_frontend_fixture_contract.py tests/unit/returns/test_returns_fixture_privacy.py tests/unit/test_frontend_serving.py` | 89 passed |
| `ruff check .` / `ruff format --check .` | all checks passed / 520 files formatted |
| `git diff --check`; new-file whitespace/CR/final-newline scan | exit 0 / none |
| secrets scan of changed and new files | only `type="password"`; no values |
| AC-14 protected-path diff vs `bf67d11`; `git diff -- data/` | exit 0 / exit 0 |

Node was run from the user-local `node-v24.22.1-win-x64` install (not on `PATH`). Not run:
Playwright (M18-06), any database or live API, the full Python suite, the Windows isolation
wrapper (**FAILED**, unchanged), CI.

**Limitations:**
- The stale-response test passes partly because React ignores updates to an unmounted
  component; the explicit abort check is defence in depth, not separately proven by a mutant.
- Browser behaviour (real file pickers, password managers, the built page under the CSP) is
  unverified until the M18-06 Playwright runs.
- Still open, unchanged: the three older fixtures' plaintext role passwords, the missing AC-14
  M17 listing, the Windows isolation timeout, API-level admin/superuser importer rejection,
  Compose `RETURNS_*` wiring and Caddy query-string logging (M18-06/07), and the unconfirmed SHA
  of the catalog-failure CI run.

### M18-05 pre-commit review (2026-10-28)

Owner accepted: JSON part naming `bundle.json`, capabilities-based controls, UTC display. Every
displayed timestamp now carries a " (UTC)" suffix (helper `utc()` in `ReturnsCommon.tsx`; values
unchanged).

**Reviewed guards:**
- **Session generation and cancellation:** the page owner stays mounted. The signed-in view is
  keyed by a new session ID on every sign-in. `useReturnsRequests` aborts every pending request
  on unmount, and a request with the same key aborts the previous one. A response whose request
  was aborted returns `undefined` before the 401 check, so a late old-session 401 never reaches
  the owner's sign-out callback. Sign-in uses its own controller and ignores superseded
  results. No runtime bug found.
- **HTTP client:** without options, buyer requests are byte-for-byte the same path, so their
  headers, body, error mapping and logs are unchanged (Search and Assistant tests unmodified and
  passing). Log lines use fixed templates for all seven routes. Safe error fields are pattern
  checked.
- **Finding R1 (guard defect, not reachable today; FIXED after owner approval, see below):** the
  `/returns`-only token check tests the raw path string, so a path with dot segments passes and
  then resolves elsewhere. Node `URL` check: `/returns/../search/reranked` resolves to
  `/search/reranked`, `/returns/%2e%2e/assistant/answer` to `/assistant/answer`, and
  `/returns/..\search` to `/search`; all pass the current pattern.
  - Not reachable from current callers: every call site uses a fixed path, except the detail and
    batch paths. Their server-supplied parts are `encodeURIComponent`-encoded, and the fixed
    segments before them (`records`, the version, `import-batches`) absorb any `..`, so the
    resolved path stays under `/returns/`. The server's ID patterns also forbid a leading `.`.
    Any leak would be to the same origin only.
  - Smallest proposed fix (`client.ts` only): check
    `new URL(path, window.location.origin)` instead of the raw string. Require the same origin
    and a pathname that matches the pattern; also refuse a raw path containing `\` or a dot
    segment (`/.`, `%2e`). Add the three paths above as refusal cases to `client.test.ts`.

**Stale-session regression tests (new):**
- `ReturnsPage.test.tsx` › session isolation (5): sign in, leave the list request pending, sign
  out, sign in with a second token, then answer the old request through a mock that ignores its
  AbortSignal (its signal is asserted aborted). Old data, an old 500 with its request ID, and an
  old 401 neither repopulate, overwrite nor sign out the new session; the new session sends only
  the new token; timestamps show "(UTC)".
- `useReturnsRequests.test.tsx` (4, new file): the guard's own result, independent of React:
  current result and current 401 reported; superseded response and superseded 401 dropped while
  mounted; data and a 401 after unmount dropped, with `onUnauthorized` never called.
- **Mutant check:** with the `controller.signal.aborted` check removed from
  `useReturnsRequests.ts`, 4 tests failed (the 3 hook guard tests and the page-level late-401
  test). The page-level late-data and late-error tests still passed, because React ignores
  updates to the unmounted old view; the hook tests cover that path directly. The file was
  restored from a scratchpad copy, with the SHA-256 prefix `1090ee56ae10761b` identical before
  and after.

| Command | Result |
|---|---|
| `npx vitest run src/domains/returns src/shared/http` | 92 passed (4 files) |
| same guard tests against the mutant | 4 failed, 5 passed (as intended) |
| `npm run typecheck` | exit 0 |
| `npm test` | **149 passed** (7 files), 0 `act` warnings |
| `npm run build` | exit 0 |

Python evidence was reused, not rerun: 89 focused passed, Ruff clean. No Python file changed in
this review.

### M18-05 R1 fix (2026-10-28; owner-approved before the M18-05 commit)

`frontend/src/shared/http/client.ts` only. A token-carrying request now goes through
`returnsDestination(path)`:
- a backslash or a `.`/`..` segment (plain or `%2e`, any case or mix) in the **path part** is
  refused before parsing; the query is not inspected, so encoded IDs and filter values pass;
- the path is resolved with `new URL(path, document.baseURI)` (the base `fetch` uses). The
  resolved origin must equal `window.location.origin`, and the pathname must be exactly
  `/returns` or start with `/returns/`;
- the request is sent to the resolved `pathname + search`, the destination that was checked.

A refused request throws before `fetch`, so no request, header or log line is produced.
Requests without a token are unchanged: same path string, no resolution, no `Authorization`.

**Tests (`client.test.ts`, +27):**
- **Refused** (each via `getJson`, `postJson` and `postForm`; `fetch` never called; token absent
  from logs): the three demonstrated bypasses (`/returns/../search/reranked`,
  `/returns/%2e%2e/assistant/answer`, `/returns/..\search`), `/returnsx`, `/returnsXYZ`,
  `/returnsXYZ/capabilities`, buyer routes, `https://example.com/returns(/capabilities)`,
  `//example.com/returns`, `http://127.0.0.1:1/returns`, in-`/returns` traversal
  (`/returns/records/../import`, `/returns/./capabilities`, `/returns/%2E%2e/...`,
  `/returns/.%2e/x`), `/returns\capabilities`, and the empty path.
- **Allowed**, each requesting exactly the checked path with the bearer header: `/returns`,
  `/returns/`, the capabilities, validate and batches routes with a cursor; detail paths with
  `FIX-RET-0001`, encoded `A%3AB%2FC` and dotted `SYN.RET..00001`; queries holding `..%2F`,
  `../` and `%5C`; this origin written absolutely; and the relative `returns/capabilities`
  (resolves to `/returns/capabilities` against the base `/`).
- **Unauthenticated control:** `/returns/../search/x` and `/search/reranked` are sent unchanged
  without `Authorization`.

| Command | Result |
|---|---|
| `npx vitest run src/shared/http src/domains/returns` | first run 1 failed: my test listed the relative `returns/capabilities` as refused, but it resolves to this origin's `/returns/capabilities` (allowed by the approved rule); moved to the allowed cases. Rerun **119 passed** |
| `npm run typecheck` | exit 0 |
| `npm test` | **176 passed** (7 files), 0 `act` warnings |
| `npm run build` | exit 0 |

Python evidence reused (no Python change since the 89-passed focused run and clean Ruff).

## M18-04B API import integration (2026-10-28; uncommitted, awaiting review)

- **CI on `6e961855d1d64939570f6111030becddd73c9b71`:** all jobs green, **as confirmed by the
  owner** (not independently verified).
- **Gap closed:** the approved 04B plan listed `tests/integration/test_returns_api_import_integration.py`,
  but 04B shipped with hermetic unit evidence only. The module now exists. It runs the real
  path: HTTP → access → commit gate → `LazyImporterEngine` → existing `validate_import` /
  `commit_import`, as real roles.
- **No production code, auth ordering or contract changed.** No runtime bug or contract
  mismatch was found.
- **Roles:** throwaway `m18t_app_*`, `m18t_imp_*` and `m18t_ro_*`, created by
  `tests/returns_roles.py`.
  - The read-only role receives the API role's grants, applied a second time by
    `apply_grants`.
  - **Admin/superuser rejection through the API is untested.** The dev cluster's admin role
    was not used as a rejected importer, because only disposable roles were authorized.
    Existing evidence covers it below the API only: the M18-03B service test
    (`test_returns_import.py`, superuser → `ImportRoleError("superuser")`) and the CLI test.
  - The importer user equal to the app user is tested at its existing boundary: the
    `Settings` model validator.

| Case | Expected (existing behaviour) | Result |
|---|---|---|
| Local import (gate open), importer token | 201 `imported`; identical repeat 200 `already_imported` with the same `batch_id`; repeat writes nothing; `imported_by_label` = the configured label (DB and batch detail API) | passed |
| Validate as the API role, viewer and importer tokens | 200 `valid`, `error_count` 0, no `batch_id`; all 15 table counts unchanged | passed (2) |
| Viewer token on `POST /returns/import` (gate open) | 403 `forbidden`: the scope check precedes the gate | passed |
| Importer token, commit flag off | 403 `import_commit_disabled`; importer engine never built; `can_commit` false | passed |
| Valid credentials of the read-only role as importer | 403 `import_commit_disabled`; logged `returns_import_role_rejected` with reason `missing_privilege`; counts unchanged | passed |
| Wrong importer password | 503 `import_storage_unavailable` (never connects); no role-rejection event; counts unchanged | passed |
| Importer user = app user | `ValidationError` ("must differ from postgres_user"); no password in the message | passed |

| Command | Result |
|---|---|
| server identity: `system_identifier` from the host connection vs inside the container | equal (`7697502985140146214`) |
| `python -m pytest tests/integration/test_returns_api_import_integration.py -m "integration and not real_model"` (first run) | 1 failed, 8 passed: a **test-helper bug** (`returns_import_commit_enabled` passed twice to `app_settings`); fixed by merging overrides |
| same, rerun | **9 passed** (18 s) |
| database and role lists before vs after both runs | identical; no scratch database or `m18t_*` role left |
| server log since start and both pytest outputs: `SCRAM-SHA-256` / `CREATE ROLE` / `PASSWORD` | 0 / 0 / 0. Two expected `password authentication failed` lines (the wrong-password case, once per run) |
| `ruff check .` / `ruff format --check .` | all checks passed / 520 files formatted |
| hermetic `--collect-only` / integration `--collect-only` | 6092/7178 / 1068/1086, no errors |
| duplicate test basenames; AC-14 protected-path diff | 0; exit 0 |

Not run: the full suite, the Windows isolation wrapper (**FAILED**), CI for this change. Still
open, separately: the three older fixtures' plaintext role passwords, the missing AC-14 M17
listing, the Windows isolation timeout, the unconfirmed SHA of the catalog-failure run, and the
deploy-kit items (M18-06/07).

## Catalog ingest timestamp correction (2026-10-28; outside M18 feature scope; committed `6e96185`, CI green as confirmed by the owner)

**Scope exception (owner-authorized):** a pre-M18 catalog ingestion fix, outside M18 feature
scope. It is a single assignment in `src/ecommerce_search/ingestion/service.py` (last changed in
`2c41a36`, M3) plus one regression test. There is no schema, migration, default or constraint
change, and no seed or AC-14 path is touched.

**CI failure** (owner-reported; the failing run's SHA was not stated; HEAD was `cf122fb`):
`test_concurrent_ingests_of_one_family_cannot_leave_last_writer_wins_corruption`. v2 got
`IngestRefused` with `ck_products_updated_not_before_created` (1 failed, 1057 passed,
18 deselected).

**Demonstrated cause:**
- `persist` updates a changed product with `updated_at = now()`. `now()` is the transaction
  start, and psycopg sends `BEGIN` before the family-lock statement, so that time is taken
  *before* the lock wait.
- If v2 begins first but v1 takes the lock and commits first, v1's rows have
  `created_at = t1 > t0`, and v2's update writes `updated_at = t0 < t1`. The constraint
  rejects it, and the newer version (v2) is lost.
- The bug is latent since M2/M3, not caused by M18.
- It was reproduced deterministically on a throwaway database with forced lock order (no
  sleeps): v2's transaction started about 53 ms before v1's `created_at`, and v2 was refused
  exactly as in CI.

**Fix:** `product.updated_at = func.greatest(func.statement_timestamp(), Product.created_at)`.
- **Timestamp semantics change:** on update, `updated_at` is now the time of the UPDATE
  statement (after the lock is held), not the transaction start. It is never earlier than the
  row's `created_at`, even if the server clock steps back.
- Insert defaults (`created_at`, `updated_at` = `now()`) and `created_at` are unchanged.

**Regression test** (`test_catalog_ingestion.py`):
`test_an_ingest_that_began_before_a_concurrent_commit_still_updates_to_the_newer_version`.
- v1 runs on a single-connection engine holding the family's session-level advisory lock,
  which `persist`'s transaction lock re-enters; v2 waits on the lock (seen in `pg_locks`).
- The test asserts that the race is set up (v2's `xact_start` < v1's `created_at`).
- After v1 commits and the lock is released, it asserts that v2 succeeds, that SYN-LAP-0001
  references version 2 with price 777777, that `created_at` is unchanged, and that
  `updated_at >= created_at`.
- The lock is released and the worker joined in `finally`; the throwaway database is dropped
  by `migrated_engine`.
- The existing concurrent-ingest test is unchanged.

| Command | Result |
|---|---|
| server identity: `system_identifier` from the host connection vs inside the container | equal (`7697502985140146214`) |
| new regression test, original `service.py` | **1 failed**: `IngestRefused (ck_products_updated_not_before_created)`, as in CI |
| new regression test, with the fix | 1 passed; then 3/3 separate reruns passed |
| `python -m pytest tests/integration/test_catalog_ingestion.py -m "integration and not real_model"` | **37 passed** (71 s), including the unchanged original concurrent test |
| database and role lists before vs after all runs (including the failing one) | identical; no scratch database or role left |
| `ruff check .` / `ruff format --check .`; whitespace and secret scan of the diff; AC-14 protected-path diff | see the session report |

Not run: the full integration suite, the full unit suite, the Windows isolation wrapper
(**FAILED**), CI. Still open, separately: the three older fixtures' plaintext role passwords,
the missing AC-14 M17 listing, the Windows isolation timeout, and CI on `cf122fb`.

## M18-04C corrective: CI test race and slot ownership (2026-10-28)

- **CI failure on `2b5e221`** (owner-reported; this session cannot open the private run):
  `test_a_cancelled_caller_keeps_the_slot_until_the_worker_finishes` failed with
  `finished == []` (1 failed, 6082 passed, 4 skipped).
  - **Cause: a test race.** anyio's shield does not stop a native `Task.cancel()`, so the
    caller can finish before the worker. `asyncio.run` then returned while the worker thread
    was still finishing, and the final assertion raced it.
  - The slot itself stayed held until the worker finished (verified by a bounded probe).
  - **Fix:** the test now uses explicit events and a slot subclass that signals its release. It
    asserts the slot is held while the work is blocked, then awaits worker completion and the
    release, with cleanup in `finally`.
- **A separate, real ownership race in `run_in_slot`**, demonstrated in a **controlled unit
  reproduction only** (never observed on a server):
  - The setup: a native cancel lands after anyio's worker has dequeued the job and passed its
    `future.cancelled()` check, but before `guarded` runs.
  - The caller then released the slot, the work ran **without the slot**, and the worker
    released a second time (`RuntimeError`).
  - The reproduction uses a deterministic gate inside the real anyio worker thread; no sleeps.
- **Fix (owner-authorized):** an `ownership` lock decides whether the worker starts (and alone
  releases) or the caller abandons the job (and alone releases; the worker then skips `work`).
  The lock is never held while `work` runs. Caller cancellation behaviour is unchanged and
  implies no rollback.

| Command | Result |
|---|---|
| the fixed race test, 30 separate runs | 30 passed |
| that test against an in-memory mutant `run_in_slot` that frees the slot on cancellation | failed, as required |
| new deterministic slot tests against HEAD's `run_in_slot` (in-memory plugin) | 1 failed (the queued-cancellation case: work ran unowned), 6 passed |
| slot tests with the fix, 20 separate runs (8 tests each) | 20/20 passed |
| `tests/unit/returns/test_returns_api_upload.py` + `test_returns_api_access.py` | **218 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 519 files formatted |

Still open, separately: the three older fixtures' plaintext role passwords, the missing AC-14
M17 listing, and the Windows isolation timeout (**FAILED**).

## M18-04C summary provenance, role-helper hardening and first DB run (2026-10-28; committed `2b5e221`)

**Owner-approved §7 amendment:** `ReturnSummary` gains `provenance`
(`contracts.md` §7 and §7.1; the original wording is quoted there).
- Summaries and the detail now share one source: `pv_` columns from the return's own
  `return_datasets` (`d`), `return_import_batches` (`rb`) and `return_import_files` (`rf`).
- The `batch_id` filter now compares `rb.batch_id` (same meaning as the previous subquery).
- The OpenAPI snapshot diff against the previous review snapshot shows one change only:
  `ReturnSummary` gains a required `provenance`. No path changed.

**Role helper (`tests/returns_roles.py`):** the verifier is treated as a secret.
- The statement goes through the raw DBAPI cursor, so SQLAlchemy (`echo`, `sqlalchemy.engine`)
  never sees it.
- The session first sets `log_statement=none`, `log_min_error_statement=panic`, duration logging
  and sampling off, `track_activities=off` and `pg_stat_statements.track_utility=off`.
  `RESET ALL` runs afterwards. If any guard fails, no `CREATE ROLE` is sent.
- A failure carries only the type and SQLSTATE and is not chained.
- **Server-side check:** the dev server's `log_min_error_statement` is `error`, so without the
  guard a failing `CREATE ROLE` would have logged its full text (verifier included) to the
  container log.

**Dependency on the three older fixtures:** none. The 04C module imports only
`returns_support.scratch`, `returns_release` and `returns_roles`. The plaintext-password issue in
`test_returns_import.py`, `test_returns_dataset_integration.py` and `test_returns_grants.py`
stays **tracked and unchanged**.

**Docker / isolation:**
- Docker Desktop (existing install) was started. `docker compose up -d --no-build --pull never db`
  used the existing image (`pgvector/pgvector:0.8.6-pg16`), container and volume
  (`ecommerce-search_pgdata`); nothing was pulled, built, reconfigured or deleted.
- The server behind the test settings (`127.0.0.1:5432`) was positively identified as that
  container: the same `pg_control_system().system_identifier` from inside the container and from
  the host.
- The fixture creates `ecommerce_search_test_<uuid>`, asserted by the `scratch()` prefix guard,
  and `m18t_app_*`, `m18t_imp_*` and `m18t_probe_*` roles; it drops them afterwards.
- The dev database was not migrated, seeded or queried. Only cluster catalogs were read: the
  database and role lists, and log settings.
- The db container was left running.

| Command | Result |
|---|---|
| affected unit tests (`test_returns_api_read.py`, `test_returns_roles.py`, `test_returns_api_access.py`, `test_returns_api_upload.py`, `test_filtered_api.py`, `test_reranked_api.py`) | **766 passed** |
| first role-test run after the hardening | 1 failed, 8 passed: a **test artifact** (the fixture patched the global `secrets.token_bytes`, so the "random" password equalled the salt); fixed by patching only the helper's `secrets` |
| `python -m pytest tests/integration/test_returns_api_read_integration.py -m "integration and not real_model"` | **29 passed** (33 s), first run |
| databases and roles before vs after | identical; 0 `ecommerce_search_test_*`, 0 `m18t_*` left |
| server log since start: `SCRAM-SHA-256` / `CREATE ROLE` / `PASSWORD` | 0 / 0 / 0. The duplicate-role error was logged **without** a `STATEMENT` line. The two `STATEMENT` lines are my malformed baseline `pg_settings` query and the intentional READ ONLY `CREATE TABLE` probe |
| pytest output: `SCRAM-SHA-256` / `CREATE ROLE` / `PASSWORD` | 0 / 0 / 0 |
| `ruff check .` / `ruff format --check .` | all checks passed / 519 files formatted |
| hermetic `--collect-only` / integration `--collect-only` | 6087/7163 / 1058/1076, no errors |
| duplicate test basenames; AC-14 protected-path diff | 0; exit 0 |

**Still open:**
- the three older fixtures (tracked);
- the AC-14 M17 listing is still missing;
- the focused 1489-test regression set was not rerun after these changes (reused evidence);
- not run: the full suite and the isolation wrapper (**FAILED**), CI.

No commit, push, deployment or acceptance.

## M18-04C review corrections (2026-10-28; uncommitted, awaiting review)

Owner decisions are recorded in `contracts.md` §7.1:
- **M1:** the raw price, typed integer-or-string;
- **I1, I3:** approved;
- **I4:** repeated scalar parameters → 422;
- **I2:** replaced by per-record provenance.

**Fixed:**
1. **Per-record provenance.** Every nested source or evidence record on a detail carries its
   own `provenance`, resolved from its own `dataset_pk`, `batch_pk` and `source_file_pk`:
   - `order` and `order_item`;
   - comments and inspection observations;
   - `shipment` and its delivery events;
   - related reviews and complaints;
   - correction cases.

   The return's own block now takes `dataset_version` from its dataset row, not the path.
   **Reported conflict, not edited:** §7 fixes the `ReturnSummary` field list and AC-05
   requires it exactly, so a summary still carries only `is_synthetic` of the eight. This
   needs an owner decision.
2. **OpenAPI.** The four read routes now document `ReturnsErrorBody`
   (400/401/404/500/503, with `WWW-Authenticate` on 401) and `ReturnsValidationErrorBody`
   (422), instead of FastAPI's default validation body.
   - Found and fixed in the same pass: the 04C batch counts model shared the name
     `EntityCounts` with 04B's `ImportReport` counts, which had renamed 04B's OpenAPI
     component. It is now `BatchEntityCounts`, and `ImportReport` is back to `EntityCounts`.
   - A snapshot diff shows only the four 04C paths changed. The 04A/04B paths are identical,
     and `ImportReport` differs only by that restored ref name.
   - The snapshot still covers all seven `/returns` paths. No 04A/04B runtime code changed.
3. **Secret-safe test roles** (`tests/returns_roles.py`, used by the 04C fixture):
   - The SQL text carries a client-computed SCRAM-SHA-256 verifier, never the password.
   - A failure is re-raised as `RoleSetupError` with only the exception type and SQLSTATE,
     outside the `except` block, so nothing is chained.
   - The fixture's `assert re.fullmatch(..., password)` was removed, because pytest's
     assertion rewriting would have printed the password on failure.
   - **Real defect caught by the new test:** `text()` read `:<base64>` inside the verifier as
     a bind parameter (`StatementError`), so the helper now uses `exec_driver_sql`.
   - **Not changed (pending approval):** the committed 03B/03C fixtures
     `test_returns_import.py`, `test_returns_dataset_integration.py` and
     `test_returns_grants.py` (found by search) keep `CREATE ROLE ... PASSWORD '<plaintext>'`.
4. **Owner decisions applied:**
   - **M1:** `listing_snapshot.price` is the raw `int | str` (strict, no coercion). Null,
     boolean, float, array, object or a missing price is a 500.
   - **I1:** unchanged SQL, now approved, with positive and negative integration tests.
   - **I3:** recorded.
   - **I4:** `_refuse_repeated`, run after access.

**Tests added:**
- **Unit:** M1 typing; nested provenance from the record's own columns (a missing column fails
  rather than borrowing); the nested queries join their own references; the summary pin; I4
  (repeated → 422 without values, unknown ignored, access first); OpenAPI error shapes and
  unchanged 04A/04B responses; the role helper (RFC 7677 vector, SQL text, failure surfaces).
- **Integration (written, NOT RUN):**
  - a split-batch dataset version (`synthetic-returns` 20: parents first, then the rest), so
    a comment's batch differs from its return's batch, plus every nested record compared with
    its stored provenance;
  - I1 for every return of version 1 (positive, and same-product negative);
  - the M1 raw price type.

| Command | Result |
|---|---|
| `python -m pytest tests/unit/returns/test_returns_roles.py tests/unit/returns/test_returns_api_read.py` (first run) | 2 failed, 173 passed: the OpenAPI snapshot (expected before regeneration) and the role failure test (the `text()` bind defect above) |
| `python -m pytest tests/unit/returns/test_returns_roles.py` after the fix | 7 passed |
| snapshot regenerated; diff against the previous snapshot | only the four 04C paths changed; schemas as described above |
| affected unit tests: `test_returns_api_read.py`, `test_returns_roles.py`, `test_returns_api_access.py`, `test_returns_api_upload.py`, `test_filtered_api.py`, `test_reranked_api.py` | **762 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 519 files already formatted |
| duplicate test basenames | 0 |

The role tests use an **in-memory SQLite engine** (no server, no file) to drive SQLAlchemy's
real statement logging and failure path. No PostgreSQL was started or accessed.

Not run: the integration tests (no database), the focused 1489-test regression set,
collect-only, the full suite, the isolation wrapper (stays **FAILED**), CI. No commit, push or
deploy.

## Current state (2026-10-28, M18-04C implemented; uncommitted, awaiting review)

- **HEAD at start:** `64cea6627d48f523651c2e7a5ed32f326f114514` (04B), equal to the local
  `origin/main` ref (not fetched). CI on `d8f2856` and `64cea66` is green **as confirmed by the
  owner**, not independently verified.
- **Decisions:** the owner approved D6a–D6e, D7, D8 (withdrawn) and E1–E5. They are recorded as
  `contracts.md` §7.1. D6–D8 are the M18-04 plan's decisions (2026-10-26), not `plan.md` §8.
- **Added (no 04A/04B behaviour changed):**
  - `api/returns_schemas.py`: query models, response models (exact allowlists) and cursors.
  - `api/returns_read.py`: fixed SQL in one READ ONLY REPEATABLE READ snapshot per request.
    The plan did not list this file; the queries are kept out of the route module.
  - Four routes in `api/returns.py`: `GET /returns`,
    `/returns/records/{dataset_id}/{dataset_version}/{return_id}`, `/returns/import-batches`
    and `/returns/import-batches/{batch_id}`.
  - Four new fixed error codes (`contracts.md` §7.1).
- **Changes to existing tests:**
  - route pins only: the 04A inventory test and the two buyer path sets each gain the four
    routes;
  - one 04A case: `test_unmatched_returns_paths_are_safe_404s` used the bare `/returns` path as
    an unmatched 404, but it is now the list route, so that case is `/returns/records` (still
    unmatched). The other four cases are unchanged.
- **Open for owner review:**
  - **M1 (contract mismatch, D6d):** the raw catalog `price` is an integer on 231 lines and
    seller text (e.g. `"Rs 44,999"`) on 9. `price` is not returned; the owner must decide:
    raw, normalized or omitted.
  - **I1:** the detail `correction_cases` are those citing the return or its comments or
    observations.
  - **I2:** nested records (comments, observations, reviews, complaints, shipment, events)
    carry no per-record provenance; only the return detail has the provenance block.
  - **I3:** the summary and detail `product` is the current catalog record; the snapshot is
    separate, with `matches_current_catalog`.
  - **I4:** unknown query parameters are ignored and repeated scalar parameters take the last
    value (FastAPI default), so the hash and the SQL use the same parsed value.
  - **D6e:** the exact fields in `api/returns_schemas.py` and
    `tests/unit/returns/returns_openapi_snapshot.json`.
- **Integration: NOT RUN.** The Docker engine was not running, and no existing disposable scratch
  database existed (the fixtures create and drop their own). Creating one was not authorized.
  The 24 tests are collected only. Running them needs owner approval to start the local Docker
  `db`; the module then creates a throwaway database and two throwaway roles, as M18-03C did.
- **Checks:**

| Command | Result |
|---|---|
| `uv run pytest tests/unit/returns/test_returns_api_read.py` (first attempt) | blocked: Windows Application Control refused the `pytest` launcher (os error 4551); retried as `python -m pytest` |
| `uv run python -m pytest tests/unit/returns/test_returns_api_read.py` (first run) | 5 failed, 117 passed: test bugs (log assertions without `caplog.set_level`); fixed |
| same, rerun | **122 passed** |
| focused regression (04B set + the new file) | **1489 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 517 files already formatted |
| CI-equivalent hermetic `--collect-only` | 6030/7101, no errors |
| integration-job `--collect-only` | 1053/1071, no errors; new module 24 collected |
| duplicate test basenames | 0 |
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |

  Not run: the integration tests, the full unit suite, the isolation wrapper (its local timeout
  stays **FAILED**, separate from CI), CI, any database. The AC-14 M17 listing is still
  missing; no fresh baseline was taken, since it would not prove the earlier state. No commit,
  push or deploy.

## State after M18-04B (2026-10-28, committed `64cea66`; CI later owner-confirmed green)

- HEAD `d8f2856` (04A, CI green as confirmed by the owner). D1 approved:
  `python-multipart==0.0.32` added. The `uv.lock` diff is only that package and the root
  project's two references to it (11 added lines).
- **C1 and C2 approved and recorded** in `contracts.md` §4.4 (two rows) and §6.3. The
  deadline starts immediately before waiting for the first body chunk, so it covers a sender that
  sends no body bytes. It bounds body reading only, not the worker or database. On the 13th part
  the whole request is rejected and nothing is imported.
- **Added (no 04A behaviour changed):**
  - `api/returns_upload.py`: low-level `MultipartParser` callbacks, all in memory; a streaming
    9 MiB cap (chunked bodies included); a closing-boundary check; part-header rules (C4); an
    `ImportSlot` that is released on every terminal path and held until a worker that is still
    running after cancellation has finished.
  - `POST /returns/import/validate` (validate scope) and `POST /returns/import` (import scope;
    the gate is checked before the body is read, then 403 `import_commit_disabled`). Check order
    (C3): access → gate → 415 → declared 413 → 429 → streamed 413/408/400 → service. Existing
    `validate_import`/`commit_import` are unchanged. Error mappings: storage 503, role 403
    (D3), other 500.
  - `app.state.returns_import_slot` and `app.state.returns_importer_label`.
  - Seven new fixed error texts.
- **Changes to existing files:** route pins only (the 04A inventory test and the two buyer path
  sets each gain the two routes). The capabilities `limits` field set is unchanged.
- **Library logging (fixed):** python-multipart wrote parse warnings containing body byte values
  and offsets. Its only logger, `python_multipart.multipart`, is now disabled, does not
  propagate and has a `NullHandler` (`silence_library_logs`, applied when `returns_upload` is
  imported). No other logger is touched, and the app still logs `returns_upload_rejected` with
  the code. Plain `propagate = False` was not enough: pytest attaches its capture handlers
  directly to non-propagating loggers, and the test caught that.
- Memory: about 2 × 9 MiB per process is an **estimate, not a proven bound**.
- **Checks:**

| Command | Result |
|---|---|
| `uv add python-multipart==0.0.32`; `git diff uv.lock` | only the new package and its root references |
| `pytest tests/unit/returns/test_returns_api_upload.py tests/unit/returns/test_returns_api_access.py` (first run) | 1 failed, 197 passed: a test bug (the body under test was not valid multipart, so the parser failed before the cap); fixed |
| same, rerun | **198 passed** |
| focused regression: `tests/unit/returns` + settings, app, observability, dependencies, engine, compose, frontend serving, `test_filtered_api.py`, `test_reranked_api.py`, assistant API | **1352 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 513 files already formatted |
| CI-equivalent hermetic `--collect-only`; duplicate basenames | 5893/6940 collected, no errors; 0 |

  Not run: the whole `tests/unit/search` directory, the full suite, the isolation wrapper (its
  local timeout stays **FAILED**), integration (no database used), CI. No commit or push.

### M18-04B review checkpoint (2026-10-27, one bounded pass over the actual diff)

**Fixed in this pass:**
- **Library logs:** see above.
- **Huge `Content-Length`:** `int()` refuses strings of more than 4,300 digits, so such a
  header would have become a 500. Declared lengths are now compared by digit count first, so a
  huge value gives 413; leading zeros are ignored.
- **Tests added:**
  - the deadline with a sender that never sends a chunk;
  - library diagnostics reach no handler (root, pytest capture, stderr), with a sanity run
    showing they would be emitted if the logger were enabled;
  - only that one logger is configured;
  - huge declared lengths;
  - `GET` on both import routes gives the safe 405 with `Allow: POST`.

**Confirmed by reading the code and the tests:**
- **Access and gate before the body:** the import routes have no body parameter
  (`body_field is None`), so FastAPI reads nothing before the dependencies. 503/401/403 and the
  gate's 403 `import_commit_disabled` all run before `read_upload`; a spy test confirms no read.
- **Limits:** declared length is checked before the slot. The streaming counter includes the
  epilogue and chunked bodies. Part headers are bounded by the library (count 4, line 4,224
  bytes); the boundary is capped at 256.
- **Parser completion:** a missing closing boundary, an empty body or a disconnect gives 400.
- **Timeout:** `asyncio.timeout` is entered before the first receive and covers the whole read.
- **Single flight:** the slot is released on every terminal path: 413/408/400, cancellation
  while reading (`BaseException` branch), service errors (in the worker's `finally`), and
  cancellation before the worker starts. A worker still running after cancellation keeps the
  slot until it finishes, and cancellation rolls nothing back.
- **No spill:** a 3 MiB part is read with `tempfile` creators and `Request.form` patched to
  fail; no route declares `Form`/`File`.
- **Safe errors:** fixed texts with `no-store` (the route class flag keeps the middleware's
  `no-store`). Service errors map to 503/403/500 without detail.
- **04A and buyer compatibility (not inferred from "added lines only"):**
  - all 04A access tests pass unchanged except the route-inventory pin;
  - capabilities is unchanged (`request_body_bytes` still 9 MiB; `concurrent_imports` 1
    matches the slot);
  - the 04A middleware's 405/404 handling covers the new paths (test);
  - buyer routes: the pinned buyer API files passed (1352-test focused run before this pass);
  - installing python-multipart makes `Request.form` usable app-wide, but no route uses it;
  - the logger change touches only that library's logger, which only `Request.form` /
    `FormParser` would otherwise use.

**Boundaries (not changed here):**
- When the app answers before reading the whole body (413/400/408), it is up to uvicorn to
  close the connection rather than parse leftover bytes. That was not verified with a real
  server.
- ~~A part whose `Content-Disposition` repeats `name=` takes the library's last value.~~
  **Fixed 2026-10-28 (owner-approved, C4):** see "Repeated-name fix" below.
- The memory figure remains an estimate.

**Checks run in this pass:**

| Command | Result |
|---|---|
| `pytest tests/unit/returns/test_returns_api_upload.py` (after the logging change, first run) | 2 failed, 76 passed: `propagate = False` alone still let pytest's handler (attached directly to non-propagating loggers) receive the records, and the handler-list assertion did not allow for pytest's handlers. Fixed by also disabling the logger and asserting by type. |
| same, rerun | 78 passed; after the `Content-Length` fix 79 passed; after the 405 test, with the access file, **204 passed** |
| `pytest tests/unit/returns tests/unit/test_app.py tests/unit/test_observability.py` | **743 passed** |
| `ruff check .` / `ruff format --check .` | all checks passed / 513 files already formatted |
| `git diff --check` on the tracked 04B paths; trailing whitespace and CR count in the two new files | exit 0; none; 0 |
| secret-pattern grep over the 04B diff and new files | one hit: the fake test connection string built from the fake `DB_PASSWORD` |

Not rerun after this pass: the 1352-test focused set and collect-only (one new test file
already counted; no buyer file touched since).

### Repeated-name fix (2026-10-28, owner-approved; C4 unchanged, no contract amendment)

- **Mismatch:** python-multipart keeps only the last of repeated parameters, so
  `form-data; name="x"; name="bundle.json"` was accepted as `bundle.json`. A repeated parameter
  makes `Content-Disposition` invalid (RFC 6266 §4.1; RFC 7578 requires one `name`), and C4 says
  an invalid disposition gets 400.
- **Fix:** `_name_count` uses the standard-library header parser
  (`email.message.Message.get_params`) only to count `name` parameters, in any case and
  including `name*`. Anything other than exactly one gives 400 `malformed_upload`. The name
  itself still comes from python-multipart, which remains the upload parser.
- **Actual parse results checked before the tests** (count / library name):

| Content-Disposition | Count | Library name | Result |
|---|---|---|---|
| `name="x"; name="bundle.json"` | 2 | `bundle.json` | 400 |
| `name="bundle.json"; NAME="x"` / `Name=x` | 2 | `x` | 400 |
| `name="bundle.json"; name*=utf-8''x` | 2 | `bundle.json` | 400 |
| `name*=utf-8''bundle.json` | 1 | none | 400 (no plain name) |
| `filename="a; name=x"; name="bundle.json"` | 1 | `bundle.json` | accepted |
| `filename="a\"; name=x"; name="bundle.json"` | 1 | `bundle.json` | accepted |
| unbalanced `filename="x; name=bundle.json` | 0 | none | 400 |
| `name=""; name="bundle.json"` | 2 | `bundle.json` | 400 |

- **Checks:**

| Command | Result |
|---|---|
| `pytest tests/unit/returns/test_returns_api_upload.py tests/unit/returns/test_returns_api_access.py` (first attempt) | **did not run**: collection error `ImportError: DLL load failed while importing _util_cy: An Application Control policy has blocked this file` (SQLAlchemy's compiled module; Windows policy, not code) |
| same, retried with no change | **213 passed** (9 new cases) |
| `uv run ruff check .` / `ruff format --check .` | all checks passed / 513 files already formatted |

### Commit checkpoint (2026-10-28)

| Command | Result |
|---|---|
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |
| AC-14 M17 SHA-256 listing | **not run**: the listing file is still missing (see the 04A checkpoint). It was not recreated, and nothing here stands in for it. |
| Staged files (12): `pyproject.toml`, `uv.lock`, `api/app.py`, `api/returns.py`, `api/returns_upload.py`, the two returns API test files, the two buyer path-pin files, `contracts.md`, `handoff.md`, `test-matrix.md` | reviewed; `returns.py` has no removed lines; the lockfile adds only python-multipart |
| `git diff --cached --check` | exit 0 |
| secret-pattern grep over `git diff --cached`; staged `.env` | one hit, the fake test connection string; no `.env` |
| `uv lock --check` | exit 0 (lock matches `pyproject.toml`) |

The four unrelated items are not staged. Reused (not rerun): the 1352-test focused run and the
743-test returns/app/observability run. Not run: the full suite, the isolation wrapper (local
timeout stays **FAILED**, separate from CI), integration, database access.

## Current state (2026-10-27, M18-04A reviewed; amendments A1/A2 applied; scoped commit authorized)

- **Owner decision (2026-10-27):** A1 and A2 approved and applied as `contracts.md` §6.2, with
  the strict single-space Bearer parsing recorded there. The wording matches the existing
  implementation (`RETURNS_TOKEN_PATTERN` `[A-Za-z0-9\-._~+/]+=*`, 32–256 characters;
  `parameters` built from `errors.safe_key` names and `[a-z_]{1,40}` codes or `invalid`); no
  implementation mismatch found. Scoped 04A commit and fast-forward push authorized; the commit
  SHA and CI result are reported to the owner (this file is part of that commit).

### M18-04A review checkpoint (2026-10-27, one bounded pass)

HEAD still `ca8b7ab2197c…`; status shows only the 04A files and the four unrelated items. No
commit authorization exists (the 04A approval excluded commits), so **nothing was committed or
pushed**; CI for 04A therefore does not exist.

**Findings and fixes:**
- **Fixed (correctness):** `ReturnsRoute` caught only FastAPI's `HTTPException`, so a
  Starlette `HTTPException` (what Starlette raises, for example while parsing a request body,
  relevant to 04B) would have become a 500. It now catches Starlette's class, which includes
  FastAPI's subclass. New test case `starlette-http-error` → 400 `request_failed`, detail not
  echoed.
- **Confirmed, no change:** order 503 → 401 → 403; all four commit conditions required
  (16-combination test); importer engine built only with the gate open, lazily, as the
  importer role, one connection, disposed in the lifespan; middleware acts only on `/returns`
  and `/returns/…` (no catch-all frontend route can shadow it, and no `--root-path` or prefix
  stripping is configured in the Dockerfile or Caddyfile); buyer responses unchanged.

**Contract compatibility:**
- **Duplicate `Authorization` → 401:** compatible. `Authorization` is a single-value field, so
  two of them are "malformed" (§6). Likewise more than one space after `Bearer` (RFC 6750
  allows `1*SP`) is refused as malformed; standard clients send one space.
- **401 before 422:** compatible, and needed: §6 requires 503 "on every `/returns` route", which
  only holds if access is checked before parameters.
- **Lower-case importer role name:** compatible. §6 sets no form for the setting, and the §2.1
  role `returns_importer` satisfies it. It does not stop a privileged role name such as
  `postgres`; the runtime role check (`ImportRoleError`, D3/D5) stays the control in 04B.
- **Token syntax and 256 cap — amendment A1 proposed, not applied.** §6 says only "≥32 chars";
  the code also refuses tokens over 256 characters or outside the RFC 6750 b64token set (such a
  setting fails at startup). Every token made by the §6 rotation procedure passes. Proposed
  minimal text for the §6 table row: "`RETURNS_VIEWER_TOKEN` (`SecretStr`, 32–256 RFC 6750
  b64token characters)". Alternative: remove the extra checks from the code.
- **422 `parameters` list — amendment A2 proposed, not applied.** §7 fixes the error body as
  `{"detail": str, "code": str}`, while §5.1 says the route-scoped 422 "returns parameter name
  and code only". The two cannot both hold literally; the code adds
  `parameters: [{"name", "code"}]` to 422 `invalid_parameter` only. Proposed minimal text after
  the §7 error-body sentence: "A 422 `invalid_parameter` adds `parameters`, a list of
  `{name, code}` (§5.1)." Alternative: drop the list (then §5.1 is not met).

**Logging across the stack (read only; nothing changed):**
- **App:** the request log writes the route template, method, status and duration only;
  `/returns` events carry fixed codes and the exception type only.
- **uvicorn** (Dockerfile: default logging, access log on): the M24A `AccessLogRedactionFilter`
  strips the query string and client address; uvicorn's access format holds no headers, so
  `Authorization` is never in it. Evidence: the existing `test_observability.py` tests pin the
  format against the installed uvicorn source and check the filter (passed in this review). A
  real uvicorn process was not run.
- **Caddy 2.11.6 (EC2):** no `log` directive, so no access log. Caddy's error log can include a
  request URI with its query string (for example when the app is unreachable); by Caddy's
  documented default it redacts `Authorization` and cookies (no `log_credentials` here). Not
  verified by running Caddy. Caddy's HTTP→HTTPS redirect echoes the path and query; a client
  that sends a token over plain HTTP has exposed it before the app is involved.
- **AWS Terraform (`infra/staging`):** no ALB, CloudFront or WAF access logging is configured.
- **Limitation (corrected 2026-10-27):** in the Caddy error-log case above, the whole query
  string can be written, so **any sensitive query data** can be exposed there: list filters,
  cursors and any value a client puts in a URL (a mis-sent token is only one example). Tokens
  are accepted only in the header. Proxy and deployment configuration is unchanged in this
  task; this stays an open deploy-kit item (M18-06/07).

**Blockers:** no security blocker found. The commit was waiting on owner decisions on A1 and
A2 and on commit authorization; both were given on 2026-10-27 (above).

**Checks run in this review:**

| Command | Result |
|---|---|
| `git rev-parse HEAD`; `git status --short`; `git diff --stat` | `ca8b7ab…`; 04A files + four unrelated items |
| `uv run --frozen python -m pytest tests/unit/returns/test_returns_api_access.py tests/unit/test_observability.py -q -p no:cacheprovider` (after the fix) | **148 passed** |
| same access file `--co` | 123 tests (122 + the new case) |
| `uv run --frozen ruff check .` / `ruff format --check .` | all checks passed / 511 files already formatted |
| `git diff --check` on the tracked 04A paths | exit 0 |
| `git diff --no-index --check /dev/null <new file>` for the two new files; trailing-whitespace grep; final newline | no whitespace errors reported (exit 1 only means the files differ); none; present |
| secret-pattern grep (AWS/GitHub/Google/OpenAI keys, private keys, `KEY=value`, credentialed URLs) over the 04A diff and new files | one hit: the test's fake `postgresql://user:{DB_PASSWORD}@host` message built from the fake `DB_PASSWORD`; no real secret |
| a broad repository grep for logging/proxy settings | stopped after 120 s with no output used; replaced by the targeted reads above |
| Pre-commit (2026-10-27): `git diff --cached --check` on the 12 staged 04A files | exit 0 |
| Pre-commit: secret-pattern grep over `git diff --cached`; staged `.env` check | two hits, both the fake test connection string (in the test and quoted in this table); no `.env` staged |
| Pre-commit: `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |
| Pre-commit: AC-14 M17 SHA-256 listing | **not run: the listing file (own SHA-256 `cf74568d…`) no longer exists**; its session temp directory was cleaned. It was not recreated, because a new listing cannot prove the files are unchanged since before M18-02. The untracked M17 items are not staged or touched. The owner decides how to re-baseline. |

Not rerun: the 3647-test focused set, collect-only and AC-14 (the review changed one source
line and one test file; no protected path). Not run: the full unit suite, the isolation wrapper
(local timeout stays **FAILED**, separate from CI), the integration suite, CI.

## State before the review checkpoint (2026-10-26, M18-04A implemented)

- **HEAD at start:** `ca8b7ab2197ccade8356eb8c5b1e77083485e25d`, as expected. CI on `ca8b7ab`
  is green **as confirmed by the owner**; this session cannot open the private run, so it is
  not independently verified.
- **M18-04 plan:** three bounded subparts (04A access and safe errors, 04B upload/validate/
  import, 04C read APIs and package verification). The owner approved **04A only**, with
  decisions D1–D5 (below). D6–D8 are **not** accepted yet.
- **M18-04A: implemented and focused-verified. Not committed.** 04B and 04C have not started.
  M18 is not accepted.
- Unrelated working-tree items are unchanged: `docs/m12-diagnostic-tooling.md` (modified),
  untracked `.claude/`, `docs/m17-handoff.md` and `evals/results/m17/unsupported-claims/`.
- **Not done:** no database access of any kind (all 04A tests are hermetic), no installs,
  migrations, grants, credentials, deployments, provider calls, AWS actions, commits or
  pushes. The full unit suite, the isolation wrapper and the integration suite were not run;
  the local isolation timeout stays **FAILED**.

### Owner decisions for M18-04 (2026-10-26)

- **D1:** 04B should use a maintained multipart library's low-level in-memory interface, not a
  hand-written parser. Nothing installed in 04A. At the 04B checkpoint: confirm the version,
  the dependency and lockfile changes, and bounded callback behaviour before installing. No
  temp-file spill stays mandatory.
- **D2:** canonical multipart field names; client filenames ignored; unknown or duplicate parts
  rejected under the existing contracts.
- **D3 (provisional for 04B):** 415 `unsupported_media_type`, 400 `malformed_upload`, 503
  `import_storage_unavailable`, and `ImportRoleError` on the API commit path → 403
  `import_commit_disabled`. No database text, credential, token or role detail in responses.
- **D4:** contract correction removing `RETURNS_VIEWER_LABEL` (applied as `contracts.md` §6.1;
  original clause quoted there). A search before the edit found no other use.
- **D5:** importer DB user and password both or neither, and the user must differ from
  `POSTGRES_USER`. This does not replace the runtime least-privilege role check.

### What M18-04A implements

**Settings** (`config/settings.py`):
- `RETURNS_VIEWER_TOKEN`, `RETURNS_IMPORTER_TOKEN`: `SecretStr`, empty = unset, 32–256 RFC 6750
  b64token characters, must differ (compared with `hmac.compare_digest`).
- `RETURNS_IMPORT_COMMIT_ENABLED`: strict `true`/`false`, default false.
- `RETURNS_IMPORTER_DB_USER` (plain lower-case role name) and `RETURNS_IMPORTER_DB_PASSWORD`
  (`SecretStr`): both or neither; the user must differ from `POSTGRES_USER` (D5).
- `returns_importer_database_url()`: the app's host, port and database as the importer role.
- All three secrets are masked at every settings source (existing `_SecretInputSource`);
  validation errors name the field only.

**`api/returns.py`:**
- `ReturnsAccess`: keeps only SHA-256 digests of the tokens (never the tokens). Every presented
  token is compared with both digests with `hmac.compare_digest`. Viewer scopes: `returns:read`,
  `returns:validate`; importer adds `returns:import`.
- `bearer_token`: exactly one `Authorization` header, at most 263 characters, scheme `Bearer`
  (case-insensitive), one space, a b64token of valid length. Anything else is a 401; oversized
  values are refused without hashing. Cookies and query parameters are never read.
- `require_scope`: 503 `retailer_access_not_configured` when neither token is set, 401
  `unauthorized` (with `WWW-Authenticate: Bearer`), 403 `forbidden`. Denials log
  `returns_access_denied` with the code only.
- `CommitGate`: importer token **and** commit flag **and** importer DB credentials **and**
  importer label. `LazyImporterEngine` exists only while the gate is open, builds its engine on
  first `get()` (`db/engine.py` `create_importer_engine`: one connection, no overflow, the app
  timeouts) and is disposed at shutdown. Nothing calls `get()` yet (04B).
- `ReturnsRoute` (route class of the `/returns` router): `ReturnsError` → fixed
  `{"detail","code"}`; `RequestValidationError` → 422 `invalid_parameter` with
  `parameters: [{name, code}]` (safe keys and Pydantic type codes only); other
  `HTTPException` → fixed text (`not_found`, `method_not_allowed` or `request_failed`); any
  other exception → 500 `internal_error`, logging only `returns_internal_error` and the type.
- `ReturnsPathMiddleware` (paths `/returns` and `/returns/…` only, inside the request-ID
  middleware): adds `Cache-Control: no-store` to every response, and replaces responses that
  no `/returns` route produced: an unmatched path → 404 `not_found`; a slash redirect → 404
  `not_found` (its `Location` would echo the query string); a wrong method → 405
  `method_not_allowed` with `Allow` kept. Every other path passes through untouched.
- `GET /returns/capabilities` (read scope, no database access): `contract_version`, `scopes`,
  `can_validate`, `can_commit` (import scope and the gate open), `limits` (§4.4) and
  `reason_codes`. No credential, user, label, host or gate detail.

**App** (`api/app.py`): the middleware, `app.state.returns_access`,
`app.state.returns_importer_engine` (disposed in the lifespan) and the router.

### Interpretations made in M18-04A (for owner review)

1. **Token format:** 32–256 RFC 6750 b64token characters. §6 says only "≥32 chars"; the
   character set keeps a token sendable in a header, and the cap bounds header handling.
2. **Role name:** `RETURNS_IMPORTER_DB_USER` must be a plain lower-case role name.
3. **422 body:** `{"detail","code","parameters":[{"name","code"}]}`. The extra `parameters`
   list implements §5.1 "parameter name and code only".
4. **Repeated `Authorization` headers** are 401 even when both are valid.
5. **Capabilities:** `limits` holds every §4.4 row plus `request_body_bytes` and
   `concurrent_imports`; `reason_codes` is the code list (its version is not a field).
6. **Ordering:** the access dependency runs while FastAPI solves parameters, so an
   unauthenticated request with a bad parameter gets 401, not 422.

### Boundaries reported (not waived; owner review requested)

- **Unmatched `/returns` paths** get 404 `not_found` (and 405 for wrong methods) whatever the
  token configuration: no route exists, so the §6 503/401 rules (which name routes) do not
  apply. They carry `no-store` and the request ID, and echo no path, query or token.
- **Slash redirects under `/returns`** become 404 instead of Starlette's 307 (`/returns` only;
  buyer redirects unchanged).
- **Outside the app:** malformed HTTP or oversized header sections are refused by the server
  (uvicorn/h11) before the app runs; those responses carry no `no-store` and are not covered.
- **Not covered by `ReturnsPathMiddleware` rewriting:** any status other than 3xx/404/405 from
  outside a `/returns` route (none exists today); such a response still gets `no-store`.
- **OpenAPI** (`/openapi.json`, `/docs`) is public and lists `/returns/capabilities`, as for
  every other route.
- **No lockout or rate limit on token guessing** (not in the contract); tokens carry at least
  32 b64token characters (`token_urlsafe(32)` gives 43, about 256 bits).
- **`docker-compose.yml` does not forward `RETURNS_*`** to the api service, so the compose API
  answers 503 on `/returns`. Wiring is deploy-kit work (M18-06/07); not changed here.

### Commands run and actual results (2026-10-26, M18-04A)

| Command | Result |
|---|---|
| `git rev-parse HEAD`; `git status --short` | `ca8b7ab2197c…`; only the four unrelated items |
| `uv run pytest tests/unit/returns/test_returns_api_access.py -q -p no:cacheprovider -x` (first run) | 1 failed, 97 passed: a **test bug**. Log assertions read every logger, and the test client's own `httpx2` logger records full request URLs (the M24A tests already filter it out). Fixed by checking the application's loggers only. |
| same file, after the fix | **122 passed** |
| A one-off in-memory smoke script (TestClient: capabilities, unmatched paths, 405, 401, buyer 404) before the tests were written | ran; behaviour as described above; not a test |
| First focused run: the access file + `test_settings.py`, `test_app.py`, `test_observability.py`, `test_dependencies.py`, `test_engine.py`, `test_compose_config.py`, `tests/unit/search`, `tests/unit/assistant/test_assistant_api.py` | **2 failed, 3126 passed, 3 skipped**. Both failures were the existing app path-set pins in `test_filtered_api.py` and `test_reranked_api.py` (their frozen-operation digests passed). Each pin gained `/returns/capabilities`, as M16 did for its routes; strict equality kept. |
| **Final focused run:** `uv run pytest tests/unit/returns tests/unit/test_settings.py tests/unit/test_app.py tests/unit/test_observability.py tests/unit/test_dependencies.py tests/unit/test_engine.py tests/unit/test_compose_config.py tests/unit/test_frontend_serving.py tests/unit/search tests/unit/assistant/test_assistant_api.py -q -p no:cacheprovider` | **3647 passed, 3 skipped** (3 min 19 s). Skips: symlink permission on this Windows account. |
| `uv run ruff check .` / `uv run ruff format --check .` | all checks passed / 511 files already formatted |
| CI-equivalent hermetic `--collect-only` (same unset prefixes, `ECOMMERCE_SEARCH_HERMETIC_CHILD=1`, empty `POSTGRES_PASSWORD`, `-m "not integration and not real_model"`) | exit 0: **5817/6864** collected (1047 deselected), no errors (+122 from 5695) |
| Integration-job `--collect-only` (placeholder password, no connection) | 1029/1047 collected, no errors |
| Duplicate `test_*.py` basenames under `tests/` | 0 |
| `git diff --exit-code bf67d11 -- <AC-14 protected paths>` | exit 0 |
| AC-14 M17 listing (`ac14-m17-untracked.sha256`, own SHA-256 starts `cf74568d`), `sha256sum -c` | 10 OK, exit 0 |

Not run: the full unit suite, the isolation wrapper (local timeout stays **FAILED**), the
integration suite, CI.

### Files changed by M18-04A (uncommitted)

- **New:** `src/ecommerce_search/api/returns.py`,
  `tests/unit/returns/test_returns_api_access.py`
- **Modified:** `src/ecommerce_search/config/settings.py`, `src/ecommerce_search/db/engine.py`
  (shared connect arguments; `create_importer_engine`), `src/ecommerce_search/api/app.py`,
  `.env.example`, `tests/unit/test_settings.py` (secret-name set),
  `tests/unit/search/test_filtered_api.py` and `test_reranked_api.py` (path-set pins),
  `docs/m18/contracts.md` (§6 row and §6.1 correction, D4), `docs/m18/handoff.md`,
  `docs/m18/test-matrix.md`

### Next task

1. Owner review of M18-04A (interpretations and boundaries above), then a decision on a scoped
   commit.
2. With separate approval: **M18-04B**, starting with the D1 checkpoint (multipart library
   version, dependency and lockfile changes, bounded in-memory callbacks) before any install.

## State at the end of the M18-03C session (2026-10-26)

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
