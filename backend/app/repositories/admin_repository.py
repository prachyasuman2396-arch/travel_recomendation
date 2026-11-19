"""Aggregates for the admin dashboard, users, and retention purge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    FlickrPhoto,
    IngestionRun,
    MatchCandidate,
    Poi,
    PoiSentiment,
    PoiSource,
    Review,
    User,
    YelpBusiness,
)


class AdminRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def dashboard(self) -> dict:
        s = self.session
        count = lambda model: s.scalar(select(func.count()).select_from(model)) or 0  # noqa: E731
        by_category = dict(s.execute(select(Poi.category, func.count()).group_by(Poi.category)).all())
        by_origin = dict(s.execute(select(Poi.data_origin, func.count()).group_by(Poi.data_origin)).all())
        coverage = dict(s.execute(
            select(PoiSource.provider, func.count(func.distinct(PoiSource.poi_id))).group_by(PoiSource.provider)
        ).all())
        methods = dict(s.execute(
            select(PoiSource.matching_method, func.count()).group_by(PoiSource.matching_method)).all())
        conf = s.execute(select(
            func.count().filter(PoiSource.matching_confidence >= 0.8),
            func.count().filter(PoiSource.matching_confidence.between(0.6, 0.7999)),
            func.count().filter(PoiSource.matching_confidence < 0.6),
        ).where(PoiSource.matching_method == "auto")).one()
        pending = s.scalar(select(func.count()).select_from(MatchCandidate)
                           .where(MatchCandidate.status == "pending")) or 0
        quality = s.execute(select(
            func.count().filter(Poi.description.is_(None)),
            func.count().filter(Poi.opening_hours.is_(None)),
            func.count().filter(Poi.website.is_(None)),
            func.count().filter(Poi.address.is_(None)),
        )).one()
        sentiment = dict(s.execute(select(PoiSentiment.status, func.count()).group_by(PoiSentiment.status)).all())
        runs = s.execute(select(
            IngestionRun.source, func.count(),
            func.sum(case((IngestionRun.status == "failed", 1), else_=0)),
            func.max(IngestionRun.started_at),
        ).group_by(IngestionRun.source)).all()
        return {
            "totals": {"pois": count(Poi), "source_records": count(PoiSource), "reviews": count(Review),
                       "flickr_photos": count(FlickrPhoto), "yelp_businesses": count(YelpBusiness),
                       "users": count(User), "pending_match_reviews": pending},
            "pois_by_category": by_category,
            "pois_by_origin": by_origin,
            "source_coverage": coverage,
            "matching": {"by_method": methods,
                         "auto_confidence_buckets": {"high": conf[0], "medium": conf[1], "low": conf[2]}},
            "data_quality": {"missing_description": quality[0], "missing_opening_hours": quality[1],
                             "missing_website": quality[2], "missing_address": quality[3]},
            "sentiment_status": sentiment,
            "ingestion_by_source": [{"source": r[0], "runs": r[1], "failed": int(r[2] or 0),
                                     "last_started_at": r[3]} for r in runs],
        }

    def runs(self, *, limit: int, offset: int, source: str | None) -> tuple[list[IngestionRun], int]:
        where = [IngestionRun.source == source] if source else []
        total = self.session.scalar(select(func.count()).select_from(IngestionRun).where(*where)) or 0
        items = self.session.scalars(select(IngestionRun).where(*where).order_by(IngestionRun.started_at.desc())
                                     .limit(limit).offset(offset)).all()
        return list(items), total

    def get_run(self, run_id: str) -> IngestionRun | None:
        return self.session.get(IngestionRun, run_id)

    def match_candidates(self, *, status: str, limit: int) -> list[MatchCandidate]:
        return list(self.session.scalars(
            select(MatchCandidate).where(MatchCandidate.status == status)
            .order_by(MatchCandidate.score.desc()).limit(limit)).all())

    def purge_expired(self, now: datetime | None = None) -> dict:
        """Delete provider content past its retention window (e.g. Yelp, 24 h by default)."""
        now = now or datetime.now(UTC)
        reviews = self.session.execute(delete(Review).where(Review.expires_at < now)).rowcount
        businesses = self.session.execute(delete(YelpBusiness).where(YelpBusiness.expires_at < now)).rowcount
        return {"reviews_deleted": reviews or 0, "yelp_businesses_deleted": businesses or 0}


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, name: str, email: str | None, preferences: dict) -> User:
        user = User(user_id=str(uuid.uuid4()), name=name, email=email, preferences=preferences)
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def email_taken(self, email: str) -> bool:
        return self.session.scalar(select(func.count()).select_from(User).where(User.email == email)) > 0

    def update_preferences(self, user: User, preferences: dict) -> User:
        user.preferences = preferences
        self.session.flush()
        return user
