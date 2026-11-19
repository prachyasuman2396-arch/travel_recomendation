from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from app.schemas.preference import Interest, PreferenceProfile
from app.services.feature_engineering.features import (
    adjusted_rating,
    build_feature_frame,
    compute_features,
    exposure,
    hours_for_day,
    interest_match,
    time_fit,
    weather_fit,
)
from app.services.preprocessing.categories import INTEREST_CATEGORIES, POI_CATEGORIES
from app.services.sentiment.aspects import analyze_aspects
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer, score_text
from app.services.sentiment.service import SentimentService
from app.services.sentiment.transformer import _to_score
from app.services.weather.base import WeatherPoint, WeatherSnapshot, season_for
from app.services.weather.open_meteo import MockWeatherProvider, parse_open_meteo
from app.services.weather.service import WeatherService
from tests.unit.factories import CLOSED_MONDAY, bhubaneswar_candidates

pytestmark = pytest.mark.anyio
MONDAY = datetime(2026, 11, 16, 10, tzinfo=UTC)


def snapshot(code=2, temp=27.0, precip=10.0) -> WeatherSnapshot:
    return WeatherSnapshot(source="t", is_mock=True, retrieved_at=MONDAY, latitude=0, longitude=0,
                           current=WeatherPoint(kind="current", valid_at=MONDAY, temperature_c=temp, weather_code=code),
                           forecast=[WeatherPoint(kind="forecast", valid_at=MONDAY, precipitation_probability=precip)])


def test_interest_vocabularies_are_in_sync():
    assert set(get_args(Interest)) == set(INTEREST_CATEGORIES)
    assert set(POI_CATEGORIES) < set(INTEREST_CATEGORIES)


# --------------------------------------------------------------- features
def test_adjusted_rating_shrinks_low_count_ratings():
    assert adjusted_rating(5.0, 3) < adjusted_rating(4.5, 10_000)
    assert adjusted_rating(None, 0) is None


def test_interest_match_primary_beats_secondary_and_neutral_without_interests():
    assert interest_match(["wildlife", "nature"], ["wildlife"], 0)[0] > interest_match(
        ["wildlife", "nature"], ["nature"], 0)[0]
    assert interest_match(["shopping"], [], 0) == (0.5, [])
    score, matched = interest_match(["religious"], ["photography"], 0.9)
    assert matched == ["photography"] and score > 0.5


@pytest.mark.parametrize(("cats", "weather", "expected"), [
    (["nature"], None, "unknown"),
    (["nature"], snapshot(), "suitable"),
    (["nature"], snapshot(precip=80), "unsuitable"),
    (["nature"], snapshot(code=95), "unsuitable"),
    (["nature"], snapshot(temp=36), "acceptable"),
    (["cultural"], snapshot(precip=90), "suitable"),
    (["shopping"], snapshot(code=95), "acceptable"),
])
def test_weather_fit_rules(cats, weather, expected):
    assert weather_fit(cats, weather, 4) == expected


def test_time_fit_is_coarse_and_labelled():
    assert time_fit(5, 5, 25) == "fits"
    assert time_fit(50, 5, 25) == "fits"  # 100 km / 25 km/h = 4 h + 1 h visit = 5 h
    assert time_fit(55, 5, 25) == "tight"
    assert time_fit(80, 5, 25) == "unlikely"


def test_hours_for_day_detects_closed():
    assert hours_for_day(CLOSED_MONDAY, MONDAY) == ("Closed", True)
    assert hours_for_day(CLOSED_MONDAY, MONDAY.replace(day=17))[1] is False
    assert hours_for_day(None, MONDAY) == (None, False)


def test_compute_features_and_feature_frame():
    profile = PreferenceProfile(latitude=20.29, longitude=85.82, interests=["nature", "photography"],
                                max_distance_km=20, available_hours=5)
    cands = bhubaneswar_candidates()
    feats = compute_features(cands, profile, snapshot(), MONDAY, 25)
    by_id = {f.poi_id: f for f in feats}
    assert by_id["POI000005"].closed_today is True
    assert by_id["POI000006"].rating is None and by_id["POI000006"].photo_activity != "none"
    assert by_id["POI000004"].photo_activity == "none"
    # nature-loving profile: garden outranks the mall
    assert by_id["POI000001"].pre_score > by_id["POI000004"].pre_score
    frame = build_feature_frame(cands, feats)
    assert frame.shape[0] == len(cands)
    assert {"cat_nature", "distance_km", "adjusted_rating_missing"} <= set(frame.columns)
    assert frame["distance_km"].between(0, 1).all()
    assert frame.loc["POI000006", "adjusted_rating_missing"] == 1


# ---------------------------------------------------------------- weather
def test_parse_open_meteo_and_mock_provider(sample_dir):
    import json
    payload = json.loads((sample_dir / "weather_open_meteo.json").read_text())
    snap = parse_open_meteo(payload, source="mock", is_mock=True, latitude=1, longitude=2, hours=6)
    assert snap.current.condition == "Partly cloudy" and snap.current.temperature_c == 27.4
    assert len(snap.forecast) == 6 and snap.forecast[0].kind == "forecast"
    assert snap.max_precipitation_probability(6) == 20
    assert snap.current.valid_at.tzinfo is not None


async def test_weather_service_degrades_gracefully_and_caches(sample_dir):
    class Failing:
        name = "failing"

        async def fetch(self, *a):
            raise RuntimeError("down")

    assert await WeatherService(Failing()).get(20, 85, 4) == (None, "unavailable")
    assert await WeatherService(None).get(20, 85, 4) == (None, "disabled")

    provider = MockWeatherProvider(sample_dir / "weather_open_meteo.json")
    calls = {"n": 0}
    original = provider.fetch

    async def counting(*a):
        calls["n"] += 1
        return await original(*a)

    provider.fetch = counting
    service = WeatherService(provider, cache_ttl_seconds=60)
    snap, status = await service.get(20.2961, 85.8245, 4)
    await service.get(20.2962, 85.8246, 4)  # same ~1 km cell -> cache hit
    assert status == "available" and snap.is_mock and calls["n"] == 1


def test_season_by_hemisphere():
    assert season_for(datetime(2026, 7, 1), 20) == "summer"
    assert season_for(datetime(2026, 7, 1), -33) == "winter"


# -------------------------------------------------------------- sentiment
def test_lexicon_negation_scope_and_intensifiers():
    assert score_text("very clean") > score_text("clean") > 0
    assert score_text("not clean") < 0
    assert score_text("Not very clean. Peaceful in the morning.") > score_text("Not very clean.")


def test_aspects_only_reported_when_mentioned():
    aspects = analyze_aspects("Beautiful garden, but the food facilities are poor.", LexiconSentimentAnalyzer())
    assert aspects == {"environment": "positive", "facilities": "negative", "food": "negative"}
    assert analyze_aspects("We went there yesterday.", LexiconSentimentAnalyzer()) == {}


def test_sentiment_aggregation_requires_minimum_reviews():
    service = SentimentService(LexiconSentimentAnalyzer(), min_reviews=3)
    results = service.analyze([(1, "Beautiful and peaceful", "h1"), (2, "Dirty and crowded", "h2")])
    summary = service.aggregate([(r.sentiment, r.score, r.aspects) for r in results])
    assert summary.status == "insufficient_data" and summary.overall is None

    results = service.analyze([(1, "Beautiful and peaceful", "h1"), (2, "Lovely clean garden", "h2"),
                               (3, "Crowded but scenic", "h3"), (4, "", "h4")])
    assert len(results) == 3  # empty text skipped, not scored
    summary = service.aggregate([(r.sentiment, r.score, r.aspects) for r in results])
    assert summary.status == "available" and summary.overall == "positive"
    assert summary.aspects["environment"]["label"] == "positive"
    assert summary.aspects["crowding"]["mentions"] >= 1


@pytest.mark.parametrize(("label", "conf", "expected"), [("5 stars", 0.9, 1.0), ("1 star", 0.8, -1.0),
                                                          ("3 stars", 0.7, 0.0), ("positive", 0.8, 0.8)])
def test_transformer_label_mapping(label, conf, expected):
    assert _to_score(label, conf) == pytest.approx(expected)


# --------------------------------------------------------------- weather for the planned visit
def _hourly(start, values):
    """values: list of (temp, code, precip) per hour from ``start``."""
    from datetime import timedelta
    return [WeatherPoint(kind="forecast", valid_at=start + timedelta(hours=i), temperature_c=t, weather_code=c,
                         precipitation_probability=p) for i, (t, c, p) in enumerate(values)]


@pytest.mark.parametrize(("cats", "weather", "expected"), [
    (["historical", "cultural"], snapshot(precip=90), "acceptable"),   # open-air monument in heavy rain odds
    (["historical"], snapshot(code=95), "unsuitable"),                 # storm at a fort
    (["religious", "historical"], snapshot(temp=43), "unsuitable"),    # barefoot courtyards at 43 °C
    (["religious"], snapshot(temp=38), "acceptable"),
    (["historical"], snapshot(code=55), "acceptable"),                  # dense drizzle counts as rain
    (["beaches", "nature"], snapshot(code=55), "acceptable"),
    (["beaches", "nature"], snapshot(code=65), "unsuitable"),           # heavy rain
    (["cultural", "historical"], snapshot(code=65, precip=90), "suitable"),  # museum: indoors
])
def test_open_air_sites_and_drizzle_count(cats, weather, expected):
    assert weather_fit(cats, weather, 4) == expected


def test_storm_later_in_the_window_counts():
    snap = snapshot()
    snap = snap.model_copy(update={"forecast": _hourly(MONDAY, [(27, 2, 10), (27, 2, 10), (26, 95, 60)])})
    assert weather_fit(["nature"], snap, 3) == "unsuitable"
    assert weather_fit(["nature"], snap, 2) == "suitable"


def test_for_window_uses_the_planned_hours_not_now():
    from datetime import timedelta
    tomorrow_9 = MONDAY + timedelta(hours=23)
    hours = [(24, 61, 80)] * 23 + [(29, 1, 5)] * 4 + [(30, 95, 90)] * 5  # rain tonight, clear 09-13, storm later
    snap = snapshot(code=61, temp=24, precip=80).model_copy(update={"forecast": _hourly(MONDAY, hours)})
    window = snap.for_window(tomorrow_9, 4)
    assert [p.valid_at for p in window.forecast] == [tomorrow_9 + timedelta(hours=i) for i in range(4)]
    assert window.current.valid_at == tomorrow_9 and window.current.kind == "forecast"
    assert window.max_precipitation_probability(4) == 5
    assert weather_fit(["beaches", "nature"], snap, 4) == "unsuitable"     # tonight
    assert weather_fit(["beaches", "nature"], window, 4) == "suitable"     # tomorrow morning


def test_for_window_outside_forecast_keeps_snapshot():
    from datetime import timedelta
    snap = snapshot()
    assert snap.for_window(MONDAY + timedelta(days=30), 4) is snap


async def test_weather_service_beyond_forecast_range_is_unavailable(sample_dir):
    from datetime import timedelta
    service = WeatherService(MockWeatherProvider(sample_dir / "weather_open_meteo.json"))
    far = datetime.now(UTC) + timedelta(days=20)
    assert await service.get(20.29, 85.82, 4, start=far) == (None, "unavailable")


def test_bad_weather_reorders_the_list():
    from app.schemas.preference import PreferenceProfile
    from tests.unit.factories import bhubaneswar_candidates
    profile = PreferenceProfile(latitude=20.2961, longitude=85.8245, interests=[], max_distance_km=20,
                                available_hours=4)
    cands = bhubaneswar_candidates()
    clear = {f.poi_id: f for f in compute_features(cands, profile, snapshot(), MONDAY, 25)}
    storm = {f.poi_id: f for f in compute_features(cands, profile, snapshot(code=95, precip=90), MONDAY, 25)}
    for c in cands:
        drop = clear[c.poi_id].pre_score - storm[c.poi_id].pre_score
        if exposure(c.categories or [c.category]) == "outdoor":
            assert drop >= 0.3, c.name           # weight + penalty: real reordering, not a 0.09 nudge
        elif exposure(c.categories or [c.category]) == "indoor":
            assert drop < 0.1, c.name
