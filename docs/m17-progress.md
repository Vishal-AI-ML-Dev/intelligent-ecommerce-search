# Milestone 17 — Buyer RAG Evaluation and Safety: progress

**Status: IN PROGRESS. Not accepted.** M16's prerequisite is met (M16 accepted for development and
the country-gated staging scope, 2026-10-23, `941f9e0`). The §5 measured provider selection
moved to M17 by sequencing amendment and is **not met**. No M17 evaluation data, live run or
human judgement exists yet.

## Step A (offline harness, mocked tests only) — 2026-10-23

Groundwork `6425af7` extended. Nothing here sends a provider request; every live-path test uses
an in-process fake.

| File | Role |
|---|---|
| `evals/assistant/contracts.py` | Case format `aq-2`: split, evidence provenance, case review, counterparts, expectations. |
| `evals/assistant/checks.py` | Deterministic checks, each labelled `structural`, `catalog_factual` or `expectation`. |
| `evals/assistant/runner.py` | Mocked runs (scripted provider) and live runs (one real provider) over frozen evidence through the real M16 workflow; request cap, minimum interval, stop on quota/access failures. |
| `evals/assistant/report.py` | Metrics with numerator, denominator and definition; clean/injected pair comparison; latency, tokens and cost reported separately. |
| `evals/assistant/judgements.py` | Human judgement format `aj-1` and its summary. |
| `evals/assistant/__main__.py` | `mocked` (default), `live` (refuses unless every gate holds; dry run without `--confirm-live`), `judgements`. |
| `evals/assistant/fixtures/synthetic-v1/cases.jsonl` | 20 `dev` cases, `synthetic_test_fixture`, including 3 clean/injected pairs. |
| `docs/m17-answer-rubric.md` | Draft human rubric. |

Mocked run: 20/20 dev cases pass. That describes harness and workflow behaviour only.

### Definitions

- **Split:** `dev` cases (synthetic fixtures, smoke-style) are never scored as M17 evidence.
  `eval` cases form the bounded M17 set; they are never `synthetic_test_fixture` and never use
  `synthetic` evidence. A case file holds one split.
- **Evidence:** `captured_v3` is the V3 result list for the message, captured once from the local
  catalog and frozen (`evidence_capture` records search version, depth and time).
  `captured_v3_injected` copies a captured set and changes only free text (title, subcategory,
  description) of named records. Expected outcomes are written per case by the author. A no-match
  case is not assumed to skip generation: V3 may return non-empty but unsuitable evidence, in
  which case the provider is called and the expected outcome is a model abstention.
- **Counterparts:** each injected case names its clean case. Message injections keep the evidence;
  listing injections keep the message and record list. The report gives, per pair, whether the
  outcome or recommendation set changed and, separately, whether an injected item was
  recommended. One run per case: a change may also come from model non-determinism.
- **Check kinds:** `structural` and `catalog_factual` checks are deterministic properties of the
  displayed response; `expectation` checks compare with the author's expectations. None is a
  faithfulness or relevance judgement; those come only from human `aj-1` judgements.
- **Authorship and review:** `authorship` never changes; an AI-authored case reviewed or revised by
  a person stays `ai_authored`, with a `case_review` entry. `ann-<n>` handles record provenance;
  they are pseudonymous and do not prove human identity.
- **Limits vs quotas:** `--max-requests` and `--min-interval-seconds` are harness limits. The app's
  usage guard (2 in flight, 10/min, 200/day) is the server's own limit. Neither is the Google
  account or model quota, which the harness does not know; a `rate_limited` result stops the run.
- **Latency:** `workflow_total_ms` (end-to-end workflow over frozen evidence; no search, no HTTP),
  `workflow_generation_ms` (the workflow's generation span, including a repair) and
  `provider_call_ms` (each provider call) are reported separately.
- **Tokens and cost:** tokens are the counts the provider reported, with missing counts listed.
  Cost is an estimate only from operator-supplied prices with a stated source; otherwise unknown.
  The report never states what was billed.

## Proposed eval set `abe-v1` (not built; Step B needs approval)

40 `eval` cases over the local seed catalog, evidence captured with the V3 retriever:

| Group | Cases | Expected | Provider calls |
|---|---|---|---|
| Grounded answer / constraint (clean) | 10 | answer, acceptable IDs | yes |
| Clarification | 6 | clarify (reason) | none (decided before retrieval) |
| Catalog cannot support | 6 | abstain (`no_matching_products` or `model_abstained`) | yes, unless evidence is empty |
| False-refusal probes (edge phrasing, Hinglish) | 6 | answer | yes |
| Message-injection counterparts of 6 clean answers | 6 | same as clean; forbidden IDs | yes |
| Listing-injection counterparts of 6 clean answers | 6 | same as clean; forbidden IDs | yes |

Upper bound without a cap: 34 generation cases × 2 calls = 68 requests. No cap is approved.

## Thresholds

MASTER_PLAN M17 names the metrics but sets no numeric thresholds; it requires failures to be
reported honestly. Any pass thresholds are owner decisions, provisional until each is tied to a
metric and denominator defined above. Existing requirements are not lowered.

## Remaining work and blockers

- **Step B (owner):** author or review the 40 cases and expectations; approve evidence capture
  (needs the local database and already-cached models; no downloads) and the case-file SHA-256.
- **Step C:** live run; needs approval of the case file, request cap and interval.
- **Step D (owner):** `aj-1` judgements per `docs/m17-answer-rubric.md`.
- **Step E:** report, provider suitability note in ADR-009 (staging scope only).
- No new dependencies were added.
