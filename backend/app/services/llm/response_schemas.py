"""Shared structured-output schema for every provider.

The JSON Schema is written by hand (not generated from Pydantic) so it stays
inside the subset that both Gemini ``responseJsonSchema`` and Groq strict
``json_schema`` accept: no $ref, every property required, additionalProperties false.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

WeatherSuitability = Literal["suitable", "acceptable", "unsuitable", "unknown"]


class LLMRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    poi_id: str = Field(min_length=1, max_length=20)
    rank: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=800, validation_alias=AliasChoices("reason", "explanation", "description"))
    matched_preferences: list[str] = Field(default_factory=list, max_length=10)
    advantages: list[str] = Field(default_factory=list, max_length=8)
    considerations: list[str] = Field(default_factory=list, max_length=8)
    weather_suitability: WeatherSuitability = "unknown"
    review_insights: list[str] = Field(default_factory=list, max_length=6)
    cited_fact_ids: list[str] = Field(default_factory=list, max_length=12)


class LLMRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendations: list[LLMRecommendation] = Field(default_factory=list, max_length=30)
    summary: str = Field(default="", max_length=1200)


_STR_LIST = {"type": "array", "items": {"type": "string"}}

RECOMMENDATION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "poi_id": {"type": "string", "description": "Exactly one of the supplied candidate ids"},
                    "rank": {"type": "integer", "description": "1 = best match"},
                    "reason": {"type": "string"},
                    "matched_preferences": _STR_LIST,
                    "advantages": _STR_LIST,
                    "considerations": _STR_LIST,
                    "weather_suitability": {"type": "string",
                                            "enum": ["suitable", "acceptable", "unsuitable", "unknown"]},
                    "review_insights": _STR_LIST,
                    "cited_fact_ids": {"type": "array", "items": {"type": "string"},
                                       "description": "ids of this candidate's facts the explanation relies on"},
                },
                "required": ["poi_id", "rank", "reason", "matched_preferences", "advantages", "considerations",
                             "weather_suitability", "review_insights", "cited_fact_ids"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["recommendations", "summary"],
    "additionalProperties": False,
}
