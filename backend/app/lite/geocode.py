"""Place-name search via the free Open-Meteo geocoding API (GeoNames data, no key).

Docs: https://open-meteo.com/en/docs/geocoding-api — free for non-commercial use.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.http import send
from app.core.retry import with_retries

URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class Place:
    name: str
    admin: str | None
    country: str | None
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return ", ".join(x for x in (self.name, self.admin, self.country) if x)


def parse_results(payload: dict) -> list[Place]:
    out = []
    for r in payload.get("results") or []:
        try:
            out.append(Place(str(r["name"]), r.get("admin1"), r.get("country"), float(r["latitude"]),
                             float(r["longitude"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def search_places(query: str, http: httpx.AsyncClient, *, country_code: str | None = "IN",
                        count: int = 6) -> list[Place]:
    query = query.strip()
    if len(query) < 2:
        return []
    params = {"name": query, "count": count, "language": "en", "format": "json"}
    if country_code:
        params["countryCode"] = country_code

    async def call() -> dict:
        request = http.build_request("GET", URL, params=params)
        return (await send(http, request, "geocoding")).json()

    return parse_results(await with_retries(call, attempts=2, operation="geocode"))
