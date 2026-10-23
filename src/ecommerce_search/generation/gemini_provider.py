"""Optional Gemini generation adapter (Milestone 16, ADR-009). The only module importing the
`google-genai` SDK; imported only when `ASSISTANT_GENERATION_PROVIDER=gemini`.

Bounds: one HTTP attempt per call (SDK retries explicitly off), the SDK's request timeout, a
fixed `max_output_tokens`. The timeout is per HTTP request, not a wall-clock bound on the whole
call; the assistant workflow adds a deadline check before any repair call. Never verified
against the live API in Milestone 16: every test uses a fake client, and the model's
structured-output support and free-tier limits are unverified. Nothing here logs the key,
prompt or response.
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import SecretStr, ValidationError

from ecommerce_search.generation.provider import (
    FACT_FIELDS,
    GenerationFailure,
    GenerationRequest,
    GenerationResult,
    GroundedDraft,
)

PROVIDER_NAME = "gemini"

# Hand-written JSON schema for structured output. Only basic keywords are used (Gemini strips
# length keywords); lengths and counts are enforced by `GroundedDraft` after parsing.
DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["answer", "abstain"]},
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "relevant_facts": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(FACT_FIELDS)},
                    },
                },
                "required": ["product_id", "relevant_facts"],
            },
        },
    },
    "required": ["outcome", "selected"],
}

# Finish reasons that mean the provider declined or filtered the output.
_REFUSED = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE"}
)


@dataclass(frozen=True)
class GeminiConfig:
    api_key: SecretStr
    model: str
    timeout_seconds: float

    def __repr__(self) -> str:
        return f"GeminiConfig(model={self.model!r}, timeout_seconds={self.timeout_seconds!r})"


class GeminiGenerationProvider:
    provider = PROVIDER_NAME

    def __init__(self, config: GeminiConfig, *, client: Any = None) -> None:
        self.model = config.model
        # Constructing the client performs no I/O. The key is passed explicitly, so no ambient
        # credential (GOOGLE_API_KEY, application default credentials) is consulted.
        self._client = client or genai.Client(
            api_key=config.api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=round(config.timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=request.user_content,
                config=types.GenerateContentConfig(
                    system_instruction=request.instructions,
                    max_output_tokens=request.max_output_tokens,
                    response_mime_type="application/json",
                    response_json_schema=DRAFT_SCHEMA,
                ),
            )
        except (errors.APIError, httpx.HTTPError) as exc:
            return _failed(_error_code(exc), None, None)
        input_tokens, output_tokens = _usage(response)
        feedback = getattr(response, "prompt_feedback", None)
        if getattr(feedback, "block_reason", None):
            return _failed("refused", input_tokens, output_tokens)
        candidates = getattr(response, "candidates", None) or ()
        if not candidates:
            return _failed("invalid_response", input_tokens, output_tokens)
        finish = _name(getattr(candidates[0], "finish_reason", None))
        if finish in _REFUSED:
            return _failed("refused", input_tokens, output_tokens)
        if finish == "MAX_TOKENS":
            return _failed("truncated", input_tokens, output_tokens)
        text = _text(candidates[0])
        if text is None:
            return _failed("invalid_response", input_tokens, output_tokens)
        try:
            draft = GroundedDraft.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            return _failed("invalid_response", input_tokens, output_tokens)
        return GenerationResult(
            draft=draft, failure=None, input_tokens=input_tokens, output_tokens=output_tokens
        )


def _failed(
    code: GenerationFailure, input_tokens: int | None, output_tokens: int | None
) -> GenerationResult:
    return GenerationResult(
        draft=None, failure=code, input_tokens=input_tokens, output_tokens=output_tokens
    )


def _name(value: object) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


def _text(candidate: Any) -> str | None:
    """The candidate's answer text, skipping thought parts."""
    content = getattr(candidate, "content", None)
    texts = [
        part.text
        for part in getattr(content, "parts", None) or ()
        if isinstance(getattr(part, "text", None), str) and not getattr(part, "thought", False)
    ]
    return "".join(texts) if texts else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(response: Any) -> tuple[int | None, int | None]:
    """Input and output tokens. Output includes thinking tokens; a missing thinking count means
    no thinking was reported. A missing prompt or candidate count stays unknown (None)."""
    usage = getattr(response, "usage_metadata", None)
    prompt = _count(getattr(usage, "prompt_token_count", None))
    candidates = _count(getattr(usage, "candidates_token_count", None))
    thoughts = getattr(usage, "thoughts_token_count", None)
    if candidates is None or (thoughts is not None and _count(thoughts) is None):
        return prompt, None
    return prompt, candidates + (thoughts or 0)


def _error_code(error: Exception) -> GenerationFailure:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.HTTPError):
        return "transport_error"
    code = getattr(error, "code", None)
    if code in (401, 403):
        return "auth_error"
    if code == 429:
        return "rate_limited"
    if code == 408 or code == 504:
        return "timeout"
    if isinstance(code, int) and code >= 500:
        return "server_error"
    return "request_rejected"
