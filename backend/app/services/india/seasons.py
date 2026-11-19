"""Indian seasons and climate-aware suitability.

India's travel calendar is shaped by the monsoon, not by four temperate seasons:
  summer (Mar–May) · monsoon (Jun–Sep) · post-monsoon (Oct–Nov) · winter (Dec–Feb)
Outside India's bounding box the generic hemisphere seasons are used.
"""

from __future__ import annotations

from datetime import date, datetime

from app.services.weather.base import season_for

INDIA_BBOX = (6.0, 37.5, 68.0, 97.5)  # lat_min, lat_max, lon_min, lon_max (approximate)


def in_india(latitude: float, longitude: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = INDIA_BBOX
    return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max


def indian_season(d: date | datetime) -> str:
    m = d.month
    if m in (3, 4, 5):
        return "summer"
    if m in (6, 7, 8, 9):
        return "monsoon"
    if m in (10, 11):
        return "post_monsoon"
    return "winter"


def season(d: date | datetime, latitude: float, longitude: float) -> str:
    return indian_season(d) if in_india(latitude, longitude) else season_for(
        d if isinstance(d, datetime) else datetime(d.year, d.month, d.day), latitude)


SEASON_LABELS = {"summer": "Summer (hot)", "monsoon": "Monsoon", "post_monsoon": "Post-monsoon",
                 "winter": "Winter (peak travel season)"}


def season_category_adjustment(season_name: str, categories: list[str]) -> tuple[float, str | None]:
    """Soft score adjustment (−0.3..+0.2) and an optional note for a place's categories in a season."""
    cats = set(categories)
    if season_name == "monsoon":
        if "waterfalls" in cats:
            return 0.2, "Waterfalls are at their fullest in the monsoon; paths can be slippery."
        if "beaches" in cats:
            return -0.3, "Monsoon seas are rough; swimming is usually unsafe."
        if cats & {"adventure"}:
            return -0.15, "Trails can be slippery and leech-prone in the monsoon."
        if cats & {"nature", "parks_gardens"}:
            return 0.05, "Landscapes are green in the monsoon; carry rain gear."
    if season_name == "summer" and cats & {"nature", "adventure", "beaches", "historical"}:
        return -0.1, "Hot season: visit early morning or late afternoon."
    if season_name in ("winter", "post_monsoon") and cats & {"nature", "historical", "beaches", "adventure"}:
        return 0.05, None
    return 0.0, None
