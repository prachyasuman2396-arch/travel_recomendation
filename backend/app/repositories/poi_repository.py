"""Read-side POI queries (PostGIS).

Candidate retrieval is ONE statement: ST_DWithin (index-backed via GiST) as the
hard radius filter, ST_Distance for the true geodesic distance, and grouped
sub-queries for ratings/photos/reviews — no per-POI round trips (no N+1).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    FlickrPhoto,
    Poi,
    PoiDocument,
    PoiEmbedding,
    PoiFact,
    PoiRating,
    PoiSentiment,
    PoiSource,
    Review,
)
from app.repositories.geo import geography_point
from app.services.india.facts import FactView
from app.services.india.rules import EventView
from app.services.recommendation.types import CandidateRow, RatingInfo


def _ratings_subquery():
    return (
        select(
            PoiRating.poi_id,
            func.json_agg(func.json_build_object("source", PoiRating.source, "rating", PoiRating.rating,
                                                 "count", PoiRating.rating_count)).label("ratings"),
        ).group_by(PoiRating.poi_id).subquery()
    )


def _photos_subquery():
    return (
        select(
            FlickrPhoto.poi_id,
            func.count().label("photo_count"),
            # One deterministic permitted URL per POI for card thumbnails (NULLs ignored by max()).
            func.max(FlickrPhoto.permitted_photo_url).label("photo_url"),
        ).group_by(FlickrPhoto.poi_id).subquery()
    )


def _reviews_subquery():
    return select(Review.poi_id, func.count().label("review_count")).group_by(Review.poi_id).subquery()


def _sentiment_dict(s: PoiSentiment | None) -> dict | None:
    if s is None:
        return None
    return {"status": s.status, "reviews_analyzed": s.reviews_analyzed, "overall": s.overall,
            "positive_share": s.positive_share, "neutral_share": s.neutral_share,
            "negative_share": s.negative_share, "aspects": s.aspects or {}, "model_name": s.model_name}


class PoiRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------ candidates
    def find_candidates(self, latitude: float, longitude: float, radius_m: float, limit: int) -> list[CandidateRow]:
        point = geography_point(latitude, longitude)
        distance = func.ST_Distance(Poi.location, point).label("distance_m")
        ratings, photos, reviews = _ratings_subquery(), _photos_subquery(), _reviews_subquery()
        stmt = (
            select(Poi, distance, ratings.c.ratings, photos.c.photo_count, photos.c.photo_url,
                   reviews.c.review_count, PoiSentiment)
            .outerjoin(ratings, ratings.c.poi_id == Poi.poi_id)
            .outerjoin(photos, photos.c.poi_id == Poi.poi_id)
            .outerjoin(reviews, reviews.c.poi_id == Poi.poi_id)
            .outerjoin(PoiSentiment, PoiSentiment.poi_id == Poi.poi_id)
            .where(func.ST_DWithin(Poi.location, point, radius_m))
            .order_by(distance)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        ids = [r.Poi.poi_id for r in rows]
        snippets = self._review_snippets(ids)
        facts, docs, vectors, status = self._facts(ids), self._docs(ids), self._embeddings(ids), self._status(ids)
        out = []
        for r in rows:
            summary, wiki_url, image, attribution = docs.get(r.Poi.poi_id, (None, None, None, None))
            out.append(CandidateRow(
                poi_id=r.Poi.poi_id, name=r.Poi.name, category=r.Poi.category,
                categories=list(r.Poi.categories or []), latitude=r.Poi.latitude, longitude=r.Poi.longitude,
                distance_m=float(r.distance_m), data_origin=r.Poi.data_origin, description=r.Poi.description,
                address=r.Poi.address, website=r.Poi.website, opening_hours=r.Poi.opening_hours,
                ratings=[RatingInfo(x["source"], float(x["rating"]), int(x["count"])) for x in (r.ratings or [])],
                photo_count=int(r.photo_count or 0), photo_url=r.photo_url, review_count=int(r.review_count or 0),
                sentiment=_sentiment_dict(r.PoiSentiment), review_snippets=snippets.get(r.Poi.poi_id, []),
                facts=facts.get(r.Poi.poi_id, []), alt_names=list(r.Poi.alt_names or []),
                wiki_summary=summary, wiki_url=wiki_url, image_url=image, image_attribution=attribution,
                embedding=vectors.get(r.Poi.poi_id), business_status=status.get(r.Poi.poi_id),
            ))
        return out

    # Batch loaders (one query each for the whole candidate set — no N+1)
    def _facts(self, ids: list[str]) -> dict[str, list[FactView]]:
        out: dict[str, list[FactView]] = {}
        if not ids:
            return out
        stmt = (select(PoiFact).where(PoiFact.poi_id.in_(ids), PoiFact.status != "rejected")
                .order_by(PoiFact.poi_id, (PoiFact.status == "verified").desc(), PoiFact.fact_id))
        for f in self.session.scalars(stmt):
            out.setdefault(f.poi_id, []).append(FactView(f.fact_id, f.fact_key, f.value, f.display_text, f.status,
                                                         f.source_type, f.source_name, f.source_url))
        return out

    def _docs(self, ids: list[str]) -> dict[str, tuple]:
        if not ids:
            return {}
        rows = self.session.execute(
            select(PoiDocument.poi_id, PoiDocument.summary, PoiDocument.url, PoiDocument.image_url,
                   PoiDocument.image_attribution).where(PoiDocument.poi_id.in_(ids))
            .order_by(PoiDocument.poi_id, PoiDocument.fetched_at.desc())).all()
        out: dict[str, tuple] = {}
        for poi_id, summary, url, image, attribution in rows:
            out.setdefault(poi_id, (summary, url, image, attribution))
        return out

    def _embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        return dict(self.session.execute(
            select(PoiEmbedding.poi_id, PoiEmbedding.vector).where(PoiEmbedding.poi_id.in_(ids))).all())

    def _status(self, ids: list[str]) -> dict[str, str | None]:
        if not ids:
            return {}
        return dict(self.session.execute(
            select(PoiSource.poi_id, PoiSource.source_metadata["business_status"].astext)
            .where(PoiSource.poi_id.in_(ids), PoiSource.provider == "google_places")).all())

    def events_between(self, start: datetime, end: datetime) -> list[EventView]:
        rows = self.session.scalars(select(Event).where(Event.start_date <= end, Event.end_date >= start)).all()
        return [EventView(e.name, e.start_date, e.end_date, e.latitude, e.longitude, e.radius_km,
                          tuple(e.poi_names or []), e.crowd_impact, e.description, e.date_note, e.source_url,
                          e.location_name) for e in rows]

    def photo_refs(self, poi_id: str) -> list[dict]:
        """Google photo resource names (+ attributions) for a POI, resolved to URLs on demand."""
        meta = self.session.scalar(select(PoiSource.source_metadata).where(
            PoiSource.poi_id == poi_id, PoiSource.provider == "google_places"))
        return list((meta or {}).get("photos") or [])

    def facts(self, poi_id: str) -> list[PoiFact]:
        return list(self.session.scalars(select(PoiFact).where(PoiFact.poi_id == poi_id)
                                         .order_by((PoiFact.status == "verified").desc(), PoiFact.fact_key)))

    def document(self, poi_id: str) -> PoiDocument | None:
        return self.session.scalars(select(PoiDocument).where(PoiDocument.poi_id == poi_id)
                                    .order_by(PoiDocument.fetched_at.desc()).limit(1)).first()

    def _review_snippets(self, poi_ids: list[str], per_poi: int = 5) -> dict[str, list[str]]:
        """Most recent review texts, ≤ ``per_poi`` per POI, in one windowed query."""
        if not poi_ids:
            return {}
        rn = func.row_number().over(partition_by=Review.poi_id,
                                    order_by=Review.review_date.desc().nulls_last()).label("rn")
        sub = (select(Review.poi_id, Review.review_text, rn)
               .where(Review.poi_id.in_(poi_ids), Review.review_text.is_not(None)).subquery())
        out: dict[str, list[str]] = {}
        for poi_id, text in self.session.execute(select(sub.c.poi_id, sub.c.review_text).where(sub.c.rn <= per_poi)):
            out.setdefault(poi_id, []).append(text)
        return out

    # --------------------------------------------------------------- browse
    def nearby(self, latitude: float, longitude: float, radius_m: float, *, category: str | None,
               limit: int, offset: int) -> tuple[list[tuple[Poi, float]], int]:
        point = geography_point(latitude, longitude)
        distance = func.ST_Distance(Poi.location, point)
        where = [func.ST_DWithin(Poi.location, point, radius_m)]
        if category:
            where.append(or_(Poi.category == category, Poi.categories.any(category)))
        total = self.session.scalar(select(func.count()).select_from(Poi).where(*where)) or 0
        rows = self.session.execute(
            select(Poi, distance.label("d")).where(*where).order_by(distance).limit(limit).offset(offset)
        ).all()
        return [(r.Poi, float(r.d)) for r in rows], total

    def list(self, *, category: str | None, q: str | None, limit: int, offset: int) -> tuple[list[Poi], int]:
        where = []
        if category:
            where.append(or_(Poi.category == category, Poi.categories.any(category)))
        if q:
            where.append(Poi.name.ilike(f"%{q.strip()}%"))  # bound parameter, not string SQL
        total = self.session.scalar(select(func.count()).select_from(Poi).where(*where)) or 0
        items = self.session.scalars(select(Poi).where(*where).order_by(Poi.name).limit(limit).offset(offset)).all()
        return list(items), total

    def get(self, poi_id: str) -> Poi | None:
        return self.session.get(Poi, poi_id)

    def detail_extras(self, poi_id: str) -> dict:
        ratings = self.session.scalars(select(PoiRating).where(PoiRating.poi_id == poi_id)).all()
        sources = self.session.scalars(select(PoiSource).where(PoiSource.poi_id == poi_id)).all()
        sentiment = self.session.get(PoiSentiment, poi_id)
        counts = self.session.execute(select(
            select(func.count()).select_from(Review).where(Review.poi_id == poi_id).scalar_subquery(),
            select(func.count()).select_from(FlickrPhoto).where(FlickrPhoto.poi_id == poi_id).scalar_subquery(),
        )).one()
        return {"ratings": ratings, "sources": sources, "sentiment": _sentiment_dict(sentiment),
                "review_count": int(counts[0]), "photo_count": int(counts[1])}

    def reviews(self, poi_id: str, *, limit: int, offset: int) -> tuple[list[Review], int]:
        total = self.session.scalar(select(func.count()).select_from(Review).where(Review.poi_id == poi_id)) or 0
        items = self.session.scalars(
            select(Review).where(Review.poi_id == poi_id)
            .order_by(Review.review_date.desc().nulls_last()).limit(limit).offset(offset)
        ).all()
        return list(items), total

    def photos(self, poi_id: str, *, limit: int, offset: int) -> tuple[list[FlickrPhoto], int]:
        total = self.session.scalar(
            select(func.count()).select_from(FlickrPhoto).where(FlickrPhoto.poi_id == poi_id)) or 0
        items = self.session.scalars(
            select(FlickrPhoto).where(FlickrPhoto.poi_id == poi_id)
            .order_by(FlickrPhoto.match_confidence.desc()).limit(limit).offset(offset)
        ).all()
        return list(items), total

    def average_rating(self, poi_id: str) -> tuple[float | None, int]:
        row = self.session.execute(
            select(
                cast(func.sum(PoiRating.rating * PoiRating.rating_count), Float)
                / func.nullif(func.sum(PoiRating.rating_count), 0),
                cast(func.coalesce(func.sum(PoiRating.rating_count), 0), Integer),
            ).where(PoiRating.poi_id == poi_id)
        ).one()
        return (round(row[0], 2) if row[0] is not None else None), int(row[1])
