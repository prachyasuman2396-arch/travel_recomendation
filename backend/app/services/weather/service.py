"""Weather service: caching + graceful degradation.

Returns ``(snapshot | None, status)``. A provider failure never breaks a
recommendation: the engine proceeds without weather and the UI is told why.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Literal

from app.core.cache import TTLCache
from app.services.weather.base import WeatherProvider, WeatherSnapshot

logger = logging.getLogger(__name__)
WeatherStatus = Literal["available", "unavailable", "disabled"]
MAX_FORECAST_HOURS = 384  # Open-Meteo forecast range: 16 days


class WeatherService:
    def __init__(self, provider: WeatherProvider | None, cache_ttl_seconds: int = 600,
                 air_quality: bool = True) -> None:
        self.provider = provider
        self.air_quality = air_quality
        self._cache: TTLCache[WeatherSnapshot] = TTLCache(cache_ttl_seconds, max_items=512)

    async def get(self, latitude: float, longitude: float, hours: float = 6, *,
                  start: datetime | None = None) -> tuple[WeatherSnapshot | None, WeatherStatus]:
        """Weather for ``hours`` from now — or, with ``start``, for the planned visit window.

        A trip planned for tomorrow morning is judged on tomorrow morning's forecast, not tonight's sky.
        Beyond the forecast range (16 days) there is no honest answer, so it reports "unavailable".
        """
        if self.provider is None:
            return None, "disabled"
        ahead = 0.0
        if start is not None:
            start = start if start.tzinfo else start.replace(tzinfo=UTC)
            ahead = max(0.0, (start - datetime.now(UTC)).total_seconds() / 3600)
        if ahead + hours > MAX_FORECAST_HOURS:
            logger.info("weather_out_of_forecast_range", extra={"hours_ahead": round(ahead)})
            return None, "unavailable"
        if ahead > 1:  # whole days, so requests for the same day share a cache entry
            horizon = min(MAX_FORECAST_HOURS, math.ceil((ahead + hours + 1) / 24) * 24)
        else:
            horizon = max(1, min(48, round(hours) + 1))
        # ~1 km grid key: nearby requests share a cache entry.
        key = f"{self.provider.name}:{latitude:.2f}:{longitude:.2f}:{horizon}"
        if cached := self._cache.get(key):
            return self._window(cached, start, hours), "available"

        fetch_aq = getattr(self.provider, "fetch_air_quality", None)
        try:
            if self.air_quality and fetch_aq is not None:
                weather_coro = self.provider.fetch(latitude, longitude, horizon)
                aq_coro = fetch_aq(latitude, longitude)
                w_res, aq_res = await asyncio.gather(weather_coro, aq_coro, return_exceptions=True)
                if isinstance(w_res, Exception):
                    raise w_res
                snapshot = w_res
                if not isinstance(aq_res, Exception) and aq_res is not None:
                    snapshot = snapshot.model_copy(update={"air_quality": aq_res})
            else:
                snapshot = await self.provider.fetch(latitude, longitude, horizon)
        except Exception as exc:  # any provider failure degrades to "no weather"
            logger.warning("weather_unavailable", extra={"provider": self.provider.name, "error": str(exc)})
            return None, "unavailable"

        if snapshot.current is None and not snapshot.forecast:
            return None, "unavailable"
        self._cache.set(key, snapshot)
        return self._window(snapshot, start, hours), "available"

    @staticmethod
    def _window(snapshot: WeatherSnapshot, start: datetime | None, hours: float) -> WeatherSnapshot:
        return snapshot.for_window(start, hours) if start is not None else snapshot
