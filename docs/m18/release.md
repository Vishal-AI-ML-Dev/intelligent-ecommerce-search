# M18 — Release, migration and recovery runbook

Status: **prepared and rehearsed locally (M18-07, 2026-10-28). Not deployed.** Staging release
is M18-08 and needs explicit owner approval. No AWS, SSM or staging command was run for this
document.

## 1. What the release changes on the single-EC2 stack

| Item | Before (M26A kit) | After (this kit) |
|---|---|---|
| Schema | `0006` (expected; **not verified on staging**) | `0007` (M18 return data, additive) |
| API role grants | blanket `GRANT SELECT ON ALL TABLES` in `setup.sh` and `restore.sh` | versioned grants script `returns-grants-1` (contracts §2.1): pre-M18 reads kept explicitly, M18 reads added, **no access to raw return files** |
| Role passwords in setup | plaintext in `CREATE/ALTER ROLE` SQL | client-computed SCRAM verifiers, sent only after log guards (`log_statement`, `log_min_error_statement`, duration logging, `track_activities`, `pg_stat_statements.track_utility`); no role is changed if a guard fails |
| Roles | `search_admin`, `search_app` | + `returns_importer` (NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; NOLOGIN until a password is supplied) |
| App container | no returns settings | `RETURNS_VIEWER_TOKEN` only; `RETURNS_IMPORT_COMMIT_ENABLED` fixed `"false"`; no importer token or importer DB credentials |
| Data load | none | one-off `returns-import` as `returns_importer`, from checksum-verified files mounted read-only |
| Caddy logs | error log could contain the full request URI, query included | the default logger drops everything from `?` in `request>uri`; credential headers stay `REDACTED` (Caddy default) |
| Restore | `pg_restore --clean` into the live database, then the blanket grant | database dropped and re-created empty, then grants by revision (`0007`+: grants script; older: the pre-M18 grants) |

## 2. Files

`deploy/ec2/`:
- `compose.yaml`: `setup` (unchanged service, plus importer variables), new profile-`m18`
  one-offs `grants` and `returns-import` (image `M18_TOOLS_IMAGE`, falling back to `APP_IMAGE`),
  and `app` returns settings.
- `m18-release.sh` (new). `setup-db.sh` (refuses a non-empty database), `restore.sh`,
  `update.sh` (comment only: it is not a substitute), `backup.sh` (directory override only).
- `Caddyfile`.

`infra/staging/setup.sh` is shared with the M26 ECS task (Terraform unchanged; it still runs the
script with no argument, i.e. full setup). Every script honours `M26A_DIR` (default
`/opt/m26a`), which exists only so the kit can be rehearsed locally.

## 3. Release order (M18-08, after approval)

Preconditions:
- CI is green on the release commit.
- The owner approves the M18-08 window.
- The deployed image tag and staging revision are recorded first. The read-only staging check
  was deliberately not done in M18-07.

1. **Copy the kit** to `/opt/m26a`:
   - `compose.yaml`, `Caddyfile`, `setup.sh` (from `infra/staging/`), the scripts;
   - `returns_v1/provenance.json` and `returns_v1/bundle/returns_v1.json` from the release
     commit.
   - Check the SHA-256 on both sides.
2. **Add to `.env` on the server** (generated there, mode 600, values never printed):
   - `RETURNS_VIEWER_TOKEN`: `secrets.token_urlsafe(32)` or `openssl rand -base64 32` without `/+=`;
   - `RETURNS_IMPORTER_DB_PASSWORD`: hex or url-safe;
   - `RETURNS_IMPORTER_LABEL`, for example `m18-release`.
3. **Upload the image archive** (`docker save <tag> | gzip -1`), with SHA-256 checked on both
   sides.
4. Run `sudo ./m18-release.sh <archive> <tag> <provenance.json sha256>`. The expected provenance
   SHA-256 is computed from the release commit:
   `git show <commit>:data/seed/returns_v1/provenance.json | sha256sum`.

   The script:
   1. **Preflight** (no writes):
      - the three `.env` keys are present;
      - the `provenance.json` SHA-256 matches the argument;
      - the bundle matches `provenance.bundle_sha256.json`, computed as in contracts §4.6:
        SHA-256 of `return-data-v1\n` + `bundle.json\0<file sha256>\n`;
      - the database revision is `0006` or `0007`.
   2. **`backup.sh`**: the recovery point. Then it loads the image.
   3. **`setup.sh --release`** with the new image: `alembic upgrade head`, roles, grants script.
      The running app is not touched. The revision must then be `0007`.
   4. **Writes `M18_TOOLS_IMAGE=<tag>`** to `.env`, so the tools survive an app rollback.
   5. **`returns-import import`, then `returns-import verify`**, as `returns_importer`. Both are
      idempotent on re-run.
   6. **`grants` one-off** (re-applied and verified). Then `APP_IMAGE=<tag>` and
      `docker compose up -d app`.
5. **Smoke (AC-19):**
   - `/health`, ranked search, assistant;
   - `/returns/capabilities`: 401 without a token, 200 with the viewer token
     (`can_commit: false`);
   - `POST /returns/import` with the viewer token: 403;
   - the list count equals the import report (180 returns);
   - `returns verify` gives `match` against `records_sha256` `497cebbd…63ca`;
   - after `docker compose restart app`, the same data is served.

**Not supported:**
- `update.sh` alone for this release: it neither migrates nor applies grants.
- `setup-db.sh` on an existing database: it now refuses.
- **Never run the M26A-era `setup.sh` (blanket grant) after `0007`.** With the new kit it is
  replaced. With an old image, `alembic upgrade head` fails on the unknown revision `0007`, so the
  script stops before any grant.

## 4. Rollback and recovery

| Situation | Action | Rehearsed locally |
|---|---|---|
| App problem, schema fine | Application rollback: set `APP_IMAGE` to the previous tag and run `docker compose up -d app` (or `update.sh` with the previous archive). The old image runs against `0007` unchanged (AC-18). `/returns` is then not served (404). | yes (P3) |
| Data must return to before the release | `sudo ./restore.sh backups/<pre-release dump>`, then roll the app back as above. Destroys every write after the backup, M18 data included. Needs owner approval at the time. | yes (P4: revision `0006`, 0 M18 tables left, `search_app` SELECT on 19/19 tables, old image passes) |
| Restore after the release | `sudo ./restore.sh backups/<0007 dump>`. The grants script is re-applied (`M18_TOOLS_IMAGE` must name an image with the returns tools). | yes (P4: counts and privilege rows equal; raw files unreadable) |
| Schema downgrade | `alembic downgrade 0006` drops the M18 tables and data. Not rehearsed in M18-07; use the pre-release dump instead. | no |

## 5. Local rehearsal evidence (M18-07, 2026-10-28)

**Artifacts:**

| Artifact | Identity | Notes |
|---|---|---|
| Release candidate | `ecommerce-search:m18-rc-7c87532`, image ID `sha256:1fec521b044a6c3db5347eadcb561081a32c4db1d00d163fa236c39da817029c`, created 2026-10-28T12:59:06Z | Built locally from the working tree at HEAD `7c87532`. Every path the Dockerfile copies equals HEAD (`git diff HEAD` on `src`, `migrations`, `alembic.ini`, `frontend/src` and config, seed catalog, `models/manifests`, `Dockerfile`, `.dockerignore`, `uv.lock`, `pyproject.toml`, `frontend/package*.json`: no difference). Uncommitted at build time: deploy kit, compose files and docs only, none of which are copied into the image. **It is not a clean committed or CI-built artifact.** **The build downloaded 76 Python packages (`uv sync --frozen`) and 108 npm packages (`npm ci`), all pinned by the unchanged lockfiles, plus the pinned base layers (`python:3.12-slim-bookworm`, `node:24.22.1-bookworm-slim`, `ghcr.io/astral-sh/uv:0.11.12`). Those package downloads were not authorized. They were reported, and the owner accepted continued use of this artifact only, with no further downloads.** |
| Old image | `ecommerce-search:m16-geo-e057d00`, image ID `sha256:ecaeb63335ce1c40a151bf2ba1d2bc61bfd97d2d4acc7a570d955c80b8623503`, created 2026-10-23T06:18:26Z | Local tag; **not verified to be the digest deployed on staging.** It was not rebuilt. |
| Kit at `e057d00` | `deploy/ec2/*`, `infra/staging/setup.sh`, `frontend/e2e/live.spec.ts` | Identical at `e057d00` and HEAD before M18-07 (`git diff e057d00 HEAD` empty), so "the old image's own live Playwright path" is the same spec file. |
| Others | `pgvector/pgvector:0.8.6-pg16` (`sha256:ccc6e83d…fb4d6b`), `caddy:2.11.6` (`sha256:3422ce6d…72d73`) | Already local; not pulled. |

**Isolation:**
- Each run is one compose project `m18rel-<hex>` with `M26A_DIR` set to its scratchpad
  directory and a `compose.override.yaml` that makes the project network **internal**.
- From the app container, the egress probe got `Network is unreachable`.
- One run-owned edge network and one `caddy reverse-proxy` container publish
  `127.0.0.1:<random port>` to the host.
- Before every destructive step, a guard checked all of: the kit directory is this run's;
  `pwd` equals it; the project is `m18rel-*`; the `db` container belongs to the project and is
  not `ecommerce-search-db-1`; the database volume is the project's.
- Teardown (EXIT trap): the proxy is removed; `docker compose down -v` runs for that project
  only; the edge network is removed; container, volume and network lists are compared with
  before; the run directory is deleted.
- The dev database container and volume were never touched.

**Results** (attempt 4, all passed):

| Phase | Check | Result |
|---|---|---|
| P0 staging today | old kit and old image: setup on an empty database | revision `0006`, 240 products, documents and embeddings `CURRENT` |
| P0 | old image buyer path | health 200; ranked search 200, 10 results, reranker `applied`; assistant 200, generation `disabled` |
| P1 release | `setup-db.sh` on the populated database | refused (19 tables), nothing run |
| P1 | `m18-release.sh - <candidate> <provenance sha>` | **PASS**. Preflight passed (revision `0006`, bundle `d7a9f535…1ffb`). Migration `0006 → 0007`, roles (verifiers) and grants (`returns-grants-1`, 68 statements) took **7 s**, which is the AC-18 migration duration. Whole script 29 s. Import `imported` (batch `f5d4b00f…`, 0 errors); verify `match` (`497cebbd…63ca` both sides). |
| P1 | re-run of `m18-release.sh` at `0007` | **PASS**: import `already_imported`; verify `match`; grants re-applied; step 3 took 6 s |
| P2 AC-19 rehearsal | `/returns` with the new image | 401 without a token and with a wrong token; viewer 200, scopes `returns:read, returns:validate`, `can_commit: false`; viewer `POST /returns/import` 403 `forbidden`; 180 listed, 180 unique; 1 batch |
| P2 | buyer path with the new image | health, ranked search (10 results, `applied`), assistant generation `disabled`; `live.spec.ts` **1 passed** |
| P2 | `docker compose restart app` | same 180 returns and 1 batch served |
| P3 AC-18 | **old image** on `0007` with the new grants and M18 data, no egress, generation off | health 200; ranked search 200, 10 results, `applied`; assistant 200, `disabled`; `/returns/capabilities` 404; its live Playwright spec **1 passed** |
| P4 backup / restore | `backup.sh` dump → new database `restore_check` → grants script | **34/34 table counts equal** (for example `returns=180`, `orders=600`, `delivery_events=2365`, `products=240`, `return_import_file_contents=1`); **985/985 privilege rows** (table and column, for `search_app`, `returns_importer`, PUBLIC) equal; raw return files readable by `search_app` or the importer: **false** in both |
| P4 | `restore.sh` with the `0007` dump on the live database | counts and privilege rows equal to before; returns served (180) |
| P4 | `restore.sh` with the **pre-release** dump (rollback) | revision `0006`, **0 M18 tables left**, `search_app` SELECT on 19/19 tables; the old image then passes the buyer checks |
| Cleanup | containers, volumes and networks | equal to before; 0 project containers |
| Secrets | scan of 35 run log files for the 4 run secrets | 0 hits (values never printed) |

**Earlier attempts (none reached a destructive step; teardown clean each time):**
1. My driver passed a Git Bash `/tmp/...` path to Windows Python, so it stopped before any
   container was created.
2. My driver's `git -C /d/...` failed under `MSYS_NO_PATHCONV`, so the expected provenance
   checksum was empty. **The release preflight correctly stopped** ("provenance.json does not
   match"), after P0 and before any write.
3. **Kit defect found and fixed:** the first `m18-release.sh` compared the raw file SHA-256 with
   `provenance.bundle_sha256.json`, but that value is the contract's derived `bundle_sha256`
   (§4.6). The preflight stopped ("returns_v1.json does not match"). Corrected to compute the
   §4.6 form, then attempt 4 passed.

**Caddy:** see §1.
- Verified with the real `caddy:2.11.6` and this Caddyfile, upstream unreachable: 3 error entries,
  each `uri` without its query; `Authorization` and `Cookie` `REDACTED`; synthetic query, auth
  and cookie markers: 0 hits.
- Negative control with the previous Caddyfile: the query marker was logged (1 hit).
- `caddy validate`: valid.

## 6. Limitations

- **The release candidate is a local build from a working tree**, with unauthorized (since
  accepted) lockfile-pinned package downloads. M18-08 should deploy an image built from the
  committed release commit; that build is not authorized here.
- **The old image is a local tag**, not verified against the staging digest. The staging
  revision (`0006`) is also unverified. Both are to be read in M18-08 before any write.
- **Caddy:** the automatic HTTP→HTTPS redirect still echoes the path and query to a client that
  sends a request over plain HTTP; nothing is logged, but the URL has already left the client.
  Caddy was tested with an unreachable upstream only.
- **`setup.sh` on the M26 ECS/RDS path is unverified:** the RDS master user may not be allowed
  to set `log_statement` or `log_min_error_statement`. The script then stops before any role
  change (fail-closed) instead of sending a verifier unguarded. Terraform was not changed and
  that stack is not deployed.
- The app's existing `search_app` password is re-set by `setup.sh --release` (pre-M18 setup
  behaviour, now as a verifier). No other credential rotation was done.
- `alembic downgrade 0006` was not rehearsed. Staging database durability and restore are not
  tested (AC-19 checks only an app-container restart).
- Rehearsal on Windows Docker Desktop with Git Bash; the EC2 host (Ubuntu, `sudo`) was not
  exercised.
