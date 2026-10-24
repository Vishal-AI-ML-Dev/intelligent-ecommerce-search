# ADR-009: Optional generation provider for the shopping assistant (Gemini Developer API)

## Status

**Accepted for local development and the existing country-gated staging demo, Gemini API free
tier only (owner decision, scoped amendment, 2026-10-23).** Not a production-provider selection.
Default off everywhere unless explicitly enabled.

- **Scope:** local development, and the single-EC2 staging demo
  (<https://intelligent-ecommerce-search.duckdns.org/>) with these controls kept in force:
  - generation runs only for visitors in eligible countries: Gemini's available regions minus
    the paid-only EEA, Switzerland and UK, plus conservative extra exclusions. This uses the
    offline DB-IP Lite data; restricted or unknown regions get the fallback;
  - messages that look like they contain personal data are never sent;
  - the provider privacy and use notice is shown, adults only;
  - usage caps of 2 in flight, 10 per minute and 200 per day; SDK retries off; at most one
    repair; bounded output and timeouts;
  - the kill switch `ASSISTANT_GENERATION_PROVIDER=none`;
  - the key exists only in the server's root-only `.env`.
  IP geolocation is best-effort; these controls lower the risk but do not guarantee compliance.
- **Observed evidence (limited smoke evidence only, not systematic quality or production
  validation):**
  - three local trials on synthetic data (7 successful requests, 0.9–1.5 s each);
  - on the staging demo, 3 online answers with one call each, validation passed, 860–1043
    input and 71–186 output tokens, 1.14–1.37 s generation latency;
  - the asst-policy-3 structured-output schema is accepted by the live API.
- **Sequencing amendment, not a waiver:** the master plan (§5) requires the provider to be
  selected from measured quality, latency and cost. The owner moved that measurement to M17.
  **M17 measured suitability (owner, 2026-10-24):** the assessment below ("M17 measured
  suitability assessment") is accepted **only for retaining Gemini in this existing scope**. It
  is not a production, paid-use or comparative selection, and it does not accept M17.
- **Not approved:** paid billing, paid fallback, other providers or production use.

History: Proposed 2026-10-22 (mocked tests only); accepted for local development only on
2026-10-23; amended the same day to add the country-gated staging demo; M17 measured
suitability assessment accepted for retaining this scope on 2026-10-24.

## Date

2026-10-22

## Context

M16 needs bounded generation that can only select retrieved catalog products, name the catalog
fields that support each selection, and write a short summary (`docs/MASTER_PLAN.md` §17, §18).
The core search engine must not depend on any generation provider (§5).

The existing Jev integration (ADR-005, `decision/jev_provider.py`) asks TypeSafe fixed
multiple-choice "Choice" questions and returns label probabilities. It has no free-text or
structured-answer generation path, so it is not a candidate.

Provider history in this milestone, recorded for transparency:

- The M16 plan first proposed Anthropic Claude. On 2026-10-22 the owner allowed Claude Haiku 5.5
  as the proposed optional provider, and an Anthropic adapter with mocked tests was drafted.
- Later the same day, before any commit or live call, the owner replaced that direction with
  the Gemini Developer API free tier. The Anthropic adapter and its SDK were removed from the
  working tree and never committed. No Anthropic key was set up or used.

## Decision (accepted for local development and the country-gated staging demo)

| Concern | Decision |
|---|---|
| Provider | Gemini Developer API, **free tier only**: no paid billing, no paid fallback |
| Model | `gemini-3.5-flash-lite`: listed as stable and with a free tier on the official pages read on 2026-10-22. Not selected by measurement. |
| SDK | `google-genai==2.32.0` (exact pin), imported only by `generation/gemini_provider.py`, which is imported only when `ASSISTANT_GENERATION_PROVIDER=gemini` |
| Default | `ASSISTANT_GENERATION_PROVIDER=none` everywhere, including the staging demo |
| Credentials | `GEMINI_API_KEY`, only in a developer's git-ignored `.env`; passed explicitly to the client, so `GOOGLE_API_KEY` and ambient Google credentials are not read. Never requested in chat. None exists yet. |
| Request | `models.generate_content` with `system_instruction`, `response_mime_type="application/json"`, `response_json_schema` (a hand-written schema using basic keywords only; outcome, product IDs and cited field names, no free text since asst-policy-3), `max_output_tokens` (default 2048) |
| Bounds | SDK retries explicitly off (`HttpRetryOptions(attempts=1)`; with no retry options the SDK default is also a single attempt); per-request timeout of 20 s; at most two calls per request (one repair); no repair after a 30 s deadline. The per-request timeout does not bound total wall-clock time. |
| Validation | Every output is parsed into the Pydantic `GroundedDraft` and checked deterministically (`assistant/validate.py`). Failures are repaired once, then the assistant abstains. Provider failures return `generation_unavailable`, never an answer. |
| Cost reporting | Token counts as reported (thinking tokens counted as output); unknown counts stay null. A USD estimate is computed only from owner-supplied prices; otherwise the cost is reported as unknown. |

## Facts read from official documentation (2026-10-22; not verified by a call)

- Pricing page (`ai.google.dev/gemini-api/docs/pricing`, last updated 2026-10-19): the free
  tier lists `gemini-3.5-flash-lite` among other models. For free-tier models, **content is used
  to improve Google's products.**
- Rate-limit page (`ai.google.dev/gemini-api/docs/rate-limits`, last updated 2026-10-15): limits
  are per project, depend on the tier, are shown in Google AI Studio, and the daily quota resets
  at midnight Pacific time. The page lists no free-tier per-model numbers.
- Structured-output page: JSON Schema subset; `minLength`/`maxLength` and similar keywords are
  stripped, not enforced; `minItems`/`maxItems` are enforced. The page's example uses the
  newer `client.interactions.create` API. This adapter uses `generate_content` with
  `response_json_schema`, a field the installed SDK exposes. Whether
  `gemini-3.5-flash-lite` accepts this request shape has not been verified.
- Models page: `gemini-3.5-flash-lite` is listed as stable with no deprecation date.

## Local live smoke trial result (2026-10-22)

Owner-approved, at most 5 requests, synthetic data. Model access was confirmed by one metadata
request. 2 generation requests were sent: the first returned a draft that parsed into the
structured-output schema but failed deterministic validation; the repair request got a provider
5xx (`server_error`), and the trial stopped. Free-tier status rests on the owner's AI Studio
check; the API does not expose billing. Details: `docs/m16-progress.md`. The ADR stayed
Proposed at that point: one parsed response is not a measurement of quality, latency or cost.

A second approved trial (2026-10-23) sent 2 requests; both parsed and passed validation without
repair (594/99 and 476/93 tokens, 1.5 s and 1.0 s). It exposed a validator gap (spelled-out
numbers pass the summary checks). Two samples are not a measurement.

Since policy asst-policy-3 (2026-10-23) the provider returns no text: only outcome, product IDs
and cited field names (`response_json_schema` without `summary`). Explanations are rendered from
catalog data. A third approved trial (2026-10-23, 3 requests) showed the live API accepts this schema; all three drafts passed validation. The model selected an injection-titled synthetic record once (rendered text stayed clean). 

## Free-tier terms and public use (read 2026-10-23)

The Gemini API Additional Terms allow only Paid Services for API clients offered to users in the
EEA, Switzerland or the UK. They forbid submitting personal or sensitive data to unpaid services,
which Google may use and have humans review. They require adult users. An open public demo with
generation on and no restrictions therefore does not comply on the free tier.

Owner decisions (2026-10-23):
1. Accepted as technically compatible for local development only, with quality, latency and
   cost selection deferred to M17.
2. Later the same day, amended to include the staging demo with generation on, under the country
   gate, the personal-data screen, the provider notice and the usage limits (see Status). The
   demo does not offer generation to EEA, Swiss or UK visitors or to unknown regions. These
   controls are best-effort and are not a compliance guarantee.

## Required before any live trial (separate owner approval)

1. Confirm in Google AI Studio that the project is on the free tier with no billing account
   attached, and record the shown rate limits for the model.
2. Re-read the Gemini API terms. Because free-tier content may be used to improve Google's
   products, send only synthetic or public catalog data and test messages, never real customer
   data.
3. Verify that the model accepts the structured-output request (one small local call, after
   approval), and record the actual token usage.
4. Keep the trial local. No staging or public deployment enables generation.

## Consequences

- With generation off, the assistant still clarifies and retrieves, and labels the results as
  ordinary search results with no generated answer.
- Free-tier data use makes this provider unsuitable for real customer conversations unless the
  owner later decides otherwise with recorded terms.
- Passing validation does not prove faithfulness or prompt-injection resistance; see
  `docs/m16-progress.md` for the limitations and M17 for evaluation.
- Changing the provider later requires only a new adapter behind `GenerationProvider` and a new
  ADR; the workflow, API and UI do not change.

## M17 measured suitability assessment (2026-10-24) — accepted, scope-limited

**Owner decision (2026-10-24): accepted only for retaining Gemini `gemini-3.5-flash-lite` (free
tier) in the existing local-development and country-gated staging scope.** No production,
paid-use or comparative-superiority approval; M17 itself is not accepted (its frozen
human-threshold gate is open). Source of every figure: `docs/m17-evidence-report.md` (frozen `abe-v1`,
synthetic catalog, `gemini-3.5-flash-lite` free tier, one attempt per call, 20 s timeout). Runs
are reported separately, never pooled. Only one provider was measured, so this is a
single-candidate suitability assessment, not a comparison between providers.

### Recorded evidence

| | Run 001 (40, complete) | Run 002 (23/40, stopped) | Run 003 (2/17, stopped) | Run 004 (15/15, complete) |
|---|---|---|---|---|
| Calls / provider failures | 29 / 0 | 12 / 2 (`f01` timeout, `g02` invalid_response) | 2 / 1 (`f03` timeout) | 15 / 0 |
| Provider call p50 / max | 2.5 s / 13.0 s | 13.8 s / 17.6 s | 1.5 s / 20.2 s (n=2) | 1.6 s / 7.0 s |
| Hard gates (prose, citations, server-checked requirements) | met | no verdict (incomplete); 0 prose, 0 bad citations, 0 failed checks | no verdict (incomplete); same | met (subset) |
| Human: outcome appropriate (scored) | not reviewable | 17/21 | 1/2 | 13/15 |
| Unsupported claims (post-hoc `uc-2`, catalog support) | not measurable | 4/215 | 0/27 | 0/286 |
| Injection targets selected | 0/12 | not run | not run | 0/12 |
| Paid-equivalent estimate (USD; billing not verified) | 0.0167945 | partial 0.0060818 | partial 0.0006326 | 0.0089916 |

Caveats: one reviewer (`ann-1`), AI-assisted and owner-endorsed judgements; no complete run is
human-reviewed, so the frozen human thresholds are not evaluated; timeout origins are unknown;
the free tier is published as free of charge and actual billing was not checked; free-tier
content may be used by Google, so only synthetic data may be sent.

### Assessment

- **Quality:** on the synthetic catalog the bounded design (IDs and cited fields only; text
  rendered from the catalog) kept 0 displayed prose, 0 invalid citations and 0 failed
  server-checked requirements in every run, and the post-hoc unsupported claims found are
  server-template notices in diagnostic/contract cases (`c03`, `c05`) and `u01`, not model prose. Use-suitability needs (`f04`, `f06`) were not addressed. This supports the
  current limited scope; it is not production-quality evidence.
- **Latency:** usually 1.5–2.5 s p50, but one run had a 13.8 s p50 and two calls reached the
  20 s timeout. Latency is not stable enough to promise a response time.
- **Cost:** zero on the free tier as published; paid-equivalent estimates are about USD 0.0006
  per successful call. Paid use is not approved.
- **Reliability (main weakness):** 3 failures in 58 calls across four runs (2 timeouts, 1 invalid
  response); two of four runs stopped early under the stop-on-timeout rule. The fallback
  (`generation_unavailable`) worked in every failure; nothing failed open.

**Conclusion (accepted as worded, 2026-10-24):** the measured evidence is
consistent with keeping Gemini `gemini-3.5-flash-lite` (free tier) as the optional generation
provider for the **existing scope only** (local development and the country-gated staging demo,
generation optional, deterministic fallback), with reliability recorded as a known limitation.
It does not support production use, paid use, or a claim that this provider is better than any
alternative. Any wider scope (production, paid tier, another provider) needs new measurement
and a new owner decision.
