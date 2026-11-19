from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.weather.base import WeatherSnapshot


class WeatherResponse(BaseModel):
    status: Literal["available", "unavailable", "disabled"]
    snapshot: WeatherSnapshot | None
    message: str | None = None
