"""Deterministic feature engineering.

These features do two jobs:
  1. pre-rank the geographic candidates so the best ~40 go to the LLM
     (and power the clearly-labelled deterministic fallback)
  2. give the LLM compact, pre-computed facts instead of raw records

No LLM is used for arithmetic, encoding or distance. Flickr photo counts are
treated as a *photo-activity* signal only — never as visitor numbers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder

from app.schemas.preference import PreferenceProfile
from app.services.entity_resolution.geo import haversine_m
from app.services.india.facts import facts_by_key
from app.services.india.rules import EventView, Notice, VisitorProfile, apply_rules
from app.services.india.seasons import season_category_adjustment
from app.services.preprocessing.categories import POI_CATEGORIES
from app.services.recommendation.types import CandidateRow
from app.services.weather.base import WeatherSnapshot

OUTDOOR = {"nature", "adventure", "wildlife", "beaches", "waterfalls", "parks_gardens"}
INDOOR = {"cultural", "shopping", "entertainment", "food"}
RATING_PRIOR_MEAN = 3.8
RATING_PRIOR_WEIGHT = 50  # pseudo-ratings: 5.0 from 3 reviews should not beat 4.5 from 10k
WEIGHTS = {"interest": 0.35, "rating": 0.20, "distance": 0.15, "sentiment": 0.10, "popularity": 0.10,
           "weather": 0.10}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class CandidateFeatures:
    poi_id: str
    distance_km: float
    rating: float | None
    rating_count: int
    adjusted_rating: float | None
    interest_match: float
    matched_interests: list[str]
    popularity: float
    photo_activity: str
    photo_activity_score: float
    sentiment_score: float | None
    weather_fit: str  # suitable | acceptable | unsuitable | unknown
    time_fit: str  # fits | tight | unlikely  (coarse straight-line estimate, labelled as such)
    hours_today: str | None
    closed_today: bool
    pre_score: float
    # India context
    excluded_reason: str | None = None
    notices: list[Notice] | None = None
    season_note: str | None = None
    semantic: float | None = None
    visit_minutes: int | None = None


def combined_rating(c: CandidateRow) -> tuple[float | None, int]:
    """Review-count-weighted mean across sources."""
    total = sum(r.count for r in c.ratings)
    if not c.ratings:
        return None, 0
    if total == 0:
        return round(sum(r.rating for r in c.ratings) / len(c.ratings), 2), 0
    return round(sum(r.rating * r.count for r in c.ratings) / total, 2), total


def adjusted_rating(rating: float | None, count: int) -> float | None:
    if rating is None:
        return None
    return (RATING_PRIOR_WEIGHT * RATING_PRIOR_MEAN + rating * count) / (RATING_PRIOR_WEIGHT + count)


def interest_match(categories: list[str], interests: list[str], photo_score: float) -> tuple[float, list[str]]:
    if not interests:
        return 0.5, []  # no stated interests -> neutral, not zero
    matched, best = [], 0.0
    for interest in interests:
        if interest == "photography":
            if photo_score > 0.3:
                matched.append(interest)
                best = max(best, 0.5 + 0.5 * photo_score)
        elif categories and interest == categories[0]:
            matched.append(interest)
            best = max(best, 1.0)
        elif interest in categories:
            matched.append(interest)
            best = max(best, 0.7)
    breadth = min(1.0, len(matched) / max(1, min(len(interests), 3)))
    return round(0.8 * best + 0.2 * breadth, 3), matched


def weather_fit(categories: list[str], weather: WeatherSnapshot | None, hours: float) -> str:
    if weather is None or weather.current is None:
        return "unknown"
    fit = _weather_fit(categories, weather, hours)
    aqi = weather.air_quality.us_aqi if weather.air_quality else None
    outdoor = bool(set(categories) & OUTDOOR) and not (set(categories[:1]) & INDOOR)
    if outdoor and aqi is not None:  # long outdoor exposure in poor air (common in Indian winters)
        if aqi > 200:
            return "unsuitable"
        if aqi > 150 and fit == "suitable":
            return "acceptable"
    return fit


def _weather_fit(categories: list[str], weather: WeatherSnapshot, hours: float) -> str:
    precip = weather.max_precipitation_probability(hours) or 0.0
    code = weather.current.weather_code or 0
    temp = weather.current.temperature_c
    storm = code >= 95
    outdoor = bool(set(categories) & OUTDOOR) and not (set(categories[:1]) & INDOOR)
    if not outdoor:
        return "acceptable" if storm else "suitable"
    if storm or precip >= 70 or (temp is not None and temp >= 40):
        return "unsuitable"
    if precip >= 40 or (temp is not None and temp >= 35) or code in (61, 63, 65, 80, 81, 82):
        return "acceptable"
    return "suitable"


def time_fit(distance_km: float, available_hours: float, speed_kmh: float, min_visit_hours: float = 1.0) -> str:
    """Coarse feasibility from straight-line round trip — NOT a route or travel-time estimate."""
    travel_h = (2 * distance_km) / speed_kmh
    if travel_h + min_visit_hours <= available_hours:
        return "fits"
    if travel_h < available_hours:
        return "tight"
    return "unlikely"


_CLOSED = re.compile(r"\bclosed\b", re.IGNORECASE)


def hours_for_day(opening_hours: dict | None, when: datetime) -> tuple[str | None, bool]:
    """Read Google-style ``weekdayDescriptions``. Returns (text for that day, closed?)."""
    if not opening_hours:
        return None, False
    descriptions = opening_hours.get("weekdayDescriptions") or []
    day = WEEKDAYS[when.weekday()]
    for line in descriptions:
        if line.startswith(day + ":"):
            text = line.split(":", 1)[1].strip()
            return text, bool(_CLOSED.fullmatch(text.strip()))
    return None, False


def photo_bucket(score: float, count: int) -> str:
    if count == 0:
        return "none"
    return "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"


def events_for(row: CandidateRow, events: list[EventView], when: datetime, hours: float) -> list[EventView]:
    """Events that overlap the visit window and are at/near this place."""
    end = when.timestamp() + hours * 3600
    out = []
    for ev in events:
        if ev.end.timestamp() < when.timestamp() - 3600 or ev.start.timestamp() > end + 2 * 86400:
            continue  # include events up to 2 days ahead: plans and crowds build early
        near = haversine_m(row.latitude, row.longitude, ev.latitude, ev.longitude) <= ev.radius_km * 1000
        if near or row.name in ev.poi_names:
            out.append(ev)
    return out


def compute_features(candidates: list[CandidateRow], profile: PreferenceProfile, weather: WeatherSnapshot | None,
                     when: datetime, speed_kmh: float, *, visitor: VisitorProfile | None = None,
                     events: list[EventView] | None = None, semantic: dict[str, float] | None = None,
                     season: str | None = None, extra_interests: list[str] | None = None,
                     detour_factor: float = 1.0) -> list[CandidateFeatures]:
    if not candidates:
        return []
    max_reviews = max((combined_rating(c)[1] for c in candidates), default=0)
    max_photos = max((c.photo_count for c in candidates), default=0)
    out = []
    for c in candidates:
        rating, count = combined_rating(c)
        adj = adjusted_rating(rating, count)
        popularity = math.log1p(count) / math.log1p(max_reviews) if max_reviews else 0.0
        photo_score = math.log1p(c.photo_count) / math.log1p(max_photos) if max_photos else 0.0
        interests = list(dict.fromkeys([*profile.interests, *(extra_interests or [])]))
        match, matched = interest_match(c.categories or [c.category], interests, photo_score)
        sentiment = None
        if c.sentiment and c.sentiment.get("status") == "available":
            sentiment = (c.sentiment.get("positive_share") or 0) - (c.sentiment.get("negative_share") or 0)
        distance_km = c.distance_m / 1000.0
        wfit = weather_fit(c.categories or [c.category], weather, profile.available_hours)
        hours_text, closed = hours_for_day(c.opening_hours, when)
        if c.business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            closed = True
        cats = c.categories or [c.category]
        rule = apply_rules(
            visitor or VisitorProfile(),
            c.category,
            cats,
            c.facts,
            events_for(c, events or [], when, profile.available_hours),
            visit_window=(when, when + timedelta(hours=profile.available_hours)),
            poi_name=c.name,
            description=c.wiki_summary or c.description or "",
            lat=c.latitude,
            lon=c.longitude,
        )
        season_adj, season_note = season_category_adjustment(season, cats) if season else (0.0, None)
        sem = (semantic or {}).get(c.poi_id)
        visit = facts_by_key(c.facts).get("typical_visit_minutes")
        parts = {
            "interest": match,
            "rating": (adj - 1) / 4 if adj is not None else 0.5,
            "distance": max(0.0, 1 - distance_km / profile.max_distance_km),
            "sentiment": (sentiment + 1) / 2 if sentiment is not None else 0.5,
            "popularity": popularity,
            "weather": {"suitable": 1.0, "acceptable": 0.6, "unsuitable": 0.1, "unknown": 0.5}[wfit],
        }
        score = sum(WEIGHTS[k] * v for k, v in parts.items())
        if sem is not None:
            score = 0.85 * score + 0.15 * sem  # semantic match to the free-text request
        score += 0.5 * season_adj - rule.penalty
        out.append(CandidateFeatures(
            poi_id=c.poi_id, distance_km=round(distance_km, 2), rating=rating, rating_count=count,
            adjusted_rating=None if adj is None else round(adj, 3), interest_match=match,
            matched_interests=matched, popularity=round(popularity, 3),
            photo_activity=photo_bucket(photo_score, c.photo_count), photo_activity_score=round(photo_score, 3),
            sentiment_score=None if sentiment is None else round(sentiment, 3), weather_fit=wfit,
            time_fit=time_fit(distance_km * detour_factor, profile.available_hours, speed_kmh),
            hours_today=hours_text, closed_today=closed, pre_score=round(score, 4),
            excluded_reason=rule.reason if rule.excluded else None, notices=rule.notices, season_note=season_note,
            semantic=sem, visit_minutes=int(visit.value["minutes"]) if visit else None,
        ))
    return out


def build_feature_frame(candidates: list[CandidateRow], features: list[CandidateFeatures]) -> pd.DataFrame:
    """Model-ready matrix: one-hot categories + min-max scaled numeric features (for EDA/experiments)."""
    if not candidates:
        return pd.DataFrame()
    by_id = {f.poi_id: f for f in features}
    base = pd.DataFrame([{
        "poi_id": c.poi_id, "category": c.category, "distance_km": by_id[c.poi_id].distance_km,
        "adjusted_rating": by_id[c.poi_id].adjusted_rating, "log_rating_count": np.log1p(by_id[c.poi_id].rating_count),
        "photo_activity": by_id[c.poi_id].photo_activity_score, "sentiment": by_id[c.poi_id].sentiment_score,
    } for c in candidates]).set_index("poi_id")
    encoder = OneHotEncoder(categories=[list(POI_CATEGORIES)], handle_unknown="ignore", sparse_output=False)
    onehot = pd.DataFrame(encoder.fit_transform(base[["category"]]),
                          columns=[f"cat_{c}" for c in POI_CATEGORIES], index=base.index)
    numeric_cols = ["distance_km", "adjusted_rating", "log_rating_count", "photo_activity", "sentiment"]
    numeric = base[numeric_cols].astype(float)
    # Missing rating/sentiment stay explicit via indicator columns, then impute the neutral midpoint.
    indicators = numeric[["adjusted_rating", "sentiment"]].isna().astype(int).add_suffix("_missing")
    scaled = pd.DataFrame(MinMaxScaler().fit_transform(numeric.fillna(numeric.median()).fillna(0.0)),
                          columns=numeric_cols, index=base.index)
    return pd.concat([onehot, scaled, indicators], axis=1)
