"""Domain types for candidate retrieval and ranking (independent of SQLAlchemy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.services.india.facts import FactView
from app.services.india.rules import EventView


@dataclass(frozen=True)
class RatingInfo:
    source: str
    rating: float
    count: int


@dataclass
class CandidateRow:
    """One POI inside the user's radius, with every fact the engine may cite."""

    poi_id: str
    name: str
    category: str
    categories: list[str]
    latitude: float
    longitude: float
    distance_m: float  # computed by PostGIS ST_Distance — never by the LLM
    data_origin: str
    description: str | None = None
    address: str | None = None
    website: str | None = None
    opening_hours: dict | None = None
    ratings: list[RatingInfo] = field(default_factory=list)
    photo_count: int = 0
    photo_url: str | None = None
    review_count: int = 0
    sentiment: dict | None = None  # poi_sentiment row as dict, or None
    review_snippets: list[str] = field(default_factory=list)
    # India knowledge base
    facts: list[FactView] = field(default_factory=list)
    alt_names: list[str] = field(default_factory=list)
    wiki_summary: str | None = None
    wiki_url: str | None = None
    image_url: str | None = None  # Wikimedia Commons (openly licensed) when available
    image_attribution: str | None = None
    embedding: list[float] | None = None
    business_status: str | None = None  # from Google (OPERATIONAL, CLOSED_TEMPORARILY, ...)


class CandidateSource(Protocol):
    def find_candidates(self, latitude: float, longitude: float, radius_m: float,
                        limit: int) -> list[CandidateRow]: ...

    def events_between(self, start: datetime, end: datetime) -> list[EventView]: ...
