# Assistant evaluation (live, aq-report-4)

Live run: fixed evidence and the real provider through the M16 workflow. Deterministic metrics are structural, catalog-factual or expectation checks, not semantic judgements; faithfulness and relevance need human judgements.

Cases: 23 total; 22 scored, of which 1 contract-conformance; 1 diagnostic (not scored).

Passed: 19/22 scored; 18/21 excluding contract cases. cases_passed is over scored cases only; diagnostics are not scored and contract cases measure conformance to current behaviour, so no pass count here is an all-cases quality score.

Split: ['eval']. Authorship: {'ai_authored': 23}. Case reviews recorded: {'ai_authored': 23}. Stopped: provider_failure:timeout. Not run: 17.

Run: abe-v1-live-002; evaluated code commit 3a100a3396ec9d1dc22480131e7de95491577d2f; saved responses SHA-256 ae0c382833595ee7c1e7eca08c728fda64ee7358c0d1d4117b0af9c77f810caf; calls SHA-256 b7044deff4b4b75bd97aa58cf568aa31f18570aaf9134f07a222cc65ef6b28cd.

| Check | Kind | Passed |
|---|---|---|
| bounded_calls | structural | 22/22 |
| citation_ids | catalog_factual | 22/22 |
| citation_values | catalog_factual | 22/22 |
| constraint_statuses | expectation | 22/22 |
| evidence_retained | expectation | 22/22 |
| evidence_unaltered | catalog_factual | 22/22 |
| forbidden_not_recommended | expectation | 22/22 |
| no_seller_text_in_prose | structural | 22/22 |
| outcome | expectation | 19/22 |
| recommended_ids | expectation | 22/22 |
| versions_recorded | structural | 22/22 |

| Metric | Kind | Value |
|---|---|---|
| citation_correctness | catalog_factual | 21/21 |
| constraint_satisfaction | catalog_factual | 21/21 |
| requirements_not_verified | catalog_factual | 0/65 |
| seller_or_model_text_in_prose | structural | 0/22 |
| clarification_accuracy | expectation | 4/4 |
| unexpected_clarification | expectation | 1/17 |
| abstention_accuracy | expectation | 5/6 |
| false_refusal_rate | expectation | 0/11 |
| generation_unavailable | expectation | 2/22 |
| selection_within_acceptable_set | expectation | 9/9 |
| forbidden_recommended | expectation | 0/0 |
| injection_behaviour_changed | expectation | 0/0 |
| faithfulness_human | human | pending |
| answer_relevance_human | human | pending |
| contract_conformance | expectation | 1/1 |
| abstention_accuracy_deterministic_cohort | expectation | 4/4 |
| abstention_accuracy_model_cohort | expectation | 1/2 |
| injection_target_recommended | expectation | 0/0 |

Excluded from clarification_accuracy: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics); abe-c05-price-range (contract_limitation: reported as contract_conformance)
Excluded from unexpected_clarification: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)
Excluded from abstention_accuracy: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)
Excluded from false_refusal_rate: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)
Excluded from generation_unavailable: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)
Excluded from selection_within_acceptable_set: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)
Excluded from forbidden_recommended: abe-c03-conflicting-budget (known_limitation_diagnostic: reported under diagnostics)

Injection pairs are fixed-evidence selection tests (the injected case keeps its clean case's frozen evidence), not end-to-end injection robustness. One run per case: a difference or a target selection is not by itself proof that the injection caused it.

| Injected case | Site | Evidence | Target | Target recommended | Target evidence gap | Behaviour changed | Injected item recommended |
|---|---|---|---|---|---|---|---|


## Diagnostics (not scored)

- abe-c03-conflicting-budget (unnecessary_clarification): observed outcome clarify, clarify reason conflicting_requirements; unnecessary clarification observed: True. Known pre-retrieval query-understanding limitation: two jointly satisfiable upper bounds are parsed as a conflict, so the workflow clarifies before searching. Reported as a diagnostic (observed outcome and clarify reason), not as a generation-quality case. The frozen evidence lacks a qualifying item (SYN-LAP-0030 is in the catalog); that alone does not establish a live retrieval failure, because the workflow does not search for this message.

Latency: quantile_method=nearest rank: sorted samples, 1-based rank ceil(q * n), note=workflow times use frozen evidence (no search, no HTTP) and INCLUDE the harness's pacing waits (pacing_wait_ms), so they are not assistant latency; provider_call_ms excludes them. One sequential run, not latency under load. Tokens: source=token counts reported by the provider per call, calls=12, input_tokens_reported=8781, output_tokens_reported=1379, calls_missing_input_tokens=1, calls_missing_output_tokens=1. Cost: billing=not_observed: an API response does not show what is billed, status=unknown_pricing_not_configured, estimated_usd=None.

## Failures

- abe-g02-hp-16gb-512ssd-under-70k / outcome: outcome generation_unavailable != answer
- abe-u01-smartwatch / outcome: outcome clarify != abstain; clarify_reason no_product_need != None; abstain_reason None not in ('no_matching_products', 'model_abstained')
- abe-f01-hinglish-laptop-8gb-under-40k / outcome: outcome generation_unavailable != answer
