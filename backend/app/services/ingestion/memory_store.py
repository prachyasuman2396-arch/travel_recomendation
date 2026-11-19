"""In-memory implementations of the ingestion storage protocols.

Used by unit tests and by the offline evaluation harness (app/eval), so the whole pipeline —
ingestion, sentiment, fact extraction and recommendation — can run without PostgreSQL.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import asdict
from datetime import datetime

from app.services.data_collection.base import FlickrPhotoRecord, YelpBusinessRecord
from app.services.entity_resolution.geo import haversine_m
from app.services.entity_resolution.matcher import EntityView
from app.services.ingestion.store import NewPoi, RunStats, SourceLink, UpsertResult


class MemoryPoiStore:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.pois: dict[str, dict] = {}
        self.sources: dict[tuple[str, str], SourceLink] = {}
        self.ratings: dict[tuple[str, str], tuple[float, int]] = {}
        self.candidates: dict[tuple[str, str, str], dict] = {}
        self.yelp: dict[str, dict] = {}
        self.reviews: dict[tuple[str, str], dict] = {}
        self.photos: dict[str, dict] = {}
        self.facts: dict[tuple, dict] = {}
        self.documents: dict[tuple, dict] = {}
        self.events: dict[tuple, object] = {}
        self.embeddings: dict[str, list[float]] = {}  # poi_id -> vector (filled by the lite runtime)
        self.commits = 0
        self.rollbacks = 0

    def find_source(self, provider: str, external_id: str) -> str | None:
        link = self.sources.get((provider, external_id))
        return link.poi_id if link else None

    def find_nearby_views(self, latitude: float, longitude: float, radius_m: float) -> list[EntityView]:
        return [EntityView(pid, p["name"], p["latitude"], p["longitude"], tuple(p["categories"]), p["address"])
                for pid, p in self.pois.items()
                if haversine_m(latitude, longitude, p["latitude"], p["longitude"]) <= radius_m]

    def create_poi(self, poi: NewPoi) -> str:
        pid = f"POI{next(self._ids):06d}"
        self.pois[pid] = asdict(poi) | {"categories": list(poi.categories), "alt_names": list(poi.alt_names)}
        return pid

    def update_poi(self, poi_id: str, poi: NewPoi, *, overwrite: bool) -> bool:
        current = self.pois[poi_id]
        before = dict(current)
        if overwrite:
            current.update({k: v for k, v in asdict(poi).items() if v is not None and k != "alt_names"})
            current["categories"] = list(poi.categories) or current["categories"]
        else:
            for k in ("description", "address", "website", "opening_hours"):
                if current.get(k) is None and getattr(poi, k) is not None:
                    current[k] = getattr(poi, k)
            current["categories"] = list(dict.fromkeys([*current["categories"], *poi.categories]))
        return current != before

    def upsert_source(self, link: SourceLink) -> None:
        existing = self.sources.get((link.provider, link.external_id))
        self.sources[(link.provider, link.external_id)] = existing or link

    def upsert_rating(self, poi_id: str, source: str, rating: float, count: int, fetched_at: datetime) -> None:
        self.ratings[(poi_id, source)] = (rating, count)

    def add_match_candidate(self, provider, external_id, poi_id, score, breakdown, payload) -> bool:
        key = (provider, external_id, poi_id)
        if key in self.candidates:
            return False
        self.candidates[key] = {"score": score, "breakdown": breakdown, "payload": payload}
        return True

    def upsert_yelp_business(self, poi_id: str, record: YelpBusinessRecord, expires_at) -> UpsertResult:
        new = {"poi_id": poi_id, "rating": record.rating, "review_count": record.review_count}
        old = self.yelp.get(record.external_id)
        self.yelp[record.external_id] = new
        return "inserted" if old is None else ("updated" if old != new else "unchanged")

    def upsert_review(self, poi_id: str, review, text_hash, expires_at) -> UpsertResult:
        key = (review.source, review.external_id)
        new = {"poi_id": poi_id, "rating": review.rating, "text": review.text, "hash": text_hash,
               "origin": review.data_origin, "expires_at": expires_at, "author": review.author_name,
               "date": review.created_at}
        old = self.reviews.get(key)
        if old and old["hash"] == text_hash and old["rating"] == review.rating:
            return "unchanged"
        self.reviews[key] = new
        return "inserted" if old is None else "updated"

    def upsert_fact(self, poi_id: str, fact) -> UpsertResult:
        key = (poi_id, fact.fact_key, fact.source_url)
        old = self.facts.get(key)
        status = old["status"] if old and old["status"] in ("verified", "rejected") else fact.status
        new = {"poi_id": poi_id, "key": fact.fact_key, "value": fact.value, "display_text": fact.display_text,
               "source_type": fact.source_type, "source_url": fact.source_url, "source_name": fact.source_name,
               "status": status, "evidence": fact.evidence, "extracted_by": fact.extracted_by,
               "fact_id": old["fact_id"] if old else len(self.facts) + 1}
        if old == new:
            return "unchanged"
        self.facts[key] = new
        return "inserted" if old is None else "updated"

    def upsert_document(self, poi_id: str, doc) -> UpsertResult:
        key = (poi_id, doc.source, doc.url)
        old = self.documents.get(key)
        if old and old["hash"] == doc.text_hash:
            return "unchanged"
        self.documents[key] = {"poi_id": poi_id, "text": doc.text, "hash": doc.text_hash, "summary": doc.summary,
                               "title": doc.title, "url": doc.url, "origin": doc.data_origin,
                               "image_url": doc.image_url, "image_attribution": doc.image_attribution,
                               "license": doc.license}
        return "inserted" if old is None else "updated"

    def add_alt_names(self, poi_id: str, names) -> bool:
        poi = self.pois[poi_id]
        merged = [n for n in dict.fromkeys([*poi.get("alt_names", []), *names]) if n != poi["name"]]
        changed = merged != poi.get("alt_names", [])
        poi["alt_names"] = merged
        return changed

    def wiki_targets(self):
        from app.services.data_collection.wikipedia_client import parse_wikipedia_tag
        out = {}
        for (provider, _), link in self.sources.items():
            meta = link.metadata or {}
            if provider != "osm" or not (meta.get("wikipedia") or meta.get("wikidata")):
                continue
            parsed = parse_wikipedia_tag(meta.get("wikipedia"))
            title = parsed[1] if parsed and parsed[0] == "en" else None
            out.setdefault(link.poi_id, (link.poi_id, title, meta.get("wikidata")))
        return list(out.values())

    def upsert_event(self, event) -> UpsertResult:
        key = (event.name, event.start_date)
        old = self.events.get(key)
        self.events[key] = event
        return "inserted" if old is None else ("unchanged" if old == event else "updated")

    def upsert_photo(self, poi_id: str, record: FlickrPhotoRecord, confidence: float, method: str) -> UpsertResult:
        new = {"poi_id": poi_id, "confidence": confidence, "method": method, "tags": record.tags}
        old = self.photos.get(record.external_id)
        self.photos[record.external_id] = new
        return "inserted" if old is None else ("updated" if old != new else "unchanged")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class MemoryRunLog:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}

    def start(self, source: str, mode: str, params: dict) -> str:
        run_id = str(uuid.uuid4())
        self.runs[run_id] = {"source": source, "mode": mode, "status": "running", "params": params}
        return run_id

    def finish(self, run_id: str, status: str, stats: RunStats, error: str | None = None) -> None:
        self.runs[run_id] |= {"status": status, "stats": stats, "error": error}


class MemoryCandidateSource:
    """CandidateSource over a MemoryPoiStore (haversine instead of PostGIS). For offline eval/demos."""

    def __init__(self, store: MemoryPoiStore, sentiment=None) -> None:
        self.store = store
        self.sentiment = sentiment  # optional SentimentService to summarise stored reviews

    def find_candidates(self, latitude: float, longitude: float, radius_m: float, limit: int):
        from app.services.india.facts import FactView
        from app.services.recommendation.types import CandidateRow, RatingInfo

        rows = []
        for pid, p in self.store.pois.items():
            d = haversine_m(latitude, longitude, p["latitude"], p["longitude"])
            if d > radius_m:
                continue
            ratings = [RatingInfo(src, r, c) for (poi, src), (r, c) in self.store.ratings.items() if poi == pid]
            reviews = [r for r in self.store.reviews.values() if r["poi_id"] == pid]
            facts = [FactView(f["fact_id"], f["key"], f["value"], f["display_text"], f["status"], f["source_type"],
                              f["source_name"], f["source_url"])
                     for f in self.store.facts.values() if f["poi_id"] == pid and f["status"] != "rejected"]
            facts.sort(key=lambda f: (not f.verified, f.key))
            doc = next((x for x in self.store.documents.values() if x["poi_id"] == pid), None)
            photos = [ph for ph in self.store.photos.values() if ph["poi_id"] == pid]
            rows.append(CandidateRow(
                poi_id=pid, name=p["name"], category=p["category"], categories=list(p["categories"]),
                latitude=p["latitude"], longitude=p["longitude"], distance_m=d, data_origin=p["data_origin"],
                description=p.get("description"), address=p.get("address"), website=p.get("website"),
                opening_hours=p.get("opening_hours"), ratings=ratings, photo_count=len(photos),
                review_count=len(reviews), sentiment=self._sentiment(reviews),
                review_snippets=[r["text"] for r in reviews if r.get("text")][:2], facts=facts,
                alt_names=list(p.get("alt_names", [])), wiki_summary=doc["summary"] if doc else None,
                wiki_url=doc["url"] if doc else None, image_url=doc.get("image_url") if doc else None,
                image_attribution=doc.get("image_attribution") if doc else None,
                embedding=self.store.embeddings.get(pid),
            ))
        rows.sort(key=lambda r: r.distance_m)
        return rows[:limit]

    def _sentiment(self, reviews: list[dict]) -> dict | None:
        if self.sentiment is None or not reviews:
            return None
        results = self.sentiment.analyze([(i, r["text"], r["hash"] or "") for i, r in enumerate(reviews)
                                          if r.get("text")])
        summary = self.sentiment.aggregate([(r.sentiment, r.score, r.aspects) for r in results])
        return {"status": summary.status, "reviews_analyzed": summary.reviews_analyzed, "overall": summary.overall,
                "positive_share": summary.positive_share, "neutral_share": summary.neutral_share,
                "negative_share": summary.negative_share, "aspects": summary.aspects}

    def events_between(self, start, end):
        from app.services.india.rules import EventView

        return [EventView(e.name, e.start_date, e.end_date, e.latitude, e.longitude, e.radius_km, e.poi_names,
                          e.crowd_impact, e.description, e.date_note, e.source_url, e.location_name)
                for e in self.store.events.values() if e.start_date <= end and e.end_date >= start]


class MemoryFactSink:
    """FactSink for the fact-extraction agent over a MemoryPoiStore."""

    def __init__(self, store: MemoryPoiStore) -> None:
        self.store = store
        self.extracted: dict[tuple, str] = {}

    def documents_to_extract(self, limit: int):
        from app.services.agents.fact_extraction import DocumentToProcess

        out = []
        for key, d in self.store.documents.items():
            if self.extracted.get(key) != d["hash"]:
                out.append(DocumentToProcess(key, d["poi_id"], self.store.pois[d["poi_id"]]["name"], d["url"],
                                             "mock" if d["origin"] == "mock" else "wikipedia", d["text"], d["hash"]))
        return out[:limit]

    def save_extracted_fact(self, doc, fact, extractor: str) -> None:
        from app.services.ingestion.store import FactInput

        self.store.upsert_fact(doc.poi_id, FactInput(
            fact.fact_key, fact.value, fact.display_text, "mock" if doc.source == "mock" else "llm_extracted",
            doc.url, status="unverified", source_name="Wikipedia", evidence=fact.evidence, confidence=0.7,
            extracted_by=extractor))

    def mark_extracted(self, doc) -> None:
        self.extracted[doc.document_id] = doc.text_hash
