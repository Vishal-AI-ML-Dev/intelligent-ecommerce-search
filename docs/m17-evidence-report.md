# M17 evidence report — Buyer RAG Evaluation and Safety (`abe-v1`)

**Status: evidence collected; M17 NOT accepted.** This report states what was measured and what
was not. It makes no acceptance decision. Each run's results stay separate; nothing below is a
pooled or full-dataset result, and no single run is both complete over all 40 cases and
human-reviewed.

## Evidence base

- **Dataset `abe-v1`:** 40 `eval` cases (frozen: cases `3ce04c99…7f829`, annotations
  `e1fdebeb…daca5b`), separate from the M9 Golden Dataset and the dev fixtures. Messages and
  expectations were AI-authored; all 40 cases were reviewed and decided by the owner (`ann-1`;
  36 approved, 4 revised); 20 metric-eligibility annotations owner-approved. Evidence is a frozen
  V3 top-5 capture from the project's **synthetic** seed catalog (240 records, not real listings
  or prices). Classes: 38 scored, 1 contract-conformance (`c05`), 1 diagnostic (`c03`).
- **Provider:** Gemini `gemini-3.5-flash-lite` (free tier), timeout 20 s, repair deadline 30 s,
  max output 1024 tokens, evidence k 5, SDK retries off, one attempt per call.
- **Human review:** one reviewer handle (`ann-1`). Of 40 judgements, 32 are recorded as
  **AI-assisted and explicitly owner-endorsed** and 8 as provenance **not stated** (neither is
  "unaided"). This is **not independent human validation**; no inter-annotator agreement exists.

## Runs (never pooled)

| Run | Code | Scope | Status | Calls | Provider failures | Saved responses | Human review |
|---|---|---|---|---|---|---|---|
| `abe-v1-live-001` | `db1c450` | all 40 | complete | 29 | 0 | **none** (not persisted) | none possible |
| `abe-v1-live-002` | `3a100a3` | all 40 | **incomplete** (timeout) | 12 | `g02` invalid_response; `f01` timeout | 23 | 23/23 judged |
| `abe-v1-live-003` | `7f87a3c` | 17-case subset (002's unrun) | **incomplete** (timeout) | 2 | `f03` timeout | 2 | 2/2 judged |
| `abe-v1-live-004` | `ab96d81` | 15-case subset (003's unrun) | complete | 15 | 0 | 15 | 15/15 judged |

Provider failures across all runs: **3** — **2 timeouts** (`f01` in run 002, `f03` in run 003)
and **1 `invalid_response`** (`g02` in run 002). Timeout origins are **unknown** (not recorded before `ab96d81`; elapsed time alone cannot
establish them). Provider call time varied widely across runs (p50 2.5 s, 13.8 s, 1.5 s
(n=2), 1.6 s). Every one of the 40 cases has exactly one human-judged saved response, but those
come from three different runs (002, 003, 004) with different code commits and conditions; three
of them (`g02`, `f01`, `f03`) are generation failures, so their answer quality was never judged.

### Automated results per run

| Metric | 001 (40, complete) | 002 (23 of 40) | 003 (2 of 17) | 004 (15 of 15) |
|---|---|---|---|---|
| Displayed model/seller prose | 0/39 | 0/22 | 0/2 | 0/15 |
| Citation correctness | 64/64 | 21/21 | 3/3 | 32/32 |
| Server-checked requirements (constraint satisfaction) | 64/64 (0/193 unverified) | 21/21 (0/65) | 3/3 (0/9) | 32/32 (0/89) |
| Generation unavailable | 0/39 | **2/22** | **1/2** | 0/15 |
| False refusals (expected-answer cases) | 0/28 | 0/11 | 0/2 | 0/15 |
| Selection within acceptable set (catalog expectations) | 28/28 | 9/9 | 1/1 | 15/15 |
| Forbidden / injection target recommended | 0/12 · 0/12 | not run | not run | 0/12 · 0/12 |
| Injection pair behaviour changed (same-run pairs) | 0/12 | — | — | 0/2 (10 unavailable) |
| Clarification accuracy · contract conformance | 4/4 · 1/1 | 4/4 · 1/1 | — | — |
| Abstention: deterministic · model cohort | 4/4 · **1/2** | 4/4 · **1/2** | — | — |
| Tokens in / out | 25,115 / 3,704 | 8,781 / 1,379 | 692 / 170 | 13,522 / 1,974 |
| Provider call p50 / max | 2.5 s / 13.0 s | 13.8 s / 17.6 s | 1.5 s / 20.2 s | 1.6 s / 7.0 s |
| Cost | see Cost section: paid-equivalent estimate only; billing not verified | partial estimate | partial estimate | estimate |

Hard gates in the freeze manifest (0 displayed prose, 0 invalid citations, 0 failed
server-checked requirements, 0 generation-unavailable in a complete run) were met by run 001,
the only complete 40-case run; run 004 met them for its 15-case subset (subset results are not
evaluated against full-population thresholds). Runs 002 and 003 are incomplete and receive no
verdict.

### Human review per run (`aj-1`, scored cases excluding contract unless stated)

| Run | Judged | Outcome appropriate | Not misleading | Product fit (fits / partly / does not / unsure) | Provenance |
|---|---|---|---|---|---|
| 002 | 21 scored + contract `c05` + diagnostic `c03` | 17/21 (contract `c05`: 0/1; diagnostic `c03`: 0/1) | 20/20 + 1 unsure (`u01`); `c03` misleading: yes | 18 / 3 / 0 / 0 of 21 | 13 AI-assisted endorsed, 8 not stated (scored) |
| 003 | 2 | 1/2 | 2/2 | 3 / 0 / 0 / 0 of 3 | 2 AI-assisted endorsed |
| 004 | 15 | 13/15 | 15/15 | 27 / 5 / 0 / 0 of 32 | 15 AI-assisted endorsed |

Outcome "no" cases: `g02`, `f01`, `f03` (generation failed; fallback honestly labelled), `g06`,
`f04`, `f06` (stated use not established), `u01` (unrecognised category asked again), `c05`
(range unsupported), `c03` (diagnostic: compatible upper bounds called conflicting). Human
thresholds in the manifest are **not evaluated**: no complete run has human review.

## Cost (sourced, offline; recorded 2026-10-23)

Source: Google, "Gemini Developer API pricing", https://ai.google.dev/gemini-api/docs/pricing,
accessed 2026-10-23 (page "Last updated 2026-10-22 UTC"). Model `gemini-3.5-flash-lite`,
Standard pricing, single tier (no context-length tiers listed):

- **Published free-tier price:** input and output "Free of charge" (free-tier content may be
  used by Google to improve its products).
- **Paid-tier list price** (basis for the paid-equivalent estimate only): input USD 0.30 per
  1M tokens (text); output USD 2.50 per 1M tokens, "including thinking tokens". The recorded
  output counts already include thinking tokens (the adapter adds them).
- **Actual billing: not verified.** No billing or account data was read. The runs used the free
  tier (ADR-009); nothing billed is claimed.
- **Historical applicability:** the page shows its update date, not the price in force at each
  run time; the runs took place on 2026-10-23, after that update, but the price in force then is
  not independently verified.

Paid-equivalent estimate = recorded input tokens × 0.30/1M + recorded output tokens × 2.50/1M,
per run, never pooled. Missing usage stays unknown, not zero.

| Run | Calls | Recorded input / output tokens | Calls without usage | Paid-equivalent estimate (USD) |
|---|---|---|---|---|
| 001 | 29 | 25,115 / 3,704 | 0 | 0.0167945 |
| 002 | 12 | 8,781 / 1,379 | 1 (`f01` timeout) | **partial:** 0.0060818 for 11 calls; run total unknown |
| 003 | 2 | 692 / 170 | 1 (`f03` timeout) | **partial:** 0.0006326 for 1 call; run total unknown |
| 004 | 15 | 13,522 / 1,974 | 0 | 0.0089916 |

Whether failed (timed-out) calls would be billed on the paid tier is unknown.

## Unsupported claims (post-hoc, provisional, not adjudicated; recorded 2026-10-24)

Tool: `evals/assistant/claims.py` (`python -m evals.assistant claims`). **Post-hoc:** defined after
runs 002–004 existed; descriptive only, no threshold, not a preregistered pass criterion. Runs are
never pooled; run 001 has no saved responses and no measurement. **No human adjudication exists;
no rate is reported.** Mechanical verdicts check only displayed values against the frozen
synthetic catalog (catalog consistency), never real-world truth; catalog-consistent seller text
is left for human review, never marked unsupported because no external check exists.

**Boundary amendment `uc-1` → `uc-2` (also post-hoc, not blind).** The `uc-1` boundaries were
fixed before extraction, but its output (kept unchanged and uncommitted in
`evals/results/m17/unsupported-claims/`) showed three defects; `uc-2` was defined after seeing
it: D1 "?" is now a boundary; D2 a clause that is only a question, or exactly one of the six
known instruction sentences of the server templates, is a recorded non-claim (a clause mixing an
instruction with an assertion stays a claim); D3 the known ", so" status notices are two claims.
D4: message phrases that no stated requirement, `not_assessed` note or filler word accounts for
are listed as **unadjudicated omission candidates** (`omission-candidates.jsonl`, exact text,
span and exclusion reasons), never as omission findings or requirements. Outputs:
`evals/results/m17/unsupported-claims-uc-2/abe-v1-live-00{2,3,4}/`.

| Run | Claims uc-1 → uc-2 | Mechanical supported | Contradicted | Unresolved (needs human) | Non-claims | Omission findings | Candidates (unadjudicated) |
|---|---|---|---|---|---|---|---|
| 002 | 214 → 215 | 169 → 173 | 0 → 0 | 45 → 42 | 9 (6 questions, 3 instructions) | 9 → 9 | 0 (7 cases excluded: no recommendation) |
| 003 | 27 → 27 | 24 → 24 | 0 → 0 | 3 → 3 | 0 | 0 → 0 | 1 (`f02` "sasta") |
| 004 | 286 → 286 | 246 → 246 | 0 → 0 | 40 → 40 | 0 | 19 → 19 | 6 (`f04`, `f05`, `f06` ×2, `m06`, `l04`) |

Run 002 changes: four `u02`/`u03`/`u05`/`u06` notices split into "No catalog products matched
this request" (needs human: catalog-wide claim) and "so no answer was generated." (mechanically
supported); the merged `c06`/`u01` question+instruction claims and the `c05` "Please give…"
instruction left the claim set. Mechanical "supported" means consistent with the frozen synthetic
catalog or the response itself, not true in the real world.

## Known limitations

- `u01`: category-recognition limitation (clarifies "smartwatch" instead of abstaining).
- `c03`: query-understanding limitation (two compatible upper bounds treated as a conflict).
- `c05`: price ranges unsupported (contract conformance, not shopper success).
- Evidence-sufficiency gap: ANC absence not visible for three `g04` records (`m01`, `l01` targets).
- Use-suitability claims (coding, blocking voices, elderly usability, exams) are not assessed by
  any check; explanations do not claim them.
- Injection results are fixed-evidence selection tests from single runs: not end-to-end injection
  robustness, not causal proof; 10 of 12 run-004 pairs have no same-run clean counterpart (run
  001 had all 12 pairs, automated only).
- Workflow latency figures include harness pacing; only provider call time is usable; no
  end-to-end API latency under load was measured.

## MASTER_PLAN M17 requirements

| Requirement | Assessment | Basis |
|---|---|---|
| Separate, versioned, human-reviewed answer-evaluation dataset (not M9) | **Met, with caveats** | `abe-v1` frozen and versioned, all 40 cases owner-reviewed; cases AI-authored, synthetic catalog, one reviewer |
| Faithfulness | **Partial** | Human "misleading" judgements cover all 40 cases but across three runs, AI-assisted/owner-endorsed, one reviewer; no complete reviewed run |
| Answer relevance | **Partial** | Human product-fit judgements per run (002: 18 fits / 3 partly of 21; 003: 3 / 0 of 3; 004: 27 / 5 of 32; not pooled); 3 cases never answered |
| Citation correctness | **Met** | Automated, every run: all recommended products' citations correct |
| Unsupported-claim rate | **Unmet (post-hoc measurement not adjudicated)** | Post-hoc `uc-2` claim extraction for runs 002–004 (see Unsupported claims): 0 mechanically contradicted, 42/3/40 unresolved, no human adjudication, no rate; proxies: 0 model/seller prose; human "misleading" verdicts per run |
| Constraint satisfaction | **Met for server-checked fields; partial otherwise** | 0 failed server-checked requirements in every run; catalog-expectation fit (gender, ANC, colour, availability) via acceptable sets and human review only |
| Clarification accuracy | **Met (with a recorded limitation)** | 4/4 in runs 001/002; `c03` unnecessary clarification reported as a diagnostic |
| Abstention accuracy | **Met as measured; failures reported** | deterministic 4/4; model cohort 1/2 (`u01` known failure) |
| Latency | **Partial** | Provider call time per run; workflow times pacing-contaminated; no API latency under load |
| Token usage | **Met** | Provider-reported per call; missing counts for failed calls listed |
| Cost | **Partial** | Sourced paid-equivalent estimates per run (runs 002/003 partial: missing usage); free tier published as free; actual billing not verified |
| Prompt-injection tests | **Partial** | 6 message injections: 0/6 targets selected in runs 001 and 004; single runs, fixed evidence; run 004 same-run pair only for `m06` |
| Malicious listing-content tests | **Partial** | 6 listing injections: 0/6 targets in runs 001 and 004; same caveats; run 004 same-run pair only for `l04` |
| False-refusal tests | **Partial** | 0 refusals in every run, but `f01`, `f03` ended as generation failures (timeouts) in runs 002/003 |
| Report failures honestly | **Met** | Incomplete runs, 3 provider failures (2 timeouts, origin unknown; 1 invalid_response), known limitations and all exclusions reported |
| §5 measured provider selection (moved to M17) | **Unmet** | Quality and latency measured; cost estimated (sourced, billing unverified); no ADR records a measured selection |
| Frozen human thresholds (manifest) | **Not evaluated** | Required population (one complete run's responses, human-reviewed) does not exist |

## Acceptance gaps, checked against the exact rules

Sources: `docs/MASTER_PLAN.md` (M17 section, §5 generation models, evaluation metrics, annotation
rules, milestone table) and the frozen, owner-approved `freeze-manifest.json` evaluation rules.
No gate is added here and no approved threshold is waived.

### Mandatory gates not met

1. **Human thresholds (frozen rule).** Manifest: "overall_quality_verdict: None before human review
   is complete" and human thresholds "faithfulness_human (not misleading) at least 95% of responses
   judged yes or no; answer_relevance_human (fits) at least 85% of judged products;
   outcome_appropriate_human at least 85% of responses judged yes or no", set for the complete
   40-case run. Their required population (human judgements of one complete run's responses) does
   not exist: run 001 is complete but has no saved responses; runs 002–004 are partial or subsets.
   **Not evaluated** (not met, not failed).
2. **Measured provider selection (MASTER_PLAN §5).** "is selected in its milestone through an ADR
   from measured quality, latency, and cost; … for M16 the measured quality, latency and cost
   selection of the Gemini provider is moved to M17 … The measurement requirement itself is
   unchanged and not yet met." Quality and latency are measured; cost now has a sourced
   paid-equivalent estimate (billing unverified); no ADR records a measured selection. **Unmet**
   (the ADR step remains).
3. **Cost (MASTER_PLAN M17 "Measure: … cost"; evaluation list "latency, tokens, and cost").**
   Sourced paid-equivalent estimates recorded per run (see Cost); runs 002 and 003 are partial
   (missing usage); actual billing not verified. **Partially met**; whether an estimate satisfies
   "measure cost" is an owner decision.
4. **Unsupported-claim rate (MASTER_PLAN M17 "Measure: … unsupported-claim rate").** No metric with
   that definition was predeclared; a post-hoc extraction (`uc-2`, after a post-hoc boundary
   amendment) exists for runs 002–004 but no claim has been human-adjudicated, so no rate is
   reported. **Unmet** until adjudication; the measurement stays post-hoc and descriptive.

### Met, with evidence limitations

- "Create a separate, versioned, human-reviewed answer-evaluation dataset. It is not the M9 Golden
  Dataset." — met: `abe-v1`, 40 cases, owner-reviewed. Limitations: AI-authored cases; synthetic
  catalog.
- Milestone table "Real human reviewers"; annotation rules "If only one annotator exists, reports
  say so and do not claim inter-annotator agreement. AI is never an annotator, and AI suggestions
  become labels only after explicit human review." — met as worded: `ann-1` explicitly reviewed
  and endorsed every judgement; one reviewer, stated; 32 of 40 judgements AI-assisted and
  owner-endorsed, 8 provenance not stated. Not independent validation.
- "Measure: faithfulness; answer relevance" — measured by human judgements per run (002, 003,
  004), covering all 40 cases once but across three runs with different code commits; three cases
  (`g02`, `f01`, `f03`) were judged only as generation failures. Not pooled.
- "citation correctness", "constraint satisfaction" — met: hard gates met in complete run 001 and
  in every partial run (server-checked fields only; catalog expectations via acceptable sets).
- "clarification accuracy", "abstention accuracy" — measured: 4/4, contract 1/1, deterministic
  cohort 4/4, model cohort 1/2 (`u01`, reported failure); `c03` diagnostic.
- "Add prompt-injection tests, malicious listing-content tests, and false-refusal tests"; §17 item 7
  "Prompt-injection and malicious-content tests are mandatory (M17)" — tests added and run: run 001
  (complete) 0/12 targets, 0/12 pair changes, false refusals 0/28 (manifest quality targets met);
  run 004 0/12 targets. Limitation: single runs on fixed evidence, not end-to-end robustness.
- "latency", "token usage" — provider call latency and provider-reported tokens per run.
  Limitation: workflow latency includes pacing; no latency under load.
- "Report failures honestly" — met.

### Optional improvements (not required by any rule)

- An independent second reviewer and agreement measure.
- Same-run clean/injected pairs for all 12 injection cases with human review.
- End-to-end API latency under load; a further diagnostics-enabled run to observe timeout origin.
