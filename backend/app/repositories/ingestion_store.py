"""PostgreSQL/PostGIS implementation of the ingestion ``PoiStore`` and ``RunLog``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Event,
    FlickrPhoto,
    IngestionRun,
    MatchCandidate,
    Poi,
    PoiDocument,
    PoiFact,
    PoiRating,
    PoiSource,
    Review,
    YelpBusiness,
)
from app.repositories.geo import geography_point
from app.services.data_collection.base import FlickrPhotoRecord, YelpBusinessRecord
from app.services.data_collection.wikipedia_client import parse_wikipedia_tag
from app.services.entity_resolution.matcher import EntityView
from app.services.ingestion.store import (
    DocumentInput,
    EventInput,
    FactInput,
    NewPoi,
    ReviewInput,
    RunStats,
    SourceLink,
    UpsertResult,
)

# `xmax = 0` is true for a freshly inserted row and false for an ON CONFLICT update.
INSERTED = literal_column("(xmax = 0)").label("inserted")

_FILLABLE = ("description", "address", "website", "opening_hours")


def _outcome(row) -> UpsertResult:
    if row is None:
        return "unchanged"
    return "inserted" if row.inserted else "updated"


class SqlPoiStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_source(self, provider: str, external_id: str) -> str | None:
        return self.session.scalar(
            select(PoiSource.poi_id).where(PoiSource.provider == provider, PoiSource.external_id == external_id)
        )

    def find_nearby_views(self, latitude: float, longitude: float, radius_m: float) -> list[EntityView]:
        rows = self.session.execute(
            select(Poi.poi_id, Poi.name, Poi.latitude, Poi.longitude, Poi.categories, Poi.address)
            .where(func.ST_DWithin(Poi.location, geography_point(latitude, longitude), radius_m))
        ).all()
        return [EntityView(r.poi_id, r.name, r.latitude, r.longitude, tuple(r.categories or ()), r.address)
                for r in rows]

    def create_poi(self, poi: NewPoi) -> str:
        return self.session.scalar(
            insert(Poi).values(
                name=poi.name, normalized_name=poi.normalized_name, description=poi.description,
                category=poi.category, categories=list(poi.categories), address=poi.address,
                latitude=poi.latitude, longitude=poi.longitude,
                location=geography_point(poi.latitude, poi.longitude),
                website=poi.website, opening_hours=poi.opening_hours, alt_names=list(poi.alt_names),
                primary_source=poi.primary_source, data_origin=poi.data_origin,
            ).returning(Poi.poi_id)
        )

    def update_poi(self, poi_id: str, poi: NewPoi, *, overwrite: bool) -> bool:
        current = self.session.get(Poi, poi_id)
        if current is None:
            return False
        changes: dict = {}
        if overwrite:
            for attr in ("name", "normalized_name", "category", *_FILLABLE):
                new = getattr(poi, attr)
                if new is not None and getattr(current, attr) != new:
                    changes[attr] = new
            if list(poi.categories) and list(current.categories or []) != list(poi.categories):
                changes["categories"] = list(poi.categories)
            if (current.latitude, current.longitude) != (poi.latitude, poi.longitude):
                changes |= {"latitude": poi.latitude, "longitude": poi.longitude,
                            "location": geography_point(poi.latitude, poi.longitude)}
        else:
            # A matched (non-origin) record only fills gaps; it never overwrites.
            for attr in _FILLABLE:
                if getattr(current, attr) is None and getattr(poi, attr) is not None:
                    changes[attr] = getattr(poi, attr)
            merged = list(dict.fromkeys([*(current.categories or []), *poi.categories]))
            if merged != list(current.categories or []):
                changes["categories"] = merged
        if not changes:
            return False
        self.session.execute(update(Poi).where(Poi.poi_id == poi_id).values(**changes))
        return True

    def upsert_source(self, link: SourceLink) -> None:
        stmt = insert(PoiSource).values(
            poi_id=link.poi_id, provider=link.provider, external_id=link.external_id,
            matching_confidence=link.confidence, matching_method=link.method, score_breakdown=link.breakdown,
            source_metadata=link.metadata, data_origin=link.data_origin, fetched_at=link.fetched_at,
        )
        # poi_id is intentionally NOT updated: a source's POI assignment is stable once made.
        self.session.execute(stmt.on_conflict_do_update(
            index_elements=["provider", "external_id"],
            set_={"source_metadata": stmt.excluded.source_metadata, "fetched_at": stmt.excluded.fetched_at},
        ))

    def upsert_rating(self, poi_id: str, source: str, rating: float, count: int, fetched_at: datetime) -> None:
        stmt = insert(PoiRating).values(poi_id=poi_id, source=source, rating=rating, rating_count=count,
                                        fetched_at=fetched_at)
        self.session.execute(stmt.on_conflict_do_update(
            index_elements=["poi_id", "source"],
            set_={"rating": stmt.excluded.rating, "rating_count": stmt.excluded.rating_count,
                  "fetched_at": stmt.excluded.fetched_at},
        ))

    def add_match_candidate(self, provider: str, external_id: str, poi_id: str, score: float,
                            breakdown: dict, payload: dict) -> bool:
        row = self.session.execute(
            insert(MatchCandidate).values(
                provider=provider, external_id=external_id, candidate_poi_id=poi_id, score=score,
                score_breakdown=breakdown, source_payload=payload,
            ).on_conflict_do_nothing().returning(MatchCandidate.id)
        ).first()
        return row is not None

    def upsert_yelp_business(self, poi_id: str, record: YelpBusinessRecord,
                             expires_at: datetime | None) -> UpsertResult:
        stmt = insert(YelpBusiness).values(
            poi_id=poi_id, external_business_id=record.external_id, name=record.name, rating=record.rating,
            review_count=record.review_count, categories=list(record.categories), url=record.url,
            data_origin=record.data_origin, fetched_at=record.fetched_at, expires_at=expires_at,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["external_business_id"],
            set_={k: stmt.excluded[k] for k in ("name", "rating", "review_count", "categories", "url",
                                                "fetched_at", "expires_at")},
        ).returning(INSERTED)).first()
        return _outcome(row)

    def upsert_review(self, poi_id: str, review: ReviewInput, text_hash: str | None,
                      expires_at: datetime | None) -> UpsertResult:
        stmt = insert(Review).values(
            poi_id=poi_id, source=review.source, external_review_id=review.external_id, rating=review.rating,
            review_text=review.text, text_hash=text_hash, review_date=review.created_at,
            author_name=review.author_name, author_url=review.author_url, language=review.language,
            data_origin=review.data_origin, fetched_at=review.fetched_at, expires_at=expires_at,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["source", "external_review_id"],
            set_={"rating": stmt.excluded.rating, "review_text": stmt.excluded.review_text,
                  "text_hash": stmt.excluded.text_hash, "fetched_at": stmt.excluded.fetched_at,
                  "expires_at": stmt.excluded.expires_at, "author_name": stmt.excluded.author_name,
                  "author_url": stmt.excluded.author_url},
            # Only touch the row when content changed -> unchanged reviews keep their sentiment cache.
            where=(Review.text_hash.is_distinct_from(stmt.excluded.text_hash)
                   | Review.rating.is_distinct_from(stmt.excluded.rating)),
        ).returning(INSERTED)).first()
        return _outcome(row)

    def upsert_fact(self, poi_id: str, fact: FactInput) -> UpsertResult:
        stmt = insert(PoiFact).values(
            poi_id=poi_id, fact_key=fact.fact_key, value=fact.value, display_text=fact.display_text,
            source_type=fact.source_type, source_url=fact.source_url, source_name=fact.source_name,
            evidence=fact.evidence, confidence=fact.confidence, status=fact.status,
            extracted_by=fact.extracted_by, checked_at=fact.checked_at,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["poi_id", "fact_key", "source_url"],
            set_={"value": stmt.excluded.value, "display_text": stmt.excluded.display_text,
                  "evidence": stmt.excluded.evidence, "confidence": stmt.excluded.confidence,
                  "checked_at": stmt.excluded.checked_at, "updated_at": func.now(),
                  # Never downgrade a verified (or admin-rejected) fact through re-ingestion.
                  "status": case((PoiFact.status.in_(["verified", "rejected"]), PoiFact.status),
                                 else_=stmt.excluded.status)},
            where=(PoiFact.value.is_distinct_from(stmt.excluded.value)
                   | PoiFact.display_text.is_distinct_from(stmt.excluded.display_text)
                   | PoiFact.status.is_distinct_from(stmt.excluded.status)),
        ).returning(INSERTED)).first()
        return _outcome(row)

    def upsert_document(self, poi_id: str, doc: DocumentInput) -> UpsertResult:
        stmt = insert(PoiDocument).values(
            poi_id=poi_id, source=doc.source, url=doc.url, title=doc.title, summary=doc.summary, text=doc.text,
            text_hash=doc.text_hash, license=doc.license, image_url=doc.image_url,
            image_attribution=doc.image_attribution, data_origin=doc.data_origin, fetched_at=doc.fetched_at,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["poi_id", "source", "url"],
            set_={k: stmt.excluded[k] for k in ("title", "summary", "text", "text_hash", "image_url",
                                                "image_attribution", "fetched_at")},
            where=PoiDocument.text_hash.is_distinct_from(stmt.excluded.text_hash),
        ).returning(INSERTED)).first()
        return _outcome(row)

    def add_alt_names(self, poi_id: str, names: tuple[str, ...]) -> bool:
        poi = self.session.get(Poi, poi_id)
        if poi is None:
            return False
        merged = list(dict.fromkeys([*(poi.alt_names or []), *names]))
        merged = [n for n in merged if n != poi.name]
        if merged == list(poi.alt_names or []):
            return False
        self.session.execute(update(Poi).where(Poi.poi_id == poi_id).values(alt_names=merged))
        return True

    def wiki_targets(self) -> list[tuple[str, str | None, str | None]]:
        rows = self.session.execute(
            select(PoiSource.poi_id, PoiSource.source_metadata["wikipedia"].astext,
                   PoiSource.source_metadata["wikidata"].astext)
            .where(PoiSource.provider == "osm")
            .where(PoiSource.source_metadata["wikipedia"].astext.is_not(None)
                   | PoiSource.source_metadata["wikidata"].astext.is_not(None))
        ).all()
        out: dict[str, tuple[str, str | None, str | None]] = {}
        for poi_id, wiki, qid in rows:
            parsed = parse_wikipedia_tag(wiki)
            title = parsed[1] if parsed and parsed[0] == "en" else None
            out.setdefault(poi_id, (poi_id, title, qid))
        return list(out.values())

    def upsert_event(self, event: EventInput) -> UpsertResult:
        stmt = insert(Event).values(
            name=event.name, start_date=event.start_date, end_date=event.end_date,
            location_name=event.location_name, latitude=event.latitude, longitude=event.longitude,
            radius_km=event.radius_km, poi_names=list(event.poi_names), crowd_impact=event.crowd_impact,
            description=event.description, date_note=event.date_note, source_url=event.source_url,
            data_origin=event.data_origin,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["name", "start_date"],
            set_={k: stmt.excluded[k] for k in ("end_date", "location_name", "latitude", "longitude", "radius_km",
                                                "poi_names", "crowd_impact", "description", "date_note",
                                                "source_url")},
        ).returning(INSERTED)).first()
        return _outcome(row)

    def upsert_photo(self, poi_id: str, record: FlickrPhotoRecord, confidence: float, method: str) -> UpsertResult:
        stmt = insert(FlickrPhoto).values(
            poi_id=poi_id, external_photo_id=record.external_id, owner_id=record.owner_id,
            owner_name=record.owner_name, title=record.title, tags=list(record.tags),
            latitude=record.latitude, longitude=record.longitude, uploaded_at=record.uploaded_at,
            taken_at=record.taken_at, license_id=record.license_id, permitted_photo_url=record.photo_url,
            page_url=record.page_url, match_confidence=confidence, match_method=method,
            data_origin=record.data_origin,
        )
        row = self.session.execute(stmt.on_conflict_do_update(
            index_elements=["external_photo_id"],
            set_={"title": stmt.excluded.title, "tags": stmt.excluded.tags,
                  "match_confidence": stmt.excluded.match_confidence, "match_method": stmt.excluded.match_method,
                  "permitted_photo_url": stmt.excluded.permitted_photo_url},
            where=(FlickrPhoto.tags.is_distinct_from(stmt.excluded.tags)
                   | FlickrPhoto.match_confidence.is_distinct_from(stmt.excluded.match_confidence)),
        ).returning(INSERTED)).first()
        return _outcome(row)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


class SqlRunLog:
    """Uses its own short transactions so a failed run is still recorded."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def start(self, source: str, mode: str, params: dict) -> str:
        run_id = str(uuid.uuid4())
        with self.session_factory() as s, s.begin():
            s.add(IngestionRun(run_id=run_id, source=source, mode=mode, status="running", params=params))
        return run_id

    def finish(self, run_id: str, status: str, stats: RunStats, error: str | None = None) -> None:
        details = {"rejection_reasons": stats.rejection_reasons, "notes": stats.notes}
        with self.session_factory() as s, s.begin():
            s.execute(update(IngestionRun).where(IngestionRun.run_id == run_id).values(
                status=status, completed_at=datetime.now(UTC), records_fetched=stats.fetched,
                records_inserted=stats.inserted, records_updated=stats.updated,
                records_rejected=stats.rejected, records_flagged=stats.flagged,
                error_summary=error, error_details=details,
            ))
