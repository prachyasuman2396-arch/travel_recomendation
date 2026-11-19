"""Gemini / Groq clients (httpx.MockTransport) and the retry + fallback service."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm.base import (
    LLMAuthError,
    LLMBlockedError,
    LLMContextOverflowError,
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMServerError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from app.services.llm.gemini_client import GeminiProvider
from app.services.llm.groq_client import GroqProvider
from app.services.llm.llm_service import AllProvidersFailedError, LLMService
from app.services.llm.response_schemas import RECOMMENDATION_JSON_SCHEMA

pytestmark = pytest.mark.anyio
REQ = LLMRequest(system="sys", user="user", json_schema=RECOMMENDATION_JSON_SCHEMA)
GOOD = json.dumps({"recommendations": [], "summary": "ok"})


def http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- gemini
async def test_gemini_success_uses_structured_output_and_skips_thought_parts():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["key"] = request.headers["x-goog-api-key"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": GOOD}]}}], "usageMetadata": {"totalTokenCount": 9}})

    async with http(handler) as client:
        out = await GeminiProvider("key", "gemini-test", client).generate(REQ)
    assert captured["url"].endswith("/models/gemini-test:generateContent")
    assert captured["key"] == "key"
    gc = captured["body"]["generationConfig"]
    assert gc["responseMimeType"] == "application/json" and gc["responseJsonSchema"] == RECOMMENDATION_JSON_SCHEMA
    assert out.text == GOOD and out.provider == "gemini"


@pytest.mark.parametrize(("status", "body", "error"), [
    (400, {"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}},
     LLMAuthError),
    (403, {"error": {"message": "denied"}}, LLMAuthError),
    (404, {"error": {"message": "models/x is not found"}}, LLMModelUnavailableError),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, LLMRateLimitError),
    (400, {"error": {"message": "The input token count exceeds the maximum number of tokens allowed"}},
     LLMContextOverflowError),
    (500, {}, LLMServerError),
    (504, {}, LLMTimeoutError),
])
async def test_gemini_error_mapping(status, body, error):
    async with http(lambda r: httpx.Response(status, json=body)) as client:
        with pytest.raises(error):
            await GeminiProvider("key", "m", client).generate(REQ)


@pytest.mark.parametrize(("payload", "error"), [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, LLMBlockedError),
    ({"candidates": []}, LLMInvalidResponseError),
    ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{\"rec"}]}}]}, LLMTruncatedError),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "  "}]}}]}, LLMInvalidResponseError),
])
async def test_gemini_bad_payloads(payload, error):
    async with http(lambda r: httpx.Response(200, json=payload)) as client:
        with pytest.raises(error):
            await GeminiProvider("key", "m", client).generate(REQ)


async def test_gemini_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with http(handler) as client:
        with pytest.raises(LLMTimeoutError):
            await GeminiProvider("key", "m", client).generate(REQ)


# ------------------------------------------------------------------ groq
async def test_groq_success_strict_json_schema():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]})

    async with http(handler) as client:
        # The API passes GROQ_STRUCTURED_MODE (default json_schema); the bare constructor defaults to plain_text.
        out = await GroqProvider("gk", "openai/gpt-oss-120b", client, structured_mode="json_schema").generate(REQ)
    fmt = captured["body"]["response_format"]
    assert captured["auth"] == "Bearer gk"
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert out.text == GOOD and out.model == "openai/gpt-oss-120b"


async def test_groq_plain_text_default_sends_no_response_format():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]})

    async with http(handler) as client:
        out = await GroqProvider("gk", "openai/gpt-oss-20b", client).generate(REQ)
    assert "response_format" not in captured["body"]
    assert "Respond ONLY with a valid JSON object" in captured["body"]["messages"][0]["content"]
    assert out.text == GOOD


async def test_groq_json_object_mode_puts_schema_in_prompt():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]})

    async with http(handler) as client:
        await GroqProvider("gk", "m", client, structured_mode="json_object").generate(REQ)
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "JSON" in captured["body"]["messages"][0]["content"]


@pytest.mark.parametrize(("status", "body", "error"), [
    (401, {"error": {"code": "invalid_api_key"}}, LLMAuthError),
    (404, {"error": {"code": "model_not_found"}}, LLMModelUnavailableError),
    (400, {"error": {"code": "context_length_exceeded", "message": "too long"}}, LLMContextOverflowError),
    (413, {"error": {"message": "Request too large"}}, LLMContextOverflowError),
    (429, {"error": {"message": "rate"}}, LLMRateLimitError),
    (503, {}, LLMServerError),
    (400, {"error": {"code": "json_validate_failed"}}, LLMInvalidResponseError),
])
async def test_groq_error_mapping(status, body, error):
    async with http(lambda r: httpx.Response(status, json=body)) as client:
        with pytest.raises(error):
            await GroqProvider("gk", "m", client).generate(REQ)


async def test_groq_truncated_output():
    payload = {"choices": [{"finish_reason": "length", "message": {"content": "{\"a\""}}]}
    async with http(lambda r: httpx.Response(200, json=payload)) as client:
        with pytest.raises(LLMTruncatedError):
            await GroqProvider("gk", "m", client).generate(REQ)


# --------------------------------------------------------- service logic
class FakeProvider:
    def __init__(self, name: str, outcomes: list) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(text=outcome, provider=self.name, model=self.model)


def parse(response: LLMResponse) -> dict:
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise LLMInvalidResponseError("bad json", provider=response.provider) from exc


async def test_primary_success_no_fallback():
    gemini, groq = FakeProvider("gemini", [GOOD]), FakeProvider("groq", [GOOD])
    result = await LLMService([gemini, groq], max_attempts=3).generate(REQ, parse)
    assert result.response.provider == "gemini" and groq.calls == 0


async def test_transient_error_is_retried_on_same_provider():
    gemini = FakeProvider("gemini", [LLMRateLimitError("429", provider="gemini"), GOOD])
    result = await LLMService([gemini], max_attempts=3).generate(REQ, parse)
    assert gemini.calls == 2 and result.attempts[0].outcome == "rate_limit"


async def test_auth_error_not_retried_and_falls_back_to_groq():
    gemini = FakeProvider("gemini", [LLMAuthError("bad key", provider="gemini")])
    groq = FakeProvider("groq", [GOOD])
    result = await LLMService([gemini, groq], max_attempts=3).generate(REQ, parse)
    assert gemini.calls == 1  # permanent error: no retries
    assert result.response.provider == "groq"


async def test_invalid_json_is_regenerated_then_falls_back():
    gemini = FakeProvider("gemini", ["not json"])
    groq = FakeProvider("groq", [GOOD])
    result = await LLMService([gemini, groq], max_attempts=2).generate(REQ, parse)
    assert gemini.calls == 2 and result.response.provider == "groq"
    assert [a.outcome for a in result.attempts] == ["invalid_response", "invalid_response", "ok"]


async def test_both_providers_unavailable_raises_controlled_error():
    gemini = FakeProvider("gemini", [LLMServerError("500", provider="gemini")])
    groq = FakeProvider("groq", [LLMTimeoutError("timeout", provider="groq")])
    with pytest.raises(AllProvidersFailedError) as exc:
        await LLMService([gemini, groq], max_attempts=2).generate(REQ, parse)
    assert gemini.calls == 2 and groq.calls == 2
    assert exc.value.last_kind == "timeout"


async def test_provider_switching_order_is_respected():
    groq, gemini = FakeProvider("groq", [GOOD]), FakeProvider("gemini", [GOOD])
    result = await LLMService([groq, gemini]).generate(REQ, parse)
    assert result.response.provider == "groq" and gemini.calls == 0


def test_service_requires_a_provider():
    with pytest.raises(ValueError):
        LLMService([])
