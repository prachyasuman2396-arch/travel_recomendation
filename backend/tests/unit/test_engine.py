"""Recommendation engine orchestration with fakes (no DB, no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.core.errors import RecommendationUnavailableError
from app.schemas.preference import PreferenceProfile
from app.services.llm.base import LLMContextOverflowError, LLMResponse, LLMServerError
from app.services.llm.llm_service import LLMService
from app.services.llm.mock_client import MockLLMProvider
from app.services.llm.prompts.recommendation_prompt import extract_input
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.weather.open_meteo import MockWeatherProvider
from app.services.weather.service import WeatherService
from tests.unit.factories import bhubaneswar_candidates

pytestmark = pytest.mark.anyio
MONDAY = datetime(2026, 11, 16, 10, tzinfo=UTC)


class FakeSource:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def find_candidates(self, latitude, longitude, radius_m, limit):
        self.calls.append((latitude, longitude, radius_m, limit))
        return [r for r in self.rows if r.distance_m <= radius_m]


class ScriptedProvider:
    """Answers with whatever ``respond(payload)`` returns; records what it was sent."""

    def __init__(self, name, respond):
        self.name, self.model, self.respond, self.payloads = name, f"{name}-m", respond, []

    async def generate(self, request):
        payload = extract_input(request.user)
        self.payloads.append(payload)
        out = self.respond(payload)
        if isinstance(out, Exception):
            raise out
        return LLMResponse(text=json.dumps(out), provider=self.name, model=self.model)


def pick_first(payload):
    c = payload["candidates"][0]
    return {"recommendations": [{"poi_id": c["id"], "rank": 1, "reason": "Fits your request.",
                                 "matched_preferences": [], "advantages": [], "considerations": [],
                                 "weather_suitability": "suitable", "review_insights": []},
                                {"poi_id": "POI_INVENTED", "rank": 2, "reason": "Made up.",
                                 "matched_preferences": [], "advantages": [], "considerations": [],
                                 "weather_suitability": "suitable", "review_insights": []}],
            "summary": "One pick."}


def profile(**kw):
    base = dict(latitude=20.2961, longitude=85.8245, interests=["nature"], max_distance_km=20, available_hours=5,
                natural_language="I want peaceful places surrounded by nature.", client_datetime=MONDAY)
    return PreferenceProfile(**(base | kw))


def engine(providers, sample_dir, rows=None, weather=True, **cfg):
    service = LLMService(providers, max_attempts=2) if providers else None
    ws = WeatherService(MockWeatherProvider(sample_dir / "weather_open_meteo.json") if weather else None)
    source = FakeSource(bhubaneswar_candidates() if rows is None else rows)
    return RecommendationEngine(source, ws, service, EngineConfig(**cfg))


async def test_full_flow_with_mock_llm(sample_dir):
    result = await engine([MockLLMProvider(5)], sample_dir).recommend(profile())
    assert result.generation_method == "mock_llm" and result.provider == "mock"
    assert result.data_origin == "mock" and result.weather_status == "available" and result.weather.is_mock
    ids = [r.poi_id for r in result.recommendations]
    assert ids[0] == "POI000001"  # nature-loving profile: the garden first
    assert "POI000005" not in ids and result.excluded["closed_today"] == 1  # museum closed on Mondays
    assert all(r.facts.distance_km <= 20 for r in result.recommendations)
    assert [r.rank for r in result.recommendations] == list(range(1, len(ids) + 1))
    assert any("MOCK" in w for w in result.warnings)


async def test_hard_distance_constraint_applied_before_llm(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    result = await engine([provider], sample_dir).recommend(profile(max_distance_km=5))
    sent = {c["id"] for c in provider.payloads[0]["candidates"]}
    # within 5 km: garden 1.5, mall 3.5, museum 4.4 — the museum is closed on Mondays (hard constraint)
    assert sent == {"POI000001", "POI000004"}
    assert result.candidates_found == 3 and result.excluded["closed_today"] == 1


async def test_invented_ids_dropped_and_facts_come_from_db(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    result = await engine([provider], sample_dir).recommend(profile())
    assert [r.poi_id for r in result.recommendations] == ["POI000001"]
    assert result.recommendations[0].facts.rating == 4.3  # from candidate data, not LLM text
    assert any("POI_INVENTED" in w for w in result.warnings)
    assert result.generation_method == "llm" and not result.fallback_used


async def test_gemini_failure_falls_back_to_groq(sample_dir):
    gemini = ScriptedProvider("gemini", lambda p: LLMServerError("down", provider="gemini"))
    groq = ScriptedProvider("groq", pick_first)
    result = await engine([gemini, groq], sample_dir).recommend(profile())
    assert result.provider == "groq" and result.fallback_used and len(gemini.payloads) == 2


async def test_both_providers_fail_raises_without_fake_results(sample_dir):
    failing = [ScriptedProvider(n, lambda p, n=n: LLMServerError("down", provider=n)) for n in ("gemini", "groq")]
    with pytest.raises(RecommendationUnavailableError) as exc:
        await engine(failing, sample_dir).recommend(profile())
    assert exc.value.status_code == 503
    assert "gemini:server" in exc.value.details["attempts"]


async def test_opt_in_deterministic_fallback_is_labelled(sample_dir):
    failing = [ScriptedProvider("gemini", lambda p: LLMServerError("down", provider="gemini"))]
    result = await engine(failing, sample_dir, deterministic_fallback=True).recommend(profile())
    assert result.generation_method == "deterministic_fallback" and result.provider is None
    assert result.recommendations and "deterministic fallback" in result.warnings[0]


async def test_no_llm_configured_is_a_clear_error(sample_dir):
    with pytest.raises(RecommendationUnavailableError):
        await engine([], sample_dir).recommend(profile())


async def test_weather_unavailable_continues_without_weather_claims(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    result = await engine([provider], sample_dir, weather=False).recommend(profile())
    assert result.weather is None and result.weather_status == "disabled"
    assert provider.payloads[0]["weather"] is None
    assert result.recommendations[0].weather_suitability == "unknown"


async def test_context_overflow_retries_with_fewer_candidates(sample_dir):
    calls = {"n": 0}

    def respond(payload):
        calls["n"] += 1
        if len(payload["candidates"]) > 5:
            return LLMContextOverflowError("too long", provider="gemini")
        return pick_first(payload)

    rows = bhubaneswar_candidates() * 1
    for i in range(10):  # add more synthetic candidates so the first payload exceeds 5
        extra = bhubaneswar_candidates()[0]
        extra.poi_id, extra.name = f"POI1000{i:02d}", f"Extra Garden {i}"
        rows.append(extra)
    result = await engine([ScriptedProvider("gemini", respond)], sample_dir, rows=rows).recommend(profile())
    assert result.recommendations and result.candidates_sent <= 6


async def test_empty_area_returns_empty_result_without_llm_call(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    result = await engine([provider], sample_dir, rows=[]).recommend(profile())
    assert result.recommendations == [] and result.generation_method == "none" and provider.payloads == []


async def test_candidate_limit_is_configurable(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    await engine([provider], sample_dir, candidate_limit=2).recommend(profile())
    assert len(provider.payloads[0]["candidates"]) == 2


async def test_prompt_payload_contains_no_raw_reviews_or_secrets(sample_dir):
    provider = ScriptedProvider("gemini", pick_first)
    await engine([provider], sample_dir).recommend(profile())
    c = provider.payloads[0]["candidates"][0]
    assert set(c) >= {"id", "distance_km", "rating", "review_sentiment", "photo_activity", "time_fit"}
    assert "api_key" not in json.dumps(provider.payloads[0]).lower()
