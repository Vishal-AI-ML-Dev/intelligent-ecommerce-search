"""A deterministic, offline stand-in for a cross-encoder reranker (tests only; no torch, no files).

Score = the number of distinct lower-cased `[a-z0-9]+` query tokens that also occur in the
document, as a float. Pair token count = query words + document words + 3 (like the special
tokens of a wordpiece pair). This exercises reranking plumbing only. It says nothing about the
quality of any real model, and `FAKE_SPEC` is a test-local specification, not a reviewed pin.
"""

import re
from collections.abc import Callable, Sequence

from ecommerce_search.reranking.provider import RerankerUnavailable
from ecommerce_search.reranking.spec import RerankerModelSpec

TOKEN = re.compile(r"[a-z0-9]+")

# Test-local only: not a real model and not part of any registry.
FAKE_SPEC = RerankerModelSpec(
    model_id="test-org/fake-reranker",
    revision="0123456789abcdef0123456789abcdef01234567",
    max_length=512,
)


def overlap_score(query: str, document: str) -> float:
    return float(len(set(TOKEN.findall(query.lower())) & set(TOKEN.findall(document.lower()))))


class FakeReranker:
    def __init__(self, spec: RerankerModelSpec = FAKE_SPEC) -> None:
        self._spec = spec
        self.loaded = False
        self.load_calls = 0
        self.count_calls: list[tuple[str, list[str]]] = []
        self.score_calls: list[tuple[str, list[str]]] = []
        # Controlled failures: stage name ("load", "count", "score") -> exception to raise.
        self.failures: dict[str, BaseException] = {}
        # Replaces the computed scores / token counts (may be malformed on purpose).
        self.score_output: Callable[[str, list[str]], object] | None = None
        self.token_output: Callable[[str, list[str]], object] | None = None

    @property
    def spec(self) -> RerankerModelSpec:
        return self._spec

    def _fail(self, stage: str) -> None:
        if stage in self.failures:
            raise self.failures[stage]

    def load(self) -> float | None:
        self.load_calls += 1
        self._fail("load")
        if self.loaded:
            return None
        self.loaded = True
        return 1.0

    def count_pair_tokens(self, query: str, documents: Sequence[str]) -> list[int]:
        texts = list(documents)
        self.count_calls.append((query, texts))
        if not self.loaded:
            raise RerankerUnavailable("load_failed")
        self._fail("count")
        if self.token_output is not None:
            return self.token_output(query, texts)  # type: ignore[return-value]
        base = len(TOKEN.findall(query.lower())) + 3
        return [base + len(TOKEN.findall(text.lower())) for text in texts]

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        texts = list(documents)
        self.score_calls.append((query, texts))
        if not self.loaded:
            raise RerankerUnavailable("load_failed")
        self._fail("score")
        if self.score_output is not None:
            return self.score_output(query, texts)  # type: ignore[return-value]
        return [overlap_score(query, text) for text in texts]
