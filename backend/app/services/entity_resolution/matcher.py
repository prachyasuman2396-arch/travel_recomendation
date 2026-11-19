"""Entity resolution: decide whether a provider record is the same place as an existing POI.

Weighted score over name, distance, category compatibility and address:

    score = Σ wᵢ·sᵢ / Σ wᵢ   (components that are not comparable are skipped)
            − conflict_penalty  if categories conflict

Guards (because proximity alone must never merge two places):
  * beyond ``max_distance_m``                -> not a candidate at all
  * name similarity < ``min_name_similarity`` -> LOW regardless of score
  * distinctive words conflict ("Odisha Crafts Museum" vs "Odisha State Museum") -> name capped at 0.5
  * category conflict                        -> score penalised (cafe vs garden)
  * two strong candidates within ``ambiguity_margin`` -> MEDIUM (flag, do not guess)

Outcomes: HIGH = auto-link, MEDIUM = flag for manual review, LOW = keep separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.services.entity_resolution.geo import haversine_m
from app.services.entity_resolution.text_similarity import (
    address_similarity,
    compact_similarity,
    distinctive_conflict,
    name_similarity,
    token_overlap,
)
from app.services.preprocessing.categories import category_compatibility
from app.services.preprocessing.cleaning import normalize_address, normalize_name

METHOD = "weighted_v1"


class MatchOutcome(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class MatchConfig:
    high_threshold: float = 0.80
    medium_threshold: float = 0.60
    max_distance_m: float = 400.0
    # Pins for large attractions (zoos, parks) legitimately differ by tens of metres across providers.
    full_score_distance_m: float = 75.0
    min_name_similarity: float = 0.55
    conflict_penalty: float = 0.20
    ambiguity_margin: float = 0.05
    w_name: float = 0.45
    w_distance: float = 0.25
    w_category: float = 0.20
    w_address: float = 0.10


@dataclass(frozen=True)
class EntityView:
    """The minimal view of a place that matching needs (provider record or stored POI)."""

    key: str  # poi_id for stored POIs, external id for provider records
    name: str
    latitude: float
    longitude: float
    categories: tuple[str, ...] = ()
    address: str | None = None


@dataclass
class MatchResult:
    candidate_key: str | None
    score: float
    outcome: MatchOutcome
    breakdown: dict = field(default_factory=dict)
    method: str = METHOD


class EntityMatcher:
    def __init__(self, config: MatchConfig | None = None) -> None:
        self.config = config or MatchConfig()

    def score_pair(self, record: EntityView, poi: EntityView) -> MatchResult:
        cfg = self.config
        distance = haversine_m(record.latitude, record.longitude, poi.latitude, poi.longitude)
        breakdown: dict = {"distance_m": round(distance, 1)}
        if distance > cfg.max_distance_m:
            return MatchResult(poi.key, 0.0, MatchOutcome.LOW, breakdown | {"reason": "too_far"})

        rec_name, poi_name = normalize_name(record.name), normalize_name(poi.name)
        s_name = max(name_similarity(rec_name, poi_name),
                     compact_similarity(normalize_name(record.name, canonical=False),
                                        normalize_name(poi.name, canonical=False)))
        conflict = distinctive_conflict(rec_name, poi_name) and s_name < 0.9
        if conflict:
            # Shared generic words ("Odisha ... Museum") must not make two different places look alike.
            s_name = min(s_name, 0.5)
        span = max(cfg.max_distance_m - cfg.full_score_distance_m, 1e-6)
        s_dist = 1.0 if distance <= cfg.full_score_distance_m else 1.0 - (distance - cfg.full_score_distance_m) / span
        s_cat = category_compatibility(record.categories, poi.categories)
        s_addr = address_similarity(normalize_address(record.address), normalize_address(poi.address))

        parts = [(cfg.w_name, s_name), (cfg.w_distance, s_dist)]
        if s_cat is not None:
            parts.append((cfg.w_category, s_cat))
        if s_addr is not None:
            parts.append((cfg.w_address, s_addr))
        score = sum(w * s for w, s in parts) / sum(w for w, _ in parts)
        if s_cat == 0.0:
            score -= cfg.conflict_penalty
        score = max(0.0, min(1.0, score))

        breakdown |= {"name": round(s_name, 3), "distance": round(s_dist, 3), "distinctive_conflict": conflict,
                      "category": s_cat, "address": None if s_addr is None else round(s_addr, 3),
                      "score": round(score, 3)}

        if s_name < cfg.min_name_similarity:
            outcome = MatchOutcome.LOW
            breakdown["reason"] = "name_too_different"
        elif score >= cfg.high_threshold:
            outcome = MatchOutcome.HIGH
        elif score >= cfg.medium_threshold:
            outcome = MatchOutcome.MEDIUM
        else:
            outcome = MatchOutcome.LOW
        return MatchResult(poi.key, score, outcome, breakdown)

    def best_match(self, record: EntityView, candidates: list[EntityView]) -> MatchResult:
        scored = sorted((self.score_pair(record, c) for c in candidates), key=lambda m: m.score, reverse=True)
        if not scored or scored[0].outcome == MatchOutcome.LOW:
            best = scored[0] if scored else None
            return MatchResult(None, best.score if best else 0.0, MatchOutcome.LOW,
                               best.breakdown if best else {"reason": "no_candidates"})
        best = scored[0]
        if (best.outcome == MatchOutcome.HIGH and len(scored) > 1
                and scored[1].outcome != MatchOutcome.LOW
                and best.score - scored[1].score < self.config.ambiguity_margin):
            best.outcome = MatchOutcome.MEDIUM
            best.breakdown["reason"] = "ambiguous_between_candidates"
            best.breakdown["runner_up"] = scored[1].candidate_key
        return best


@dataclass(frozen=True)
class PhotoAssociationConfig:
    max_distance_m: float = 500.0
    min_confidence: float = 0.55
    w_text: float = 0.6
    w_distance: float = 0.4


def associate_photo(title: str | None, tags: tuple[str, ...], lat: float, lon: float,
                    pois: list[EntityView], cfg: PhotoAssociationConfig | None = None
                    ) -> tuple[str | None, float, str]:
    """Associate a photo with at most one POI.

    Text evidence (tags/title mentioning the POI name) is required: a photo
    with no textual link gets confidence ≤ w_distance (< min_confidence by
    default), so proximity alone never associates it.
    Returns (poi_id | None, confidence, method).
    """
    cfg = cfg or PhotoAssociationConfig()
    # Photo titles/tags use local spellings ("ekamrakanan"), so compare un-canonicalised names here.
    text = normalize_name(" ".join([title or "", *tags]), canonical=False)
    joined_tags = "".join(tags)
    best: tuple[str | None, float, str] = (None, 0.0, "none")
    for poi in pois:
        d = haversine_m(lat, lon, poi.latitude, poi.longitude)
        if d > cfg.max_distance_m:
            continue
        raw_name = normalize_name(poi.name, canonical=False)
        name_tokens = {t for t in raw_name.split() if len(t) >= 4}
        overlap = token_overlap(text, name_tokens)
        # Flickr tags are often concatenated ("ekamrakanan"); check that too.
        compact = raw_name.replace(" ", "")
        if compact and len(compact) >= 6 and compact[:8] in joined_tags:
            overlap = max(overlap, 0.8)
        s_dist = 1.0 - d / cfg.max_distance_m
        confidence = cfg.w_text * min(1.0, overlap * 1.25) + cfg.w_distance * s_dist
        method = "geo+text" if overlap > 0 else "geo_only"
        if confidence > best[1]:
            best = (poi.key, round(min(confidence, 1.0), 3), method)
    if best[1] < cfg.min_confidence:
        return None, best[1], best[2]
    return best
