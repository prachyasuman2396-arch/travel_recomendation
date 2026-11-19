"""Storage interface used by the ingestion pipeline.

The production implementation is ``app.repositories.ingestion_store.SqlPoiStore``
(PostgreSQL/PostGIS). Tests use an in-memory implementation of the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.services.data_collection.base import FlickrPhotoRecord, YelpBusinessRecord
from app.services.entity_resolution.matcher import EntityView

UpsertResult = Literal["inserted", "updated", "unchanged"]


@dataclass(frozen=True)
class NewPoi:
    name: str
    normalized_name: str
    category: str
    categories: tuple[str, ...]
    latitude: float
    longitude: float
    primary_source: str
    data_origin: str
    description: str | None = None
    address: str | None = None
    website: str | None = None
    opening_hours: dict | None = None
    alt_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceLink:
    poi_id: str
    provider: str
    external_id: str
    confidence: float
    method: str  # origin | auto | manual
    data_origin: str
    fetched_at: datetime
    breakdown: dict | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class ReviewInput:
    """Provider-neutral review (Google or Yelp)."""

    source: str
    external_id: str
    rating: float | None
    text: str | None
    created_at: datetime | None
    data_origin: str
    fetched_at: datetime
    author_name: str | None = None
    author_url: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class FactInput:
    fact_key: str
    value: dict
    display_text: str
    source_type: str
    source_url: str
    status: str = "unverified"
    source_name: str | None = None
    evidence: str | None = None
    confidence: float = 1.0
    extracted_by: str | None = None
    checked_at: datetime | None = None


@dataclass(frozen=True)
class DocumentInput:
    source: str
    url: str
    title: str | None
    summary: str | None
    text: str
    text_hash: str
    data_origin: str
    fetched_at: datetime
    license: str | None = None
    image_url: str | None = None
    image_attribution: str | None = None


@dataclass(frozen=True)
class EventInput:
    name: str
    start_date: datetime
    end_date: datetime
    location_name: str
    latitude: float
    longitude: float
    radius_km: float
    poi_names: tuple[str, ...]
    crowd_impact: str
    description: str
    date_note: str | None
    source_url: str
    data_origin: str


@dataclass
class RunStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    flagged: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def reject(self, reason: str, n: int = 1) -> None:
        self.rejected += n
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + n


class PoiStore(Protocol):
    def find_source(self, provider: str, external_id: str) -> str | None: ...
    def find_nearby_views(self, latitude: float, longitude: float, radius_m: float) -> list[EntityView]: ...
    def create_poi(self, poi: NewPoi) -> str: ...
    def update_poi(self, poi_id: str, poi: NewPoi, *, overwrite: bool) -> bool: ...
    def upsert_source(self, link: SourceLink) -> None: ...
    def upsert_rating(self, poi_id: str, source: str, rating: float, count: int, fetched_at: datetime) -> None: ...
    def add_match_candidate(self, provider: str, external_id: str, poi_id: str, score: float,
                            breakdown: dict, payload: dict) -> bool: ...
    def upsert_yelp_business(self, poi_id: str, record: YelpBusinessRecord,
                             expires_at: datetime | None) -> UpsertResult: ...
    def upsert_review(self, poi_id: str, review: ReviewInput, text_hash: str | None,
                      expires_at: datetime | None) -> UpsertResult: ...
    def upsert_fact(self, poi_id: str, fact: FactInput) -> UpsertResult: ...
    def upsert_document(self, poi_id: str, doc: DocumentInput) -> UpsertResult: ...
    def add_alt_names(self, poi_id: str, names: tuple[str, ...]) -> bool: ...
    def wiki_targets(self) -> list[tuple[str, str | None, str | None]]: ...
    def upsert_event(self, event: EventInput) -> UpsertResult: ...
    def upsert_photo(self, poi_id: str, record: FlickrPhotoRecord, confidence: float,
                     method: str) -> UpsertResult: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class RunLog(Protocol):
    """Ingestion-run bookkeeping; must persist even when the data transaction rolls back."""

    def start(self, source: str, mode: str, params: dict) -> str: ...
    def finish(self, run_id: str, status: str, stats: RunStats, error: str | None = None) -> None: ...
