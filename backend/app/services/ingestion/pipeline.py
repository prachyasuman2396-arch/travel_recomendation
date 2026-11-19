"""Ingestion pipeline:

    collect -> validate (in client parsers) -> raw store -> clean/normalise
            -> entity resolution -> internal POI assignment -> idempotent upserts

Idempotency comes from the (provider, external_id) unique keys: re-running a
source updates existing rows instead of creating duplicates.

Provider roles:
  * Google Places is the primary POI creator.
  * Yelp enriches matched POIs; unmatched Yelp businesses with a supported
    category become their own POI; ambiguous ones are only flagged.
  * Flickr photos attach to at most one POI and only with textual evidence.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.services.data_collection.base import (
    FlickrCollector,
    OsmCollector,
    OsmPlaceRecord,
    PlaceRecord,
    PlacesCollector,
    WikipediaCollector,
    YelpBusinessRecord,
    YelpCollector,
)
from app.services.data_collection.osm_client import osm_categories
from app.services.entity_resolution.matcher import (
    EntityMatcher,
    EntityView,
    MatchOutcome,
    PhotoAssociationConfig,
    associate_photo,
)
from app.services.india.facts import validate_fact_value
from app.services.ingestion.raw_store import RawStore
from app.services.ingestion.store import (
    DocumentInput,
    EventInput,
    FactInput,
    NewPoi,
    PoiStore,
    ReviewInput,
    RunLog,
    RunStats,
    SourceLink,
)
from app.services.preprocessing.categories import (
    GOOGLE_TYPE_MAP,
    YELP_ALIAS_MAP,
    categories_from_name,
    map_types,
    primary_category,
)
from app.services.preprocessing.cleaning import (
    clean_photos,
    clean_places,
    clean_reviews,
    clean_yelp_businesses,
    normalize_name,
    text_hash,
)
from app.services.preprocessing.osm_hours import osm_to_weekday_descriptions

logger = logging.getLogger(__name__)


def _place_to_poi(r: PlaceRecord) -> NewPoi | None:
    cats = map_types([*([r.primary_type] if r.primary_type else []), *r.types], GOOGLE_TYPE_MAP)
    if not cats:  # generic "tourist_attraction": fall back to whole-word name hints (lake, dam, ghat, ...)
        cats = categories_from_name(r.name)
    return NewPoi(
        name=r.name, normalized_name=normalize_name(r.name), category=primary_category(cats),
        categories=tuple(cats), latitude=r.latitude, longitude=r.longitude, primary_source=r.provider,
        data_origin=r.data_origin, description=r.description, address=r.address, website=r.website,
        opening_hours=r.opening_hours,
    )


def _day(value: str, *, end: bool = False) -> datetime:
    """'2027-07-05' -> start/end of that day in IST (festival dates are Indian calendar days)."""
    from zoneinfo import ZoneInfo

    d = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return d.replace(hour=23, minute=59, second=59) if end else d


def _yelp_categories(r: YelpBusinessRecord) -> list[str]:
    return map_types(r.categories, YELP_ALIAS_MAP)


class IngestionPipeline:
    def __init__(self, store: PoiStore, run_log: RunLog, matcher: EntityMatcher, raw_store: RawStore,
                 *, yelp_retention_hours: int = 24, google_retention_days: int = 30,
                 photo_config: PhotoAssociationConfig | None = None) -> None:
        self.store = store
        self.run_log = run_log
        self.matcher = matcher
        self.raw_store = raw_store
        self.yelp_retention = timedelta(hours=yelp_retention_hours)
        self.google_retention = timedelta(days=google_retention_days)
        self.photo_config = photo_config or PhotoAssociationConfig()

    async def _run(self, source: str, mode: str, params: dict, work) -> tuple[str, RunStats]:
        run_id = self.run_log.start(source, mode, params)
        stats = RunStats()
        try:
            await work(run_id, stats)
            self.store.commit()
        except Exception as exc:
            self.store.rollback()
            logger.exception("ingestion_failed", extra={"source": source, "run_id": run_id})
            self.run_log.finish(run_id, "failed", stats, error=f"{type(exc).__name__}: {exc}")
            raise
        status = "partial" if stats.rejected and (stats.inserted or stats.updated) else "succeeded"
        self.run_log.finish(run_id, status, stats)
        logger.info("ingestion_finished", extra={"source": source, "run_id": run_id, "status": status,
                                                 "inserted": stats.inserted, "updated": stats.updated,
                                                 "rejected": stats.rejected, "flagged": stats.flagged})
        return run_id, stats

    # ------------------------------------------------------------------ places
    async def ingest_places(self, collector: PlacesCollector, latitude: float, longitude: float,
                            radius_m: float) -> tuple[str, RunStats]:
        params = {"latitude": latitude, "longitude": longitude, "radius_m": radius_m}

        async def work(run_id: str, stats: RunStats) -> None:
            result = await collector.search_area(latitude, longitude, radius_m)
            self.raw_store.save("places", run_id, result.raw_pages, data_origin=collector.data_origin)
            stats.fetched = len(result.records) + len(result.rejected)
            stats.notes.extend(result.notes)
            for rej in result.rejected:
                stats.reject(rej.reason)
            records, rejected = clean_places(result.records)
            for rej in rejected:
                stats.reject(rej.reason)
            for record in records:
                self._ingest_place(record, stats)

        return await self._run("places", collector.data_origin, params, work)

    def _ingest_place(self, r: PlaceRecord, stats: RunStats) -> None:
        poi = _place_to_poi(r)
        metadata = {"types": list(r.types), "primary_type": r.primary_type, "maps_url": r.maps_url,
                    "business_status": r.business_status,
                    # Photo resource names only; URLs are resolved on demand and never stored long-term.
                    "photos": [{"name": ph.name, "attributions": list(ph.attributions)} for ph in r.photos]}
        existing = self.store.find_source(r.provider, r.external_id)
        if existing:
            # Re-ingestion of a record that created this POI may refresh it; matched records only fill gaps.
            if self.store.update_poi(existing, poi, overwrite=True):
                stats.updated += 1
            self._link(existing, r.provider, r.external_id, 1.0, "origin", r, metadata)
            self._rating(existing, r.provider, r.rating, r.rating_count, r.fetched_at)
            self._place_extras(existing, r, stats)
            return

        view = EntityView(r.external_id, r.name, r.latitude, r.longitude, poi.categories, r.address)
        match = self.matcher.best_match(view, self.store.find_nearby_views(
            r.latitude, r.longitude, self.matcher.config.max_distance_m))
        if match.outcome == MatchOutcome.HIGH and match.candidate_key:
            self.store.update_poi(match.candidate_key, poi, overwrite=False)
            self._link(match.candidate_key, r.provider, r.external_id, match.score, "auto", r, metadata,
                       match.breakdown)
            self._rating(match.candidate_key, r.provider, r.rating, r.rating_count, r.fetched_at)
            self._place_extras(match.candidate_key, r, stats)
            stats.updated += 1
            return

        poi_id = self.store.create_poi(poi)
        self._link(poi_id, r.provider, r.external_id, 1.0, "origin", r, metadata)
        self._rating(poi_id, r.provider, r.rating, r.rating_count, r.fetched_at)
        self._place_extras(poi_id, r, stats)
        stats.inserted += 1
        # A possible duplicate of an existing POI: kept separate, queued for review.
        if (match.outcome == MatchOutcome.MEDIUM and match.candidate_key
                and self.store.add_match_candidate(r.provider, r.external_id, match.candidate_key, match.score,
                                                   match.breakdown, {"name": r.name, "new_poi_id": poi_id})):
            stats.flagged += 1

    def _place_extras(self, poi_id: str, r: PlaceRecord, stats: RunStats) -> None:
        """Google reviews (with author attribution + retention) and reported attributes as facts."""
        expires = r.fetched_at + self.google_retention if r.data_origin == "live" else None
        for rv in r.reviews:
            text = rv.text.strip() if rv.text else None
            if not text and rv.rating is None:
                continue
            self.store.upsert_review(poi_id, ReviewInput(
                source="google_places", external_id=rv.external_id, rating=rv.rating, text=text,
                created_at=rv.published_at, data_origin=r.data_origin, fetched_at=r.fetched_at,
                author_name=rv.author_name, author_url=rv.author_url, language=rv.language,
            ), text_hash(text), expires)
        url = r.maps_url or f"https://www.google.com/maps/place/?q=place_id:{r.external_id}"
        source_type = "mock" if r.data_origin == "mock" else "google_places"
        if r.wheelchair_accessible is not None:
            self.store.upsert_fact(poi_id, FactInput(
                "wheelchair_access", {"accessible": r.wheelchair_accessible},
                "Wheelchair-accessible entrance" if r.wheelchair_accessible
                else "No wheelchair-accessible entrance", source_type, url, source_name="Google Maps"))
        if r.good_for_children is not None:
            self.store.upsert_fact(poi_id, FactInput(
                "kid_friendly", {"suitable": r.good_for_children},
                "Listed as good for children" if r.good_for_children else "Not listed as good for children",
                source_type, url, source_name="Google Maps"))

    # --------------------------------------------------------------------- osm
    async def ingest_osm(self, collector: OsmCollector, latitude: float, longitude: float,
                         radius_m: float) -> tuple[str, RunStats]:
        params = {"latitude": latitude, "longitude": longitude, "radius_m": radius_m}

        async def work(run_id: str, stats: RunStats) -> None:
            result = await collector.search_area(latitude, longitude, radius_m)
            self.raw_store.save("osm", run_id, result.raw_pages, data_origin=collector.data_origin)
            stats.fetched = len(result.records) + len(result.rejected)
            stats.notes.extend(result.notes)
            for rej in result.rejected:
                stats.reject(rej.reason)
            for record in result.records:
                self._ingest_osm_record(record, stats)

        return await self._run("osm", collector.data_origin, params, work)

    def _ingest_osm_record(self, r: OsmPlaceRecord, stats: RunStats) -> None:
        cats = osm_categories(r.tags)
        metadata = {"tags": r.tags, "wikipedia": r.wikipedia, "wikidata": r.wikidata}
        poi_id = self.store.find_source("osm", r.external_id)
        if poi_id is None:
            view = EntityView(r.external_id, r.name, r.latitude, r.longitude, tuple(cats), None)
            match = self.matcher.best_match(view, self.store.find_nearby_views(
                r.latitude, r.longitude, self.matcher.config.max_distance_m))
            # Also try local-script / alternate names (e.g. Odia) when the English name is weak.
            for alt in r.alt_names:
                if match.outcome == MatchOutcome.HIGH:
                    break
                alt_match = self.matcher.best_match(
                    EntityView(r.external_id, alt, r.latitude, r.longitude, tuple(cats), None),
                    self.store.find_nearby_views(r.latitude, r.longitude, self.matcher.config.max_distance_m))
                if alt_match.score > match.score:
                    match = alt_match
            if match.outcome == MatchOutcome.HIGH and match.candidate_key:
                poi_id = match.candidate_key
                self._link(poi_id, "osm", r.external_id, match.score, "auto", r, metadata, match.breakdown)
                stats.updated += 1
            elif match.outcome == MatchOutcome.MEDIUM and match.candidate_key:
                if self.store.add_match_candidate("osm", r.external_id, match.candidate_key, match.score,
                                                  match.breakdown, r.model_dump(mode="json")):
                    stats.flagged += 1
                return
            elif cats:
                poi_id = self.store.create_poi(NewPoi(
                    name=r.name, normalized_name=normalize_name(r.name), category=primary_category(cats),
                    categories=tuple(cats), latitude=r.latitude, longitude=r.longitude, primary_source="osm",
                    data_origin=r.data_origin, website=r.tags.get("website"),
                    opening_hours=(osm_to_weekday_descriptions(r.tags.get("opening_hours"))
                                   or ({"osm": r.tags["opening_hours"]} if r.tags.get("opening_hours") else None)),
                    alt_names=r.alt_names,
                ))
                self._link(poi_id, "osm", r.external_id, 1.0, "origin", r, metadata)
                stats.inserted += 1
            else:
                stats.reject("no supported category")
                return
        else:
            self._link(poi_id, "osm", r.external_id, 1.0, "origin", r, metadata)
        if r.alt_names:
            self.store.add_alt_names(poi_id, r.alt_names)
        self._osm_facts(poi_id, r)

    def _osm_facts(self, poi_id: str, r: OsmPlaceRecord) -> None:
        url = f"https://www.openstreetmap.org/{r.external_id}"
        source_type = "mock" if r.data_origin == "mock" else "osm"
        wheelchair = r.tags.get("wheelchair")
        if wheelchair in ("yes", "no", "limited"):
            accessible = wheelchair == "yes"
            text = {"yes": "Wheelchair accessible (OpenStreetMap)", "no": "Not wheelchair accessible (OpenStreetMap)",
                    "limited": "Limited wheelchair access (OpenStreetMap)"}[wheelchair]
            self.store.upsert_fact(poi_id, FactInput("wheelchair_access", {"accessible": accessible}, text,
                                                     source_type, url, source_name="OpenStreetMap"))

    # --------------------------------------------------------------- wikipedia
    async def ingest_wikipedia(self, collector: WikipediaCollector) -> tuple[str, RunStats]:
        async def work(run_id: str, stats: RunStats) -> None:
            for poi_id, title, qid in self.store.wiki_targets():
                stats.fetched += 1
                page = await collector.get_page(title=title, wikidata=qid)
                if page is None or not page.text.strip():
                    stats.reject("no Wikipedia article")
                    continue
                outcome = self.store.upsert_document(poi_id, DocumentInput(
                    source="mock" if page.data_origin == "mock" else "wikipedia", url=page.url, title=page.title,
                    summary=page.summary, text=page.text, text_hash=text_hash(page.text) or "",
                    data_origin=page.data_origin, fetched_at=page.fetched_at, license=page.license,
                    image_url=page.image_url, image_attribution=page.image_attribution))
                if outcome == "inserted":
                    stats.inserted += 1
                elif outcome == "updated":
                    stats.updated += 1
                if page.summary:
                    # Fill a missing description only; never overwrite one from another source.
                    self.store.update_poi(poi_id, NewPoi(
                        name="", normalized_name="", category="", categories=(), latitude=0, longitude=0,
                        primary_source="", data_origin=page.data_origin, description=page.summary),
                        overwrite=False)

        return await self._run("wikipedia", collector.data_origin, {}, work)

    # ----------------------------------------------------------------- curated
    async def load_curated(self, facts: list[dict], events: list[dict], *, data_origin: str = "live"
                           ) -> tuple[str, RunStats]:
        """Load hand-curated, sourced facts (status=verified) and the festival calendar."""

        async def work(run_id: str, stats: RunStats) -> None:
            for item in facts:
                stats.fetched += 1
                place = item["place"]
                view = EntityView("curated", place["name"], place["latitude"], place["longitude"])
                match = self.matcher.best_match(view, self.store.find_nearby_views(
                    place["latitude"], place["longitude"], self.matcher.config.max_distance_m))
                value = validate_fact_value(item["fact_key"], item["value"])
                if match.outcome != MatchOutcome.HIGH or not match.candidate_key:
                    stats.reject("curated place not found")
                    continue
                if value is None:
                    stats.reject("invalid fact value")
                    continue
                outcome = self.store.upsert_fact(match.candidate_key, FactInput(
                    item["fact_key"], value, item["display_text"], "curated", item["source_url"], status="verified",
                    source_name=item.get("source_name"), evidence=item.get("evidence"),
                    extracted_by="curated", checked_at=datetime.now(UTC)))
                stats.inserted += outcome == "inserted"
                stats.updated += outcome == "updated"
            for ev in events:
                stats.fetched += 1
                outcome = self.store.upsert_event(EventInput(
                    name=ev["name"], start_date=_day(ev["start_date"]), end_date=_day(ev["end_date"], end=True),
                    location_name=ev["location_name"], latitude=ev["latitude"], longitude=ev["longitude"],
                    radius_km=ev.get("radius_km", 5), poi_names=tuple(ev.get("poi_names", [])),
                    crowd_impact=ev["crowd_impact"], description=ev["description"], date_note=ev.get("date_note"),
                    source_url=ev["source_url"], data_origin=data_origin))
                stats.inserted += outcome == "inserted"
                stats.updated += outcome == "updated"

        return await self._run("curated", "live" if data_origin == "live" else "mock", {}, work)

    # -------------------------------------------------------------------- yelp
    async def ingest_yelp(self, collector: YelpCollector, latitude: float, longitude: float,
                          radius_m: int, fetch_reviews: bool = True) -> tuple[str, RunStats]:
        params = {"latitude": latitude, "longitude": longitude, "radius_m": radius_m, "reviews": fetch_reviews}

        async def work(run_id: str, stats: RunStats) -> None:
            result = await collector.search_businesses(latitude, longitude, radius_m)
            pages = list(result.raw_pages)
            stats.fetched = len(result.records) + len(result.rejected)
            stats.notes.extend(result.notes)
            for rej in result.rejected:
                stats.reject(rej.reason)
            records, rejected = clean_yelp_businesses(result.records)
            for rej in rejected:
                stats.reject(rej.reason)
            for record in records:
                poi_id = self._ingest_yelp_business(record, stats)
                if poi_id and fetch_reviews:
                    reviews = await collector.get_reviews(record.external_id)
                    pages.extend(reviews.raw_pages)
                    stats.notes.extend(n for n in reviews.notes if n not in stats.notes)
                    clean, bad = clean_reviews(reviews.records)
                    for rej in bad:
                        stats.reject(rej.reason)
                    for review in clean:
                        expires = review.fetched_at + self.yelp_retention if review.data_origin == "live" else None
                        self.store.upsert_review(poi_id, ReviewInput(
                            source="yelp", external_id=review.external_id, rating=review.rating, text=review.text,
                            created_at=review.created_at, data_origin=review.data_origin,
                            fetched_at=review.fetched_at), text_hash(review.text), expires)
            self.raw_store.save("yelp", run_id, pages, data_origin=collector.data_origin)

        return await self._run("yelp", collector.data_origin, params, work)

    def _ingest_yelp_business(self, r: YelpBusinessRecord, stats: RunStats) -> str | None:
        cats = _yelp_categories(r)
        expires = r.fetched_at + self.yelp_retention if r.data_origin == "live" else None
        metadata = {"categories": list(r.categories), "url": r.url}
        poi_id = self.store.find_source(r.provider, r.external_id)
        if poi_id is None:
            view = EntityView(r.external_id, r.name, r.latitude, r.longitude, tuple(cats), r.address)
            match = self.matcher.best_match(view, self.store.find_nearby_views(
                r.latitude, r.longitude, self.matcher.config.max_distance_m))
            if match.outcome == MatchOutcome.HIGH and match.candidate_key:
                poi_id = match.candidate_key
                self._link(poi_id, r.provider, r.external_id, match.score, "auto", r, metadata, match.breakdown)
            elif match.outcome == MatchOutcome.MEDIUM and match.candidate_key:
                if self.store.add_match_candidate(r.provider, r.external_id, match.candidate_key, match.score,
                                                  match.breakdown, r.model_dump(mode="json")):
                    stats.flagged += 1
                return None  # not stored until a reviewer accepts the match
            elif cats:
                poi_id = self.store.create_poi(NewPoi(
                    name=r.name, normalized_name=normalize_name(r.name), category=primary_category(cats),
                    categories=tuple(cats), latitude=r.latitude, longitude=r.longitude, primary_source="yelp",
                    data_origin=r.data_origin, address=r.address,
                ))
                self._link(poi_id, r.provider, r.external_id, 1.0, "origin", r, metadata)
                stats.inserted += 1
            else:
                stats.reject("no supported category")
                return None
        else:
            self._link(poi_id, r.provider, r.external_id, 1.0, "origin", r, metadata)
        if self.store.upsert_yelp_business(poi_id, r, expires) == "updated":
            stats.updated += 1
        self._rating(poi_id, "yelp", r.rating, r.review_count, r.fetched_at)
        return poi_id

    # ------------------------------------------------------------------ flickr
    async def ingest_flickr(self, collector: FlickrCollector, latitude: float, longitude: float,
                            radius_km: float, max_pages: int = 2) -> tuple[str, RunStats]:
        params = {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}

        async def work(run_id: str, stats: RunStats) -> None:
            result = await collector.search_photos(latitude, longitude, radius_km, max_pages)
            self.raw_store.save("flickr", run_id, result.raw_pages, data_origin=collector.data_origin)
            stats.fetched = len(result.records) + len(result.rejected)
            for rej in result.rejected:
                stats.reject(rej.reason)
            photos, rejected = clean_photos(result.records)
            for rej in rejected:
                stats.reject(rej.reason)
            pois = self.store.find_nearby_views(latitude, longitude,
                                                radius_km * 1000 + self.photo_config.max_distance_m)
            for photo in photos:
                poi_id, confidence, method = associate_photo(photo.title, photo.tags, photo.latitude,
                                                             photo.longitude, pois, self.photo_config)
                if poi_id is None:
                    stats.reject("no confident POI association")
                    continue
                outcome = self.store.upsert_photo(poi_id, photo, confidence, method)
                if outcome == "inserted":
                    stats.inserted += 1
                elif outcome == "updated":
                    stats.updated += 1

        return await self._run("flickr", collector.data_origin, params, work)

    # ----------------------------------------------------------------- helpers
    def _link(self, poi_id: str, provider: str, external_id: str, confidence: float, method: str,
              record, metadata: dict, breakdown: dict | None = None) -> None:
        self.store.upsert_source(SourceLink(
            poi_id=poi_id, provider=provider, external_id=external_id, confidence=round(confidence, 4),
            method=method, data_origin=record.data_origin, fetched_at=record.fetched_at,
            breakdown=breakdown, metadata=metadata,
        ))

    def _rating(self, poi_id: str, source: str, rating: float | None, count: int | None,
                fetched_at: datetime | None) -> None:
        if rating is not None:
            self.store.upsert_rating(poi_id, source, rating, count or 0, fetched_at or datetime.now(UTC))
