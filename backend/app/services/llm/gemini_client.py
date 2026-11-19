"""Google Gemini via the REST ``generateContent`` endpoint (HTTPX, no SDK).

Verified against ai.google.dev structured-output docs (Nov 2026):
  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  header ``x-goog-api-key``; ``generationConfig.responseMimeType = "application/json"``
  and ``generationConfig.responseJsonSchema`` for schema-constrained JSON.

Why REST instead of the google-genai SDK: one HTTP stack (httpx) for every
integration, uniform timeout/error mapping, and tests with httpx.MockTransport.
"""

from __future__ import annotations

import httpx

from app.services.llm.base import (
    LLMAuthError,
    LLMBlockedError,
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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
NAME = "gemini"


def _retry_delay(err: dict) -> float | None:
    """Gemini puts the wait in error.details[].retryDelay, e.g. {"@type": ".../RetryInfo", "retryDelay": "17s"}."""
    for detail in err.get("details") or []:
        delay = str((detail or {}).get("retryDelay") or "")
        if delay.endswith("s"):
            try:
                return max(0.0, float(delay[:-1]))
            except ValueError:
                return None
    return None


def _error_for(response: httpx.Response) -> LLMError:
    status = response.status_code
    try:
        err = response.json().get("error", {})
    except ValueError:
        err = {}
    message = str(err.get("message", ""))[:300]
    text = (message + " " + str(err.get("status", "")) + " " + response.text[:500]).lower()
    if status in (401, 403) or "api_key_invalid" in text or "api key not valid" in text:
        return LLMAuthError(f"Gemini rejected the API key (HTTP {status})", provider=NAME)
    if status == 404:
        return LLMModelUnavailableError(f"Gemini model not found: {message}", provider=NAME)
    if status == 429:
        exc = LLMRateLimitError("Gemini rate limit / quota exceeded", provider=NAME)
        exc.retry_after = _retry_delay(err)
        return exc
    if status == 400 and ("token" in text and ("exceed" in text or "too long" in text or "limit" in text)):
        return LLMContextOverflowError("Gemini prompt exceeds the model's context window", provider=NAME)
    if status == 504:
        return LLMTimeoutError("Gemini deadline exceeded", provider=NAME)
    if status >= 500:
        return LLMServerError(f"Gemini server error (HTTP {status})", provider=NAME)
    return LLMError(f"Gemini request failed (HTTP {status}): {message}", provider=NAME)


class GeminiProvider:
    name = NAME

    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider")
        if not model:
            raise ValueError("GEMINI_MODEL is required")
        self._key = api_key
        self.model = model
        self._http = http
        self._timeout = timeout

    async def generate(self, request: LLMRequest) -> LLMResponse:
        body = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": request.json_schema,
            },
        }
        try:
            response = await self._http.post(
                f"{BASE_URL}/models/{self.model}:generateContent", json=body,
                headers={"x-goog-api-key": self._key}, timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini request timed out", provider=NAME) from exc
        except httpx.TransportError as exc:
            raise LLMServerError(f"Gemini network error: {type(exc).__name__}", provider=NAME) from exc
        if response.status_code >= 400:
            raise _error_for(response)
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> LLMResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError("Gemini returned a non-JSON HTTP body", provider=NAME) from exc
        if block := (payload.get("promptFeedback") or {}).get("blockReason"):
            raise LLMBlockedError(f"Gemini blocked the prompt: {block}", provider=NAME)
        candidates = payload.get("candidates") or []
        if not candidates:
            raise LLMInvalidResponseError("Gemini returned no candidates", provider=NAME)
        first = candidates[0]
        finish = first.get("finishReason")
        parts = (first.get("content") or {}).get("parts") or []
        # Thinking models may return thought parts; only keep the answer text.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if finish == "MAX_TOKENS":
            partial = LLMResponse(text=text, provider=NAME, model=self.model, finish_reason=finish,
                                  usage=payload.get("usageMetadata") or {}) if text.strip() else None
            raise LLMTruncatedError("Gemini output truncated (raise LLM_MAX_OUTPUT_TOKENS)", provider=NAME,
                                    partial=partial)
        if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            raise LLMBlockedError(f"Gemini stopped generation: {finish}", provider=NAME)
        if not text.strip():
            raise LLMInvalidResponseError("Gemini returned empty text", provider=NAME)
        return LLMResponse(text=text, provider=NAME, model=self.model, finish_reason=finish,
                           usage=payload.get("usageMetadata") or {})
