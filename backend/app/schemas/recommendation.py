from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.preference import PreferenceProfile

GenerationMethod = Literal["llm", "mock_llm", "deterministic_fallback", "none"]


class WeatherSummary(BaseModel):
    source: str
    is_mock: bool
    retrieved_at: datetime
    observed_at: datetime | None
    condition: str | None
    temperature_c: float | None
    humidity: float | None
    wind_speed_kmh: float | None
    max_precipitation_probability: float | None


class NoticeOut(BaseModel):
    kind: str
    text: str
    severity: Literal["info", "warning", "critical"]
    source_url: str | None = None
    verified: bool


class FactOut(BaseModel):
    id: str
    key: str
    text: str
    verified: bool
    source_name: str | None
    source_url: str


class AirQualityOut(BaseModel):
    us_aqi: float | None
    pm2_5: float | None
    pm10: float | None
    category: str | None


class ItineraryStopOut(BaseModel):
    poi_id: str
    name: str
    arrive: datetime
    depart: datetime
    travel_minutes: int
    wait_minutes: int
    visit_minutes: int
    hours_known: bool


class ItineraryOut(BaseModel):
    start: datetime
    end: datetime
    stops: list[ItineraryStopOut]
    total_travel_minutes: int
    travel_method: Literal["osrm", "estimate"]
    skipped: list[str]
    notes: list[str]


class EventOut(BaseModel):
    name: str
    start: datetime
    end: datetime
    location_name: str
    crowd_impact: str
    description: str
    date_note: str | None
    source_url: str


class RecommendationFacts(BaseModel):
    """Facts come from the database/feature layer — never from the LLM."""

    distance_km: float
    rating: float | None
    rating_count: int
    rating_sources: list[str]
    review_count: int
    photo_count: int
    photo_activity: str
    sentiment_status: str
    sentiment_overall: str | None
    positive_aspects: list[str]
    negative_aspects: list[str]
    hours_today: str | None
    time_fit: str
    data_origin: str


class RecommendationItem(BaseModel):
    poi_id: str
    rank: int
    name: str
    category: str
    categories: list[str]
    latitude: float
    longitude: float
    address: str | None
    website: str | None
    photo_url: str | None
    facts: RecommendationFacts
    # AI-generated (or templated) explanation fields:
    reason: str
    matched_preferences: list[str]
    advantages: list[str]
    considerations: list[str]
    weather_suitability: str
    review_insights: list[str]
    # India context — computed by code, attached after the LLM (cannot be dropped by the model)
    notices: list[NoticeOut] = []
    cited_facts: list[FactOut] = []
    place_facts: list[FactOut] = []  # all sourced facts for this place (the LLM cited a subset)
    season_note: str | None = None
    local_names: list[str] = []
    image_url: str | None = None
    image_attribution: str | None = None
    wiki_url: str | None = None


class CandidateMarker(BaseModel):
    """Lightweight candidate for the map view (all POIs considered, not just recommended)."""

    poi_id: str
    name: str
    category: str
    latitude: float
    longitude: float
    distance_km: float
    recommended: bool


class RecommendationResult(BaseModel):
    request: PreferenceProfile
    recommendations: list[RecommendationItem]
    candidates: list[CandidateMarker]
    summary: str
    generation_method: GenerationMethod
    provider: str | None
    model: str | None
    fallback_used: bool
    weather_status: Literal["available", "unavailable", "disabled"]
    weather: WeatherSummary | None
    candidates_found: int
    candidates_sent: int
    excluded: dict[str, int]
    data_origin: Literal["mock", "live", "mixed", "none"]
    warnings: list[str]
    generated_at: datetime
    disclaimer: str
    understood: list[str] = []
    intent_method: str = "none"
    season: str | None = None
    air_quality: AirQualityOut | None = None
    events: list[EventOut] = []
    itinerary: ItineraryOut | None = None
    semantic_method: str = "none"
    timings_ms: dict[str, float] = {}


class ProviderInfo(BaseModel):
    role: str
    provider: str
    model: str
    configured: bool


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]
    fallback_enabled: bool
    deterministic_fallback_enabled: bool


class RefineRequest(BaseModel):
    profile: PreferenceProfile
    message: str


class RefineResponse(BaseModel):
    profile: PreferenceProfile
    changes: list[str]
    result: RecommendationResult
