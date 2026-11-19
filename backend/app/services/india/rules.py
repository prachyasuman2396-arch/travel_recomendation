"""Visitor rules — deterministic hard constraints and mandatory notices for Indian destinations.

These run in code BEFORE the LLM sees anything, and their notices are attached to results by code
AFTER the LLM — so an entry restriction can never be omitted or softened by model output.

Hard exclusions only use **verified** facts. Unverified facts produce notices ("reported: ...").
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

from app.services.india.facts import FactView, facts_by_key


class VisitorProfile(BaseModel):
    """Who is travelling. Optional, per request, never stored unless the user saves preferences."""

    non_hindu_visitor: bool = Field(False, description="Show temple entry rules that apply to non-Hindus")
    foreign_national: bool = Field(False, description="Use foreign-visitor ticket prices")
    wheelchair_user: bool = False
    with_children: bool = False
    with_elderly: bool = False
    avoid_crowds: bool = False
    max_entry_fee_inr: int | None = Field(None, ge=0, le=100000)
    avoid_categories: list[str] = Field(default_factory=list, max_length=12)


@dataclass
class Notice:
    kind: str  # entry | fee | accessibility | crowd | season | phones | dress
    text: str
    severity: str  # info | warning | critical
    source_url: str | None = None
    verified: bool = False


@dataclass
class RuleOutcome:
    excluded: bool = False
    reason: str | None = None
    notices: list[Notice] = field(default_factory=list)
    penalty: float = 0.0  # soft, subtracted from the pre-score


@dataclass(frozen=True)
class EventView:
    name: str
    start: datetime
    end: datetime
    latitude: float
    longitude: float
    radius_km: float
    poi_names: tuple[str, ...]
    crowd_impact: str
    description: str
    date_note: str | None
    source_url: str
    location_name: str = ""


def entry_fee_for(fee: dict, foreign: bool) -> int | None:
    return fee.get("foreign") if foreign else fee.get("indian")


# ----------------------------------------------------------------------------- non-Hindu entry heuristics
# Used when no curated fact exists for a place (e.g. a Wikipedia GeoSearch record). All matching is on
# word boundaries — plain substrings matched "math" in "aftermath" and "puri" in "purity".
# Only the place NAME and CATEGORY decide whether a place is a Hindu shrine or secular. The free-text
# description is too noisy: Odisha temple articles say "Kalinga school of architecture" (which used to
# make Jagannath look "secular"), and craft-village articles mention temples (which used to exclude
# Raghurajpur).


def _words(*terms: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)


# Shrines whose names are unique enough to match anywhere in India.
_ORTHODOX_ANYWHERE = _words(
    "lingaraj", "lingaraja", "guruvayur", "guruvayoor", "padmanabhaswamy", "ananta vasudeva",
    "sakhigopal", "sakshi gopal",
)
# Names that are only restricted inside Puri (there are open Jagannath/Narasimha temples elsewhere in India).
_ORTHODOX_PURI = _words(
    "jagannath", "jagannatha", "vimala", "bimala", "mukti mandap", "mukti mandapa", "markandeshwar",
    "markandeswar", "govardhan math", "radhakanta math", "gundicha", "alamchandi", "bata mangala",
    "chakra narasimha", "narasimha", "lokanath", "lokanatha", "kapalamochana",
)
_HINDU_SHRINE = _words("temple", "mandir", "mandira", "math", "matha", "deula", "shrine", "mandap", "mandapa",
                       "devi", "pitha", "peetha", "gopuram")
_OTHER_FAITH = _words("church", "cathedral", "basilica", "chapel", "mosque", "masjid", "dargah", "gurudwara",
                      "gurdwara", "monastery", "gompa", "stupa", "vihara", "buddhist", "jain", "synagogue")
_SECULAR = _words("beach", "hospital", "medical", "college", "school", "university", "railway", "station",
                  "bridge", "museum", "village", "market", "bazaar", "park", "garden", "lake", "lighthouse",
                  "hotel", "airport", "stadium", "crafts", "sea", "road")
_VIEWING = _words("viewing platform", "viewpoint", "view point")
_SECULAR_CATEGORIES = {"beaches", "parks_gardens", "shopping", "food", "nature", "wildlife", "waterfalls",
                       "entertainment", "adventure"}
_RESTRICTION_PHRASES = _words(
    "restricted to hindus", "hindus only", "only hindus", "non-hindus are not allowed", "non-hindus not allowed",
    "not open to non-hindus", "out of bounds for non-hindus", "non-hindu visitors are not permitted",
    "prohibited for non-hindus", "entry is forbidden to non-hindus", "non-hindus are not permitted",
)

# (lat, lon, radius_m): any Hindu shrine inside these zones is treated as restricted.
_JAGANNATH_PURI = (19.8048, 85.8180)
_PURI_ZONE_M = 6_000          # Puri town: Jagannath complex, its subsidiary shrines and the mathas
_LINGARAJ = (20.2382, 85.8338)
_LINGARAJ_COMPLEX_M = 200     # subsidiary shrines inside the Lingaraj compound wall


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def restricted_for_non_hindus(category: str, categories: list[str], poi_name: str | None,
                              description: str | None, lat: float | None, lon: float | None) -> bool:
    """Heuristic exclusion for places without a curated ``non_hindu_entry`` fact.

    Order matters: secular, other-faith and viewing-platform NAMES are never excluded; then known orthodox
    names (anywhere, or inside Puri); then location zones and explicit "Hindus only" wording, which apply to
    Hindu shrines only. A curated ``non_hindu_entry`` fact, when present, is checked before this and wins.
    """
    name = poi_name or ""
    # Secular / other-faith names first: "Lingaraj Temple Road railway station", "Sri Jagannath Sanskrit
    # University", "Lingaraj viewing platform" and "Puri Beach" are all open to everyone.
    if _VIEWING.search(name) or _SECULAR.search(name) or _OTHER_FAITH.search(name):
        return False
    if _ORTHODOX_ANYWHERE.search(name):
        return True
    in_puri = lat is not None and lon is not None and _distance_m(lat, lon, *_JAGANNATH_PURI) <= _PURI_ZONE_M
    if in_puri and _ORTHODOX_PURI.search(name):
        return True
    if category in _SECULAR_CATEGORIES:
        return False
    is_shrine = bool(_HINDU_SHRINE.search(name)) or category == "religious" or "religious" in categories
    if not is_shrine:
        return False
    if in_puri:
        return True
    if lat is not None and lon is not None and _distance_m(lat, lon, *_LINGARAJ) <= _LINGARAJ_COMPLEX_M:
        return True
    return bool(description and _RESTRICTION_PHRASES.search(description))


def apply_rules(visitor: VisitorProfile, category: str, categories: list[str], facts: list[FactView],
                events: list[EventView], visit_window: tuple[datetime, datetime] | None = None,
                poi_name: str | None = None, description: str | None = None,
                lat: float | None = None, lon: float | None = None) -> RuleOutcome:
    """Hard exclusions (entry rules & constraints), notices and soft penalties for one place.

    ``visit_window``: when given, the avoid-crowds penalty applies only to events that overlap it;
    events a day or two ahead still produce a notice ("plans and crowds build early") but no penalty.
    """
    out = RuleOutcome()
    f = facts_by_key(facts)

    if category in visitor.avoid_categories:
        return RuleOutcome(excluded=True, reason="avoided_category")

    # --- Temple entry for non-Hindu visitors
    entry = f.get("non_hindu_entry")
    if visitor.non_hindu_visitor:
        if entry and entry.value.get("allowed") is False:
            # Verified or reported, with or without an outside viewing platform: the place itself cannot be
            # entered, so it is never recommended as a stop. (Erring towards exclusion is the safe direction.)
            return RuleOutcome(excluded=True, reason="entry_restricted")
        if restricted_for_non_hindus(category, categories, poi_name, description, lat, lon):
            return RuleOutcome(excluded=True, reason="entry_restricted")
    elif entry and entry.value.get("allowed") is False:
        # Inform everyone, without assuming the user's religion.
        out.notices.append(Notice("entry", entry.display_text, "info", entry.source_url, entry.verified))

    # --- Entry for foreign nationals (some shrines, protected/tribal areas)
    foreign_entry = f.get("foreigner_entry")
    if visitor.foreign_national and foreign_entry and foreign_entry.value.get("allowed") is False:
        if foreign_entry.verified:
            return RuleOutcome(excluded=True, reason="entry_restricted")
        out.notices.append(Notice("entry", f"Reported: {foreign_entry.display_text}", "critical",
                                  foreign_entry.source_url, False))
        out.penalty += 0.5

    # --- Ticket price (dual pricing: Indian vs foreign visitor tiers)
    fee = f.get("entry_fee")
    if fee:
        price = entry_fee_for(fee.value, visitor.foreign_national)
        if price is not None and visitor.max_entry_fee_inr is not None and price > visitor.max_entry_fee_inr:
            if fee.verified:
                return RuleOutcome(excluded=True, reason="over_budget")
            out.penalty += 0.2
        label = "foreign visitors" if visitor.foreign_national else "Indian visitors"
        if price is not None:
            text = "Free entry" if price == 0 else f"Entry ₹{price} for {label}"
            out.notices.append(Notice("fee", text, "info", fee.source_url, fee.verified))
        elif visitor.max_entry_fee_inr is not None:
            # Only the other tier is known (e.g. Indian price listed, foreign price missing): never assume the
            # cheaper tier applies to this visitor.
            out.notices.append(Notice("fee", f"Ticket price for {label} not confirmed — check before going.",
                                      "warning", fee.source_url, False))
            out.penalty += 0.1

    # --- Accessibility
    access = f.get("wheelchair_access")
    if visitor.wheelchair_user:
        if access and access.value.get("accessible") is False:
            if access.verified:
                return RuleOutcome(excluded=True, reason="not_wheelchair_accessible")
            out.notices.append(Notice("accessibility", f"Reported: {access.display_text}", "warning",
                                      access.source_url, False))
            out.penalty += 0.3
        elif access is None:
            out.notices.append(Notice("accessibility", "Wheelchair access not confirmed — check before going.",
                                      "warning"))
            out.penalty += 0.05
    if visitor.with_elderly and access and access.value.get("accessible") is False:
        out.notices.append(Notice("accessibility", access.display_text, "warning", access.source_url,
                                  access.verified))
        out.penalty += 0.1

    # --- Phones / dress code (everyone)
    phones = f.get("phones_allowed")
    if phones and phones.value.get("allowed") is False:
        out.notices.append(Notice("phones", phones.display_text, "info", phones.source_url, phones.verified))
    dress = f.get("dress_code")
    if dress and dress.value.get("required"):
        out.notices.append(Notice("dress", dress.display_text, "info", dress.source_url, dress.verified))

    # --- Kids
    kids = f.get("kid_friendly")
    if visitor.with_children and kids:
        # Reported as good for children -> small bonus; reported as unsuitable -> small penalty.
        out.penalty += -0.1 if kids.value.get("suitable") else 0.1

    # --- Festivals / crowds
    for ev in events:
        severity = "critical" if ev.crowd_impact == "extreme" else "warning"
        when = ev.start.strftime("%d %b") + ("" if ev.end.date() == ev.start.date() else
                                             f"–{ev.end.strftime('%d %b')}")
        note = f" ({ev.date_note})" if ev.date_note else ""
        out.notices.append(Notice("crowd", f"{ev.name} on {when}: {ev.description}{note}", severity,
                                  ev.source_url, True))
        overlaps = visit_window is None or (ev.start <= visit_window[1] and ev.end >= visit_window[0])
        if visitor.avoid_crowds and overlaps:
            out.penalty += 0.35 if ev.crowd_impact in ("high", "extreme") else 0.15
    return out
