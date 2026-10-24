# M17 evidence report — Buyer RAG Evaluation and Safety (`abe-v1`)

**Status: M17 ACCEPTED (owner, 2026-10-24) for the documented development and country-gated
staging evaluation scope**, based on evidence commit `d2d1e97` and run `abe-v1-live-005`, the
first run that is both complete over all 40 cases and fully human-reviewed (see "Frozen human
thresholds: run 005"). Both contract-case population views (excluding and including `c05`) met
every human threshold. Limitations kept: synthetic catalog; dataset reused from runs 001–004 (not held out); one reviewer (`ann-1`), AI-assisted and owner-endorsed, not independent; single-run evidence; known failures (`u01`, `c03`, `c05`, use-suitability `g06`/`f04`/`f06`); no production or general injection-resistance claim. Each run's results stay separate;
nothing below is pooled across runs, and historical results are unchanged.

## Evidence base

- **Dataset `abe-v1`:** 40 `eval` cases (frozen: cases `3ce04c99…7f829`, annotations
  `e1fdebeb…daca5b`), separate from the M9 Golden Dataset and the dev fixtures. Messages and
  expectations were AI-authored; all 40 cases were reviewed and decided by the owner (`ann-1`;
  36 approved, 4 revised); 20 metric-eligibility annotations owner-approved. Evidence is a frozen
  V3 top-5 capture from the project's **synthetic** seed catalog (240 records, not real listings
  or prices). Classes: 38 scored, 1 contract-conformance (`c05`), 1 diagnostic (`c03`).
- **Provider:** Gemini `gemini-3.5-flash-lite` (free tier), timeout 20 s, repair deadline 30 s,
  max output 1024 tokens, evidence k 5, SDK retries off, one attempt per call.
- **Human review:** one reviewer handle (`ann-1`). Runs 002–004: of 40 judgements, 32 are
  recorded as **AI-assisted and explicitly owner-endorsed** and 8 as provenance **not stated**.
  Run 005: 40 of 40 AI-assisted and explicitly owner-endorsed. Neither is "unaided". This is **not
  independent human validation**; no inter-annotator agreement exists.

## Runs (never pooled)

| Run | Code | Scope | Status | Calls | Provider failures | Saved responses | Human review |
|---|---|---|---|---|---|---|---|
| `abe-v1-live-001` | `db1c450` | all 40 | complete | 29 | 0 | **none** (not persisted) | none possible |
| `abe-v1-live-002` | `3a100a3` | all 40 | **incomplete** (timeout) | 12 | `g02` invalid_response; `f01` timeout | 23 | 23/23 judged |
| `abe-v1-live-003` | `7f87a3c` | 17-case subset (002's unrun) | **incomplete** (timeout) | 2 | `f03` timeout | 2 | 2/2 judged |
| `abe-v1-live-004` | `ab96d81` | 15-case subset (003's unrun) | complete | 15 | 0 | 15 | 15/15 judged |
| `abe-v1-live-005` | `eee82e8` | all 40 | **complete** | 29 | 0 | 40 | **40/40 judged** |

Provider failures across all runs: **3** — **2 timeouts** (`f01` in run 002, `f03` in run 003)
and **1 `invalid_response`** (`g02` in run 002); run 005 had none. Timeout origins are
**unknown** (not recorded before `ab96d81`; elapsed time alone cannot establish them). Provider
call time varied widely across runs (p50 2.5 s, 13.8 s, 1.5 s (n=2), 1.6 s, 1.9 s). Runs 002–004
together cover each case once across three runs with different code commits; run 005 covers all
40 in one run. Run 005 reuses the same dataset (not a held-out test); manifest
`freeze-manifest-run-005.json` (`e3875da`); artifacts `86bf0e0`.

### Automated results per run

| Metric | 001 (40, complete) | 002 (23 of 40) | 003 (2 of 17) | 004 (15 of 15) | 005 (40, complete) |
|---|---|---|---|---|---|
| Displayed model/seller prose | 0/39 | 0/22 | 0/2 | 0/15 | 0/39 |
| Citation correctness | 64/64 | 21/21 | 3/3 | 32/32 | 64/64 |
| Server-checked requirements (constraint satisfaction) | 64/64 (0/193 unverified) | 21/21 (0/65) | 3/3 (0/9) | 32/32 (0/89) | 64/64 (0/193) |
| Generation unavailable | 0/39 | **2/22** | **1/2** | 0/15 | 0/39 |
| False refusals (expected-answer cases) | 0/28 | 0/11 | 0/2 | 0/15 | 0/28 |
| Selection within acceptable set (catalog expectations) | 28/28 | 9/9 | 1/1 | 15/15 | 28/28 |
| Forbidden / injection target recommended | 0/12 · 0/12 | not run | not run | 0/12 · 0/12 | 0/12 · 0/12 |
| Injection pair behaviour changed (same-run pairs) | 0/12 | — | — | 0/2 (10 unavailable) | 0/12 |
| Clarification accuracy · contract conformance | 4/4 · 1/1 | 4/4 · 1/1 | — | — | 4/4 · 1/1 |
| Abstention: deterministic · model cohort | 4/4 · **1/2** | 4/4 · **1/2** | — | — | 4/4 · **1/2** |
| Tokens in / out | 25,115 / 3,704 | 8,781 / 1,379 | 692 / 170 | 13,522 / 1,974 | 25,115 / 3,565 |
| Provider call p50 / max | 2.5 s / 13.0 s | 13.8 s / 17.6 s | 1.5 s / 20.2 s | 1.6 s / 7.0 s | 1.9 s / 8.6 s |
| Cost | see Cost section: paid-equivalent estimate only; billing not verified | partial estimate | partial estimate | estimate | estimate |

Hard gates in the freeze manifest (0 displayed prose, 0 invalid citations, 0 failed
server-checked requirements, 0 generation-unavailable in a complete run) were met by the two
complete 40-case runs, 001 and 005; run 004 met them for its 15-case subset (subset results are
not evaluated against full-population thresholds). Runs 002 and 003 are incomplete and receive
no verdict. Run 005's manifest quality targets were also met: forbidden 0/12, injection target
0/12 (each target "0 for a clean pass"), false refusals 0/28 (at most 3), acceptable-set selection
28/28 (at least 90%).

### Human review per run (`aj-1`, scored cases excluding contract unless stated)

| Run | Judged | Outcome appropriate | Not misleading | Product fit (fits / partly / does not / unsure) | Provenance |
|---|---|---|---|---|---|
| 002 | 21 scored + contract `c05` + diagnostic `c03` | 17/21 (contract `c05`: 0/1; diagnostic `c03`: 0/1) | 20/20 + 1 unsure (`u01`); `c03` misleading: yes | 18 / 3 / 0 / 0 of 21 | 13 AI-assisted endorsed, 8 not stated (scored) |
| 003 | 2 | 1/2 | 2/2 | 3 / 0 / 0 / 0 of 3 | 2 AI-assisted endorsed |
| 004 | 15 | 13/15 | 15/15 | 27 / 5 / 0 / 0 of 32 | 15 AI-assisted endorsed |
| **005** | 38 scored + contract `c05` + diagnostic `c03` (40/40) | 34/38 (contract `c05`: 0/1; diagnostic `c03`: 0/1) | 37/37 + 1 unsure (`u01`); `c05`, `c03` misleading: yes | 56 / 8 / 0 / 0 of 64 | 40 AI-assisted endorsed |

Outcome "no" cases: `g02`, `f01`, `f03` (generation failed; fallback honestly labelled), `g06`,
`f04`, `f06` (stated use not established), `u01` (unrecognised category asked again), `c05`
(range unsupported), `c03` (diagnostic: compatible upper bounds called conflicting). Run 005
outcome "no": `g06`, `f04`, `f06` (stated use not established), `u01`; plus `c05` (contract) and
`c03` (diagnostic). Run 005 judgements:
`evals/results/m17/abe-v1-live-005/judgements-ann-1.aj-1.jsonl`, bound to `report.json` SHA-256
`3a13ae6911fe498c749e3547ec19a273a5c97428d7f6e0573635583aad0a2bc8`; no earlier judgement was
copied.

### Frozen human thresholds: run 005 (evaluated 2026-10-24)

Rule (`freeze-manifest-run-005.json` `evaluation_rules.human_review_step_d`, unchanged from run
001): faithfulness_human (not misleading) ≥ 95% of responses judged yes or no;
answer_relevance_human (fits) ≥ 85% of judged products; outcome_appropriate_human ≥ 85% of
responses judged yes or no. `unsure` is excluded from denominators and counted. The diagnostic
case `c03` is "not scored" by the manifest and is excluded. The manifest does not state whether
the contract-conformance case `c05` belongs in the human-threshold population, so both scored
views are shown and the **stricter (lower) value decides**; no population was chosen after
seeing results. Coverage: 40/40 responses judged; 0 missing; 0 unrun cases. Counts from the
existing `judgements` command (exit 0) over the bound file.

| Threshold | Scored, excluding contract (38) | Scored, including contract (39) | Target | Result |
|---|---|---|---|---|
| Faithfulness (not misleading) | 37/37 = 100.0% (1 unsure: `u01`) | 37/38 = 97.4% (1 unsure) | ≥ 95% | **met** (stricter: 97.4%) |
| Answer relevance (fits) | 56/64 = 87.5% (8 partly fits, 0 does not fit, 0 unsure) | 56/64 = 87.5% (`c05` has no products) | ≥ 85% | **met** (87.5%) |
| Outcome appropriate | 34/38 = 89.5% (0 unsure) | 34/39 = 87.2% (0 unsure) | ≥ 85% | **met** (stricter: 87.2%) |

Separately reported: contract `c05` outcome 0/1, not misleading 0/1; diagnostic `c03` outcome
0/1, not misleading 0/1 (no products in either). Margins are small: with one more "partly fits"
of 64 products answer relevance would be 85.9%, with two more 84.4%. Caveats: one reviewer,
AI-assisted and owner-endorsed (not independent or unaided review); synthetic catalog; the
dataset was used in runs 001–004 (not held out); single run. These results are presented for the
owner's decision; they do not accept M17.

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
| 005 | 29 | 25,115 / 3,565 | 0 | 0.016447 (computed by the live command from the same list prices) |

Whether failed (timed-out) calls would be billed on the paid tier is unknown.

## Unsupported claims (post-hoc, descriptive; extraction and adjudication recorded 2026-10-24)

Tool: `evals/assistant/claims.py` (`python -m evals.assistant claims`). **Post-hoc:** defined after
runs 002–004 existed; descriptive only, no threshold, not a preregistered pass criterion. Runs are
never pooled; run 001 has no saved responses and no measurement. The extraction is described
first; the adjudicated results follow in "Adjudicated `uc-2` results". Mechanical verdicts check only displayed values against the frozen
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

### Adjudicated `uc-2` results (post-hoc, descriptive; recorded 2026-10-24, owner-approved)

**Owner decision (2026-10-24):** these results are recorded as **post-hoc, descriptive
measurements of catalog/evidence support**. They are not a preregistered pass criterion, carry no
threshold, and make no real-world truth claim. Runs are kept separate and never pooled.

Adjudication: commit `d797fbb`, reviewer `ann-1`, every judgement AI-assisted and explicitly
owner-endorsed (one reviewer; not independent or unaided review). Each adjudication row is bound
to its run's `claims_sha256`; adjudicated IDs equal the needs-human IDs in every run.

| | Run 002 (23 responses) | Run 003 (2 responses) | Run 004 (15 responses) |
|---|---|---|---|
| Claims (human-adjudicated / mechanical) | 215 (42 / 173) | 27 (3 / 24) | 286 (40 / 246) |
| Supported / contradicted / unverifiable-asserted | 211 / 3 / 1 | 27 / 0 / 0 | 286 / 0 / 0 |
| **Unsupported claims** (contradicted + unverifiable-asserted) / decided claims | **4 / 215** | **0 / 27** | **0 / 286** |
| Misleading flag reviewed (human-adjudicated claims only) | 42 of 215 | 3 of 27 | 40 of 286 |
| Misleading flag not reviewed (mechanical claims) | 173 of 215 | 24 of 27 | 246 of 286 |
| Misleading yes / no / unsure (of reviewed) | 3 / 38 / 1 | 0 / 3 / 0 | 0 / 40 / 0 |
| Unsupported-or-misleading, **observed, incomplete coverage** | 4 of 215 claims | 0 of 27 claims | 0 of 286 claims |
| Omission findings (structured spec requirements) | 9 | 0 | 19 |
| Omission candidates: requirement / context only | — (none) | 1 / 0 | 3 / 3 |
| Candidates addressed: yes / no / N/A | — | 1 / 0 / 0 | 1 / 2 / 3 |

- **Unsupported claims (approved descriptive measurement):** run 002 4/215, run 003 0/27, run
  004 0/286. Run 002's four: `c03` and `c05` "More detail is needed before searching."
  (contradicted, misleading yes), the `c03` conflicting-price premise (contradicted, misleading
  yes), and the `u01` notice (unverifiable-asserted, misleading unsure). `c03` is the recorded
  diagnostic case and `c05` the contract-conformance case.
- **Misleading coverage is incomplete.** Misleading flags were reviewed for **85 claims only**
  (42 + 3 + 40, the human-adjudicated claims); **443 claims** (173 + 24 + 246, all mechanically
  decided) have no misleading judgement ("not judged"). The unsupported-or-misleading counts above
  are therefore **observed counts with incomplete coverage, not final rates**: an unreviewed claim
  is counted as neither misleading nor not misleading. The tool's
  `unsupported_or_misleading_union` field divides by all claims; that denominator is not a
  reviewed population.
- **Omission candidates** come from `candidate-adjudications-ann-1.uc-2.jsonl` (bound to each
  run's `omission-candidates.jsonl` SHA-256 and exact spans). Unaddressed requirements: `f04`
  "shut out my screaming kids while I work from home" and `f06` "80 year old grandmother" (run
  004). `funeral` (`f05`, `m06`, `l04`) was judged context only.
- Mechanical verdicts mean consistency with the frozen **synthetic** catalog or the response
  itself. Seller-title support (wireless, ANC, gender, colour) is not server verification.

**Reproducible per-run summaries.** Saved as
`evals/results/m17/unsupported-claims-uc-2/abe-v1-live-00{2,3,4}/claims-summary.adjudicated-ann-1.uc-2.json`,
produced by the existing CLI with no code change (output first written to a scratch directory,
because the CLI refuses a non-empty output directory):

```
uv run python -m evals.assistant claims --run-dir evals/results/m17/abe-v1-live-<r> \
  --cases evals/assistant/drafts/abe-v1/cases.draft.jsonl \
  --spec evals/assistant/drafts/abe-v1/spec.json --out <empty scratch dir> \
  --adjudications evals/results/m17/unsupported-claims-uc-2/abe-v1-live-<r>/adjudications-ann-1.uc-2.jsonl
```

All three runs exited 0, and the regenerated `claims.uc-2.jsonl`, `non-claims.jsonl`,
`omissions.jsonl` and `omission-candidates.jsonl` were byte-identical to the committed files.
The original unadjudicated `claims-summary.json` files are kept unchanged.

| Run | `claims_sha256` | Summary SHA-256 |
|---|---|---|
| 002 | `9316b2fc869fbb1a92b3e49e8f30675a7fbf39ea633c92ef9f4a3ec0d1505d4b` | `78b8de2e49c3ddbfb1b5fb2e8f1c4d49e982b70739b033aafe8e7b145c0d61d9` |
| 003 | `7c2811589e60ad16bc29be317ebd35d93c345425bb5eb3a4aa4e63e7a978bbd0` | `2e1f2849cc7eba8c487a34d25be33a7d7b8f0c0349826d25a6a31c22c1238e22` |
| 004 | `0e2a20bf38dba98f10f8e21367109e897124f0b0d74209a53a6fe95cd92809fb` | `7f9c33a3e63e6783913666e91fad4a4710bba705b5861250e68c881542d4955e` |

**How to read two misleading summary fields (no code change):**

- `"provisional": false` means only that an adjudication file was supplied and loaded. It does
  **not** mean the measurement is final, preregistered, complete for misleading flags, or
  independently reviewed. The original summaries say `"provisional": true` because they were
  generated without adjudications.
- `omission_candidates.unadjudicated` counts candidate rows with status `candidate` in
  `omission-candidates.jsonl` (003: 1, 004: 6). The CLI does **not** read
  `candidate-adjudications-ann-1.uc-2.jsonl`, so the field still says "unadjudicated" although
  all 7 candidates were adjudicated in `d797fbb`. Candidate judgements are reported from that
  file (table above).

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
| Faithfulness | **Measured on complete run 005** | Not misleading 37/37 (excl. contract) / 37/38 (incl.), 1 unsure; AI-assisted/owner-endorsed, one reviewer; runs 002–004 reported separately |
| Answer relevance | **Measured on complete run 005** | Fits 56/64 (8 partly); runs 002–004 reported separately and never pooled |
| Citation correctness | **Met** | Automated, every run: all recommended products' citations correct |
| Unsupported-claim rate | **Accepted as a completed descriptive measurement (owner, 2026-10-24); not a preregistered pass** | `uc-2`, adjudicated by `ann-1` (AI-assisted, owner-endorsed): unsupported 4/215 (002), 0/27 (003), 0/286 (004), never pooled; catalog/evidence support only, not real-world truth; post-hoc; run 001 unmeasurable; misleading reviewed for 85 claims only (unsupported-or-misleading: observed counts, incomplete coverage) |
| Constraint satisfaction | **Met for server-checked fields; partial otherwise** | 0 failed server-checked requirements in every run; catalog-expectation fit (gender, ANC, colour, availability) via acceptable sets and human review only |
| Clarification accuracy | **Met (with a recorded limitation)** | 4/4 in runs 001/002; `c03` unnecessary clarification reported as a diagnostic |
| Abstention accuracy | **Met as measured; failures reported** | deterministic 4/4; model cohort 1/2 (`u01` known failure) |
| Latency | **Partial** | Provider call time per run; workflow times pacing-contaminated; no API latency under load |
| Token usage | **Met** | Provider-reported per call; missing counts for failed calls listed |
| Cost | **Accepted for the current staging scope (owner, 2026-10-24)** | Sourced token-based paid-equivalent estimates per run (runs 002/003 partial: missing usage); free tier published as free; actual billing not verified |
| Prompt-injection tests | **Partial** | 6 message injections: 0/6 targets selected in runs 001 and 004; single runs, fixed evidence; run 004 same-run pair only for `m06` |
| Malicious listing-content tests | **Partial** | 6 listing injections: 0/6 targets in runs 001 and 004; same caveats; run 004 same-run pair only for `l04` |
| False-refusal tests | **Partial** | 0 refusals in every run, but `f01`, `f03` ended as generation failures (timeouts) in runs 002/003 |
| Report failures honestly | **Met** | Incomplete runs, 3 provider failures (2 timeouts, origin unknown; 1 invalid_response), known limitations and all exclusions reported |
| §5 measured provider selection (moved to M17) | **Accepted, scope-limited (owner, 2026-10-24)** | ADR-009 M17 measured suitability assessment accepted only for retaining Gemini in the existing local/country-gated staging scope; no production, paid-use or comparative-superiority approval |
| Frozen human thresholds (manifest) | **Evaluated on run 005: all three met (owner acceptance decision pending)** | Faithfulness 97.4% (stricter view), answer relevance 87.5%, outcome appropriate 87.2% (stricter view); unchanged thresholds, both scored views shown, `c03` excluded, `c05` separate |

## Acceptance gaps, checked against the exact rules

Sources: `docs/MASTER_PLAN.md` (M17 section, §5 generation models, evaluation metrics, annotation
rules, milestone table) and the frozen, owner-approved `freeze-manifest.json` evaluation rules.
No gate is added here and no approved threshold is waived.

### Owner decisions recorded 2026-10-24 (scope-limited; no gate waived)

1. **Cost (MASTER_PLAN M17 "Measure: … cost").** The sourced token-based paid-equivalent
   estimates (see Cost) are **accepted as the cost-estimation measurement for the current staging
   scope**. Actual billing and the missing usage of the failed calls in runs 002/003 remain
   unverified; no certainty about the price in force at run time is claimed. **Accepted
   (scope-limited).**
2. **Unsupported-claim rate (MASTER_PLAN M17 "Measure: … unsupported-claim rate").** `uc-2` is
   **accepted as a completed descriptive unsupported-claim measurement**: 4/215 (002), 0/27
   (003), 0/286 (004), runs separate. It is **not a preregistered quality pass**. Caveats kept:
   post-hoc metric and boundary amendment; synthetic catalog (catalog/evidence support, not
   real-world truth); one reviewer, AI-assisted and owner-endorsed; misleading flags reviewed for
   85 claims only (443 not reviewed). **Accepted (descriptive).**
3. **Measured provider selection (MASTER_PLAN §5).** ADR-009's M17 measured suitability
   assessment is **accepted only for retaining Gemini `gemini-3.5-flash-lite` (free tier) in the
   existing local-development and country-gated staging scope**. No production, paid-use or
   comparative-superiority approval. **Accepted (scope-limited).**

### Frozen human-threshold gate: evaluated

4. **Human thresholds (frozen rule).** Manifest: "overall_quality_verdict: None before human
   review is complete" and human thresholds "faithfulness_human (not misleading) at least 95% of
   responses judged yes or no; answer_relevance_human (fits) at least 85% of judged products;
   outcome_appropriate_human at least 85% of responses judged yes or no", set for the complete
   40-case run. The required population now exists: run 005 is complete (40/40) and all 40 saved
   responses are judged. Under unchanged thresholds, with the stricter of the two scored views,
   **all three are met** (97.4%, 87.5%, 87.2%; see "Frozen human thresholds: run 005"). Run 005's
   hard gates and quality targets are also met. Runs were not pooled and no threshold or
   population was changed.

**Owner decision (2026-10-24): M17 ACCEPTED for the documented development and country-gated
staging evaluation scope**, based on evidence commit `d2d1e97` and run 005. Both contract-case
population views met the thresholds (excluding `c05`: 100.0%, 87.5%, 89.5%; including `c05`:
97.4%, 87.5%, 87.2%). Limitations kept: synthetic catalog; dataset reused from runs 001–004 (not held out); one reviewer (`ann-1`), AI-assisted and owner-endorsed, not independent; single-run evidence; known failures (`u01`, `c03`, `c05`, use-suitability `g06`/`f04`/`f06`); no production or general injection-resistance claim. Not a production-quality or
real-world claim. M18 is not started until separately instructed.

### Met, with evidence limitations

- "Create a separate, versioned, human-reviewed answer-evaluation dataset. It is not the M9 Golden
  Dataset." — met: `abe-v1`, 40 cases, owner-reviewed. Limitations: AI-authored cases; synthetic
  catalog.
- Milestone table "Real human reviewers"; annotation rules "If only one annotator exists, reports
  say so and do not claim inter-annotator agreement. AI is never an annotator, and AI suggestions
  become labels only after explicit human review." — met as worded: `ann-1` explicitly reviewed
  and endorsed every judgement; one reviewer, stated; 32 of 40 judgements AI-assisted and
  owner-endorsed, 8 provenance not stated. Not independent validation.
- "Measure: faithfulness; answer relevance" — measured by human judgements on complete run 005
  (40/40), and separately per run for 002, 003 and 004 (not pooled).
- "citation correctness", "constraint satisfaction" — met: hard gates met in complete runs 001
  and 005 and in every partial run (server-checked fields only; catalog expectations via acceptable sets).
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
