"""Traffic-aware travel (Google Routes), travel-based ranking and review summaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.feature_engineering.features import CandidateFeatures, apply_travel
from app.services.llm.base import LLMRequest, LLMResponse
from app.services.llm.llm_service import LLMService
from app.services.recommendation.review_summary import PlaceReviews, ReviewSummarizer, keyword_summary
from app.services.routing.router import EstimateRouter, GoogleRoutesRouter, RoutingService, traffic_level

pytestmark = pytest.mark.anyio
ORIGIN = (19.7982, 85.8249)
DESTS = [(19.8048, 85.8180), (19.8876, 86.0945)]


# ----------------------------------------------------------------------------- Google Routes
def routes_handler(seen: dict, elements: list[dict] | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "API not enabled"}})
        return httpx.Response(200, json=elements if elements is not None else [
            # proto3 JSON omits zero values: originIndex/destinationIndex 0 are absent
            {"duration": "1080s", "staticDuration": "600s", "distanceMeters": 2100, "condition": "ROUTE_EXISTS"},
            {"destinationIndex": 1, "duration": "2400s", "staticDuration": "2280s", "distanceMeters": 35100,
             "condition": "ROUTE_EXISTS"},
        ])
    return handler


async def test_routes_matrix_parses_traffic_and_sends_future_departure() -> None:
    seen: dict = {}
    departure = datetime.now(UTC) + timedelta(hours=10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes_handler(seen))) as http:
        row = (await GoogleRoutesRouter("rk", http).legs([ORIGIN], DESTS, departure))[0]
    assert seen["url"].endswith("/distanceMatrix/v2:computeRouteMatrix")
    assert seen["headers"]["x-goog-api-key"] == "rk" and "staticDuration" in seen["headers"]["x-goog-fieldmask"]
    assert seen["body"]["routingPreference"] == "TRAFFIC_AWARE" and seen["body"]["departureTime"].endswith("Z")
    assert row[0].minutes == 18 and row[0].traffic == "heavy" and row[0].km == 2.1  # 18 min vs 10 free-flow
    assert row[1].minutes == 40 and row[1].traffic == "light" and row[1].method == "google_traffic"


async def test_past_departure_is_not_sent() -> None:
    seen: dict = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes_handler(seen))) as http:
        await GoogleRoutesRouter("rk", http).legs([ORIGIN], DESTS, datetime.now(UTC) - timedelta(hours=3))
    assert "departureTime" not in seen["body"]  # the API rejects past times; omitted = now


async def test_routing_falls_back_to_estimate_when_routes_api_fails() -> None:
    seen: dict = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes_handler(seen, status=403))) as http:
        service = RoutingService(None, EstimateRouter(), traffic=GoogleRoutesRouter("rk", http))
        legs = await service.from_origin(ORIGIN, DESTS)
    assert [leg.method for leg in legs] == ["estimate", "estimate"] and legs[0].traffic == "unknown"


async def test_unroutable_destination_falls_back_per_place() -> None:
    seen: dict = {}
    elements = [{"duration": "600s", "staticDuration": "560s", "distanceMeters": 2000, "condition": "ROUTE_EXISTS"},
                {"destinationIndex": 1, "condition": "ROUTE_NOT_FOUND"}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(routes_handler(seen, elements))) as http:
        service = RoutingService(None, EstimateRouter(), traffic=GoogleRoutesRouter("rk", http))
        legs = await service.from_origin(ORIGIN, DESTS)
    assert legs[0].method == "google_traffic" and legs[1].method == "estimate"


@pytest.mark.parametrize(("minutes", "free", "level"), [
    (10, 10, "light"), (14, 10, "moderate"), (18, 10, "heavy"), (3, 2, "light"),  # +1 min is not "heavy"
    (20, None, "unknown"),
])
def test_traffic_levels(minutes, free, level) -> None:
    assert traffic_level(minutes, free) == level


# ----------------------------------------------------------------------------- ranking with travel
def _feat(score: float = 1.0) -> CandidateFeatures:
    return CandidateFeatures(poi_id="P", distance_km=5, rating=None, rating_count=0, adjusted_rating=None,
                             interest_match=1, matched_interests=[], popularity=0, photo_activity="none",
                             photo_activity_score=0, sentiment_score=None, weather_fit="suitable", time_fit="fits",
                             hours_today=None, closed_today=False, pre_score=score)


def test_long_drive_in_heavy_traffic_drops_the_place() -> None:
    near, far = _feat(), _feat()
    apply_travel(near, 12, "light", "google_traffic", 4)
    apply_travel(far, 95, "heavy", "google_traffic", 4)
    assert near.pre_score > far.pre_score and far.pre_score < 0.8
    assert near.time_fit == "fits" and far.time_fit == "tight"
    gone = _feat()
    apply_travel(gone, 130, "heavy", "google_traffic", 4)
    assert gone.time_fit == "unlikely"


def test_estimates_are_shown_but_do_not_rerank() -> None:
    f = _feat()
    apply_travel(f, 95, "unknown", "estimate", 4)
    assert f.pre_score == 1.0 and f.travel_minutes == 95 and f.time_fit == "fits"


# ----------------------------------------------------------------------------- review summaries
PLACE = PlaceReviews("P1", "Puri Beach", ["Lovely sunset and calm sea", "Food stalls are dirty and overpriced"],
                     ["environment", "crowding"], ["food", "cleanliness"])


def test_keyword_summary_reads_like_a_person() -> None:
    s = keyword_summary(PLACE)
    assert set(s.likes) == {"the views and atmosphere", "the calm, uncrowded feel"}
    assert set(s.dislikes) == {"the food", "cleanliness"} and s.method == "keywords"
    assert s.text.startswith("People like ") and " — but some mention " in s.text and s.text.endswith(".")


def test_no_reviews_no_summary() -> None:
    assert keyword_summary(PlaceReviews("P2", "X", [], ["environment"], [])) is None


class FakeLLM:
    name, model = "groq", "m"

    def __init__(self, text: str) -> None:
        self.text, self.calls = text, 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        assert "Lovely sunset" in request.user  # the real reviews are what the model reads
        return LLMResponse(text=self.text, provider=self.name, model=self.model)


async def test_ai_summary_is_cleaned_and_grounded_ids_only() -> None:
    llm = FakeLLM(json.dumps({"places": [
        {"id": "P1", "likes": ["Peaceful vibe ", "beautiful sunset views", "costs 50 rupees"],
         "dislikes": ["food is not good", "dirty food stalls", "x", "long queues", "too many touts"]},
        {"id": "NOT-A-PLACE", "likes": ["invented"], "dislikes": []}]}))
    out = await ReviewSummarizer(LLMService([llm])).summarize([PLACE])
    s = out["P1"]
    assert s.method == "ai" and s.likes == ["peaceful vibe", "beautiful sunset views"]  # number dropped
    assert s.dislikes == ["food is not good", "dirty food stalls", "long queues"]       # max 3, junk dropped
    assert s.text.startswith("People like peaceful vibe and beautiful sunset views — but some mention")
    assert "NOT-A-PLACE" not in out


async def test_bad_ai_output_falls_back_to_keywords() -> None:
    out = await ReviewSummarizer(LLMService([FakeLLM("sorry, I can't help")], max_attempts=1)).summarize([PLACE])
    assert out["P1"].method == "keywords"
