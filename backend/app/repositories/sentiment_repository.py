"""Persistence for review analysis: incremental (only new/changed reviews) + POI aggregation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PoiSentiment, Review, ReviewAnalysis
from app.services.sentiment.service import SentimentService


class SentimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def pending_reviews(self, model_name: str, limit: int = 1000) -> list[tuple[int, str, str, str]]:
        """Reviews with text that have no analysis for this model, or whose text changed since."""
        stmt = (
            select(Review.review_id, Review.review_text, Review.text_hash, Review.poi_id)
            .outerjoin(ReviewAnalysis, and_(ReviewAnalysis.review_id == Review.review_id,
                                            ReviewAnalysis.model_name == model_name))
            .where(Review.review_text.is_not(None), Review.text_hash.is_not(None))
            .where((ReviewAnalysis.id.is_(None)) | (ReviewAnalysis.text_hash != Review.text_hash))
            .limit(limit)
        )
        return [(r.review_id, r.review_text, r.text_hash, r.poi_id) for r in self.session.execute(stmt)]

    def run(self, service: SentimentService, batch: int = 500) -> dict:
        model = service.model_name
        analysed, touched = 0, set()
        while pending := self.pending_reviews(model, batch):
            results = service.analyze([(rid, text, h) for rid, text, h, _ in pending])
            for res in results:
                stmt = insert(ReviewAnalysis).values(
                    review_id=res.review_id, text_hash=res.text_hash, sentiment=res.sentiment, score=res.score,
                    aspects=res.aspects, model_name=model, model_version=service.analyzer.version,
                )
                self.session.execute(stmt.on_conflict_do_update(
                    index_elements=["review_id", "model_name"],
                    set_={k: stmt.excluded[k] for k in ("text_hash", "sentiment", "score", "aspects", "model_version")}
                    | {"analyzed_at": datetime.now(UTC)},
                ))
            analysed += len(results)
            touched |= {poi for *_, poi in pending}
            if len(pending) < batch:
                break
        for poi_id in touched:
            self._aggregate(poi_id, service)
        return {"reviews_analyzed": analysed, "pois_updated": len(touched), "model": model}

    def _aggregate(self, poi_id: str, service: SentimentService) -> None:
        rows = self.session.execute(
            select(ReviewAnalysis.sentiment, ReviewAnalysis.score, ReviewAnalysis.aspects)
            .join(Review, Review.review_id == ReviewAnalysis.review_id)
            .where(Review.poi_id == poi_id, ReviewAnalysis.model_name == service.model_name)
        ).all()
        summary = service.aggregate([(r.sentiment, r.score, r.aspects or {}) for r in rows])
        values = dict(status=summary.status, reviews_analyzed=summary.reviews_analyzed, overall=summary.overall,
                      positive_share=summary.positive_share, neutral_share=summary.neutral_share,
                      negative_share=summary.negative_share, aspects=summary.aspects, model_name=service.model_name,
                      updated_at=datetime.now(UTC))
        stmt = insert(PoiSentiment).values(poi_id=poi_id, **values)
        self.session.execute(stmt.on_conflict_do_update(index_elements=["poi_id"], set_=values))
