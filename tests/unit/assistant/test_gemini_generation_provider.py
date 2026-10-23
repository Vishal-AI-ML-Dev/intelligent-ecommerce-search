"""Milestone 16: the optional Gemini adapter against a fake client. No network call, no key.

These tests cover request bounds, response parsing and error mapping only. The live API has not
been called; the model's structured-output support and the schema have not been verified
against it.
"""

import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types
from pydantic import SecretStr

from ecommerce_search.generation.gemini_provider import (
    DRAFT_SCHEMA,
    GeminiConfig,
    GeminiGenerationProvider,
)
from ecommerce_search.generation.provider import GenerationRequest

KEY = "gm-test-not-a-real-key"  # noqa: S105 - fake test value
REQUEST = GenerationRequest(instructions="rules", user_content="data", max_output_tokens=512)
VALID = {
    "outcome": "answer",
    "selected": [{"product_id": "P1", "relevant_facts": ["price"]}],
}
VALID_TEXT = json.dumps(VALID)
USAGE = {"prompt_token_count": 120, "candidates_token_count": 30, "thoughts_token_count": 10}


def config():
    return GeminiConfig(api_key=SecretStr(KEY), model="gemini-3.5-flash-lite", timeout_seconds=5)


def response(text=VALID_TEXT, finish=types.FinishReason.STOP, usage=USAGE, block=None):
    parts = [SimpleNamespace(text="thinking...", thought=True)]
    if text is not None:
        parts.append(SimpleNamespace(text=text, thought=None))
    candidate = SimpleNamespace(finish_reason=finish, content=SimpleNamespace(parts=parts))
    return SimpleNamespace(
        candidates=[candidate],
        prompt_feedback=None if block is None else SimpleNamespace(block_reason=block),
        usage_metadata=None if usage is None else SimpleNamespace(**usage),
    )


class FakeModels:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def provider_with(outcome):
    models = FakeModels(outcome)
    return GeminiGenerationProvider(config(), client=SimpleNamespace(models=models)), models


def test_request_is_bounded_and_structured():
    provider, models = provider_with(response())
    result = provider.generate(REQUEST)
    assert result.failure is None and result.draft.selected[0].product_id == "P1"
    assert (result.input_tokens, result.output_tokens) == (120, 40)  # thinking counts as output
    [call] = models.calls
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["contents"] == "data"
    cfg = call["config"]
    assert cfg.system_instruction == "rules"
    assert cfg.max_output_tokens == 512
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == DRAFT_SCHEMA
    assert cfg.tools is None and cfg.temperature is None


def test_real_client_is_built_without_retries_and_with_a_timeout():
    provider = GeminiGenerationProvider(config())
    try:
        options = provider._client._api_client._http_options
        assert options.timeout == 5000
        assert options.retry_options.attempts == 1
    finally:
        provider.close()


def test_config_repr_hides_the_key():
    assert KEY not in repr(config())


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout("t"), "timeout"),
        (httpx.ConnectError("c"), "transport_error"),
        (errors.ClientError(401, {}), "auth_error"),
        (errors.ClientError(403, {}), "auth_error"),
        (errors.ClientError(429, {}), "rate_limited"),
        (errors.ClientError(400, {}), "request_rejected"),
        (errors.ClientError(404, {}), "request_rejected"),
        (errors.ServerError(500, {}), "server_error"),
        (errors.ServerError(504, {}), "timeout"),
    ],
)
def test_sdk_errors_map_to_fixed_codes(error, code):
    provider, _ = provider_with(error)
    result = provider.generate(REQUEST)
    assert result.draft is None and result.failure == code
    assert result.input_tokens is None and result.output_tokens is None


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (response(block="SAFETY"), "refused"),
        (response(finish=types.FinishReason.SAFETY), "refused"),
        (response(finish=types.FinishReason.MAX_TOKENS), "truncated"),
        (response(text=None), "invalid_response"),
        (response(text="not json"), "invalid_response"),
        (response(text=json.dumps(VALID | {"extra": 1})), "invalid_response"),
        (response(text=json.dumps(VALID | {"summary": "16GB RAM"})), "invalid_response"),
        (response(text=json.dumps(VALID | {"outcome": "buy"})), "invalid_response"),
    ],
)
def test_bad_replies_are_failures_with_known_usage(reply, code):
    provider, _ = provider_with(reply)
    result = provider.generate(REQUEST)
    assert result.draft is None and result.failure == code
    assert result.input_tokens == 120


def test_no_candidates_is_invalid():
    reply = response()
    reply.candidates = []
    provider, _ = provider_with(reply)
    assert provider.generate(REQUEST).failure == "invalid_response"


def test_missing_usage_is_unknown_not_zero():
    provider, _ = provider_with(response(usage=None))
    result = provider.generate(REQUEST)
    assert result.draft is not None
    assert result.input_tokens is None and result.output_tokens is None


def test_missing_thinking_count_means_no_thinking():
    usage = {"prompt_token_count": 120, "candidates_token_count": 30, "thoughts_token_count": None}
    provider, _ = provider_with(response(usage=usage))
    assert provider.generate(REQUEST).output_tokens == 30
