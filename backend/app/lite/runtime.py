"""One place that wires everything for the Streamlit app.

All functions are async. In the Streamlit app they run on ONE background event loop (``background_loop``)
and share its pooled HTTP client; called any other way (tests, ``asyncio.run``) they open and close their
own client, so they stay safe to call from anywhere."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.http import build_async_client
from app.eval.offline import BHUBANESWAR, build_offline_store
from app.lite.discovery import ProviderStatus, discover_gemini, discover_groq
from app.lite.geocode import Place, search_places
from app.lite.live_data import DEFAULT_OVERPASS_URLS, LiveReport, build_live_store
from app.lite.loop import BackgroundLoop, install, shared_client
from app.lite.router import Keys, TaskRouter, build_router
from app.schemas.preference import PreferenceProfile
from app.schemas.recommendation import RecommendationResult
from app.services.ingestion.memory_store import MemoryCandidateSource, MemoryPoiStore
from app.services.intent.parser import LLMIntentParser, RuleIntentParser
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.recommendation.review_summary import ReviewSummarizer
from app.services.routing.router import EstimateRouter, GoogleRoutesRouter, OSRMRouter, RoutingService
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
    return build_async_client(user_agent=settings.user_agent, timeout=settings.http_timeout, connect_timeout=5.0,
                              max_connections=50, max_keepalive_connections=20, follow_redirects=True)


@asynccontextmanager
async def _http(settings: LiteSettings) -> AsyncIterator[httpx.AsyncClient]:
    """The process-wide pooled client when running on the Streamlit background loop; otherwise (tests,
    scripts using ``asyncio.run``) a short-lived client that is closed afterwards."""
    shared = shared_client()
    if shared is not None:
        yield shared
        return
    async with _client(settings) as http:
        yield http


def background_loop(settings: LiteSettings) -> BackgroundLoop:
    """Create and register the Streamlit process's single event loop + pooled HTTP client."""
    return install(BackgroundLoop(lambda: _client(settings)))


async def check_keys(keys: Keys, settings: LiteSettings) -> Statuses:
    async with _http(settings) as http:
        out = Statuses()
        if keys.gemini:
            out.gemini = await discover_gemini(keys.gemini, http)
        if keys.groq:
            out.groq = await discover_groq(keys.groq, http)
        return out


async def geocode(query: str, settings: LiteSettings) -> list[Place]:
    async with _http(settings) as http:
        return await search_places(query, http)


async def load_area(latitude: float, longitude: float, radius_km: float, keys: Keys, statuses: Statuses,
                    settings: LiteSettings, *, deep_facts: bool = False, progress=None
                    ) -> tuple[MemoryPoiStore, LiveReport]:
    async with _http(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        return await build_live_store(
            latitude, longitude, radius_km, http, user_agent=settings.user_agent, data_dir=settings.data_dir,
            overpass_urls=settings.overpass_urls, embedder=router.embedder, max_wikipedia=6, wiki_concurrency=6,
            extract_llm=router.for_task("extract") if deep_facts else None, progress=progress,
            google_key=keys.google)


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


_WEATHER: dict[int, WeatherService] = {}


def _weather(http: httpx.AsyncClient) -> WeatherService:
    """One weather service (and its 10-minute cache) per HTTP client — i.e. per process on the shared loop."""
    if len(_WEATHER) > 8:  # short-lived clients (tests, asyncio.run) must not pile up
        _WEATHER.clear()
    if (svc := _WEATHER.get(id(http))) is None or svc.provider is None or svc.provider._http is not http:
        svc = _WEATHER[id(http)] = WeatherService(OpenMeteoProvider(http), 600, air_quality=True)
    return svc


def _engine(store: MemoryPoiStore, router: TaskRouter, settings: LiteSettings, http: httpx.AsyncClient,
            keys: Keys | None = None) -> RecommendationEngine:
    understand = router.for_task("understand")
    google = keys.google if keys else None
    rules = RuleIntentParser()
    return RecommendationEngine(
        MemoryCandidateSource(store, SentimentService(LexiconSentimentAnalyzer())),
        _weather(http),
        router.for_task("rank"),
        EngineConfig(max_results=settings.max_results, deterministic_fallback=True, max_output_tokens=4096,
                     candidate_limit=15, max_prompt_chars=20_000),
        intent=LLMIntentParser(understand, rules) if understand else rules,
        semantic=SemanticService(router.embedder),
        routing=RoutingService(OSRMRouter(settings.osrm_url, http) if settings.osrm_url else None, EstimateRouter(),
                               traffic=GoogleRoutesRouter(google, http) if google else None),
        reviews=ReviewSummarizer(understand or router.for_task("rank")),
    )


async def recommend(store: MemoryPoiStore, profile: PreferenceProfile, keys: Keys, statuses: Statuses,
                    settings: LiteSettings) -> tuple[RecommendationResult, dict[str, list[str]]]:
    async with _http(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        result = await _engine(store, router, settings, http, keys).recommend(profile)
        return result, router.plan


async def refine(store: MemoryPoiStore, profile: PreferenceProfile, message: str, keys: Keys, statuses: Statuses,
                 settings: LiteSettings) -> tuple[PreferenceProfile, list[str], RecommendationResult]:
    async with _http(settings) as http:
        router = build_router(keys, statuses.gemini, statuses.groq, http, timeout=settings.llm_timeout)
        engine = _engine(store, router, settings, http, keys)
        new_profile, changes = await engine.refine(profile, message)
        return new_profile, changes, await engine.recommend(new_profile)
