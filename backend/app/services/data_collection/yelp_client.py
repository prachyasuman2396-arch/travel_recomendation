"""Yelp Places (Fusion) API client.

Verified against docs.developer.yelp.com (Nov 2026):
  * GET https://api.yelp.com/v3/businesses/search — ``radius`` ≤ 40000 m,
    ``limit`` ≤ 50, ``offset``; ``limit + offset`` ≤ 240
  * GET https://api.yelp.com/v3/businesses/{id}/reviews — returns review
    *excerpts* (max 3 on standard plans; not available on the Base plan)
  * Auth: ``Authorization: Bearer <key>``
  * Daily quota exhaustion (``ACCESS_LIMIT_REACHED``) is not retryable

Yelp coverage outside North America/Europe can be sparse; empty results are
normal and handled. Review text is never fabricated or expanded.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import ExternalServiceError, RateLimitedError
from app.core.http import send
from app.core.retry import with_retries
from app.services.data_collection.base import (
    CollectionResult,
    DataOrigin,
    Rejection,
    YelpBusinessRecord,
    YelpReviewRecord,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.yelp.com/v3"
PROVIDER = "yelp"
MAX_RADIUS_M = 40_000
PAGE_LIMIT = 50
MAX_RESULTS = 240
DEFAULT_CATEGORIES = [
    "landmarks", "museums", "parks", "religiousorgs", "zoos", "hiking", "artgalleries",
    "amusementparks", "beaches", "gardens", "tours",
]


class _Coordinates(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class _Category(BaseModel):
    alias: str
    title: str | None = None


class _Location(BaseModel):
    model_config = ConfigDict(extra="ignore")
    display_address: list[str] = []


class _Business(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str | None = None
    coordinates: _Coordinates = _Coordinates()
    categories: list[_Category] = []
    rating: float | None = None
    review_count: int | None = None
    url: str | None = None
    is_closed: bool = False
    location: _Location = _Location()


class _Review(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    rating: float | None = None
    text: str | None = None
    time_created: str | None = None


class YelpClient:
    data_origin = "live"

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, max_attempts: int = 3) -> None:
        if not api_key:
            raise ValueError("Yelp API key is required")
        self._key = api_key
        self._http = http
        self._attempts = max_attempts

    async def _get(self, path: str, params: dict) -> dict:
        async def call() -> dict:
            request = self._http.build_request(
                "GET", f"{BASE_URL}{path}", params=params, headers={"Authorization": f"Bearer {self._key}"}
            )
            try:
                return (await send(self._http, request, PROVIDER)).json()
            except RateLimitedError as exc:
                # Yelp uses 429 both for per-second throttling (retry) and daily quota (do not retry).
                if "ACCESS_LIMIT_REACHED" in str(exc.details.get("body", "")):
                    raise ExternalServiceError("Yelp daily quota reached", provider=PROVIDER,
                                               status=429, retryable=False) from exc
                raise

        return await with_retries(call, attempts=self._attempts, operation="yelp" + path)

    async def search_businesses(self, latitude: float, longitude: float, radius_m: int,
                                categories: list[str] | None = None) -> CollectionResult[YelpBusinessRecord]:
        result: CollectionResult[YelpBusinessRecord] = CollectionResult()
        seen: set[str] = set()
        offset, total = 0, None
        while offset < MAX_RESULTS and (total is None or offset < total):
            limit = min(PAGE_LIMIT, MAX_RESULTS - offset)
            payload = await self._get("/businesses/search", {
                "latitude": latitude, "longitude": longitude, "radius": min(int(radius_m), MAX_RADIUS_M),
                "categories": ",".join(categories or DEFAULT_CATEGORIES), "limit": limit, "offset": offset,
                "sort_by": "best_match",
            })
            result.requests_made += 1
            result.raw_pages.append(payload)
            total = int(payload.get("total") or 0)
            businesses = payload.get("businesses") or []
            self._parse_businesses(businesses, result, seen)
            if not businesses:
                break
            offset += limit
        if total == 0:
            result.notes.append("Yelp returned no businesses for this area (coverage may be limited)")
        return result

    async def get_reviews(self, business_id: str) -> CollectionResult[YelpReviewRecord]:
        result: CollectionResult[YelpReviewRecord] = CollectionResult()
        try:
            payload = await self._get(f"/businesses/{business_id}/reviews", {"limit": 3, "sort_by": "yelp_sort"})
        except ExternalServiceError as exc:
            if exc.status == 403:
                # Plan does not include review excerpts — a normal, non-fatal condition.
                result.notes.append("review excerpts not available on this Yelp plan")
                return result
            raise
        result.requests_made += 1
        result.raw_pages.append(payload)
        self.parse_reviews(payload, business_id, result)
        return result

    @staticmethod
    def parse_reviews(payload: dict, business_id: str, result: CollectionResult[YelpReviewRecord],
                      origin: DataOrigin = "live") -> None:
        now = datetime.now(UTC)
        for raw in payload.get("reviews") or []:
            try:
                review = _Review.model_validate(raw)
            except ValidationError:
                result.rejected.append(Rejection(provider=PROVIDER, reason="invalid review payload"))
                continue
            created = None
            if review.time_created:
                try:
                    created = datetime.strptime(review.time_created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                except ValueError:
                    created = None
            result.records.append(YelpReviewRecord(
                external_id=review.id, business_external_id=business_id, rating=review.rating,
                text=review.text, created_at=created, data_origin=origin, fetched_at=now,
            ))

    @staticmethod
    def _parse_businesses(items: list, result: CollectionResult[YelpBusinessRecord], seen: set[str],
                          origin: DataOrigin = "live") -> None:
        now = datetime.now(UTC)
        for raw in items:
            try:
                biz = _Business.model_validate(raw)
            except ValidationError:
                result.rejected.append(Rejection(provider=PROVIDER, external_id=(raw or {}).get("id"),
                                                 reason="invalid business payload"))
                continue
            if biz.id in seen:
                continue
            seen.add(biz.id)
            if biz.coordinates.latitude is None or biz.coordinates.longitude is None or not biz.name:
                result.rejected.append(Rejection(provider=PROVIDER, external_id=biz.id,
                                                 reason="missing name or coordinates"))
                continue
            result.records.append(YelpBusinessRecord(
                external_id=biz.id, name=biz.name, address=", ".join(biz.location.display_address) or None,
                latitude=biz.coordinates.latitude, longitude=biz.coordinates.longitude,
                categories=tuple(c.alias for c in biz.categories), rating=biz.rating,
                review_count=biz.review_count, url=biz.url, is_closed=biz.is_closed,
                data_origin=origin, fetched_at=now,
            ))
