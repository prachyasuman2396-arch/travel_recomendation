"""Route each LLM task to the provider that suits it, with the other as automatic fallback.

* understand — parse free text / refine messages (Hinglish): Groq fast model first (tiny prompt, latency).
* rank — rank candidates and write cited explanations: Gemini first (long context, strict JSON schema).
* extract — pull facts out of Wikipedia text (optional): Gemini first (long documents).
* embed — semantic matching ("peaceful lake"): Gemini embeddings; Groq has none, so TF-IDF otherwise.

If only one key is present, every task uses that provider. If a provider fails (rate limit, outage,
bad JSON), LLMService retries and then falls back to the other one automatically. With no key at all
the app still runs: ranking uses the labelled deterministic fallback and intent uses the rule parser.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.lite.discovery import ProviderStatus
from app.services.llm.base import LLMProvider
from app.services.llm.gemini_client import GeminiProvider
from app.services.llm.groq_client import GroqProvider
from app.services.llm.llm_service import LLMService
from app.services.semantic.search import GeminiEmbedder

TASKS = ("understand", "rank", "extract")
TASK_LABELS = {"understand": "Understanding your request", "rank": "Ranking & explanations",
               "extract": "Fact extraction", "embed": "Semantic matching"}


@dataclass
class Keys:
    gemini: str | None = None
    groq: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.gemini or self.groq)


@dataclass
class TaskRouter:
    services: dict[str, LLMService | None]
    embedder: GeminiEmbedder | None
    plan: dict[str, list[str]]  # task -> ["groq:openai/gpt-oss-20b", "gemini:..."] for display

    def for_task(self, task: str) -> LLMService | None:
        return self.services.get(task)


def build_router(keys: Keys, gemini: ProviderStatus | None, groq: ProviderStatus | None,
                 http: httpx.AsyncClient, *, timeout: float = 15.0, max_attempts: int = 1) -> TaskRouter:
    def gem(model: str | None) -> LLMProvider | None:
        if keys.gemini and gemini and gemini.ok and model:
            return GeminiProvider(keys.gemini, model, http, timeout=timeout)
        return None

    def grq(model: str | None) -> LLMProvider | None:
        if keys.groq and groq and groq.ok and model:
            return GroqProvider(keys.groq, model, http, timeout=timeout, structured_mode=groq.structured_mode(model))
        return None

    gem_main = gemini.text_model if gemini else None
    groq_big = groq.text_model if groq else None
    groq_fast = groq.fast_model if groq else None
    chains: dict[str, list[LLMProvider | None]] = {
        "understand": [grq(groq_fast), gem(gem_main)],
        "rank": [grq(groq_fast), gem(gem_main)],  # Groq fast first (Gemini often 503); Gemini as fallback
        "extract": [gem(gem_main), grq(groq_big)],
    }
    services: dict[str, LLMService | None] = {}
    plan: dict[str, list[str]] = {}
    for task, chain in chains.items():
        providers = [p for p in chain if p is not None]
        services[task] = LLMService(providers, max_attempts=max_attempts) if providers else None
        plan[task] = [f"{p.name}:{p.model}" for p in providers]
    embedder = None
    if keys.gemini and gemini and gemini.ok and gemini.embed_model:
        embedder = GeminiEmbedder(keys.gemini, http, gemini.embed_model, 768)
    plan["embed"] = [f"gemini:{embedder.model}"] if embedder else ["tfidf (offline)"]
    return TaskRouter(services, embedder, plan)
