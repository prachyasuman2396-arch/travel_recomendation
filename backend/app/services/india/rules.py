"""Visitor rules — deterministic hard constraints and mandatory notices for Indian destinations.

These run in code BEFORE the LLM sees anything, and their notices are attached to results by code
AFTER the LLM — so an entry restriction can never be omitted or softened by model output.

Hard exclusions only use **verified** facts. Unverified facts produce notices ("reported: ...").
"""

from __future__ import annotations

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
            platform = entry.value.get("viewing_platform")
            if platform is not True:
                return RuleOutcome(excluded=True, reason="entry_restricted")
            text = entry.display_text if entry.verified else f"Reported: {entry.display_text}"
            out.notices.append(Notice("entry", text, "critical", entry.source_url, entry.verified))
            out.penalty += 0.5  # Viewing from outside only

        text_to_check = f"{poi_name or ''} {description or ''}".lower()
        restricted_phrases = (
            "jagannath temple complex", "jagannatha temple complex",
            "within the jagannath", "inside the jagannath", "precincts of the jagannath",
            "lingaraj temple complex", "lingaraja temple complex",
            "within the lingaraj", "inside the lingaraj",
            "guruvayur temple", "padmanabhaswamy",
            "restricted to hindus", "hindus only", "entry is restricted to hindus",
            "only hindus are allowed", "only hindus may enter", "only hindus",
            "non-hindus are not allowed", "non-hindus not allowed", "not open to non-hindus",
            "out of bounds for non-hindus", "non-hindu visitors are not permitted",
            "prohibited for non-hindus", "entry is forbidden to non-hindus",
        )
        if any(p in text_to_check for p in restricted_phrases):
            return RuleOutcome(excluded=True, reason="entry_restricted")

        is_religious = (
            "religious" in categories
            or category == "religious"
            or any(t in text_to_check for t in ["temple", "mandir", "math", "matha", "deula", "shrine"])
        )
        is_secular = any(s in text_to_check for s in ["hospital", "medical college", "school", "railway", "station", "beach", "bridge", "stambha"])

        if is_religious and not is_secular:
            # Orthodox Hindu shrines/complexes in Puri where non-Hindus are strictly not permitted
            puri_orthodox = (
                "jagannath", "vimala", "bimala", "mukti mandap", "mukti mandapa",
                "markandeshwar", "govardhan math", "radhakanta math", "gundicha",
                "alamchandi", "bata mangala", "chakra narasimha", "narasimha temple",
                "sakhigopal", "lokanatha", "lokanath", "kapalamochana",
            )
            is_in_puri_zone = (
                (lat is not None and lon is not None and abs(lat - 19.81) < 0.2 and abs(lon - 85.83) < 0.2)
                or "puri" in text_to_check
            )
            if any(t in text_to_check for t in puri_orthodox) or is_in_puri_zone:
                return RuleOutcome(excluded=True, reason="entry_restricted")
    elif entry and entry.value.get("allowed") is False:
        # Inform everyone, without assuming the user's religion.
        out.notices.append(Notice("entry", entry.display_text, "info", entry.source_url, entry.verified))

    # --- Ticket price
    fee = f.get("entry_fee")
    if fee:
        price = entry_fee_for(fee.value, visitor.foreign_national)
        if price is not None and visitor.max_entry_fee_inr is not None and price > visitor.max_entry_fee_inr:
            if fee.verified:
                return RuleOutcome(excluded=True, reason="over_budget")
            out.penalty += 0.2
        if price is not None:
            label = "foreign visitors" if visitor.foreign_national else "Indian visitors"
            text = "Free entry" if price == 0 else f"Entry ₹{price} for {label}"
            out.notices.append(Notice("fee", text, "info", fee.source_url, fee.verified))

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
