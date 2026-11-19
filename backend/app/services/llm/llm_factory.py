"""Build the ordered provider chain from configuration (primary first, then fallback)."""

from __future__ import annotations

import logging

import httpx

from app.core.config import LLMProviderName, Settings
from app.services.llm.base import LLMProvider
from app.services.llm.gemini_client import GeminiProvider
from app.services.llm.groq_client import GroqProvider
from app.services.llm.llm_service import LLMService
from app.services.llm.mock_client import MockLLMProvider

logger = logging.getLogger(__name__)


def _build(name: LLMProviderName, settings: Settings, http: httpx.AsyncClient) -> LLMProvider | None:
    if name == "mock":
        return MockLLMProvider(settings.recommendation_max_results)
    if name == "gemini":
        if not settings.gemini_api_key:
            return None
        return GeminiProvider(settings.gemini_api_key.get_secret_value(), settings.gemini_model, http,
                              timeout=settings.llm_timeout)
    if name == "groq":
        if not settings.groq_api_key:
            return None
        return GroqProvider(settings.groq_api_key.get_secret_value(), settings.groq_model, http,
                            timeout=settings.llm_timeout, structured_mode=settings.groq_structured_mode)
    raise ValueError(f"Unknown LLM provider {name}")


def provider_chain(settings: Settings, http: httpx.AsyncClient) -> list[LLMProvider]:
    names: list[LLMProviderName] = [settings.llm_provider]
    if settings.llm_fallback_enabled and settings.llm_fallback_provider:
        names.append(settings.llm_fallback_provider)
    chain = []
    for name in names:
        provider = _build(name, settings, http)
        if provider is None:
            logger.warning("llm_provider_not_configured", extra={"provider": name})
        else:
            chain.append(provider)
    return chain


def build_llm_service(settings: Settings, http: httpx.AsyncClient) -> LLMService | None:
    """``None`` when no provider is configured — the engine reports that clearly (never fakes output)."""
    chain = provider_chain(settings, http)
    return LLMService(chain, max_attempts=settings.llm_max_retries) if chain else None


def describe_providers(settings: Settings) -> list[dict]:
    """Public, secret-free description for GET /recommendations/providers."""
    entries = [("primary", settings.llm_provider)]
    if settings.llm_fallback_enabled and settings.llm_fallback_provider:
        entries.append(("fallback", settings.llm_fallback_provider))
    out = []
    for role, name in entries:
        model = {"gemini": settings.gemini_model, "groq": settings.groq_model,
                 "mock": MockLLMProvider.model}[name]
        configured = name == "mock" or bool({"gemini": settings.gemini_api_key,
                                             "groq": settings.groq_api_key}[name])
        out.append({"role": role, "provider": name, "model": model, "configured": configured})
    return out
