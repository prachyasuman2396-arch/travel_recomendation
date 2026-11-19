"""Protected admin endpoints (Authorization: Bearer <ADMIN_API_TOKEN>)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppServices, get_services, require_admin
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.database.session import get_db, get_session_factory
from app.models import Poi, PoiFact
from app.repositories.admin_repository import AdminRepository
from app.repositories.agent_repository import AgentRepository
from app.repositories.ingestion_store import SqlPoiStore, SqlRunLog
from app.repositories.sentiment_repository import SentimentRepository
from app.schemas.admin import IngestionRequest, IngestionRunOut, MatchCandidateOut
from app.schemas.common import Page
from app.schemas.poi import PoiFactOut
from app.services.agents.fact_extraction import (
    FactExtractionAgent,
    FactExtractor,
    LLMFactExtractor,
    RuleFactExtractor,
)
from app.services.agents.maintenance import EmbeddingIndexer, FreshnessAgent
from app.services.agents.match_judge import Judge, LLMJudge, MatchJudgeAgent, RuleJudge
from app.services.data_collection.factory import build_collector
from app.services.data_collection.places_client import GooglePlacesClient
from app.services.entity_resolution.matcher import EntityMatcher, MatchConfig
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.ingestion.raw_store import RawStore

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def build_pipeline(db: Session, settings: Settings) -> IngestionPipeline:
    matcher = EntityMatcher(MatchConfig(high_threshold=settings.match_high_threshold,
                                        medium_threshold=settings.match_medium_threshold,
                                        max_distance_m=settings.match_max_distance_m))
    raw = RawStore(settings.data_dir / "raw", {"places": settings.raw_store_places, "osm": settings.raw_store_osm,
                                               "flickr": settings.raw_store_flickr, "yelp": settings.raw_store_yelp})
    return IngestionPipeline(SqlPoiStore(db), SqlRunLog(get_session_factory()), matcher, raw,
                             yelp_retention_hours=settings.yelp_content_retention_hours,
                             google_retention_days=settings.google_content_retention_days)


def load_curated_files(settings: Settings) -> tuple[list[dict], list[dict]]:
    """Read every curated JSON file (``*facts*.json`` and ``events*.json``) from ``data/curated``."""
    root = settings.data_dir / "curated"
    facts: list[dict] = []
    events: list[dict] = []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        facts.extend(data.get("facts", []))
        events.extend(data.get("events", []))
    return facts, events


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
              services: AppServices = Depends(get_services)):
    data = AdminRepository(db).dashboard()
    data["integrations"] = settings.provider_status()  # booleans only, never keys
    data["llm"] = {"primary": settings.llm_provider, "fallback": settings.llm_fallback_provider,
                   "configured_chain": services.llm.provider_names if services.llm else []}
    return data


@router.get("/ingestion", response_model=Page[IngestionRunOut])
@router.get("/ingestion/runs", response_model=Page[IngestionRunOut], include_in_schema=False)
def ingestion_runs(source: str | None = None, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    items, total = AdminRepository(db).runs(limit=limit, offset=offset, source=source)
    return Page(items=[IngestionRunOut.model_validate(r) for r in items], total=total, limit=limit, offset=offset)


@router.post("/ingestion/{source}", response_model=IngestionRunOut)
async def run_ingestion(source: Literal["places", "osm", "wikipedia", "flickr", "yelp"], body: IngestionRequest,
                        db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                        services: AppServices = Depends(get_services)):
    """Run one collector synchronously and return its ingestion-run record.

    ``mode=auto`` uses live APIs when keys exist, otherwise MOCK data (recorded as mode=mock).
    """
    collector = build_collector(source, settings, services.http, body.mode)
    pipeline = build_pipeline(db, settings)
    if source == "places":
        run_id, _ = await pipeline.ingest_places(collector, body.latitude, body.longitude, body.radius_km * 1000)
    elif source == "osm":
        run_id, _ = await pipeline.ingest_osm(collector, body.latitude, body.longitude, body.radius_km * 1000)
    elif source == "wikipedia":
        run_id, _ = await pipeline.ingest_wikipedia(collector)
    elif source == "flickr":
        run_id, _ = await pipeline.ingest_flickr(collector, body.latitude, body.longitude, body.radius_km)
    else:
        run_id, _ = await pipeline.ingest_yelp(collector, body.latitude, body.longitude, int(body.radius_km * 1000),
                                               fetch_reviews=body.fetch_reviews)
        SentimentRepository(db).run(services.sentiment)
    db.expire_all()  # the run row was finalised in its own transaction
    return IngestionRunOut.model_validate(AdminRepository(db).get_run(run_id))


@router.post("/sentiment/run")
def run_sentiment(db: Session = Depends(get_db), services: AppServices = Depends(get_services)):
    return SentimentRepository(db).run(services.sentiment)


@router.get("/match-candidates", response_model=list[MatchCandidateOut])
def match_candidates(status: Literal["pending", "accepted", "rejected"] = "pending",
                     limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return [MatchCandidateOut.model_validate(m) for m in AdminRepository(db).match_candidates(status=status,
                                                                                              limit=limit)]


@router.post("/retention/purge")
def purge(db: Session = Depends(get_db)):
    return AdminRepository(db).purge_expired()


@router.post("/curated/load", response_model=IngestionRunOut)
async def load_curated(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Load hand-curated, sourced facts (status=verified) and the festival calendar from ``data/curated``."""
    facts, events = load_curated_files(settings)
    run_id, _ = await build_pipeline(db, settings).load_curated(facts, events)
    db.expire_all()
    return IngestionRunOut.model_validate(AdminRepository(db).get_run(run_id))


# ------------------------------------------------------------------ agents
@router.post("/agents/{agent}")
async def run_agent(agent: Literal["fact_extraction", "match_judge", "embeddings", "freshness"],
                    limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings), services: AppServices = Depends(get_services)):
    """Run one offline curation agent. Agents never touch the request path; they write
    *unverified* facts / proposals that an admin (or a high-confidence rule) promotes."""
    repo = AgentRepository(db)
    if agent == "fact_extraction":
        extractors: list[FactExtractor] = [RuleFactExtractor()]
        if services.llm:
            extractors.append(LLMFactExtractor(services.llm))
        report = await FactExtractionAgent(repo, extractors).run(limit)
    elif agent == "match_judge":
        judges: list[Judge] = [RuleJudge()]
        if services.llm:
            judges.append(LLMJudge(services.llm))
        report = await MatchJudgeAgent(repo, judges).run(limit)
    elif agent == "embeddings":
        report = await EmbeddingIndexer(repo, services.embedder).run()
    else:
        checker = (GooglePlacesClient(settings.places_api_key.get_secret_value(), services.http)
                   if settings.places_api_key else None)
        report = await FreshnessAgent(repo, checker).run(limit=limit)
    db.commit()
    return report.as_dict()


# ------------------------------------------------------------------- facts
class FactDecision(BaseModel):
    status: Literal["verified", "rejected", "unverified"]


@router.get("/facts", response_model=Page[PoiFactOut])
def list_facts(status: Literal["verified", "unverified", "rejected"] = "unverified",
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    """Facts awaiting review. Verify only after opening ``source_url`` and checking ``evidence``."""
    base = select(PoiFact).where(PoiFact.status == status)
    total = db.scalar(select(func.count()).select_from(PoiFact).where(PoiFact.status == status)) or 0
    rows = db.scalars(base.order_by(PoiFact.poi_id, PoiFact.fact_key).limit(limit).offset(offset)).all()
    return Page(items=[PoiFactOut.model_validate(f) for f in rows], total=total, limit=limit, offset=offset)


@router.post("/facts/{fact_id}", response_model=PoiFactOut)
def decide_fact(fact_id: int, body: FactDecision, db: Session = Depends(get_db)):
    fact = db.get(PoiFact, fact_id)
    if fact is None or db.get(Poi, fact.poi_id) is None:
        raise NotFoundError(f"Fact {fact_id} not found")
    fact.status = body.status
    fact.checked_at = datetime.now(UTC)
    if body.status == "verified" and not (fact.extracted_by or "").startswith("admin"):
        fact.extracted_by = f"{fact.extracted_by or 'unknown'}+admin"
    db.commit()
    return PoiFactOut.model_validate(fact)
