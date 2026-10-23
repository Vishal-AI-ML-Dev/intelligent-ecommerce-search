# Milestone 17 — Buyer RAG Evaluation and Safety: progress

**Status: IN PROGRESS, preparatory groundwork only. Not accepted.**

M17's prerequisite (M16 complete) is **not met**: M16 is in progress (see `docs/m16-progress.md`)
and is not waived by this work. Nothing here is the M17 human-reviewed answer set or a
measurement of a live model.

## What exists (2026-10-23)

A mocked evaluation harness, `evals/assistant/`:

| File | Role |
|---|---|
| `contracts.py` | Case format `aq-1` (JSONL): message, fixed catalog evidence, up to two scripted provider results, hand-written expectations, tags, authorship. |
| `checks.py` | Deterministic checks of an `AssistantResponse` against the case. |
| `runner.py` | Runs each case through the real M16 `workflow.answer` with a fixed retriever and a scripted provider. No network; no live mode. |
| `report.py`, `__main__.py` | JSON and Markdown report: per-check and per-tag pass counts, every failure listed. |
| `fixtures/synthetic-v1/cases.jsonl` | 17 synthetic cases (`synthetic_test_fixture`). |

Run: `uv run python -m evals.assistant [--cases PATH] [--out DIR]`.
Tests: `tests/unit/assistant_eval/test_assistant_eval_harness.py`.

### Checks

`outcome` (and clarify/abstain reason), `recommended_ids`, `citation_ids` (recommended IDs were
retrieved), `citation_values` (each cited value equals the raw catalog value), `forbidden_not_recommended`,
`evidence_retained` and `evidence_unaltered` (search results stay visible and unchanged, separately
from recommendations), `constraint_statuses` (expected passed/failed/not_verified; `all_verified`
only when every requirement passed), `no_seller_text_in_prose` (seller title, subcategory or
description, or the shopper message, in server-written text), `bounded_calls`,
`versions_recorded`, `script_consumed`.

The checks read the response, the hand-written expectations and the raw case records only; they
never call the assistant's validation or rendering code. Mutation tests corrupt good responses by
hand (forbidden product recommended, wrong cited value, hidden or altered evidence, unverified
requirement reported as passed, injected or seller text in an explanation or summary, an echoed
message, extra generation calls, missing versions) and require the named check to fail.

### Synthetic cases (tags)

Clarification 4, grounded answer 5, constraint 6, abstention 3, prompt injection 3 (shopper
message and listing text), malicious listing 3, false refusal 2, degradation 2 (generation off,
provider error). Result of the mocked run: 17/17 cases pass.

## What the mocked results mean

They describe harness and workflow behaviour on scripted provider output. They are **not**
evidence of live-model faithfulness, answer relevance, citation quality, injection resistance,
false-refusal rate, latency, token usage or cost. Latency, tokens and cost are reported as
`not_measured_mocked`.

## Authorship and human review

`authorship` (`synthetic_test_fixture`, `ai_authored`, `human_authored`) is separate from
`review_status`, which `aq-1` fixes to `not_reviewed`. `ann-<n>` handles are reserved for
human-authored cases. AI-authored cases never become human-reviewed automatically; human answer
judgements will be a separate, versioned file recorded by a named human reviewer.

## Remaining M17 work and blockers

- **Prerequisite:** M16 completion (owner decision).
- **Human-reviewed answer set:** needs real human reviewers, a versioned rubric and a judgement
  file format (faithfulness, answer relevance, clarification and abstention judgements). Not
  started; AI is never an annotator.
- **Live measurements** (latency, tokens, cost, live robustness): need the Gemini key and owner
  approval for live calls. No live mode exists in the harness.
- No new dependencies were added.
