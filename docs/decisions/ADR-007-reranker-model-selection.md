# ADR-007: Cross-encoder reranker model selection: a provisional compatibility pin (Milestone 8)

## Status

**Draft (Milestone 8 Session C; independently reviewed offline in Session D; finalized in
Session H).** A provisional **compatibility** pin, not a relevance or quality selection.
**Milestone 10 must re-decide the reranker on the human-reviewed Golden Dataset.** The V3
reranked search path, its API exposure, deployment wiring and the latency benchmark belong to
later Milestone 8 sessions; no benchmark, latency or quality number is recorded here because
none has been measured.

## Date

2026-10-03

## Decision

| Setting | Value |
|---|---|
| Model | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| Immutable revision | `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| Declared licence | `apache-2.0` (evidence below) |
| Architecture | `BertForSequenceClassification`, `model_type: bert`, 6 hidden layers |
| Output | one label (`num_labels = 1`), score type `logit` |
| Score activation | `torch.nn.Identity` (raw logit; never a sigmoid) |
| Maximum pair length | 512 tokens (`max_position_embeddings` = tokenizer `model_max_length` = 512) |
| Reranker document | `rd-1` (embedding text v1, unchanged; `reranking/documents.py`) |
| Device | CPU |
| Configuration hash | `b30e3ddda48b373d0d929ee16aeec5a2282680b927e47340c5c58c7b299bdef2` (`RerankerModelSpec.config_sha256()`) |

Pinned in code as `MS_MARCO_MINILM_L6_V2`, the only entry of `RERANKER_MODELS`
(`DEFAULT_RERANKER_MODEL_ID`) in `src/ecommerce_search/reranking/spec.py`. Settings
(`RERANKER_MODEL_ID`, `RERANKER_MODEL_REVISION`) may only select that entry at that revision.
Changing any value requires a new ADR and a reviewed code change.

## Why one pinned model, not a "quality" comparison

The Golden Dataset (Milestone 9) does not exist yet and the catalogue is synthetic, so no
comparison of reranker models could support a relevance claim. Comparing candidates on
unlabelled probes would only look like evidence. One widely used, small, permissively licensed
English cross-encoder was therefore pinned to make the V3 plumbing, failure behaviour and latency
measurable on CPU. Nothing in this ADR says it is the best reranker for this catalogue; it may
even reorder worse than the fused V2 list. Milestone 10 decides, with labelled data, whether to
keep it, replace it or disable reranking.

## Compatibility gates and the evidence for each

| Gate | Evidence |
|---|---|
| Immutable revision | Resolved once from the official repository metadata; full 40-hex commit pinned; never a branch at runtime |
| Licence declared | `apache-2.0` in the model card's YAML front matter (`license: apache-2.0`) at the pinned revision, and the `license:apache-2.0` repository tag in the same metadata; recorded in the manifest as `declared_license`. Not legal advice |
| No remote code | No `.py` file in the repository listing; no `auto_map` in `config.json` or `tokenizer_config.json`; loaded with `trust_remote_code=False` |
| Not gated / private / disabled | Repository metadata: `gated: false`, `private: false`, `disabled: false` |
| Single-logit classifier | `id2label` has one entry; loaded model reports `num_labels == 1` |
| Raw logits | Upstream config sets `sbert_ce_default_activation_function` to `Identity`; the provider forces `Identity` anyway, and the real-model test proves each score equals the classifier's own unactivated output |
| No truncation | Pair lengths counted with the model tokenizer (special tokens included, `truncation=False`); any pair above 512 is refused (`input_too_long`) before inference |
| `rd-1` fits | Real-model test: all 240 seed `rd-1` documents paired with every committed hybrid-benchmark query, and with a worst-case 200-character all-punctuation query (the default `SEARCH_MAX_QUERY_LENGTH`), are at most 512 tokens |
| CPU, offline | Loaded on `cpu` with `local_files_only=True` and hub offline mode, after offline manifest verification; real-model tests block sockets for the whole module |
| Deterministic | Real-model tests: repeated identical calls in one process are bitwise equal; each batched score equals the classifier's single-pair unactivated output within 1e-4, and reversing the input order changes no score beyond 1e-4. Not asserted across processes, machines or library versions |
| Integrity | Strict snapshot profile below, re-verified before every load and by `model-verify --kind reranker` |

Measured pair lengths (observations, not a quality signal): longest seed `rd-1` pair 138 tokens
with an empty query, 143 with the committed benchmark queries, 338 with the 200-character
worst-case query.

## Snapshot profile and manifest

`RERANKER_PROFILE` in `src/ecommerce_search/embeddings/fetch.py` (strict) downloads and requires
exactly these seven files. It excludes `.gitattributes`, `pytorch_model.bin`, `flax_model.msgpack`
and every `onnx/*` and `openvino/*` file listed at that revision. The repository has no
`modules.json`, `LICENSE` or Python file at that revision.

| File | Bytes | SHA-256 |
|---|---:|---|
| `README.md` | 3,673 | `7c0ec39941d0d1d766ccde671f592971d82fe45b7eea550a80d1ea864ebc3baa` |
| `config.json` | 794 | `380e02c93f431831be65d99a4e7e5f67c133985bf2e77d9d4eba46847190bacc` |
| `model.safetensors` | 90,870,598 | `821d1aa69520101d6e0737f78a042ae25b19e5cb9160701909d10434f4aeb0ae` |
| `special_tokens_map.json` | 132 | `3c3507f36dff57bce437223db3b3081d1e2b52ec3e56ee55438193ecb2c94dd6` |
| `tokenizer.json` | 711,396 | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `tokenizer_config.json` | 1,330 | `a5c2e5a7b1a29a0702cd28c08a399b5ecc110c263009d17f7e3b415f25905fd8` |
| `vocab.txt` | 231,508 | `07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3` |
| **Total** | **91,819,431** | |

The `model.safetensors` SHA-256 equals the LFS SHA-256 published in the repository metadata.
Every other file was cross-checked offline against the size and git blob id in that metadata.
All seven SHA-256 values are pinned in code (`RERANKER_PINNED_SHA256`). The strict profile
rejects missing files, any extra file, any pinned-hash mismatch, a non-object or invalid
`config.json`, and any link (symbolic link or Windows directory junction) that could leave the
model's hub folder: the model folder, `snapshots/` and the snapshot directory must be real
directories, no directory inside the snapshot may be a link, and a file link must resolve inside
the model folder (it cannot be dangling or looping). Links are checked before any recursive
listing, so a looping link is never followed.

The snapshot lives under the git-ignored `models/hub/models--cross-encoder--ms-marco-MiniLM-L6-v2/snapshots/<revision>/`.
Its manifest, in the existing version 1 format, lives at
`models/manifests/models--cross-encoder--ms-marco-MiniLM-L6-v2@<revision>.json`.
Neither is committed.

## Network operations (Session C, the only network use)

All ran without credentials (no token, no login; every Hugging Face cache, token and log location
confined to the ignored `models/` directory), against the approved repository only. Two
high-level operations were approved. Both succeeded after transient connection resets:

1. **Metadata and immutable revision** (`HfApi.model_info(..., revision="main",
   files_metadata=True, token=False)`; `main` was used only to discover the commit, which is
   what is pinned).
   - Attempt 1 failed: `ConnectError` (`WinError 10054`, connection reset during the TLS
     handshake with `huggingface.co`), nothing received.
   - Attempt 2 succeeded.
2. **Snapshot download at the pinned commit with the seven-file allowlist**
   (`snapshot_download` via `fetch_snapshot(..., profile=RERANKER_PROFILE)`).
   - Attempts 1 and 2 (`model-fetch --kind reranker`) failed with a sanitized `download failed
     (ConnectError)`, and nothing was written.
   - Attempt 3 (the same call, reporting the failing host instead of a fully sanitized message)
     succeeded. During it, `huggingface_hub` itself retried two connection resets on a `HEAD`
     to `huggingface.co/.../resolve/<revision>/special_tokens_map.json`.
   - Hosts contacted: `huggingface.co` (API and `resolve`) and, according to the local `hf_xet`
     log, the Hugging Face Xet delivery host `cas-server.xethub.hf.co`. Both are
     Hugging Face-controlled. No other repository or host was requested.

Session D could corroborate offline only the Xet host, from the local `hf_xet` log under the
ignored `models/.hf_home/`. The `huggingface.co` attempt counts above are Session C's own record.

No further network access followed. All later work (verification, tests) ran with
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`. Request-time
search never uses the network.

## Runtime contract (concrete provider)

`src/ecommerce_search/reranking/cross_encoder_provider.py` (`CrossEncoderReranker`):

- **Lazy construction and loading.** Construction does no file, model or network work, and
  importing the module, the reranking package or the API loads no torch,
  sentence-transformers or transformers. The first `load()` re-verifies the manifest and the
  strict profile, checks the snapshot configuration against the spec and the reviewed
  architecture, forces offline mode, and only then imports the libraries and loads
  `CrossEncoder(..., device="cpu", local_files_only=True, trust_remote_code=False,
  activation_fn=Identity)` in evaluation mode. A failed load is not cached.
- **Locks.** A load lock makes each instance load at most once. An inference lock serializes
  token counting and scoring per instance.
- **Scoring.** One raw logit per document, as plain Python floats, in input order, under
  inference mode, with no threshold. An empty document list does no model work.
- **Fixed failures** (`RerankerUnavailable`). No message carries query or document text, paths,
  library exception text or tracebacks, and nothing is logged by the provider.

  | Reason | Cause |
  |---|---|
  | `snapshot_missing` | Snapshot or manifest absent |
  | `load_failed` | Integrity failure or library load failure |
  | `config_mismatch` | Snapshot or loaded model contradicts the spec |
  | `input_too_long` | A pair longer than 512 tokens |
  | `invalid_output` | Malformed tokenizer or model output, or non-finite scores |
  | `score_failed` | Tokenizer or inference failure |

  Wrong argument types are programming errors (`TypeError`) and are not converted.

## Score semantics and limitations

- Scores are **raw logits**: uncalibrated, not probabilities, comparable only within one query
  and this model revision. No threshold may be derived from them, and a low score must never
  become a hard filter or a rejection.
- The model was trained on English MS MARCO web passages, not on e-commerce products, and has
  not been evaluated on this synthetic catalogue. Domain mismatch (specification-heavy titles,
  units, brand names, Indian price contexts) is expected. Non-English queries are out of scope.
- The 512-token limit is enforced by refusal, not truncation. A deployment that raises
  `SEARCH_MAX_QUERY_LENGTH` far above its default could produce refused pairs. Session C did not
  measure this beyond the 200-character worst case above.
- An empty document is scored as `[CLS] query [SEP] [SEP]` by the library's batched pair
  encoding (a bare single-pair tokenizer call would drop the empty segment). The provider's pair
  count follows the library's batched form. `rd-1` documents are never empty: their builder
  refuses empty text.
- Upstream metadata: `config.json` carries `"_name_or_path": "cross-encoder/ms-marco-MiniLM-L-12-v2"`.
  This is historical metadata only. The pinned files report 6 hidden layers, and identity is
  the repository plus the immutable commit plus the pinned file hashes. The field is neither
  checked nor rewritten.

## Consequences

- **No migration.** Reranking stores nothing. Migration head stays `0004`.
- New settings, validated against the registry: `RERANKER_ENABLED` (default `true`),
  `RERANKER_MODEL_ID`, `RERANKER_MODEL_REVISION`, `RERANKER_BATCH_SIZE` (default 16, 1–256) and
  `SEARCH_RERANK_DEPTH` (default 20, 1–1000, must not exceed `SEARCH_CANDIDATE_K`).
  - The depth bound applies even when reranking is disabled, so every accepted configuration is
    valid as a whole (approved plan §10).
  - Consequence: a deployment that sets `SEARCH_CANDIDATE_K` below 20 must also set
    `SEARCH_RERANK_DEPTH` to at most that value, or settings validation fails for every
    endpoint. V0–V2 request behaviour is unchanged.
  - Compose and `.env.example` wiring is a later session.
- `model-fetch --kind reranker` fetches only the registry pin. `model-verify --kind
  embedding|reranker` re-checks a snapshot offline. `--kind embedding` remains the default and
  the embedding fetch behaviour is unchanged.
- `real_model` tests now also require the reranker snapshot.

## Follow-up

- Later Milestone 8 sessions: V3 reranked search, API and deployment wiring, and the
  **latency-only** benchmark of reranker disabled / depth 10 / depth 20 (approved decision D8,
  Session G). It also reports determinism and descriptive rank movement, and never selects a
  quality winner. Record measured numbers there, not here.
- Milestone 10: compare no reranker / depth 10 / depth 20 on the human-reviewed Golden Dataset,
  and re-decide the reranker (or no reranker).

## References

- ADR-002 (hybrid retrieval and RRF), ADR-004 and ADR-006 (local model strategy and the
  embedding pin whose snapshot tooling this reuses).
- `docs/MASTER_PLAN.md`, sections "Reranking" and "Milestone 8 — Reranking".
