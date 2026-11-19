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
from datetime import UTC, datetime, timedelta

import httpx

from app.core.errors import ExternalServiceError
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


@dataclass(frozen=True)
class Leg:
    minutes: float
    km: float
    traffic: str  # light | moderate | heavy | unknown
    method: str  # google_traffic | osrm | estimate
    minutes_without_traffic: float | None = None


def traffic_level(minutes: float, free_flow_minutes: float | None) -> str:
    """Delay ratio -> label. Short hops are never "heavy" just because 2 min became 3."""
    if not free_flow_minutes or free_flow_minutes <= 0:
        return "unknown"
    ratio, extra = minutes / free_flow_minutes, minutes - free_flow_minutes
    if ratio >= 1.4 and extra >= 5:
        return "heavy"
    if ratio >= 1.15 and extra >= 3:
        return "moderate"
    return "light"


class GoogleRoutesRouter:
    """Google Routes API ``computeRouteMatrix`` with live/predicted traffic (TRAFFIC_AWARE).

    POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix with ``X-Goog-Api-Key`` and a
    required ``X-Goog-FieldMask``. ``duration`` includes traffic, ``staticDuration`` doesn't; their ratio is
    the traffic level. ``departureTime`` must not be in the past, so past/now departures send none (= now).
    Proto3 JSON omits zero values: a missing ``originIndex``/``destinationIndex`` means 0.
    """

    method = "google_traffic"
    URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    FIELD_MASK = "originIndex,destinationIndex,duration,staticDuration,distanceMeters,condition"
    MAX_ELEMENTS = 100

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, timeout: float = 8.0) -> None:
        self._key = api_key
        self._http = http
        self._timeout = timeout

    @staticmethod
    def _waypoint(p: Point) -> dict:
        return {"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}}

    async def legs(self, origins: list[Point], destinations: list[Point],
                   departure: datetime | None = None) -> list[list[Leg | None]]:
        if len(origins) * len(destinations) > self.MAX_ELEMENTS:
            raise ValueError("route matrix too large")
        body: dict = {"origins": [self._waypoint(p) for p in origins],
                      "destinations": [self._waypoint(p) for p in destinations],
                      "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE"}
        if departure is not None:
            dep = departure if departure.tzinfo else departure.replace(tzinfo=UTC)
            if dep > datetime.now(UTC) + timedelta(minutes=2):
                body["departureTime"] = dep.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        request = self._http.build_request("POST", self.URL, json=body, timeout=self._timeout, headers={
            "X-Goog-Api-Key": self._key, "X-Goog-FieldMask": self.FIELD_MASK, "Content-Type": "application/json"})
        elements = (await send(self._http, request, "google_routes")).json()
        if not isinstance(elements, list):
            raise ValueError("unexpected Routes API response")
        grid: list[list[Leg | None]] = [[None] * len(destinations) for _ in origins]
        for el in elements:
            if el.get("condition") not in (None, "ROUTE_EXISTS") or "duration" not in el:
                continue
            i, j = el.get("originIndex", 0), el.get("destinationIndex", 0)
            minutes = _seconds(el["duration"]) / 60
            static = _seconds(el["staticDuration"]) / 60 if el.get("staticDuration") else None
            grid[i][j] = Leg(round(minutes, 1), round(el.get("distanceMeters", 0) / 1000, 2),
                             traffic_level(minutes, static), self.method,
                             round(static, 1) if static is not None else None)
        return grid

    async def matrix(self, points: list[Point], departure: datetime | None = None) -> TravelMatrix:
        grid = await self.legs(points, points, departure)
        if any(grid[i][j] is None for i in range(len(points)) for j in range(len(points)) if i != j):
            raise ValueError("Routes API could not route between some points")
        minutes = [[0.0 if i == j else grid[i][j].minutes for j in range(len(points))] for i in range(len(points))]
        km = [[0.0 if i == j else grid[i][j].km for j in range(len(points))] for i in range(len(points))]
        return TravelMatrix(minutes, km, self.method)


def _seconds(value: str | int | float) -> float:
    return float(str(value).removesuffix("s") or 0)


class RoutingService:
    def __init__(self, primary: OSRMRouter | None, fallback: EstimateRouter,
                 traffic: GoogleRoutesRouter | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.traffic = traffic

    async def matrix(self, points: list[Point], departure: datetime | None = None) -> TravelMatrix:
        if self.traffic is not None and len(points) > 1 and len(points) ** 2 <= GoogleRoutesRouter.MAX_ELEMENTS:
            try:
                return await self.traffic.matrix(points, departure)
            except (ExternalServiceError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("traffic_routing_fallback", extra={"error": str(exc)})
        if self.primary is not None and len(points) > 1:
            try:
                return await self.primary.matrix(points)
            except Exception as exc:  # routing is helpful context, never a reason to fail a request
                logger.warning("routing_fallback", extra={"error": str(exc)})
        return await self.fallback.matrix(points)

    async def from_origin(self, origin: Point, destinations: list[Point],
                          departure: datetime | None = None) -> list[Leg]:
        """Drive time from the traveller to each place — traffic-aware with Google, else road/estimate."""
        if not destinations:
            return []
        if self.traffic is not None and len(destinations) <= GoogleRoutesRouter.MAX_ELEMENTS:
            try:
                row = (await self.traffic.legs([origin], destinations, departure))[0]
                if all(leg is not None for leg in row):
                    return row  # type: ignore[return-value]
                fallback = await self._plain_legs(origin, destinations)
                return [leg or fb for leg, fb in zip(row, fallback, strict=True)]
            except (ExternalServiceError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("traffic_routing_fallback", extra={"error": str(exc)})
        return await self._plain_legs(origin, destinations)

    async def _plain_legs(self, origin: Point, destinations: list[Point]) -> list[Leg]:
        """OSRM road times, else the labelled estimate — never the traffic API again."""
        points = [origin, *destinations]
        m: TravelMatrix | None = None
        if self.primary is not None:
            try:
                m = await self.primary.matrix(points)
            except Exception as exc:  # routing is helpful context, never a reason to fail a request
                logger.warning("routing_fallback", extra={"error": str(exc)})
        if m is None:
            m = await self.fallback.matrix(points)
        return [Leg(round(m.minutes[0][j], 1), round(m.km[0][j], 2), "unknown", m.method)
                for j in range(1, len(destinations) + 1)]
