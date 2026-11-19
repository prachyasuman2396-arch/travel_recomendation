"""PostgreSQL implementations of the agent interfaces (fact sink, match queue, embeddings, freshness)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    FlickrPhoto,
    MatchCandidate,
    Poi,
    PoiDocument,
    PoiEmbedding,
    PoiFact,
    PoiRating,
    PoiSentiment,
    PoiSource,
    Review,
    YelpBusiness,
)
from app.repositories.admin_repository import AdminRepository
from app.services.agents.fact_extraction import DocumentToProcess, ProposedFact
from app.services.agents.maintenance import EmbeddingTarget
from app.services.agents.match_judge import Judgement, MatchCase
from app.services.data_collection.osm_client import osm_categories
from app.services.preprocessing.categories import YELP_ALIAS_MAP, map_types
from app.services.semantic.search import build_poi_document


class AgentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------ fact sink
    def documents_to_extract(self, limit: int) -> list[DocumentToProcess]:
        rows = self.session.execute(
            select(PoiDocument, Poi.name).join(Poi, Poi.poi_id == PoiDocument.poi_id)
            .where(PoiDocument.extracted_hash.is_distinct_from(PoiDocument.text_hash)).limit(limit)
        ).all()
        return [DocumentToProcess(d.document_id, d.poi_id, name, d.url, d.source, d.text, d.text_hash)
                for d, name in rows]

    def save_extracted_fact(self, doc: DocumentToProcess, fact: ProposedFact, extractor: str) -> None:
        stmt = insert(PoiFact).values(
            poi_id=doc.poi_id, fact_key=fact.fact_key, value=fact.value, display_text=fact.display_text,
            source_type="mock" if doc.source == "mock" else "llm_extracted", source_url=doc.url,
            source_name="Wikipedia" if doc.source == "wikipedia" else doc.source, evidence=fact.evidence,
            confidence=0.7, status="unverified", extracted_by=extractor, checked_at=datetime.now(UTC),
        )
        self.session.execute(stmt.on_conflict_do_update(
            index_elements=["poi_id", "fact_key", "source_url"],
            set_={"value": stmt.excluded.value, "display_text": stmt.excluded.display_text,
                  "evidence": stmt.excluded.evidence, "extracted_by": stmt.excluded.extracted_by,
                  "checked_at": stmt.excluded.checked_at},
            where=PoiFact.status == "unverified",  # never touch verified or rejected facts
        ))

    def mark_extracted(self, doc: DocumentToProcess) -> None:
        self.session.execute(update(PoiDocument).where(PoiDocument.document_id == doc.document_id)
                             .values(extracted_hash=doc.text_hash))

    # ---------------------------------------------------------- match queue
    def pending_cases(self, limit: int) -> list[MatchCase]:
        rows = self.session.execute(
            select(MatchCandidate, Poi).join(Poi, Poi.poi_id == MatchCandidate.candidate_poi_id)
            .where(MatchCandidate.status == "pending")
            .where(~MatchCandidate.score_breakdown.has_key("judge"))
            .order_by(MatchCandidate.score.desc()).limit(limit)
        ).all()
        cases = []
        for mc, poi in rows:
            p = mc.source_payload or {}
            if mc.provider == "yelp":
                cats, lat, lon = map_types(p.get("categories") or [], YELP_ALIAS_MAP), p.get("latitude"), p.get(
                    "longitude")
            elif mc.provider == "osm":
                cats, lat, lon = osm_categories(p.get("tags") or {}), p.get("latitude"), p.get("longitude")
            else:  # a new google POI that may duplicate an existing one
                dup = self.session.get(Poi, p.get("new_poi_id")) if p.get("new_poi_id") else None
                cats = list(dup.categories) if dup else []
                lat, lon = (dup.latitude, dup.longitude) if dup else (None, None)
            if lat is None or lon is None:
                continue
            cases.append(MatchCase(mc.id, mc.provider, p.get("name") or mc.external_id, tuple(cats), lat, lon,
                                   poi.poi_id, poi.name, tuple(poi.alt_names or []), tuple(poi.categories or []),
                                   poi.latitude, poi.longitude, mc.score))
        return cases

    def apply_verdict(self, case: MatchCase, judgement: Judgement, judge: str, *, decided: bool) -> None:
        mc = self.session.get(MatchCandidate, case.candidate_id)
        breakdown = dict(mc.score_breakdown or {}) | {"judge": {**judgement.model_dump(), "by": judge,
                                                                "at": datetime.now(UTC).isoformat()}}
        status = mc.status
        if decided and judgement.verdict == "same":
            self._accept(mc, judgement)
            status = "accepted"
        elif decided and judgement.verdict == "different":
            status = "rejected"  # the record stays unlinked; a later run may create it as its own POI
        self.session.execute(update(MatchCandidate).where(MatchCandidate.id == mc.id)
                             .values(status=status, score_breakdown=breakdown))

    def _accept(self, mc: MatchCandidate, judgement: Judgement) -> None:
        p = mc.source_payload or {}
        now = datetime.now(UTC)
        if mc.provider == "google_places" and p.get("new_poi_id"):
            self.merge_pois(keep=mc.candidate_poi_id, drop=p["new_poi_id"])
            return
        self.session.execute(insert(PoiSource).values(
            poi_id=mc.candidate_poi_id, provider=mc.provider, external_id=mc.external_id,
            matching_confidence=min(1.0, judgement.confidence), matching_method="judge",
            score_breakdown=mc.score_breakdown, source_metadata={k: p.get(k) for k in ("tags", "categories", "url")},
            data_origin=p.get("data_origin", "live"), fetched_at=now,
        ).on_conflict_do_nothing())
        if mc.provider == "yelp" and p.get("rating") is not None:
            self.session.execute(insert(YelpBusiness).values(
                poi_id=mc.candidate_poi_id, external_business_id=mc.external_id, name=p.get("name", ""),
                rating=p.get("rating"), review_count=p.get("review_count"), categories=p.get("categories") or [],
                url=p.get("url"), data_origin=p.get("data_origin", "live"), fetched_at=now,
            ).on_conflict_do_nothing())
            stmt = insert(PoiRating).values(poi_id=mc.candidate_poi_id, source="yelp", rating=p["rating"],
                                            rating_count=p.get("review_count") or 0, fetched_at=now)
            self.session.execute(stmt.on_conflict_do_nothing())

    def merge_pois(self, keep: str, drop: str) -> None:
        """Move everything from ``drop`` onto ``keep`` and delete ``drop`` (one transaction)."""
        if keep == drop:
            return
        # Ratings are unique per (poi, source): keep the surviving POI's rating where both exist.
        keep_sources = select(PoiRating.source).where(PoiRating.poi_id == keep)
        self.session.execute(delete(PoiRating).where(PoiRating.poi_id == drop, PoiRating.source.in_(keep_sources)))
        keep_fact_keys = select(PoiFact.fact_key + PoiFact.source_url).where(PoiFact.poi_id == keep)
        self.session.execute(delete(PoiFact).where(PoiFact.poi_id == drop,
                                                   (PoiFact.fact_key + PoiFact.source_url).in_(keep_fact_keys)))
        for model in (PoiSource, PoiRating, Review, FlickrPhoto, YelpBusiness, PoiFact, PoiDocument):
            self.session.execute(update(model).where(model.poi_id == drop).values(poi_id=keep))
        self.session.execute(update(MatchCandidate).where(MatchCandidate.candidate_poi_id == drop)
                             .values(candidate_poi_id=keep))
        for model in (PoiEmbedding, PoiSentiment):
            self.session.execute(delete(model).where(model.poi_id.in_([keep, drop])))  # recomputed later
        dropped = self.session.get(Poi, drop)
        kept = self.session.get(Poi, keep)
        if dropped and kept:
            names = list(dict.fromkeys([*(kept.alt_names or []), dropped.name, *(dropped.alt_names or [])]))
            kept.alt_names = [n for n in names if n != kept.name]
        self.session.execute(delete(Poi).where(Poi.poi_id == drop))

    # ----------------------------------------------------------- embeddings
    def embedding_targets(self, model: str) -> list[EmbeddingTarget]:
        pois = self.session.scalars(select(Poi)).all()
        docs = dict(self.session.execute(select(PoiDocument.poi_id, PoiDocument.summary)).all())
        facts: dict[str, list[str]] = {}
        for poi_id, text in self.session.execute(select(PoiFact.poi_id, PoiFact.display_text)
                                                 .where(PoiFact.status != "rejected")):
            facts.setdefault(poi_id, []).append(text)
        aspects = dict(self.session.execute(select(PoiSentiment.poi_id, PoiSentiment.aspects)).all())
        current = dict(self.session.execute(select(PoiEmbedding.poi_id, PoiEmbedding.document_hash)
                                            .where(PoiEmbedding.model == model)).all())
        return [EmbeddingTarget(p.poi_id, build_poi_document(
            p.name, list(p.categories or []), p.description, facts.get(p.poi_id, []), docs.get(p.poi_id),
            aspects.get(p.poi_id), [], list(p.alt_names or [])), current.get(p.poi_id)) for p in pois]

    def save_embedding(self, poi_id: str, model: str, vector: list[float], doc_hash: str) -> None:
        stmt = insert(PoiEmbedding).values(poi_id=poi_id, model=model, vector=vector, document_hash=doc_hash)
        self.session.execute(stmt.on_conflict_do_update(
            index_elements=["poi_id"], set_={"model": stmt.excluded.model, "vector": stmt.excluded.vector,
                                             "document_hash": stmt.excluded.document_hash,
                                             "updated_at": func.now()}))

    # ------------------------------------------------------------ freshness
    def stale_google_places(self, older_than_days: int, limit: int) -> list[tuple[str, str]]:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        rows = self.session.execute(
            select(PoiSource.poi_id, PoiSource.external_id).where(
                PoiSource.provider == "google_places", PoiSource.data_origin == "live",
                PoiSource.fetched_at < cutoff).limit(limit)).all()
        return [(r[0], r[1]) for r in rows]

    def update_google_status(self, poi_id: str, place_id: str, status: str | None,
                             opening_hours: dict | None) -> None:
        src = self.session.scalar(select(PoiSource).where(PoiSource.provider == "google_places",
                                                          PoiSource.external_id == place_id))
        if src is None:
            return
        src.source_metadata = dict(src.source_metadata or {}) | {"business_status": status}
        src.fetched_at = datetime.now(UTC)
        if opening_hours:
            self.session.execute(update(Poi).where(Poi.poi_id == poi_id).values(opening_hours=opening_hours))

    def purge_expired(self) -> dict:
        return AdminRepository(self.session).purge_expired()
