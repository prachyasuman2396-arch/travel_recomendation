"""Relational schema.

Three layers are kept distinct (see docs/architecture.md):
  * raw provider responses       -> files under data/raw/ (only where terms allow)
  * normalised source records    -> poi_sources, yelp_businesses, flickr_photos, reviews, poi_ratings
  * unified POIs                 -> pois (our own POI000001-style IDs)

Every source-derived row records its provider, external id and ``data_origin``
("mock" or "live") so mock data can never be presented as live data.
"""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

POI_ID_SEQUENCE = Sequence("poi_id_seq")
DATA_ORIGIN_CHECK = "data_origin IN ('mock', 'live')"


class User(TimestampMixin, Base):
    """Preferences only — no interaction/click/visit tracking by design."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Poi(TimestampMixin, Base):
    __tablename__ = "pois"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_pois_location_gist", "location", postgresql_using="gist"),
        Index("ix_pois_category", "category"),
        Index("ix_pois_normalized_name", "normalized_name"),
    )

    # DB-generated, human-readable, concurrency-safe: POI000001, POI000002, ...
    poi_id: Mapped[str] = mapped_column(
        String(16),
        primary_key=True,
        server_default=text("'POI' || lpad(nextval('poi_id_seq')::text, 6, '0')"),
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, server_default="{}")
    # Local-language and variant names (e.g. Odia/Hindi from OSM name:or / name:hi) used for matching & search
    alt_names: Mapped[list[str]] = mapped_column(ARRAY(String(300)), nullable=False, server_default="{}")
    address: Mapped[str | None] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # geography(Point, 4326): distances in metres on the spheroid, no projection needed.
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    website: Mapped[str | None] = mapped_column(String(500))
    opening_hours: Mapped[dict | None] = mapped_column(JSONB)
    primary_source: Mapped[str] = mapped_column(String(20), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)

    sources: Mapped[list[PoiSource]] = relationship(back_populates="poi", cascade="all, delete-orphan")
    ratings: Mapped[list[PoiRating]] = relationship(back_populates="poi", cascade="all, delete-orphan")
    reviews: Mapped[list[Review]] = relationship(back_populates="poi", cascade="all, delete-orphan")
    photos: Mapped[list[FlickrPhoto]] = relationship(back_populates="poi", cascade="all, delete-orphan")
    sentiment: Mapped[PoiSentiment | None] = relationship(back_populates="poi", cascade="all, delete-orphan")
    facts: Mapped[list[PoiFact]] = relationship(back_populates="poi", cascade="all, delete-orphan")


class PoiSource(Base):
    """Links a provider record to our POI, with the evidence for the link."""

    __tablename__ = "poi_sources"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        CheckConstraint("matching_confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_poi_sources_poi_id", "poi_id"),
    )

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    matching_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # origin = record created this POI; auto = high-confidence match; manual = reviewer accepted
    matching_method: Mapped[str] = mapped_column(String(20), nullable=False)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    poi: Mapped[Poi] = relationship(back_populates="sources")


class MatchCandidate(Base):
    """Medium-confidence matches waiting for manual review (never auto-merged)."""

    __tablename__ = "match_candidates"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", "candidate_poi_id"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    candidate_poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PoiRating(Base):
    """Aggregate rating per (POI, source). Kept per source for provenance."""

    __tablename__ = "poi_ratings"
    __table_args__ = (
        UniqueConstraint("poi_id", "source"),
        CheckConstraint("rating BETWEEN 0 AND 5", name="rating_range"),
        CheckConstraint("rating_count >= 0", name="rating_count_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    poi: Mapped[Poi] = relationship(back_populates="ratings")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("source", "external_review_id"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="rating_range"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_reviews_poi_id", "poi_id"),
        Index("ix_reviews_expires_at", "expires_at"),
    )

    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_review_id: Mapped[str] = mapped_column(String(200), nullable=False)
    rating: Mapped[float | None] = mapped_column(Float)
    review_text: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Google requires showing the author attribution next to each review.
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(10))
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set for providers with retention limits (Yelp, Google); purged by scripts/purge_expired.py
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    poi: Mapped[Poi] = relationship(back_populates="reviews")


class ReviewAnalysis(Base):
    """Per-review NLP cache keyed by text hash + model, so unchanged reviews are not re-analysed."""

    __tablename__ = "review_analyses"
    __table_args__ = (UniqueConstraint("review_id", "model_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.review_id", ondelete="CASCADE"), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    aspects: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(60))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PoiSentiment(Base):
    __tablename__ = "poi_sentiment"

    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # available | insufficient_data
    reviews_analyzed: Mapped[int] = mapped_column(Integer, nullable=False)
    overall: Mapped[str | None] = mapped_column(String(10))
    positive_share: Mapped[float | None] = mapped_column(Float)
    neutral_share: Mapped[float | None] = mapped_column(Float)
    negative_share: Mapped[float | None] = mapped_column(Float)
    aspects: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    poi: Mapped[Poi] = relationship(back_populates="sentiment")


class FlickrPhoto(Base):
    __tablename__ = "flickr_photos"
    __table_args__ = (
        UniqueConstraint("external_photo_id"),
        CheckConstraint("match_confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_flickr_photos_poi_id", "poi_id"),
    )

    photo_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    external_photo_id: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(60))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False, server_default="{}")
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    license_id: Mapped[str | None] = mapped_column(String(10))
    permitted_photo_url: Mapped[str | None] = mapped_column(String(500))
    page_url: Mapped[str | None] = mapped_column(String(500))
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[str] = mapped_column(String(40), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    poi: Mapped[Poi] = relationship(back_populates="photos")


class YelpBusiness(Base):
    __tablename__ = "yelp_businesses"
    __table_args__ = (
        UniqueConstraint("external_business_id"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="rating_range"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_yelp_businesses_poi_id", "poi_id"),
    )

    business_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    external_business_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False, server_default="{}")
    url: Mapped[str | None] = mapped_column(String(500))
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PoiContext(Base):
    """Historical weather context; current observations and forecasts are distinguished."""

    __tablename__ = "poi_context"
    __table_args__ = (
        CheckConstraint("context_type IN ('current', 'forecast')", name="context_type"),
        Index("ix_poi_context_poi_recorded", "poi_id", "recorded_at"),
    )

    context_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    context_type: Mapped[str] = mapped_column(String(10), nullable=False)
    weather_condition: Mapped[str | None] = mapped_column(String(60))
    weather_code: Mapped[int | None] = mapped_column(Integer)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    precipitation_probability: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)
    wind_speed_kmh: Mapped[float | None] = mapped_column(Float)
    season: Mapped[str | None] = mapped_column(String(20))
    valid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    context_source: Mapped[str] = mapped_column(String(30), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="status"),
        Index("ix_ingestion_runs_started_at", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # mock | live
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_flagged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_summary: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict | None] = mapped_column(JSONB)


FACT_SOURCE_TYPES = ("source_type IN ('curated', 'official', 'google_places', 'osm', 'wikipedia', "
                     "'llm_extracted', 'mock')")
FACT_STATUSES = "status IN ('verified', 'unverified', 'rejected')"


class PoiFact(Base):
    """A single, sourced, India-relevant fact about a POI (entry rules, fees, dress code, timings...).

    Only ``verified`` facts may drive hard filtering. ``unverified`` facts (e.g. extracted by the
    fact-extraction agent) are shown to users as "reported" and never exclude a place on their own.
    """

    __tablename__ = "poi_facts"
    __table_args__ = (
        UniqueConstraint("poi_id", "fact_key", "source_url"),
        CheckConstraint(FACT_SOURCE_TYPES, name="source_type"),
        CheckConstraint(FACT_STATUSES, name="status"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_poi_facts_poi_id", "poi_id"),
    )

    fact_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    display_text: Mapped[str] = mapped_column(String(400), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    source_name: Mapped[str | None] = mapped_column(String(120))
    evidence: Mapped[str | None] = mapped_column(Text)  # exact quote from the source document
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="unverified")
    extracted_by: Mapped[str | None] = mapped_column(String(80))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    poi: Mapped[Poi] = relationship(back_populates="facts")


class PoiDocument(Base):
    """Text about a POI (Wikipedia extract, official page) kept for fact extraction and semantic search."""

    __tablename__ = "poi_documents"
    __table_args__ = (UniqueConstraint("poi_id", "source", "url"), Index("ix_poi_documents_poi_id", "poi_id"))

    document_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # wikipedia | official | mock
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    license: Mapped[str | None] = mapped_column(String(60))
    image_url: Mapped[str | None] = mapped_column(String(500))
    image_attribution: Mapped[str | None] = mapped_column(String(300))
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # text_hash the fact-extraction agent last processed (skip unchanged documents)
    extracted_hash: Mapped[str | None] = mapped_column(String(64))


class PoiEmbedding(Base):
    """Semantic vector per POI. Stored as float[]: retrieval is geo-filtered first (≤ a few hundred
    candidates), so cosine similarity in NumPy is enough — no pgvector/vector DB needed."""

    __tablename__ = "poi_embeddings"

    poi_id: Mapped[str] = mapped_column(ForeignKey("pois.poi_id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    vector: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """Festivals and events that change crowds or access (Rath Yatra, Shivaratri, Konark Festival...)."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("name", "start_date"),
        CheckConstraint("crowd_impact IN ('low', 'medium', 'high', 'extreme')", name="crowd_impact"),
        CheckConstraint("end_date >= start_date", name="date_order"),
        CheckConstraint(DATA_ORIGIN_CHECK, name="data_origin"),
        Index("ix_events_dates", "start_date", "end_date"),
    )

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location_name: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False, server_default="5")
    poi_names: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False, server_default="{}")
    crowd_impact: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    date_note: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(8), nullable=False)
