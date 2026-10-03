"""`RerankerProvider` backed by the pinned local cross-encoder snapshot (ADR-007). CPU only.

This is the only reranking module that imports torch / sentence-transformers, and it does so
lazily on the first `load()`; importing it (or the reranking package, or the API) loads neither.
Constructing the provider is cheap and touches no files.

Loading never contacts the network: the snapshot is first re-verified offline against its
manifest and the strict reranker profile (exact file set, pinned SHA-256, confined links) and its
configuration is checked against the spec; only then is the model read from that explicit local
directory with hub offline mode forced, `local_files_only=True` and `trust_remote_code=False`.
A failed load is not cached: the next call tries again (for example after `model-fetch`).

Scores are the model's raw single logit (`torch.nn.Identity` activation, never a sigmoid), in
input order, one per document, with no threshold. Every pair is counted with the model's own
tokenizer, special tokens included and without truncation; a pair longer than the pinned
`max_length` is refused with `input_too_long` before inference, so nothing is ever silently
truncated. One instance loads at most once (a load lock); scoring and token counting are
serialized per instance (an inference lock), because fast tokenizers are not safe for concurrent
use and the API serves requests from a thread pool.

Failures raise `RerankerUnavailable` with a fixed reason code. Neither exception messages nor
logs carry query or document text, paths, library exception text or tracebacks.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ecommerce_search.embeddings.fetch import MISSING_PROBLEM, RERANKER_PROFILE, verify_manifest
from ecommerce_search.embeddings.spec import snapshot_dir
from ecommerce_search.reranking.provider import (
    RerankerUnavailable,
    validate_scores,
    validate_token_counts,
)
from ecommerce_search.reranking.spec import MS_MARCO_MINILM_L6_V2, RerankerModelSpec

# The reviewed architecture of each registry entry (ADR-007), checked offline in the snapshot's
# `config.json` before loading. The upstream `_name_or_path` field (which names the L-12 parent
# model) is historical metadata, not identity, and is deliberately not checked.
REVIEWED_ARCHITECTURES: dict[str, dict[str, object]] = {
    MS_MARCO_MINILM_L6_V2.model_id: {
        "architectures": ["BertForSequenceClassification"],
        "model_type": "bert",
        "num_hidden_layers": 6,
    },
}

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}
# The library logs the absolute snapshot path at INFO when it loads a model; its INFO output is
# dropped, warnings and errors are kept.
LIBRARY_LOGGER = "sentence_transformers"
LIBRARY_LOG_LEVEL = logging.WARNING
DEVICE = "cpu"
MAX_BATCH_SIZE = 256


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def snapshot_config_problems(models_dir: Path, spec: RerankerModelSpec) -> list[str]:
    """Offline: does the snapshot's own configuration match `spec`? Returns fixed messages.

    Checks the reviewed architecture of the registry entry, the number of labels (one relevance
    logit), that the tokenizer's `model_max_length` equals the pinned `max_length` and that the
    position embeddings cover it, and that no remote code is referenced. Reads two JSON files;
    imports no model library."""
    snapshot = snapshot_dir(models_dir, spec.model_id, spec.revision)
    config = _read_json(snapshot / "config.json")
    tokenizer = _read_json(snapshot / "tokenizer_config.json")
    if not isinstance(config, dict) or not isinstance(tokenizer, dict):
        return ["model or tokenizer configuration is missing or not valid JSON"]
    problems = []
    expected = REVIEWED_ARCHITECTURES.get(spec.model_id)
    if expected is None:
        problems.append("no reviewed architecture is recorded for this model")
    elif any(config.get(key) != value for key, value in expected.items()):
        problems.append("model architecture differs from the reviewed architecture")
    id2label = config.get("id2label")
    labels = len(id2label) if isinstance(id2label, dict) else config.get("num_labels")
    if labels != spec.num_labels:
        problems.append("model configuration does not have exactly one output label")
    positions = config.get("max_position_embeddings")
    if type(positions) is not int or positions < spec.max_length:
        problems.append("model position embeddings do not cover the pinned max_length")
    if tokenizer.get("model_max_length") != spec.max_length:
        problems.append("tokenizer model_max_length differs from the pinned max_length")
    if "auto_map" in config or "auto_map" in tokenizer:
        problems.append("configuration references remote code")
    return problems


def _check_inputs(query: object, documents: object) -> list[str]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if isinstance(documents, str | bytes) or not isinstance(documents, Sequence):
        raise TypeError("documents must be a sequence of strings")
    texts = list(documents)
    if not all(isinstance(text, str) for text in texts):
        raise TypeError("documents must be a sequence of strings")
    return texts


class CrossEncoderReranker:
    def __init__(self, spec: RerankerModelSpec, models_dir: Path, batch_size: int = 16) -> None:
        if not isinstance(spec, RerankerModelSpec):
            raise TypeError("spec must be a RerankerModelSpec")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= MAX_BATCH_SIZE
        ):
            raise ValueError(f"batch_size must be an integer between 1 and {MAX_BATCH_SIZE}")
        self._spec = spec
        self._models_dir = Path(models_dir)
        self._path = snapshot_dir(self._models_dir, spec.model_id, spec.revision)
        self._batch_size = batch_size
        self._model: Any = None
        self._torch: Any = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    @property
    def spec(self) -> RerankerModelSpec:
        return self._spec

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> float | None:
        """Load the model if needed. Returns the load time in ms, or None if already loaded."""
        if self._model is not None:
            return None
        with self._load_lock:
            if self._model is not None:
                return None
            started = time.perf_counter()
            model, torch = self._load_model()
            self._torch = torch
            self._model = model
            return round((time.perf_counter() - started) * 1000, 3)

    def _verify_snapshot(self) -> None:
        try:
            problems = verify_manifest(
                self._models_dir, self._spec.model_id, self._spec.revision, profile=RERANKER_PROFILE
            )
        except OSError:
            raise RerankerUnavailable("load_failed") from None
        if problems == [MISSING_PROBLEM]:
            raise RerankerUnavailable("snapshot_missing")
        if problems:
            raise RerankerUnavailable("load_failed")
        if snapshot_config_problems(self._models_dir, self._spec):
            raise RerankerUnavailable("config_mismatch")

    def _load_model(self) -> tuple[Any, Any]:
        self._verify_snapshot()
        os.environ.update(OFFLINE_ENV)
        logging.getLogger(LIBRARY_LOGGER).setLevel(LIBRARY_LOG_LEVEL)
        try:
            import torch
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(
                str(self._path),
                device=DEVICE,
                trust_remote_code=False,
                local_files_only=True,
                max_length=self._spec.max_length,
                activation_fn=torch.nn.Identity(),
            )
            model.eval()
        except Exception:  # any library failure is the same sanitized outcome
            raise RerankerUnavailable("load_failed") from None
        try:
            compatible = (
                model.num_labels == self._spec.num_labels
                and type(model.activation_fn) is torch.nn.Identity
                and model.tokenizer.model_max_length == self._spec.max_length
                and model.max_seq_length == self._spec.max_length
                and str(model.device) == DEVICE
            )
        except Exception:
            raise RerankerUnavailable("load_failed") from None
        if not compatible:
            raise RerankerUnavailable("config_mismatch")
        return model, torch

    def _raw_pair_counts(self, query: str, texts: list[str]) -> list[int]:
        """Token count of every (query, document) pair; caller holds the inference lock."""
        try:
            encoded = self._model.tokenizer(
                [query] * len(texts),
                texts,
                add_special_tokens=True,
                truncation=False,
                padding=False,
                return_attention_mask=False,
                return_token_type_ids=False,
                verbose=False,
            )
        except Exception:
            raise RerankerUnavailable("score_failed") from None
        try:
            ids = encoded["input_ids"]
            counts = [len(row) for row in ids]
        except Exception:  # malformed tokenizer output
            raise RerankerUnavailable("invalid_output") from None
        if len(counts) != len(texts):
            raise RerankerUnavailable("invalid_output")
        return counts

    def count_pair_tokens(self, query: str, documents: Sequence[str]) -> list[int]:
        """Pair token counts, special tokens included, never truncated (not length-checked)."""
        texts = _check_inputs(query, documents)
        if not texts:
            return []
        self.load()
        with self._infer_lock:
            return self._raw_pair_counts(query, texts)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """One raw logit per document, in input order. Over-length pairs are refused first."""
        texts = _check_inputs(query, documents)
        if not texts:
            return []
        self.load()
        torch = self._torch
        with self._infer_lock:
            validate_token_counts(
                self._raw_pair_counts(query, texts),
                count=len(texts),
                max_length=self._spec.max_length,
            )
            try:
                with torch.inference_mode():
                    raw = self._model.predict(
                        [(query, text) for text in texts],
                        batch_size=self._batch_size,
                        activation_fn=torch.nn.Identity(),
                        apply_softmax=False,
                        convert_to_numpy=True,
                        convert_to_tensor=False,
                        show_progress_bar=False,
                    )
            except Exception:
                raise RerankerUnavailable("score_failed") from None
        return validate_scores(_to_list(raw, len(texts)), count=len(texts))


def _to_list(raw: Any, count: int) -> object:
    """A one-dimensional array of `count` scores as a list of Python floats, else the
    `invalid_output` failure."""
    try:
        shape = tuple(raw.shape)
        values = raw.tolist()
    except Exception:
        raise RerankerUnavailable("invalid_output") from None
    if shape != (count,):
        raise RerankerUnavailable("invalid_output")
    return values
