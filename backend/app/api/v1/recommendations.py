from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_engine
from app.core.config import Settings, get_settings
from app.schemas.common import ErrorResponse
from app.schemas.preference import PreferenceProfile
from app.schemas.recommendation import (
    ProviderInfo,
    ProvidersResponse,
    RecommendationResult,
    RefineRequest,
    RefineResponse,
)
from app.services.llm.llm_factory import describe_providers
from app.services.recommendation.engine import RecommendationEngine

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("", response_model=RecommendationResult,
             responses={503: {"model": ErrorResponse, "description": "All LLM providers unavailable"}})
async def recommend(profile: PreferenceProfile, engine: RecommendationEngine = Depends(get_engine)):
    """Rank destinations near the given location for this preference profile."""
    return await engine.recommend(profile)


@router.post("/refine", response_model=RefineResponse,
             responses={503: {"model": ErrorResponse, "description": "All LLM providers unavailable"}})
async def refine(body: RefineRequest, engine: RecommendationEngine = Depends(get_engine)):
    """Conversational refinement: 'fewer temples', 'only 2 hours', 'kuch khana bhi' -> updated profile + results."""
    profile, changes = await engine.refine(body.profile, body.message)
    return RefineResponse(profile=profile, changes=changes, result=await engine.recommend(profile))


@router.get("/providers", response_model=ProvidersResponse)
def providers(settings: Settings = Depends(get_settings)):
    return ProvidersResponse(
        providers=[ProviderInfo(**p) for p in describe_providers(settings)],
        fallback_enabled=settings.llm_fallback_enabled,
        deterministic_fallback_enabled=settings.recommendation_deterministic_fallback,
    )
