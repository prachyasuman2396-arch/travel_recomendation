from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.schemas.preference import PreferenceProfile
from app.services.feature_engineering.features import compute_features
from app.services.llm.base import LLMInvalidResponseError, LLMResponse
from app.services.recommendation.validator import RecommendationValidator
from app.services.weather.base import WeatherPoint, WeatherSnapshot
from tests.unit.factories import bhubaneswar_candidates

TUESDAY = datetime(2026, 11, 17, 10, tzinfo=UTC)
PROFILE = PreferenceProfile(latitude=20.2961, longitude=85.8245, interests=["nature"], max_distance_km=20,
                            available_hours=5)
WEATHER = WeatherSnapshot(source="mock", is_mock=True, retrieved_at=TUESDAY, latitude=20.29, longitude=85.82,
                          current=WeatherPoint(kind="current", valid_at=TUESDAY, temperature_c=27.4,
                                               weather_code=2, condition="Partly cloudy"), forecast=[])


def make_validator(weather=WEATHER, max_results=8):
    cands = bhubaneswar_candidates()
    feats = compute_features(cands, PROFILE, weather, TUESDAY, 25)
    return RecommendationValidator(cands, feats, PROFILE.max_distance_km, weather, max_results)


def rec(poi_id, rank=1, reason="A calm garden that matches your love of nature.", **kw):
    return {"poi_id": poi_id, "rank": rank, "reason": reason, "matched_preferences": ["nature"],
            "advantages": kw.get("advantages", ["Quiet green space"]), "considerations": kw.get("considerations", []),
            "weather_suitability": kw.get("weather", "suitable"), "review_insights": kw.get("insights", [])}


def response(recs, summary="Great picks.", fenced=False) -> LLMResponse:
    text = json.dumps({"recommendations": recs, "summary": summary})
    return LLMResponse(text=f"```json\n{text}\n```" if fenced else text, provider="gemini", model="m")


def test_valid_output_passes_and_ranks_are_renumbered():
    out = make_validator()(response([rec("POI000003", 5), rec("POI000001", 2)], fenced=True))
    assert [(r.poi_id, r.rank) for r in out.items] == [("POI000001", 1), ("POI000003", 2)]


def test_unknown_and_duplicate_ids_are_rejected():
    out = make_validator()(response([rec("POI999999"), rec("POI000001", 2), rec("POI000001", 3)]))
    assert [r.poi_id for r in out.items] == ["POI000001"]
    assert any("unknown poi_id" in w for w in out.warnings)
    assert any("duplicate" in w for w in out.warnings)


def test_all_invalid_raises_so_llm_layer_retries():
    with pytest.raises(LLMInvalidResponseError):
        make_validator()(response([rec("NOPE")]))


@pytest.mark.parametrize("text", ["{not json", json.dumps({"recommendations": "x"}), json.dumps({"summary": 1})])
def test_malformed_output_raises(text):
    with pytest.raises(LLMInvalidResponseError):
        make_validator()(LLMResponse(text=text, provider="groq", model="m"))


def test_wrong_distance_claim_is_removed_but_true_one_kept():
    reason = "Only 1.5 km away. It is just 9 km from you. Peaceful lawns."
    out = make_validator()(response([rec("POI000001", reason=reason)]))
    assert out.items[0].reason == "Only 1.5 km away. Peaceful lawns."
    assert any("distance" in w for w in out.warnings)


def test_fabricated_rating_and_review_count_removed():
    out = make_validator()(response([rec("POI000001", advantages=["Rated 4.9 stars", "Rated 4.3 by visitors",
                                                                  "Over 99,999 reviews", "15,322 ratings"])]))
    assert out.items[0].advantages == ["Rated 4.3 by visitors", "15,322 ratings"]


def test_travel_time_claims_are_always_removed():
    out = make_validator()(response([rec("POI000001", reason="Lovely garden. It is a 15 minute drive away.")]))
    assert out.items[0].reason == "Lovely garden."


def test_opening_hours_claim_removed_when_no_hours_supplied():
    out = make_validator()(response([rec("POI000006", reason="Scenic reservoir. Opens at 6 AM daily.")]))
    assert out.items[0].reason == "Scenic reservoir."


def test_temperature_must_match_weather():
    out = make_validator()(response([rec("POI000001", considerations=["It is 27°C now", "It will hit 38°C"])]))
    assert out.items[0].considerations == ["It is 27°C now"]


def test_without_weather_suitability_is_unknown_and_weather_claims_removed():
    v = make_validator(weather=None)
    out = v(response([rec("POI000001", weather="suitable", considerations=["Pleasant 26 °C today"])]))
    assert out.items[0].weather_suitability == "unknown"
    assert out.items[0].considerations == []


def test_reason_with_only_unsupported_claims_rejects_item():
    with pytest.raises(LLMInvalidResponseError):
        make_validator()(response([rec("POI000001", reason="It is a 10 minute drive away.")]))


def test_max_results_enforced():
    recs = [rec(f"POI00000{i}", i) for i in range(1, 7)]
    assert len(make_validator(max_results=3)(response(recs)).items) == 3
