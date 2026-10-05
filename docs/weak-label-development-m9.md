# M9 optional track — deterministic machine weak-label development set (`weak_dev_v1`)

> **WARNING — 568/568 labels are deterministic machine-generated weak labels. Human audit
> coverage: 0/568 (0%). Two started human audit decisions were abandoned, archived and excluded.
> These results are provisional development diagnostics only. They are not Golden Dataset
> results or final held-out test evidence.**

The machine-only development evaluation is complete. `weak_dev_v1` is a **deterministic machine
weak-label development set**:
- every label is an **unaudited machine-generated label** from `weak-label-policy-1`;
- every metric on it is a **provisional development evaluation**;
- it is **not a human-reviewed Golden Dataset** and **not final held-out test evidence**;
- human audit coverage is zero: no label was reviewed, accepted or overridden by a human;
- there is no delayed intra-annotator consistency measurement and no inter-annotator agreement.

Two human audit decisions were started and then abandoned when the user selected the
machine-only track. They are archived for auditability and excluded from all labels, metrics and
human-audit claims. See "Abandoned audit archive".

The strict Golden Dataset lifecycle (`docs/golden-dataset-m9.md`) is separate and stays paused at
0/568 primary judgements. The strict Golden Dataset is incomplete and final M10 evidence is
incomplete. This track did not start strict annotation, the 72-hour consistency clock or M10, and
it writes nothing into the strict paths.

## Status

| Step | State |
|---|---|
| Proposal generation (`weak-label-policy-1`) | Done (`07416a3`). Two fresh-process runs were byte-identical; stored and verified |
| Audit queue (`weak-audit-1`, 100 pairs) | Generated, but not used: the user chose the machine-only path |
| Human audit | **Not performed (0/100).** Two started decisions were abandoned and archived (see below) |
| `weak_dev_sealed` (audited assembly) | Not run, and not applicable to the machine-only path |
| **Machine-only finalization (`weak_machine_dev_sealed`)** | **Done.** 568/568 machine labels, provisional V0–V3 metrics and report; two fresh-process builds byte-identical |

## Identities

| Identity | Used for |
|---|---|
| `weak-label-policy-1` | Author of every machine proposal (`proposal_source`). Never a human handle |
| `ann-1` | Only on explicit human audit decisions (accept/override) and on the `weak_dev_sealed` assembly |

A proposal is never an `ann-1` judgement, and no machine proposal is written into the strict
primary-judgement path.

Every final weak label records its source:
- `machine_unreviewed` (an unaudited proposal);
- `human_accepted_proposal`;
- `human_override`.

## Inputs (local and offline only)

- The verified `queries_frozen` (`51204dae…`) and `pool_frozen` (`4c26628a…`, 568 pairs)
  stages: queries, human-authored gold constraints, product intents, eligibility and `card-1`
  card hashes.
- The verified private pool provenance (binding `fc6526f9…`). It provides each pair's retrieval
  hits for the six pooled systems (V0 lexical, dense-only, V1 hybrid, V2 filtered, V3 reranked at
  depth 10 and 20) and its inclusion reason.
- The pinned local cross-encoder `cross-encoder/ms-marco-MiniLM-L6-v2` at revision `233902d2…`
  (snapshot verified offline). It scores all 568 pairs against the `rd-1` reranker documents.

No remote provider, LLM, Jev, paid API, internet access, downloaded model, Claude-generated
per-product label or seller/user behaviour is used. The generation runs behind the outbound
network guard with hub offline mode.

**Signal verification.** The recomputed logits must reproduce every V3 reranker score in the
verified provenance within `1e-4`, and the provenance's reranker pins must equal the pinned
spec; otherwise nothing is generated. Actual run: 479 provenance scores checked, maximum
absolute difference `4.29e-6`.

## Proposal policy `weak-label-policy-1` (`evals/weak_labels/policy.py`)

The label comes from deterministic catalog and gold facts. The two local signals only lower
confidence when they disagree:

- the cross-encoder's tertile band inside the query's pool (`high`/`medium`/`low`);
- retrieval consensus: `strong` means at least 4 of the 6 systems retrieved the pair, `partial`
  means 1–3, and `none` means 0.

Rules, first match wins:

| Rule | Proposal | Base confidence |
|---|---|---|
| Out-of-catalog query | `not_relevant/out_of_catalog` | high |
| Hard category violated, accessory category (phone or laptop query → headphones) | `not_relevant/complement` | medium |
| Hard category violated, other | `not_relevant/off_target` | high |
| Two or more hard violations | `not_relevant/off_target` | high |
| Brand is the only violation | `not_relevant/off_target` | medium |
| Processor is the only violation, but the title has the soft series token | `borderline/partial` | low |
| One near miss: price at most 10% over an upper bound, RAM/storage one ×2 step away, or women/men → unisex | `borderline/substitute` | low |
| Any other single hard violation | `not_relevant/off_target` | medium |
| Hard unknown (a missing value, no violation): **not treated as a violation** | `relevant/satisfies_intent` | low |
| Ambiguous query, category in a recorded interpretation / not | `relevant/satisfies_intent` / `borderline/partial` | medium / low |
| Soft semantic intent `gaming`: dedicated GPU / performance laptop without one / neither | `relevant/satisfies_intent` / `borderline/partial` / `not_relevant/off_target` | medium / low / low |
| Soft semantic intent `office`: business subcategory or "office" in the description / otherwise | `relevant/satisfies_intent` | medium / low |
| Soft low-price preference (`sasta`): at or below / above the median price of the query's eligible pooled products | `relevant/satisfies_intent` / `borderline/partial` | medium / low |
| Soft series intent: title has the series / not | `relevant/exact_match` / `relevant/satisfies_intent` | high / medium |
| Only a category constraint | `relevant/satisfies_intent` | high |
| Every hard constraint satisfied | `relevant/exact_match` | high |

Confidence rules:
- Semantic and ambiguous proposals are capped at medium.
- A **signal conflict** always lowers confidence by one step, so a conflicting proposal is never
  high. A conflict is either:
  - proposed `relevant`, but in the cross-encoder's bottom third; or
  - proposed `not_relevant`, but in its top third with strong consensus.
- `requires_human_review` is set on every non-high or conflicting proposal.
- A hard-violating product is never proposed `relevant`.
- Complements are always `not_relevant`.
- No proposal is used as a filter.

## Generated artifacts

Public files (committed) in `evals/weaksets/internal-search/v1/`:

| File | Content |
|---|---|
| `proposals.jsonl` | 568 `WeakLabelProposal` records: label, relation, confidence, rule, hard-constraint status, violated/unknown fields, coarse reranker band, consensus band, conflict flag, `requires_human_review`, a short public reason, and the pool-manifest, product-content, card, catalog, model and policy versions. No logit, rank, score, source name or inclusion reason |
| `audit_queue.jsonl` | 100 `AuditQueueItem` records, each bound to its proposal's SHA-256 |
| `proposal_manifest.json` | Deterministic manifest: file hashes, the private signals binding, versions, counts and disclosures. It has no wall-clock time |
| `generation_run.json` | Non-deterministic run facts, kept out of the compared bytes: `generated_at`, two independent runs, byte identity, network guard, no remote provider, and the reranker check |

Private files (git-ignored):
- `data/processed/weak_labels/4c26628ac45cd8dd/signals.jsonl`: logits, in-pool ranks, retrieval
  hits and inclusion reasons. The public manifest binds it by SHA-256 only (`signals_binding`).
- The audit work directory `data/processed/weak_audit/<proposal-manifest sha256[:16]>/`.

| Fact | Value |
|---|---|
| `proposal_manifest` SHA-256 | `9dc1fab28fbd89e9151424cd5990375728c71a6c3fc7a802d20f1526d0fa8b6e` |
| `signals_binding` | `dc232ba954e20c91e3b53698a335a00cf53d19a71ee6838322e042f91e90dadc` |
| Proposals | 568: 212 relevant, 61 borderline, 295 not_relevant |
| Confidence | 249 high, 198 medium, 121 low; 42 signal conflicts |
| Flagged `requires_human_review` | 319 |

**Determinism.** `propose` runs the whole generation twice in fresh child processes. It writes
only when `signals.jsonl`, `proposals.jsonl`, `audit_queue.jsonl` and `proposal_manifest.json`
are byte-identical; otherwise it stops and names the first differing file and line.

`verify` re-derives everything from the stored logits without a model and compares every byte:
the signals' ranks and hits are re-derived against the provenance, and the proposals, queue and
manifest are regenerated.

## Audit queue `weak-audit-1` (`evals/weak_labels/sampling.py`)

The queue holds exactly 100 pairs: 5 for each of the 20 queries, with no duplicates. It is
chosen from the proposals only and never from human labels. Per query, the slots are:

1. a proposed `relevant` pair;
2. a proposed `borderline` pair;
3. a proposed `not_relevant` pair;
4. the lowest-confidence pair, conflicts first;
5. a SHA-256-selected remaining pair.

Ties use `sha256("weak-audit-1|<slot>|<query>|<product>")`. An unavailable slot is filled in
SHA-256 order and recorded as `sha256_fill`.

Actual queue:
- slots: 17 proposed relevant, 13 proposed borderline, 20 proposed not_relevant, 20
  lowest-confidence/conflict, 20 SHA-256-selected and 10 SHA-256 fills;
- proposed labels: 32 relevant, 19 borderline, 49 not_relevant;
- confidences: 33 high, 38 medium, 29 low.

Because every pilot query is audited, every query type is covered: semantic (`gq-p11`, `gq-p13`,
`gq-p15`), Hinglish (`gq-p13`–`gq-p15`), noisy (`gq-p16`, `gq-p17`), exact series (`gq-p04`),
ambiguous (`gq-p18`), zero-eligible (`gq-p19`) and out-of-catalog (`gq-p20`). The queue
validator enforces this coverage.

## The audit tooling (not used; human audit coverage 0)

```text
uv run python -m evals.weak_labels audit --annotator ann-1
```

For each queued pair the CLI shows:
- the query, its interpretations, and the query's gold constraints and intents;
- the `card-1` product card;
- the machine proposal (label, relation, confidence) and its short reason;
- a warning that the proposal may be wrong.

It never shows a logit, rank, score, source or inclusion reason.

| Command | Effect |
|---|---|
| `a` | accept the proposal (`human_accepted_proposal`) |
| `r<n><c>` / `b<n><c>` / `n<n><c>` | override as relevant / borderline / not_relevant. `n` is the relation number from the codes line and `c` is h/m/l (`human_override`) |
| `u` | undo the latest decision (also from an earlier session) and show that pair again |
| `?` | help |
| `q` | quit; every decision is already saved. End of input (EOF) quits the same way |

Safeguards:
- There is no default, no auto-accept and no accept-all; an empty or unknown answer does nothing.
- An override equal to the proposal is refused (use `a`).
- `relevant` for a hard-violating product is refused.
- Every decision is written atomically and work resumes where it stopped.
- One session holds the exclusive `audit.lock`. A stale lock is never removed automatically.

The work directory is bound to the proposal manifest and the pool. Proposals and decisions are
stored separately: the proposal files are never modified. Each of the following fails closed:
- a wrong handle;
- a duplicate decision;
- an unknown pair;
- a changed proposal hash;
- a changed card;
- a changed pool or proposal manifest.

This is a **non-blind** audit: the auditor sees the proposal before deciding.

Other commands:
- `status`: progress counts.
- `validate`: re-check the decision file.
- `verify`: re-verify the generation.

## Audited assembly (tooling only; not run)

```text
uv run python -m evals.weak_labels assemble --annotator ann-1
uv run --offline python -m evals.weak_labels audit-report
uv run --offline python -m evals.weak_labels metrics
```

`assemble` runs only when all 100 decisions exist and no lock is held. It writes, exclusively:
- `audit_decisions.jsonl`;
- `weak_final_labels.jsonl`: 568 `WeakFinalLabel` records. Audited pairs take the human outcome;
  unaudited pairs keep the machine proposal as `machine_unreviewed`;
- `weak_dev_sealed.json`, last. It records the banner and file hashes, sets
  `human_reviewed_golden_dataset=false`, `final_test_evidence=false`, `blind_audit=false` and
  `consistency_measured=false`, and holds the counts and disclosures.

It never creates or imitates strict primary judgements, `primary_sealed`, a consistency sample,
`labels_frozen` or `provenance_unsealed`.

`audit-report` writes `reports/audit_report.{json,md}`. It covers:
- accept and override rates;
- agreement by proposed label and by proposal confidence (exact and label-only);
- the proposed × final confusion matrix;
- per-query coverage;
- audited, unaudited and machine-only counts and rates;
- limitations.

It claims no accuracy for unaudited labels.

`metrics` writes `reports/provisional_metrics.{json,md}`. It re-scores the six pooled systems from
their top-20 lists in the verified private provenance with the `eval-1` definitions:
- Precision@5/10 and Recall@5/10 (strict and lenient);
- MRR@10 and NDCG@5/10;
- CSR (hard and fp-1);
- irrelevant-result rate and zero-result rate;
- per-query and macro values.

Every report carries the provisional banner. Nothing is written to an M10 report path.

## Commands executed in this session

| Command | Result |
|---|---|
| `uv run --offline python -m evals.weak_labels propose` | Two fresh runs byte-identical; proposals written; manifest `9dc1fab2…` |
| `uv run --offline python -m evals.weak_labels verify` | Verified: 568 proposals, 100 audit pairs, 319 flagged for review |
| `uv run python -m evals.weak_labels status` | 0 of 100 audit pairs decided |
| `uv run --offline python -m evals.weak_labels assemble-machine-only` (no flag) | Refused: `--acknowledge-unaudited` is required (exit 1) |
| `uv run --offline python -m evals.weak_labels assemble-machine-only --acknowledge-unaudited` | Two fresh-process builds byte-identical; `weak_machine_dev_sealed`, 568 machine_unreviewed labels |
| `uv run --offline python -m evals.weak_labels verify-machine-only` (twice) | Every machine-only file regenerated byte for byte |

## Judgment calls (policy choices, open to revision)

- **Headphones as a complement for phone and laptop queries.** No accessory category exists in
  the catalog, so `complement_category` only reaches headphones. The label stays
  `not_relevant`; only the relation differs.
- **Tertile bands inside the query's pool, not absolute logit thresholds.** On the unlabeled
  distribution, logits vary strongly by query: Hinglish and price queries score low across the
  board. No human label was used to choose these bands.
- **Near-miss thresholds** (10% over the price bound, one ×2 capacity step, unisex for a gendered
  query) and the **semantic evidence rules** (dedicated GPU for gaming; business subcategory or
  "office" in the description for office) are fixed policy choices, not learned values.
- **Queue order** is by query, then slot. The auditor therefore sees five pairs of one query in a
  row.

## Machine-only finalization (`evals/weak_labels/machine_only.py`)

```text
uv run --offline python -m evals.weak_labels assemble-machine-only --acknowledge-unaudited
uv run --offline python -m evals.weak_labels verify-machine-only
```

**Identity.** The output identity, recorded in `machine-only/manifest.json`
(`MachineDevSealed`, deterministic, no wall-clock time):

| Field | Value |
|---|---|
| `status` | `weak_machine_dev_sealed` |
| `label_basis` | `deterministic_machine_weak_labels` |
| `policy_version` | `weak-label-policy-1` |
| `human_audit_coverage` | `0` |
| `provisional` | `true` |
| `abandoned_human_decision_count` | `2` |
| `abandoned_human_decisions_included` | `false` |

It is not `weak_dev_sealed`, `primary_sealed`, `labels_frozen` or `provenance_unsealed`.

**Refusals.** The command requires `--acknowledge-unaudited`. It refuses when any of these holds:
- an audit lock or any audit decision is active;
- the audit root points into the abandoned-audit archive;
- the archive metadata is missing, or does not record exactly two excluded decisions;
- the proposals, pool, cards or private signals binding do not verify;
- a strict judgement, seal or consistency file exists;
- the outputs already exist;
- the two fresh-process builds differ.

The archived decision files are never opened. Only the archive's metadata record is checked.

**Labels.** `labels.jsonl` holds 568 `MachineWeakLabel` records. Every record has:
- `label_source=machine_unreviewed`, `human_audited=false` and `proposal_source=weak-label-policy-1`;
- the label, relation and heuristic confidence;
- the rule, the `requires_human_review` flag and the signal-conflict flag;
- the proposal, proposal-manifest, pool-manifest, card and content hashes;
- the catalog, model, policy and rubric versions.

No record has an annotator handle, a human timestamp, an acceptance or override, or an agreement
field, and no record carries `ann-1`.

| Count | Value |
|---|---|
| Labels | 212 relevant, 61 borderline, 295 not_relevant |
| Relations | 119 exact_match, 93 satisfies_intent, 49 substitute, 12 partial, 6 complement, 265 off_target, 24 out_of_catalog |
| Heuristic confidence (all unaudited) | 249 high, 198 medium, 121 low |
| Deterministic-rule only / model-assisted | 526 / 42 |
| `requires_human_review` | 319 |
| Machine-only coverage / human audit coverage | 568/568 / 0/568 |

Every label and relation comes from the deterministic rules. "Model-assisted" counts the
records whose heuristic confidence was lowered because the local cross-encoder and retrieval
consensus disagreed with the rule outcome (`signal_conflict`).

**Metrics.** V0 = `v0_lexical`, V1 = `v1_hybrid`, V2 = `v2_filtered`, V3 = `v3_reranked_d20` (the
configured default `search_rerank_depth=20`). The pooling-only sources `dense_only` and
`v3_reranked_d10` are not reported.

Each version is scored from its top-20 list in the verified private pool provenance with the
`eval-1` definitions (strict and lenient, hard and fp-1 CSR, per-query and macro). Values are
macro means over the queries where the metric is defined (`n`).

| Metric | V0 | V1 | V2 | V3 |
|---|---|---|---|---|
| Precision@5 (strict) | 0.4500 | 0.6100 | 0.7000 | 0.7100 |
| Precision@10 (strict) | 0.3950 | 0.5500 | 0.6300 | 0.6250 |
| Precision@10 (lenient) | 0.4000 | 0.6400 | 0.6850 | 0.6750 |
| Recall@5 (strict, n=17) | 0.2914 | 0.4103 | 0.4962 | 0.4797 |
| Recall@10 (strict, n=17) | 0.4348 | 0.6231 | 0.7358 | 0.7299 |
| MRR@10 (strict, n=17) | 0.5588 | 0.8647 | 1.0000 | 0.9314 |
| NDCG@5 (n=19) | 0.4943 | 0.7450 | 0.8498 | 0.8247 |
| NDCG@10 (n=19) | 0.4756 | 0.7584 | 0.8449 | 0.8150 |
| CSR@10 hard | 1.0000 (n=10) | 0.6263 (n=19) | 0.8722 (n=18) | 0.8722 (n=18) |
| CSR@10 fp-1 | 1.0000 (n=10) | 0.6737 (n=19) | 0.9444 (n=18) | 0.9444 (n=18) |
| Irrelevant-result rate@10 | 0.0000 (n=10) | 0.3600 (n=20) | 0.2105 (n=19) | 0.2211 (n=19) |
| Zero-result rate | 0.5000 | 0.0000 | 0.0500 | 0.0500 |

The full table (lenient values, per-query values) is in
`machine-only/reports/provisional-v0-v3.md` and `machine-only/metrics/*.json`. Two cautions for
reading it:
- V0 returns nothing for 10 of the 20 queries, so its CSR and irrelevant-result rate cover only
  10 queries.
- These values do not show that any version is better: the labels are unaudited heuristics
  (see the limitations).

**Outputs.** `evals/weaksets/internal-search/v1/machine-only/` holds:
- `labels.jsonl`, `manifest.json` and `proposal-summary.json`;
- `metrics/v0.json`, `metrics/v1.json`, `metrics/v2.json`, `metrics/v3.json` and
  `metrics/comparison.json`;
- `reports/provisional-v0-v3.md`.

Every metric file and the report carry the mandatory warning and the metadata: label basis,
human audit coverage 0, abandoned-decision count 2, provisional, policy version, and the
proposal and pool manifest hashes. No human statistic is reported anywhere: no agreement,
accuracy, override rate, confusion matrix or annotator consistency.

**Determinism.** `assemble-machine-only` built all nine files in two fresh child processes and
in-process. It wrote only after all three were byte-identical (`machine-only/manifest.json`
SHA-256 `f021d9709572ec32295e310639d5aa9e89a140e97fa532e75cb2635779ebd95f`).
`verify-machine-only`, run afterwards in two further fresh processes, regenerated every file byte
for byte.

The operational record (`assembled_at`, run facts) is in the git-ignored
`data/processed/weak_labels/4c26628ac45cd8dd/machine-only-run.json`, outside the deterministic
bytes.

## Abandoned audit archive

The user had opened the non-blind audit, then chose the machine-only track and quit with `q`.
With the user's approval, the complete audit work directory was moved atomically and unedited
from `data/processed/weak_audit/9dc1fab28fbd89e9/` to the git-ignored
`data/processed/weak_audit_archive/abandoned-2026-10-05-9dc1fab28fbd89e9/`. It contains two
`ann-1` acceptances, both for `gq-p01`: `SYN-HDP-0045` and `SYN-HDP-0029`.

| File | SHA-256 (identical before and after the move) |
|---|---|
| `audit_decisions.jsonl` | `5a507df49409c585161af6d775da3a905e9ae791c7106cefb526af46f79d81d1` |
| `binding.json` | `d804d88dc383f845517dd4ac81ad795ea138c2e7274cb566d6c58d33fac494fc` |

The metadata record `abandoned-2026-10-05-9dc1fab28fbd89e9.archive.json`, beside the folder,
records:
- `status: abandoned_partial_audit` and `decision_count: 2`;
- `included_in_machine_labels`, `included_in_metrics` and `included_in_human_audit_statistics`
  all `false`;
- both paths, the file hashes and `archived_at`.

The archive is never committed and never imported. The active audit has 0 decisions.

## Limitations

- **Unaudited machine labels.** All 568 labels are unaudited machine-generated weak labels
  (human audit coverage 0/568). Their accuracy is unknown, and every metric is a provisional
  development diagnostic.
- **Labels can favour some versions.** The heuristics reuse the retrieval signals and the gold
  constraints that V2/V3 filter on, which may favour those versions.
- **Abandoned decisions.** Two started human audit decisions were abandoned, archived and
  excluded.
- The audit-related limitations below (anchoring, sampling, exposure) apply only if a human
  audit is ever run.
- **Non-blind audit.** The auditor sees the proposal, so agreement is biased upward (anchoring)
  and is not an accuracy estimate.
- **Unrepresentative sample.** The 100 pairs are stratified, not uniform. Rates do not extrapolate
  to the 468 unaudited pairs, which stay unreviewed machine proposals; no accuracy is claimed for
  them.
- **One auditor.** `ann-1` is the only auditor. There is no inter-annotator agreement and no
  delayed intra-annotator consistency measurement.
- **Not test evidence.** The 20 pilot queries are development/rubric-calibration data, not a
  held-out test set. Pooled recall is biased where more than 25 products are eligible.
- **Effect on the strict track.** Auditing these proposals exposes `ann-1` to machine labels for
  100 pooled pairs, and the committed proposals cover all 568. Any later strict blind annotation
  of the same pool is no longer fully blind for this annotator. This is disclosed here and must
  be disclosed in any later strict report.
- **The cross-encoder** is the provisional M8 compatibility pin (ADR-007), not a quality winner.
- **Byte identity** held across two runs on this machine with the recorded library versions. It
  is not claimed across other hardware or library versions.
- **Private files stay local.** The private signals exist only in this working copy's ignored
  `data/processed/`.
