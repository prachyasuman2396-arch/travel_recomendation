"""Compact, token-budgeted LLM input built from candidates + features."""

from __future__ import annotations

import json

from app.schemas.preference import PreferenceProfile
from app.services.feature_engineering.features import CandidateFeatures
from app.services.recommendation.types import CandidateRow
from app.services.weather.base import WeatherSnapshot


def _clip(text: str | None, n: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def fact_ids(c: CandidateRow) -> dict[str, object]:
    """Short per-place ids the LLM cites ('F1', 'F2', ...) -> FactView. Rejected facts are never shown."""
    return {f"F{i}": fact for i, fact in enumerate((x for x in c.facts if x.status != "rejected"), start=1)}


def candidate_payload(c: CandidateRow, f: CandidateFeatures) -> dict:
    sentiment: dict | str = "unavailable"
    if c.sentiment and c.sentiment.get("status") == "available":
        aspects = c.sentiment.get("aspects") or {}
        sentiment = {
            "overall": c.sentiment.get("overall"),
            "reviews_analyzed": c.sentiment.get("reviews_analyzed"),
            "positive_aspects": sorted(a for a, v in aspects.items() if v.get("label") == "positive"),
            "negative_aspects": sorted(a for a, v in aspects.items() if v.get("label") == "negative"),
        }
    elif c.sentiment:
        sentiment = "insufficient review data"
    return {
        "id": c.poi_id,
        "name": c.name,
        "category": c.category,
        "categories": c.categories,
        "description": _clip(c.description, 220),
        "distance_km": f.distance_km,
        "rating": f.rating,
        "rating_count": f.rating_count,
        "rating_sources": sorted({r.source for r in c.ratings}),
        "review_sentiment": sentiment,
        "review_snippets": [_clip(s, 180) for s in c.review_snippets[:2]],
        "opening_hours_today": f.hours_today or "unknown",
        "photo_activity": f.photo_activity,
        "matched_interests": f.matched_interests,
        "weather_fit_hint": f.weather_fit,
        "time_fit": f.time_fit,
        "local_names": c.alt_names[:3],
        "background": _clip(c.wiki_summary, 260),
        # Sourced facts the explanation may rely on; cite their ids in cited_fact_ids.
        "facts": [{"id": fid, "text": fact.display_text, "verified": fact.verified,
                   "source": fact.source_name or fact.source_type} for fid, fact in fact_ids(c).items()],
        # Computed by the system from verified rules/events. Never contradict; mention critical ones.
        "notices": [{"text": n.text, "severity": n.severity} for n in (f.notices or [])],
        "season_note": f.season_note,
    }


def weather_payload(weather: WeatherSnapshot | None, hours: float) -> dict | None:
    if weather is None or weather.current is None:
        return None
    cur = weather.current
    return {
        "observed_at": cur.valid_at.isoformat(),
        "condition": cur.condition,
        "temperature_c": cur.temperature_c,
        "humidity_pct": cur.humidity,
        "wind_kmh": cur.wind_speed_kmh,
        "max_rain_probability_pct_next_hours": weather.max_precipitation_probability(hours),
        "is_mock": weather.is_mock,
        "air_quality": None if weather.air_quality is None else {
            "us_aqi": weather.air_quality.us_aqi, "category": weather.air_quality.category,
            "pm2_5": weather.air_quality.pm2_5},
    }


def preferences_payload(p: PreferenceProfile) -> dict:
    return {
        "interests": p.interests,
        "request_text": p.natural_language,
        "max_distance_km": p.max_distance_km,
        "available_hours": p.available_hours,
        "budget": p.budget,
        "travel_type": p.travel_type,
        "constraints": p.constraints,
    }


def build_user_payload(profile: PreferenceProfile, candidates: list[CandidateRow], features: list[CandidateFeatures],
                       weather: WeatherSnapshot | None, max_chars: int, *, context: dict | None = None
                       ) -> tuple[dict, int]:
    """Return (payload, number_of_candidates_included). Candidates must already be sorted best-first;
    the lowest-ranked are dropped until the serialized payload fits ``max_chars``."""
    by_id = {f.poi_id: f for f in features}
    items = [candidate_payload(c, by_id[c.poi_id]) for c in candidates]
    base = {"preferences": preferences_payload(profile), "weather": weather_payload(weather, profile.available_hours),
            **(context or {})}
    while True:
        payload = base | {"candidates": items}
        if len(json.dumps(payload, ensure_ascii=False)) <= max_chars or len(items) <= 1:
            return payload, len(items)
        items = items[: max(1, int(len(items) * 0.8))]
