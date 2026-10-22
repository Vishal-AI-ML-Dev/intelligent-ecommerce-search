# Master Engineering Plan

## Project: Intelligent Ecommerce Search, Shopping & Return Intelligence Platform

You are the lead Search Engineer, Backend Engineer, Applied ML Engineer, and AI Evaluation Engineer responsible for building this project.

Execution philosophy:

```text
Build small -> test -> measure -> evaluate -> document -> stop
```

This is not a generic RAG chatbot, an unrestricted LLM wrapper, an autonomous agent, or a collection of disconnected demos. It is a production-oriented e-commerce platform built around measurable retrieval quality, explicit constraints, evidence grounding, confidence gating, safe fallback, and human authority over every consequential decision.

The project has two connected workflows built on one search foundation:

1. **Workflow A — Buyer Intelligence.** Product search (the core portfolio project, M0–M10), followed later by a grounded, citation-backed shopping assistant (M16–M17).
2. **Workflow B — Retailer Return Intelligence.** Return-reason analysis, evidence retrieval, root-cause hypotheses, and corrective-action proposals that go through human review (M18–M22).

Seller-side listing intelligence (M13–M15) connects the two: approved listing corrections improve the catalog that buyers search.

The existing search engine (M3–M8) is both:

1. the buyer-facing product retrieval engine; and
2. a reusable retrieval foundation for later evidence-based workflows.

### Plan revision note (2026-10-04)

The project was expanded from "Intelligent E-commerce Search & Decision Engine". The milestones M0–M15 keep their meaning. M0–M8 and their implementation boundaries are unchanged. New milestones M16–M22 and the optional M27 were added. The production milestones were renumbered:

| Former number | New number | Milestone |
|---|---|---|
| 16 | 23 | Regression CI |
| 17 | 24 | Observability |
| 18 | 25 | Frontend (now Dual Frontend) |
| 19 | 26 | AWS Deployment |

Other documents written before this revision still use the former numbers. These are `docs/spec.md`, `docs/architecture.md`, `docs/query-understanding-m6.md` and ADR-001. Read them through this table until those documents are updated.

### Repository status at this revision

| Milestone | Status | Evidence |
|---|---|---|
| M0–M8 | Complete as documented | Milestone commits on `main`. `0a93e52` is the historical M8 boundary commit. Per-milestone docs live in `docs/`. |
| M9 Sessions B, C1 and C2 | Complete | Session B contracts (`15f3ac1`) and Session C1 (`6009fa3`, `queries_frozen` manifest `51204dae…`, confirmed by `ann-1`) are committed. Session C2 froze `pool_frozen` (manifest `4c26628a…`, 568 pairs, private provenance bound by checksum only) and added the blind annotation CLI, primary-seal tooling and the retrieval-metric evaluator (synthetic fixtures only). Development/rubric-calibration data, not test evidence. Details in `docs/golden-dataset-m9.md`. |
| M9 human-reviewed pilot/dev Golden Dataset (strict track) | Complete under `review-2`, with disclosed limitations (scope designation approved 2026-10-17); committed with this revision | Golden version 1, 20 `pilot_dev` queries: 568 final pilot/dev labels frozen (`labels_frozen` `16358129…`); 114 independent second-human (`ann-2`) reviews sealed; 76 mandatory adjudications and 21 joint resolutions completed; 218 optional candidates not re-adjudicated; provenance unsealed (`provenance_unsealed` `413b72ed…`) and the chain verified. Limitations: `ann-1` saw machine proposals and was AI-assisted on the rubric; blindness is procedural; agreement describes the cs-1 sample only. Development/rubric-calibration data, not held-out test evidence. Details in `docs/golden-dataset-m9.md`. |
| M9 review protocol amendment `review-2` | Approved 2026-10-15; carried out on real data | Blind second-human review (`ann-2`) of the cs-1 sample with inter-annotator agreement on that sample only; joint consensus adjudication. Replaces the 72-hour intra-annotator consistency round, which is not measured. Details in `docs/golden-dataset-m9.md`. |
| M9 held-out test dataset | Deferred | Separately authored, sealed test partition with a separately reported Hinglish slice (provisional ≥20%). Size and stratification not approved; nothing authored. No held-out performance claims are permitted. |
| M9 (optional machine-only development track) | Complete | `weak_dev_v1` (`weak_machine_dev_sealed`): 568/568 labels are machine generated (`weak-label-policy-1`). No delayed consistency measurement exists, and all V0–V3 metrics are provisional. Details in `docs/weak-label-development-m9.md`. |
| M9 weak-label audit (`weak-audit-1`) | Deferred at 0/100 | Active human audit coverage is zero. Two abandoned human audit decisions remain archived and excluded. A later 100-entry rapid non-blind acceptance attempt (about 98 seconds, median gap 0.35 s) did not establish substantive review; it is archived privately and excluded. |
| M10 provisional development analysis | Complete | Committed as `73c67c1`. Analysis of existing machine-only metrics; development evidence, not test evidence. Details in `docs/m10-provisional-evaluation.md`. |
| M10 human-reviewed report | Closed, development-scoped (pilot/dev), under the owner-approved M10 scope amendment (2026-10-17); not a claim that all original requirements were met | Approved report `docs/m10-human-pilot-dev-evaluation.md`: V0–V3 on the 20 human-reviewed pilot/dev queries and fresh `/search/reranked` latency on a different 17-query set. Named limitations and deferred items: (1) `rrf_k` = 100 and the embedding model remain provisional, not comparatively re-decided; (2) fresh latency excludes V0, V1 and `/search/filtered`; (3) the latency run has connected-standby contamination and unexplained between-run variation and is not a clean baseline; (4) quality uses recorded actual retrieval runs, not fresh runs; (5) held-out evidence deferred, no held-out or production claims. No reranker approved for development scope only; configuration unchanged. Does not by itself satisfy M16's "M10 complete" prerequisite. |
| M11 Jev decision provider | Complete for provider implementation and verification scope (owner decision, 2026-10-18); live success-path smoke-verified, not production-validated | Commits `4de0a03`, `ca4128e`, `fe5adca`, `b622426`. Off by default; used by no endpoint. Full unit suite 3,739 passed (4 OS-related skips), database integration tests 721 passed, offline real-model tests 14 passed; one live smoke request (HTTP 200, `jev-1.13.0`, validated response). Not verified, and not waived for production: live failure, retry, rate-limit and cancellation behaviour (unobserved); server-side cancellation and billing behaviour (unknown); quality improvement, calibration and latency distributions (unproven). Details in `docs/jev-provider-m11.md`. |
| M12 Jev V4 experiment | Closed as development-scoped, partially met (owner decision, 2026-10-19) | Diagnostic, calibration and gate tooling, category-only annotation and an experimental, ungated, opt-in V4 (`/search/routed`, commit `2fa1d56`) are implemented. Live development runs `live-001` (40) and `cal1-live-001` (80), on AI-authored queries with one human annotator, plus three approved V3/V4 live queries, were performed on 2026-10-19. Calibration found no candidate confidence gate. The V3/V4 comparison was performed and showed no relevance improvement. Not met: the confidence-gated Jev comparison and the planned confidence-gated V4. Partly met: human-reviewed decision data (one annotator, AI-assisted) and latency (no V4 P50/P95/P99). Deferred: independent held-out confirmation, default enablement (FR-DEC-4), model-derived hard filters, and V4 latency under load. Optional, not done: the general-LLM comparison. V3 remains the baseline; V4 stays opt-in and experimental; the confidence gate is off. The milestone requirements below are unchanged. Report: `docs/m12-diagnostic-tooling.md` (M12 experiment report and owner decision) and `docs/v4-jev-routing-m12.md`. |
| M13 Listing normalization | Complete for the approved scope, on synthetic inputs and scratch databases only (M13-4 closure, 2026-10-21), with a verification caveat; committed as `c185fe6` (status corrected 2026-10-22; the original row said "uncommitted at the time of writing") | Raw-listing intake contract, pure deterministic normalizer (candidates with exact source evidence; conflict, contradiction, compatibility, missing, empty and unsupported findings; no winner chosen, nothing invented), append-only versioned storage (migration `0005`) and a command line (`normalize`, `submit`, `show`). Rules version `listing-norm-1` released, digest `3b97986aa7585b4b38c3557167df8e9a43f25866f267dfe77cf8557d208711db`. Final checks (2026-10-21): full unit suite: 4,365 passed, 2 failed, 4 skipped; the 2 failed M10 runner tests passed on a targeted rerun with the working tree static (14 passed, pytest exit 0); the cause of the original failures is not proven; 86 M13 persistence/migration integration tests on scratch databases passed; repository-wide Ruff passed; a 15-check end-to-end CLI smoke test on a scratch database passed. Owner decision D1: command line only; the indicative `POST /listings/normalize` endpoint was not built. Not claimed: accuracy on real seller listings, search improvement, production readiness; the development database was not migrated to `0005`. The milestone requirements below are unchanged. Details: `docs/listing-normalization-m13.md`, `docs/m13-progress.md`. |
| M14 Jev listing decisions | Implementation present; **BLOCKED / INCOMPLETE** (recorded 2026-10-21). Not accepted. | Commits `be2cdf3` through `f01cf93` (M14-1 to M14-6, runner and tooling, append-only decision storage in migration `0006`, `decide` / `show` command line, blinded human-review path `lh-1`). The Jev listing provider is off by default. Unmet mandatory criteria, recorded in the acceptance matrix of `docs/m14-progress.md`: no human-reviewed evaluation (0 human reviews of the frozen `hr-dev-1` selection); calibration not done; the confidence-gated comparison arm is missing; the run-quality criterion **failed**: 7 invalid responses in 286 results = 2.45 %, above the owner-approved 2 % limit, not waived; dev coverage incomplete (286/291, 5 listings never sent); the test split was never evaluated; upstream data rights for the sent sources are unresolved (owner send decisions are not a rights clearance). The development-split results on external source labels are not human-reviewed evidence. Endpoints and the operational review queue were assigned to M21 (owner, 2026-10-21). |
| M25A buyer Search UI | Accepted by the owner (2026-10-22) for the M25A scope in §28; committed with this revision. M25A does not complete Milestone 25: M25B remains. | One React + TypeScript + Vite Search page (ADR-008) over the unchanged `POST /search/reranked`. FastAPI serves it same-origin only when `FRONTEND_DIST_DIR` is set; unset, the app stays API-only. Evidence (2026-10-22, local, on the committed build): typecheck; Vitest 24 passed; production build; **fixture-backed** Playwright 6/6 passed (mocked API, synthetic fixtures validated against the Pydantic contract); **live-stack** Playwright 1/1 passed (seeded query, local reranker `applied`, cards checked against the browser's own response); serving and fixture-contract unit tests passed; Ruff passed. Backend unit suite (run once): 4,851 passed, 4 skipped, 2 failed, recorded as follow-up debt and not fixed in M25A: (1) the catalog metadata-table test, whose cause is confirmed (the four M14 `listing_decision_*` tables are not in its expected set) and which also fails on pre-M25A commit `7567654`; (2) the hermetic child-suite test, observed failing, exact cause unverified (child output not saved). The full suite is not green. Details: `docs/m25a-progress.md`. |
| M23A baseline CI | Complete (2026-10-22) for the M23A scope in §28: all four required GitHub Actions jobs passed on `c9b6e6d` (run `39738936822`). M23B remains. | `.github/workflows/ci.yml`: backend Ruff + hermetic unit suite (4,853 passed), pgvector integration (762 passed, `real_model` excluded), frontend typecheck/Vitest/build, fixture Playwright 6/6. No live provider or model snapshot in CI. Metadata-test debt fixed; the recursive isolation test is skipped visibly in CI because the job is its child. Details: `docs/m23a-progress.md`. |
| M24A observability (search path, Search page) | **In progress** (2026-10-22): implemented and verified with focused local tests; complete only after its acceptance tests pass in CI | Request IDs, one completion log per request, reranked summary, query-free access logs, frontend request IDs. Details: `docs/m24a-progress.md`. |
| M15–M26 (except M25A, M23A, M24A) | Not started | Includes M26A and M25B. |
| M27 | Excluded from the current scope | The original optional milestone definition below is unchanged. |

The status above was checked against git history at this revision. The M13 and M14 rows were
corrected on 2026-10-22 from commit history and `docs/m13-progress.md` / `docs/m14-progress.md`;
the tests were not re-run for that correction. A commit's existence does not by itself make a
milestone complete; the status column records the recorded acceptance state. Push status is an
operational authorization, not a quality gate.

The execution order of the remaining milestones was amended on 2026-10-22; see §28,
"Execution order (amended 2026-10-22)". The milestone IDs, completed work, evidence and
acceptance criteria above and below are unchanged by that amendment.

The M9 strict-track rows were checked on 2026-10-17 against the read-only verification of the golden chain, which is committed with this revision. The test suite was not re-run as part of this documentation change.

## 1. Business problem

### Buyer side

Marketplace listings are inconsistent across sellers. They may contain misspellings, missing attributes, conflicting units, misleading claims, policy risks, and duplicate products. Buyers submit short search queries that mix category, brand, exact specifications, price, and semantic intent.

Examples:

```text
laptop 8gb 256 ssd
hp laptop under 50k
coding ke liye laptop
nike shoes under 5k
anc headphones
iphone 15
```

A pure semantic retriever may return semantically similar products that violate explicit constraints. For `laptop 8gb 256 ssd`, RAM, storage, and storage type must be treated as explicit constraints. For `coding laptop`, coding is primarily semantic intent.

Buyers also need help comparing products and deciding between them. An answer that is not grounded in the catalog, or that ignores an explicit constraint, is worse than no answer.

### Retailer side

Returns are expensive for retailers. They have many causes:

- wrong size or fit;
- products that do not match the listing;
- incorrect specifications;
- quality defects;
- damage in transit;
- wrong items delivered;
- logistics delays;
- change of mind;
- occasional abuse.

Return reasons are often free text, multilingual (including Hinglish), misspelled, and spread across order, return, review, and support systems. Teams struggle to see which SKUs and variants share a pattern. They also struggle to tell whether a fix belongs to the listing, the product, the supplier, the warehouse, or the carrier.

Not every return is caused by a listing problem. Many returns are not preventable by any catalog change. The platform proposes evidence-backed hypotheses for qualified humans to investigate. It does not guarantee lower return rates or higher sales.

### What the platform combines

```text
Catalog ingestion
+ listing normalization
+ query understanding
+ lexical retrieval
+ dense retrieval
+ structured filtering
+ Reciprocal Rank Fusion
+ reranking
+ bounded model decisions
+ confidence gating
+ grounded generation with citations (bounded)
+ return-reason classification and clustering
+ evidence retrieval and root-cause hypotheses
+ human review and audit
+ evaluation
```

### Closed feedback loop

```text
Buyer search
 -> purchase
 -> return / feedback
 -> return-reason analysis
 -> evidence retrieval
 -> root-cause hypothesis
 -> corrective-action proposal
 -> authorized human approval
 -> listing / catalog improvement
 -> search-index refresh
 -> future outcome monitoring
```

Every arrow after "return-reason analysis" can stop early. The pipeline must stop when there is insufficient evidence, conflicting evidence, low confidence, or a human rejection. Outcome monitoring measures change after an approved correction. It does not by itself prove that the correction caused the change.

## 2. Absolute execution rule

Implement exactly one milestone at a time. For every milestone:

1. Inspect the repository.
2. Understand the existing implementation.
3. Present a milestone-scoped plan before editing.
4. Implement only the current milestone.
5. Write or update tests.
6. Run relevant tests, lint, and validation.
7. Run the relevant benchmark or evaluation when it exists.
8. Fix milestone-related failures.
9. Update relevant documentation.
10. Report exact files changed and commands executed.
11. Report actual results and limitations.
12. Stop.

Do not automatically continue. Do not generate future milestone code. Create only the smallest interface required by the current milestone.

## 3. Scope

### Level 1 — Core search engine (M0–M10)

- Product catalog
- Catalog quality validation
- PostgreSQL Full Text Search
- Local embeddings and pgvector
- Hybrid retrieval with Reciprocal Rank Fusion
- Deterministic query understanding
- Structured filtering
- Cross-encoder reranking
- Human-reviewed Golden Dataset
- Search evaluation
- FastAPI
- Tests and measured latency

The core search project is portfolio-complete when this level is complete. Every later level builds on it and must not weaken it.

### Level 2 — Marketplace decision layer (M11–M15)

Only after the core is stable:

- Jev Decision Provider
- Query category and semantic-intent classification
- Retrieval-strategy routing
- Ambiguity and out-of-catalog detection
- Listing normalization
- Listing-category classification
- Attribute-candidate validation
- Policy-risk triage
- Confidence-based human-review routing

### Level 3 — Buyer and Return Intelligence (M16–M22)

Only after the core and its evaluation are stable:

- Grounded buyer assistant over V3/V4 search, with evaluation and safety testing (M16–M17)
- Return data foundation with PII redaction and provenance (M18)
- Return-reason classification and complaint clustering (M19)
- Evidence retrieval and root-cause hypotheses (M20)
- Corrective-action proposals with human-in-the-loop review and audit (M21)
- Closed-loop evaluation (M22)

### Level 4 — Production extensions (M23–M26)

Only after evaluation is stable:

- Regression CI
- Observability
- Dual frontend (buyer and retailer)
- Production Docker hardening
- AWS deployment
- Terraform

**Amended 2026-10-22.** The "only after" lines in Levels 3 and 4 describe the original sequence.
The authoritative order is now §28, "Execution order (amended 2026-10-22)". It schedules the
scoped subdivisions M25A, M23A, M24A and M26A, which cover only the existing core search, before
M16. The remaining Level 4 work (M25B, M23B, M24B, M26B) keeps its original position after the
evaluation milestones. Every milestone's own prerequisites and acceptance criteria still apply.

### Optional — Authorized marketplace integration (M27)

This level is optional and needs authorized official access. The portfolio MVP is complete without it, using CSV/JSON import and export.

## 4. Goals and non-goals

### Goals

1. Return relevant products for short, mixed-intent buyer queries, including Hinglish.
2. Respect explicit, reliably parsed constraints in retrieval, recommendations, and comparisons.
3. Give buyers grounded answers that:
   - cite product IDs;
   - ask for clarification when requirements are incomplete;
   - abstain when the catalog cannot support an answer.
4. Help retailer teams understand written return reasons, cluster related complaints, and find the affected SKUs and variants.
5. Separate the responsible area of each return: listing, product quality, fulfilment, logistics, customer choice, or possible abuse.
6. Propose evidence-backed root-cause hypotheses and corrective actions that authorized humans approve, edit, reject, or escalate.
7. Make every stage measurable: quality, latency at P50/P95/P99, token usage, and cost.
8. Keep the core search engine working fully locally without any remote model provider.
9. Keep raw inputs, normalized data, model decisions, human outcomes, and measured outcomes separately auditable.

### Non-goals

- A generic, open-domain RAG chatbot or an unrestricted LLM wrapper.
- A multi-agent system. LangGraph or similar orchestration is not a default dependency. It may be evaluated later only if a measured need justifies it, such as:
  - bounded multi-step state;
  - pause/resume for human review;
  - recovery requirements.
- Placing orders, taking payments, issuing refunds, or taking any financial action.
- Autonomous publishing of marketplace listing changes. Every marketplace publication requires explicit approval from an authorized human reviewer.
- Automatic seller banning, automatic listing rejection, or legal conclusions.
- Guaranteed sales growth or guaranteed return reduction.
- Personalization and learning-to-rank from user behavior.
- Multi-currency conversion.
- Scraping, or any unauthorized access to marketplaces.
- Elasticsearch, OpenSearch, Qdrant, Pinecone, Kafka, Airflow, Kubernetes, or multiple databases or vector stores for the same workload, unless measurements prove PostgreSQL insufficient.

## 5. Locked technology stack

Use Python 3.12, uv, FastAPI, PostgreSQL, pgvector, PostgreSQL Full Text Search, SQLAlchemy, Alembic, Pydantic, pytest, and Ruff.

Use configurable local sentence-transformers-compatible models for embeddings and cross-encoder reranking. Do not make a paid embedding API a core dependency.

The frontend stack (from M25A) is React, TypeScript and Vite with Playwright browser tests, served same-origin by the existing FastAPI application (owner decision 2026-10-22, ADR-008). The core search engine does not depend on it.

Do not introduce Elasticsearch, OpenSearch, Qdrant, Pinecone, Kafka, Airflow, Kubernetes, or multiple databases for the same search workload unless measurements prove PostgreSQL insufficient.

Generation models (M16, M19–M21) are not part of the locked stack. Each generation provider:

- is selected in its milestone through an ADR from measured quality, latency, and cost;
- sits behind a typed, provider-isolated interface;
- has its output validated through Pydantic.

The core search engine never depends on a generation provider. When a provider is unavailable, generated workflows must degrade to deterministic results, abstention, or manual review. They must never fail open.

## 6. Safety and human authority

These rules apply to every milestone:

1. GenAI output is an evidence-backed **proposal**, never final truth.
2. Correlation is not a confirmed root cause. A frequent pattern among returns is a hypothesis until a human investigation confirms it.
3. Low-confidence cases and cases with conflicting evidence go to manual investigation. They are never resolved automatically.
4. Production approval belongs to an authorized retailer, catalog manager, quality team, operations team, or policy specialist, not the developer.
   - The developer may simulate a reviewer only during local prototyping.
   - Simulated decisions are marked as simulated and never reported as real reviewer outcomes.
5. The platform never publishes marketplace listing changes autonomously. Every marketplace publication requires explicit approval from an authorized human reviewer. Milestone 27 may support an authorized submission mechanism after approval, but it never permits autonomous publishing.
6. AI must not invent product specifications, return evidence, policies, discounts, prices, availability, or customer facts.
7. Each of the following stays separately stored and auditable:
   - raw seller input;
   - raw return evidence;
   - normalized data;
   - model decisions;
   - human outcomes;
   - measured outcomes.
8. Customer PII is redacted before it reaches prompts, logs, annotations, or evaluation artifacts.
9. The platform takes none of the following actions:
   - automatic seller banning;
   - legal conclusions;
   - payment actions;
   - refunds;
   - order placement.
10. Low-confidence model decisions are never turned into hard filters or automatic rejections.
11. External marketplace integrations use authorized official access only.
12. Seller content, customer comments, reviews, and retrieved documents are untrusted data. They cannot override system instructions.

## 7. Architecture

### Overview

```text
                 +---------------------------------------------+
                 | Shared foundation                           |
                 | catalog, taxonomy, attribute schema,        |
                 | normalization, provider interfaces,         |
                 | confidence policies, provenance, evaluation |
                 +---------------------------------------------+
                     |                 |                   |
          Workflow A (buyer)   Listing intelligence   Workflow B (retailer)
          search V0-V4         M13-M15                return intelligence
          grounded assistant                          M18-M22
                     |                 |                   |
                     +--------> human review & audit <-----+
                                       |
                       approved catalog change -> reindex
```

### Buyer search (implemented through V3)

```text
Query
 -> deterministic parser
 -> optional Jev bounded decisions
 -> confidence gate
 -> lexical + dense retrieval
 -> safe structured filters
 -> Reciprocal Rank Fusion
 -> optional cross-encoder reranker
 -> ranked products + telemetry
```

### Buyer assistant (M16)

```text
User message
 -> query understanding (deterministic, optional gated Jev)
 -> need clarification?  -> clarification question (no retrieval-backed claims)
 -> V3/V4 search (retriever)
 -> catalog facts for retrieved product IDs only
 -> bounded generation (typed output, product-ID citations)
 -> deterministic constraint and citation validation
 -> pass: answer with citations | fail: repair once or abstain
 -> telemetry (latency, tokens, cost, validation outcome)
```

### Seller listing (M13–M15)

```text
Raw listing
 -> deterministic normalization
 -> attribute candidates
 -> optional Jev bounded decisions
 -> confidence/risk gate
 -> auto-accept | warning | manual review
 -> normalized catalog record
```

### Return intelligence (M18–M21)

```text
Orders, returns, comments, reviews, support complaints, delivery signals
 -> validation, provenance, PII redaction
 -> deterministic reason-code mapping (baseline)
 -> typed multi-label classification (optional GenAI, confidence-gated)
 -> complaint clustering by SKU / variant / pattern
 -> authorized evidence retrieval (listing versions, returns, reviews,
    complaints, similar SKUs, guidelines, seller docs, policies)
 -> typed root-cause hypothesis with confidence and evidence references
 -> sufficient and consistent evidence?  no -> manual investigation
 -> corrective-action proposal (original vs proposed diff)
 -> HITL review queue
```

### Human-in-the-loop review (M21)

```text
Proposal (evidence, hypothesis, confidence, diff, versions)
 -> authorized reviewer
 -> approve | edit | reject | request more evidence | escalate
 -> audit record (who, when, decision, reason, versions)
 -> approved: export / apply to catalog (no autonomous marketplace publishing)
 -> search-index refresh
 -> outcome monitoring window (M22)
 -> rollback possible from history
```

### Shared components

- Taxonomy and attribute schema
- Brand dictionary
- Normalization rules
- Provider interfaces (embedding, reranker, decision, generation)
- Confidence policies
- PII redaction
- Provenance records
- Audit trail
- Evaluation infrastructure

Retrieval for the buyer assistant and for root-cause evidence reuses the same PostgreSQL lexical and pgvector infrastructure. It does not add a separate vector store.

## 8. Product catalog

Core fields:

```text
product_id, seller_id, title, description, category, subcategory,
brand, price, currency, rating, review_count, availability,
source_type, created_at, updated_at
```

Laptop fields: `ram_gb`, `storage_gb`, `storage_type`, `processor`, `gpu`, `screen_size_inches`, `operating_system`, `weight_kg`.

Phone fields: `ram_gb`, `storage_gb`, `camera`, `battery_mah`, `screen_size_inches`, `operating_system`.

Shoes fields: `size`, `color`, `material`, `gender`.

Headphone fields: `wireless`, `anc`, `battery_life_hours`, `connectivity`.

Keep the schema extensible. Do not populate irrelevant category attributes. JSONB may hold validated long-tail attributes, but frequently filtered fields should have proper columns.

## 9. Data model overview

Each layer is stored separately. One layer never silently overwrites another.

| Layer | Examples | Introduced |
|---|---|---|
| Raw | raw seller listing, raw return record, raw customer comment, raw review | M2 (catalog), M13 (listings), M18 (returns) |
| Normalized | normalized catalog record, normalized units, mapped reason codes | M2, M13, M18 |
| Inferred | query understanding, model decisions, return classifications, clusters, root-cause hypotheses | M6, M11+, M19, M20 |
| Human | relevance labels, review decisions, approvals, edits, rejections | M9, M14+, M21 |
| Outcome | measured or simulated post-correction return rates | M22 |

Return-intelligence entities (M18) are typed, versioned contracts:

- products and SKU/variant linkage;
- listing versions;
- orders and order items (anonymized);
- shipments and delivery/fulfilment signals;
- returns;
- return reason codes;
- customer-written return comments;
- reviews and support complaints;
- correction cases and proposed corrections;
- human decisions;
- outcomes.

Every record carries these provenance fields:

```text
source_type, source_name, license, source_record_id, dataset_version,
ingestion_timestamp, transformation_version, is_synthetic
```

## 10. Dataset and provenance strategy

### Catalog quality (existing)

Use a reproducible public dataset, a clearly labeled synthetic/seed catalog, or an allowed combination. Never describe synthetic data as real marketplace data. Record provenance. The M2 decision is the project-authored synthetic seed catalog (`docs/data-quality.md` §2.5).

Before evaluation, validate:

1. Product IDs are unique.
2. Required fields exist.
3. Prices and numeric attributes are plausible.
4. Category and attribute combinations are coherent.
5. Titles do not obviously contradict structured attributes.
6. Units and storage types are recognized.
7. Brand/category combinations are plausible.
8. Duplicates do not dominate.
9. Synthetic records are identified.
10. Embedding text contains no evaluation labels.

Prepare at least 30 representative products for real human review. Do not mark the review complete automatically.

### Multi-dataset strategy

1. **Amazon Shopping Queries (ESCI).** Use the official dataset for product-search relevance experiments, where its license and fields permit.
   - ESCI is an optional external benchmark track. It is not part of M9, and the core M10 report does not need it. It runs only after the core internal M10 report is complete. No mandatory milestone is added for it.
   - Record the exact source, version, download date, license, locale, split, and transformation history.
   - If the license or terms cannot be confirmed, ESCI is not used.
   - ESCI is separately versioned and loaded through its own corpus adapter. It has separate provenance, metrics output, and report.
   - It is never mixed into the internal seed catalog or the M9 Golden Dataset. The catalog schema requires price and category, and ESCI does not reliably provide them; such values are never invented.
2. **Never describe ESCI as return, order, review, price, inventory, or live marketplace data.**
3. **Preserve ESCI labels** as Exact, Substitute, Complement, and Irrelevant.
   - Any conversion into internal graded relevance must be documented, versioned, and human-reviewed.
   - For primary-product retrieval:
     - Exact and Substitute contribute through a documented, versioned mapping.
     - Complement is not relevant by default.
     - Irrelevant remains not relevant.
   - Complement does not affect the internal M9 Golden Dataset.
   - Complement may be used only in a later, separately versioned cross-sell/accessory task with its own policy, dataset, metrics, and report. That task is not planned as a milestone and is not implemented.
4. **Use the approved structured/seed catalog** for fields that ESCI does not reliably provide: price, RAM, storage, size, category, availability, and similar attributes.
5. **Mark every synthetic product and event.** Never present synthetic records or simulated outcomes as real Amazon or Flipkart data.
6. **Hinglish.** ESCI provides no Hinglish coverage, so Hinglish relevance is evaluated on the internal catalog.
   - The approved M9 20-query development pilot contains only 3 Hinglish queries. Reports must disclose this limited coverage. The pilot is not expanded or redesigned for this.
   - The later, separately authored and sealed held-out test partition must contain a separately reported Hinglish slice. The provisional target is at least 20% of the held-out test set.
   - The final test-set size and stratification are approved when that test partition is planned.
7. **Return Intelligence data.** Build a separate, internally consistent linked dataset. It contains:
   - products and listing versions;
   - anonymized orders and order items;
   - shipments;
   - returns and return reasons;
   - feedback;
   - proposed corrections and human decisions;
   - measured or simulated outcomes, each marked as such.
8. **Public reviews.** Legally permitted public review or complaint data is used only as review evidence. A review is never treated as a confirmed return event.
9. **Return-reason and root-cause evaluation.** Build a separate human-reviewed evaluation set. Generated labels are never authoritative ground truth.
10. **Separate versioning.** Search, buyer-RAG, listing, policy-risk, return-reason, root-cause, and HITL/outcome evaluation datasets are versioned separately.
11. **No coincidental joins.** Never join unrelated public datasets on IDs that happen to match. A cross-dataset join requires a verified common identifier; otherwise the datasets stay separate.
12. **Provenance fields.** Store the provenance fields from §9 on every record and dataset version.
13. **PII.** Redact PII before prompts, logs, annotations, and stored evaluation artifacts.
14. **Real retailer data** is optional. It may be used only with:
    - authorization;
    - anonymization;
    - access controls;
    - documented retention rules.

### Evaluation dataset registry

| Dataset | Unit | Milestone | Ground truth |
|---|---|---|---|
| Internal search Golden Dataset (dev pilot: 3 Hinglish queries; held-out test: Hinglish slice, provisional ≥20%) | query → product → relevance grade | M9 | human (`ann-1`) |
| ESCI benchmark (optional external track) | query → product → ESCI label | after the core M10 report; no mandatory milestone; subject to license check | ESCI labels, preserved as published |
| Query decision set | query → decision | M12 | human |
| Listing decision set | listing → decision | M14 | human |
| Policy-risk set | listing → risk triage | M15 | human, versioned criteria |
| Buyer-RAG answer set | conversation → answer judgement | M17 | human |
| Return-reason set | return text → labels | M19 | human |
| Root-cause set | cluster + evidence → hypothesis judgement | M20 | human |
| HITL / outcome set | proposal → decision → outcome | M21–M22 | human decisions; outcomes measured or simulated (marked) |

## 11. Query understanding

Create a typed `QueryUnderstanding` model with optional fields:

```json
{
  "raw_query": "hp laptop 8gb 256 ssd under 40k",
  "category": "laptop",
  "brand": "HP",
  "ram_gb": 8,
  "storage_gb": 256,
  "storage_type": "SSD",
  "min_price": null,
  "max_price": 40000,
  "semantic_intent": null,
  "retrieval_strategy": null,
  "attributes": {}
}
```

Do not invent missing constraints. Treat explicit category, brand, model, RAM, storage, storage type, and price as potential hard constraints. Treat gaming, coding, student, lightweight, premium, comfort, travel, and office use as semantic intent unless reliable structured data supports filtering.

## 12. Deterministic parser

Implement deterministic parsing first. Support values such as `8gb`, `8 gb`, `16gb`, `256gb`, `512gb`, `1tb`, `40k`, `₹40000`, and `rs 40000`.

Normalize `40k` to `40000` and `1tb` to `1024gb`. Support RAM, memory, storage, SSD, NVMe, HDD, and common Hinglish phrases including `ke andar`, `ke under`, `wala`, `ka`, `sasta`, `coding ke liye`, and `gaming ke liye`.

Keep the parser small, typed, and testable. Do not create hundreds of fragile regular expressions.

## 13. Decision providers and Jev

Create a small provider interface conceptually shaped as:

```text
understand(query, deterministic_result) -> DecisionResult
```

Implementations may include:

- `DeterministicDecisionProvider`
- `JevDecisionProvider`
- Optional `LLMDecisionProvider`

The core must work without any remote provider. Isolate provider-specific API code and validate outputs through Pydantic.

Jev access is available, but integration occurs only after core evaluation is stable. Use Jev as a probability-backed bounded-decision layer for:

- category;
- semantic intent;
- retrieval strategy;
- ambiguity;
- out-of-catalog detection;
- listing-category validation;
- attribute-candidate validation;
- policy-risk triage;
- human-review routing.

Do not use Jev to:

- invent attributes;
- replace reliable numeric parsing;
- make final legal conclusions;
- automatically ban sellers;
- create labels;
- replace the reranker without a controlled experiment.

Keep endpoint, model, timeout, retry, enablement, and thresholds configurable. Store credentials only in environment variables. Log provider, pinned model version, latency, decision probabilities, fallback reason, and returned cost where available. Never log secrets.

## 14. Confidence gating

Initial thresholds are experimental. Final thresholds must be selected from human-reviewed data, calibration, coverage, abstention, and mistake cost.

High-confidence query decisions may be accepted. Medium-confidence decisions should avoid risky hard filters and use broader retrieval. Low-confidence decisions should fall back safely or request clarification.

For listings, low-risk high-confidence cases may be auto-accepted; uncertain cases must be warned or routed to human review. Confidence is not correctness.

For return intelligence, confidence never auto-approves a correction. It only decides which queue a case goes to and in what order. Low confidence or conflicting evidence always means manual investigation.

## 15. Listing intelligence

Preserve raw seller input. Store normalized facts, inferred decisions, risk signals, and human outcomes separately.

Use deterministic normalization for casing, whitespace, known brand aliases, units, storage terms, and numeric formats. Do not automatically rewrite uncertain model names.

Extract exact attributes deterministically where possible. Use Jev only for bounded validation questions, such as:

- whether an extracted value refers to RAM or storage;
- whether an attribute is category-compatible;
- whether the listing is ambiguous;
- whether review is required.

Optional risk signals include:

- prohibited item;
- counterfeit indicator;
- misleading claim;
- suspicious review;
- policy mismatch;
- adult content;
- unsafe product;
- unsupported medical claim.

These are triage signals, not final legal conclusions.

Automated review outcomes may be `auto_accept`, `accept_with_warning`, or `manual_review`. Final rejection requires human review.

Listing versions (M13 onward) are retained. Return intelligence (M20–M21) can then compare a return pattern against the listing that the buyer actually saw.

## 16. Retrieval and ranking

### Lexical

Use PostgreSQL Full Text Search for brands, model names, exact terms, and specifications. Create appropriate indexes and measure latency. This is V0.

### Dense

Use local embeddings and pgvector. Make the model configurable and document its name, revision, dimensions, normalization, catalog version, and generation timestamp. Regenerate embeddings and rerun evaluation after configuration changes.

### Hybrid

Combine lexical and dense retrieval using Reciprocal Rank Fusion. Initial configurable values: `lexical_k=50`, `dense_k=50`, `candidate_k=50`. Preserve source ranks and fused score. This contributes to V1.

### Filtering

Safely apply explicit category, brand, RAM, storage, storage type, and price constraints. Push filtering into SQL when safe and beneficial. Never use low-confidence model decisions as hard filters. This is V2.

### Reranking

Use a configurable lightweight cross-encoder. Support reranker on and off. Start with 50 fused candidates, rerank 20, and return 10. Benchmark no reranker, rerank 10, and rerank 20. This is V3.

## 17. RAG boundaries

Retrieval-augmented generation is allowed only in M16 (buyer assistant) and M20 (root-cause evidence). Both are bounded as follows.

1. **Retriever.**
   - Buyer answers use the existing V3/V4 search.
   - Root-cause analysis retrieves only authorized evidence sources (§19).
   - No retriever reaches the open web.
2. **Grounding.**
   - Every factual claim about a product cites a retrieved product ID.
   - Every claim about a return pattern cites evidence record IDs.
   - Uncited claims fail validation.
3. **Deterministic validation.**
   - Explicit constraints are re-checked against catalog fields after generation.
   - Citations must refer to retrieved items.
   - Prices, specifications, and availability come from the catalog, never from model text.
4. **Typed output.** Generation returns a Pydantic-validated structure, not free-form prose alone.
5. **Clarification and abstention.**
   - The system asks a clarification question when requirements are incomplete or contradictory.
   - It abstains when retrieved evidence cannot support an answer.
6. **Bounded workflow.**
   - Each step runs a fixed, small number of times, with at most one repair attempt.
   - There is no autonomous tool loop.
   - The system takes no actions beyond answering or proposing.
7. **Untrusted content.** Listing text, reviews, and comments are passed as data and cannot change instructions. Prompt-injection and malicious-content tests are mandatory (M17).
8. **Versioning.** The prompt, model, retriever configuration, and policy versions are recorded with every generated output.

## 18. Buyer workflow

1. The buyer searches. V0–V3 are available today. M12 added only an experimental, opt-in,
   ungated V4 (`/search/routed`); the planned confidence-gated V4 was not realized.
2. The buyer may ask the assistant (M16) to:
   - find products for a need;
   - compare specific products;
   - recommend among catalog products.
3. The assistant can do one of three things:
   - answer with product-ID citations;
   - ask a clarification question;
   - abstain.
4. Recommendations never violate explicit constraints. Any trade-off (for example "nothing under ₹40,000 has 16 GB RAM") is stated explicitly.
5. The assistant never places orders, applies discounts, or takes payments. It never invents prices, offers, or availability.
6. Buyer feedback can be captured for evaluation (originally "M25 UI"; under the 2026-10-22 execution-order amendment M25B no longer creates first-time domain UI. Buyer feedback is deferred: no owning milestone, endpoint or dataset is defined; §28).

## 19. Return-intelligence workflow

1. **Ingest (M18).** Ingest orders, order items, shipments, returns, reason codes, comments, reviews, and support complaints from CSV/JSON. Validate the data, attach provenance, and redact PII.
2. **Understand (M19).**
   - Map reason codes deterministically (the baseline).
   - Run optional typed GenAI multi-label classification with confidence. It must handle Hinglish and misspellings.
   - Predict the responsible area.
   - Route low-confidence cases to review.
3. **Cluster (M19).** Group related complaints by SKU, variant, and pattern.
4. **Retrieve evidence (M20).** Use only authorized sources:
   - the current listing and historical listing versions;
   - previous returns, reviews, and support complaints;
   - similar SKU cases;
   - category guidelines;
   - seller documents;
   - marketplace policies.
5. **Hypothesize (M20).** Produce a typed output containing:
   - the affected SKU/variant;
   - the observed pattern;
   - supporting evidence references;
   - the likely root-cause hypothesis;
   - confidence;
   - the responsible team;
   - the recommended next investigation.

   Abstain or send the case to manual investigation when evidence is insufficient or conflicting.
6. **Propose (M21).** Draft a corrective action with an original-versus-proposed diff where applicable.
7. **Review (M21).** An authorized human decides.
8. **Apply and measure (M21–M22).** Approved catalog changes are exported or applied, the search index is refreshed, and outcomes are monitored.

Return categories (where supported by data):

| Category | Typical responsible area |
|---|---|
| sizing/fit | listing or product |
| expectation mismatch | listing |
| colour/image mismatch | listing (photography) |
| incorrect specification | listing / catalog |
| product-quality defect | quality / supplier |
| damaged product | packaging / logistics |
| wrong item delivered | fulfilment / warehouse |
| logistics delay | logistics |
| manufacturing defect | quality / supplier |
| change of mind | customer choice |
| suspected abuse | operations (investigation only, never automatic action) |
| insufficient evidence | manual investigation |

## 20. Human-in-the-loop workflow

- **Reviewers.** Authorized roles are retailer, catalog manager, quality team, operations team, and policy specialist. During local prototyping only, the developer may act as a simulated reviewer, and the outcome is marked `simulated`.
- **Actions.** Approve, edit, reject, request more evidence, or escalate. Every action requires a reason.
- **What reviewers see:**
  - the evidence;
  - the hypothesis and confidence;
  - the original-versus-proposed diff;
  - the model, prompt, policy, and evidence versions.
- **Audit.** An append-only decision history with reviewer handle, timestamp, decision, reason, and versions. Rollback history is kept.
- **Publishing.** Nothing is published to a marketplace autonomously. Approved changes become an export file or an internal catalog update. Marketplace submission exists only through M27, and only for changes an authorized reviewer has explicitly approved.
- **Search refresh.** An approved internal catalog change triggers the existing reindex and embedding steps. The change is then visible in search.

## 21. Latency, cost, and evaluation

Do not promise an arbitrary universal latency. Measure `query_understanding_ms`, `jev_decision_ms`, `lexical_ms`, `vector_ms`, `filtering_ms`, `rrf_ms`, `reranking_ms`, `serialization_ms`, and `total_ms`. Report P50, P95, and P99. Generated workflows additionally measure:

- `retrieval_ms`, `generation_ms`, and `validation_ms`;
- input and output tokens;
- cost per request and per 1,000 requests, from actual provider-returned or published pricing recorded with its date.

Create `evals/` separate from application code. Implement Precision@K, Recall@K, NDCG@K, MRR, Constraint Satisfaction Rate, irrelevant-result rate, and zero-result rate.

Search versions:

```text
V0 = lexical
V1 = lexical + dense + RRF
V2 = V1 + structured filtering
V3 = V2 + reranking
V4 = V3 + confidence-gated Jev decisions
```

The internal Golden Dataset (M9) covers:

- unconstrained queries;
- brand/category queries;
- specification queries;
- price queries;
- semantic-intent queries;
- Hinglish queries;
- noisy queries.

Its structure:

- **Dev pilot.** A 20-query development/rubric-calibration pilot. It contains only 3 Hinglish queries, and reports disclose this limited coverage.
- **Test partition.** A separately authored, sealed test partition comes later. It contains a separately reported Hinglish slice (provisional target: at least 20% of the test set). Its size and stratification are approved when it is planned. Pilot results are never presented as final test evidence.

Generated labels are never authoritative.

For decisions, compare deterministic, optional general LLM, Jev, and deterministic plus confidence-gated Jev. Measure:

- accuracy, precision, recall, and F1;
- confusion matrix;
- schema validity;
- abstention and coverage;
- calibration, with Brier score where appropriate;
- latency;
- cost per 1,000 decisions.

For review routing, measure automation rate, review rate, unsafe auto-accept rate, and unnecessary review rate.

Buyer-RAG evaluation (M17):

- faithfulness;
- answer relevance;
- citation correctness;
- unsupported-claim rate;
- constraint satisfaction;
- clarification accuracy;
- abstention accuracy;
- false-refusal rate;
- prompt-injection and malicious-listing robustness;
- latency, tokens, and cost.

Return intelligence (M19–M20):

- per-class precision, recall, and F1;
- macro and micro averages;
- responsible-area accuracy;
- cluster quality judged by humans;
- root-cause hypothesis quality judged by humans;
- evidence-citation correctness;
- abstention on insufficient evidence;
- low-confidence routing rate.

Closed loop (M22):

- overall, preventable, SKU-level, and variant-level return rates;
- before/after outcomes per approved correction;
- recommendation acceptance, edits, and rejections;
- false recommendation rate;
- time to resolution;
- search-quality impact (re-run search evaluation after catalog changes);
- estimated cost savings, with every assumption stated.

Simulated or estimated impact is always labeled as such and never presented as real production impact.

## 22. Annotation and experiment tracking

Build a small CLI or local interface showing query, parsed constraints, optional Jev decisions, candidate products, attributes, and retrieval sources. Allow labels `relevant`, `borderline`, and `not relevant`.

For listing review, show raw and normalized listing data, extracted attributes, risk signals, confidence, and proposed route. Save structured human labels.

Later annotation tasks follow the same pattern. Each dataset has its own versioned schema and rubric:

- buyer-RAG answer judgements (M17);
- return-reason labels (M19);
- root-cause judgements (M20).

Annotators see redacted text only. Annotators are identified by pseudonymous handles. If only one annotator exists, reports say so and do not claim inter-annotator agreement. AI is never an annotator, and AI suggestions become labels only after explicit human review.

Every experiment records:

- ID and timestamp;
- Git commit;
- catalog, dataset, and golden versions;
- provider and model;
- policy and prompt version;
- embedding model and reranker;
- candidate counts and thresholds;
- quality, latency, and cost;
- notes and environment.

Generate reports from actual outputs.

## 23. API boundaries

Implemented search endpoints (M1–M8):

```text
GET  /health
GET  /search?q=...            POST /search            (V0 lexical)
GET  /search/dense?q=...      POST /search/dense      (dense)
GET  /search/hybrid?q=...     POST /search/hybrid     (V1)
GET  /search/filtered?q=...   POST /search/filtered   (V2)
GET  /search/reranked?q=...   POST /search/reranked   (V3)
```

Use typed requests/responses and OpenAPI. Search metadata should include search version, provider, reranker state, latency, result count, and safe applied filters.

Deferred endpoints are created only in their milestone. The paths below are indicative, not final:

```text
POST /listings/analyze                      (M13-M14)
POST /listings/normalize                    (M13)
POST /assistant/answer                      (M16)
POST /returns/import                        (M18)
GET  /returns/clusters                      (M19)
GET  /returns/cases/{case_id}/evidence      (M20)
GET  /reviews/pending                       (M21; originally M14/M21)
POST /reviews/{review_id}/decision          (M21; originally M14/M21)
GET  /corrections/{correction_id}/history   (M21)
GET  /reports/outcomes                      (M22)
```

Amended 2026-10-22: the operational human-review queue and its endpoints belong to M21 (owner
assignment of 2026-10-21, recorded in `docs/m14-progress.md`). M14 does not build that queue.
Listing API adapters are created in M14–M15 only to expose existing listing domain logic; see
§28, "Listings API and the M21 review queue".

No endpoint places orders, issues refunds, moves money, or bans sellers, and none publishes to a marketplace without explicit authorized human approval.

## 24. Intended repository structure

Create directories only when their milestone begins. The layout below was recorded through M11
and has not been redrawn since. Corrected 2026-10-22: M12 is closed (development-scoped), M13 is
complete for its approved scope, and M14's implementation is present but BLOCKED (§"Repository
status at this revision"). They added `src/ecommerce_search/normalization/` (M13), listing modules
in `src/ecommerce_search/decision/` (M14), and `evals/query_decisions/` (M12) and
`evals/listing_decisions/` (M14). M15 and later have not started. The recorded layout is:

```text
intelligent-ecommerce-search/
  docs/
    MASTER_PLAN.md, spec.md, architecture.md, data-quality.md,
    per-milestone docs (search M3-M8, golden-dataset-m9.md, weak-label-development-m9.md,
    rubric-1 reviewer copies, m10-*-evaluation.md, jev-provider-m11.md),
    decisions/ (ADR-001..ADR-007)
  src/ecommerce_search/
    api/ catalog/ config/ db/ decision/ embeddings/ filtering/
    ingestion/ models/ query_understanding/ reranking/ search/
    (decision/: the deterministic provider and the optional, off-by-default Jev provider, M11)
  migrations/
  tests/
    unit/ (catalog/, golden/, search/, weak_labels/) integration/
  evals/
    contracts/ annotation/ metrics/ runners/   (M9-M10 tooling)
    goldens/          (M9; internal-search/v1: the sealed review-2 pilot/dev golden chain and
                      its manifests; no held-out test partition)
    weak_labels/ weaksets/                     (M9 machine-only development track)
    results/          (M10; internal-search/v1/human-pilot-dev)
  data/
    seed/ labels/ processed/ (processed/ contents are git-ignored)
  scripts/
  pyproject.toml, uv.lock, Dockerfile, docker-compose.yml, .env.example, README.md
```

Local model snapshots live in the git-ignored `models/` directory.

Future packages, each created only in its milestone:

```text
src/ecommerce_search/
  normalization/       (M13)
  assistant/           (M16)
  generation/          (M16; provider-isolated)
  returns/             (M18-M19)
  evidence/            (M20)
  review/              (M21 operational review queue; originally M14/M21)
  observability/       (M24)
evals/
  reports/          (planned for M9-M10, never created; M10 reports are in docs/ and evals/results/)
  decisions/                                        (M12, M14)
  assistant/                                        (M17)
  returns/                                          (M19-M22)
```

The M9 contract tests live in `tests/unit/golden/`, not `tests/unit/evals/`. A `tests/unit/evals` directory would shadow the real `evals` package as a namespace package.

## 25. Testing

Test:

- parsing, catalog validation, retrieval, RRF, filtering, and reranking;
- APIs;
- provider failure, timeouts, malformed responses, low confidence, and fallback;
- empty input, nonsense, and contradictory constraints;
- unsupported categories;
- prompt-like malicious seller text.

Later milestones add:

- generation-provider contract tests with recorded or fake providers (routine tests never call live providers);
- citation and constraint validators;
- clarification and abstention paths;
- prompt-injection fixtures in listings, reviews, and return comments;
- PII-redaction tests;
- return-data contract validation;
- audit-trail immutability;
- the rule that no code path refunds, orders, or bans, and none publishes to a marketplace without explicit authorized human approval.

## 26. Security and privacy

Treat seller content, customer comments, reviews, and retrieved documents as untrusted data. They cannot override system instructions.

Use environment variables for secrets, timeouts, limited retries, safe fallbacks, typed validation, and no secret logging. Never hard-code credentials or commit `.env` files.

Redact customer PII before prompts, logs, annotations, and evaluation artifacts. Apply access controls and documented retention to any real retailer data. Use only authorized official access for external marketplace integrations.

## 27. Observability

Later observability (M24) records:

- request ID;
- search version;
- provider/model;
- stage timing;
- result count and filters;
- fallback reason;
- review routing;
- errors;
- aggregate latency, error, fallback, and confidence metrics.

Generated workflows also record:

- prompt, model, and policy versions;
- token usage and cost;
- validation failures;
- abstention and clarification rates;
- review-queue depth;
- time to decision.

Logs never contain secrets or unredacted PII.

## 28. Milestones

### Execution order (amended 2026-10-22)

This amendment changes the order in which the remaining milestones run. It does not change
history. Milestone IDs M0–M27, completed work, recorded evidence and every acceptance criterion
below stay as written. No historical acceptance gate is waived. Running a milestone earlier
does not lower its acceptance standard and does not imply production readiness. This subsection
is the only authoritative execution order. There is no separate phase plan.

Authoritative order of the remaining work:

```text
M25A -> M23A -> M24A -> M26A -> M16 -> M17 -> M18 -> M19 -> M20 ->
M14 -> M15 -> M21 -> M22 -> M25B -> M23B -> M24B -> M26B
```

- **A/B subdivisions.** M23A/M23B, M24A/M24B, M25A/M25B and M26A/M26B are scoped subdivisions
  of the existing M23–M26. They do not replace or renumber them. A B part covers only what its A
  part did not deliver; it does not redo A's work. Each original milestone is complete only when
  its A and B parts together meet its original requirements plus the approved amendments.
- **M14–M15 may resume earlier.** They may resume before their listed position, at a safe
  milestone boundary (never in the middle of another milestone), once their recorded blockers
  are resolved by the owner. The M14 blockers are in §"Repository status at this revision" and
  `docs/m14-progress.md`. They are not waived.
- **Order is not independence.** The dependencies below apply in addition to the order. When a
  dependency blocks a step, record the block in that milestone's progress document. Do not
  bypass it and do not implement around it.
- **A blocker stops only its dependents.** An external blocker (owner approval, a chargeable
  action, human reviewers, data rights) stops the steps that depend on it, not independent
  steps. The next unblocked step in the order proceeds, at a milestone boundary, and the blocked
  step runs when its blocker clears. In particular, M26A waiting for approval of chargeable
  deployment does not block M16–M20 or other local work, and the M14 blockers do not block
  M16–M20.

| Step | Depends on | Known blocking or open items |
|---|---|---|
| M25A | M8 `POST /search/reranked` (exists); frontend stack (decided, ADR-008) | None recorded |
| M23A | M25A and its Playwright smoke path; existing test suites | CI never requires live Jev or any live generation provider |
| M24A | Existing search path; M25A for UI-to-API request correlation | Logs never contain unredacted PII |
| M26A | M23A and M24A; local stability (Milestone 26) | Owner approval before any chargeable action; cloud account and billing are owner decisions. Blocks only M26B. |
| M16 | M10 prerequisite (the development-scoped M10 closure does not by itself satisfy it; the owner decides); generation-provider ADR | No generation provider selected |
| M17 | M16; a separate human-reviewed answer set | Real human reviewers |
| M18 | Return data source with recorded provenance and rights; PII redaction | Data source not chosen |
| M19 | M18; a separate human-reviewed return-reason set; generation-provider ADR | Real human reviewers |
| M20 | M18, M19; M13 listing versions (exist) | Separate human-reviewed root-cause set |
| M14 (resume) | Recorded M14 blockers resolved by the owner | Human reviews (0 recorded), calibration, gated comparison arm, run-quality failure, 5 unsent dev listings, test split, upstream rights |
| M15 | M13; M14 routing and decisions; human-reviewed examples | M14 blocked |
| M21 | M14 decisions and routing; M20 evidence; authorized-reviewer check | M14 blocked |
| M22 | M19, M20, M21 | Measured outcomes need real decisions; simulated figures must be labelled |
| M25B | The domain UIs built in M14–M22 | — |
| M23B, M24B | M25B and all domain milestones | — |
| M26B | M23B, M24B, M25B; owner approval before chargeable actions | — |

The A subdivisions are provisionally scoped below. Each scope is confirmed in a milestone-scoped
plan when that step is planned:

- **M25A:** the buyer Search frontend over the existing Search backend (bounded below).
- **M23A:** Milestone 23's requirements applied to the existing core search and the M25A page.
  It automates in CI the tests M25A already passes; it does not supply M25A's acceptance tests.
- **M24A:** Milestone 24's requirements applied to the existing search path and the M25A page.
  It strengthens M25A's logging; it does not supply the logging M25A needs for acceptance.
- **M26A:** Milestone 26's requirements applied to the existing core search and the M25A page.
- **B parts:** the requirements of M23–M26 that the A parts did not deliver, across all domains.

#### Frontend ownership

| Milestone | Owns |
|---|---|
| M25A | The buyer Search frontend, using the existing Search backend |
| M14–M15 | The Listings UI, plus the minimum API adapters needed to expose existing listing domain logic |
| M16–M17 | The shopping-assistant backend and its UI |
| M18–M22 | The returns capabilities and their UI |
| M21 | The operational human-review queue and its UI |
| M25B | Unifying and hardening the existing UIs. It does not create first-time domain UI |

Milestone 25's buyer and retailer UI lists remain the overall UI inventory. Each screen is built
by the milestone that owns its backend, as listed above.

#### Listings API and the M21 review queue

- **CLI-only decision amended prospectively.** Owner decision D1 (M13, command line only) is
  amended prospectively. The command lines (`python -m ecommerce_search.normalization`,
  `python -m ecommerce_search.decision.listing_cli`) remain supported.
- **Adapters reuse domain logic.** API adapters created in M14–M15 call the same domain and
  application services as the command lines. They do not duplicate business logic.
- **No early queue.** M14 does not build, and does not implement early, M21's operational
  review queue or the `/reviews/*` endpoints. M14's blinded human-review path (`lh-1`) is
  evaluation tooling, not the operational queue.
- **Decisions are not sanctions.** No adapter rejects listings, bans sellers or publishes to a
  marketplace (§6).

#### Access control

Any exposed workflow that touches sensitive data or consequential decisions gets minimum access
controls in the same milestone that exposes it. This covers seller listings, return records, PII,
review decisions and corrections. Essential security is not deferred to M25B, M23B, M24B or M26B.

#### M25A scope

In scope:

- one buyer search page;
- the existing `POST /search/reranked` with `{query, top_k}`;
- product cards built from the existing response contract (`RerankedSearchResponse`);
- loading, empty and error states, plus the reranker status and fallback state the response
  already reports;
- existing captured responses (`docs/demo/*.json`) as fixtures where suitable;
- minimal same-origin browser serving and integration.

Out of scope:

- new search, retrieval or ranking implementation;
- the experimental `/search/routed` (V4) enabled by default;
- a debug-mode or search-version selector, comparison, the assistant, and unsupported controls;
- explicit filter controls, until their request contract is approved (the search request models
  accept only `query` and `top_k`; filters come from the M6 parse);
- product-detail or purchase flows, which no backend supports.

**Stack (decided 2026-10-22, ADR-008).** React, TypeScript and Vite in one frontend codebase with
domain-specific modules, served same-origin by the existing FastAPI application, with Playwright
browser smoke tests. Nothing is installed yet.

**Reuse boundary.** The Search UI is a domain module that later buyer UIs can reuse; it consumes
the existing API contract and contains no search logic. Shared code is limited to what M25A
itself uses across the app shell (for example the HTTP client and the loading and error
handling). No speculative shared framework.

**Acceptance essentials stay in M25A:** its unit and Playwright smoke tests, safe rendering of
catalog text, no secrets in the browser bundle, and request and error logging sufficient to
diagnose the UI-to-API flow. M23A and M24A automate and strengthen these; they do not postpone
them.

#### Reuse and completion rules

Before creating a component, service, endpoint or data store, inspect existing ownership and
record **REUSE**, **EXTEND** or **CREATE** with a reason. A CREATE must state why existing code
cannot serve the need.

Every user-facing deliverable needs:

- integrated backend and frontend;
- an explicit API contract;
- relevant automated tests;
- one browser-level smoke path through the UI-to-API flow;
- safe loading, error and empty states;
- appropriate logs, documentation, and fallback or feature flags.

Two clarifications:

- **Browser smoke test.** A `TestClient` check that a page is served is not an end-to-end browser
  test.
- **Report kind.** Mocked or fixture-backed smoke tests and checks against a live local stack are
  reported separately and labelled as such.

Provider-dependent functionality stays off by default.

### Milestone 0 — Specification

Create only:

- `docs/spec.md`
- `docs/architecture.md`
- `docs/data-quality.md`
- ADR-001 PostgreSQL + pgvector
- ADR-002 Hybrid Retrieval with RRF
- ADR-003 DecisionProvider abstraction
- ADR-004 Local embedding strategy
- ADR-005 Jev as optional bounded-decision provider

Include goals, non-goals, requirements, architecture, data model, hard vs soft constraints, evaluation, quality, Jev boundaries, confidence gating, human review, latency, security, risks, and acceptance criteria. Do not write application code. Stop.

### Milestone 1 — Foundation

Implement uv, Python 3.12, FastAPI, PostgreSQL, pgvector, SQLAlchemy, Alembic, Docker Compose, configuration, and `GET /health`. Run tests, lint, and connectivity validation. Stop.

### Milestone 2 — Product catalog

Implement schema, migrations, ingestion, approved data, indexes, and automated quality checks. Prepare a 30-product human-review sample. Do not claim review completion until a human reviews it. Stop.

### Milestone 3 — Lexical baseline V0

Implement PostgreSQL FTS, search-vector generation, indexes, retrieval, and API integration. Measure correctness and P50/P95/P99. This is V0. Stop.

### Milestone 4 — Dense retrieval

Implement configurable local embeddings, generation, pgvector storage/indexes, and vector retrieval. Measure generation and query latency. Document the model decision. Stop.

### Milestone 5 — Hybrid search V1

Implement lexical plus dense retrieval with RRF. Compare lexical, dense, and hybrid. Preserve ranks. This is V1. Stop.

### Milestone 6 — Query understanding

Implement typed query understanding, deterministic parser, normalization, provider interface, and deterministic provider. Test English, short-form, numeric, and Hinglish queries. Do not implement Jev yet. Stop.

### Milestone 7 — Structured filtering V2

Implement safe filters for category, brand, RAM, storage, storage type, min price, and max price. Evaluate extraction, satisfaction, zero results, and contradictions. This is V2. Stop.

### Milestone 8 — Reranking V3

Implement configurable cross-encoder reranking with on/off modes. Benchmark no reranker, rerank 10, and rerank 20 using quality and latency. This is V3. Stop.

### Milestone 9 — Golden Dataset

M9 covers search relevance only:

```text
Query -> candidate product -> human relevance grade
```

Implement:

- typed evaluation contracts;
- deterministic candidate pooling;
- the annotation tool;
- the evaluator;
- retrieval metrics;
- 20 development-pilot query candidates, including Hinglish and noisy queries, over the internal catalog.

The approved pilot contains only 3 Hinglish queries. They are identified separately, and reports disclose the limited coverage. The pilot is not expanded or redesigned. The held-out test partition, with its Hinglish slice, is planned and approved separately. Pause for real human labeling.

The approved M9 decisions cover labels, the single-annotator workflow and disclosure, pool policy, the dev/test partition, and provenance sealing. They were approved before implementation began, override the generic text here, and must be written into the M9 documentation.

Amendment `review-2` (approved 2026-10-15, after the primary round was complete): the single-annotator decision's delayed intra-annotator consistency round is replaced by a blind second-human review of the same cs-1 sample, reported as inter-annotator agreement on that label-stratified sample only. Disagreements are resolved by joint consensus of both humans, and an unresolved disagreement blocks the label freeze. Intra-annotator consistency is not measured. The original decision text is preserved as history in the M9 documentation.

Scope designation (approved by the owner, 2026-10-17): "M9 human-reviewed pilot/dev Golden Dataset complete under review-2, with disclosed limitations. Separately authored held-out test dataset remains deferred; no held-out performance claims are permitted." This is a scope clarification. It does not remove or change the held-out test partition requirement (§10 and §21): that partition is still separately authored and sealed, with a separately reported Hinglish slice, and its size and stratification are still approved when it is planned. The 20 pilot/dev queries and their labels are never held-out evidence.

M9 does not include:

- buyer-RAG answer labels;
- return-reason or root-cause labels;
- HITL implementation;
- marketplace integration;
- ESCI ingestion;
- any future-milestone code.

Stop.

### Milestone 10 — Core Search Experiment Report

Compare V0–V3 from actual runs on the human-reviewed internal Golden Dataset. Include configuration, quality, latency, failure cases, limitations, and a measured recommendation. Re-decide the provisional `rrf_k`, embedding model, and reranker pins (or no reranker).

M10 is complete without ESCI.

Scope amendment (approved by the owner, 2026-10-17): M10 is closed as **development-scoped (pilot/dev)**. This narrows what M10 closure means; it is not a claim that every requirement above was fulfilled. The approved report is `docs/m10-human-pilot-dev-evaluation.md`. Named limitations and deferred items:

1. `rrf_k` = 100 and the current embedding model remain provisional; neither was comparatively re-decided.
2. Fresh latency coverage excludes V0, V1 and `/search/filtered`.
3. The latency run includes connected-standby contamination and unexplained between-run variation; it is not a clean latency baseline.
4. Quality scores use recorded actual retrieval runs, not fresh runs.
5. Held-out evidence remains deferred; no held-out or production claims are permitted.

The no-reranker decision is development-scoped only; the configuration is unchanged. This closure does not by itself satisfy the "M10 complete" prerequisite of M16; that is a separate owner decision when M16 is planned.

ESCI is an optional external benchmark track (§10). It may run only after the core internal M10 report is complete and its license is verified. It runs:

- through its own corpus adapter, as a separately versioned corpus;
- with labels preserved and a documented, versioned mapping (Complement not relevant by default);
- with separate provenance, metrics output, and report.

It never mixes with the internal catalog or the M9 Golden Dataset. It adds no mandatory milestone.

Stop.

### Milestone 11 — Jev Decision Provider

Prerequisites:

- stable V0–V3;
- a human-reviewed Golden Dataset;
- credentials;
- verified official API behavior.

Implement a typed client/provider, timeout, limited retry, safe fallback, configuration, and telemetry. Use bounded decisions only. The core must still work without Jev. Stop.

### Milestone 12 — Jev Query Experiment V4

Create human-reviewed decision data. Compare deterministic, optional LLM, Jev, and confidence-gated Jev. Measure classification, calibration, coverage, latency, cost, and end-to-end retrieval metrics. This is V4. Stop.

### Milestone 13 — Listing Normalization

Implement:

- raw and normalized models;
- listing versions;
- deterministic unit and brand normalization;
- attribute candidates;
- compatibility checks;
- contradiction detection.

Preserve original values. Stop.

### Milestone 14 — Jev Listing Decisions

Implement bounded category, subcategory, attribute-validation, ambiguity, and review-routing decisions. Do not allow invented attributes. Evaluate on human-reviewed data. Stop.

Amended 2026-10-22 (§28 execution order). This adds no acceptance criterion and waives none:

- **Status:** M14 is BLOCKED. When resumed, it continues from its recorded state in
  `docs/m14-progress.md`. It is not rebuilt.
- **UI ownership:** M14–M15 also own the Listings UI and the minimum listing API adapters,
  reusing existing domain logic.
- **Review queue:** the operational review queue remains M21's.

### Milestone 15 — Policy-Risk Triage

Use explicit versioned criteria, human-reviewed examples, audit fields, and manual review. Do not implement automatic seller banning. Measure false positives and false negatives separately. Stop.

### Milestone 16 — Grounded Buyer RAG Assistant

Prerequisites: M10 complete. The V4 retriever is optional, used if M12 has shipped. (M10's development-scoped closure of 2026-10-17 does not by itself satisfy this prerequisite; the owner decides when M16 is planned.)

Implement a bounded assistant that uses existing V3/V4 search as its retriever. It covers:

- product discovery;
- grounded recommendations;
- comparisons;
- clarification for incomplete or contradictory requirements;
- abstention when the catalog cannot support an answer.

Rules:

- Output is typed and cites product IDs.
- Deterministic validation re-checks explicit constraints and citations against catalog fields after generation.
- Prices, specifications, and availability come only from the catalog.
- No order, payment, discount, or refund actions.
- The bounded generation workflow has a fixed step count and at most one repair attempt.
- The generation provider sits behind a typed interface and is selected by ADR.
- When the provider is unavailable, the assistant degrades to search results or abstention.

Stop.

### Milestone 17 — Buyer RAG Evaluation and Safety

Create a separate, versioned, human-reviewed answer-evaluation dataset. It is not the M9 Golden Dataset.

Measure:

- faithfulness;
- answer relevance;
- citation correctness;
- unsupported-claim rate;
- constraint satisfaction;
- clarification accuracy;
- abstention accuracy;
- latency;
- token usage;
- cost.

Add prompt-injection tests, malicious listing-content tests, and false-refusal tests. Report failures honestly. Stop.

### Milestone 18 — Return Data Foundation

Implement typed, versioned contracts and storage for:

- orders and order items;
- returns and return reason codes;
- customer-written return comments;
- reviews and support complaints;
- delivery/fulfilment signals;
- product/SKU linkage;
- listing versions;
- correction cases.

Implement PII redaction, provenance fields, and validation. Ingest data from CSV/JSON. Build the separate, linked Return Intelligence dataset with every synthetic record marked. Preserve raw evidence separately from normalized values. Stop.

### Milestone 19 — Return Reason Intelligence

Implement:

- deterministic baselines (reason-code mapping and keyword rules);
- typed GenAI multi-label classification with confidence, including Hinglish and spelling-error support;
- responsible-area prediction;
- complaint clustering by SKU/variant/pattern;
- low-confidence review routing.

Use the §19 categories where the data supports them. Evaluate against a separate human-reviewed return-reason set, with per-class precision, recall, and F1, and compare the GenAI classifier with the deterministic baseline. Stop.

### Milestone 20 — Evidence Retrieval and Root-Cause RAG

Retrieve only authorized evidence:

- the current listing and historical listing versions;
- previous returns, reviews, and support complaints;
- similar SKU cases;
- category guidelines;
- seller documents;
- marketplace policies.

Produce typed output containing:

- the affected SKU/variant;
- the observed pattern;
- supporting evidence references;
- the likely root-cause hypothesis;
- confidence;
- the responsible team;
- the recommended next investigation.

Require abstention or manual investigation when evidence is insufficient or conflicting. Correlation is reported as a hypothesis, never a confirmed cause. Evaluate on a separate human-reviewed root-cause set. Stop.

### Milestone 21 — Corrective Action and HITL Workflow

Implement corrective-action proposals:

- title/description corrections;
- size-chart corrections;
- specification corrections;
- product-photography requests;
- packaging recommendations;
- supplier-quality investigations;
- warehouse/logistics escalations.

Each proposal shows an original-versus-proposed diff.

Implement the review workflow:

- actions: approve, edit, reject, request more evidence, escalate;
- an authorized-reviewer check;
- an append-only audit trail;
- model, prompt, policy, and evidence versions on every record;
- rollback history.

Approved changes become an export or an internal catalog update followed by a search-index refresh. There is no autonomous marketplace publishing; every marketplace publication requires explicit authorized approval (§6). Simulated developer reviews are marked `simulated`. Stop.

### Milestone 22 — Closed-Loop Evaluation

Measure:

- overall return rate and preventable-return rate;
- SKU and variant return rates;
- before/after correction outcomes;
- return-reason classification quality;
- root-cause quality;
- recommendation acceptance, plus human edits and rejections;
- false recommendation rate;
- time to resolution;
- search-quality impact;
- estimated cost savings, with explicit assumptions.

Label every measured, simulated, and estimated figure as such. Do not present simulated or estimated impact as real production impact. Do not claim causation from before/after comparisons alone. Stop.

### Milestone 23 — Regression CI

Implement unit/integration CI, evaluation smoke tests, provider contract tests, and configurable thresholds. Routine CI must not require live Jev or any live generation provider. Stop.

Amended 2026-10-22: delivered as M23A (existing core search and the M25A page) and M23B (the rest); see §28 execution order.

### Milestone 24 — Observability

Implement:

- structured logging;
- request IDs;
- provider telemetry;
- stage timings;
- fallback, confidence, and review metrics;
- token and cost metrics;
- P50/P95/P99 reporting.

Logs never contain unredacted PII. Stop.

Amended 2026-10-22: delivered as M24A (existing search path and the M25A page) and M24B (the rest); see §28 execution order.

### Milestone 25 — Dual Frontend

Buyer UI:

- search, filters, and product cards;
- the assistant;
- comparison;
- citations;
- clarification;
- feedback.

Retailer UI:

- return dashboard;
- affected SKUs and complaint clusters;
- evidence viewer;
- root-cause reports;
- proposed listing diff;
- HITL review queue;
- correction history;
- outcome reports.

Build only the screens whose backend milestones exist. Never expose hidden reasoning or unredacted PII. Stop.

Amended 2026-10-22 (§28 execution order):

- **Inventory:** the lists above remain the UI inventory.
- **M25A:** the buyer Search page over the existing Search backend only (scope in §28).
- **Domain screens:** first-time domain screens are built by their owning domain milestones
  (M14–M15, M16–M17, M18–M22).
- **M25B:** unifies and hardens those existing UIs. It does not create first-time domain UI.

### Milestone 26 — AWS Deployment

Only after local stability, implement:

- production image;
- ECR;
- ECS/Fargate;
- PostgreSQL with pgvector;
- Secrets Manager;
- CloudWatch;
- Terraform.

Do not introduce EKS. Require approval before chargeable actions. Stop.

Amended 2026-10-22: delivered as M26A (existing core search and the M25A page) and M26B (the rest); see §28 execution order. Local stability and approval before chargeable actions apply to both parts.

### Milestone 27 — Optional Authorized Marketplace Integration

This milestone is optional and needs authorized official access. It may cover:

- authorized seller-report import;
- return-report import;
- export of approved corrections;
- official API integration where access exists.

No scraping and no unofficial access. The project does not pretend that Amazon, Flipkart, or any other production API access exists. M27 may submit only changes that an authorized human reviewer has explicitly approved; it never permits autonomous publishing. The portfolio MVP remains valid with CSV/JSON import and export. Stop.

## 29. Definition of done

**Core search** is done only when all of the following exist:

- PostgreSQL/pgvector;
- catalog quality;
- lexical, dense, and hybrid retrieval;
- RRF;
- deterministic understanding;
- structured filtering;
- reranking;
- a human-reviewed Golden Dataset;
- evaluation;
- FastAPI;
- tests;
- measured latency and quality;
- an experiment report;
- an explanatory README.

**Jev** is done only when all of the following exist:

- official integration;
- typed validation;
- fallback;
- model/version logging;
- human evaluation;
- latency and cost measurement;
- calibration;
- justified thresholds;
- a deterministic-vs-Jev comparison.

**Listing intelligence** is done only when:

- raw data is preserved;
- normalization and contradiction detection work;
- decisions are bounded;
- uncertainty reaches human review;
- evaluation is human-reviewed.

**Buyer assistant** is done only when:

- answers are grounded and cite product IDs;
- constraints and citations are validated deterministically;
- clarification and abstention work;
- no order or payment actions exist;
- a separate human-reviewed answer evaluation reports faithfulness, citation correctness, unsupported-claim rate, constraint satisfaction, and false-refusal rate, plus latency, tokens, and cost;
- injection tests exist.

**Return intelligence** is done only when:

- raw return evidence is preserved and PII is redacted;
- classification is compared with a deterministic baseline on human-reviewed data;
- root-cause output is evidence-referenced and abstains when unsupported;
- every correction passes an authorized human decision with a full audit trail and rollback;
- closed-loop reports clearly label measured, simulated, and estimated figures.

## 30. Final principles

Prefer:

- deterministic extraction over unnecessary inference;
- bounded decisions over unrestricted generation;
- grounded, cited answers over fluent unsupported ones;
- abstention over guessing;
- confidence gating over blind automation;
- human authority over automated action;
- human-reviewed labels over generated ground truth;
- hypotheses over asserted causes;
- measured improvement over assumed performance;
- simple architecture over unnecessary infrastructure.

## Start now

**Superseded 2026-10-22.** The text below is the historical start instruction from the M9
period, kept for the record. M9–M13 have since progressed or closed with the scopes and
deferrals recorded in §"Repository status at
this revision", and M14 is BLOCKED. M25A, the first item of §28, "Execution order (amended 2026-10-22)", was
accepted on 2026-10-22, and M23A was completed on 2026-10-22 (`docs/m23a-progress.md`). M24A, the
next step, is in progress (`docs/m24a-progress.md`).

Historical text:

M0–M8 are complete. The next implementation milestone is **Milestone 9 — Golden Dataset**. Its Session B contracts (`15f3ac1`) and Session C1 (`6009fa3`, `queries_frozen`) are committed; Session C2 (`pool_frozen`, blind annotation CLI, primary-seal tooling, retrieval-metric evaluator) is committed. The next step is human primary annotation by `ann-1`.

Inspect the repository and the existing M9 work before continuing. Continue only M9, which is limited to search relevance:

```text
Query -> candidate product -> human relevance grade
```

Do not create:

- buyer-RAG answer labels;
- return-reason or root-cause labels;
- HITL code;
- ESCI ingestion;
- marketplace integration;
- any code for M10 or later.

Those require their own separately versioned datasets in their own milestones. Do not call Jev or any generation provider, and do not generate fake labels or results. Pause for real human labeling where M9 requires it. Report files and commands, then stop.
