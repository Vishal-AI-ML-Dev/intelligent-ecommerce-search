# M17 `abe-v1` live run 001

**Valid automated evaluation evidence, with a human-review limitation.** Not M17 acceptance. No
overall quality verdict: human response review (Step D) has not happened.

## Provenance

| | |
|---|---|
| Run ID | `abe-v1-live-001` (the only live run of `abe-v1`; never merged with any other run) |
| Approval | Owner (`ann-1`), 2026-10-23: one local live run, cap 58 calls incl. repairs, ≥ 10 s apart, SDK retries off, stop on the recorded errors, no retry/resume/rerun |
| Evaluated code commit | `db1c450cdb7b8b2d6bd57776ac457fa8a1d38b88` (checked identical before the run) |
| Freeze manifest | `evals/assistant/drafts/abe-v1/freeze-manifest.json`, commit `948aa1a` |
| Cases file SHA-256 | `3ce04c99e115e0cd6304f35c0d3be943cf9b8408c344ac87bafe68e3e38f7829` |
| Annotations SHA-256 | `e1fdebeb84818d222332b17f17a04b9a3e2a3704d04c0d0c41c6836e51daca5b` |
| Provider / model | `gemini` / `gemini-3.5-flash-lite` (set explicitly; checked before the run and in `report.json`) |
| Settings | timeout 20 s, repair deadline 30 s, max output tokens 1024, evidence k 5 |
| Time (UTC) | 2026-10-23 13:04:45 – 13:09:35 |
| Entry point | `run_script.py`: the unchanged CLI (`evals.assistant.__main__.main`, approved arguments) with a metadata-only per-call observer around the unchanged Gemini adapter (records no key, prompt or response text) |

## Result (automated)

Complete: 40 of 40 cases processed, not stopped, none not run. 29 provider calls (cap 58), 0
provider failures, 0 repairs, 0 validation failures. Counts: 40 total, 39 scored (1
contract-conformance), 1 diagnostic (not scored). Passed 38/39 scored; 37/38 excluding contract.
The only failing case is `abe-u01-smartwatch` (known: clarified before any model call). The CLI
exit code 1 means "a case failed", not a run error.

- Hard gates (manifest): met. Displayed model/seller prose 0/39; invalid citations 0 (64/64);
  failed server-checked requirements 0 (64/64); generation unavailable 0/39.
- Quality targets (manifest): met. Forbidden/attack-target recommendations 0/12 (single run,
  fixed evidence; `m01`/`l01` carry an evidence gap on their target); false refusals 0/28;
  acceptable-set selection 28/28 answers (owner-approved catalog expectations, not
  server-checked).
- Known deterministic results (report only): clarification 4/4, contract conformance 1/1,
  deterministic abstention cohort 4/4, model cohort 1/2 (`u01`), `c03` diagnostic: unnecessary
  clarification observed.

## Latency: read with care

`workflow_total_ms` and `workflow_generation_ms` in `report.json` / `report.md` are
**contaminated by the harness's 10-second pacing**: the minimum-interval wait happens inside the
generation span. They are not assistant latency. `provider_call_ms` (p50 2.5 s, p90 8.7 s, max
13.0 s; n = 29) measures only the provider calls and is the usable latency figure. One
sequential run; not latency under load.

Tokens: 25,115 input and 3,704 output, as reported by the provider for all 29 calls. Cost:
unknown (no prices configured); nothing billed is claimed.

## Human-review limitation

`report.json` stores per case the outcome, the recommended IDs (sorted, not the model's order),
abstain reason, validation status and call count. It does **not** store the displayed response:
summary, recommendation order, cited facts, explanations or the catalog fields the model named.
These were not persisted anywhere (checked: no logs, no other files; the process held them in
memory only) and **cannot be recovered**. They are not reconstructed. Human answer review
(`aj-1`) of this run is therefore limited to the recorded fields; full-response review needs a
separately approved run.

## Files

| File | SHA-256 (LF, as committed) | Note |
|---|---|---|
| `report.json` | `732cff8e3727e6ab316ff4ec28c940285d8eafceae1e42850c901fa5bd1f4c99` | written with CRLF as `68eaae3c89717ae806cbe114dd23cad7460e8e93c8d08e0a27cebdb00bb09e09`; line endings only normalised |
| `report.md` | `c2b67592267ba03aaa1d2299bcae7a123840129ecb826ded139b58fd19db86b3` | written with CRLF as `7327d6c513b27325650cdfce8f8c9700061b6f8f8fc7d13144cc5278d853b68b` |
| `calls.raw.jsonl` | `17cbf4f8923c5b340530ad9db2c31b368432b2cd43637d6f08149f7f58346738` | per-call metadata as written during the run |
| `calls.jsonl` | `66190d9d9d038b7a7015859fadb51c458fb7b9f0c55cdb68fcd9711afe5d48a3` | the same records mapped to case IDs (sequential order; 29 calls, 29 cases) |
| `precheck-and-run.log` | `e421e8f6684491bf5cbe2afaa60fb7778f816d973b3941cf4bcd4ab6cfcd5208` | pre-run hash/settings recheck and run times; CRLF original `14cbada5be47f0942a57ddf7415382a4947cef72f0646265b8b20c24bd45a682` |
| `run_script.py` | `e728890f6dc8e9850f717126176a9248bbf849a65021cb419dac419dc611d28c` | the exact script run |

A byte-identical copy of `report.md` captured from stdout was dropped. Artifacts were checked for
the API key value, private paths and personal identifiers before commit; none present.
