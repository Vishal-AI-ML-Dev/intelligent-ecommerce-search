# ADR-009: Optional generation provider for the shopping assistant (Gemini Developer API)

## Status

**Proposed (owner direction, 2026-10-22). Unmeasured. Default off.** The adapter exists behind a
typed interface and is tested only against fake clients. No live call has been made. No
quality, latency, cost, structured-output or free-tier-limit claim is made here. The master
plan (§5) requires the generation-provider ADR to rest on measured quality, latency and cost.
Those measurements belong to a separately approved local live trial and to M17. Until then this
ADR is not Accepted.

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

## Decision (proposed)

| Concern | Decision |
|---|---|
| Provider | Gemini Developer API, **free tier only**: no paid billing, no paid fallback |
| Model | `gemini-3.5-flash-lite`: listed as stable and with a free tier on the official pages read on 2026-10-22. Proposed, not selected by measurement. |
| SDK | `google-genai==2.32.0` (exact pin), imported only by `generation/gemini_provider.py`, which is imported only when `ASSISTANT_GENERATION_PROVIDER=gemini` |
| Default | `ASSISTANT_GENERATION_PROVIDER=none` everywhere, including the staging demo |
| Credentials | `GEMINI_API_KEY`, only in a developer's git-ignored `.env`; passed explicitly to the client, so `GOOGLE_API_KEY` and ambient Google credentials are not read. Never requested in chat. None exists yet. |
| Request | `models.generate_content` with `system_instruction`, `response_mime_type="application/json"`, `response_json_schema` (a hand-written schema using basic keywords only), `max_output_tokens` (default 2048) |
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
check; the API does not expose billing. Details: `docs/m16-progress.md`. The ADR stays
**Proposed**: one parsed response is not a measurement of quality, latency or cost.

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
