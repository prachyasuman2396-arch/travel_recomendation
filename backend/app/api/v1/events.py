"""Festival / crowd calendar (curated, sourced)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.poi_repository import PoiRepository
from app.schemas.recommendation import EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(start: datetime | None = None, days: int = Query(60, ge=1, le=400), db: Session = Depends(get_db)):
    begin = start or datetime.now(UTC)
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=UTC)
    rows = PoiRepository(db).events_between(begin, begin + timedelta(days=days))
    return [EventOut(name=e.name, start=e.start, end=e.end, location_name=e.location_name,
                     crowd_impact=e.crowd_impact, description=e.description, date_note=e.date_note,
                     source_url=e.source_url) for e in sorted(rows, key=lambda e: e.start)]
