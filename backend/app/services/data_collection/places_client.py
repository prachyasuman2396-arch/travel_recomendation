"""Google Places API (New) client — Nearby Search + Text Search.

Verified against the official docs (Nov 2026):
  * POST https://places.googleapis.com/v1/places:searchNearby
  * Headers ``X-Goog-Api-Key`` and a *required* ``X-Goog-FieldMask``
  * ``maxResultCount`` 1–20; ``locationRestriction.circle.radius`` 0–50000 m
  * Nearby Search has NO pagination -> we tile large areas into smaller circles
  * Text Search (``places:searchText``) supports ``pageSize`` + ``pageToken``

Terms note: Google allows caching ``place_id`` indefinitely but restricts other
content; raw responses are therefore NOT persisted by default (RAW_STORE_PLACES).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import ExternalServiceError
from app.core.http import send
from app.core.retry import with_retries
from app.services.data_collection.base import (
    CollectionResult,
    DataOrigin,
    PlacePhoto,
    PlaceRecord,
    PlaceReview,
    Rejection,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://places.googleapis.com/v1"
PROVIDER = "google_places"
MAX_RADIUS_M = 50_000.0
MAX_RESULTS_PER_CALL = 20

# Only request what we store: field masks keep responses small and billing predictable.
FIELD_MASK = ",".join(
    "places." + f
    for f in (
        "id", "displayName", "formattedAddress", "location", "types", "primaryType",
        "editorialSummary", "rating", "userRatingCount", "regularOpeningHours",
        "websiteUri", "googleMapsUri", "businessStatus", "photos", "accessibilityOptions",
    )
)
# Enterprise + Atmosphere SKU fields (higher cost) — controlled by PLACES_INCLUDE_REVIEWS.
ATMOSPHERE_MASK = "places.reviews,places.goodForChildren"

DEFAULT_TOURISM_TYPES = [
    "tourist_attraction", "park", "museum", "hindu_temple", "zoo", "amusement_park",
    "national_park", "art_gallery", "aquarium", "church", "mosque", "hiking_area", "shopping_mall",
]


class _LocalizedText(BaseModel):
    text: str | None = None


class _LatLng(BaseModel):
    latitude: float
    longitude: float


class _Place(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    displayName: _LocalizedText | None = None
    formattedAddress: str | None = None
    location: _LatLng
    types: list[str] = []
    primaryType: str | None = None
    editorialSummary: _LocalizedText | None = None
    rating: float | None = None
    userRatingCount: int | None = None
    regularOpeningHours: dict | None = None
    websiteUri: str | None = None
    googleMapsUri: str | None = None
    businessStatus: str | None = None
    reviews: list[dict] = []
    photos: list[dict] = []
    accessibilityOptions: dict | None = None
    goodForChildren: bool | None = None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reviews(raw: list[dict], place_id: str) -> tuple[PlaceReview, ...]:
    out = []
    for i, r in enumerate(raw or []):
        text = (r.get("text") or {}).get("text") or (r.get("originalText") or {}).get("text")
        author = r.get("authorAttribution") or {}
        out.append(PlaceReview(
            external_id=r.get("name") or f"{place_id}/review/{i}", rating=r.get("rating"), text=text,
            language=(r.get("text") or {}).get("languageCode"), published_at=_parse_time(r.get("publishTime")),
            author_name=author.get("displayName"), author_url=author.get("uri") or None,
        ))
    return tuple(out)


def _photos(raw: list[dict]) -> tuple[PlacePhoto, ...]:
    return tuple(
        PlacePhoto(name=p["name"], width=p.get("widthPx"), height=p.get("heightPx"),
                   attributions=tuple(a.get("displayName", "") for a in p.get("authorAttributions") or []))
        for p in (raw or [])[:5] if p.get("name")
    )


def tile_circles(latitude: float, longitude: float, radius_m: float, tile_radius_m: float) -> list[tuple]:
    """Cover a circle with a grid of smaller circles (centre lat, centre lon, radius).

    Nearby Search returns at most 20 places per call with no paging, so a large
    radius is split into overlapping tiles. Spacing of r*sqrt(2) gives full coverage.
    """
    if radius_m <= tile_radius_m:
        return [(latitude, longitude, radius_m)]
    step_m = tile_radius_m * math.sqrt(2)
    n = math.ceil(radius_m / step_m)
    tiles = []
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * max(math.cos(math.radians(latitude)), 1e-6)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            dx, dy = i * step_m, j * step_m
            if math.hypot(dx, dy) > radius_m + tile_radius_m:
                continue
            tiles.append((latitude + dy / m_per_deg_lat, longitude + dx / m_per_deg_lon, tile_radius_m))
    return tiles


class GooglePlacesClient:
    data_origin = "live"

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, max_attempts: int = 3,
                 tile_radius_m: float = 5_000.0, max_tiles: int = 25, language: str = "en",
                 include_reviews: bool = True) -> None:
        if not api_key:
            raise ValueError("Google Places API key is required")
        self._key = api_key
        self._http = http
        self._attempts = max_attempts
        self._tile_radius_m = tile_radius_m
        self._max_tiles = max_tiles
        self._language = language
        self._mask = FIELD_MASK + ("," + ATMOSPHERE_MASK if include_reviews else "")

    def _headers(self) -> dict[str, str]:
        return {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": self._mask, "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict) -> dict:
        async def call() -> dict:
            request = self._http.build_request("POST", f"{BASE_URL}/{path}", json=body, headers=self._headers())
            response = await send(self._http, request, PROVIDER)
            try:
                return response.json()
            except ValueError as exc:
                raise ExternalServiceError("Places returned invalid JSON", provider=PROVIDER) from exc

        return await with_retries(call, attempts=self._attempts, operation="places." + path)

    async def search_area(self, latitude: float, longitude: float, radius_m: float,
                          included_types: list[str] | None = None) -> CollectionResult[PlaceRecord]:
        radius_m = min(radius_m, MAX_RADIUS_M * 4)  # sanity cap; tiles enforce the per-call limit
        tiles = tile_circles(latitude, longitude, radius_m, min(self._tile_radius_m, MAX_RADIUS_M))
        result: CollectionResult[PlaceRecord] = CollectionResult()
        if len(tiles) > self._max_tiles:
            result.notes.append(f"area needs {len(tiles)} tiles; capped at {self._max_tiles} to limit quota")
            tiles = tiles[: self._max_tiles]

        seen: set[str] = set()
        for lat, lon, r in tiles:
            body = {
                "includedTypes": included_types or DEFAULT_TOURISM_TYPES,
                "maxResultCount": MAX_RESULTS_PER_CALL,
                "languageCode": self._language,
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": r}},
            }
            payload = await self._post("places:searchNearby", body)
            result.requests_made += 1
            result.raw_pages.append(payload)
            self._parse_places(payload, result, seen)
        logger.info("places_collected", extra={"records": len(result.records), "requests": result.requests_made})
        return result

    async def search_text(self, query: str, latitude: float, longitude: float, radius_m: float,
                          max_pages: int = 3) -> CollectionResult[PlaceRecord]:
        """Text Search with pageToken pagination (up to ``max_pages`` pages of 20)."""
        result: CollectionResult[PlaceRecord] = CollectionResult()
        seen: set[str] = set()
        token: str | None = None
        for _ in range(max_pages):
            body: dict = {
                "textQuery": query,
                "pageSize": MAX_RESULTS_PER_CALL,
                "languageCode": self._language,
                "locationBias": {"circle": {"center": {"latitude": latitude, "longitude": longitude},
                                            "radius": min(radius_m, MAX_RADIUS_M)}},
            }
            if token:
                body["pageToken"] = token
            # nextPageToken must be in the mask for Text Search pagination
            payload = await self._post_text(body)
            result.requests_made += 1
            result.raw_pages.append(payload)
            self._parse_places(payload, result, seen)
            token = payload.get("nextPageToken")
            if not token:
                break
        return result

    async def _post_text(self, body: dict) -> dict:
        async def call() -> dict:
            headers = self._headers() | {"X-Goog-FieldMask": self._mask + ",nextPageToken"}
            request = self._http.build_request("POST", f"{BASE_URL}/places:searchText", json=body, headers=headers)
            return (await send(self._http, request, PROVIDER)).json()

        return await with_retries(call, attempts=self._attempts, operation="places.searchText")

    async def get_photo_uri(self, photo_name: str, max_width: int = 800) -> str | None:
        """Resolve a photo resource name to a short-lived URL (key stays on the server)."""
        if not photo_name.startswith("places/") or "/photos/" not in photo_name:
            return None

        async def call() -> dict:
            request = self._http.build_request(
                "GET", f"{BASE_URL}/{photo_name}/media",
                params={"maxWidthPx": max_width, "skipHttpRedirect": "true"},
                headers={"X-Goog-Api-Key": self._key},
            )
            return (await send(self._http, request, PROVIDER)).json()

        payload = await with_retries(call, attempts=self._attempts, operation="places.photo")
        return payload.get("photoUri")

    async def get_status(self, place_id: str) -> dict:
        """Place Details (Pro): business status + opening hours, for freshness checks."""
        async def call() -> dict:
            request = self._http.build_request(
                "GET", f"{BASE_URL}/places/{place_id}",
                headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": "id,businessStatus,regularOpeningHours"},
            )
            return (await send(self._http, request, PROVIDER)).json()

        return await with_retries(call, attempts=self._attempts, operation="places.details")

    @staticmethod
    def _parse_places(payload: dict, result: CollectionResult[PlaceRecord], seen: set[str],
                      origin: DataOrigin = "live") -> None:
        places = payload.get("places", [])
        if not isinstance(places, list):
            raise ExternalServiceError("Places response 'places' is not a list", provider=PROVIDER)
        now = datetime.now(UTC)
        for raw in places:
            try:
                place = _Place.model_validate(raw)
            except ValidationError as exc:
                result.rejected.append(Rejection(provider=PROVIDER, external_id=(raw or {}).get("id"),
                                                 reason=f"schema: {exc.errors()[0]['msg']}"))
                continue
            if place.id in seen:
                continue
            seen.add(place.id)
            name = place.displayName.text if place.displayName else None
            if not name:
                result.rejected.append(Rejection(provider=PROVIDER, external_id=place.id, reason="missing name"))
                continue
            result.records.append(PlaceRecord(
                external_id=place.id, name=name, address=place.formattedAddress,
                latitude=place.location.latitude, longitude=place.location.longitude,
                types=tuple(place.types), primary_type=place.primaryType,
                description=place.editorialSummary.text if place.editorialSummary else None,
                rating=place.rating, rating_count=place.userRatingCount,
                opening_hours=place.regularOpeningHours, website=place.websiteUri,
                maps_url=place.googleMapsUri, business_status=place.businessStatus,
                reviews=_reviews(place.reviews, place.id), photos=_photos(place.photos),
                wheelchair_accessible=(place.accessibilityOptions or {}).get("wheelchairAccessibleEntrance"),
                good_for_children=place.goodForChildren,
                data_origin=origin, fetched_at=now,
            ))
