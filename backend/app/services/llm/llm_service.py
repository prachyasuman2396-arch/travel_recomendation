"""Retry + fallback orchestration across providers, with shared output validation.

Flow:
  1. primary provider; retry only *retryable* errors with exponential backoff
  2. if it still fails and the error permits fallback, try the secondary provider
  3. every response goes through the same ``parse`` function (schema + grounding)
  4. if all providers fail -> AllProvidersFailedError (the engine maps it to HTTP 503)

Nothing here ever fabricates a response.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.core.retry import with_retries
from app.services.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass
class Attempt:
    provider: str
    model: str
    outcome: str  # ok | <error kind>
    message: str = ""


@dataclass
class LLMResult(Generic[T]):
    value: T
    response: LLMResponse
    attempts: list[Attempt]


class AllProvidersFailedError(Exception):
    def __init__(self, attempts: list[Attempt]) -> None:
        super().__init__("All LLM providers failed")
        self.attempts = attempts

    @property
    def last_kind(self) -> str | None:
        return self.attempts[-1].outcome if self.attempts else None


class LLMService:
    def __init__(self, providers: list[LLMProvider], *, max_attempts: int = 3) -> None:
        if not providers:
            raise ValueError("At least one LLM provider must be configured")
        self.providers = providers
        self.max_attempts = max_attempts

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    async def generate(self, request: LLMRequest, parse: Callable[[LLMResponse], T]) -> LLMResult[T]:
        attempts: list[Attempt] = []
        for index, provider in enumerate(self.providers):

            async def call(provider: LLMProvider = provider) -> tuple[T, LLMResponse]:
                try:
                    response = await provider.generate(request)
                    value = parse(response)  # raises LLMInvalidResponseError -> retryable
                except LLMError as exc:
                    attempts.append(Attempt(provider.name, provider.model, exc.kind, str(exc)))
                    raise
                attempts.append(Attempt(provider.name, provider.model, "ok"))
                return value, response

            try:
                value, response = await with_retries(call, attempts=self.max_attempts,
                                                     operation=f"llm.{provider.name}")
                if index > 0:
                    logger.warning("llm_fallback_used", extra={"provider": provider.name})
                return LLMResult(value=value, response=response, attempts=attempts)
            except LLMError as exc:
                logger.warning("llm_provider_failed",
                               extra={"provider": provider.name, "kind": exc.kind, "error": str(exc)})
                if not exc.fallback:
                    break
        raise AllProvidersFailedError(attempts)
