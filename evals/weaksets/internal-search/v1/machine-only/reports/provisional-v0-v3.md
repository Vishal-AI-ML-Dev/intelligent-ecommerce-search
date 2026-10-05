# weak_dev_v1 — provisional V0–V3 development evaluation (machine-only)

> **WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit coverage: 0/568 (0%). Two started human audit decisions were abandoned, archived and excluded. These results are provisional development diagnostics only. They are not Golden Dataset results or final held-out test evidence.**

Deterministic machine weak-label development set: unaudited machine-generated labels (`weak-label-policy-1`), provisional development evaluation. Not a human-reviewed Golden Dataset; not final held-out test evidence.

| Field | Value |
|---|---|
| status | `weak_machine_dev_sealed` |
| label_basis | `deterministic_machine_weak_labels` |
| policy_version | `weak-label-policy-1` |
| proposal manifest | `9dc1fab28fbd89e9151424cd5990375728c71a6c3fc7a802d20f1526d0fa8b6e` |
| pool manifest | `4c26628ac45cd8dd1c31411e586df2c518c492ffbcd95145d19c1965a67e5897` |
| evaluator | `eval-1` |
| provisional | true |

## Disclosures

- This is a deterministic machine weak-label development set: 568/568 labels are unaudited machine-generated labels from weak-label-policy-1.
- Human audit coverage is 0/568. No label was reviewed, accepted or overridden by a human.
- Two human audit decisions were started and then abandoned when the user selected the machine-only track. They are archived for auditability and excluded from all labels, metrics and human-audit claims.
- It is not a human-reviewed Golden Dataset and not final held-out test evidence.
- There is no delayed intra-annotator consistency measurement and no inter-annotator agreement.
- Every metric computed on it is a provisional development evaluation only.

## Labels

| Item | Value |
|---|---|
| Total machine labels | 568 |
| Machine-only coverage | 568/568 |
| Human audit coverage | 0/568 |
| Abandoned human decisions | 2, excluded |
| requires_human_review | 319 |
| Deterministic-rule only | 526 |
| Model-assisted (confidence lowered by local signals) | 42 |

Every label and relation comes from the deterministic catalog/gold-constraint rules. A record is counted as model-assisted when the local cross-encoder and retrieval-consensus signals disagreed with the rule outcome and lowered its heuristic confidence (signal_conflict); otherwise it is deterministic-rule only.

### Label counts

| Value | Count |
|---|---|
| relevant | 212 |
| borderline | 61 |
| not_relevant | 295 |

### Relation counts

| Value | Count |
|---|---|
| exact_match | 119 |
| satisfies_intent | 93 |
| substitute | 49 |
| partial | 12 |
| complement | 6 |
| off_target | 265 |
| out_of_catalog | 24 |

### Unaudited heuristic-confidence counts

| Value | Count |
|---|---|
| high | 249 |
| medium | 198 |
| low | 121 |

## Macro V0–V3 aggregates

Macro means over the queries where the metric is defined (`n` = defined queries). Versions: v0 = `v0_lexical`, v1 = `v1_hybrid`, v2 = `v2_filtered`, v3 = `v3_reranked_d20`.

| Metric | V0 | V1 | V2 | V3 |
|---|---|---|---|---|
| precision@5 (strict) | 0.4500 (n=20) | 0.6100 (n=20) | 0.7000 (n=20) | 0.7100 (n=20) |
| precision@10 (strict) | 0.3950 (n=20) | 0.5500 (n=20) | 0.6300 (n=20) | 0.6250 (n=20) |
| precision@5 (lenient) | 0.4600 (n=20) | 0.7000 (n=20) | 0.7600 (n=20) | 0.7600 (n=20) |
| precision@10 (lenient) | 0.4000 (n=20) | 0.6400 (n=20) | 0.6850 (n=20) | 0.6750 (n=20) |
| recall@5 (strict) | 0.2914 (n=17) | 0.4103 (n=17) | 0.4962 (n=17) | 0.4797 (n=17) |
| recall@10 (strict) | 0.4348 (n=17) | 0.6231 (n=17) | 0.7358 (n=17) | 0.7299 (n=17) |
| recall@5 (lenient) | 0.1882 (n=19) | 0.3456 (n=19) | 0.3604 (n=19) | 0.3491 (n=19) |
| recall@10 (lenient) | 0.3004 (n=19) | 0.5724 (n=19) | 0.5631 (n=19) | 0.5514 (n=19) |
| MRR@10 (strict) | 0.5588 (n=17) | 0.8647 (n=17) | 1.0000 (n=17) | 0.9314 (n=17) |
| MRR@10 (lenient) | 0.5263 (n=19) | 0.8234 (n=19) | 0.8947 (n=19) | 0.8596 (n=19) |
| ndcg@5 | 0.4943 (n=19) | 0.7450 (n=19) | 0.8498 (n=19) | 0.8247 (n=19) |
| ndcg@10 | 0.4756 (n=19) | 0.7584 (n=19) | 0.8449 (n=19) | 0.8150 (n=19) |
| csr@10 (hard) | 1.0000 (n=10) | 0.6263 (n=19) | 0.8722 (n=18) | 0.8722 (n=18) |
| csr@10 (fp1) | 1.0000 (n=10) | 0.6737 (n=19) | 0.9444 (n=18) | 0.9444 (n=18) |
| irrelevant-result rate@10 | 0.0000 (n=10) | 0.3600 (n=20) | 0.2105 (n=19) | 0.2211 (n=19) |
| zero_result | 0.5000 (n=20) | 0.0000 (n=20) | 0.0500 (n=20) | 0.0500 (n=20) |

## Per-query V0–V3 metrics

### V0 (`v0_lexical`)

| Query | P@5 s | P@10 s | P@10 l | R@5 s | R@10 s | R@10 l | MRR@10 s | NDCG@5 | NDCG@10 | CSR@10 hard | CSR@10 fp1 | Irrel@10 | Zero |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq-p01 | 1.0000 | 1.0000 | 1.0000 | 0.1613 | 0.3226 | 0.3226 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p02 | 1.0000 | 1.0000 | 1.0000 | 0.4167 | 0.8333 | 0.8333 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p03 | 1.0000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p04 | 0.2000 | 0.1000 | 0.1000 | 1.0000 | 1.0000 | 0.2500 | 1.0000 | 0.5615 | 0.5615 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p05 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p06 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p07 | 1.0000 | 1.0000 | 1.0000 | 0.3571 | 0.7143 | 0.4545 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p08 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.3704 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p09 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.7500 | 1.0000 | 1.0000 | 0.9106 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p10 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p11 | 0.8000 | 0.6000 | 0.7000 | 0.3636 | 0.5455 | 0.5833 | 0.5000 | 0.8304 | 0.6906 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p12 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.6667 | 1.0000 | 1.0000 | 0.8743 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p13 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p14 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p15 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p16 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p17 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p18 | 1.0000 | 1.0000 | 1.0000 | 0.2381 | 0.4762 | 0.4762 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p19 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p20 | 0.0000 | 0.0000 | 0.0000 | — | — | — | — | — | — | — | — | — | 1.0000 |

### V1 (`v1_hybrid`)

| Query | P@5 s | P@10 s | P@10 l | R@5 s | R@10 s | R@10 l | MRR@10 s | NDCG@5 | NDCG@10 | CSR@10 hard | CSR@10 fp1 | Irrel@10 | Zero |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq-p01 | 1.0000 | 1.0000 | 1.0000 | 0.1613 | 0.3226 | 0.3226 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p02 | 1.0000 | 1.0000 | 1.0000 | 0.4167 | 0.8333 | 0.8333 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p03 | 1.0000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p04 | 0.2000 | 0.1000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.1000 | 0.6000 | 0.6000 | 0.0000 |
| gq-p05 | 0.2000 | 0.4000 | 0.4000 | 0.0526 | 0.2105 | 0.2000 | 0.2000 | 0.1312 | 0.2942 | 0.4000 | 0.4000 | 0.6000 | 0.0000 |
| gq-p06 | 0.6000 | 0.7000 | 0.7000 | 0.4286 | 1.0000 | 1.0000 | 1.0000 | 0.7227 | 0.9263 | 0.7000 | 0.7000 | 0.3000 | 0.0000 |
| gq-p07 | 1.0000 | 1.0000 | 1.0000 | 0.3571 | 0.7143 | 0.4545 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p08 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.3704 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p09 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.7500 | 1.0000 | 1.0000 | 0.9106 | 0.6000 | 0.6000 | 0.4000 | 0.0000 |
| gq-p10 | 0.0000 | 0.0000 | 0.1000 | 0.0000 | 0.0000 | 0.5000 | 0.0000 | 0.1900 | 0.1900 | 0.0000 | 0.0000 | 0.9000 | 0.0000 |
| gq-p11 | 1.0000 | 0.7000 | 0.8000 | 0.4545 | 0.6364 | 0.6667 | 1.0000 | 1.0000 | 0.8276 | 1.0000 | 1.0000 | 0.2000 | 0.0000 |
| gq-p12 | 1.0000 | 0.6000 | 0.8000 | 0.8333 | 1.0000 | 0.8889 | 1.0000 | 1.0000 | 0.9524 | 0.6000 | 1.0000 | 0.2000 | 0.0000 |
| gq-p13 | 0.0000 | 0.0000 | 0.2000 | — | — | 0.6667 | — | 0.0000 | 0.2769 | 0.5000 | 0.5000 | 0.8000 | 0.0000 |
| gq-p14 | 0.2000 | 0.2000 | 0.6000 | 0.1000 | 0.2000 | 0.2400 | 1.0000 | 0.5118 | 0.4696 | 0.3000 | 0.3000 | 0.4000 | 0.0000 |
| gq-p15 | 0.4000 | 0.4000 | 0.9000 | 0.1000 | 0.2000 | 0.2571 | 0.5000 | 0.6918 | 0.6527 | 0.4000 | 0.4000 | 0.1000 | 0.0000 |
| gq-p16 | 0.6000 | 0.3000 | 0.3000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 | 0.9082 | 0.9082 | 0.3000 | 0.3000 | 0.7000 | 0.0000 |
| gq-p17 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p18 | 1.0000 | 1.0000 | 1.0000 | 0.2381 | 0.4762 | 0.4762 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p19 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| gq-p20 | 0.0000 | 0.0000 | 0.0000 | — | — | — | — | — | — | — | — | 1.0000 | 0.0000 |

### V2 (`v2_filtered`)

| Query | P@5 s | P@10 s | P@10 l | R@5 s | R@10 s | R@10 l | MRR@10 s | NDCG@5 | NDCG@10 | CSR@10 hard | CSR@10 fp1 | Irrel@10 | Zero |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq-p01 | 1.0000 | 1.0000 | 1.0000 | 0.1613 | 0.3226 | 0.3226 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p02 | 1.0000 | 1.0000 | 1.0000 | 0.4167 | 0.8333 | 0.8333 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p03 | 1.0000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p04 | 0.2000 | 0.1000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.1000 | 1.0000 | 0.6000 | 0.0000 |
| gq-p05 | 1.0000 | 1.0000 | 1.0000 | 0.2632 | 0.5263 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p06 | 0.6000 | 0.7000 | 0.7000 | 0.4286 | 1.0000 | 1.0000 | 1.0000 | 0.7227 | 0.9263 | 0.7000 | 0.7000 | 0.3000 | 0.0000 |
| gq-p07 | 1.0000 | 1.0000 | 1.0000 | 0.3571 | 0.7143 | 0.4545 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p08 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.3704 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p09 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.7500 | 1.0000 | 1.0000 | 0.9106 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p10 | 0.2000 | 0.1000 | 0.1000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 0.7602 | 0.7602 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p11 | 1.0000 | 0.7000 | 0.8000 | 0.4545 | 0.6364 | 0.6667 | 1.0000 | 1.0000 | 0.8276 | 1.0000 | 1.0000 | 0.2000 | 0.0000 |
| gq-p12 | 1.0000 | 0.6000 | 0.8000 | 0.8333 | 1.0000 | 0.8889 | 1.0000 | 1.0000 | 0.9524 | 0.6000 | 1.0000 | 0.2000 | 0.0000 |
| gq-p13 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| gq-p14 | 0.4000 | 0.5000 | 1.0000 | 0.2000 | 0.5000 | 0.4000 | 1.0000 | 0.7544 | 0.7683 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p15 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.2857 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p16 | 0.6000 | 0.3000 | 0.3000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 | 0.9082 | 0.9082 | 0.3000 | 0.3000 | 0.7000 | 0.0000 |
| gq-p17 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p18 | 1.0000 | 1.0000 | 1.0000 | 0.2381 | 0.4762 | 0.4762 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p19 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p20 | 0.0000 | 0.0000 | 0.0000 | — | — | — | — | — | — | — | — | 1.0000 | 0.0000 |

### V3 (`v3_reranked_d20`)

| Query | P@5 s | P@10 s | P@10 l | R@5 s | R@10 s | R@10 l | MRR@10 s | NDCG@5 | NDCG@10 | CSR@10 hard | CSR@10 fp1 | Irrel@10 | Zero |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq-p01 | 1.0000 | 1.0000 | 1.0000 | 0.1613 | 0.3226 | 0.3226 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p02 | 1.0000 | 1.0000 | 1.0000 | 0.4167 | 0.8333 | 0.8333 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p03 | 1.0000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p04 | 0.2000 | 0.1000 | 0.4000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9315 | 0.9315 | 0.1000 | 1.0000 | 0.6000 | 0.0000 |
| gq-p05 | 1.0000 | 1.0000 | 1.0000 | 0.2632 | 0.5263 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p06 | 1.0000 | 0.7000 | 0.7000 | 0.7143 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9951 | 0.7000 | 0.7000 | 0.3000 | 0.0000 |
| gq-p07 | 1.0000 | 1.0000 | 1.0000 | 0.3571 | 0.7143 | 0.4545 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p08 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.3704 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p09 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.7500 | 1.0000 | 1.0000 | 0.9106 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p10 | 0.2000 | 0.1000 | 0.1000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 0.7602 | 0.7602 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p11 | 1.0000 | 0.7000 | 0.8000 | 0.4545 | 0.6364 | 0.6667 | 1.0000 | 1.0000 | 0.8334 | 1.0000 | 1.0000 | 0.2000 | 0.0000 |
| gq-p12 | 1.0000 | 0.6000 | 0.6000 | 0.8333 | 1.0000 | 0.6667 | 1.0000 | 1.0000 | 0.8743 | 0.6000 | 1.0000 | 0.4000 | 0.0000 |
| gq-p13 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| gq-p14 | 0.6000 | 0.4000 | 1.0000 | 0.3000 | 0.4000 | 0.4000 | 0.5000 | 0.7648 | 0.7037 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p15 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.2857 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p16 | 0.2000 | 0.3000 | 0.3000 | 0.3333 | 1.0000 | 0.7500 | 0.3333 | 0.2131 | 0.4759 | 0.3000 | 0.3000 | 0.7000 | 0.0000 |
| gq-p17 | 1.0000 | 1.0000 | 1.0000 | 0.2500 | 0.5000 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p18 | 1.0000 | 1.0000 | 1.0000 | 0.2381 | 0.4762 | 0.4762 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| gq-p19 | 0.0000 | 0.0000 | 0.0000 | — | — | 0.0000 | — | 0.0000 | 0.0000 | — | — | — | 1.0000 |
| gq-p20 | 0.0000 | 0.0000 | 0.0000 | — | — | — | — | — | — | — | — | 1.0000 | 0.0000 |

## Heuristic limitations

- Labels are produced by fixed heuristics (weak-label-policy-1) over catalog and gold-constraint facts; no label was checked by a human and no accuracy is known.
- Semantic intents (gaming, office), the low-price preference, near-miss substitutes and the headphones-as-complement mapping are hand-chosen rules, not learned or validated.
- Heuristic confidence is a rule-based ordering, not a calibrated probability.
- The cross-encoder (the provisional M8 compatibility pin, ADR-007) and retrieval consensus only lower confidence; labels can still be wrong where the facts are incomplete.
- The labels partly reuse the systems being evaluated (their consensus affects confidence), and gold constraints mirror what V2 filters on, so V2/V3 may be favoured.

## Pooled-recall limitations

- Pooled evaluation: only the frozen pool (union of the six systems' top 20 plus eligibility completion and 4 negatives, at most 60 per query) is labelled.
- Recall is pooled recall. For gq-p01, gq-p11, gq-p13 and gq-p15 more than 25 products are eligible, completion did not run, and pooled recall is biased upward.
- Every evaluated result list is inside the pool, so unjudged@K is 0 by construction; this does not mean the catalog was exhaustively judged.
- 20 pilot development queries only (3 Hinglish, 2 noisy); not a held-out test partition.

## Strict Golden Dataset status

- Strict M9 remains paused: 0/568 primary judgements, no primary seal, no 72-hour consistency clock, no labels freeze.
- The strict human-reviewed Golden Dataset is incomplete, and final M10 evidence is incomplete.

> **WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit coverage: 0/568 (0%). Two started human audit decisions were abandoned, archived and excluded. These results are provisional development diagnostics only. They are not Golden Dataset results or final held-out test evidence.**
