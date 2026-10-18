# ADR-005: Jev as an optional bounded-decision provider

## Status

Accepted (architectural boundary).

Milestone 11 is **complete for provider implementation and verification scope; live success-path
smoke-verified, not production-validated** (owner decision, 2026-10-18). The implementation is off
by default and not wired into any endpoint. Credentials now exist: paid access, with the key only
in the developer's git-ignored `.env` (until 2026-10-18 there was only waitlist access and no
key). Live
connectivity is smoke-verified with one request on 2026-10-18. Dependencies are reviewed and
accepted (2026-10-18), and planned local regression checks passed. Apart from that one successful
response, the API contract is documented from official sources only. See
"Implementation status (Milestone 11)" below and `docs/jev-provider-m11.md`.

## Date

2026-09-30

## Context

Jev access is reported as available. `docs/MASTER_PLAN.md` describes Jev as a
probability-backed bounded-decision layer. The core search engine must work
without Jev or any other remote model, and model decisions must not become
hard filters or final judgments without confidence gating and human review.

This ADR records only the role and boundary defined in the master plan. It
does not document Jev API endpoints, model names, pricing, the meaning of
Jev's returned probabilities, or implementation details. Those are unverified.
API verification is deferred to Milestone 11, and evaluation of the
probabilities' meaning and calibration to Milestone 12.

## Decision

Jev may be integrated only as an optional `JevDecisionProvider` behind the
`DecisionProvider` interface (ADR-003), and only after its prerequisites are
met.

### Milestone order

- **Milestone 11** implements the optional provider: typed client, timeout,
  limited retry, safe fallback, configuration and telemetry.
- **Milestone 12** evaluates it against human-reviewed decision data.
- Jev is enabled in the default/V4 configuration **only if** Milestone 12
  demonstrates a measured benefit and acceptable safety, latency and cost.
  Otherwise the default stays deterministic and Jev remains an optional,
  disabled provider.

### Prerequisites (Milestone 11)

- Stable V0–V3.
- A human-reviewed Golden Dataset.
- Credentials available through environment variables.
- Official API behavior verified.

### Permitted bounded decisions

- Query category
- Semantic intent
- Retrieval strategy
- Ambiguity
- Out-of-catalog detection
- Listing-category validation
- Attribute-candidate validation
- Policy-risk triage
- Human-review routing

### Prohibited uses

Jev must not be used to:

- invent attributes;
- replace reliable deterministic numeric parsing;
- make final legal conclusions;
- automatically ban sellers or reject listings;
- create evaluation or review labels;
- replace the reranker without a controlled experiment.

### Boundary rules

- Jev outputs are validated through Pydantic into bounded, closed-set
  decisions. Anything else is discarded, with a recorded fallback reason.
- Every Jev decision passes the confidence gate.
- Hard-filter rule: before Milestone 12 validates a confidence threshold from
  human-reviewed data, Jev decisions may influence ranking or retrieval
  routing but never hard filtering. Low-confidence decisions never become
  hard filters at any time.
- **Raw probability is not gate confidence.** A Jev raw probability is
  provider output. It must not be used as gate confidence until its meaning
  and calibration are verified in Milestone 12 (see ADR-003). Raw
  probabilities and gate-confidence values are logged separately.
- Any failure falls back to the deterministic provider. The search result is
  still returned.
- Configurable: endpoint, model, timeout, retry, enablement and thresholds.
  Nothing is hard-coded.
- Credentials only in environment variables. Secrets are never logged.
- Log: provider, pinned model version, latency, raw provider probabilities and
  gate-confidence values (as separate fields), fallback reason and returned
  cost where available.
- Seller and query text sent to Jev is treated as untrusted data.
- Routine CI never requires live Jev access.

## Implementation status (Milestone 11)

Documented contract (official TypeSafe documentation, read 2026-10-17; only the success path was
observed live, in one smoke request on 2026-10-18;
sources in `docs/jev-provider-m11.md` §7): Jev is TypeSafe's model, called through
`POST https://api.typesafe.ai/v1/systemone` with a Bearer key; typed **Choice** questions return
`choice`, `probabilities` (summing to 1) and a TypeSafe-computed `confidence`; versioned model ids
(`jev-1.13.0`) can be pinned; pricing is $0.042 per million input tokens (output free); rate
limits are per organisation and not published.

Mocked implementation in `src/ecommerce_search/decision/jev_provider.py` with
`typesafe-sdk==0.7.5`:

- Bounded decisions: query category and semantic intent only, over the existing `Category` and
  `Intent` label sets plus an explicit `none` option. The deterministic parse is returned
  unchanged; Jev is never asked for numeric constraints.
- Validated outputs: the whole response is rejected (`invalid_response`) unless every label,
  probability and confidence is in range and complete. Equal top probabilities are kept as
  `top_labels` and propose no label.
- Raw provider probabilities and the provider's `confidence` are stored exactly as returned, as
  separate fields. No gate confidence exists until Milestone 12.
- Configuration errors fail (settings validation, `JevConfigurationError`); runtime failures fall
  back to the deterministic result with a fixed reason. The model must be a versioned id.
- The SDK `RetryPolicy` is the only retry layer. Its budget is **not** a wall-clock deadline
  (in-flight attempts are not interrupted; the attempt timeout is per httpx2 operation). Bounds
  are documented in `docs/jev-provider-m11.md` §5. FR-DEC-3's "late outputs fall back" is met
  with mocks by a per-call deadline (§5.1). One managed event loop and async client per provider,
  a bounded caller wait plus scheduling tolerance (not hard real time), cancellation that stops
  client-side retries (it cannot guarantee that server processing or billing stops), late results
  discarded, bounded in-flight calls (slots taken before a call is queued) with fail-fast
  `saturated`, and an idempotent, time-bounded `close()`. The loop thread owns cleanup and
  finishes it once finite obstructions end; code that never returns is reclaimed only at process
  exit. All verified with mocks only.
- Disabled mode is not a fallback: it is the deterministic provider (`provider="deterministic"`,
  `jev=None`), so no fallback reason is recorded.
- Off by default; `typesafe_sdk` is imported only when an enabled provider is built; no endpoint
  uses it; Compose forwards no Jev setting.

Live smoke check (2026-10-18, one request; `docs/jev-provider-m11.md` §7.1): HTTP 200, returned
model `jev-1.13.0`, validated response, category `laptop`, intent `gaming`, 627.3 ms, 641 input /
142 output tokens, no cost field.

Still open:
- answer quality, latency and token distributions, and Hinglish behaviour beyond that one request
  (need approved live calls);
- live failure, retry and cancellation behaviour;
- the error-body schema, organisation rate limits, and data-retention and billing terms;
- everything assigned to Milestone 12.
The structured Settings-error disclosure risk (shown with fake inputs only) is fixed by masking
secret inputs at every settings source (`docs/jev-provider-m11.md` §6).

## Alternatives considered

- **Jev in the core path as a required dependency:** violates offline
  operation and the fallback requirements. Rejected.
- **No model-based decisions at all:** remains the default behavior. Jev is
  enabled by default only if the Milestone 12 experiment shows a measured
  benefit.

## Consequences

### Positive

- The core stays fully functional and testable without Jev.
- Jev's value is decided by measurement, not assumed.

### Negative

- Jev's value must be demonstrated in Milestone 12 against deterministic,
  optional LLM and gated-Jev baselines, using human-reviewed decision data,
  calibration, coverage, latency and cost. This is extra work that may
  conclude Jev is not worth enabling.
- Until Milestone 11, all Jev-specific behavior is an open question. Until
  Milestone 12, probabilities cannot be used as gate confidence.

## Revisit when

- Milestone 11 verification of the official API contradicts an assumption in
  this ADR.
- Milestone 12 shows no measured benefit, or unacceptable safety, latency or
  cost.

## Open questions

Milestone 11 (API verification), documented from official sources, with only the success path
observed live (one smoke request, 2026-10-18): API contract,
authentication, error statuses, models and version pinning, published pricing (see
"Implementation status (Milestone 11)"). Still unknown: the error-body schema, organisation rate
limits, data-retention terms, and any behaviour that only a live call can show.

Deferred to Milestone 12 (evaluation):

- Semantics and calibration of returned probabilities.

## References

- `docs/MASTER_PLAN.md`: role and boundaries.
- Official TypeSafe documentation, read 2026-10-17: https://docs.typesafe.ai/api,
  https://docs.typesafe.ai/primitives/choice, https://docs.typesafe.ai/confidence,
  https://docs.typesafe.ai/models and the Python SDK reference under
  https://docs.typesafe.ai/sdk/python. Full table in `docs/jev-provider-m11.md` §7.
