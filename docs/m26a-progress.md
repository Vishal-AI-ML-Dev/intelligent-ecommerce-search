# M26A: Search staging demo, progress

Status: **IN PROGRESS.** A single-EC2 staging demo is online at
<https://intelligent-ecommerce-search.duckdns.org/> (2026-10-22, slice 3). It runs on Free-plan
credits until cleanup; it is not a permanent URL. A container that works locally is not an online deployment, and M26A stays open until
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

## Slice 2: Terraform prepared locally, not applied (2026-10-22, uncommitted)

**Region (verified):** the account uses AWS's new project-based experience. The project is
pinned to **ap-southeast-2 (Sydney)**. Read-only calls in us-east-1 return
`UnauthorizedOperation`. The AWS doc "AWS Regions for your projects" says Regional resources are
created only in the project Region, and **AWS WAF cannot be used with CloudFront**. CLI identity:
an assumed project role, used through the `m26a` `aws login` profile (temporary credentials). In
ap-southeast-2, list/describe calls succeeded for EC2/VPC, ECS, ECR, ELBv2, RDS, Secrets Manager,
CloudWatch Logs, regional WAF, CloudFront, S3 and Budgets. This shows read access only, not that
creation is allowed. RDS PostgreSQL 16.9–16.15 and `db.t4g.micro` are offered there.

**Configuration** (`infra/staging/`, Terraform 1.17.0, `hashicorp/aws` 6.69.1, `hashicorp/random`
3.9.1, lock file for windows_amd64 + linux_amd64):

- `bootstrap/`: the private state bucket only. Versioned, SSE-S3, public access blocked, TLS-only
  policy, `prevent_destroy`. Its own local state is git-ignored.
- Main configuration (S3 backend with `use_lockfile`; the bucket is supplied by a git-ignored
  `backend.hcl`):
  - Network: VPC with 2 public and 2 private subnets and no NAT.
  - Security groups: ALB accepts only the CloudFront origin-facing prefix list; the app accepts
    only the ALB; the database accepts only the app.
  - ALB: returns 403 unless the CloudFront origin-verification header is present. Regional WAF on
    the ALB: a per-client rate limit on `X-Forwarded-For`, plus a total `/search` rate limit as a
    backstop.
  - CloudFront: `*.cloudfront.net` HTTPS, caching disabled.
  - ECS/Fargate: one x86 task at 0.5 vCPU / 2 GB, circuit-breaker rollback, desired count 0 or 1.
  - RDS: PostgreSQL 16.15 on `db.t4g.micro`, 20 GB gp3, encrypted, private, 1-day backups,
    deletion protection plus `prevent_destroy`, final snapshot.
  - ECR: immutable tags, scan on push, keeps the last 5 images.
  - CloudWatch Logs: 14-day retention.
  - Budgets: the $10 monthly alert, with credits excluded.
- Credentials:
  - RDS generates and stores the master password (`manage_master_user_password`).
  - The app role's password is an ephemeral, write-only value, never stored in state or plans.
  - Two execution roles, each limited to its own secrets. No task role.
  - Outputs hold identifiers only.
- `setup.sh`, run by the one-off `setup` task as the master user: `alembic upgrade head`, ingest,
  embed, both `--require-current` checks, then the `search_app` role with **SELECT only**.

**Image change:** the image now also carries `data/seed/catalog_seed_v1.jsonl` and its provenance
file (the only `data/` files; tested). `infra/` is excluded from the build context.

**Checks:**

| Check | Result |
|---|---|
| `terraform fmt -check -recursive` and `terraform validate` (both configurations) | PASS |
| `bash -n setup.sh` | PASS |
| `pytest tests/unit/test_compose_config.py` | 29 passed |
| Ruff check and format | PASS |
| Image rebuilt locally; seed files present | PASS |
| `setup.sh` in the image against a throwaway local pgvector 16 container (SSL on), run twice | 240 products, 240 documents, 240 embeddings, both checks `CURRENT`, role created |
| API as `search_app` | `/health` ok, `/search/reranked` 200, 10 results, reranker `applied`, same top 3 as the slice 1 run |
| `search_app` writes | DELETE and CREATE TABLE refused |

The throwaway containers and network were removed.

Provider downloads from `registry.terraform.io` kept failing over IPv6 (connection reset). The
providers were downloaded over IPv4 from `releases.hashicorp.com`, their SHA256SUMS signatures
verified with HashiCorp's key, and installed from a local mirror. Deployment will need the same
workaround or a working network path.

**Estimated always-on cost** (ap-southeast-2 on-demand rates from the AWS Price List API; 730
h/month; traffic-dependent parts estimated):

| Item | Rate | ~USD/month |
|---|---|---|
| Fargate, 0.5 vCPU / 2 GB | $0.04856 per vCPU-h, $0.00532 per GB-h | 25.5 |
| ALB | $0.0252/h + LCUs at $0.008 | 19–24 |
| RDS `db.t4g.micro` + 20 GB gp3 | $0.025/h, $0.138/GB-month | 21.0 |
| Public IPv4 ×3 | $0.005/h each | 11.0 |
| WAF (ACL + 2 rules) | $5 + $1 + $1 | 7.0 |
| Secrets ×2 | $0.40 each | 0.8 |
| Logs, ECR, S3 | unverified | ~1 |
| CloudFront | assumed always-free tier, unverified | 0 |
| **Total** | | **~86–91** |

$100 in credits therefore lasts about **33–35 days** always-on. When credits run out, AWS closes
the account.

## Owner cost constraint (2026-10-22, overrides earlier deployment approvals)

- At most **$95 of AWS usage in total over the demo's whole lifetime** (not per month), and
  **zero out-of-pocket payment**. Never upgrade the Free plan, enable paid billing or increase
  resources.
- Deployment may proceed only if an **AWS-enforced** limit that satisfies this can be verified
  for the account. Budget alerts and delayed or automated cleanup are not caps.
- **Finding (2026-10-22): no such limit is available, so deployment is STOPPED.**
  - AWS spend limits (AWS Settings, per project) pause a project when it reaches its limit, but
    they require a Paid Plan, and the minimum limit is $20 or AWS's estimate of likely spend,
    whichever is higher ([Create a spend limit](https://docs.aws.amazon.com/accounts/latest/reference/create-spend-limit.html)).
    Upgrading the plan is not allowed.
  - The Free plan states that no charges are incurred, and the account closes when the credits
    are used up or the plan ends ([Choosing a plan](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)).
    That is an AWS-enforced zero-out-of-pocket boundary, but it sits at the full $100 credit
    balance, not $95. It works by closing the account, which loses access to resources, and AWS
    deletes them after 90 days.
  - AWS Budgets only send alerts.
- State at the stop: **no AWS resource was created**. Only read-only calls were made (STS,
  describe/list, Price List). The planned single-EC2 deployment and the managed-stack Terraform
  under `infra/staging/` are both unapplied.

## Slice 3: single-EC2 staging demo, deployed (2026-10-22)

**Direction (owner, 2026-10-22; amends the M26 AWS stack for staging only):** one EC2 instance in
the Sydney project running the existing image with Docker Compose, PostgreSQL/pgvector on a
persistent encrypted disk, and Caddy for HTTPS on a DuckDNS hostname. No ECS/Fargate, ALB, RDS,
WAF, Kubernetes or NAT. The managed-stack Terraform (`infra/staging/`) is preserved and unapplied.

**Cost rule (clarified):** zero out-of-pocket payment is the hard requirement. The $95 cap was
withdrawn because the Free plan cannot enforce it. Never upgrade the plan or accept a paid
service. Clean up by **2027-01-15**, or earlier if the credit balance falls below $25.

**Resources** (ap-southeast-2, all tagged `Project=m26a-search-staging`):

- **Instance:** t3.small (Free-plan eligible), Ubuntu 24.04 (Canonical AMI), standard CPU
  credits, IMDSv2 required, termination protection.
- **Disk:** 20 GB gp3, encrypted.
- **Elastic IP:** 15.134.113.75.
- **Security group:** 80/443 public, 22 from the owner's IP only.
- **SSH key pair:** imported; the private key is only on the owner's PC.
- **Not created:** an instance role, ECR repository or any other AWS resource.

**Estimated credit use:** about $25–26/month (Sydney Price List rates: t3.small $0.0264/h,
gp3 $0.096/GB-month, IPv4 $0.005/h). When the instance is stopped, about $5.60/month remains
(disk and IP).

**On the server** (`deploy/ec2/`, copied to `/opt/m26a`):

- `compose.yaml`: `db`, `app`, `caddy`, and a one-off `setup` service in its own profile.
- `bootstrap.sh`: installs Docker and Compose from Ubuntu's archive, adds 2 GB swap, enables
  `ufw`, and writes `.env` (root-only, generated passwords, never printed).
- `setup-db.sh`: runs the reused `infra/staging/setup.sh` with `SETUP_DB_SSLMODE=disable` on the
  private Compose network.
- `backup.sh` (daily cron, 7 kept), `restore.sh`, `update.sh`.
- `README.md`: the runbook.

The image `ecommerce-search:m26a-staging-1` (`sha256:3658d892eff6…`) is the local slice 2 build,
copied with `docker save`/`scp`/`docker load`. It was built from the uncommitted working tree.

**Checks:**

| Check | Result |
|---|---|
| Whole stack locally (Caddy on `localhost`), throwaway database | Setup OK, HTTPS 200, HTTP→308, search 10 results + `X-Request-Id`; backup → delete → restore → search OK; removed afterwards |
| Server setup | 240 products, documents and embeddings, state `CURRENT`, `search_app` SELECT only (52 s) |
| First search on the server (cold) | 13.2 s, `model_load_ms` 11,461 |
| Warm search on the server | 759 ms |
| App container memory on the server | 486 MiB (limit 1.25 GiB) |
| Host memory | 1.07–1.14 GB used of 1.9 GB; swap 3 MB used |
| DNS (1.1.1.1 and 8.8.8.8) | → 15.134.113.75 before Caddy was started |
| Certificate | Let's Encrypt (`CN=YE2`), valid until 2027-01-20; Caddy renews it automatically |
| Public `POST /search/reranked` | 200, 10 results, reranker `applied`, `X-Request-Id` present, 1.7 s |
| `E2E_BASE_URL=<public URL> playwright --project=live` | 1/1 passed |
| From the owner's IP | ports 5432 and 8000 closed; 22 open |
| First server backup (608 KB) | Done; copy exported to `~/m26a-backups/` on the PC |

**Limitations:**

- One instance, so there is no high availability. A stop/start means a short outage, then a cold
  search of about 13 s.
- Performance was measured only for single sequential requests. The instance has not been sized
  for load or for future features.
- Backups are on the same disk as the database, plus manual exports to the PC.
- The URL depends on DuckDNS, a third-party service.
- The old `vishal-ai-search.duckdns.org` subdomain also points at the IP. It is not configured in
  Caddy, so it gets no certificate. The owner may delete it in DuckDNS.

## Slice 4: Search UI similarity notice (2026-10-22)

**Investigation:** the owner reported that the query `HI` showed six Realme phones, then laptops
and shoes. One read-only call to the live API confirmed what happened:

- The parser left `HI` unresolved, so `applied_filters` was empty.
- Every result had `lexical_rank` null, meaning it came only from dense (embedding) retrieval.
- Rerank logits were low and close together (−9.16 to −9.91).
- The page showed the API order.

This is expected top-k behaviour, not a contract violation. It is a relevance limitation: the
system has no calibrated "no relevant result" signal.

**Change** (UI only; API, ranking, retrieval and cards unchanged):

- When the results are non-empty, no result has a `lexical_rank`, and `applied_filters` is empty,
  the page shows: "Showing similarity-based suggestions. Try a product name or requirement for
  more specific results."
- The UI types and shape check now read two existing contract fields, `results[].lexical_rank`
  and `applied_filters`.
- New synthetic fixture `synthetic-reranked-similarity-only.json`, validated against the
  Pydantic contract.
- No score cutoff, no brand or greeting rules, no frontend filtering.

**Checks:**

| Check | Result |
|---|---|
| `npm run typecheck` | PASS |
| `npm test` (includes notice shown, hidden for a keyword hit, hidden for applied filters, absent in empty and error states) | 34 passed |
| `npm run build` | PASS |
| Fixture Playwright | 6/6 |
| `pytest` frontend fixture-contract, serving and compose-config tests | 45 passed |
| Ruff | PASS |

### Slice 4 deployment (2026-10-22)

- **Commit:** `88518678c5e3a508dedd6cf0e27e6502e199544f`.
- **CI:** run `39795620361` passed all four jobs: backend lint + hermetic unit suite, pgvector
  integration, frontend typecheck/unit/build, fixture Playwright.
- **Image:** `ecommerce-search:m26a-8851867`, ID `sha256:2082f377515f88557115b870b9d834b05136660d1308a5d5c097cfafa1e1d79e`,
  label `org.opencontainers.image.revision=88518678…`. Built locally from the committed tree; the
  only uncommitted files (`docs/m12-…`, `.claude/`) are outside the build context.
- **Update:** `update.sh` on the EC2 server. It took a backup first
  (`ecommerce_search-20261022T152613Z.dump`, 608 KB), then recreated only `app`. The database
  (240 products), the hostname and Caddy were unchanged. No new AWS resource.
- **Previous image:** `ecommerce-search:m26a-staging-1` (`sha256:3658d892eff6…`), still on the
  server as the rollback target.
- **Public checks:**
  - HTTPS `/health` 200 with a valid certificate.
  - `live.spec.ts` 1/1 passed.
  - A temporary browser check against the public URL (not committed) passed 2/2:
    - `HI`: 10 results, 0 filters, 0 keyword hits, notice shown.
    - `laptop 8gb 256 ssd`: 10 results, 4 filters, notice hidden.
    - In both cases the cards equalled the API response, and an `X-Request-Id` was present.

## Remaining M26A deployment gaps (each needs owner decisions or approval)

AWS account and region; budget and billing alarm; domain or CloudFront URL; always-on or
scale-to-zero; Terraform state location; IAM permissions; Terraform for ECR, ECS/Fargate, RDS
pgvector, ALB/ACM, WAF, Secrets Manager and CloudWatch (not written yet); a least-privilege DB
user; a one-off migrate/ingest/embed task on RDS; ECR push; online smoke test over HTTPS;
rollback drill. No chargeable action happens before explicit approval.
