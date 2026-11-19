from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import AppServices, get_services
from app.schemas.weather import WeatherResponse

router = APIRouter(prefix="/weather", tags=["weather"])

MESSAGES = {
    "unavailable": "Weather is temporarily unavailable; recommendations continue without weather context.",
    "disabled": "Weather integration is disabled (WEATHER_PROVIDER=none).",
}


@router.get("", response_model=WeatherResponse)
@router.get("/current", response_model=WeatherResponse, include_in_schema=False)
async def current_weather(latitude: float = Query(ge=-90, le=90), longitude: float = Query(ge=-180, le=180),
                          hours: float = Query(6, ge=1, le=48), services: AppServices = Depends(get_services)):
    snapshot, status = await services.weather.get(latitude, longitude, hours)
    return WeatherResponse(status=status, snapshot=snapshot, message=MESSAGES.get(status))
