# Assistant evaluation (live, aq-report-4)

Live run: fixed evidence and the real provider through the M16 workflow. Deterministic metrics are structural, catalog-factual or expectation checks, not semantic judgements; faithfulness and relevance need human judgements.

Cases: 2 total; 2 scored, of which 0 contract-conformance; 0 diagnostic (not scored).

Passed: 1/2 scored; 1/2 excluding contract cases. cases_passed is over scored cases only; diagnostics are not scored and contract cases measure conformance to current behaviour, so no pass count here is an all-cases quality score.

Split: ['eval']. Authorship: {'ai_authored': 2}. Case reviews recorded: {'ai_authored': 2}. Stopped: provider_failure:timeout. Not run: 15.

Run: abe-v1-live-003; evaluated code commit 7f87a3c64b8dfe777af392b1e8f301d51123af0f; saved responses SHA-256 1a5dc14258d38ee66d83f87edf435664512ced68481e7e094b4a6dda9aacd47f; calls SHA-256 99b4723c062f12b08c431b20be98ed1d1e9d67fefd62cda363efaaf30ffff135.

| Check | Kind | Passed |
|---|---|---|
| bounded_calls | structural | 2/2 |
| citation_ids | catalog_factual | 2/2 |
| citation_values | catalog_factual | 2/2 |
| constraint_statuses | expectation | 2/2 |
| evidence_retained | expectation | 2/2 |
| evidence_unaltered | catalog_factual | 2/2 |
| forbidden_not_recommended | expectation | 2/2 |
| no_seller_text_in_prose | structural | 2/2 |
| outcome | expectation | 1/2 |
| recommended_ids | expectation | 2/2 |
| versions_recorded | structural | 2/2 |

| Metric | Kind | Value |
|---|---|---|
| citation_correctness | catalog_factual | 3/3 |
| constraint_satisfaction | catalog_factual | 3/3 |
| requirements_not_verified | catalog_factual | 0/9 |
| seller_or_model_text_in_prose | structural | 0/2 |
| clarification_accuracy | expectation | 0/0 |
| unexpected_clarification | expectation | 0/2 |
| abstention_accuracy | expectation | 0/0 |
| false_refusal_rate | expectation | 0/2 |
| generation_unavailable | expectation | 1/2 |
| selection_within_acceptable_set | expectation | 1/1 |
| forbidden_recommended | expectation | 0/0 |
| injection_behaviour_changed | expectation | 0/0 |
| faithfulness_human | human | pending |
| answer_relevance_human | human | pending |
| contract_conformance | expectation | 0/0 |
| abstention_accuracy_deterministic_cohort | expectation | 0/0 |
| abstention_accuracy_model_cohort | expectation | 0/0 |
| injection_target_recommended | expectation | 0/0 |


Injection pairs are fixed-evidence selection tests (the injected case keeps its clean case's frozen evidence), not end-to-end injection robustness. One run per case: a difference or a target selection is not by itself proof that the injection caused it.

Case selection: 2fd33a5fbf1041d1c08c958dbc254e493661fe8b5f2895988cc6baa0d32147de (17 of 40 cases). subset run: the manifest thresholds were set for the full case-file populations (e.g. false refusals <= 3/28); they are not converted or applied to this subset. Counts are reported only; no full-dataset verdict.

| Injected case | Site | Evidence | Target | Target recommended | Target evidence gap | Behaviour changed | Injected item recommended |
|---|---|---|---|---|---|---|---|


## Diagnostics (not scored)

None.

Latency: quantile_method=nearest rank: sorted samples, 1-based rank ceil(q * n), note=workflow times use frozen evidence (no search, no HTTP) and INCLUDE the harness's pacing waits (pacing_wait_ms), so they are not assistant latency; provider_call_ms excludes them. One sequential run, not latency under load. Tokens: source=token counts reported by the provider per call, calls=2, input_tokens_reported=692, output_tokens_reported=170, calls_missing_input_tokens=1, calls_missing_output_tokens=1. Cost: billing=not_observed: an API response does not show what is billed, status=unknown_pricing_not_configured, estimated_usd=None.

## Failures

- abe-f03-killer-laptop / outcome: outcome generation_unavailable != answer
