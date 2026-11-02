# ADR-010: Optional typed provider comparison for return-reason classification

## Status

**Proposed, 2026-11-02. Not accepted.** No provider call has been made for return
classification. None may be made until all of these hold:
1. the owner confirms Jev's data terms;
2. the official price page is rechecked and the run fits the caps below;
3. dev and test labels are both complete;
4. the owner approves the specific run.

The deterministic baseline (`return-baseline-rules-1`) stays the default, works without any
provider, and is unaffected.

## Context

- MASTER_PLAN Milestone 19 asks for optional typed GenAI multi-label classification with
  confidence, handling Hinglish and misspellings. It must be compared with the deterministic
  baseline on a separate human-reviewed set.
- Part 3 step A (`docs/m19/progress.md`) provides:
  - `return-reason-split-1`: seed 20261102; 60 dev, 60 held-out test and 60 unlabelled returns,
    stratified by reason code;
  - a blind labelling kit and the `return-reason-labels-1` contract (the owner is the only
    annotator);
  - the `return-reason-scoring-1` harness.
- **Provider:** Jev (TypeSafe), through the M11 adapter (`decision/jev_provider.py`,
  ADR-005): typed Choice questions with per-choice probabilities. The API returns token
  counts but no cost field (`docs/jev-provider-m11.md`). One M11 smoke call used 641 input and
  142 output tokens.
- **Gemini:** the M16 adapter (`generation/gemini_provider.py`, ADR-009) is scoped to the
  shopping assistant and is out of scope for M19.

## Owner answers (2026-11-02)
1. **Jev only,** via the existing M11 adapter after contract checks. Gemini is out of scope for
   M19.
2. **Data terms:** the owner confirms Jev's data terms before any call. Until then, no calls.
3. **Price and caps:** recheck the official price page before any run. Hard cap **USD 1**, and
   a **call cap equal to the planned call count**; the run stops on either cap.
4. **Call shape:** one yes/no judgement per category per return.
5. **Labels first:** dev and test labels must both be finished before any provider run.
6. **Display:** provider outputs stay evaluation-only. Part 4 shows baseline results only.

## Proposed decision

1. **A typed, provider-isolated interface,** `ReturnJudgementProvider`, with a Jev adapter
   built on the M11 adapter. Before any call, contract checks confirm that:
   - the Choice question set is fixed and versioned;
   - every answer is exactly `yes` or `no`;
   - probabilities are well-formed;
   - cited evidence IDs come only from the supplied inputs.

   No assistant or listing prompt is reused.
2. **One yes/no judgement per category per return**, as a separate call. The judged categories
   are the ten taxonomy categories other than `suspected_abuse` and `insufficient_evidence`:
   - abuse is never asked;
   - `insufficient_evidence` is derived when every judgement is `no`;
   - Jev's per-choice probabilities are recorded as uncalibrated, never as calibrated
     confidence.
3. **Same inputs as the baseline:** the same `ClassificationInput` built under
   `complaints-by-order-item-1`, with redacted text passed as quoted data (the dataset holds
   prompt-injection and markup overlays). Same split, same scoring version.
4. **Fail closed.** If a call fails validation, times out or errors, that return's provider
   result falls back to the baseline result and is routed to review. When a cap is reached the
   whole run stops, and the partial run is marked incomplete and not scored as a comparison.
5. **Sequencing:**
   - only after both label sheets are complete;
   - dev runs first, with at most two prompt revisions, judged on dev only;
   - then exactly one held-out test run for each of the baseline and the provider, scored with
     `--final`;
   - no prompt change after the test run.
6. **Logging (per call):**
   - provider, model ID and response model;
   - question-set and prompt version with their SHA-256, input SHA-256 and request ID;
   - validated answer and probabilities, rejection reason, tokens and latency.

   Records go to a new `evals/returns/runs/` directory, outside the protected `evals/results/`
   tree, which the AC-14 protected-path check compares. Nothing goes to the M18 or 0008 tables,
   and nothing reaches Part 4 displays.
7. **Planned calls and caps:**
   - one full pass is 10 judgements × 60 returns = **600 calls** per set;
   - the maximum plan is 3 dev passes (the first plus at most two revisions) and 1 test pass,
     **2,400 calls**;
   - the call cap equals the calls planned for the approved run (at most 2,400);
   - the cost cap is **USD 1**, estimated before the run and tracked from reported tokens during
     it;
   - one call in flight, SDK retries off, bounded output; the run stops on either cap.
8. **Cost estimate (to be rechecked against the official price page before any run):**
   - **Tokens per call (estimate, not measured):** about 700 input and about 50 output (a short
     yes/no answer).
   - **At 2,400 calls:** about 1.7M input and 0.12M output tokens.
   - **No price is assumed here.** The M12 tooling computes cost from an owner-supplied price
     per million tokens, with its `--max-estimated-usd` guard set to 1.
   - **If the estimate exceeds USD 1,** fewer dev passes are planned before any call is made.

## Consequences

- If accepted, M19 gains a measured, like-for-like comparison on 60 held-out returns.
- With one annotator and 60 test returns, most per-category intervals will be wide. The
  comparison supports "no worse or better than the baseline" statements only, not a production
  choice.
- Per-category calls cost about ten times one call per return, in exchange for separate,
  independent judgements.

## Still open before acceptance
1. **Data terms:** the owner's confirmation of Jev's data terms.
2. **Price:** the official price, checked on the run date, and the resulting estimate within
   USD 1.
3. **Defect kind:** whether to add a yes/no "is there a defect of any kind" judgement.
   - With only the two separate defect-kind judgements, the provider cannot express "defect,
     kind undetermined".
   - Adding the judgement would make 11 calls per return (660 per pass, 2,640 maximum).
4. **Run approval:** the owner's approval of one specific run.
