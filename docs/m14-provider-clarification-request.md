# Provider clarification request: Jev `system_one` Choice answers (DRAFT, NOT SENT)

Status: **prepared 2026-10-21, not sent.** Sending it is an external communication and needs
the owner's approval. It contains no listing content, no request id and no credential.

---

**To:** TypeSafe support. **Subject:** Choice answers: probability precision, sums and
`choice` selection (`jev-1.13.0`).

Hello,

We call `jev-1.13.0` (`system_one`, Choice questions with closed label sets) and validate each
answer before use. In 286 recorded listing decisions (557 HTTP attempts, all HTTP 200), 7
responses failed our validation. The causes we could record:
- 4: the returned `probabilities` did not sum to 1 within ±0.001;
- 1: the returned `choice` was not a label with the highest returned probability (we accept
  ties within 1e-9);
- 2: the cause was not recorded.

We don't change answers that fail validation, so we would like to understand the contract.

1. Is `choice` always the label with the highest returned probability? Or can it differ within
   numeric precision, or for another reason?
2. What precision do the returned probabilities have (for example 32-bit floats, or rounding
   before serialization)? Should they sum to 1, and within what tolerance?
3. How is `choice` selected when two or more labels share the highest probability?
4. Are the probabilities normalized before or after `choice` is selected?
5. Is `confidence` derived from the probabilities, and if so how?

Thank you.

---

**Facts behind the numbers** (from `docs/m14-progress.md`):
- Combined dev outcomes: 286 records from `dev-full-1` and `dev-complete-1`, 7
  `invalid_response`.
- The causes come from allowlisted rejection codes: 4 `probability_sum`, 1 `choice_not_top`, 2
  `not_recorded` (outcomes from before the codes existed).
- Attempts per run journal: `dev-full-1` 544, `dev-complete-1` 13; every attempt result is
  HTTP 200.
- Validation constants: `PROBABILITY_SUM_TOLERANCE = 1e-3` and `TOP_TIE_TOLERANCE = 1e-9`. No
  tolerance, threshold or normalization was changed in response to these failures.
