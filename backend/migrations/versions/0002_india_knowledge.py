"""India knowledge base: sourced facts, documents, embeddings, events, review attribution, alt names.

Revision ID: 0002_india
Revises: 0001_initial
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_india"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pois", sa.Column("alt_names", sa.ARRAY(sa.String(300)), nullable=False, server_default="{}"))
    op.add_column("reviews", sa.Column("author_name", sa.String(200)))
    op.add_column("reviews", sa.Column("author_url", sa.String(500)))
    op.add_column("reviews", sa.Column("language", sa.String(10)))

    op.create_table(
        "poi_facts",
        sa.Column("fact_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_facts_poi_id_pois"), nullable=False),
        sa.Column("fact_key", sa.String(40), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("display_text", sa.String(400), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("source_name", sa.String(120)),
        sa.Column("evidence", sa.Text),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="unverified"),
        sa.Column("extracted_by", sa.String(80)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("poi_id", "fact_key", "source_url", name="uq_poi_facts_poi_id_fact_key_source_url"),
        sa.CheckConstraint("source_type IN ('curated', 'official', 'google_places', 'osm', 'wikipedia', "
                           "'llm_extracted', 'mock')", name="ck_poi_facts_source_type"),
        sa.CheckConstraint("status IN ('verified', 'unverified', 'rejected')", name="ck_poi_facts_status"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_poi_facts_confidence_range"),
    )
    op.create_index("ix_poi_facts_poi_id", "poi_facts", ["poi_id"])

    op.create_table(
        "poi_documents",
        sa.Column("document_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_documents_poi_id_pois"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("title", sa.String(300)),
        sa.Column("summary", sa.Text),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("license", sa.String(60)),
        sa.Column("image_url", sa.String(500)),
        sa.Column("image_attribution", sa.String(300)),
        sa.Column("data_origin", sa.String(8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extracted_hash", sa.String(64)),
        sa.UniqueConstraint("poi_id", "source", "url", name="uq_poi_documents_poi_id_source_url"),
    )
    op.create_index("ix_poi_documents_poi_id", "poi_documents", ["poi_id"])

    op.create_table(
        "poi_embeddings",
        sa.Column("poi_id", sa.String(16), sa.ForeignKey("pois.poi_id", ondelete="CASCADE",
                                                         name="fk_poi_embeddings_poi_id_pois"), primary_key=True),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("vector", sa.ARRAY(sa.Float), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "events",
        sa.Column("event_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_name", sa.String(200), nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("radius_km", sa.Float, nullable=False, server_default="5"),
        sa.Column("poi_names", sa.ARRAY(sa.String(200)), nullable=False, server_default="{}"),
        sa.Column("crowd_impact", sa.String(10), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("date_note", sa.String(300)),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("data_origin", sa.String(8), nullable=False),
        sa.UniqueConstraint("name", "start_date", name="uq_events_name_start_date"),
        sa.CheckConstraint("crowd_impact IN ('low', 'medium', 'high', 'extreme')", name="ck_events_crowd_impact"),
        sa.CheckConstraint("end_date >= start_date", name="ck_events_date_order"),
        sa.CheckConstraint("data_origin IN ('mock', 'live')", name="ck_events_data_origin"),
    )
    op.create_index("ix_events_dates", "events", ["start_date", "end_date"])


def downgrade() -> None:
    op.drop_index("ix_events_dates", table_name="events")
    for table in ("events", "poi_embeddings", "poi_documents", "poi_facts"):
        op.drop_table(table)
    op.drop_column("reviews", "language")
    op.drop_column("reviews", "author_url")
    op.drop_column("reviews", "author_name")
    op.drop_column("pois", "alt_names")
