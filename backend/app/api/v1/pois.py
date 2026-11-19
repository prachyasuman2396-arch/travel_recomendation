from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import AppServices, get_services
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.database.session import get_db
from app.repositories.poi_repository import PoiRepository
from app.schemas.common import Page
from app.schemas.poi import (
    PhotoOut,
    PoiDetail,
    PoiDocumentOut,
    PoiFactOut,
    PoiSummary,
    RatingOut,
    ReviewOut,
    SourceOut,
)
from app.services.data_collection.places_client import GooglePlacesClient

router = APIRouter(prefix="/pois", tags=["pois"])


def _repo(db: Session = Depends(get_db)) -> PoiRepository:
    return PoiRepository(db)


@router.get("", response_model=Page[PoiSummary])
def list_pois(category: str | None = None, q: str | None = Query(None, max_length=100),
              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
              repo: PoiRepository = Depends(_repo)):
    items, total = repo.list(category=category, q=q, limit=limit, offset=offset)
    return Page(items=[PoiSummary.model_validate(p) for p in items], total=total, limit=limit, offset=offset)


@router.get("/nearby", response_model=Page[PoiSummary])
def nearby(latitude: float = Query(ge=-90, le=90), longitude: float = Query(ge=-180, le=180),
           radius_km: float = Query(10, gt=0, le=200), category: str | None = None,
           limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), repo: PoiRepository = Depends(_repo)):
    rows, total = repo.nearby(latitude, longitude, radius_km * 1000, category=category, limit=limit, offset=offset)
    items = [PoiSummary.model_validate(p).model_copy(update={"distance_km": round(d / 1000, 2)}) for p, d in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{poi_id}", response_model=PoiDetail)
def get_poi(poi_id: str, repo: PoiRepository = Depends(_repo)):
    poi = repo.get(poi_id)
    if poi is None:
        raise NotFoundError(f"POI {poi_id} not found")
    extras = repo.detail_extras(poi_id)
    rating, count = repo.average_rating(poi_id)
    return PoiDetail(
        **PoiSummary.model_validate(poi).model_dump(), description=poi.description, website=poi.website,
        opening_hours=poi.opening_hours, primary_source=poi.primary_source, rating=rating, rating_count=count,
        ratings=[RatingOut.model_validate(r) for r in extras["ratings"]],
        sources=[SourceOut.model_validate(s) for s in extras["sources"]], sentiment=extras["sentiment"],
        review_count=extras["review_count"], photo_count=extras["photo_count"], updated_at=poi.updated_at,
        alt_names=list(poi.alt_names or []),
        facts=[PoiFactOut.model_validate(f) for f in repo.facts(poi_id) if f.status != "rejected"],
        background=PoiDocumentOut.model_validate(doc) if (doc := repo.document(poi_id)) else None,
        google_photo_count=len(repo.photo_refs(poi_id)),
    )


@router.get("/{poi_id}/reviews", response_model=Page[ReviewOut])
def get_reviews(poi_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                repo: PoiRepository = Depends(_repo)):
    if repo.get(poi_id) is None:
        raise NotFoundError(f"POI {poi_id} not found")
    items, total = repo.reviews(poi_id, limit=limit, offset=offset)
    return Page(items=[ReviewOut.model_validate(r) for r in items], total=total, limit=limit, offset=offset)


@router.get("/{poi_id}/photos", response_model=Page[PhotoOut])
def get_photos(poi_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
               repo: PoiRepository = Depends(_repo)):
    if repo.get(poi_id) is None:
        raise NotFoundError(f"POI {poi_id} not found")
    items, total = repo.photos(poi_id, limit=limit, offset=offset)
    return Page(items=[PhotoOut.model_validate(p) for p in items], total=total, limit=limit, offset=offset)


@router.get("/{poi_id}/photo", responses={404: {"description": "No Google photo (e.g. mock data or no key)"}})
async def google_photo(poi_id: str, index: int = Query(0, ge=0, le=9), max_width: int = Query(800, ge=100, le=1600),
                       repo: PoiRepository = Depends(_repo), settings: Settings = Depends(get_settings),
                       services: AppServices = Depends(get_services)):
    """Redirect to a short-lived Google photo URL. The API key never reaches the browser.

    Show the returned ``attributions`` alongside the image, as Google's terms require; the
    detail endpoint exposes them via ``/photo-attributions``.
    """
    refs = repo.photo_refs(poi_id)
    if index >= len(refs) or not settings.places_api_key:
        raise NotFoundError("No Google photo available for this place")
    client = GooglePlacesClient(settings.places_api_key.get_secret_value(), services.http)
    uri = await client.get_photo_uri(refs[index]["name"], max_width=max_width)
    if not uri:
        raise NotFoundError("No Google photo available for this place")
    return RedirectResponse(uri, status_code=307)


@router.get("/{poi_id}/photo-attributions")
def photo_attributions(poi_id: str, repo: PoiRepository = Depends(_repo)) -> list[dict]:
    return [{"index": i, "attributions": r.get("attributions", [])} for i, r in enumerate(repo.photo_refs(poi_id))]
