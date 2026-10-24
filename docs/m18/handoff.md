# M18 — Handoff

## Current state (2026-10-24)

- **Baseline:** M18-01 design committed as `docs(m18): freeze return data foundation design` (parent `bf67d11`); not pushed.
- **M18-01 Contracts, Dataset & Acceptance Design:** **FROZEN.** The owner approved the
  revision-2 design on 2026-10-24 with qualifications Q1–Q5 (`plan.md` §8).
- **MASTER_PLAN:** only edits C1–C3 applied (§9 entity note, §10 item 7 note, §28 M18 row),
  docs only, in the baseline commit. C7 (staging status line) is pending M18-09.
- **Staging:** M18 targeting the existing EC2 stack is agreed in principle. Deployment, staging
  migrations, grants changes, imports and credential changes are **not authorized**
  (`plan.md` §9). No M18 deployment has happened.
- **Completed ACs:** none (design package). **Tests run:** none.

## Files in the M18-01 baseline commit

- `docs/m18/plan.md` (new)
- `docs/m18/contracts.md` (new)
- `docs/m18/test-matrix.md` (new)
- `docs/m18/handoff.md` (new)
- `docs/MASTER_PLAN.md` (C1–C3 only)

## Next task: M18-02 Storage & Migrations

Scope (`plan.md` §3; contracts §1–3; ACs AC-01–AC-05, plus the AC-02 concurrency backstops and
the AC-13 privilege checks):

1. `src/ecommerce_search/returns/`: Pydantic contracts and enums for `return-data-v1`;
   canonicalization and hashing helpers (§4.6). No import service, API or UI.
2. ORM models and migration `0007`:
   - the provenance, file-metadata, restricted file-contents and eleven entity tables;
   - composite FKs per dataset version, UNIQUE one-shipment-per-order and the 152-character
     evidence key;
   - append-only triggers and the deferred quantity trigger.
3. A versioned, parameterized grants script per contracts §2.1 (Q3):
   - preserve every pre-M18 `search_app` privilege;
   - revoke raw-contents access only;
   - grant `returns_importer` explicitly.

   Do not change `setup.sh`/`restore.sh` (M18-07).
4. Tests:
   - enum sync and canonical forms;
   - FK/UNIQUE/CHECK and direct-SQL backstops;
   - upgrade/downgrade to `0006` with schema equality;
   - append-only;
   - exact column allowlists;
   - real LOGIN-role privilege, preservation and raw-revoke tests;
   - update the head table-set test.
5. Run Ruff, the hermetic unit suite, and the integration suite (`not real_model`) on scratch
   databases only.

**Before starting:**
- Take the SHA-256 listing of the untracked M17 outputs (AC-14), outside the repo.

**Do not:** touch the development or staging databases, change deploy files or credentials,
call providers, or start M18-03.
