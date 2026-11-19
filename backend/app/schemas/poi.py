from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source: str
    rating: float
    rating_count: int
    fetched_at: datetime


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider: str
    external_id: str
    matching_confidence: float
    matching_method: str
    data_origin: str
    fetched_at: datetime


class PoiSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    poi_id: str
    name: str
    category: str
    categories: list[str]
    latitude: float
    longitude: float
    address: str | None
    data_origin: str
    distance_km: float | None = None


class PoiFactOut(BaseModel):
    """A sourced fact. Only ``status == 'verified'`` facts can exclude a place; others are shown as 'Reported'."""
    model_config = ConfigDict(from_attributes=True)
    fact_id: int
    poi_id: str
    fact_key: str
    value: dict
    display_text: str
    source_type: str
    source_name: str | None
    source_url: str
    evidence: str | None
    status: str
    extracted_by: str | None
    checked_at: datetime | None


class PoiDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source: str
    url: str
    title: str | None
    summary: str | None
    license: str | None
    image_url: str | None
    image_attribution: str | None
    data_origin: str


class PoiDetail(PoiSummary):
    description: str | None
    website: str | None
    opening_hours: dict | None
    primary_source: str
    rating: float | None
    rating_count: int
    ratings: list[RatingOut]
    sources: list[SourceOut]
    sentiment: dict | None
    review_count: int
    photo_count: int
    updated_at: datetime
    alt_names: list[str] = []
    facts: list[PoiFactOut] = []
    background: PoiDocumentOut | None = None
    google_photo_count: int = 0


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    review_id: int
    source: str
    rating: float | None
    review_text: str | None
    review_date: datetime | None
    author_name: str | None = None
    author_url: str | None = None
    language: str | None = None
    data_origin: str


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    external_photo_id: str
    title: str | None
    tags: list[str]
    latitude: float
    longitude: float
    taken_at: datetime | None
    owner_name: str | None
    license_id: str | None
    permitted_photo_url: str | None
    page_url: str | None
    match_confidence: float
    match_method: str
    data_origin: str
