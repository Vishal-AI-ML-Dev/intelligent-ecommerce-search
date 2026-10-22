# M26A: Search staging demo, progress

Status: **IN PROGRESS.** Slice 1 (local container readiness) is done. Nothing is deployed
online. A container that works locally is not an online deployment, and M26A stays open until
an owner-approved deployment passes the online smoke test.
Scope: `docs/MASTER_PLAN.md` §28 "M26A" and Milestone 26, applied to the existing core search
and the M25A page. The owner approved the plan and authorized **local image work only**
(2026-10-22): no AWS resources, uploads, ECR push, cloud login, Terraform apply or paid calls.

## Slice 1: local container readiness (2026-10-22)

### Delivered

| Item | Decision | Change |
|---|---|---|
| `Dockerfile` | EXTEND | New `frontend` stage (`node:24.22.1-bookworm-slim`, the same Node line as CI) runs `npm ci` from the pinned lockfile and `npm run build`. The runtime stage copies `dist/` and sets `FRONTEND_DIST_DIR=/app/frontend/dist`, so the existing FastAPI serves the UI same-origin (M25A setting, unchanged). It copies only the two pinned registry snapshots (embedding ADR-006 `1110a243…`, reranker ADR-007 `233902d2…`) and their manifests into `/app/models` (root-owned, read-only for the app user), and sets `EMBEDDING_MODELS_DIR=/app/models`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`. Still runs as non-root `app` (uid 1000). Python dependency pins (`uv.lock`), healthcheck and command are unchanged. |
| `.dockerignore` | EXTEND | Also excludes `.github`, `.claude`, `*.pem`, `*.key`, coverage, logs, freeze staging, `evals/`, `scripts/`, frontend `node_modules`, `dist`, `test-results` and `playwright-report`. `models/` stays excluded except for re-includes of exactly the two pinned snapshots and their two manifests. `.git`, `.env*`, `data/`, `tests/` and `docs/` were already excluded. |
| `frontend/playwright.config.ts` | EXTEND | Optional `E2E_BASE_URL`. When set, Playwright drives an already running server (here the container) and starts no local server. When unset, behaviour is unchanged. |
| `tests/unit/test_compose_config.py` | EXTEND | The M4 test "weights are never copied into the image" is replaced by an exact check: only the two pinned snapshots and manifests are re-included and copied, offline variables are set, and the user is non-root. A new test checks that secrets and private data stay out of the build context. |
| `docker-compose.yml`, `README.md`, `docs/architecture.md` | Comment and docs only | Record that M26A amends the M4 rule. Compose behaviour is unchanged: its read-only `./models:/models` mount and `EMBEDDING_MODELS_DIR=/models` override the baked snapshots. |

No search, API, settings or migration code changed. No second backend.

### Model packaging check (before and inside the image)

- `model-verify --kind embedding` and `--kind reranker` ran **inside the built image** with
  `--network none` and `--models-dir /app/models`. Both returned `verify: ok (offline; manifest
  and files match)`: strict file set and SHA-256 equal to the manifests written at fetch time.
- Licence: both model cards (`README.md`, packaged) declare `license: apache-2.0` in their front
  matter, matching `declared_license` in both manifests. Neither pinned revision ships a separate
  `LICENSE` file, so the model card is the only licence record in the image (see limitations).
- The image's `/app/models` holds 20 files (175 MB): the two snapshots plus two manifests. The
  bge-small and e5-small snapshots and the hub `trees/` / `CACHEDIR.TAG` files are not in it.
- No model was downloaded and no snapshot was changed. The build downloaded only base images,
  pinned npm packages (lockfile) and pinned Python wheels (`uv.lock`).

### Verification (local, Windows 11, Docker Desktop engine 29.8.2)

Database: the existing `ecommerce-search_pgdata` volume, started with `docker compose up -d db`.
It was only read: Alembic `0004`, 240 products, 240 search documents and 240 embeddings. Nothing
was migrated, seeded or re-embedded, `alembic upgrade head` was not run, and the `migrate`
service was not started.

| # | Check | How | Result |
|---|---|---|---|
| 1 | UI + health served | `GET /` and `GET /health` inside the container, and `curl` on the published port | PASS: `/` 200 `text/html` with Content-Security-Policy; `/health` 200 with database and pgvector `ok`; Docker healthcheck `healthy` |
| 2 | Ranked search + `X-Request-ID` | `POST /search/reranked` on 2 seeded queries | PASS: 200, 10 results each, reranker `applied`, an `X-Request-ID` on every response (for example `36da8130-…`) |
| 3 | No runtime downloads | Container on a temporary `--internal` Docker network that can reach only the db; DNS for `huggingface.co` failed inside it | PASS: both models loaded and search ran with no egress |
| 4 | Browser smoke against the container | `E2E_BASE_URL=http://127.0.0.1:8766 npx playwright test --project=live` | PASS 1/1 (seeded query through the page, real API, reranker `applied`, cards equal to the response). Fixture project against the container: 6/6 |
| — | Regression | `pytest tests/unit/test_compose_config.py tests/unit/test_frontend_serving.py` | 37 passed |
| — | Lint and format | `ruff check .`, `ruff format --check .` | All checks passed; 414 files formatted |
| — | Frontend | `npm run typecheck`; `npm run build` plus fixture Playwright in default (local server) mode | PASS; 6/6 |
| — | Compose | `docker compose config -q` | PASS |

The full backend suite was not re-run (not requested; no backend code changed).

### Measurements (measured, single local runs, not benchmarks)

| Metric | Value | Source |
|---|---|---|
| Image size | 2.40 GB uncompressed (2,403,598,369 B); 609 MB content size | `docker image ls` / `inspect` |
| Image breakdown | `.venv` 1.4 GB (torch CPU), models 175 MB, UI 244 KB | `du` in the image |
| `docker run` to first `/health` 200 | 2.82 s | Wall clock, one cold start |
| First `/search/reranked` (cold models) | 6.0 s wall; `model_load_ms` 5,405, `reranker_load_ms` 248 | Response `latency_ms` |
| Warm `/search/reranked` | 365 ms wall (`total_ms` 359) | One request |
| Memory after startup (models not loaded) | 89.5 MB (`memory.peak`) | cgroup v2 |
| Peak memory, offline run (2 searches) | 523,468,800 B ≈ 499 MiB | cgroup `memory.peak` |
| Peak memory, browser-smoke container | 495,091,712 B ≈ 472 MiB | cgroup `memory.peak` |

Models load lazily, so the first search after start pays about 5.7 s. A deployment should send
one warm-up search after the health check passes.

**Fargate sizing (estimate, not measured on Fargate):** about 0.5 GiB peak for one task, one
worker and sequential requests suggests 1 GB could fit with little headroom. The plan's
1 vCPU / 2 GB stays the conservative choice until concurrency and Fargate CPU are measured.
Cold-start time on Fargate will also include the image pull (609 MB compressed), which was not
measured.

### Commands executed (all local)

`docker build -t ecommerce-search:m26a-local .` (pass); `docker run --network none … model-verify`
×2 (ok); `docker compose up -d db` plus read-only `psql` counts; `docker network create --internal
m26a-noegress`, `docker network connect`, an offline container run and probe, then the container
and network removed and the db disconnected; `docker run -p 127.0.0.1:8766:8000 --network
ecommerce-search_default …` with Playwright live and fixture; pytest, Ruff, `npm run
typecheck`/`build`, fixture Playwright, `docker compose config -q`. Credentials were passed to the
container from `.env` by variable name only and never printed or written.

### Limitations

- Building the image now **requires** both pinned snapshots under `models/` (run `model-fetch`
  first). This also applies to `docker compose build`. CI does not build the image.
- The image is about 2.4 GB, mostly CPU torch. Size was not optimized.
- Licence evidence is the model-card front matter only; no `LICENSE` file exists at either pin.
- Measurements are single runs on one Windows/WSL2 host, not Fargate.
- The image is tagged `ecommerce-search:m26a-local` locally only. It was not pushed anywhere.

## Remaining M26A deployment gaps (each needs owner decisions or approval)

AWS account and region; budget and billing alarm; domain or CloudFront URL; always-on or
scale-to-zero; Terraform state location; IAM permissions; Terraform for ECR, ECS/Fargate, RDS
pgvector, ALB/ACM, WAF, Secrets Manager and CloudWatch (not written yet); a least-privilege DB
user; a one-off migrate/ingest/embed task on RDS; ECR push; online smoke test over HTTPS;
rollback drill. No chargeable action happens before explicit approval.
