"""Composition root: builds services from settings and exposes FastAPI dependencies.

Long-lived objects (HTTP client, weather cache, LLM chain) are created once in
the app lifespan and stored on ``app.state``; per-request objects (DB session,
repositories, engine) are built per call.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import verify_admin_token
from app.database.session import get_db
from app.repositories.poi_repository import PoiRepository
from app.services.data_collection.factory import build_http_client
from app.services.intent.parser import LLMIntentParser, RuleIntentParser
from app.services.llm.llm_factory import build_llm_service
from app.services.llm.llm_service import LLMService
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.recommendation.review_summary import ReviewSummarizer
from app.services.routing.router import EstimateRouter, GoogleRoutesRouter, OSRMRouter, RoutingService
from app.services.semantic.search import GeminiEmbedder
from app.services.semantic.service import SemanticService
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer
from app.services.sentiment.service import SentimentService
from app.services.sentiment.transformer import TransformerSentimentAnalyzer
from app.services.weather.open_meteo import MockWeatherProvider, OpenMeteoProvider
from app.services.weather.service import WeatherService


@dataclass
class AppServices:
    http: httpx.AsyncClient
    weather: WeatherService
    llm: LLMService | None
    sentiment: SentimentService
    intent: RuleIntentParser | LLMIntentParser | None = None
    semantic: SemanticService | None = None
    routing: RoutingService | None = None
    embedder: GeminiEmbedder | None = None
    reviews: ReviewSummarizer | None = None


def build_services(settings: Settings) -> AppServices:
    http = build_http_client(settings)
    if settings.weather_provider == "open_meteo":
        key = settings.weather_api_key.get_secret_value() if settings.weather_api_key else None
        provider = OpenMeteoProvider(http, api_key=key)
    elif settings.weather_provider == "mock":
        provider = MockWeatherProvider(settings.data_dir / "sample" / "weather_open_meteo.json")
    else:
        provider = None
    analyzer = (TransformerSentimentAnalyzer(settings.sentiment_model) if settings.sentiment_backend == "transformers"
                else LexiconSentimentAnalyzer())
    llm = build_llm_service(settings, http)
    rules = RuleIntentParser()
    intent = LLMIntentParser(llm, rules) if settings.intent_parser == "llm" and llm else rules
    embedder = None
    if settings.semantic_backend == "gemini" and settings.gemini_api_key:
        embedder = GeminiEmbedder(settings.gemini_api_key.get_secret_value(), http, settings.embedding_model,
                                  settings.embedding_dimensions)
    # Live traffic uses the same Google Maps Platform key as Places (enable the Routes API on it).
    traffic = (GoogleRoutesRouter(settings.places_api_key.get_secret_value(), http)
               if settings.places_api_key and settings.traffic_routing_enabled else None)
    routing = RoutingService(OSRMRouter(settings.osrm_url, http) if settings.osrm_url else None,
                             EstimateRouter(settings.assumed_avg_speed_kmh, settings.road_detour_factor),
                             traffic=traffic)
    return AppServices(
        http=http,
        weather=WeatherService(provider, settings.weather_cache_ttl_seconds, settings.air_quality_enabled),
        llm=llm,
        sentiment=SentimentService(analyzer, settings.sentiment_min_reviews),
        intent=intent, semantic=SemanticService(embedder), routing=routing, embedder=embedder,
        reviews=ReviewSummarizer(llm if settings.review_summary_llm else None),
    )


def get_services(request: Request) -> AppServices:
    return request.app.state.services


def engine_config(settings: Settings) -> EngineConfig:
    return EngineConfig(
        candidate_limit=settings.recommendation_candidate_limit,
        max_results=settings.recommendation_max_results,
        max_prompt_chars=settings.llm_max_prompt_chars,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
        assumed_speed_kmh=settings.assumed_avg_speed_kmh,
        detour_factor=settings.road_detour_factor,
        deterministic_fallback=settings.recommendation_deterministic_fallback,
        itinerary_max_stops=settings.itinerary_max_stops,
    )


def get_engine(db: Session = Depends(get_db), services: AppServices = Depends(get_services),
               settings: Settings = Depends(get_settings)) -> RecommendationEngine:
    return RecommendationEngine(PoiRepository(db), services.weather, services.llm, engine_config(settings),
                                intent=services.intent, semantic=services.semantic, routing=services.routing,
                                reviews=services.reviews)


def require_admin(authorization: str | None = Header(default=None),
                  settings: Settings = Depends(get_settings)) -> None:
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    expected = settings.admin_api_token.get_secret_value() if settings.admin_api_token else None
    verify_admin_token(token, expected)
