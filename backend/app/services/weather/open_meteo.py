"""Open-Meteo forecast API (https://open-meteo.com/en/docs).

Free tier needs no key; with WEATHER_API_KEY the commercial host is used.
Values are passed through as returned — nothing is interpolated or invented.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.core.http import send
from app.core.retry import with_retries
from app.services.weather.base import AirQuality, WeatherPoint, WeatherSnapshot, describe_code

FREE_URL = "https://api.open-meteo.com/v1/forecast"
COMMERCIAL_URL = "https://customer-api.open-meteo.com/v1/forecast"
CURRENT = "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
HOURLY = "temperature_2m,precipitation_probability,weather_code"
AQ_FREE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AQ_COMMERCIAL_URL = "https://customer-air-quality-api.open-meteo.com/v1/air-quality"


def parse_air_quality(payload: dict) -> AirQuality | None:
    cur = payload.get("current") or {}
    if not cur:
        return None
    observed = _local_time(cur["time"], payload.get("timezone")) if cur.get("time") else None
    aq = AirQuality(observed_at=observed, us_aqi=cur.get("us_aqi"), pm2_5=cur.get("pm2_5"), pm10=cur.get("pm10"))
    return aq if any(v is not None for v in (aq.us_aqi, aq.pm2_5, aq.pm10)) else None


def _local_time(value: str, tz: str | None) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz)) if tz else dt.replace(tzinfo=UTC)
        except Exception:  # unknown tz name
            dt = dt.replace(tzinfo=UTC)
    return dt


def parse_open_meteo(payload: dict, *, source: str, is_mock: bool, latitude: float, longitude: float,
                     hours: int) -> WeatherSnapshot:
    tz = payload.get("timezone")
    current = None
    if cur := payload.get("current"):
        current = WeatherPoint(
            kind="current", valid_at=_local_time(cur["time"], tz), temperature_c=cur.get("temperature_2m"),
            weather_code=cur.get("weather_code"), condition=describe_code(cur.get("weather_code")),
            precipitation_mm=cur.get("precipitation"), humidity=cur.get("relative_humidity_2m"),
            wind_speed_kmh=cur.get("wind_speed_10m"),
        )
    forecast: list[WeatherPoint] = []
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    for i, t in enumerate(times[:hours]):
        def col(name: str, i: int = i):
            values = hourly.get(name) or []
            return values[i] if i < len(values) else None
        forecast.append(WeatherPoint(
            kind="forecast", valid_at=_local_time(t, tz), temperature_c=col("temperature_2m"),
            precipitation_probability=col("precipitation_probability"), weather_code=col("weather_code"),
            condition=describe_code(col("weather_code")),
        ))
    return WeatherSnapshot(source=source, is_mock=is_mock, retrieved_at=datetime.now(UTC), latitude=latitude,
                           longitude=longitude, current=current, forecast=forecast)


class OpenMeteoProvider:
    name = "open_meteo"

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None, max_attempts: int = 1) -> None:
        self._http = http
        # If a Google API key (starts with AIza) or non-Open-Meteo key is provided, ignore it
        # so Open-Meteo uses the free public endpoints without failing with HTTP 400.
        if api_key and (api_key.startswith("AIza") or len(api_key) > 35):
            api_key = None
        self._key = api_key
        self._attempts = max_attempts

    async def fetch(self, latitude: float, longitude: float, hours: int) -> WeatherSnapshot:
        params: dict = {"latitude": round(latitude, 4), "longitude": round(longitude, 4), "current": CURRENT,
                        "hourly": HOURLY, "forecast_hours": max(1, min(hours, 384)), "timezone": "auto"}
        url = FREE_URL
        if self._key:
            url, params["apikey"] = COMMERCIAL_URL, self._key

        async def call() -> dict:
            request = self._http.build_request("GET", url, params=params)
            try:
                return (await send(self._http, request, self.name)).json()
            except Exception:
                # If commercial URL with key fails, fallback immediately to free endpoint
                if url != FREE_URL:
                    fallback_params = dict(params)
                    fallback_params.pop("apikey", None)
                    fallback_req = self._http.build_request("GET", FREE_URL, params=fallback_params)
                    return (await send(self._http, fallback_req, self.name)).json()
                raise

        payload = await with_retries(call, attempts=self._attempts, operation="weather.open_meteo")
        return parse_open_meteo(payload, source=self.name, is_mock=False, latitude=latitude,
                                longitude=longitude, hours=hours)

    async def fetch_air_quality(self, latitude: float, longitude: float) -> AirQuality | None:
        params: dict = {"latitude": round(latitude, 4), "longitude": round(longitude, 4),
                        "current": "us_aqi,pm2_5,pm10", "timezone": "auto"}
        url = AQ_FREE_URL
        if self._key:
            url, params["apikey"] = AQ_COMMERCIAL_URL, self._key

        async def call() -> dict:
            request = self._http.build_request("GET", url, params=params)
            try:
                return (await send(self._http, request, "open_meteo_air")).json()
            except Exception:
                if url != AQ_FREE_URL:
                    fallback_params = dict(params)
                    fallback_params.pop("apikey", None)
                    fallback_req = self._http.build_request("GET", AQ_FREE_URL, params=fallback_params)
                    return (await send(self._http, fallback_req, "open_meteo_air")).json()
                raise

        return parse_air_quality(await with_retries(call, attempts=self._attempts, operation="weather.air_quality"))


class MockWeatherProvider:
    """Serves data/sample/weather_open_meteo.json, flagged ``is_mock=True``."""

    name = "mock"

    def __init__(self, sample_file: Path) -> None:
        self._file = sample_file

    async def fetch(self, latitude: float, longitude: float, hours: int) -> WeatherSnapshot:
        payload = json.loads(self._file.read_text(encoding="utf-8"))
        return parse_open_meteo(payload, source=self.name, is_mock=True, latitude=latitude, longitude=longitude,
                                hours=hours)

    async def fetch_air_quality(self, latitude: float, longitude: float) -> AirQuality | None:
        aq_file = self._file.parent / "air_quality_open_meteo.json"
        if not aq_file.exists():
            return None
        return parse_air_quality(json.loads(aq_file.read_text(encoding="utf-8")))
