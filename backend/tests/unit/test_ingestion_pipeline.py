"""End-to-end ingestion on the MOCK dataset with an in-memory store."""

from __future__ import annotations

import pytest

from app.services.data_collection.mock_clients import (
    MockFlickrCollector,
    MockPlacesCollector,
    MockYelpCollector,
)
from app.services.entity_resolution.matcher import EntityMatcher
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.ingestion.raw_store import RawStore
from tests.fakes import MemoryPoiStore, MemoryRunLog

pytestmark = pytest.mark.anyio
CENTER = (20.2961, 85.8245)


@pytest.fixture
def pipeline(tmp_path):
    return IngestionPipeline(MemoryPoiStore(), MemoryRunLog(), EntityMatcher(), RawStore(tmp_path, {}))


def poi_by_name(store: MemoryPoiStore, name: str) -> str:
    return next(pid for pid, p in store.pois.items() if p["name"] == name)


async def test_places_ingestion_creates_internal_ids_and_rejects_bad_records(pipeline, sample_dir):
    run_id, stats = await pipeline.ingest_places(MockPlacesCollector(sample_dir), *CENTER, 30_000)
    store = pipeline.store
    assert all(pid.startswith("POI") for pid in store.pois)
    assert all(p["data_origin"] == "mock" for p in store.pois.values())
    # Puri Beach / Konark are ~55-65 km away: outside the 30 km collection radius
    names = {p["name"] for p in store.pois.values()}
    assert "Lingaraj Temple" in names and "Puri Beach" not in names
    assert stats.rejection_reasons == {"missing name": 1, "invalid coordinates": 1, "permanently closed": 1}
    assert pipeline.run_log.runs[run_id]["status"] == "partial"
    assert pipeline.run_log.runs[run_id]["mode"] == "mock"


async def test_reingestion_is_idempotent(pipeline, sample_dir):
    await pipeline.ingest_places(MockPlacesCollector(sample_dir), *CENTER, 30_000)
    n_pois, n_sources = len(pipeline.store.pois), len(pipeline.store.sources)
    _, stats = await pipeline.ingest_places(MockPlacesCollector(sample_dir), *CENTER, 30_000)
    assert len(pipeline.store.pois) == n_pois
    assert len(pipeline.store.sources) == n_sources
    assert stats.inserted == 0


async def test_yelp_matching_links_variants_keeps_cafe_separate_and_flags_ambiguous(pipeline, sample_dir):
    await pipeline.ingest_places(MockPlacesCollector(sample_dir), *CENTER, 30_000)
    before = len(pipeline.store.pois)
    _, stats = await pipeline.ingest_yelp(MockYelpCollector(sample_dir), *CENTER, 30_000)
    store = pipeline.store

    zoo = poi_by_name(store, "Nandankanan Zoological Park")
    assert store.find_source("yelp", "mock_y_nandankanan") == zoo
    assert store.find_source("yelp", "mock_y_lingaraja") == poi_by_name(store, "Lingaraj Temple")

    cafe_poi = store.find_source("yelp", "mock_y_ekamra_cafe")
    assert cafe_poi is not None and cafe_poi != poi_by_name(store, "Ekamra Kanan Botanical Gardens")
    assert store.pois[cafe_poi]["category"] == "food"

    # Indian synonyms: "Peace Pagoda" (Yelp) == "Shanti Stupa" (Google)
    assert store.find_source("yelp", "mock_y_dhauli_pagoda") == poi_by_name(store, "Dhauli Shanti Stupa")
    # Ambiguous (same name, pin ~380 m off): flagged, not linked, its reviews not stored
    assert store.find_source("yelp", "mock_y_rajarani_far") is None
    assert any(k[1] == "mock_y_rajarani_far" for k in store.candidates)
    assert stats.flagged == 1
    assert not any(r["poi_id"] is None for r in store.reviews.values())

    # New POIs only for unmatched businesses with a supported category (cafe, restaurant)
    assert len(store.pois) - before == stats.inserted == 2
    assert stats.rejection_reasons.get("missing name or coordinates") == 1
    # Mock reviews have no retention expiry; ratings stored per source
    assert all(r["expires_at"] is None for r in store.reviews.values())
    assert (zoo, "yelp") in store.ratings and (zoo, "google_places") in store.ratings


async def test_flickr_requires_textual_evidence(pipeline, sample_dir):
    await pipeline.ingest_places(MockPlacesCollector(sample_dir), *CENTER, 30_000)
    _, stats = await pipeline.ingest_flickr(MockFlickrCollector(sample_dir), *CENTER, 30)
    store = pipeline.store
    assert stats.inserted > 0
    assert not any(pid.startswith("mock_f_untagged") for pid in store.photos)
    assert stats.rejection_reasons["no confident POI association"] >= 3
    assert all(p["method"] == "geo+text" for p in store.photos.values())
    zoo = poi_by_name(store, "Nandankanan Zoological Park")
    assert sum(1 for p in store.photos.values() if p["poi_id"] == zoo) >= 5


async def test_failure_rolls_back_and_records_failed_run(pipeline):
    class Exploding:
        data_origin = "live"

        async def search_area(self, *a, **k):
            raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        await pipeline.ingest_places(Exploding(), *CENTER, 1000)
    run = next(iter(pipeline.run_log.runs.values()))
    assert run["status"] == "failed" and "provider down" in run["error"]
    assert pipeline.store.rollbacks == 1


async def test_raw_store_only_writes_live_permitted_payloads(tmp_path):
    store = RawStore(tmp_path, {"flickr": True, "places": False})
    assert store.save("flickr", "r1", [{"a": 1}], data_origin="mock") == []
    assert store.save("places", "r1", [{"a": 1}], data_origin="live") == []
    written = store.save("flickr", "r1", [{"a": 1}], data_origin="live")
    assert len(written) == 1 and written[0].read_text() == '{"a": 1}'
