"""Cross-encoder reranker model specification (pure).

A spec pins everything that changes what a reranker score means: model id, immutable revision,
maximum pair length in tokens, the number of output labels and the score type. Together with the
reranker document version it is hashed into `config_sha256()`.

This module holds the specification type and its validation only. There is no model registry
and no default model here: a model is pinned only after its snapshot, licence and compatibility
have been reviewed (an ADR plus a reviewed code change).

Scores are raw single logits (`score_type = "logit"`): uncalibrated, not probabilities, and only
comparable within one query and one model revision.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from ecommerce_search.embeddings.spec import SpecError, check_model_id, check_revision
from ecommerce_search.reranking.documents import RERANK_DOCUMENT_VERSION

ScoreType = Literal["logit"]
SCORE_TYPES: frozenset[str] = frozenset({"logit"})
MAX_LENGTH_LIMIT = 8192  # generous upper bound for a pair length; real models are far below it


@dataclass(frozen=True)
class RerankerModelSpec:
    model_id: str
    revision: str
    max_length: int
    num_labels: int = 1
    score_type: ScoreType = "logit"

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not isinstance(self.revision, str):
            raise SpecError("model id and revision must be strings")
        check_model_id(self.model_id)
        check_revision(self.revision)
        if (
            isinstance(self.max_length, bool)
            or not isinstance(self.max_length, int)
            or not 1 <= self.max_length <= MAX_LENGTH_LIMIT
        ):
            raise SpecError(f"max_length must be an integer between 1 and {MAX_LENGTH_LIMIT}")
        if type(self.num_labels) is not int or self.num_labels != 1:
            raise SpecError("num_labels must be 1: the reranker returns one relevance logit")
        if not isinstance(self.score_type, str) or self.score_type not in SCORE_TYPES:
            raise SpecError("score_type must be 'logit'")

    def config(self) -> dict:
        return {
            "model_id": self.model_id,
            "revision": self.revision,
            "max_length": self.max_length,
            "num_labels": self.num_labels,
            "score_type": self.score_type,
            "rerank_document_version": RERANK_DOCUMENT_VERSION,
        }

    def config_sha256(self) -> str:
        canonical = json.dumps(self.config(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
