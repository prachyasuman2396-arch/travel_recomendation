"""Flickr API client — geotagged photo search around a point.

Verified against https://www.flickr.com/services/api/flickr.photos.search.html:
  * GET https://www.flickr.com/services/rest/?method=flickr.photos.search
  * ``lat``/``lon``/``radius`` (km, max 32), ``has_geo=1``, ``extras`` for metadata
  * ``per_page`` max 250; ``page`` for pagination
  * Errors come back as HTTP 200 with ``{"stat": "fail", "code": N}``
  * Geo queries need a limiting filter, so ``min_upload_date`` is always sent

Photos are only *candidates* for a POI; association happens later with
confidence scoring. Photo counts are an activity signal, never visitor counts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import ExternalServiceError
from app.core.http import send
from app.core.retry import with_retries
from app.services.data_collection.base import CollectionResult, DataOrigin, FlickrPhotoRecord, Rejection

logger = logging.getLogger(__name__)

BASE_URL = "https://www.flickr.com/services/rest/"
PROVIDER = "flickr"
MAX_RADIUS_KM = 32.0
PER_PAGE = 250
EXTRAS = "geo,tags,date_upload,date_taken,owner_name,license,url_m"
# Flickr error codes that are transient (service unavailable / write ops disabled)
RETRYABLE_CODES = {105, 106}
AUTH_CODES = {98, 99, 100}


class _Photo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    owner: str | None = None
    ownername: str | None = None
    title: str | None = None
    tags: str = ""
    latitude: float
    longitude: float
    accuracy: int | None = None
    dateupload: str | None = None
    datetaken: str | None = None
    license: str | None = None
    url_m: str | None = None


def _parse_taken(value: str | None) -> datetime | None:
    if not value or value.startswith("0000"):
        return None
    try:
        # Flickr returns local time without zone; store as naive-UTC-labelled for ordering only.
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


class FlickrClient:
    data_origin = "live"

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, max_attempts: int = 3,
                 lookback_days: int = 3650) -> None:
        if not api_key:
            raise ValueError("Flickr API key is required")
        self._key = api_key
        self._http = http
        self._attempts = max_attempts
        self._lookback = timedelta(days=lookback_days)

    async def _get_page(self, params: dict) -> dict:
        async def call() -> dict:
            request = self._http.build_request("GET", BASE_URL, params=params)
            payload = (await send(self._http, request, PROVIDER)).json()
            if payload.get("stat") != "ok":
                code = int(payload.get("code", 0))
                message = payload.get("message", "unknown error")
                raise ExternalServiceError(f"Flickr error {code}: {message}", provider=PROVIDER,
                                           retryable=code in RETRYABLE_CODES,
                                           status=401 if code in AUTH_CODES else None)
            return payload

        return await with_retries(call, attempts=self._attempts, operation="flickr.photos.search")

    async def search_photos(self, latitude: float, longitude: float, radius_km: float,
                            max_pages: int = 2) -> CollectionResult[FlickrPhotoRecord]:
        result: CollectionResult[FlickrPhotoRecord] = CollectionResult()
        min_upload = int((datetime.now(UTC) - self._lookback).timestamp())
        seen: set[str] = set()
        page, pages = 1, 1
        while page <= min(pages, max_pages):
            params = {
                "method": "flickr.photos.search", "api_key": self._key, "format": "json", "nojsoncallback": 1,
                "lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}",
                "radius": min(radius_km, MAX_RADIUS_KM), "radius_units": "km",
                "has_geo": 1, "content_types": 0, "safe_search": 1, "min_upload_date": min_upload,
                "extras": EXTRAS, "per_page": PER_PAGE, "page": page,
            }
            payload = await self._get_page(params)
            result.requests_made += 1
            result.raw_pages.append(payload)
            photos = payload.get("photos") or {}
            pages = int(photos.get("pages") or 1)
            self._parse(photos.get("photo") or [], result, seen)
            page += 1
        logger.info("flickr_collected", extra={"records": len(result.records), "requests": result.requests_made})
        return result

    @staticmethod
    def _parse(items: list, result: CollectionResult[FlickrPhotoRecord], seen: set[str],
               origin: DataOrigin = "live") -> None:
        now = datetime.now(UTC)
        for raw in items:
            raw_id = str((raw or {}).get("id"))
            if raw_id in seen:
                continue
            seen.add(raw_id)
            try:
                photo = _Photo.model_validate(raw)
            except ValidationError:
                result.rejected.append(Rejection(provider=PROVIDER, external_id=(raw or {}).get("id"),
                                                 reason="schema/geo fields missing"))
                continue
            uploaded = (datetime.fromtimestamp(int(photo.dateupload), UTC)
                        if photo.dateupload and photo.dateupload.isdigit() else None)
            result.records.append(FlickrPhotoRecord(
                external_id=photo.id, owner_id=photo.owner, owner_name=photo.ownername,
                title=photo.title or None, tags=tuple(t for t in photo.tags.split() if t),
                latitude=photo.latitude, longitude=photo.longitude, accuracy=photo.accuracy,
                uploaded_at=uploaded, taken_at=_parse_taken(photo.datetaken), license_id=photo.license,
                photo_url=photo.url_m,
                page_url=f"https://www.flickr.com/photos/{photo.owner}/{photo.id}" if photo.owner else None,
                data_origin=origin, fetched_at=now,
            ))
