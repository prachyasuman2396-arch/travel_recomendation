"""User preference profile — validated input for every recommendation request.

Preferences are supplied per request (and optionally saved on a user profile).
There is deliberately no interaction-history-based profiling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.india.rules import VisitorProfile

Interest = Literal[
    "nature", "adventure", "historical", "religious", "cultural", "food", "entertainment", "shopping",
    "wildlife", "photography", "beaches", "waterfalls", "parks_gardens",
]
TravelType = Literal["solo", "couple", "family", "friends", "group"]
Budget = Literal["low", "medium", "high"]


class PreferenceProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    interests: list[Interest] = Field(default_factory=list, max_length=13)
    natural_language: str | None = Field(default=None, max_length=1000)
    max_distance_km: float = Field(20, gt=0, le=200)
    available_hours: float = Field(4, ge=0.5, le=24)
    budget: Budget | None = None
    travel_type: TravelType | None = None
    constraints: list[str] = Field(default_factory=list, max_length=10)
    # Client's local date-time (with offset) — used for "closed today" checks. Defaults to server time.
    client_datetime: datetime | None = None
    # Who is travelling (temple entry rules, foreign ticket prices, accessibility, kids, crowds)
    visitor: VisitorProfile = Field(default_factory=VisitorProfile)

    @field_validator("interests")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("constraints")
    @classmethod
    def _short_constraints(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip()[:200] for c in v if c and c.strip()]
        return cleaned

    @property
    def radius_m(self) -> float:
        return self.max_distance_km * 1000.0


class SavedPreferences(BaseModel):
    """What a user profile stores (no location — that is per request)."""

    visitor: VisitorProfile = Field(default_factory=VisitorProfile)
    interests: list[Interest] = Field(default_factory=list, max_length=13)
    natural_language: str | None = Field(default=None, max_length=1000)
    max_distance_km: float = Field(20, gt=0, le=200)
    available_hours: float = Field(4, ge=0.5, le=24)
    budget: Budget | None = None
    travel_type: TravelType | None = None
    constraints: list[str] = Field(default_factory=list, max_length=10)
