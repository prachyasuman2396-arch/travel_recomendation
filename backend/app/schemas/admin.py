from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.preference import SavedPreferences


class IngestionRequest(BaseModel):
    latitude: float = Field(20.2961, ge=-90, le=90)
    longitude: float = Field(85.8245, ge=-180, le=180)
    radius_km: float = Field(25, gt=0, le=100)
    mode: Literal["auto", "live", "mock"] = "auto"
    fetch_reviews: bool = True


class IngestionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: str
    source: str
    mode: str
    status: str
    params: dict
    started_at: datetime | None
    completed_at: datetime | None
    records_fetched: int
    records_inserted: int
    records_updated: int
    records_rejected: int
    records_flagged: int
    error_summary: str | None
    error_details: dict | None


class MatchCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider: str
    external_id: str
    candidate_poi_id: str
    score: float
    score_breakdown: dict
    source_payload: dict
    status: str


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Simple shape check (no extra dependency); email is optional and only used to identify a profile.
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    preferences: SavedPreferences = Field(default_factory=SavedPreferences)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    name: str
    email: str | None
    preferences: dict
    created_at: datetime
    updated_at: datetime
