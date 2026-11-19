"""Weather domain types + provider interface (provider is replaceable)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel


class WeatherPoint(BaseModel):
    kind: Literal["current", "forecast"]
    valid_at: datetime
    temperature_c: float | None = None
    condition: str | None = None
    weather_code: int | None = None
    precipitation_probability: float | None = None  # % (forecast only)
    precipitation_mm: float | None = None
    humidity: float | None = None
    wind_speed_kmh: float | None = None


class AirQuality(BaseModel):
    """Open-Meteo (CAMS) air quality. US AQI scale is reported as-is; India's NAQI is not computed."""

    observed_at: datetime | None = None
    us_aqi: float | None = None
    pm2_5: float | None = None
    pm10: float | None = None

    @property
    def category(self) -> str | None:
        if self.us_aqi is None:
            return None
        a = self.us_aqi
        return ("good" if a <= 50 else "moderate" if a <= 100 else "unhealthy_sensitive" if a <= 150
                else "unhealthy" if a <= 200 else "very_unhealthy" if a <= 300 else "hazardous")


class WeatherSnapshot(BaseModel):
    source: str
    is_mock: bool
    retrieved_at: datetime
    latitude: float
    longitude: float
    current: WeatherPoint | None
    forecast: list[WeatherPoint]
    air_quality: AirQuality | None = None

    def max_precipitation_probability(self, hours: float) -> float | None:
        values = [p.precipitation_probability for p in self.forecast[: max(1, round(hours))]
                  if p.precipitation_probability is not None]
        return max(values) if values else None


class WeatherProvider(Protocol):
    name: str

    async def fetch(self, latitude: float, longitude: float, hours: int) -> WeatherSnapshot: ...


class AirQualityProvider(Protocol):
    async def fetch_air_quality(self, latitude: float, longitude: float) -> AirQuality | None: ...


# WMO weather interpretation codes (used by Open-Meteo)
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
    82: "Violent showers", 85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


def describe_code(code: int | None) -> str | None:
    return None if code is None else WMO_CODES.get(int(code), f"WMO code {code}")


def season_for(dt: datetime, latitude: float) -> str:
    """Meteorological season by hemisphere. Region-specific seasons (e.g. monsoon) are not inferred."""
    month = dt.month
    north = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer",
             "autumn", "autumn", "autumn", "winter"][month - 1]
    if latitude >= 0:
        return north
    return {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}[north]
