"""Real PostgreSQL + PostGIS tests.

Opt-in: set TEST_DATABASE_URL to a DISPOSABLE database, e.g.
    TEST_DATABASE_URL=postgresql+psycopg://tourist:change-me@localhost:5432/tourist_test pytest -m integration
The schema is dropped and recreated via Alembic for the session.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set (needs PostGIS)")]

if DB_URL:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from app.repositories.ingestion_store import SqlPoiStore, SqlRunLog
    from app.repositories.poi_repository import PoiRepository
    from app.repositories.sentiment_repository import SentimentRepository
    from app.services.data_collection.base import YelpReviewRecord
    from app.services.data_collection.mock_clients import (
        MockFlickrCollector,
        MockPlacesCollector,
        MockYelpCollector,
    )
    from app.services.entity_resolution.matcher import EntityMatcher
    from app.services.ingestion.pipeline import IngestionPipeline
    from app.services.ingestion.raw_store import RawStore
    from app.services.ingestion.store import NewPoi
    from app.services.sentiment.lexicon import LexiconSentimentAnalyzer
    from app.services.sentiment.service import SentimentService

CENTER = (20.2961, 85.8245)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    os.environ["DATABASE_URL"] = DB_URL
    from app.core.config import get_settings
    get_settings.cache_clear()
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "..", "migrations"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.rollback()
    s.close()


def test_migration_created_all_tables_and_gist_index(engine):
    with engine.connect() as conn:
        tables = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
        index = conn.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_pois_location_gist'")).scalar()
    assert {"users", "pois", "poi_sources", "reviews", "poi_context", "flickr_photos", "yelp_businesses",
            "ingestion_runs", "match_candidates", "poi_ratings", "review_analyses", "poi_sentiment",
            "poi_facts", "poi_documents", "poi_embeddings", "events"} <= tables
    assert "user_interactions" not in tables
    assert "gist" in index.lower()


def test_poi_id_generation_and_check_constraints(session):
    store = SqlPoiStore(session)
    pid = store.create_poi(NewPoi("Test Park", "test park", "parks_gardens", ("parks_gardens",), 20.3, 85.8,
                                  "google_places", "mock"))
    assert pid.startswith("POI") and len(pid) == 9
    with pytest.raises(IntegrityError):
        store.create_poi(NewPoi("Bad", "bad", "nature", (), 95.0, 85.8, "google_places", "mock"))


@pytest.mark.anyio
async def test_full_mock_ingestion_is_idempotent_and_spatial_queries_work(engine, tmp_path):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from tests.conftest import SAMPLE_DIR

    async def ingest():
        with factory() as s:
            p = IngestionPipeline(SqlPoiStore(s), SqlRunLog(factory), EntityMatcher(), RawStore(tmp_path, {}))
            await p.ingest_places(MockPlacesCollector(SAMPLE_DIR), *CENTER, 30_000)
            await p.ingest_yelp(MockYelpCollector(SAMPLE_DIR), *CENTER, 30_000)
            await p.ingest_flickr(MockFlickrCollector(SAMPLE_DIR), *CENTER, 30)

    await ingest()
    with factory() as s:
        counts1 = {t: s.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in
                   ("pois", "poi_sources", "reviews", "flickr_photos", "yelp_businesses")}
    await ingest()
    with factory() as s:
        counts2 = {t: s.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in counts1}
        assert counts1 == counts2, "re-ingestion must not create duplicates"
        assert counts1["pois"] > 15

        repo = PoiRepository(s)
        rows = repo.find_candidates(*CENTER, 5_000, 100)
        assert rows and all(r.distance_m <= 5_000 for r in rows)
        assert rows == sorted(rows, key=lambda r: r.distance_m)
        lingaraj = next(r for r in repo.find_candidates(*CENTER, 10_000, 100) if r.name == "Lingaraj Temple")
        # ST_Distance on geography: ~6.5 km from the city centre point (approximate mock coordinates)
        assert 6_000 < lingaraj.distance_m < 7_200
        assert {r.source for r in lingaraj.ratings} == {"google_places", "yelp"}

        # Sentiment is incremental: second run analyses nothing
        service = SentimentService(LexiconSentimentAnalyzer(), min_reviews=3)
        first = SentimentRepository(s).run(service)
        s.commit()
        second = SentimentRepository(s).run(service)
        assert first["reviews_analyzed"] > 0 and second["reviews_analyzed"] == 0


def test_upsert_review_detects_changes_and_rollback_discards(session):
    store = SqlPoiStore(session)
    pid = store.create_poi(NewPoi("Review Park", "review park", "nature", ("nature",), 20.31, 85.81,
                                  "google_places", "mock"))
    now = datetime.now(UTC)
    rec = YelpReviewRecord(external_id="rv1", business_external_id="b", rating=4, text="Nice",
                           data_origin="mock", fetched_at=now)
    assert store.upsert_review(pid, rec, "h1", None) == "inserted"
    assert store.upsert_review(pid, rec, "h1", None) == "unchanged"
    assert store.upsert_review(pid, rec.model_copy(update={"text": "Nicer"}), "h2", None) == "updated"
    session.rollback()
    assert session.execute(text("SELECT count(*) FROM reviews WHERE external_review_id='rv1'")).scalar() == 0


@pytest.mark.anyio
async def test_india_layers_osm_wikipedia_curated_and_fact_governance(engine, tmp_path):
    import json

    from app.repositories.agent_repository import AgentRepository
    from app.services.agents.fact_extraction import FactExtractionAgent, RuleFactExtractor
    from app.services.data_collection.osm_client import MockOsmCollector
    from app.services.data_collection.wikipedia_client import MockWikipediaCollector
    from tests.conftest import SAMPLE_DIR

    curated = SAMPLE_DIR.parent / "curated"
    facts = json.loads((curated / "poi_facts.json").read_text(encoding="utf-8"))["facts"]
    events = json.loads((curated / "events_odisha.json").read_text(encoding="utf-8"))["events"]
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for _ in range(2):  # idempotent
        with factory() as s:
            p = IngestionPipeline(SqlPoiStore(s), SqlRunLog(factory), EntityMatcher(), RawStore(tmp_path, {}))
            await p.ingest_places(MockPlacesCollector(SAMPLE_DIR), *CENTER, 70_000)
            await p.ingest_osm(MockOsmCollector(SAMPLE_DIR), *CENTER, 70_000)
            await p.ingest_wikipedia(MockWikipediaCollector(SAMPLE_DIR))
            await p.load_curated(facts, events)
            await FactExtractionAgent(AgentRepository(s), [RuleFactExtractor()]).run(200)
            s.commit()
    with factory() as s:
        repo = PoiRepository(s)
        ling = next(r for r in repo.find_candidates(*CENTER, 10_000, 200) if r.name == "Lingaraj Temple")
        entry = [f for f in ling.facts if f.key == "non_hindu_entry"]
        assert any(f.verified for f in entry) and ling.alt_names and ling.wiki_summary
        assert s.execute(text("SELECT count(*) FROM events")).scalar() == len(events)
        # An agent re-run must never downgrade a verified fact
        assert s.execute(text("SELECT count(*) FROM poi_facts WHERE extracted_by='curated' "
                              "AND status <> 'verified'")).scalar() == 0
