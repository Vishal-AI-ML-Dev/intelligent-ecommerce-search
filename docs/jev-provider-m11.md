# Milestone 11 — Jev decision provider

## Status

**M11 is not complete.**

| Item | Status |
|---|---|
| Mocked implementation (provider, validation, fallback, settings, telemetry) | Ready for review. Mocked tests pass (Jev file: 93 tests). |
| API contract | **Documented only**, from official TypeSafe docs (section 7). No behaviour has been **observed live**. |
| Credentials | **None.** TypeSafe account access was granted from the waitlist, but no API key has been created (dashboard: "No API keys yet") and paid access has not been purchased. The M11 prerequisite "credentials" is **unmet**. |
| Live connectivity | **Unverified.** Jev requires paid access. No live call, billing activation, credit purchase or automatic top-up has been made or configured; any live call needs separate explicit approval. |
| Local regression validation | **Passed** (planned local checks; 4 OS-related unit skips). This is not live Jev verification and does not prove the absence of defects. **Final full unit suite (2026-10-18, after the timeout change): passed.** 3,743 collected; 3,739 passed; 0 failed; 0 errors; 4 skipped, all because this OS account cannot create symbolic links. All 93 Jev tests passed. The isolation wrapper (`tests/unit/test_unit_isolation.py`) passed with its 1,200 s child limit. All 233 recorded source, config, test and doc hashes were unchanged during the run. *Earlier run, kept as history:* the first full run that day had 3,738 passed, **1 failed**, 4 skipped. The failure was the then-600 s child limit; run directly with the same clean environment, that child suite passed (693.57 s), and the limit was raised to 1,200 s with approval. Offline real-model tests: 14 passed. Ruff: clean. **Database integration tests (2026-10-18): passed.** 721 passed; 0 failed, errors or skips; exit code 0. This includes 4 database + real-model tests using cached models offline. They ran against a dedicated `ecommerce_search_itest` database (never the development database); it and every scratch database were cleaned up, and the database container was returned to its prior stopped state. All 233 recorded file hashes were unchanged during the run. |
| FR-DEC-3 deadline | **Implemented with mocks** (section 5.1): a bounded caller wait plus scheduling tolerance, not hard real time. Cancellation stops client-side retries but cannot guarantee that server processing or billing stops. |
| Concurrency and cleanup | **Implemented with mocks** (section 5.1). Calls take a slot before they are queued, so queued plus running calls never exceed `JEV_MAX_IN_FLIGHT`, even while the loop is blocked. Every slot is released exactly once. `close()` waits up to the configured close-wait cap (currently 5 s), plus scheduling tolerance; the loop thread finishes cleanup after finite blocking or cancellation-resistant work. **Limits:** code that never returns keeps the thread, loop and client until process exit; if closing the client fails, the failure is logged (by error type only) and the loop still closes, but that client is left for garbage collection. |
| Structured-error disclosure risk | **Fixed** (section 6): secret inputs are masked at every settings source; tested with fake values only. |

Off by default and not wired into any endpoint. No gate, calibration, threshold or V4 exists
(Milestone 12).

Code: `src/ecommerce_search/decision/jev_provider.py` (the only module importing `typesafe_sdk`,
lazily), result models in `src/ecommerce_search/decision/provider.py`, settings in
`src/ecommerce_search/config/settings.py`. Tests: `tests/unit/search/test_jev_provider.py`,
`tests/unit/test_settings.py`, `tests/unit/search/test_decision_provider.py`,
`tests/unit/test_compose_config.py`.

## 1. What it does

One `system_one` call per query asks Jev two typed **Choice** questions about the normalized query
text (the state; untrusted data):

| Question id | Labels (closed set, fixed order) |
|---|---|
| `category` | `laptop`, `phone`, `shoes`, `headphones` (the existing `Category` enum), `none` |
| `semantic_intent` | `gaming`, `coding`, `student`, `lightweight`, `premium`, `comfort`, `travel`, `office` (the existing `Intent` enum), `none` |

The question set is versioned `jq-1` (`DecisionResult.provider_version`). The deterministic parse
is returned **unchanged** as `DecisionResult.understanding` (the same object). Jev is never asked
for, and never supplies, brand, RAM, storage, storage type or price.

## 2. What is stored (`DecisionResult.jev`, a `JevOutcome`)

- `model` (configured pin), `response_model` (the `model` the API reports), `latency_ms` (whole
  SDK call, including retries), `request_id`, `input_tokens`, `output_tokens`, `fallback_reason`.
- `category` / `semantic_intent` proposals, each with:
  - `raw_choice`, `provider_probabilities` (every label, **exactly as returned**),
    `provider_confidence` (TypeSafe's own `confidence`, **exactly as returned**);
  - `top_labels`: every label whose probability is within `1e-9` (provisional) of the maximum
    (the maximum is **not** assumed unique);
  - `label`: the `Category`/`Intent` only when the top label is unique and is not `none`;
    otherwise `None`.
- There is **no gate confidence** field. Raw probabilities, provider confidence and a future
  calibrated gate confidence (Milestone 12) are three separate things; nothing derives one from
  another here.
- No cost field: the API returns token counts only. Cost is computed later from `input_tokens`
  and the dated published price (section 7).

Invariant (validated): either both proposals are present and `fallback_reason` is `None`, or no
proposal is present and `fallback_reason` says why.

## 3. Response validation (all-or-nothing)

The response is rejected as a whole (`invalid_response`, no proposals) unless:

- the answers are exactly `category` and `semantic_intent`, both of type `choice`;
- each `choice` is in its label set;
- the `probabilities` keys equal the label set exactly; every value is a finite number (not a
  bool) in `[0, 1]`; they sum to 1 within `1e-3`;
- `confidence` is a finite number in `[0, 1]`;
- `choice` is one of the top labels (ties allowed; a non-top `choice` is rejected);
- token counts, when present, are non-negative integers.

Both tolerances (`PROBABILITY_SUM_TOLERANCE = 1e-3`, `TOP_TIE_TOLERANCE = 1e-9`) are
**provisional**: the docs say only that probabilities sum to 1, and real rounding has not been
observed. They are pinned by boundary tests (`test_probability_sum_tolerance_boundary`,
`test_top_tie_tolerance_boundary`) and may change only on evidence from approved live responses.
If live responses round more coarsely, they will fail as `invalid_response` until then: a safe
failure, not a silent acceptance.

A different `response_model` than the configured pin is `model_mismatch`. The SDK itself already
rejects malformed JSON and type errors (`TypeSafeAPIResponseValidationError` →
`invalid_response`).

## 4. Configuration and failure policy

| Name | Default | Notes |
|---|---|---|
| `JEV_ENABLED` | `false` | Only `true`/`false` words. |
| `TYPESAFE_API_KEY` | unset | Official TypeSafe variable name; `SecretStr`; empty = unset. Required only when enabled. |
| `JEV_MODEL` | `jev-1.13.0` | Versioned ids only; `jev-latest`, `jev-preview`, `jev` are rejected. |
| `JEV_BASE_URL` | `https://api.typesafe.ai` | Plain https only (no credentials, query or fragment). |
| `JEV_ATTEMPT_TIMEOUT_SECONDS` | `1.25` | httpx2 per-operation timeout. Provisional. |
| `JEV_MAX_RETRIES` | `1` | 0–2. Provisional. |
| `JEV_RETRY_BUDGET_SECONDS` | `3.0` | SDK retry budget. Provisional. Must exceed `retries × (attempt timeout + 0.25 s)`. |
| `JEV_DEADLINE_SECONDS` | `3.0` | Caller wait bound per call (plus scheduling tolerance). Provisional. Must cover the attempt timeout and the retry budget. |
| `JEV_MAX_IN_FLIGHT` | `8` | 1–64. Queued plus running calls, including cancelled calls still unwinding; extra calls get `saturated`. |

- **Configuration errors fail, never fall back.** Settings validation rejects every invalid value
  above **even while Jev is disabled** (as for the reranker settings), so a bad value fails
  application startup. Building a provider whose key the SDK rejects raises
  `JevConfigurationError` (fixed message, no chained exception).
- **Runtime failures of a call fall back** to the deterministic result with a fixed reason:

| Cause | `fallback_reason` | Retried by the SDK? |
|---|---|---|
| `TypeSafeAPITimeoutError` (httpx2 timeout), HTTP 408 | `timeout` | yes |
| `TypeSafeAPIConnectionError` | `transport_error` | yes |
| HTTP 401, 403 | `auth_error` | no |
| HTTP 429 | `rate_limited` | yes |
| HTTP 5xx (incl. 529) | `server_error` | yes |
| other 4xx (400, 404, 413, 415, 422, …) or another `TypeSafeError` | `request_rejected` | no |
| response fails validation (section 3) | `invalid_response` | no |
| answering model differs from the pin | `model_mismatch` | no |
| deadline reached (call cancelled) | `deadline_exceeded` | no (cancellation stops retries) |
| `JEV_MAX_IN_FLIGHT` calls already running | `saturated` | not sent |
| provider closed (before, while queued, or during the call) | `provider_closed` | not sent / cancelled |

  Exceptions that are not `TypeSafeError` are programming errors and propagate.
- **Disabled** (the default) is **not a fallback**. It uses the existing deterministic-provider
  contract: `Settings.jev_config()` is `None`, `build_decision_provider(None)` returns the
  `DeterministicDecisionProvider`, and its result is `provider="deterministic"`,
  `provider_version="qu-1"`, `jev=None`. No fallback reason is recorded because no Jev call was
  attempted; `jev=None` is itself the record. `typesafe_sdk` is never imported, and endpoints
  are unchanged (they construct the deterministic provider directly).

## 5. Retries and time: actual bounds (typesafe-sdk 0.7.5)

From the installed SDK source (`typesafe_sdk/_core/retry.py`, `transport.py`, `config.py`) and
`httpx2`, confirmed by the mocked tests named below.

- **One retry layer.** The provider makes exactly one `system_one` call and sets one
  `RetryPolicy` on the client. A per-call `retry=` would *replace* the client policy (it is never
  used here), so retries cannot multiply. Backoff: 0.1 s doubling to at most 0.25 s, minus up to
  25 % jitter.
- **Attempt timeout is per operation, not per attempt.** The float is passed to httpx2 as
  separate `connect`, `read`, `write` and `pool` timeouts (`test_the_attempt_timeout_reaches_httpx2_as_a_per_operation_timeout`).
  `read` applies to each socket read, so an attempt has **no whole-attempt time limit**; httpx2
  has no total timeout.
- **The budget is checked only before a retry sleep.** Tenacity's `stop_before_delay(budget)`
  stops when `elapsed + next_wait >= budget`; an attempt already in flight is never interrupted.
  (`test_the_retry_budget_is_not_a_wall_clock_deadline`: budget 0.7 s, two 0.4 s attempts, the
  call succeeded after more than 0.7 s.)
- **`Retry-After` / `retry-after-ms` are honoured and not capped by the backoff maximum**
  (`test_retry_after_is_honoured_and_is_not_capped_by_the_backoff_maximum`: 0.6 s requested,
  ≥ 0.5 s measured, cap 0.25 s), but a server wait that would reach the budget stops retrying
  immediately without sleeping (`test_retry_after_beyond_the_budget_stops_without_sleeping`:
  `Retry-After: 10` with a 3 s budget → one attempt, under 1 s).

On their own, the budget and attempt timeout do not bound a call: an attempt has no whole-attempt
limit, and no retry *starts* after the budget, but the last attempt may run past it. That is why
the provider adds a deadline.

### 5.1 Deadline (FR-DEC-3): bounded caller wait

- **Lifecycle.** Each enabled provider owns one event loop on one daemon thread
  (`jev-provider-loop`) and one `AsyncTypeSafeClient`, created once and reused by every call.
  Startup order is loop, then client, then thread. A failure at any step closes whatever already
  exists (tested), so no thread or client is left behind. Disabled mode starts no thread and needs
  no key (tested in a subprocess).
- **Bounded wait.** `understand()` submits the SDK call to the loop and waits at most the time
  left in `JEV_DEADLINE_SECONDS`, measured from the start of `understand()`. On expiry it cancels
  the call and returns the deterministic result with `deadline_exceeded`. The promise is a bounded
  caller wait **plus scheduling tolerance** (OS thread scheduling, the GIL, event-loop latency),
  not a hard real-time guarantee. Tests assert the wait within `deadline + 0.5 s`.
- **Cancellation (confirmed with mocks).** Cancelling during an in-flight attempt delivers
  `CancelledError` to the transport; cancelling during a retry wait (including a server
  `Retry-After`) prevents the retry. No attempt starts after cancellation, and no retry layer is
  added. Client cancellation does **not** guarantee that the server stops processing, or does not
  bill, a request it already received.
- **Late results.** Whatever completes after the deadline is never returned or cached. A result
  that wins the race between the timeout and the cancel is logged as `jev_late_result_discarded`,
  without content. A result that arrives after `close()` began is never surfaced either
  (`provider_closed`), even if the call swallowed its cancellation.
- **Bounded in-flight calls.** Admission happens on the caller's side before anything is queued:
  a call takes one of `JEV_MAX_IN_FLIGHT` slots or returns `saturated` immediately, so queued plus
  running calls never exceed the bound, even while the loop is blocked (tested). Each slot is
  released exactly once: by the caller if submission fails, by the loop if a queued call is skipped
  (its caller already timed out, or the provider closed; nothing is sent), or when its task ends
  (completed, failed or cancelled, including before its first step). A cancelled call keeps its
  slot until its task really ends. All three release paths are tested, the before-first-step case
  by forcing it directly; an offline probe of 25 near-zero-deadline calls found every call had
  started before its cancel arrived, which says nothing about other timings or Python versions.
- **close() and cleanup ownership.** `close()` is explicit and idempotent: it refuses new calls
  (`provider_closed`), stops the loop and waits at most 5 s (plus scheduling tolerance) for the
  loop thread. The loop thread owns cleanup: it skips queued calls, cancels running calls and
  waits for them without a time limit, then closes the async client, the loop's default executor
  and the loop, and exits. If blocking or cancellation-resistant work is still running when the
  wait ends, `close()` returns and logs `jev_provider_cleanup_pending`; the thread completes the
  same cleanup when that work ends (tested with finite blocking and finite cancellation
  resistance: thread gone, loop and client closed, no task left, every slot released). Python
  cannot force-stop code that never returns: such work keeps the thread, loop and client until
  process exit. A cancel issued by a caller after the loop has closed is skipped, so no callback
  error is logged.
- **Settings.** The attempt timeout and the retry budget must not exceed the deadline.

The values are provisional, and no real-network timing has been measured. Endpoints are unchanged
and do not use the provider.

## 6. Logging and secrets

- One INFO line per call from `ecommerce_search.decision.jev_provider`: `jev_decision` with model,
  response model, latency, request id, token counts, fallback reason, and per question the raw
  choice, top labels, raw probabilities and provider confidence. Never the key, headers, query
  text or exception text.
- The SDK logs request/response **bodies unredacted at DEBUG**, so building the provider raises the
  `typesafe_sdk` logger to at least WARNING (overriding the root level or `TYPESAFE_LOG_LEVEL`).
- The key is passed explicitly from `Settings`, as are model and base URL, so stray `TYPESAFE_*`
  environment variables change nothing. `Settings` sets `hide_input_in_errors=True`, which keeps
  input values out of error *messages* (`str`/`repr`) only; structured errors are covered by the
  next item.
- **Structured-error disclosure risk (fixed).** This was a risk demonstrated with fake inputs,
  not an actual leak: no real key exists and no real secret was involved. Pydantic records raw
  inputs in structured errors (`errors()`, `json()`) even with `hide_input_in_errors=True`, and a
  model-level error records the whole input dict. A `model_validator(mode="before")` cannot
  fix that (a prototype showed model-level errors keep the pre-validator input), so
  `Settings.settings_customise_sources` wraps each default source (constructor, environment,
  dotenv, secrets directory) in `_SecretInputSource`, which turns every `SecretStr`-typed field's
  value, matched by field name or alias, into a `SecretStr` before validation. Source order,
  precedence, values and all existing validation are unchanged. Covers `POSTGRES_PASSWORD` and
  `TYPESAFE_API_KEY`, and any future `SecretStr` field. Tested with fake values across all four
  sources, aliases, precedence, model-level, field-level, `too_short` and `missing` errors
  (`tests/unit/test_settings.py`).
- `JEV_`/`TYPESAFE_` variables are not forwarded to any Compose service, and unit tests strip
  them from the environment.

Local key setup, only if a key is ever created with separate approval (none exists; the `.env`
file was never opened by Claude): add the line `TYPESAFE_API_KEY=<key>` to the git-ignored
`.env` in an editor, without overwriting the file.
Nothing calls Jev unless `JEV_ENABLED=true` and code builds the provider; no endpoint does.

## 7. Documented API contract (official docs, read 2026-10-17; not observed live)

Everything in this section comes from documentation and the SDK source. None of it has been
confirmed by a real response.

| Fact | Source |
|---|---|
| `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`; request `state`, `model`, `questions`; Choice `criteria` map, max 255 options; response `model`, `answers`, `usage.input_tokens/output_tokens`; errors 400/401/413/415/422/429/529 | https://docs.typesafe.ai/api |
| Choice answer `choice`, `probabilities` (sum to 1), `confidence` (0–1) | https://docs.typesafe.ai/primitives/choice |
| `confidence = (p_max − 1/n)/(1 − 1/n)`; described as "one reasonable way to summarize a distribution"; calibration not claimed | https://docs.typesafe.ai/confidence |
| `jev-1.13.0` stable (`jev-latest`), `jev-1.14.0` from 2026-10-14 (`jev-preview`); pin versioned ids; **$0.042 per million input tokens, output free**; per-organisation rate limits (tokens/s, requests/s) shown only in the console; 64k tokens per request | https://docs.typesafe.ai/models |
| Python SDK `typesafe-sdk`, `TypeSafeClient`, `system_one`, `RetryPolicy` defaults, exceptions, response types, `TYPESAFE_*` variables, DEBUG body logging | https://docs.typesafe.ai/sdk/python/api/clients/sync, …/api/retries, …/api/exceptions, …/api/types/responses, …/api/constants, …/usage/advanced |
| Jev 1.13 weaknesses: numeric precision, first-option bias, adversarial content | https://docs.typesafe.ai/model-jaggedness/jev-1.13 |

Distribution check (PyPI JSON API, 2026-10-17): `typesafe-sdk` 0.7.5, MIT, `requires-python
>=3.10`, dependencies `anyio>=4.0.0`, `httpx2>=2.0.0`, `packaging>=24.0`, `pydantic>=2.12.0`,
`pydantic-core>=2.41.1`, `tenacity>=9.0.0`, `truststore>=0.10.0`, `typing-extensions>=4.13.0`;
wheel sha256 `967a8b61494d536abb55ee3f3a2b10e81685cb6baad6ccdcab588bdc6c9b86d2`, which matches
`uv.lock`.

## 8. Dependency graph (from `uv.lock`; `uv tree --frozen --offline --package typesafe-sdk`)

```
typesafe-sdk 0.7.5                      [new, pinned ==0.7.5]
├── anyio 4.15.1                        [already locked]
│   ├── idna 3.20                       [already locked]
│   └── typing-extensions 4.16.0        [already locked]
├── httpx2 2.13.1                       [new]
│   ├── anyio            (sys_platform != 'emscripten')
│   ├── httpcore2 2.13.1 [new]          (sys_platform != 'emscripten'; httpx2 pins ==2.13.1)
│   │   ├── h11 0.16.0                  [already locked]
│   │   └── truststore 0.10.5
│   ├── httpx2-jsfetch 1.0 [new]        (sys_platform == 'emscripten' only; never installed here)
│   ├── idna
│   ├── truststore 0.10.5 [new]         (sys_platform != 'emscripten')
│   └── typing-extensions
├── packaging 26.3                      [already locked]
├── pydantic 2.13.5                     [already locked; SDK needs >=2.12.0]
│   └── annotated-types, pydantic-core 2.46.5, typing-inspection, typing-extensions
├── pydantic-core 2.46.5                [already locked; SDK needs >=2.41.1]
├── tenacity 9.2.1                      [new]
├── truststore 0.10.5                   (sys_platform != 'emscripten')
└── typing-extensions 4.16.0
```

- New in `uv.lock`: 6 packages (`typesafe-sdk`, `httpx2`, `httpcore2`, `tenacity`,
  `truststore`, `httpx2-jsfetch`). Installed on this Windows/CPython 3.12 environment: 5 (all
  but `httpx2-jsfetch`). No existing package was upgraded or removed.
- The approved plan named 3 new transitive packages (`httpx2`, `tenacity`, `truststore`);
  `httpcore2` and `httpx2-jsfetch` are dependencies of `httpx2` that the plan did not list.
- SDK extras (`http2`, `pil`, `zstd`) are not installed; compression is disabled in code.
- Upload dates (PyPI): `typesafe-sdk` 2026-10-14, `truststore` 2026-10-12, `tenacity`
  2026-10-07, `httpx2`/`httpcore2` 2026-09-23, `httpx2-jsfetch` 2026-08-07. The first three
  were less than two weeks old at install time (2026-10-17).

### 8.1 Dependency decisions (owner-approved 2026-10-18)

Based on a read-only review on 2026-10-18:
- Every locked wheel and sdist hash matches PyPI.
- OSV listed no advisories for the locked versions (checked 2026-10-18 10:41 UTC).
- PyPI provenance attestations exist for `typesafe-sdk`, `tenacity` and `truststore`.
- `httpx2`/`httpcore2` are owned by the PyPI organization `pydantic`. Their sdists are
  byte-identical to `pydantic/httpx2` at tag `v2.13.1` (commit `d91c9f42`), apart from the
  generated `PKG-INFO`.

1. **Accepted** at the locked versions: `typesafe-sdk` 0.7.5, `tenacity` 9.2.1, `truststore`
   0.10.5, `httpx2` 2.13.1, `httpcore2` 2.13.1.
2. **Accepted** `httpx2-jsfetch` 1.0 as an Emscripten-only locked dependency, not installed on the
   current supported platforms. Revisit before adding Emscripten support.
3. **Accepted** the TLS behaviour: trust comes from the operating-system certificate store
   (`truststore`), and `SSL_CERT_FILE` / `SSL_CERT_DIR` override it. TLS verification is never
   disabled.
4. **Kept** the policy that invalid `JEV_*` settings fail startup, including while Jev is disabled
   (no behaviour change).
5. **Limitations kept:** there is no build attestation for `httpx2`/`httpcore2`, and no
   comprehensive code audit of the dependencies was done. "No known advisories found" is not
   proof of safety.

## 9. Unknown or not done

- Numeric rate limits for this organisation; error-body schema; data retention and training
  terms (DPA/Privacy Policy not reviewed); deprecation policy for pinned versions.
- Real tokens per request, real latency, real answer quality, Hinglish and short-query behaviour,
  first-option bias on these label sets: all need live calls (not authorised) and Milestone 12.
- Calibration of probabilities or `confidence`, thresholds, gating, V4: Milestone 12.
- FR-DEC-3 deadline: implemented and tested with mocks only (section 5.1). Real-network timing,
  and whether the server stops processing or billing cancelled requests, are unknown.
- Full unit suite: the final run passed (3,739 passed, 0 failed, 4 skipped) after the isolation
  limit was raised to 1,200 s with approval; an earlier run had failed on the old 600 s limit.
  Database integration tests passed (721) on a dedicated test database.
- Forced termination of code that never returns is not possible: such work is reclaimed only at
  process exit (section 5.1).
