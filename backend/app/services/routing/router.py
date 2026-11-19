"""Travel times between points.

* ``OSRMRouter`` — real road durations from an OSRM server's ``table`` service
  (self-host with an India/Odisha extract: see docs/routing.md; the public demo server is not for production)
* ``EstimateRouter`` — straight line × road-detour factor at an Indian city speed; always labelled
  "estimate" in responses, never presented as a route.
``RoutingService`` falls back to the estimate if OSRM fails, and says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.http import send
from app.core.retry import with_retries
from app.services.entity_resolution.geo import haversine_m

logger = logging.getLogger(__name__)
Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class TravelMatrix:
    minutes: list[list[float]]  # minutes[i][j] from point i to point j
    km: list[list[float]]
    method: str  # "osrm" | "estimate"


class EstimateRouter:
    method = "estimate"

    def __init__(self, speed_kmh: float = 22.0, detour_factor: float = 1.35) -> None:
        self.speed = speed_kmh
        self.detour = detour_factor

    async def matrix(self, points: list[Point]) -> TravelMatrix:
        km = [[haversine_m(a[0], a[1], b[0], b[1]) / 1000 * self.detour for b in points] for a in points]
        minutes = [[d / self.speed * 60 for d in row] for row in km]
        return TravelMatrix(minutes, km, self.method)


class OSRMRouter:
    method = "osrm"

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http

    async def matrix(self, points: list[Point]) -> TravelMatrix:
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)  # OSRM wants lon,lat

        async def call() -> dict:
            request = self._http.build_request("GET", f"{self.base_url}/table/v1/driving/{coords}",
                                               params={"annotations": "duration,distance"})
            return (await send(self._http, request, "osrm")).json()

        data = await with_retries(call, attempts=2, operation="osrm.table")
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM error: {data.get('code')}")
        durations, distances = data["durations"], data.get("distances")
        if any(v is None for row in durations for v in row):
            raise ValueError("OSRM could not route between some points")
        minutes = [[v / 60 for v in row] for row in durations]
        km = [[v / 1000 for v in row] for row in distances] if distances else [[0.0] * len(points)] * len(points)
        return TravelMatrix(minutes, km, self.method)


class RoutingService:
    def __init__(self, primary: OSRMRouter | None, fallback: EstimateRouter) -> None:
        self.primary = primary
        self.fallback = fallback

    async def matrix(self, points: list[Point]) -> TravelMatrix:
        if self.primary is not None and len(points) > 1:
            try:
                return await self.primary.matrix(points)
            except Exception as exc:  # routing is helpful context, never a reason to fail a request
                logger.warning("routing_fallback", extra={"error": str(exc)})
        return await self.fallback.matrix(points)
