"""Initial schema: PostGIS, POIs, sources, reviews, photos, context, ingestion runs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGIN = "data_origin IN ('mock', 'live')"


def _ts(name: str, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE SEQUENCE IF NOT EXISTS poi_id_seq")

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), unique=True),
        sa.Column("preferences", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at"),
        _ts("updated_at"),
    )

    op.create_table(
        "pois",
        sa.Column("poi_id", sa.String(16), primary_key=True,
                  server_default=sa.text("'POI' || lpad(nextval('poi_id_seq')::text, 6, '0')")),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("categories", sa.ARRAY(sa.String(40)), nullable=False, server_default="{}"),
        sa.Column("address", sa.String(500)),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("location", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("website", sa.String(500)),
        sa.Column("opening_hours", JSONB),
        sa.Column("primary_source", sa.String(20), nullable=False),
        sa.Column("data_origin", sa.String(8), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_pois_latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_pois_longitude_range"),
        sa.CheckConstraint(ORIGIN, name="ck_pois_data_origin"),
    )
    op.execute("ALTER SEQUENCE poi_id_seq OWNED BY pois.poi_id")
    op.create_index("ix_pois_location_gist", "pois", ["location"], postgresql_using="gist")
    op.create_index("ix_pois_category", "pois", ["category"])
    op.create_index("ix_pois_normalized_name", "pois", ["normalized_name"])

    op.create_table(
        "poi_sources",
        sa.Column("source_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_sources_poi_id_pois"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("matching_confidence", sa.Float, nullable=False),
        sa.Column("matching_method", sa.String(20), nullable=False),
        sa.Column("score_breakdown", JSONB),
        sa.Column("source_metadata", JSONB),
        sa.Column("data_origin", sa.String(8), nullable=False),
        _ts("fetched_at", default=False),
        _ts("created_at", nullable=True),
        sa.UniqueConstraint("provider", "external_id", name="uq_poi_sources_provider_external_id"),
        sa.CheckConstraint("matching_confidence BETWEEN 0 AND 1", name="ck_poi_sources_confidence_range"),
        sa.CheckConstraint(ORIGIN, name="ck_poi_sources_data_origin"),
    )
    op.create_index("ix_poi_sources_poi_id", "poi_sources", ["poi_id"])

    op.create_table(
        "match_candidates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("candidate_poi_id", sa.String(16),
                  sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                name="fk_match_candidates_candidate_poi_id_pois"), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("score_breakdown", JSONB, nullable=False),
        sa.Column("source_payload", JSONB, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        _ts("created_at", nullable=True),
        sa.UniqueConstraint("provider", "external_id", "candidate_poi_id",
                            name="uq_match_candidates_provider_external_id_candidate_poi_id"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_match_candidates_status"),
    )

    op.create_table(
        "poi_ratings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_ratings_poi_id_pois"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("rating", sa.Float, nullable=False),
        sa.Column("rating_count", sa.Integer, nullable=False, server_default="0"),
        _ts("fetched_at", default=False),
        sa.UniqueConstraint("poi_id", "source", name="uq_poi_ratings_poi_id_source"),
        sa.CheckConstraint("rating BETWEEN 0 AND 5", name="ck_poi_ratings_rating_range"),
        sa.CheckConstraint("rating_count >= 0", name="ck_poi_ratings_rating_count_nonneg"),
    )

    op.create_table(
        "reviews",
        sa.Column("review_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_reviews_poi_id_pois"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_review_id", sa.String(200), nullable=False),
        sa.Column("rating", sa.Float),
        sa.Column("review_text", sa.Text),
        sa.Column("text_hash", sa.String(64)),
        _ts("review_date", nullable=True, default=False),
        sa.Column("data_origin", sa.String(8), nullable=False),
        _ts("fetched_at", default=False),
        _ts("expires_at", nullable=True, default=False),
        _ts("created_at", nullable=True),
        sa.UniqueConstraint("source", "external_review_id", name="uq_reviews_source_external_review_id"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="ck_reviews_rating_range"),
        sa.CheckConstraint(ORIGIN, name="ck_reviews_data_origin"),
    )
    op.create_index("ix_reviews_poi_id", "reviews", ["poi_id"])
    op.create_index("ix_reviews_expires_at", "reviews", ["expires_at"])

    op.create_table(
        "review_analyses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer,
                  sa.ForeignKey("reviews.review_id", ondelete="CASCADE",
                                name="fk_review_analyses_review_id_reviews"), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("sentiment", sa.String(10), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("aspects", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(60)),
        _ts("analyzed_at", nullable=True),
        sa.UniqueConstraint("review_id", "model_name", name="uq_review_analyses_review_id_model_name"),
    )

    op.create_table(
        "poi_sentiment",
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_sentiment_poi_id_pois"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviews_analyzed", sa.Integer, nullable=False),
        sa.Column("overall", sa.String(10)),
        sa.Column("positive_share", sa.Float),
        sa.Column("neutral_share", sa.Float),
        sa.Column("negative_share", sa.Float),
        sa.Column("aspects", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_name", sa.String(120), nullable=False),
        _ts("updated_at", nullable=True),
    )

    op.create_table(
        "flickr_photos",
        sa.Column("photo_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_flickr_photos_poi_id_pois"), nullable=False),
        sa.Column("external_photo_id", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(60)),
        sa.Column("owner_name", sa.String(200)),
        sa.Column("title", sa.String(500)),
        sa.Column("tags", sa.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        _ts("uploaded_at", nullable=True, default=False),
        _ts("taken_at", nullable=True, default=False),
        sa.Column("license_id", sa.String(10)),
        sa.Column("permitted_photo_url", sa.String(500)),
        sa.Column("page_url", sa.String(500)),
        sa.Column("match_confidence", sa.Float, nullable=False),
        sa.Column("match_method", sa.String(40), nullable=False),
        sa.Column("data_origin", sa.String(8), nullable=False),
        _ts("created_at", nullable=True),
        sa.UniqueConstraint("external_photo_id", name="uq_flickr_photos_external_photo_id"),
        sa.CheckConstraint("match_confidence BETWEEN 0 AND 1", name="ck_flickr_photos_confidence_range"),
        sa.CheckConstraint(ORIGIN, name="ck_flickr_photos_data_origin"),
    )
    op.create_index("ix_flickr_photos_poi_id", "flickr_photos", ["poi_id"])

    op.create_table(
        "yelp_businesses",
        sa.Column("business_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_yelp_businesses_poi_id_pois"), nullable=False),
        sa.Column("external_business_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("rating", sa.Float),
        sa.Column("review_count", sa.Integer),
        sa.Column("categories", sa.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        sa.Column("url", sa.String(500)),
        sa.Column("data_origin", sa.String(8), nullable=False),
        _ts("fetched_at", default=False),
        _ts("expires_at", nullable=True, default=False),
        sa.UniqueConstraint("external_business_id", name="uq_yelp_businesses_external_business_id"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="ck_yelp_businesses_rating_range"),
        sa.CheckConstraint(ORIGIN, name="ck_yelp_businesses_data_origin"),
    )
    op.create_index("ix_yelp_businesses_poi_id", "yelp_businesses", ["poi_id"])

    op.create_table(
        "poi_context",
        sa.Column("context_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_context_poi_id_pois"), nullable=False),
        sa.Column("context_type", sa.String(10), nullable=False),
        sa.Column("weather_condition", sa.String(60)),
        sa.Column("weather_code", sa.Integer),
        sa.Column("temperature_c", sa.Float),
        sa.Column("precipitation_probability", sa.Float),
        sa.Column("humidity", sa.Float),
        sa.Column("wind_speed_kmh", sa.Float),
        sa.Column("season", sa.String(20)),
        _ts("valid_at", default=False),
        _ts("recorded_at", nullable=True),
        sa.Column("context_source", sa.String(30), nullable=False),
        sa.Column("is_mock", sa.Boolean, nullable=False, server_default="false"),
        sa.CheckConstraint("context_type IN ('current', 'forecast')", name="ck_poi_context_context_type"),
    )
    op.create_index("ix_poi_context_poi_recorded", "poi_context", ["poi_id", "recorded_at"])

    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("started_at", nullable=True),
        _ts("completed_at", nullable=True, default=False),
        sa.Column("records_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_flagged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text),
        sa.Column("error_details", JSONB),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')",
                           name="ck_ingestion_runs_status"),
    )
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])


def downgrade() -> None:
    for table in (
        "ingestion_runs", "poi_context", "yelp_businesses", "flickr_photos", "poi_sentiment",
        "review_analyses", "reviews", "poi_ratings", "match_candidates", "poi_sources", "pois", "users",
    ):
        op.drop_table(table)
    op.execute("DROP SEQUENCE IF EXISTS poi_id_seq")
