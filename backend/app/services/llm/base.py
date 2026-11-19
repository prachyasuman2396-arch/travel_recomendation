"""Provider-independent LLM interface and error taxonomy.

The recommendation engine only sees ``LLMProvider`` + these errors — never a
provider SDK. Each error says whether retrying the *same* provider can help
(``retryable``) and whether trying *another* provider makes sense (``fallback``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    json_schema: dict
    schema_name: str = "recommendation_response"
    temperature: float = 0.2
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)


class LLMError(Exception):
    retryable = False
    fallback = True
    kind = "llm_error"

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


class LLMAuthError(LLMError):
    kind = "auth"  # permanent for this provider: never retried


class LLMRateLimitError(LLMError):
    retryable = True
    kind = "rate_limit"


class LLMTimeoutError(LLMError):
    retryable = True
    kind = "timeout"


class LLMServerError(LLMError):
    retryable = True
    kind = "server"


class LLMModelUnavailableError(LLMError):
    kind = "model_unavailable"


class LLMContextOverflowError(LLMError):
    kind = "context_overflow"


class LLMBlockedError(LLMError):
    kind = "blocked"


class LLMInvalidResponseError(LLMError):
    """Empty output, invalid JSON, schema mismatch or failed grounding validation."""

    retryable = True
    kind = "invalid_response"


class LLMTruncatedError(LLMError):
    """Output hit the token limit — retrying with the same limit would truncate again.

    ``partial`` carries what the model did produce, so the caller can salvage complete items from it.
    """

    kind = "truncated"

    def __init__(self, message: str, *, provider: str, partial: LLMResponse | None = None) -> None:
        super().__init__(message, provider=provider)
        self.partial = partial


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...
