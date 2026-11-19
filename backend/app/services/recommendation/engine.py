"""Multi-stage, India-aware recommendation engine.

A  validate the PreferenceProfile; understand the free text (English/Hinglish) -> visitor flags & preferences
B  PostGIS retrieval (ST_DWithin + ST_Distance)  }
   weather + air quality (cached)                } concurrently
   festival calendar for the visit window        }
C  hard constraints in code: radius, closed today/closed by Google, verified entry rules, fee limits,
   accessibility; then deterministic features (rating, sentiment, Indian season, AQI, crowds, semantic match)
D  pre-rank and build a token-budgeted payload with sourced facts (cite-able ids) and system notices
E  LLM ranks + explains (primary -> retries -> fallback provider)
F  validate (ids, duplicates, distance, numeric/price grounding, citations), then attach by code:
   facts and notices (never droppable by the model), and a timed half-day itinerary
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import anyio

from app.core.errors import RecommendationUnavailableError
from app.schemas.preference import PreferenceProfile
from app.schemas.recommendation import (
    AirQualityOut,
    CandidateMarker,
    EventOut,
    FactOut,
    ItineraryOut,
    ItineraryStopOut,
    NoticeOut,
    RecommendationFacts,
    RecommendationItem,
    RecommendationResult,
    WeatherSummary,
)
from app.services.feature_engineering.features import CandidateFeatures, compute_features, events_for
from app.services.india.facts import DEFAULT_VISIT_MINUTES, facts_by_key
from app.services.india.rules import EventView, VisitorProfile
from app.services.india.seasons import SEASON_LABELS, in_india, season
from app.services.intent.parser import LLMIntentParser, ParsedIntent, RuleIntentParser, describe_changes
from app.services.itinerary.planner import StopInput, parse_day_windows, plan_itinerary, subtract_closure
from app.services.llm.base import LLMRequest, LLMResponse
from app.services.llm.llm_service import AllProvidersFailedError, LLMService
from app.services.llm.prompts.recommendation_prompt import build_messages
from app.services.llm.response_schemas import RECOMMENDATION_JSON_SCHEMA
from app.services.recommendation.fallback import templated_response
from app.services.recommendation.payload import build_user_payload, fact_ids
from app.services.recommendation.types import CandidateRow, CandidateSource
from app.services.recommendation.validator import RecommendationValidator, ValidatedRecommendations
from app.services.routing.router import EstimateRouter, RoutingService
from app.services.semantic.service import SemanticService
from app.services.weather.base import WeatherSnapshot
from app.services.weather.service import WeatherService

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DISCLAIMER = ("Explanations are generated from the listed data and may be imperfect. Entry rules, prices and "
              "timings come from cited sources and can change; check locally before you travel.")


@dataclass(frozen=True)
class EngineConfig:
    candidate_limit: int = 40
    prefetch_limit: int = 300
    max_results: int = 8
    max_prompt_chars: int = 60_000
    temperature: float = 0.2
    max_output_tokens: int = 4096
    assumed_speed_kmh: float = 22.0
    detour_factor: float = 1.35
    deterministic_fallback: bool = False
    itinerary_max_stops: int = 5


class RecommendationEngine:
    def __init__(self, candidates: CandidateSource, weather: WeatherService, llm: LLMService | None,
                 config: EngineConfig, *, intent: RuleIntentParser | LLMIntentParser | None = None,
                 semantic: SemanticService | None = None, routing: RoutingService | None = None) -> None:
        self.candidate_source = candidates
        self.weather = weather
        self.llm = llm
        self.config = config
        self.intent = intent or RuleIntentParser()
        self.semantic = semantic or SemanticService()
        self.routing = routing or RoutingService(None, EstimateRouter(config.assumed_speed_kmh, config.detour_factor))

    # =================================================================== public
    async def recommend(self, profile: PreferenceProfile) -> RecommendationResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        when = self._local_time(profile)

        # A — understand the request
        parsed, intent_method = await self._parse(" ".join(filter(None, [profile.natural_language,
                                                                          *profile.constraints])))
        visitor = merge_visitor(profile.visitor, parsed)
        if parsed.available_hours:
            profile = profile.model_copy(update={"available_hours": parsed.available_hours})
        extra_interests = [c for c in parsed.prefer_categories if c not in visitor.avoid_categories]
        timings["understand"] = _ms(t0)

        # B — retrieve candidates, weather and events concurrently
        t = time.perf_counter()
        rows, (weather, weather_status), events = await self._gather(profile, when)
        timings["retrieve"] = _ms(t)

        # C — hard constraints + features
        t = time.perf_counter()
        excluded = {"beyond_max_distance": 0, "closed_today": 0}
        within = [r for r in rows if r.distance_m <= profile.radius_m]  # defence in depth over ST_DWithin
        excluded["beyond_max_distance"] = len(rows) - len(within)
        rows = within
        query = " ".join(filter(None, [profile.natural_language, *parsed.vibe]))
        semantic, semantic_method = await self.semantic.scores(query, rows)
        season_name = season(when, profile.latitude, profile.longitude)
        features = compute_features(rows, profile, weather, when, self.config.assumed_speed_kmh, visitor=visitor,
                                    events=events, semantic=semantic, season=season_name,
                                    extra_interests=extra_interests, detour_factor=self.config.detour_factor)
        kept = []
        for row, feat in zip(rows, features, strict=True):
            if feat.closed_today:
                excluded["closed_today"] += 1
            elif feat.excluded_reason:
                excluded[feat.excluded_reason] = excluded.get(feat.excluded_reason, 0) + 1
            else:
                kept.append((row, feat))
        kept.sort(key=lambda p: p[1].pre_score, reverse=True)
        shortlisted = kept[: self.config.candidate_limit]
        timings["filter_and_score"] = _ms(t)

        aq = weather.air_quality if weather else None
        relevant_events = _relevant_events(events, rows, when, profile.available_hours)
        base = dict(
            request=profile.model_copy(update={"visitor": visitor}), weather_status=weather_status,
            weather=self._weather_summary(weather, profile), candidates_found=len(rows), excluded=excluded,
            generated_at=datetime.now(UTC), disclaimer=DISCLAIMER, understood=describe_changes(parsed),
            intent_method=intent_method, season=SEASON_LABELS.get(season_name, season_name),
            air_quality=AirQualityOut(us_aqi=aq.us_aqi, pm2_5=aq.pm2_5, pm10=aq.pm10, category=aq.category)
            if aq else None,
            events=[EventOut(name=e.name, start=e.start, end=e.end, location_name=e.location_name,
                             crowd_impact=e.crowd_impact, description=e.description, date_note=e.date_note,
                             source_url=e.source_url) for e in relevant_events],
            semantic_method=semantic_method,
        )
        if not shortlisted:
            timings["total"] = _ms(t0)
            return RecommendationResult(
                recommendations=[], candidates=[], summary="No destinations matched your location and constraints.",
                generation_method="none", provider=None, model=None, fallback_used=False, candidates_sent=0,
                data_origin="none", warnings=[], timings_ms=timings, **base)

        # D/E/F — LLM ranking with validation
        t = time.perf_counter()
        cands = [r for r, _ in shortlisted]
        feats = [f for _, f in shortlisted]
        context = {"season": SEASON_LABELS.get(season_name, season_name),
                   "visitor": visitor.model_dump(exclude={"avoid_categories", "max_entry_fee_inr"}),
                   "understood_request": describe_changes(parsed)}
        validated, method, provider, model, fallback_used, sent = await self._rank(
            profile, cands, feats, weather, context)
        timings["llm"] = _ms(t)

        by_id = {r.poi_id: (r, f) for r, f in shortlisted}
        items = [self._item(rec, *by_id[rec.poi_id]) for rec in validated.items]

        t = time.perf_counter()
        itinerary = await self._itinerary(profile, when, items, by_id)
        timings["itinerary"] = _ms(t)

        recommended = {i.poi_id for i in items}
        markers = [CandidateMarker(poi_id=r.poi_id, name=r.name, category=r.category, latitude=r.latitude,
                                   longitude=r.longitude, distance_km=f.distance_km,
                                   recommended=r.poi_id in recommended) for r, f in shortlisted]
        origins = {r.data_origin for r in cands}
        data_origin = origins.pop() if len(origins) == 1 else "mixed"
        if weather is not None and weather.is_mock:
            validated.warnings.append("weather data is MOCK")
        timings["total"] = _ms(t0)
        logger.info("recommendation_completed", extra={
            "method": method, "provider": provider, "candidates": len(rows), "sent": sent, "returned": len(items),
            "warnings": len(validated.warnings), "timings_ms": timings, "excluded": excluded})
        return RecommendationResult(
            recommendations=items, candidates=markers, summary=validated.summary, generation_method=method,
            provider=provider, model=model, fallback_used=fallback_used, candidates_sent=sent,
            data_origin=data_origin, warnings=validated.warnings, itinerary=itinerary, timings_ms=timings, **base)

    async def refine(self, profile: PreferenceProfile, message: str) -> tuple[PreferenceProfile, list[str]]:
        """Apply a conversational change ('fewer temples', 'only 2 hours', 'kuch khana bhi') to a profile."""
        parsed, _ = await self._parse(message)
        visitor = merge_visitor(profile.visitor, parsed)
        interests = [i for i in profile.interests if i not in parsed.avoid_categories]
        for c in parsed.prefer_categories:
            if c not in interests and c not in parsed.avoid_categories:
                interests.append(c)
        update: dict = {"visitor": visitor, "interests": interests}
        if parsed.available_hours:
            update["available_hours"] = parsed.available_hours
        if parsed.distance_factor:
            update["max_distance_km"] = round(min(200.0, max(1.0, profile.max_distance_km * parsed.distance_factor)),
                                              1)
        changes = describe_changes(parsed)
        if not changes:
            changes = ["No specific change recognised — showing results again"]
        return profile.model_copy(update=update), changes

    # =================================================================== stages
    async def _parse(self, text: str) -> tuple[ParsedIntent, str]:
        if not text.strip():
            return ParsedIntent(), "none"
        if isinstance(self.intent, LLMIntentParser):
            return await self.intent.parse(text)
        return self.intent.parse(text), "rules"

    @staticmethod
    def _local_time(profile: PreferenceProfile) -> datetime:
        if profile.client_datetime is not None:
            dt = profile.client_datetime
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return now.astimezone(IST) if in_india(profile.latitude, profile.longitude) else now

    async def _gather(self, profile: PreferenceProfile, when: datetime):
        results: dict = {}

        async def load_rows() -> None:
            results["rows"] = await anyio.to_thread.run_sync(
                self.candidate_source.find_candidates, profile.latitude, profile.longitude, profile.radius_m,
                self.config.prefetch_limit)

        async def load_weather() -> None:
            results["weather"] = await self.weather.get(profile.latitude, profile.longitude, profile.available_hours)

        async def load_events() -> None:
            finder = getattr(self.candidate_source, "events_between", None)
            results["events"] = [] if finder is None else await anyio.to_thread.run_sync(
                finder, when - timedelta(days=1), when + timedelta(days=3))

        async with anyio.create_task_group() as tg:
            tg.start_soon(load_rows)
            tg.start_soon(load_weather)
            tg.start_soon(load_events)
        return results["rows"], results["weather"], results["events"]

    async def _rank(self, profile: PreferenceProfile, cands: list[CandidateRow], feats: list[CandidateFeatures],
                    weather: WeatherSnapshot | None, context: dict):
        if self.llm is None:
            return self._deterministic_or_fail(profile, cands, feats, weather, context,
                                               reason="no LLM provider configured")
        candidate_count = len(cands)
        for attempt in range(3):  # later passes only after a context-window overflow (halving each time)
            payload, sent = build_user_payload(profile, cands[:candidate_count], feats[:candidate_count], weather,
                                               self.config.max_prompt_chars, context=context)
            system, user = build_messages(payload, self.config.max_results)
            request = LLMRequest(system=system, user=user, json_schema=RECOMMENDATION_JSON_SCHEMA,
                                 temperature=self.config.temperature,
                                 max_output_tokens=self.config.max_output_tokens)
            validator = RecommendationValidator(cands[:sent], feats[:sent], profile.max_distance_km, weather,
                                                self.config.max_results)
            try:
                result = await self.llm.generate(request, validator)
            except AllProvidersFailedError as exc:
                if exc.last_kind == "context_overflow" and attempt < 2 and sent > 5:
                    candidate_count = max(5, sent // 2)
                    logger.warning("llm_context_overflow_retry", extra={"candidates": candidate_count})
                    continue
                summary = [f"{a.provider}:{a.outcome}" for a in exc.attempts]
                return self._deterministic_or_fail(profile, cands, feats, weather, context,
                                                   reason="all LLM providers failed", attempts=summary)
            except Exception as exc:  # an unexpected provider bug must not break the request
                logger.exception("llm_unexpected_error")
                return self._deterministic_or_fail(profile, cands, feats, weather, context,
                                                   reason=f"unexpected LLM error ({type(exc).__name__})")
            response: LLMResponse = result.response
            method = "mock_llm" if response.provider == "mock" else "llm"
            fallback_used = response.provider != self.llm.providers[0].name
            return result.value, method, response.provider, response.model, fallback_used, sent
        raise AssertionError("unreachable")  # pragma: no cover

    def _deterministic_or_fail(self, profile, cands, feats, weather, context, *, reason: str,
                               attempts: list[str] | None = None):
        if not self.config.deterministic_fallback:
            raise RecommendationUnavailableError(
                "Recommendations are temporarily unavailable: " + reason + ". Please try again.",
                details={"attempts": attempts or []})
        payload, sent = build_user_payload(profile, cands, feats, weather, self.config.max_prompt_chars,
                                           context=context)
        response = LLMResponse(text=json.dumps(templated_response(payload, self.config.max_results)),
                               provider="deterministic", model="pre-score-v1")
        validator = RecommendationValidator(cands[:sent], feats[:sent], profile.max_distance_km, weather,
                                            self.config.max_results)
        validated: ValidatedRecommendations = validator(response)
        validated.warnings.insert(0, f"deterministic fallback used ({reason}); explanations are templated")
        return validated, "deterministic_fallback", None, None, True, sent

    async def _itinerary(self, profile: PreferenceProfile, when: datetime, items: list[RecommendationItem],
                         by_id: dict) -> ItineraryOut | None:
        if not items:
            return None
        stops = []
        for item in items[:6]:
            row, feat = by_id[item.poi_id]
            facts = facts_by_key(row.facts)
            closure = facts.get("midday_closure")
            windows = subtract_closure(
                parse_day_windows(row.opening_hours, when),
                _hhmm(closure.value["start"], closure.value["end"]) if closure else None)
            visit = feat.visit_minutes or DEFAULT_VISIT_MINUTES.get(row.category, 60)
            stops.append(StopInput(row.poi_id, row.name, row.latitude, row.longitude, visit, windows, item.rank))
        matrix = await self.routing.matrix([(profile.latitude, profile.longitude)]
                                           + [(s.latitude, s.longitude) for s in stops])
        plan = plan_itinerary(stops, matrix, when, int(profile.available_hours * 60), self.config.itinerary_max_stops)
        return ItineraryOut(
            start=plan.start, end=plan.end, total_travel_minutes=plan.total_travel_minutes,
            travel_method=plan.travel_method, skipped=plan.skipped, notes=plan.notes,
            stops=[ItineraryStopOut(poi_id=s.poi_id, name=s.name, arrive=s.arrive, depart=s.depart,
                                    travel_minutes=s.travel_minutes, wait_minutes=s.wait_minutes,
                                    visit_minutes=s.visit_minutes, hours_known=s.hours_known) for s in plan.stops])

    # ================================================================ assembly
    @staticmethod
    def _item(rec, row: CandidateRow, f: CandidateFeatures) -> RecommendationItem:
        s = row.sentiment or {}
        aspects = s.get("aspects") or {}
        ids = fact_ids(row)
        all_facts = [FactOut(id=fid, key=fact.key, text=fact.display_text, verified=fact.verified,
                             source_name=fact.source_name, source_url=fact.source_url) for fid, fact in ids.items()]
        cited = [fo for fo in all_facts if fo.id in set(rec.cited_fact_ids)]
        return RecommendationItem(
            poi_id=row.poi_id, rank=rec.rank, name=row.name, category=row.category, categories=row.categories,
            latitude=row.latitude, longitude=row.longitude, address=row.address, website=row.website,
            photo_url=row.photo_url or row.image_url,
            facts=RecommendationFacts(
                distance_km=f.distance_km, rating=f.rating, rating_count=f.rating_count,
                rating_sources=sorted({r.source for r in row.ratings}), review_count=row.review_count,
                photo_count=row.photo_count, photo_activity=f.photo_activity,
                sentiment_status=s.get("status", "unavailable"), sentiment_overall=s.get("overall"),
                positive_aspects=sorted(a for a, v in aspects.items() if v.get("label") == "positive"),
                negative_aspects=sorted(a for a, v in aspects.items() if v.get("label") == "negative"),
                hours_today=f.hours_today, time_fit=f.time_fit, data_origin=row.data_origin,
            ),
            reason=rec.reason, matched_preferences=rec.matched_preferences, advantages=rec.advantages,
            considerations=rec.considerations, weather_suitability=rec.weather_suitability,
            review_insights=rec.review_insights,
            notices=[NoticeOut(kind=n.kind, text=n.text, severity=n.severity, source_url=n.source_url,
                               verified=n.verified) for n in (f.notices or [])],
            cited_facts=cited, place_facts=all_facts, season_note=f.season_note, local_names=row.alt_names[:3],
            image_url=row.image_url, image_attribution=row.image_attribution, wiki_url=row.wiki_url,
        )

    @staticmethod
    def _weather_summary(weather: WeatherSnapshot | None, profile: PreferenceProfile) -> WeatherSummary | None:
        if weather is None:
            return None
        cur = weather.current
        return WeatherSummary(
            source=weather.source, is_mock=weather.is_mock, retrieved_at=weather.retrieved_at,
            observed_at=cur.valid_at if cur else None, condition=cur.condition if cur else None,
            temperature_c=cur.temperature_c if cur else None, humidity=cur.humidity if cur else None,
            wind_speed_kmh=cur.wind_speed_kmh if cur else None,
            max_precipitation_probability=weather.max_precipitation_probability(profile.available_hours),
        )


def merge_visitor(explicit: VisitorProfile, parsed: ParsedIntent) -> VisitorProfile:
    """Free text can only ADD restrictions to what the user set explicitly."""
    fee = explicit.max_entry_fee_inr
    if parsed.max_entry_fee_inr is not None:
        fee = parsed.max_entry_fee_inr if fee is None else min(fee, parsed.max_entry_fee_inr)
    return VisitorProfile(
        non_hindu_visitor=explicit.non_hindu_visitor or parsed.non_hindu_visitor,
        foreign_national=explicit.foreign_national or parsed.foreign_national,
        wheelchair_user=explicit.wheelchair_user or parsed.wheelchair_user,
        with_children=explicit.with_children or parsed.with_children,
        with_elderly=explicit.with_elderly or parsed.with_elderly,
        avoid_crowds=explicit.avoid_crowds or parsed.avoid_crowds,
        max_entry_fee_inr=fee,
        avoid_categories=list(dict.fromkeys([*explicit.avoid_categories, *parsed.avoid_categories])),
    )


def _relevant_events(events: list[EventView], rows: list[CandidateRow], when: datetime, hours: float):
    names = {e.name for r in rows for e in events_for(r, events, when, hours)}
    return [e for e in events if e.name in names]


def _hhmm(start: str, end: str) -> tuple[int, int]:
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return h1 * 60 + m1, h2 * 60 + m2


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 1)
