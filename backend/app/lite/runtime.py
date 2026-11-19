"""One place that wires everything for the Streamlit app. All functions are async and self-contained:
each opens its own HTTP client, so they are safe to call with ``asyncio.run`` from any thread."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from app.eval.offline import BHUBANESWAR, build_offline_store
from app.lite.discovery import ProviderStatus, discover_gemini, discover_groq
from app.lite.geocode import Place, search_places
from app.lite.live_data import DEFAULT_OVERPASS_URLS, LiveReport, build_live_store
from app.lite.router import Keys, TaskRouter, build_router
from app.schemas.preference import PreferenceProfile
from app.schemas.recommendation import RecommendationResult
from app.services.ingestion.memory_store import MemoryCandidateSource, MemoryPoiStore
from app.services.intent.parser import LLMIntentParser, RuleIntentParser
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.routing.router import EstimateRouter, OSRMRouter, RoutingService
from app.services.semantic.service import SemanticService
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer
from app.services.sentiment.service import SentimentService
from app.services.weather.open_meteo import OpenMeteoProvider
from app.services.weather.service import WeatherService

USER_AGENT_BASE = "RadiusTouristRecommender/0.3 (Streamlit edition)"


@dataclass
class LiteSettings:
    data_dir: Path
    contact_email: str | None = None
    overpass_urls: tuple[str, ...] = DEFAULT_OVERPASS_URLS
    osrm_url: str | None = None
    max_results: int = 6
    llm_timeout: float = 10.0
    http_timeout: float = 10.0  # fast fail — Wikipedia GeoSearch is the primary source, Overpass is capped at 5s

    @property
    def user_agent(self) -> str:
        # Wikimedia and OSM ask for a way to contact the operator; CONTACT_EMAIL provides it.
        email = self.contact_email or "tourist-app@example.com"
        return f"{USER_AGENT_BASE} (contact: {email}; https://github.com/tourist-recommender)"


@dataclass
class Statuses:
    gemini: ProviderStatus | None = None
    groq: ProviderStatus | None = None


def _client(settings: LiteSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.http_timeout, connect=10.0),
                             headers={"User-Agent": settings.user_agent}, follow_redirects=True)


async def check_keys(keys: Keys, settings: LiteSettings) -> Statuses:
    async with _client(settings) as http:
        out = Statuses()
        if keys.gemini:
            out.gemini = await discover_gemini(keys.gemini, http)
        if keys.groq:
            out.groq = await discover_groq(keys.groq, http)
        return out


async def geocode(query: str, settings: LiteSettings) -> list[Place]:
    async with _client(settings) as http:
        return await search_places(query, http)


async def load_area(latitude: float, longitude: float, radius_km: float, keys: Keys, statuses: Statuses,
                    settings: LiteSettings, *, deep_facts: bool = False, progress=None
                    ) -> tuple[MemoryPoiStore, LiveReport]:
    async with _client(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        return await build_live_store(
            latitude, longitude, radius_km, http, user_agent=settings.user_agent, data_dir=settings.data_dir,
            overpass_urls=settings.overpass_urls, embedder=router.embedder, max_wikipedia=6, wiki_concurrency=6,
            extract_llm=router.for_task("extract") if deep_facts else None, progress=progress)


async def load_demo_area(settings: LiteSettings) -> tuple[MemoryPoiStore, LiveReport]:
    store = await build_offline_store(settings.data_dir)
    report = LiveReport(
        places=len(store.pois),
        with_hours=sum(1 for p in store.pois.values() if (p.get("opening_hours") or {}).get("weekdayDescriptions")),
        wikipedia_pages=len(store.documents), events=len(store.events),
        verified_facts=sum(1 for f in store.facts.values() if f["status"] == "verified"),
        reported_facts=sum(1 for f in store.facts.values() if f["status"] == "unverified"),
        notes=["Demo area: Bhubaneswar SAMPLE data (invented ratings/reviews; real curated facts)."])
    return store, report


DEMO_CENTER = BHUBANESWAR


def _engine(store: MemoryPoiStore, router: TaskRouter, settings: LiteSettings, http: httpx.AsyncClient
            ) -> RecommendationEngine:
    understand = router.for_task("understand")
    rules = RuleIntentParser()
    return RecommendationEngine(
        MemoryCandidateSource(store, SentimentService(LexiconSentimentAnalyzer())),
        WeatherService(OpenMeteoProvider(http), 600, air_quality=True),
        router.for_task("rank"),
        EngineConfig(max_results=settings.max_results, deterministic_fallback=True, max_output_tokens=4096,
                     candidate_limit=15, max_prompt_chars=20_000),
        intent=LLMIntentParser(understand, rules) if understand else rules,
        semantic=SemanticService(router.embedder),
        routing=RoutingService(OSRMRouter(settings.osrm_url, http) if settings.osrm_url else None, EstimateRouter()),
    )


async def recommend(store: MemoryPoiStore, profile: PreferenceProfile, keys: Keys, statuses: Statuses,
                    settings: LiteSettings) -> tuple[RecommendationResult, dict[str, list[str]]]:
    async with _client(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        result = await _engine(store, router, settings, http).recommend(profile)
        return result, router.plan


async def refine(store: MemoryPoiStore, profile: PreferenceProfile, message: str, keys: Keys, statuses: Statuses,
                 settings: LiteSettings) -> tuple[PreferenceProfile, list[str], RecommendationResult]:
    async with _client(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        engine = _engine(store, router, settings, http)
        new_profile, changes = await engine.refine(profile, message)
        return new_profile, changes, await engine.recommend(new_profile)
