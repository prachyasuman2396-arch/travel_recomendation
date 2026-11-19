"""Never trust LLM output blindly.

Checks, in order:
  1. JSON parses (code fences tolerated) and matches the Pydantic schema
  2. every poi_id was in the supplied candidate set (unknown ids rejected)
  3. no duplicates; hard distance constraint re-checked; ranks renumbered 1..n
  4. grounding heuristics: numeric claims about distance, rating, review count
     and temperature must match supplied facts; claims about opening hours
     (when none were supplied) and any travel-time claim are removed
  5. weather_suitability forced to "unknown" when no weather was supplied

If nothing valid remains, LLMInvalidResponseError is raised so the LLM layer
retries / falls back. Heuristic limits are documented in docs/architecture.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.services.feature_engineering.features import CandidateFeatures
from app.services.llm.base import LLMInvalidResponseError, LLMResponse
from app.services.llm.json_repair import JSONExtractionError, extract_json
from app.services.llm.response_schemas import LLMRecommendation, LLMRecommendationResponse
from app.services.recommendation.payload import fact_ids
from app.services.recommendation.types import CandidateRow
from app.services.weather.base import WeatherSnapshot

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_KM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km|kilomet(?:er|re)s?)\b", re.IGNORECASE)
_RATING = re.compile(r"(?:rated|rating(?: of)?|score of)\s*(\d(?:\.\d+)?)|(\d\.\d)\s*(?:/\s*5|stars?|★)",
                     re.IGNORECASE)
_COUNT = re.compile(r"(\d[\d,]*)\s+(?:reviews|ratings|reviewers)", re.IGNORECASE)
_TEMP = re.compile(r"(-?\d+(?:\.\d+)?)\s*°\s*C?\b|(-?\d+(?:\.\d+)?)\s*(?:degrees|deg)\b", re.IGNORECASE)
_RUPEES = re.compile(r"(?:₹|rs\.?|inr)\s?(\d{1,6})", re.IGNORECASE)
_HOURS = re.compile(r"\b(?:open(?:s|ing)?|clos(?:es|ing))\b[^.]*\d", re.IGNORECASE)
_TRAVEL = re.compile(r"\b\d+(?:\.\d+)?\s*(?:-\s*\d+\s*)?(?:min(?:ute)?s?|hours?|hrs?)\b[^.]*"
                     r"\b(?:drive|driving|away|ride|journey|travel|by car|commute|walk)\b"
                     r"|\b(?:drive|ride|journey|travel)\s+(?:of|is|takes)\s+(?:about\s+)?\d", re.IGNORECASE)


@dataclass
class ValidatedRecommendations:
    items: list[LLMRecommendation]
    summary: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class GroundingFacts:
    distance_km: float
    ratings: list[float]
    counts: list[int]
    has_hours: bool
    fact_ids: frozenset[str] = frozenset()
    fees: frozenset[int] = frozenset()


def _num(value: str) -> float:
    return float(value.replace(",", ""))


class RecommendationValidator:
    def __init__(self, candidates: list[CandidateRow], features: list[CandidateFeatures], max_distance_km: float,
                 weather: WeatherSnapshot | None, max_results: int) -> None:
        feats = {f.poi_id: f for f in features}
        self.facts = {
            c.poi_id: GroundingFacts(
                distance_km=feats[c.poi_id].distance_km,
                ratings=[r.rating for r in c.ratings] + ([feats[c.poi_id].rating] if feats[c.poi_id].rating else []),
                counts=[r.count for r in c.ratings] + [feats[c.poi_id].rating_count, c.review_count],
                has_hours=bool(c.opening_hours),
                fact_ids=frozenset(fact_ids(c)),
                fees=frozenset(int(v) for fact in c.facts if fact.key == "entry_fee"
                               for k, v in fact.value.items() if isinstance(v, int | float) and k != "currency"),
            )
            for c in candidates
        }
        self.max_distance_km = max_distance_km
        self.weather = weather
        self.temps = self._temps(weather)
        self.max_results = max_results

    @staticmethod
    def _temps(weather: WeatherSnapshot | None) -> list[float]:
        if weather is None:
            return []
        points = ([weather.current] if weather.current else []) + weather.forecast
        return [p.temperature_c for p in points if p.temperature_c is not None]

    # ------------------------------------------------------------------ public
    def __call__(self, response: LLMResponse) -> ValidatedRecommendations:
        parsed, warnings = self._parse(response)
        seen: set[str] = set()
        valid: list[LLMRecommendation] = []
        for rec in sorted(parsed.recommendations, key=lambda r: r.rank):
            facts = self.facts.get(rec.poi_id)
            if facts is None:
                warnings.append(f"rejected unknown poi_id {rec.poi_id!r}")
                continue
            if rec.poi_id in seen:
                warnings.append(f"rejected duplicate poi_id {rec.poi_id}")
                continue
            if facts.distance_km > self.max_distance_km + 1e-6:
                warnings.append(f"rejected {rec.poi_id}: violates distance constraint")
                continue
            cleaned, notes = self._ground(rec, facts)
            warnings.extend(notes)
            if cleaned is None:
                continue
            seen.add(rec.poi_id)
            valid.append(cleaned)
            if len(valid) >= self.max_results:
                break
        if not valid:
            raise LLMInvalidResponseError("LLM output contained no valid recommendations",
                                          provider=response.provider)
        for i, rec in enumerate(valid, start=1):
            rec.rank = i
        summary = self._clean_text(parsed.summary, None)[0] or ""
        return ValidatedRecommendations(items=valid, summary=summary, warnings=warnings)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _parse(response: LLMResponse) -> tuple[LLMRecommendationResponse, list[str]]:
        """Recover JSON (fences, prose, truncation), then validate each item on its own.

        One malformed or half-written item is dropped with a warning instead of failing the whole response.
        """
        warnings: list[str] = []
        try:
            data, repaired = extract_json(response.text)
        except JSONExtractionError as exc:
            raise LLMInvalidResponseError(f"invalid JSON: {exc}", provider=response.provider) from exc
        if repaired:
            warnings.append("LLM output was cut off; kept only the complete recommendations")
        if isinstance(data, list):  # some models return the bare array
            data = {"recommendations": data}
        if not isinstance(data, dict) or not isinstance(data.get("recommendations", []), list):
            raise LLMInvalidResponseError("schema mismatch: 'recommendations' must be a list",
                                          provider=response.provider)
        items: list[LLMRecommendation] = []
        for index, raw in enumerate(data.get("recommendations") or []):
            try:
                items.append(LLMRecommendation.model_validate(raw))
            except ValidationError as exc:
                warnings.append(f"dropped malformed recommendation #{index + 1}: {exc.errors()[0]['msg']}")
        summary = data.get("summary")
        try:
            return LLMRecommendationResponse(recommendations=items[:30],
                                             summary=summary[:1200] if isinstance(summary, str) else ""), warnings
        except ValidationError as exc:
            raise LLMInvalidResponseError(f"schema mismatch: {exc.errors()[0]['msg']}",
                                          provider=response.provider) from exc

    def _ground(self, rec: LLMRecommendation, facts: GroundingFacts) -> tuple[LLMRecommendation | None, list[str]]:
        notes: list[str] = []
        reason, n1 = self._clean_text(rec.reason, facts)
        notes += n1
        if not reason:
            return None, [*notes, f"rejected {rec.poi_id}: reason contained only unsupported claims"]
        lists = {}
        for name in ("advantages", "considerations", "review_insights", "matched_preferences"):
            kept = []
            for item in getattr(rec, name):
                text, n = self._clean_text(item, facts)
                notes += n
                if text:
                    kept.append(text)
            lists[name] = kept
        cited = [fid for fid in rec.cited_fact_ids if fid in facts.fact_ids]
        if len(cited) != len(rec.cited_fact_ids):
            notes.append(f"{rec.poi_id}: dropped citations to unknown facts")
        lists["cited_fact_ids"] = list(dict.fromkeys(cited))
        suitability = rec.weather_suitability if self.weather is not None else "unknown"
        if self.weather is None and rec.weather_suitability != "unknown":
            notes.append(f"{rec.poi_id}: weather_suitability reset to unknown (no weather data)")
        return rec.model_copy(update={"reason": reason, "weather_suitability": suitability, **lists}), notes

    def _clean_text(self, text: str, facts: GroundingFacts | None) -> tuple[str, list[str]]:
        kept, notes = [], []
        for sentence in _SENTENCE.split(text.strip()):
            problem = self._unsupported(sentence, facts)
            if problem:
                notes.append(f"removed unsupported claim ({problem}): {sentence[:80]!r}")
            else:
                kept.append(sentence)
        return " ".join(kept).strip(), notes

    def _unsupported(self, s: str, facts: GroundingFacts | None) -> str | None:
        if _TRAVEL.search(s):
            return "travel time"
        for m in _TEMP.finditer(s):
            value = _num(m.group(1) or m.group(2))
            if not self.temps or not any(abs(value - t) <= 1.5 for t in self.temps):
                return "temperature"
        if facts is None:  # summary: only global checks
            return None
        for m in _KM.finditer(s):
            value = _num(m.group(1))
            if abs(value - facts.distance_km) > max(0.3, 0.1 * facts.distance_km):
                return "distance"
        for m in _RATING.finditer(s):
            value = _num(m.group(1) or m.group(2))
            if not any(abs(value - r) <= 0.05 for r in facts.ratings):
                return "rating"
        for m in _COUNT.finditer(s):
            value = _num(m.group(1))
            if not any(c and abs(value - c) <= max(1, 0.01 * c) for c in facts.counts):
                return "review count"
        if not facts.has_hours and _HOURS.search(s):
            return "opening hours"
        for m in _RUPEES.finditer(s):
            if int(m.group(1)) not in facts.fees:
                return "price"
        return None
