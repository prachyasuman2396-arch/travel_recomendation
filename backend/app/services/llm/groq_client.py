"""Groq via its OpenAI-compatible Chat Completions REST API (HTTPX, no SDK).

Verified against console.groq.com/docs/structured-outputs (Nov 2026):
  POST https://api.groq.com/openai/v1/chat/completions, ``Authorization: Bearer``
  ``response_format = {"type": "json_schema", "json_schema": {"name", "strict", "schema"}}``
  Strict mode is model-specific (e.g. openai/gpt-oss-120b); otherwise use
  ``GROQ_STRUCTURED_MODE=json_object`` (valid JSON only — we still validate the schema).
"""

from __future__ import annotations

import json
from typing import Literal

import httpx

from app.core.http import parse_retry_after
from app.services.llm.base import (
    LLMAuthError,
    LLMContextOverflowError,
    LLMError,
    LLMInvalidResponseError,
    LLMModelUnavailableError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMServerError,
    LLMTimeoutError,
    LLMTruncatedError,
)

BASE_URL = "https://api.groq.com/openai/v1"
NAME = "groq"


def _error_for(response: httpx.Response) -> LLMError:
    status = response.status_code
    try:
        err = response.json().get("error", {})
    except ValueError:
        err = {}
    code = str(err.get("code", "")).lower()
    message = str(err.get("message", ""))[:300]
    if status == 401 or code == "invalid_api_key":
        return LLMAuthError("Groq rejected the API key", provider=NAME)
    if status == 404 or code == "model_not_found" or "does not exist" in message.lower():
        return LLMModelUnavailableError(f"Groq model unavailable: {message}", provider=NAME)
    if status == 413 or code == "context_length_exceeded" or "context length" in message.lower():
        return LLMContextOverflowError("Groq prompt exceeds the model's context window", provider=NAME)
    if status == 429:
        exc = LLMRateLimitError("Groq rate limit exceeded", provider=NAME)
        exc.retry_after = parse_retry_after(response.headers.get("retry-after"))
        return exc
    if status in (498, 503):  # 498 = flex tier capacity exceeded
        return LLMServerError(f"Groq capacity unavailable (HTTP {status})", provider=NAME)
    if status >= 500:
        return LLMServerError(f"Groq server error (HTTP {status})", provider=NAME)
    if status == 400 and "json_validate_failed" in code:
        # The model produced output that failed Groq's own schema check — regenerate.
        return LLMInvalidResponseError("Groq output failed schema validation", provider=NAME)
    return LLMError(f"Groq request failed (HTTP {status}): {message}", provider=NAME)


class GroqProvider:
    name = NAME

    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient, timeout: float = 60.0,
                 structured_mode: Literal["json_schema", "json_object", "plain_text"] = "plain_text") -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for the Groq provider")
        if not model:
            raise ValueError("GROQ_MODEL is required")
        self._key = api_key
        self.model = model
        self._http = http
        self._timeout = timeout
        self._mode = structured_mode

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if self._mode == "json_schema":
            response_format = {"type": "json_schema", "json_schema": {
                "name": request.schema_name, "strict": True, "schema": request.json_schema}}
            system = request.system
        elif self._mode == "json_object":
            response_format = {"type": "json_object"}
            # JSON-object mode requires explicit JSON instructions in the prompt.
            system = request.system + "\n\nRespond ONLY with a JSON object matching this JSON Schema:\n" + \
                str(request.json_schema)
        else:
            # Plain text mode: instruct via system prompt with strict JSON structure, parse JSON ourselves
            response_format = None
            if request.schema_name == "traveller_intent":
                schema_hint = (
                    '\n\nCRITICAL: Respond ONLY with a valid JSON object. No markdown fences.\n'
                    'Required JSON structure:\n'
                    '{\n'
                    '  "non_hindu_visitor": false,\n'
                    '  "foreign_national": false,\n'
                    '  "wheelchair_user": false,\n'
                    '  "with_children": false,\n'
                    '  "with_elderly": false,\n'
                    '  "avoid_crowds": false,\n'
                    '  "max_entry_fee_inr": null,\n'
                    '  "prefer_categories": [],\n'
                    '  "avoid_categories": [],\n'
                    '  "available_hours": null,\n'
                    '  "distance_factor": null,\n'
                    '  "vibe": []\n'
                    '}'
                )
            elif request.schema_name != "recommendation_response":
                # Any other task (fact extraction, review summaries, match judging) states its own schema.
                schema_hint = ("\n\nCRITICAL: Respond ONLY with a valid JSON object, no markdown fences, matching "
                               "this JSON Schema:\n" + json.dumps(request.json_schema))
            else:
                schema_hint = (
                    '\n\nCRITICAL: Respond ONLY with a valid JSON object. No markdown fences, no explanation text outside the JSON.\n'
                    'Format:\n'
                    '{\n'
                    '  "recommendations": [\n'
                    '    {"poi_id": "<id>", "rank": 1, "reason": "<why>", "matched_preferences": [], "advantages": [], "considerations": [], "weather_suitability": "unknown", "review_insights": [], "cited_fact_ids": []}\n'
                    '  ],\n'
                    '  "summary": "<summary of day plan>"\n'
                    '}'
                )
            system = request.system + schema_hint
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": request.user}],
            "temperature": request.temperature,
            "max_completion_tokens": request.max_output_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        try:
            response = await self._http.post(f"{BASE_URL}/chat/completions", json=body,
                                             headers={"Authorization": f"Bearer {self._key}"},
                                             timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Groq request timed out", provider=NAME) from exc
        except httpx.TransportError as exc:
            raise LLMServerError(f"Groq network error: {type(exc).__name__}", provider=NAME) from exc
        if response.status_code >= 400:
            raise _error_for(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError("Groq returned a non-JSON HTTP body", provider=NAME) from exc
        choices = payload.get("choices") or []
        if not choices:
            raise LLMInvalidResponseError("Groq returned no choices", provider=NAME)
        finish = choices[0].get("finish_reason")
        text = (choices[0].get("message") or {}).get("content") or ""
        if finish == "length":
            partial = LLMResponse(text=text, provider=NAME, model=self.model, finish_reason=finish,
                                  usage=payload.get("usage") or {}) if text.strip() else None
            raise LLMTruncatedError("Groq output truncated (raise LLM_MAX_OUTPUT_TOKENS)", provider=NAME,
                                    partial=partial)
        if not text.strip():
            raise LLMInvalidResponseError("Groq returned empty content", provider=NAME)
        return LLMResponse(text=text, provider=NAME, model=self.model, finish_reason=finish,
                           usage=payload.get("usage") or {})
