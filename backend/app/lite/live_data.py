"""Build an in-memory knowledge base for one area from free, keyless sources.

  OpenStreetMap (Overpass, with mirror fail-over)  -> places, Odia/Hindi names, hours, wheelchair tags
  Wikipedia / Wikidata (concurrent, best effort)   -> background text + image
  data/curated/*.json                              -> verified sourced facts + festival calendar
  rule-based fact agent (+ optional Gemini/Groq)   -> unverified facts with verbatim evidence
  Gemini embeddings (optional)                     -> semantic matching

The same ingestion pipeline as the database version runs here into ``MemoryPoiStore``.
Every step except the OSM fetch is optional: a failure is reported in ``LiveReport.notes`` and the
area still works. Only if every Overpass server fails do we raise ``LiveDataError``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.errors import ExternalServiceError
from app.lite.gazetteer import location_conflict
from app.lite.wiki_places import categories_for, is_visitable, osm_tags_for
from app.services.agents.fact_extraction import FactExtractionAgent, LLMFactExtractor, RuleFactExtractor
from app.services.data_collection.base import CollectionResult, OsmPlaceRecord, PlaceRecord, WikiPageRecord
from app.services.data_collection.osm_client import OverpassClient
from app.services.data_collection.places_client import GooglePlacesClient
from app.services.data_collection.wikipedia_client import WikipediaClient
from app.services.entity_resolution.matcher import EntityMatcher
from app.services.ingestion.memory_store import MemoryCandidateSource, MemoryFactSink, MemoryPoiStore, MemoryRunLog
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.ingestion.raw_store import RawStore
from app.services.ingestion.store import ReviewInput
from app.services.llm.llm_service import LLMService
from app.services.semantic.search import GeminiEmbedder
from app.services.semantic.service import row_document

logger = logging.getLogger(__name__)

DEFAULT_OVERPASS_URLS = (
    "https://ovp.dresel.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
Progress = Callable[[str], None]
WIKI_GEOSEARCH_MAX_M = 10_000      # MediaWiki caps gsradius at 10 km
OVERPASS_GRACE_SECONDS = 1.0       # extra wait for Overpass once GeoSearch has answered
GEOSEARCH_FETCH = 100             # fetch wide: in cities most geotagged articles are not attractions
MAX_WIKI_PLACES = 40              # keep the nearest visitable ones


async def _fetch_google(key: str, http: httpx.AsyncClient, latitude: float, longitude: float, radius_m: float,
                        notes: list[str]) -> CollectionResult[PlaceRecord] | None:
    """One Nearby Search call (Google ranks by popularity, max 20) with reviews; never raises."""
    client = GooglePlacesClient(key, http, max_attempts=1, tile_radius_m=min(radius_m, 50_000.0), max_tiles=1,
                                include_reviews=True)
    try:
        return await client.search_area(latitude, longitude, radius_m)
    except (ExternalServiceError, httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("google_places_failed", extra={"error": str(exc), "error_type": type(exc).__name__})
        notes.append("Google Maps ratings and reviews are unavailable right now; showing places without them.")
        return None


async def _await_overpass(task: asyncio.Task, timeout: float | None) -> CollectionResult[OsmPlaceRecord] | None:
    """Wait for the concurrent Overpass fetch; any failure or timeout degrades to ``None`` (never raises)."""
    try:
        return await asyncio.wait_for(task, timeout=timeout) if timeout is not None else await task
    except TimeoutError:
        logger.info("overpass_skipped_slow", extra={"budget_s": timeout})
    except (ExternalServiceError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("overpass_failed", extra={"error": str(exc), "error_type": type(exc).__name__})
    return None


class LiveDataError(Exception):
    """Raised when no place data could be fetched for the area (all Overpass servers failed)."""


@dataclass
class LiveReport:
    places: int = 0
    google_places: int = 0
    with_hours: int = 0
    wikipedia_pages: int = 0
    verified_facts: int = 0
    reported_facts: int = 0
    events: int = 0
    embedded: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class _Prefetched:
    """Collector facade that returns already-fetched results to the pipeline."""

    data_origin = "live"

    def __init__(self, result: CollectionResult[OsmPlaceRecord] | None = None,
                 pages: dict[tuple, WikiPageRecord | None] | None = None) -> None:
        self._result = result
        self._pages = pages or {}

    async def search_area(self, latitude: float, longitude: float, radius_m: float):
        assert self._result is not None
        return self._result

    async def get_page(self, title: str | None = None, wikidata: str | None = None):
        return self._pages.get((title, wikidata))


async def _fetch_wikipedia(client: WikipediaClient, targets: list[tuple], concurrency: int,
                           notes: list[str]) -> dict[tuple, WikiPageRecord | None]:
    sem = asyncio.Semaphore(concurrency)
    failures = 0

    async def one(title: str | None, qid: str | None):
        nonlocal failures
        async with sem:
            try:
                return (title, qid), await client.get_page(title=title, wikidata=qid)
            except Exception as exc:  # one bad page must never break the area
                failures += 1
                logger.warning("wikipedia_page_failed", extra={"title": title, "qid": qid, "error": str(exc)})
                return (title, qid), None

    results = await asyncio.gather(*(one(t, q) for _, t, q in targets))
    if failures:
        notes.append(f"Wikipedia: {failures} of {len(targets)} pages could not be fetched")
    return dict(results)


def _hash_seed(name: str) -> int:
    return int(hashlib.md5(name.encode("utf-8")).hexdigest()[:8], 16)


def enrich_poi_reviews(store: MemoryPoiStore) -> None:
    now = datetime.now(UTC)
    for pid, p in store.pois.items():
        name = p["name"]
        cat = p.get("category") or "cultural"
        doc = next((x for x in store.documents.values() if x["poi_id"] == pid), None)
        seed = _hash_seed(name)

        # Realistic community rating & review count based on prominence
        if doc and len(doc.get("text", "")) > 500:
            rating = round(4.5 + (seed % 4) * 0.1, 1)
            count = 1500 + (seed % 8500)
        elif doc:
            rating = round(4.3 + (seed % 4) * 0.1, 1)
            count = 400 + (seed % 1600)
        else:
            rating = round(4.1 + (seed % 4) * 0.1, 1)
            count = 120 + (seed % 480)

        store.upsert_rating(pid, "synthetic", rating, count, now)

        # Authentic review snippets tailored to category
        if cat in ("religious", "temple"):
            revs = [
                "Deeply peaceful atmosphere with incredible spiritual significance. A must-visit place for prayer and reflection.",
                "Remarkable ancient architecture and rich traditions. Best visited during early morning for a quiet darshan.",
                "Well-maintained sacred shrine with divine, tranquil vibrations. Anand Bazar prasad was very memorable.",
            ]
        elif cat in ("historical", "monument"):
            revs = [
                "Fascinating historical heritage and majestic architecture that tells the story of our rich cultural past.",
                "Great destination for history lovers and photography. Highly recommended during sunset or early morning hours.",
                "Very photogenic and well preserved monument. Informative display and majestic craftsmanship.",
            ]
        elif cat in ("beaches", "nature", "waterfalls"):
            revs = [
                "Breathtaking natural views and refreshing sea breeze. Excellent spot for relaxation and scenic photos.",
                "Clean surroundings and tranquil atmosphere. Enjoyed a peaceful morning sunrise walk along the shore.",
                "Beautiful scenic landscape with lovely open skies. Perfect place to unwind with family.",
            ]
        elif cat in ("parks_gardens", "wildlife"):
            revs = [
                "Lush green landscape with peaceful pathways. Great spot for fresh air, morning walk, and bird watching.",
                "Well manicured gardens and calm environment away from the city bustle. Ideal for families and kids.",
                "Very pleasant greenery and clean surroundings with ample shade under tall trees.",
            ]
        else:
            revs = [
                "Wonderful local attraction with authentic cultural vibe and warm welcoming people.",
                "Vibrant destination offering a true taste of local traditions and lifestyle.",
                "Very pleasant experience exploring the area, lots of interesting sights and great photo spots.",
            ]

        authors = ["Priya N.", "Vikram P.", "Ananya S."]
        for i, text in enumerate(revs):
            r_score = min(5.0, round(rating + (0.2 if i == 0 else (-0.1 if i == 1 else 0.1)), 1))
            rev = ReviewInput(
                source="synthetic", external_id=f"{pid}_rev_{i}", rating=r_score,
                text=text, created_at=now, data_origin="mock", fetched_at=now,
                author_name=authors[i]
            )
            store.upsert_review(pid, rev, str(hash(text)), None)


def load_curated(data_dir: Path) -> tuple[list[dict], list[dict]]:
    facts: list[dict] = []
    events: list[dict] = []
    root = data_dir / "curated"
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("curated_file_unreadable", extra={"path": str(path), "error": str(exc)})
            continue
        facts.extend(data.get("facts", []))
        events.extend(data.get("events", []))
    return facts, events


async def build_live_store(latitude: float, longitude: float, radius_km: float, http: httpx.AsyncClient, *,
                           user_agent: str, data_dir: Path, overpass_urls: tuple[str, ...] = DEFAULT_OVERPASS_URLS,
                           include_food: bool = False, max_wikipedia: int = 6, wiki_concurrency: int = 6,
                           embedder: GeminiEmbedder | None = None, extract_llm: LLMService | None = None,
                           max_llm_documents: int = 4, progress: Progress | None = None,
                           synthetic_reviews: bool = False, google_key: str | None = None
                           ) -> tuple[MemoryPoiStore, LiveReport]:
    started = time.perf_counter()
    say = progress or (lambda _msg: None)
    report = LiveReport()
    store = MemoryPoiStore()
    raw_dir = Path(tempfile.gettempdir()) / "radius-raw"
    pipeline = IngestionPipeline(store, MemoryRunLog(), EntityMatcher(), RawStore(raw_dir, {}))
    radius_m = radius_km * 1000

    # 1. Wikipedia GeoSearch and Overpass run CONCURRENTLY. GeoSearch is fast and almost always up; Overpass
    #    (richer tags, hours, wheelchair) gets a short extra budget after GeoSearch returns, and is awaited in
    #    full only when GeoSearch found nothing. Previously Overpass started only after GeoSearch finished.
    say("Finding places near you…")
    # Google Maps (when a key is set): the most popular attractions with real ratings, reviews and opening
    # hours. Runs concurrently with the free sources; its places are ingested first so OSM/Wikipedia records
    # merge into them instead of duplicating them.
    google_task = (asyncio.create_task(_fetch_google(google_key, http, latitude, longitude, radius_m, report.notes))
                   if google_key else None)
    osm = OverpassClient(http, list(overpass_urls), user_agent, max_attempts=1, include_food=include_food)
    osm_task = asyncio.create_task(osm.search_area(latitude, longitude, radius_m))
    wiki_client_primary = WikipediaClient(http, user_agent, max_attempts=1)
    geo_places = await wiki_client_primary.geosearch(latitude, longitude, radius_m, limit=GEOSEARCH_FETCH)
    if radius_m > WIKI_GEOSEARCH_MAX_M:
        report.notes.append("Wikipedia GeoSearch covers at most 10 km; outer places come from OpenStreetMap only.")
    result: CollectionResult[OsmPlaceRecord] | None = None

    # Drop articles that are not places to visit (the city, constituencies, schools, stations, festivals…)
    # and articles whose coordinates contradict where they say they are ("Jagannath Temple, Bengaluru"
    # geotagged inside Puri).
    visitable = []
    for p in geo_places:
        if not is_visitable(p["title"], p.get("description"), p.get("type")):
            continue
        if conflict := location_conflict(p["title"], p.get("description"), float(p["lat"]), float(p["lon"])):
            logger.info("wikipedia_misplaced_dropped", extra={"title": p["title"], "stated": conflict})
            continue
        visitable.append(p)
    if dropped := len(geo_places) - len(visitable):
        logger.info("wikipedia_non_places_dropped", extra={"dropped": dropped, "kept": len(visitable)})

    if visitable:
        now = datetime.now(UTC)
        wiki_records = [
            OsmPlaceRecord(
                external_id=f"wiki/{p['pageid']}",
                name=p["title"],
                latitude=float(p["lat"]),
                longitude=float(p["lon"]),
                tags={**osm_tags_for(categories_for(p["title"], p.get("description"))),
                      "wikipedia": f"en:{p['title']}"},
                alt_names=(),
                wikipedia=f"en:{p['title']}",
                wikidata=None,
                data_origin="live",
                fetched_at=now,
            )
            for p in visitable[:MAX_WIKI_PLACES]
        ]
        result = CollectionResult(records=wiki_records)
        report.notes.append("Places sourced quickly via Wikipedia GeoSearch.")

        # Give Overpass a short grace period (it has been running since GeoSearch started).
        osm_result = await _await_overpass(osm_task, OVERPASS_GRACE_SECONDS)
        if osm_result and osm_result.records:
            # Merge OSM records (richer data) with Wikipedia records as seed
            seen_names = {r.name.lower() for r in osm_result.records}
            extra_wiki = [r for r in wiki_records if r.name.lower() not in seen_names]
            merged = CollectionResult(records=[*osm_result.records, *extra_wiki])
            merged.notes = osm_result.notes
            result = merged
            report.notes.append(f"Enriched with {len(osm_result.records)} OpenStreetMap places.")
    else:
        say("Fetching places from OpenStreetMap…")
        # With Google places already in hand, Overpass only gets the short grace period.
        result = await _await_overpass(osm_task, OVERPASS_GRACE_SECONDS if google_task is not None else None)

    google_result = await google_task if google_task is not None else None
    has_google = bool(google_result and google_result.records)
    if not has_google and (not result or not result.records):
        raise LiveDataError(
            "Could not find any places here right now. Try a larger radius or switch to the demo area."
        )

    if has_google:
        await pipeline.ingest_places(_Prefetched(result=google_result), latitude, longitude, radius_m)
        report.google_places = len(google_result.records)
        report.notes.append(f"{report.google_places} places with ratings, reviews and hours from Google Maps.")
    if result and result.records:
        report.notes.extend(n for n in result.notes if n.startswith(("Overpass remark", "Overpass server")))
        await pipeline.ingest_osm(_Prefetched(result=result), latitude, longitude, radius_m)
    report.places = len(store.pois)
    report.with_hours = sum(1 for p in store.pois.values() if (p.get("opening_hours") or {}).get("weekdayDescriptions"))
    if report.places == 0:
        report.notes.append("OpenStreetMap has no tagged attractions in this radius. Try a larger radius.")

    # 2. Wikipedia (best effort)
    targets = store.wiki_targets()[:max_wikipedia]
    if targets:
        say(f"Reading {len(targets)} Wikipedia articles…")
        pages = await _fetch_wikipedia(WikipediaClient(http, user_agent, max_attempts=2), targets,
                                       wiki_concurrency, report.notes)
        try:
            await pipeline.ingest_wikipedia(_Prefetched(pages=pages))
        except Exception as exc:
            report.notes.append(f"Wikipedia step skipped: {exc}")
        report.wikipedia_pages = len(store.documents)

    # 3. Curated, sourced facts + festival calendar (matched by name and distance)
    facts, events = load_curated(data_dir)
    if facts or events:
        try:
            await pipeline.load_curated(facts, events)
        except Exception as exc:
            report.notes.append(f"Curated data skipped: {exc}")

    # 4. Fact extraction: rules always; LLM optionally on a few documents
    say("Extracting facts from articles…")
    sink = MemoryFactSink(store)
    try:
        await FactExtractionAgent(sink, [RuleFactExtractor()]).run(limit=max_wikipedia)
        if extract_llm is not None:
            sink.extracted.clear()
            await FactExtractionAgent(sink, [LLMFactExtractor(extract_llm)]).run(limit=max_llm_documents)
    except Exception as exc:
        report.notes.append(f"Fact extraction partly skipped: {exc}")
    report.verified_facts = sum(1 for f in store.facts.values() if f["status"] == "verified")
    report.reported_facts = sum(1 for f in store.facts.values() if f["status"] == "unverified")
    report.events = len(store.events)

    # 5. Synthetic ratings & reviews: OFF by default. They are invented text with invented authors and must
    #    never be shown as real community reviews. Only for demos, and then labelled as mock data.
    if synthetic_reviews:
        enrich_poi_reviews(store)
        report.notes.append("Ratings and reviews are SYNTHETIC placeholders, not real visitor reviews.")

    # 6. Embeddings for semantic matching (optional)
    if embedder is not None and store.pois:
        say("Indexing places for semantic search…")
        rows = MemoryCandidateSource(store).find_candidates(latitude, longitude, 1e9, 100_000)
        try:
            vectors = await embedder.embed([row_document(r) for r in rows], "RETRIEVAL_DOCUMENT")
            store.embeddings.update({r.poi_id: v for r, v in zip(rows, vectors, strict=True)})
            report.embedded = len(vectors)
        except Exception as exc:
            report.notes.append(f"Semantic index skipped, using keyword matching instead ({type(exc).__name__})")

    report.seconds = round(time.perf_counter() - started, 1)
    return store, report
