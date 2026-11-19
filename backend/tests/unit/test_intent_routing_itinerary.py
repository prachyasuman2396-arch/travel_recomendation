"""Request understanding (English/Hinglish), travel-time routing and the itinerary planner."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.schemas.preference import PreferenceProfile
from app.services.india.rules import VisitorProfile
from app.services.intent.parser import ParsedIntent, RuleIntentParser, describe_changes
from app.services.itinerary.planner import StopInput, parse_day_windows, plan_itinerary, subtract_closure
from app.services.recommendation.engine import merge_visitor
from app.services.routing.router import EstimateRouter, OSRMRouter, RoutingService, TravelMatrix

IST = ZoneInfo("Asia/Kolkata")

parser = RuleIntentParser()


# ------------------------------------------------------------------ intent
@pytest.mark.parametrize(("text", "flag"), [
    ("I'm not Hindu", "non_hindu_visitor"),
    ("we are tourists from Germany", "foreign_national"),
    ("videshi mehmaan hain", "foreign_national"),
    ("my father uses a wheelchair", "wheelchair_user"),
    ("bachche saath hain", "with_children"),
    ("with dadi and nani", "with_elderly"),
    ("bheed kam ho please", "avoid_crowds"),
])
def test_visitor_flags(text: str, flag: str) -> None:
    assert getattr(parser.parse(text), flag) is True


@pytest.mark.parametrize("text", ["visiting from Delhi", "far from us", "I am a Sikh", "temple hopping"])
def test_no_false_visitor_flags(text: str) -> None:
    intent = parser.parse(text)
    assert not intent.foreign_national and not intent.non_hindu_visitor


@pytest.mark.parametrize(("text", "hours"), [
    ("only 3 hours", 3), ("around five hours", 5), ("sirf do ghante", 2), ("half a day", 4),
    ("2.5 hrs", 2.5), ("pura din", 8),
])
def test_time_budget(text: str, hours: float) -> None:
    assert parser.parse(text).available_hours == hours


def test_hinglish_negation_avoids_category() -> None:
    intent = parser.parse("mandir nahi chahiye, kuch nature wala dikhao")
    assert "religious" in intent.avoid_categories
    assert "nature" in intent.prefer_categories and "religious" not in intent.prefer_categories


def test_fee_cap_and_free_entry() -> None:
    assert parser.parse("tickets under ₹100 please").max_entry_fee_inr == 100
    assert parser.parse("only free entry places").max_entry_fee_inr == 0


def test_describe_changes_is_human_readable() -> None:
    text = describe_changes(parser.parse("not hindu, avoid crowds, only 4 hours"))
    assert "temple entry rules for non-Hindu visitors" in text
    assert "avoiding crowds" in text and "4 hours available" in text


def test_free_text_can_only_add_restrictions() -> None:
    explicit = VisitorProfile(foreign_national=True, max_entry_fee_inr=100, avoid_categories=["shopping"])
    merged = merge_visitor(explicit, ParsedIntent(max_entry_fee_inr=500, avoid_categories=["food"]))
    assert merged.foreign_national  # text without the flag cannot switch it off
    assert merged.max_entry_fee_inr == 100  # the stricter cap wins
    assert merged.avoid_categories == ["shopping", "food"]


@pytest.mark.anyio
async def test_engine_refine_updates_profile() -> None:
    from app.services.recommendation.engine import EngineConfig, RecommendationEngine

    engine = RecommendationEngine(None, None, None, EngineConfig(), intent=RuleIntentParser())  # type: ignore[arg-type]
    profile = PreferenceProfile(latitude=20.29, longitude=85.82, interests=["religious", "food"], available_hours=6)
    new, changes = await engine.refine(profile, "mandir kam, sirf do ghante")
    assert "religious" not in new.interests and new.available_hours == 2
    assert "religious" in new.visitor.avoid_categories
    assert changes


# ----------------------------------------------------------------- routing
POINTS = [(20.2961, 85.8245), (20.2382, 85.8338)]


@pytest.mark.anyio
async def test_estimate_router_applies_detour_and_speed() -> None:
    m = await EstimateRouter(speed_kmh=22, detour_factor=1.35).matrix(POINTS)
    assert m.method == "estimate" and m.minutes[0][0] == 0
    assert 6.4 * 1.35 * 0.95 < m.km[0][1] < 6.6 * 1.35 * 1.05
    assert m.minutes[0][1] == pytest.approx(m.km[0][1] / 22 * 60)


@pytest.mark.anyio
async def test_osrm_router_parses_table_and_uses_lon_lat() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"code": "Ok", "durations": [[0, 900], [960, 0]],
                                         "distances": [[0, 8200], [8400, 0]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        m = await OSRMRouter("http://osrm:5000", http).matrix(POINTS)
    assert seen["path"].startswith("/table/v1/driving/85.824500,20.296100;")
    assert m.method == "osrm" and m.minutes[0][1] == 15 and m.km[1][0] == 8.4


@pytest.mark.anyio
async def test_routing_service_falls_back_to_estimate() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "NoRoute"}))
                                 ) as http:
        m = await RoutingService(OSRMRouter("http://osrm:5000", http), EstimateRouter()).matrix(POINTS)
    assert m.method == "estimate"


# --------------------------------------------------------------- itinerary
def _hours(line: str) -> dict:
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {"weekdayDescriptions": [f"{d}: {line}" for d in days]}


@pytest.mark.parametrize(("line", "windows"), [
    ("6:00 AM – 9:00 PM", [(360, 1260)]),
    ("6:00 AM – 12:30 PM, 3:30 – 9:00 PM", [(360, 750), (930, 1260)]),
    ("Open 24 hours", [(0, 1440)]),
    ("Closed", []),
])
def test_parse_day_windows(line: str, windows: list) -> None:
    assert parse_day_windows(_hours(line), datetime(2026, 11, 25, tzinfo=IST)) == windows


def test_unknown_hours_is_none() -> None:
    assert parse_day_windows(None, datetime(2026, 11, 25)) is None


def test_subtract_midday_closure() -> None:
    assert subtract_closure([(360, 1260)], (750, 930)) == [(360, 750), (930, 1260)]


def _matrix(n: int, minutes: float = 10) -> TravelMatrix:
    grid = [[0.0 if i == j else minutes for j in range(n + 1)] for i in range(n + 1)]
    return TravelMatrix(grid, grid, "estimate")


def _stop(i: int, windows, visit: int = 60) -> StopInput:
    return StopInput(f"p{i}", f"Place {i}", 20.0, 85.0, visit, windows, rank=i + 1)


def test_itinerary_respects_midday_closure() -> None:
    start = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
    temple = _stop(0, [(360, 750), (930, 1260)])  # closed 12:30–15:30
    it = plan_itinerary([temple], _matrix(1), start, available_minutes=6 * 60)
    stop = it.stops[0]
    assert stop.arrive.hour * 60 + stop.arrive.minute >= 930 and stop.wait_minutes > 0


def test_itinerary_skips_closed_place_and_fits_budget() -> None:
    start = datetime(2026, 11, 25, 9, 0, tzinfo=IST)
    stops = [_stop(0, []), _stop(1, [(360, 1260)]), _stop(2, None)]
    it = plan_itinerary(stops, _matrix(3), start, available_minutes=150)
    names = [s.name for s in it.stops]
    assert "Place 0" not in names and "Place 0" in it.skipped
    assert (it.end - start).total_seconds() / 60 <= 150
    assert any("unknown" in n for n in it.notes) == any(not s.hours_known for s in it.stops)
    assert any("estimate" in n for n in it.notes)


def test_itinerary_reports_when_nothing_fits() -> None:
    it = plan_itinerary([_stop(0, [(360, 1260)], visit=300)], _matrix(1), datetime(2026, 11, 25, 9, tzinfo=IST), 60)
    assert it.stops == [] and it.notes


def test_matrix_json_roundtrip_is_serialisable() -> None:
    json.dumps(_matrix(2).minutes)
