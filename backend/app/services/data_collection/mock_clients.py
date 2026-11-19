"""Mock collectors: same interfaces as the live clients, fed from data/sample/.

They run the *real* response parsers on provider-shaped mock payloads and
stamp every record ``data_origin="mock"``. The geographic filter is applied
here (haversine) because a real API would only return nearby results.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.data_collection.base import (
    CollectionResult,
    FlickrPhotoRecord,
    PlaceRecord,
    YelpBusinessRecord,
    YelpReviewRecord,
)
from app.services.data_collection.flickr_client import FlickrClient
from app.services.data_collection.places_client import GooglePlacesClient
from app.services.data_collection.yelp_client import YelpClient
from app.services.entity_resolution.geo import haversine_m
from app.services.preprocessing.cleaning import valid_coordinates


def _load(sample_dir: Path, name: str) -> dict:
    return json.loads((sample_dir / name).read_text(encoding="utf-8"))


def _within(item: dict, lat: float, lon: float, radius_m: float, lat_key: str, lon_key: str) -> bool:
    try:
        plat, plon = float(item[lat_key]), float(item[lon_key])
    except (KeyError, TypeError, ValueError):
        return True  # keep malformed items so validation/cleaning is exercised
    if not valid_coordinates(plat, plon):
        return True  # likewise: invalid coordinates must reach the cleaning stage
    return haversine_m(lat, lon, plat, plon) <= radius_m


class MockPlacesCollector:
    data_origin = "mock"

    def __init__(self, sample_dir: Path) -> None:
        self._sample_dir = sample_dir

    async def search_area(self, latitude: float, longitude: float, radius_m: float,
                          included_types: list[str] | None = None) -> CollectionResult[PlaceRecord]:
        payload = _load(self._sample_dir, "places_nearby.json")
        places = [
            p for p in payload["places"]
            if _within(p.get("location") or {}, latitude, longitude, radius_m, "latitude", "longitude")
        ]
        result: CollectionResult[PlaceRecord] = CollectionResult(requests_made=1)
        result.raw_pages.append({"places": places})
        GooglePlacesClient._parse_places({"places": places}, result, set(), origin="mock")
        return result


class MockFlickrCollector:
    data_origin = "mock"

    def __init__(self, sample_dir: Path) -> None:
        self._sample_dir = sample_dir

    async def search_photos(self, latitude: float, longitude: float, radius_km: float,
                            max_pages: int = 2) -> CollectionResult[FlickrPhotoRecord]:
        payload = _load(self._sample_dir, "flickr_photos.json")
        photos = [p for p in payload["photos"]["photo"]
                  if _within(p, latitude, longitude, radius_km * 1000, "latitude", "longitude")]
        result: CollectionResult[FlickrPhotoRecord] = CollectionResult(requests_made=1)
        result.raw_pages.append({"photos": {"photo": photos}})
        FlickrClient._parse(photos, result, set(), origin="mock")
        return result


class MockYelpCollector:
    data_origin = "mock"

    def __init__(self, sample_dir: Path) -> None:
        self._sample_dir = sample_dir

    async def search_businesses(self, latitude: float, longitude: float, radius_m: int,
                                categories: list[str] | None = None) -> CollectionResult[YelpBusinessRecord]:
        payload = _load(self._sample_dir, "yelp_businesses.json")
        items = [b for b in payload["businesses"]
                 if _within(b.get("coordinates") or {}, latitude, longitude, radius_m, "latitude", "longitude")]
        result: CollectionResult[YelpBusinessRecord] = CollectionResult(requests_made=1)
        result.raw_pages.append({"businesses": items})
        YelpClient._parse_businesses(items, result, set(), origin="mock")
        return result

    async def get_reviews(self, business_id: str) -> CollectionResult[YelpReviewRecord]:
        payload = _load(self._sample_dir, "yelp_reviews.json")["by_business"].get(business_id, {"reviews": []})
        result: CollectionResult[YelpReviewRecord] = CollectionResult(requests_made=1)
        result.raw_pages.append(payload)
        YelpClient.parse_reviews(payload, business_id, result, origin="mock")
        return result
