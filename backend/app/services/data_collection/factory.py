"""Choose live vs mock collectors.

``mode="auto"``:
  * keyed sources (places, yelp, flickr) are live when their key is configured, otherwise mock
  * keyless sources (osm, wikipedia) follow the Google Places key, so a demo without keys stays
    entirely mock and never mixes real OpenStreetMap places into sample data
``mode="live"`` without a required key raises ServiceNotConfiguredError (never silently mocks).
"""

from __future__ import annotations

from typing import Literal

import httpx

from app.core.config import Settings
from app.core.errors import ServiceNotConfiguredError
from app.services.data_collection.flickr_client import FlickrClient
from app.services.data_collection.mock_clients import (
    MockFlickrCollector,
    MockPlacesCollector,
    MockYelpCollector,
)
from app.services.data_collection.osm_client import MockOsmCollector, OverpassClient
from app.services.data_collection.places_client import GooglePlacesClient
from app.services.data_collection.wikipedia_client import MockWikipediaCollector, WikipediaClient
from app.services.data_collection.yelp_client import YelpClient

Mode = Literal["auto", "live", "mock"]
Source = Literal["places", "osm", "wikipedia", "flickr", "yelp"]
KEYLESS: set[str] = {"osm", "wikipedia"}
ALL_SOURCES: tuple[str, ...] = ("places", "osm", "wikipedia", "yelp", "flickr")


def _key(settings: Settings, source: str) -> str | None:
    secret = {"places": settings.places_api_key, "flickr": settings.flickr_api_key,
              "yelp": settings.yelp_api_key}.get(source)
    return secret.get_secret_value() if secret else None


def resolve_mode(source: str, settings: Settings, mode: Mode) -> Literal["live", "mock"]:
    if mode != "auto":
        return mode
    if source in KEYLESS:
        return "live" if settings.places_api_key else "mock"
    return "live" if _key(settings, source) else "mock"


def build_collector(source: Source, settings: Settings, http: httpx.AsyncClient, mode: Mode = "auto"):
    sample_dir = settings.data_dir / "sample"
    resolved = resolve_mode(source, settings, mode)
    if resolved == "mock":
        mocks = {"places": MockPlacesCollector, "flickr": MockFlickrCollector, "yelp": MockYelpCollector,
                 "osm": MockOsmCollector, "wikipedia": MockWikipediaCollector}
        return mocks[source](sample_dir)
    if source == "osm":
        return OverpassClient(http, settings.overpass_url, settings.user_agent)
    if source == "wikipedia":
        return WikipediaClient(http, settings.user_agent, settings.wikipedia_language)
    key = _key(settings, source)
    if not key:
        raise ServiceNotConfiguredError(f"Live {source} collection is disabled: API key not configured")
    if source == "places":
        return GooglePlacesClient(key, http, include_reviews=settings.places_include_reviews)
    if source == "flickr":
        return FlickrClient(key, http)
    return YelpClient(key, http)


def default_sources(settings: Settings) -> list[str]:
    return [s.strip() for s in settings.default_ingest_sources.split(",") if s.strip() in ALL_SOURCES]


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(settings.api_timeout, connect=min(10.0, settings.api_timeout))
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent})
