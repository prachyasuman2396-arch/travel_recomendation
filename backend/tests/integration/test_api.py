"""HTTP-level tests with FastAPI's TestClient. No database: DB-backed dependencies are overridden."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import AppServices, get_engine  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.database.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.llm.base import LLMServerError  # noqa: E402
from app.services.llm.llm_service import LLMService  # noqa: E402
from app.services.llm.mock_client import MockLLMProvider  # noqa: E402
from app.services.recommendation.engine import EngineConfig, RecommendationEngine  # noqa: E402
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer  # noqa: E402
from app.services.sentiment.service import SentimentService  # noqa: E402
from app.services.weather.open_meteo import MockWeatherProvider  # noqa: E402
from app.services.weather.service import WeatherService  # noqa: E402
from tests.unit.factories import bhubaneswar_candidates  # noqa: E402

BODY = {"latitude": 20.2961, "longitude": 85.8245, "interests": ["nature", "adventure"],
        "natural_language": "I want peaceful places surrounded by nature.", "max_distance_km": 20,
        "available_hours": 5, "client_datetime": "2026-11-17T10:00:00+05:30"}


class Source:
    def find_candidates(self, lat, lon, radius_m, limit):
        return [c for c in bhubaneswar_candidates() if c.distance_m <= radius_m]


class Down:
    name, model = "gemini", "m"

    async def generate(self, request):
        raise LLMServerError("down", provider="gemini")


class NoDb:
    def execute(self, *a, **k):
        raise RuntimeError("no database in this test")

    def commit(self):
        pass


@pytest.fixture
def make_client(sample_dir):
    def _make(providers=None, admin_token=None):
        settings = Settings(admin_api_token=admin_token, log_json=False, llm_provider="mock",
                            cors_origins="http://localhost:5173")
        weather = WeatherService(MockWeatherProvider(sample_dir / "weather_open_meteo.json"))
        services = AppServices(http=httpx.AsyncClient(), weather=weather,
                               llm=LLMService(providers if providers is not None else [MockLLMProvider()]),
                               sentiment=SentimentService(LexiconSentimentAnalyzer()))
        app = create_app(settings, services)
        app.dependency_overrides[get_engine] = lambda: RecommendationEngine(
            Source(), services.weather, services.llm, EngineConfig())
        app.dependency_overrides[get_db] = lambda: NoDb()
        from app.core import config as cfg
        app.dependency_overrides[cfg.get_settings] = lambda: settings
        return TestClient(app)
    return _make


def test_recommendations_happy_path(make_client):
    with make_client() as client:
        r = client.post("/api/v1/recommendations", json=BODY)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["generation_method"] == "mock_llm" and data["data_origin"] == "mock"
    assert data["recommendations"][0]["poi_id"] == "POI000001"
    assert all(i["facts"]["distance_km"] <= 20 for i in data["recommendations"])
    assert r.headers["X-Request-ID"]


def test_validation_errors_use_error_envelope(make_client):
    with make_client() as client:
        r = client.post("/api/v1/recommendations", json=BODY | {"latitude": 120, "interests": ["skydiving"]})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and len(body["details"]["errors"]) == 2


def test_both_llms_down_returns_503_not_fake_results(make_client):
    with make_client(providers=[Down()]) as client:
        r = client.post("/api/v1/recommendations", json=BODY)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "recommendation_unavailable"
    assert "recommendations" not in r.json()


def test_providers_endpoint_never_exposes_keys(make_client):
    with make_client() as client:
        r = client.get("/api/v1/recommendations/providers")
    assert r.status_code == 200
    assert "key" not in r.text.lower()


def test_health_reports_degraded_db(make_client):
    with make_client() as client:
        r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "degraded"


def test_weather_endpoint_mock(make_client):
    with make_client() as client:
        r = client.get("/api/v1/weather", params={"latitude": 20.29, "longitude": 85.82})
    assert r.json()["status"] == "available" and r.json()["snapshot"]["is_mock"] is True


def test_admin_disabled_without_token_and_rejects_bad_token(make_client):
    with make_client(admin_token=None) as client:
        assert client.get("/api/v1/admin/dashboard").status_code == 503
    with make_client(admin_token="s3cret") as client:
        assert client.get("/api/v1/admin/dashboard").status_code == 401
        assert client.get("/api/v1/admin/dashboard",
                          headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_cors_allows_configured_origin_only(make_client):
    with make_client() as client:
        ok = client.options("/api/v1/recommendations", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        bad = client.options("/api/v1/recommendations", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


def test_generated_at_is_recent(make_client):
    with make_client() as client:
        data = client.post("/api/v1/recommendations", json=BODY).json()
    generated = datetime.fromisoformat(data["generated_at"])
    assert (datetime.now(UTC) - generated).total_seconds() < 60


def test_refine_endpoint_updates_profile_and_reruns(make_client):
    with make_client() as client:
        r = client.post("/api/v1/recommendations/refine",
                        json={"profile": BODY, "message": "sirf do ghante, avoid crowds"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["available_hours"] == 2
    assert data["profile"]["visitor"]["avoid_crowds"] is True
    assert "2 hours available" in data["changes"]
    assert data["result"]["recommendations"]


def test_google_photo_404_without_key_or_photos(make_client):
    from app.api.v1 import pois

    class Repo:
        def photo_refs(self, poi_id):
            return [{"name": "places/x/photos/y", "attributions": []}]

    with make_client() as client:
        client.app.dependency_overrides[pois._repo] = lambda: Repo()
        r = client.get("/api/v1/pois/POI000001/photo")
    assert r.status_code == 404  # no PLACES_API_KEY configured -> never guesses a URL
