"""OpenStreetMap via the Overpass API — free, and strong on Indian places Google misses
(small temples, ghats, viewpoints, waterfalls) plus local-script names (name:or, name:hi).

Usage policy: https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
  * identify the client (User-Agent), keep queries bounded, back off on 429/504
  * data is ODbL: attribution "© OpenStreetMap contributors" is shown in the UI
For heavy use, run your own Overpass instance and set OVERPASS_URL.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.errors import ExternalServiceError
from app.core.http import send
from app.core.retry import with_retries
from app.services.data_collection.base import CollectionResult, DataOrigin, OsmPlaceRecord, Rejection
from app.services.preprocessing.categories import map_types

logger = logging.getLogger(__name__)
PROVIDER = "osm"
MAX_RADIUS_M = 50_000

# Tag filters for tourist-relevant features. Named features only.
_FILTERS = [
    'nwr["tourism"~"^(attraction|museum|viewpoint|zoo|theme_park|gallery|picnic_site)$"]["name"]',
    'nwr["historic"~"^(monument|archaeological_site|memorial|fort|ruins|temple|castle)$"]["name"]',
    'nwr["natural"~"^(waterfall|beach|peak|cave_entrance|hot_spring)$"]["name"]',
    'nwr["leisure"~"^(park|garden|nature_reserve)$"]["name"]',
    'nwr["amenity"="place_of_worship"]["name"]["wikidata"]',  # notable shrines only (have a Wikidata item)
    'nwr["boundary"="protected_area"]["name"]',
    'nwr["amenity"="marketplace"]["name"]',
    'nwr["shop"="mall"]["name"]',
]

# Food is dense in Indian cities, so it is a separate, smaller, capped query (see build_food_query).
_FOOD_FILTERS = [
    'nwr["amenity"~"^(restaurant|cafe)$"]["name"]["cuisine"]',
    'nwr["amenity"~"^(restaurant|cafe)$"]["name"]["wikidata"]',
]
FOOD_RADIUS_M = 4_000
FOOD_LIMIT = 80

# OSM tag value -> our categories
OSM_TAG_MAP: dict[str, tuple[str, ...]] = {
    "tourism=zoo": ("wildlife", "nature"),
    "tourism=museum": ("cultural", "historical"),
    "tourism=gallery": ("cultural",),
    "tourism=viewpoint": ("nature", "adventure"),
    "tourism=theme_park": ("entertainment",),
    "tourism=picnic_site": ("nature", "parks_gardens"),
    "historic=temple": ("religious", "historical"),
    "historic=archaeological_site": ("historical", "cultural"),
    "historic=monument": ("historical",),
    "historic=memorial": ("historical",),
    "historic=fort": ("historical",),
    "historic=ruins": ("historical",),
    "natural=waterfall": ("waterfalls", "nature"),
    "natural=beach": ("beaches", "nature"),
    "natural=peak": ("adventure", "nature"),
    "natural=cave_entrance": ("adventure", "historical"),
    "natural=hot_spring": ("nature",),
    "leisure=park": ("parks_gardens", "nature"),
    "leisure=garden": ("parks_gardens", "nature"),
    "leisure=nature_reserve": ("nature", "wildlife"),
    "boundary=protected_area": ("nature", "wildlife"),
    "amenity=place_of_worship": ("religious",),
    "amenity=marketplace": ("shopping", "cultural"),
    "shop=mall": ("shopping",),
    "amenity=restaurant": ("food",),
    "amenity=cafe": ("food",),
    "tourism=attraction": (),
}
_TAG_KEYS = ("historic", "tourism", "natural", "leisure", "boundary", "amenity", "shop")
LOCAL_NAME_KEYS = ("name:en", "name:or", "name:hi", "name:bn", "name:te", "name:ta", "name:kn", "name:ml",
                   "name:mr", "name:gu", "name:pa", "alt_name", "old_name", "official_name")


def osm_categories(tags: dict[str, str]) -> list[str]:
    return map_types([f"{k}={tags[k]}" for k in _TAG_KEYS if k in tags], OSM_TAG_MAP)


def build_query(latitude: float, longitude: float, radius_m: float, timeout_s: int = 6) -> str:
    r = int(min(radius_m, MAX_RADIUS_M))
    around = f"(around:{r},{latitude:.6f},{longitude:.6f})"
    body = "".join(f"{f}{around};" for f in _FILTERS)
    return f"[out:json][timeout:{timeout_s}];({body});out center tags 120;"


def build_food_query(latitude: float, longitude: float, radius_m: float, timeout_s: int = 30) -> str:
    r = int(min(radius_m, FOOD_RADIUS_M))
    around = f"(around:{r},{latitude:.6f},{longitude:.6f})"
    body = "".join(f"{f}{around};" for f in _FOOD_FILTERS)
    return f"[out:json][timeout:{timeout_s}];({body});out tags center {FOOD_LIMIT};"


def parse_overpass(payload: dict, origin: DataOrigin = "live") -> CollectionResult[OsmPlaceRecord]:
    result: CollectionResult[OsmPlaceRecord] = CollectionResult()
    if not isinstance(payload.get("elements"), list):
        raise ExternalServiceError("Overpass response has no 'elements' list", provider=PROVIDER)
    now = datetime.now(UTC)
    seen: set[str] = set()
    for el in payload["elements"]:
        ext_id = f"{el.get('type')}/{el.get('id')}"
        if ext_id in seen:
            continue
        seen.add(ext_id)
        tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
        name = tags.get("name:en") or tags.get("name")
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if not name:
            result.rejected.append(Rejection(provider=PROVIDER, external_id=ext_id, reason="missing name"))
            continue
        if lat is None or lon is None:
            result.rejected.append(Rejection(provider=PROVIDER, external_id=ext_id, reason="missing coordinates"))
            continue
        alt = tuple(dict.fromkeys(tags[k] for k in ("name", *LOCAL_NAME_KEYS) if k in tags and tags[k] != name))
        result.records.append(OsmPlaceRecord(
            external_id=ext_id, name=name, latitude=float(lat), longitude=float(lon), tags=tags, alt_names=alt,
            wikipedia=tags.get("wikipedia"), wikidata=tags.get("wikidata"), data_origin=origin, fetched_at=now,
        ))
    return result


class OverpassClient:
    data_origin = "live"

    def __init__(self, http: httpx.AsyncClient, url: str | list[str], user_agent: str, *, max_attempts: int = 3,
                 include_food: bool = False) -> None:
        self._http = http
        self._urls = [url] if isinstance(url, str) else list(url)
        self._ua = user_agent
        self._attempts = max_attempts
        self._include_food = include_food

    async def _post(self, url: str, query: str, attempts: int) -> dict:
        async def call() -> dict:
            request = self._http.build_request("POST", url, data={"data": query},
                                               headers={"User-Agent": self._ua})
            response = await send(self._http, request, PROVIDER)
            try:
                return response.json()
            except ValueError as exc:  # Overpass returns HTML/XML error pages on overload
                raise ExternalServiceError("Overpass returned a non-JSON response (server busy?)",
                                           provider=PROVIDER, retryable=True) from exc

        return await with_retries(call, attempts=attempts, operation="osm.overpass")

    async def _query(self, query: str, attempts: int) -> tuple[dict, str]:
        """Try each configured Overpass instance in turn (public servers are often busy)."""
        last: Exception | None = None
        for url in self._urls:
            try:
                return await self._post(url, query, attempts), url
            except ExternalServiceError as exc:
                last = exc
                logger.warning("overpass_instance_failed", extra={"url": url, "error": str(exc)})
        raise ExternalServiceError(f"All Overpass servers failed ({len(self._urls)} tried): {last}",
                                   provider=PROVIDER, retryable=True)

    async def search_area(self, latitude: float, longitude: float, radius_m: float) -> CollectionResult[OsmPlaceRecord]:
        payload, used = await self._query(build_query(latitude, longitude, radius_m), self._attempts)
        if self._include_food:
            try:  # food is a nice-to-have: never fail the whole area because of it
                food, _ = await self._query(build_food_query(latitude, longitude, radius_m), 1)
                payload = {**payload, "elements": [*payload.get("elements", []), *food.get("elements", [])]}
            except ExternalServiceError as exc:
                payload = {**payload, "remark": f"food query skipped: {exc}"}
        result = parse_overpass(payload)
        result.requests_made = 2 if self._include_food else 1
        result.notes.append(f"Overpass server: {used}")
        result.raw_pages.append(payload)
        if payload.get("remark"):
            result.notes.append(f"Overpass remark: {payload['remark'][:200]}")
        logger.info("osm_collected", extra={"records": len(result.records)})
        return result


class MockOsmCollector:
    data_origin = "mock"

    def __init__(self, sample_dir: Path) -> None:
        self._file = sample_dir / "osm_overpass.json"

    async def search_area(self, latitude: float, longitude: float, radius_m: float) -> CollectionResult[OsmPlaceRecord]:
        from app.services.entity_resolution.geo import haversine_m

        payload = json.loads(self._file.read_text(encoding="utf-8"))
        kept = []
        for el in payload["elements"]:
            lat = el.get("lat", (el.get("center") or {}).get("lat"))
            lon = el.get("lon", (el.get("center") or {}).get("lon"))
            if lat is None or lon is None or haversine_m(latitude, longitude, lat, lon) <= radius_m:
                kept.append(el)
        result = parse_overpass({"elements": kept}, origin="mock")
        result.requests_made = 1
        result.raw_pages.append({"elements": kept})
        return result
