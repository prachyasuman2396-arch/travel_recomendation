"""India-specific place facts: the vocabulary, value validation and governance rules.

Facts are things a traveller in India must know and that generic place APIs don't provide:
temple entry rules, phone/camera bans, dress codes, Indian vs foreign ticket prices, typical visit
length, accessibility. Every fact carries its source.

Governance (enforced in ``rules.py``):
  * ``verified`` facts (curated from cited sources, or approved by an admin) may drive HARD filters.
  * ``unverified`` facts (e.g. extracted by the fact-extraction agent, or reported by Google/OSM)
    are shown as "reported" information and only become soft signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FactKey = Literal[
    "non_hindu_entry", "foreigner_entry", "phones_allowed", "photography", "dress_code", "entry_fee",
    "wheelchair_access", "kid_friendly", "typical_visit_minutes", "opening_note", "midday_closure",
    "best_months", "monsoon_caution", "crowd_note",
]


class _V(BaseModel):
    model_config = ConfigDict(extra="allow")


class AllowedValue(_V):
    allowed: bool


class NonHinduEntryValue(_V):
    allowed: bool
    viewing_platform: bool | None = None


class DressCodeValue(_V):
    required: bool
    note: str | None = None


class EntryFeeValue(_V):
    indian: int | None = Field(None, ge=0)
    foreign: int | None = Field(None, ge=0)
    currency: Literal["INR"] = "INR"


class AccessibleValue(_V):
    accessible: bool


class SuitableValue(_V):
    suitable: bool


class MinutesValue(_V):
    minutes: int = Field(ge=5, le=24 * 60)


class NoteValue(_V):
    note: str = Field(min_length=3, max_length=300)


class ClosureValue(_V):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class MonthsValue(_V):
    months: list[int] = Field(min_length=1, max_length=12)


VALUE_MODELS: dict[str, type[BaseModel]] = {
    "non_hindu_entry": NonHinduEntryValue,
    "foreigner_entry": AllowedValue,
    "phones_allowed": AllowedValue,
    "photography": AllowedValue,
    "dress_code": DressCodeValue,
    "entry_fee": EntryFeeValue,
    "wheelchair_access": AccessibleValue,
    "kid_friendly": SuitableValue,
    "typical_visit_minutes": MinutesValue,
    "opening_note": NoteValue,
    "midday_closure": ClosureValue,
    "best_months": MonthsValue,
    "monsoon_caution": NoteValue,
    "crowd_note": NoteValue,
}


def validate_fact_value(key: str, value: dict) -> dict | None:
    """Return a clean value dict, or None if the key is unknown or the value doesn't fit its schema."""
    model = VALUE_MODELS.get(key)
    if model is None:
        return None
    try:
        return model.model_validate(value).model_dump(exclude_none=False)
    except ValidationError:
        return None


@dataclass(frozen=True)
class FactView:
    """A fact as the recommendation engine sees it."""

    fact_id: int | str
    key: str
    value: dict
    display_text: str
    status: str  # verified | unverified
    source_type: str
    source_name: str | None
    source_url: str

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def facts_by_key(facts: list[FactView]) -> dict[str, FactView]:
    """One fact per key: verified beats unverified; first wins otherwise."""
    out: dict[str, FactView] = {}
    for f in facts:
        if f.status == "rejected":
            continue
        current = out.get(f.key)
        if current is None or (f.verified and not current.verified):
            out[f.key] = f
    return out


# Sensible defaults when no typical_visit_minutes fact exists (used by the itinerary planner).
DEFAULT_VISIT_MINUTES = {
    "religious": 45, "historical": 75, "cultural": 75, "wildlife": 150, "nature": 90, "parks_gardens": 60,
    "adventure": 120, "shopping": 60, "food": 60, "entertainment": 90, "beaches": 90, "waterfalls": 75,
}
