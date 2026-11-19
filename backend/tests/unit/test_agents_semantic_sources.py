"""Curation agents, semantic matching, OSM/Wikipedia clients, air quality and the offline evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.eval.harness import check_expectations, hard_violations, load_scenarios, run_scenarios, summarize
from app.eval.offline import BHUBANESWAR, build_offline_store
from app.schemas.preference import PreferenceProfile
from app.services.agents.fact_extraction import ProposedFact, RuleFactExtractor, _fees, ground
from app.services.agents.match_judge import MatchCase, RuleJudge
from app.services.data_collection.osm_client import MockOsmCollector, build_query, osm_categories, parse_overpass
from app.services.data_collection.wikipedia_client import WikipediaClient, parse_wikipedia_tag
from app.services.ingestion.memory_store import MemoryCandidateSource
from app.services.intent.parser import RuleIntentParser
from app.services.llm.llm_service import LLMService
from app.services.llm.mock_client import MockLLMProvider
from app.services.recommendation.engine import EngineConfig, RecommendationEngine
from app.services.semantic.search import GeminiEmbedder, cosine_scores, expand_query, tfidf_scores
from app.services.weather.open_meteo import MockWeatherProvider, parse_air_quality
from app.services.weather.service import WeatherService

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


# ------------------------------------------------------------ fact extraction
def test_grounding_requires_verbatim_evidence() -> None:
    text = "Non-Hindus are not allowed inside the temple. A viewing platform stands outside the wall."
    good = ProposedFact("non_hindu_entry", {"allowed": False, "viewing_platform": True}, "Hindus only",
                        "Non-Hindus are not allowed inside the temple.")
    invented = ProposedFact("entry_fee", {"indian": 50, "foreign": 500, "currency": "INR"}, "₹50",
                            "Tickets cost Rs 50 for Indians and Rs 500 for foreigners.")
    bad_value = ProposedFact("entry_fee", {"indian": "fifty"}, "?", "Non-Hindus are not allowed inside the temple.")
    kept, rejected = ground(text, [good, invented, bad_value])
    assert [k.fact_key for k in kept] == ["non_hindu_entry"]
    assert len(rejected) == 2


@pytest.mark.parametrize(("sentence", "fees"), [
    ("the entry fee is rs 40 for indian visitors and rs 600 for foreign visitors", {"indian": 40, "foreign": 600}),
    ("indians pay ₹40 while foreign tourists pay ₹600 as the ticket fee", {"indian": 40, "foreign": 600}),
    ("ticket: foreigners inr 600; indian citizens inr 40", {"foreign": 600, "indian": 40}),
    ("entry fee ₹25 for domestic visitors", {"indian": 25}),
])
def test_fee_pairing(sentence: str, fees: dict) -> None:
    assert _fees(sentence) == fees


@pytest.mark.anyio
async def test_rule_extractor_finds_entry_rule_and_phones() -> None:
    text = ("The temple is one of the oldest in the city. Non-Hindus are not allowed inside, but a viewing "
            "platform outside the boundary wall offers a view. Mobile phones and cameras are not allowed.")
    facts = {f.fact_key: f for f in await RuleFactExtractor().extract("Lingaraj Temple", text)}
    assert facts["non_hindu_entry"].value == {"allowed": False, "viewing_platform": True}
    assert facts["phones_allowed"].value == {"allowed": False}
    kept, _ = ground(text, list(facts.values()))
    assert len(kept) == 2  # every rule-extracted fact is grounded by construction


# --------------------------------------------------------------- match judge
def _case(record: str, poi: str, *, alt=(), dist_deg=0.0, rcats=("religious",), pcats=("religious",)) -> MatchCase:
    return MatchCase(1, "osm", record, rcats, 20.2382 + dist_deg, 85.8338, "poi1", poi, alt, pcats, 20.2382, 85.8338,
                     0.7)


@pytest.mark.anyio
async def test_rule_judge_same_place_via_synonyms() -> None:
    j = await RuleJudge().judge(_case("Lingaraj Mandir", "Lingaraj Temple", dist_deg=0.002))
    assert j.verdict == "same"


@pytest.mark.anyio
async def test_rule_judge_different_kind_of_place() -> None:
    j = await RuleJudge().judge(_case("Ekamra Kanan Cafe", "Ekamra Kanan", rcats=("food",), pcats=("parks_gardens",)))
    assert j.verdict == "different"


@pytest.mark.anyio
async def test_rule_judge_is_unsure_when_unclear() -> None:
    j = await RuleJudge().judge(_case("Old Shiva Temple", "Bhaskareswar Temple", dist_deg=0.003))
    assert j.verdict in ("unsure", "different")


# ------------------------------------------------------------------ semantic
def test_query_expansion_covers_hinglish() -> None:
    assert "lake" in expand_query("sagar kinare shanti")


def test_tfidf_matches_synonyms() -> None:
    docs = {"lake": "Bindu Sagar Lake. A sacred tank in Old Town.", "mall": "Esplanade One Mall shopping centre."}
    scores = tfidf_scores("peaceful lake", docs)
    assert scores["lake"] > scores["mall"]


def test_cosine_scores_rank_and_clip() -> None:
    scores = cosine_scores([1.0, 0.0], {"a": [0.9, 0.1], "b": [0.0, 1.0], "c": [-1.0, 0.0]})
    assert scores["a"] > scores["b"] >= scores["c"] >= 0.0


@pytest.mark.anyio
async def test_gemini_embedder_request_shape() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        body = json.loads(request.content)
        seen["task"] = body["requests"][0]["taskType"]
        seen["dims"] = body["requests"][0]["outputDimensionality"]
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]} for _ in body["requests"]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        vecs = await GeminiEmbedder("k", http, dimensions=768).embed(["a", "b"], "RETRIEVAL_QUERY")
    assert vecs == [[0.1, 0.2], [0.1, 0.2]]
    assert seen["url"].endswith("models/gemini-embedding-001:batchEmbedContents")
    assert seen["key"] == "k" and seen["task"] == "RETRIEVAL_QUERY" and seen["dims"] == 768


# ------------------------------------------------------------ OSM / Wikipedia
def test_osm_categories_and_query() -> None:
    assert osm_categories({"historic": "temple", "amenity": "place_of_worship"})[:2] == ["religious", "historical"]
    q = build_query(20.2961, 85.8245, 10_000)
    assert q.startswith("[out:json]") and "around:10000,20.296100,85.824500" in q


def test_parse_overpass_keeps_local_names(sample_dir: Path) -> None:
    payload = json.loads((sample_dir / "osm_overpass.json").read_text(encoding="utf-8"))
    result = parse_overpass(payload, "mock")
    ling = next(r for r in result.records if r.name == "Lingaraj Temple")
    assert any("ଲିଙ୍ଗରାଜ" in n for n in ling.alt_names)
    assert all(r.data_origin == "mock" for r in result.records)


@pytest.mark.anyio
async def test_mock_osm_collector_is_labelled(sample_dir: Path) -> None:
    result = await MockOsmCollector(sample_dir).search_area(*BHUBANESWAR, 70_000)
    assert result.records and MockOsmCollector(sample_dir).data_origin == "mock"


@pytest.mark.parametrize(("tag", "parsed"), [
    ("en:Lingaraja Temple", ("en", "Lingaraja Temple")), ("Konark Sun Temple", ("en", "Konark Sun Temple")),
    (None, None),
])
def test_parse_wikipedia_tag(tag, parsed) -> None:
    assert parse_wikipedia_tag(tag) == parsed


@pytest.mark.anyio
async def test_wikipedia_client_via_wikidata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers["User-Agent"] == "test-agent"
        if "wikidata.org" in url:
            return httpx.Response(200, json={"entities": {"Q1": {
                "sitelinks": {"enwiki": {"title": "Lingaraja Temple"}},
                "claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Lingaraj.jpg"}}}]}}}})
        if "/page/summary/" in url:
            return httpx.Response(200, json={"title": "Lingaraja Temple", "extract": "A Hindu temple.",
                                             "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/L"}}})
        return httpx.Response(200, json={"query": {"pages": {"1": {"extract": "Full text about the temple."}}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        page = await WikipediaClient(http, "test-agent").get_page(wikidata="Q1")
    assert page is not None and page.title == "Lingaraja Temple"
    assert page.text == "Full text about the temple." and page.data_origin == "live"
    assert page.image_url and "Lingaraj.jpg" in page.image_url and "Wikimedia Commons" in (page.image_attribution or "")


# --------------------------------------------------------------- air quality
def test_parse_air_quality(sample_dir: Path) -> None:
    aq = parse_air_quality(json.loads((sample_dir / "air_quality_open_meteo.json").read_text(encoding="utf-8")))
    assert aq is not None and aq.us_aqi is not None and aq.category
    assert parse_air_quality({}) is None


# ------------------------------------------------------- offline knowledge base
@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def offline_store():
    return await build_offline_store(DATA_DIR)


@pytest.mark.anyio
async def test_offline_store_links_sources(offline_store) -> None:
    src = MemoryCandidateSource(offline_store)
    rows = {r.name: r for r in src.find_candidates(*BHUBANESWAR, 70_000, 500)}
    ling = rows["Lingaraj Temple"]
    assert any(f.key == "non_hindu_entry" and f.verified for f in ling.facts)  # curated, sourced
    assert ling.alt_names and ling.wiki_summary  # OSM local names + Wikipedia background
    assert rows["Konark Sun Temple"].data_origin == "mock"  # place itself is mock; fact is real + sourced
    assert len({r.name for r in rows.values()}) == len(rows)  # no duplicates after OSM linking


@pytest.mark.anyio
async def test_evaluation_has_no_hard_violations(offline_store) -> None:
    src = MemoryCandidateSource(offline_store)
    weather = WeatherService(MockWeatherProvider(DATA_DIR / "sample" / "weather_open_meteo.json"))
    engine = RecommendationEngine(src, weather, LLMService([MockLLMProvider(6)]), EngineConfig(max_results=6),
                                  intent=RuleIntentParser())
    results = await run_scenarios(engine, src, load_scenarios())
    summary = summarize(results)
    assert summary["errors"] == 0
    assert summary["hard_violations"] == 0, [r.violations for r in results if r.violations]
    assert summary["pass_rate"] >= 0.9, [(r.id, r.failed_expectations) for r in results if not r.passed]


@pytest.mark.anyio
async def test_harness_detects_a_planted_violation(offline_store) -> None:
    src = MemoryCandidateSource(offline_store)
    weather = WeatherService(MockWeatherProvider(DATA_DIR / "sample" / "weather_open_meteo.json"))
    engine = RecommendationEngine(src, weather, LLMService([MockLLMProvider(6)]), EngineConfig(max_results=6))
    puri = PreferenceProfile(latitude=19.8135, longitude=85.8312, interests=["religious"], max_distance_km=15)
    result = await engine.recommend(puri)  # visitor did NOT say non-Hindu -> Jagannath may be recommended
    assert any(i.name == "Shree Jagannath Temple Puri" for i in result.recommendations)
    pretend = puri.model_copy(update={"visitor": puri.visitor.model_copy(update={"non_hindu_visitor": True})})
    rows = {r.poi_id: r for r in src.find_candidates(puri.latitude, puri.longitude, puri.radius_m, 500)}
    assert any("no entry for non-Hindus" in v for v in hard_violations(result, pretend, rows))
    assert check_expectations(result, {"exclude": ["Shree Jagannath Temple Puri"]})
