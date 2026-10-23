# M17 answer-judgement rubric (`aj-1`, draft)

**Status: draft for owner review. No judgements have been made.**

This is the human task for the M17 buyer-assistant evaluation. Judgements are written by a
reviewer into a JSONL file (format `aj-1`, `evals/assistant/judgements.py`). AI is never a
reviewer, and no code writes judgements. The `reviewer_handle` (`ann-<n>`) records provenance
only: it is pseudonymous and does not prove human identity. With one reviewer, reports claim no
inter-annotator agreement.

## What the reviewer sees

For each case of one recorded run (`report.json`, bound by its SHA-256): the shopper message,
the displayed evidence (retrieved catalog records), the outcome (answer, clarify, abstain,
generation unavailable), the notice, the summary, and each recommended product with its cited
catalog facts, requirement checks and explanation.

## What the reviewer records (one line per case)

| Field | Values | Question |
|---|---|---|
| `outcome_appropriate` | yes / no / unsure | Given the message and the evidence shown, was answering, clarifying or abstaining the right thing to do? |
| `products[].fit` | fits / partly_fits / does_not_fit / unsure | For each recommended product: does it fit what the shopper asked for, judged from the catalog record? |
| `misleading` | yes / no / unsure | Would the displayed text (notice, summary, explanations) mislead a shopper about the products or the outcome? |
| `notes` | free text, at most 500 characters | Why, for any `no`, `does_not_fit`, `yes` (misleading) or `unsure`. |

`products` must list exactly the products the run recommended for that case (none for a
non-answer). `unsure` is allowed and is excluded from denominators and counted.

## How the verdicts are used

- **Answer relevance (human):** `fits` / (`fits` + `partly_fits` + `does_not_fit`).
- **Faithfulness (human):** responses judged not misleading / responses judged yes or no.
- **Outcome appropriateness (human):** `yes` / (`yes` + `no`).

The deterministic report metrics (citations, catalog values, requirement checks, server-written
text) are structural or catalog-factual. They are not faithfulness judgements, and the human
verdicts above are not inferred from them.

## Estimated effort

About 2 minutes per response; about 35 responses for the proposed set, so about 1–1.5 hours,
plus case authoring or review (see `docs/m17-progress.md`).
