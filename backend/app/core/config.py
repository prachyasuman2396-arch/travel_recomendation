"""Centralised, validated configuration (Pydantic Settings).

Design decision: only the composition root (``app/api/deps.py``, scripts) reads
``get_settings()``. Domain services receive plain values through their
constructors, which keeps them testable without environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["gemini", "groq", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    data_dir: Path = Path(__file__).resolve().parents[3] / "data"

    # Database
    database_url: str = "postgresql+psycopg://tourist:change-me@localhost:5432/tourist"
    db_pool_size: int = Field(5, ge=1, le=50)
    db_max_overflow: int = Field(10, ge=0, le=100)

    # API
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    admin_api_token: SecretStr | None = None
    api_timeout: float = Field(30.0, gt=0, le=300)

    # Data providers
    places_api_key: SecretStr | None = None
    flickr_api_key: SecretStr | None = None
    flickr_api_secret: SecretStr | None = None
    yelp_api_key: SecretStr | None = None
    yelp_content_retention_hours: int = Field(24, ge=1)
    raw_store_places: bool = False
    raw_store_flickr: bool = True
    raw_store_yelp: bool = False
    # India: Google + OpenStreetMap + Wikipedia are the default sources; Yelp/Flickr are optional
    # (weak Indian coverage) and only run when requested explicitly.
    default_ingest_sources: str = "places,osm,wikipedia"
    # Google reviews/goodForChildren are billed at the Enterprise + Atmosphere SKU; disable to save cost.
    places_include_reviews: bool = True
    google_content_retention_days: int = Field(30, ge=1)
    raw_store_osm: bool = True  # ODbL allows storage with attribution
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    wikipedia_language: str = "en"
    contact_email: str | None = None  # sent in the User-Agent for OSM/Wikimedia etiquette

    # LLM
    llm_provider: LLMProviderName = "gemini"
    llm_fallback_enabled: bool = True
    llm_fallback_provider: LLMProviderName | None = "groq"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_structured_mode: Literal["json_schema", "json_object"] = "json_schema"
    llm_temperature: float = Field(0.2, ge=0.0, le=2.0)
    llm_max_retries: int = Field(3, ge=1, le=10)
    llm_timeout: float = Field(60.0, gt=0, le=600)
    llm_max_output_tokens: int = Field(4096, ge=256, le=65536)
    llm_max_prompt_chars: int = Field(60000, ge=4000)

    # Recommendation
    recommendation_candidate_limit: int = Field(40, ge=1, le=200)
    recommendation_max_results: int = Field(8, ge=1, le=30)
    recommendation_deterministic_fallback: bool = False
    assumed_avg_speed_kmh: float = Field(22.0, gt=0, le=120)  # Indian city traffic
    road_detour_factor: float = Field(1.35, ge=1.0, le=3.0)  # straight line -> road distance estimate
    # OSRM routing (self-hosted recommended: see docs/routing.md). Empty -> labelled estimate.
    osrm_url: str | None = None
    # Intent parsing of free text: "rules" (deterministic, offline) or "llm" (uses the LLM chain, rules as fallback)
    intent_parser: Literal["rules", "llm"] = "rules"
    # Semantic matching of free text: "gemini" embeddings (needs GEMINI_API_KEY) or "tfidf" (offline)
    semantic_backend: Literal["gemini", "tfidf"] = "tfidf"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = Field(768, ge=128, le=3072)
    itinerary_max_stops: int = Field(5, ge=1, le=7)

    # Weather
    weather_provider: Literal["open_meteo", "mock", "none"] = "open_meteo"
    weather_api_key: SecretStr | None = None
    weather_cache_ttl_seconds: int = Field(600, ge=0)
    air_quality_enabled: bool = True

    # Sentiment
    sentiment_backend: Literal["lexicon", "transformers"] = "lexicon"
    sentiment_model: str = "nlptown/bert-base-multilingual-uncased-sentiment"
    sentiment_min_reviews: int = Field(3, ge=1)

    # Entity resolution
    match_high_threshold: float = Field(0.80, gt=0, le=1)
    match_medium_threshold: float = Field(0.60, gt=0, le=1)
    match_max_distance_m: float = Field(400.0, gt=0)

    @field_validator(
        "places_api_key", "flickr_api_key", "flickr_api_secret", "yelp_api_key",
        "gemini_api_key", "groq_api_key", "weather_api_key", "admin_api_token",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_none(cls, value: object) -> object:
        # An empty "KEY=" line in .env means "not configured", not "empty key".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("osrm_url", "contact_email", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def user_agent(self) -> str:
        """OSM Nominatim/Overpass and Wikimedia ask clients to identify themselves."""
        contact = f" ({self.contact_email})" if self.contact_email else ""
        return f"RadiusTouristRecommender/0.2{contact}"

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.match_medium_threshold >= self.match_high_threshold:
            raise ValueError("MATCH_MEDIUM_THRESHOLD must be lower than MATCH_HIGH_THRESHOLD")
        if self.llm_fallback_provider == self.llm_provider:
            # Falling back to the same provider is pointless; disable rather than fail startup.
            self.llm_fallback_provider = None
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def provider_status(self) -> dict[str, bool]:
        """Which live integrations are configured (never exposes the keys)."""
        return {
            "google_places": self.places_api_key is not None,
            "flickr": self.flickr_api_key is not None,
            "yelp": self.yelp_api_key is not None,
            "gemini": self.gemini_api_key is not None,
            "groq": self.groq_api_key is not None,
            "weather": self.weather_provider == "open_meteo",
            "openstreetmap": True,
            "wikipedia": True,
            "routing": self.osrm_url is not None,
            "semantic_embeddings": self.semantic_backend == "gemini" and self.gemini_api_key is not None,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
