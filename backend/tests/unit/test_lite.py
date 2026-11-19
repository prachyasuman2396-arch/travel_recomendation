"""Lite runtime (Streamlit/Render): OSM hours, model discovery, task routing, and the full live flow
with every external service simulated — Overpass (with a failing mirror), Wikipedia/Wikidata,
Open-Meteo, Gemini (models, embeddings, generateContent) and Groq (models, chat)."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.lite import runtime
from app.lite.discovery import discover_gemini, discover_groq
from app.lite.live_data import LiveDataError
from app.lite.router import Keys, build_router
from app.lite.runtime import LiteSettings, Statuses, check_keys, geocode, load_area, recommend, refine
from app.schemas.preference import PreferenceProfile
from app.services.data_collection.osm_client import OverpassClient
from app.services.india.rules import VisitorProfile
from app.services.llm.prompts.recommendation_prompt import extract_input
from app.services.preprocessing.osm_hours import osm_to_weekday_descriptions
from app.services.recommendation.fallback import templated_response

DATA = Path(__file__).resolve().parents[3] / "data"
SAMPLE = DATA / "sample"
IST = ZoneInfo("Asia/Kolkata")
CENTER = (20.2961, 85.8245)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------ OSM opening hours
@pytest.mark.parametrize(("raw", "monday"), [
    ("24/7", "Monday: Open 24 hours"),
    ("06:00-21:00", "Monday: 6:00 AM – 9:00 PM"),
    ("Tu-Su 09:30-17:00; Mo off", "Monday: Closed"),
    ("Mo-Su 06:00-12:30,15:30-21:00", "Monday: 6:00 AM – 12:30 PM, 3:30 PM – 9:00 PM"),
    ("Fr-Mo 10:00-18:00", "Monday: 10:00 AM – 6:00 PM"),
])
def test_osm_hours_to_weekday_text(raw: str, monday: str) -> None:
    out = osm_to_weekday_descriptions(raw)
    assert out is not None and out["weekdayDescriptions"][0] == monday


@pytest.mark.parametrize("raw", ["Jan-Mar Mo 10:00-12:00", "18:00-02:00", "by appointment", "", None, "PH off"])
def test_unparseable_hours_are_unknown_not_closed(raw) -> None:
    assert osm_to_weekday_descriptions(raw) is None


def test_sunrise_sunset_is_marked_approximate() -> None:
    out = osm_to_weekday_descriptions("sunrise-sunset")
    assert out and out["approximate"] and out["weekdayDescriptions"][0] == "Monday: 6:00 AM – 6:00 PM"


# ---------------------------------------------------------------- fake internet
def _vec(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in digest[:8]]


def _osm_payload() -> dict:
    data = copy.deepcopy(json.loads((SAMPLE / "osm_overpass.json").read_text(encoding="utf-8")))
    for el in data["elements"]:
        name = (el.get("tags") or {}).get("name", "")
        if name == "Lingaraj Temple":
            el["tags"]["opening_hours"] = "Mo-Su 06:00-12:30,15:30-21:00"
        if name == "Ekamra Kanan":
            el["tags"]["opening_hours"] = "Tu-Su 07:00-18:00; Mo off"
    return data


class FakeInternet:
    """MockTransport handler. Flags let a test break individual services."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gemini_generate_status = 200
        self.groq_chat_status = 200
        self.overpass_status = {"overpass-api.de": 504}  # first mirror busy -> fail-over must work
        self.wiki_status = 200
        self.geosearch_pages: list[dict] | None = None  # None -> GeoSearch finds nothing (OSM-only path)
        self.google_places_status = 200
        self.routes_status = 200
        self.routes_bodies: list[dict] = []

    def _llm_text(self, system: str, user: str, schema: dict | None) -> str:
        blob = json.dumps(schema) if schema else system  # decide by the requested schema
        if "prefer_categories" in blob:  # intent / refine
            return json.dumps({"non_hindu_visitor": "not hindu" in user.lower(), "foreign_national": False,
                               "wheelchair_user": False, "with_children": False, "with_elderly": False,
                               "avoid_crowds": "crowd" in user.lower(), "max_entry_fee_inr": None,
                               "prefer_categories": ["historical"], "avoid_categories": [],
                               "available_hours": 2 if "2 hours" in user else None, "distance_factor": None})
        if '"facts": {"type": "array"' in blob:
            return json.dumps({"facts": []})
        return json.dumps(templated_response(extract_input(user), 6))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = request.url.host
        self.calls.append(f"{request.method} {host}{request.url.path}")
        if "overpass" in host or "kumi" in host or "private.coffee" in host or "dresel" in host:
            status = self.overpass_status.get(host, 200)
            if status != 200:
                return httpx.Response(status, text="<html>busy</html>")
            query = parse_qs(request.content.decode())["data"][0]
            if "cuisine" in query:
                return httpx.Response(200, json={"elements": [{
                    "type": "node", "id": 9001, "lat": 20.2700, "lon": 85.8400,
                    "tags": {"name": "Dalma Restaurant", "amenity": "restaurant", "cuisine": "indian;odia",
                             "opening_hours": "11:00-23:00"}}]})
            return httpx.Response(200, json=_osm_payload())
        if host == "www.wikidata.org":
            qid = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            title = {"Q_mock_lingaraj": "Lingaraja Temple"}.get(qid, f"Article {qid}")
            return httpx.Response(200, json={"entities": {qid: {"sitelinks": {"enwiki": {"title": title}},
                                                                "claims": {}}}})
        if host == "en.wikipedia.org":
            if self.wiki_status != 200:
                return httpx.Response(self.wiki_status)
            if request.url.params.get("generator") == "geosearch":
                return httpx.Response(200, json={"query": {"pages": self.geosearch_pages or []}})
            text = ("Lingaraja Temple is an 11th-century Hindu temple. Non-Hindus are not allowed inside, but a "
                    "viewing platform outside the boundary wall offers a view.")
            if request.url.path.startswith("/api/rest_v1/page/summary/"):
                title = request.url.path.rsplit("/", 1)[-1].replace("_", " ")
                return httpx.Response(200, json={"title": title, "extract": text,
                                                 "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title}"}}})
            return httpx.Response(200, json={"query": {"pages": {"1": {"extract": text}}}})
        if host == "api.open-meteo.com":
            return httpx.Response(200, json=json.loads((SAMPLE / "weather_open_meteo.json").read_text()))
        if host == "air-quality-api.open-meteo.com":
            return httpx.Response(200, json=json.loads((SAMPLE / "air_quality_open_meteo.json").read_text()))
        if host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [
                {"name": "Bhubaneswar", "admin1": "Odisha", "country": "India", "latitude": 20.27241,
                 "longitude": 85.83385}]})
        if host == "generativelanguage.googleapis.com":
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"models": [
                    {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
                ]})
            body = json.loads(request.content)
            if url.endswith(":batchEmbedContents"):
                return httpx.Response(200, json={"embeddings": [
                    {"values": _vec(r["content"]["parts"][0]["text"])} for r in body["requests"]]})
            if self.gemini_generate_status != 200:
                return httpx.Response(self.gemini_generate_status, json={"error": {"message": "quota"}})
            text = self._llm_text(body["systemInstruction"]["parts"][0]["text"],
                                  body["contents"][0]["parts"][0]["text"],
                                  body["generationConfig"].get("responseJsonSchema"))
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]},
                                                             "finishReason": "STOP"}],
                                             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10}})
        if host == "places.googleapis.com":
            if self.google_places_status != 200:
                return httpx.Response(self.google_places_status, json={"error": {"message": "denied"}})
            assert "places.reviews" in request.headers["x-goog-fieldmask"]
            return httpx.Response(200, json={"places": GOOGLE_PLACES})
        if host == "routes.googleapis.com":
            if self.routes_status != 200:
                return httpx.Response(self.routes_status, json={"error": {"message": "Routes API not enabled"}})
            body = json.loads(request.content)
            self.routes_bodies.append(body)
            out = []
            for i in range(len(body["origins"])):
                for j in range(len(body["destinations"])):
                    el = {"duration": f"{900 + 120 * j}s", "staticDuration": f"{600 + 120 * j}s",
                          "distanceMeters": 3000 + 500 * j, "condition": "ROUTE_EXISTS"}
                    el |= ({"originIndex": i} if i else {}) | ({"destinationIndex": j} if j else {})
                    out.append(el)
            return httpx.Response(200, json=out)
        if host == "api.groq.com":
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-120b", "active": True},
                                                          {"id": "openai/gpt-oss-20b", "active": True},
                                                          {"id": "whisper-large-v3", "active": True}]})
            if self.groq_chat_status != 200:
                return httpx.Response(self.groq_chat_status, json={"error": {"message": "down"}})
            body = json.loads(request.content)
            schema = (body.get("response_format") or {}).get("json_schema", {}).get("schema")
            text = self._llm_text(body["messages"][0]["content"], body["messages"][1]["content"], schema)
            return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        return httpx.Response(404, text=f"unexpected {url}")


@pytest.fixture
def net(monkeypatch):
    fake = FakeInternet()

    def client(settings):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake), headers={"User-Agent": settings.user_agent})

    monkeypatch.setattr(runtime, "_client", client)
    monkeypatch.setattr("app.core.retry.DEFAULT_INITIAL_WAIT", 0.0, raising=False)
    monkeypatch.setattr("app.core.retry.DEFAULT_MAX_WAIT", 0.0, raising=False)
    return fake


SETTINGS = LiteSettings(data_dir=DATA)
KEYS = Keys(gemini="g-key", groq="q-key")


def _profile(**kw) -> PreferenceProfile:
    base = dict(latitude=CENTER[0], longitude=CENTER[1], interests=["historical", "religious"], max_distance_km=15,
                available_hours=5, client_datetime=datetime(2026, 11, 25, 9, 0, tzinfo=IST),
                natural_language="I'm not Hindu, love old temples, avoid crowds")
    base.update(kw)
    return PreferenceProfile(**base)


# ------------------------------------------------------------------ discovery + routing
@pytest.mark.anyio
async def test_discovery_picks_usable_models(net) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(net)) as http:
        g = await discover_gemini("k", http)
        q = await discover_groq("k", http)
    assert g.ok and g.text_model == "gemini-3.6-flash" and g.embed_model == "gemini-embedding-001"  # not tts
    assert q.ok and q.text_model == "openai/gpt-oss-120b" and q.fast_model == "openai/gpt-oss-20b"
    assert q.structured_mode(q.text_model) == "plain_text"  # json_object/json_schema proved unreliable on Groq


@pytest.mark.anyio
async def test_discovery_reports_rejected_key() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as http:
        g = await discover_gemini("bad", http)
        q = await discover_groq("bad", http)
    assert not g.ok and "rejected" in g.message and not q.ok


@pytest.mark.anyio
async def test_router_assigns_tasks_by_strength(net) -> None:
    sts = await check_keys(KEYS, SETTINGS)
    async with httpx.AsyncClient() as http:
        both = build_router(KEYS, sts.gemini, sts.groq, http)
        groq_only = build_router(Keys(groq="q"), None, sts.groq, http)
        none = build_router(Keys(), None, None, http)
    assert both.plan["understand"][0].startswith("groq:openai/gpt-oss-20b")
    # Groq fast model ranks first (Gemini often 503s); Gemini is the fallback
    assert both.plan["rank"][0].startswith("groq:openai/gpt-oss-20b") and both.plan["rank"][1].startswith("gemini:")
    assert both.plan["extract"][0].startswith("gemini:")
    assert both.plan["extract"][1].startswith("groq:openai/gpt-oss-120b")
    assert both.embedder is not None
    assert groq_only.plan["rank"] == ["groq:openai/gpt-oss-20b"] and groq_only.embedder is None
    assert none.for_task("rank") is None and none.plan["embed"] == ["tfidf (offline)"]


# ------------------------------------------------------------------ live data
@pytest.mark.anyio
async def test_overpass_fails_over_to_next_mirror_and_adds_food(net) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(net)) as http:
        client = OverpassClient(http, ["https://overpass-api.de/api/interpreter",
                                       "https://overpass.private.coffee/api/interpreter"], "ua",
                                max_attempts=1, include_food=True)
        result = await client.search_area(*CENTER, 10_000)
    names = {r.name for r in result.records}
    assert "Lingaraj Temple" in names and "Dalma Restaurant" in names
    assert any("private.coffee" in n for n in result.notes)


@pytest.mark.anyio
async def test_all_overpass_down_raises_clear_error(net) -> None:
    net.overpass_status = {"overpass-api.de": 503, "ovp.dresel.systems": 504}
    with pytest.raises(LiveDataError, match="Could not find any places here"):
        await load_area(*CENTER, 10, Keys(), Statuses(), SETTINGS)


@pytest.mark.anyio
async def test_area_builds_even_when_wikipedia_fails(net) -> None:
    net.wiki_status = 500
    _, report = await load_area(*CENTER, 25, Keys(), Statuses(), SETTINGS)
    assert report.places > 5 and report.wikipedia_pages == 0
    assert any("Wikipedia" in n for n in report.notes)


@pytest.mark.anyio
async def test_geocode(net) -> None:
    places = await geocode("Bhubaneswar", SETTINGS)
    assert places[0].label == "Bhubaneswar, Odisha, India"


# ------------------------------------------------------------------ full flow
@pytest.mark.anyio
async def test_full_flow_with_both_keys(net) -> None:
    sts = await check_keys(KEYS, SETTINGS)
    store, report = await load_area(*CENTER, 25, KEYS, sts, SETTINGS)
    assert report.places > 5 and report.with_hours >= 2 and report.wikipedia_pages >= 1
    assert report.verified_facts >= 1  # real curated facts matched to OSM places
    assert report.embedded == report.places
    result, plan = await recommend(store, _profile(), KEYS, sts, SETTINGS)
    assert result.generation_method == "llm" and result.provider == "groq" and not result.fallback_used
    assert result.intent_method.startswith("llm:groq")
    assert result.semantic_method.startswith("embeddings:gemini-embedding-001")
    assert "temple entry rules for non-Hindu visitors" in result.understood
    assert result.recommendations and result.data_origin == "live"
    # verified curated no-entry rule: Lingaraj is never recommended to a non-Hindu visitor
    assert all(i.name != "Lingaraj Temple" for i in result.recommendations)
    assert result.excluded.get("entry_restricted", 0) >= 1
    assert result.itinerary is not None and result.itinerary.stops
    assert plan["understand"][0].startswith("groq") and plan["rank"][0].startswith("groq")

    # Same trip without the non-Hindu flag: Lingaraj is allowed, the rule is shown as information, hours parsed.
    open_profile = _profile(natural_language="love old temples, avoid crowds")
    result, _ = await recommend(store, open_profile, KEYS, sts, SETTINGS)
    ling = next(i for i in result.recommendations if i.name == "Lingaraj Temple")
    assert any(n.kind == "entry" and n.severity == "info" and n.verified and n.source_url for n in ling.notices)
    assert ling.facts.hours_today == "6:00 AM – 12:30 PM, 3:30 PM – 9:00 PM"

    profile, changes, again = await refine(store, _profile(), "only 2 hours please", KEYS, sts, SETTINGS)
    assert profile.available_hours == 2 and changes and again.recommendations


@pytest.mark.anyio
async def test_groq_rate_limited_falls_back_to_gemini(net) -> None:
    sts = await check_keys(KEYS, SETTINGS)
    store, _ = await load_area(*CENTER, 25, KEYS, sts, SETTINGS)
    net.groq_chat_status = 429
    result, _ = await recommend(store, _profile(), KEYS, sts, SETTINGS)
    assert result.generation_method == "llm" and result.provider == "gemini" and result.fallback_used


@pytest.mark.anyio
async def test_both_llms_down_still_returns_results(net) -> None:
    sts = await check_keys(KEYS, SETTINGS)
    store, _ = await load_area(*CENTER, 25, KEYS, sts, SETTINGS)
    net.gemini_generate_status = 503
    net.groq_chat_status = 503
    result, _ = await recommend(store, _profile(), KEYS, sts, SETTINGS)
    assert result.generation_method == "deterministic_fallback" and result.recommendations
    assert result.intent_method.startswith("rules")  # understanding fell back to the rule parser


@pytest.mark.anyio
async def test_no_keys_at_all_works(net) -> None:
    store, _ = await load_area(*CENTER, 25, Keys(), Statuses(), SETTINGS)
    result, plan = await recommend(store, _profile(visitor=VisitorProfile(non_hindu_visitor=True)), Keys(),
                                   Statuses(), SETTINGS)
    assert result.generation_method == "deterministic_fallback" and result.recommendations
    assert plan["rank"] == [] and result.semantic_method == "tfidf"


@pytest.mark.anyio
async def test_deep_facts_uses_the_extract_route(net) -> None:
    sts = await check_keys(KEYS, SETTINGS)
    _, report = await load_area(*CENTER, 25, KEYS, sts, SETTINGS, deep_facts=True)
    assert report.places > 5
    assert any("generativelanguage.googleapis.com" in c and "generateContent" in c for c in net.calls)


@pytest.mark.anyio
async def test_geosearch_non_places_are_not_recommended(net) -> None:
    def page(pid, title, description, lat, lon, kind=None):
        return {"pageid": pid, "title": title, "description": description,
                "coordinates": [{"lat": lat, "lon": lon, "type": kind}]}

    net.geosearch_pages = [
        page(1, "Odisha State Museum", "Museum in Bhubaneswar, India", 20.2577, 85.8413),
        page(2, "Utkal University", "Public university in Bhubaneswar, Odisha, India", 20.3010, 85.8410, "edu"),
        page(3, "Bhubaneswar Lok Sabha constituency", "Lok Sabha constituency in Odisha", 20.2961, 85.8245),
        page(4, "Saheed Nagar", "Neighbourhood in Bhubaneswar, Odisha", 20.2890, 85.8450),
        page(5, "Bring Your Own Film Festival", "Film festival in Bhubaneswar", 20.2900, 85.8300),
        page(6, "Jagannath Temple, Bengaluru", "Hindu temple in Karnataka, India", 20.2950, 85.8250),  # bad geotag
    ]
    store, _ = await load_area(*CENTER, 10, Keys(), Statuses(), SETTINGS)
    names = {p["name"] for p in store.pois.values()}
    assert "Odisha State Museum" in names
    assert not names & {"Utkal University", "Bhubaneswar Lok Sabha constituency", "Saheed Nagar",
                        "Bring Your Own Film Festival", "Jagannath Temple, Bengaluru"}
    museum = next(p for p in store.pois.values() if p["name"] == "Odisha State Museum")
    assert museum["category"] == "cultural"


_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _gplace(pid, name, lat, lon, types, rating, count, hours, reviews):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": lat, "longitude": lon},
            "types": types, "primaryType": types[0], "rating": rating, "userRatingCount": count,
            "regularOpeningHours": {"weekdayDescriptions": [f"{d}: {hours}" for d in _WEEK]},
            "googleMapsUri": f"https://maps.google.com/?cid={pid}", "businessStatus": "OPERATIONAL",
            "reviews": [{"name": f"places/{pid}/reviews/{i}", "rating": 5 if i == 0 else 3,
                         "text": {"text": t, "languageCode": "en"}, "publishTime": "2026-10-01T10:00:00Z",
                         "authorAttribution": {"displayName": f"Visitor {i}"}} for i, t in enumerate(reviews)]}


GOOGLE_PLACES = [
    _gplace("g-ekamra", "Ekamra Kanan Botanical Garden", 20.3034, 85.8238, ["park", "tourist_attraction"], 4.4, 21000,
            "7:00 AM – 6:00 PM", ["Beautiful garden, very peaceful and green.",
                                  "Lovely scenic views but the food court is dirty and overpriced."]),
    _gplace("g-museum", "Odisha State Museum", 20.2577, 85.8413, ["museum", "tourist_attraction"], 4.3, 9000,
            "10:00 AM – 5:00 PM", ["Great experience, the guide explained everything.",
                                   "Clean and well maintained galleries."]),
    _gplace("g-mukteswar", "Mukteswar Temple", 20.2420, 85.8410, ["hindu_temple", "tourist_attraction"], 4.7,
            15000, "6:00 AM – 8:00 PM", ["Serene and beautiful carvings, peaceful atmosphere.",
                                         "Very crowded on weekends."]),
]
GKEYS = Keys(google="maps-key")


@pytest.mark.anyio
async def test_google_key_adds_ratings_reviews_hours_and_traffic(net) -> None:
    store, report = await load_area(*CENTER, 10, GKEYS, Statuses(), SETTINGS)
    assert report.google_places == 3
    names = {p["name"] for p in store.pois.values()}
    assert "Odisha State Museum" in names and "Mukteswar Temple" in names
    tomorrow_9 = (datetime.now(IST) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    profile = _profile(interests=["historical", "religious", "parks_gardens", "cultural"], natural_language=None,
                       client_datetime=tomorrow_9, available_hours=6)
    result, _ = await recommend(store, profile, GKEYS, Statuses(), SETTINGS)
    by_name = {i.name: i for i in result.recommendations}

    museum = by_name["Odisha State Museum"]
    assert museum.facts.rating == 4.3 and museum.facts.rating_count == 9000
    assert museum.opening.status == "opens_later" and museum.opening.label == "Opens at 10:00"
    assert museum.travel.method == "google_traffic" and museum.travel.minutes >= 15
    assert museum.maps_url.startswith("https://www.google.com/maps/dir/?api=1&destination=")

    garden = next(i for i in result.recommendations if i.name.startswith("Ekamra Kanan"))
    assert garden.opening.status == "open" and garden.opening.label == "Open at 09:00 · closes 18:00"
    assert garden.review_summary is not None and garden.review_summary.method == "keywords"
    assert "the food" in garden.review_summary.dislikes
    # traffic-aware routing was asked for the planned (future) departure, for cards and the day plan
    assert any("departureTime" in b for b in net.routes_bodies)
    assert result.itinerary is not None and result.itinerary.travel_method == "google_traffic"


@pytest.mark.anyio
async def test_google_failures_degrade_quietly(net) -> None:
    net.google_places_status = 403
    net.routes_status = 403
    store, report = await load_area(*CENTER, 10, GKEYS, Statuses(), SETTINGS)
    assert report.google_places == 0 and report.places > 5  # OSM still works
    assert any("Google Maps ratings and reviews are unavailable" in n for n in report.notes)
    result, _ = await recommend(store, _profile(), GKEYS, Statuses(), SETTINGS)
    assert result.recommendations
    assert all(i.travel is None or i.travel.method == "estimate" for i in result.recommendations)
