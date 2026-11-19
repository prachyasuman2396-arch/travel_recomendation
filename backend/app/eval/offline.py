"""Build the complete knowledge base in memory from the MOCK sample data + curated files.

Runs exactly the production pipeline (Google Places -> OSM -> Wikipedia -> curated facts/events -> Yelp)
and the fact-extraction agent, into MemoryPoiStore. Used by the evaluation harness and tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.agents.fact_extraction import FactExtractionAgent, RuleFactExtractor
from app.services.data_collection.mock_clients import MockPlacesCollector, MockYelpCollector
from app.services.data_collection.osm_client import MockOsmCollector
from app.services.data_collection.wikipedia_client import MockWikipediaCollector
from app.services.entity_resolution.matcher import EntityMatcher
from app.services.ingestion.memory_store import MemoryFactSink, MemoryPoiStore, MemoryRunLog
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.ingestion.raw_store import RawStore

BHUBANESWAR = (20.2961, 85.8245)


async def build_offline_store(data_dir: Path, radius_km: float = 70) -> MemoryPoiStore:
    sample, curated = data_dir / "sample", data_dir / "curated"
    store = MemoryPoiStore()
    pipeline = IngestionPipeline(store, MemoryRunLog(), EntityMatcher(), RawStore(data_dir / "raw", {}))
    radius_m = radius_km * 1000
    await pipeline.ingest_places(MockPlacesCollector(sample), *BHUBANESWAR, radius_m)
    await pipeline.ingest_osm(MockOsmCollector(sample), *BHUBANESWAR, radius_m)
    await pipeline.ingest_wikipedia(MockWikipediaCollector(sample))
    facts = json.loads((curated / "poi_facts.json").read_text(encoding="utf-8"))["facts"]
    events = json.loads((curated / "events_odisha.json").read_text(encoding="utf-8"))["events"]
    await pipeline.load_curated(facts, events)
    await pipeline.ingest_yelp(MockYelpCollector(sample), *BHUBANESWAR, int(radius_m))
    await FactExtractionAgent(MemoryFactSink(store), [RuleFactExtractor()]).run()
    return store
