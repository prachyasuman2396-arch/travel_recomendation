"""Lightweight FastAPI server bridging the React frontend to the in-memory Lite engine.

Runs on port 8000 so the React frontend (http://localhost:5173) works out of the box
without requiring a local PostgreSQL / PostGIS database or Docker container.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.lite.router import Keys
from app.lite.runtime import LiteSettings, Statuses, check_keys, load_area, load_demo_area, recommend, refine
from app.schemas.preference import PreferenceProfile

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"

# Read keys from .env
env = dotenv_values(ROOT / ".env")
gemini_key = env.get("GEMINI_API_KEY") or None
groq_key = env.get("GROQ_API_KEY") or None
google_key = env.get("PLACES_API_KEY") or None
contact_email = env.get("CONTACT_EMAIL") or None
osrm_url = env.get("OSRM_URL") or None

SETTINGS = LiteSettings(data_dir=DATA_DIR, contact_email=contact_email, osrm_url=osrm_url)
KEYS = Keys(gemini=gemini_key, groq=groq_key, google=google_key)

app = FastAPI(title="Radius Tourist Recommender Lite API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8080", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AREA_CACHE: dict[tuple[float, float, float], Any] = {}
_STATUSES: Statuses | None = None


async def get_statuses() -> Statuses:
    global _STATUSES
    if _STATUSES is None:
        try:
            _STATUSES = await check_keys(KEYS, SETTINGS)
        except Exception:
            _STATUSES = Statuses()
    return _STATUSES


async def get_store_for(lat: float, lon: float, radius_km: float):
    # Check if close to Bhubaneswar demo center
    if abs(lat - 20.2961) < 0.1 and abs(lon - 85.8245) < 0.1 and not gemini_key and not groq_key:
        store, _ = await load_demo_area(SETTINGS)
        return store

    bucket = 10.0 if radius_km <= 10 else 25.0
    key = (round(lat, 2), round(lon, 2), bucket)
    if key in _AREA_CACHE:
        return _AREA_CACHE[key]

    statuses = await get_statuses()
    try:
        store, _ = await load_area(lat, lon, bucket, KEYS, statuses, SETTINGS)
        _AREA_CACHE[key] = store
        return store
    except Exception:
        store, _ = await load_demo_area(SETTINGS)
        return store


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/recommendations/providers")
async def get_providers():
    statuses = await get_statuses()
    providers = []
    if statuses.groq and statuses.groq.ok:
        providers.append({"role": "rank", "provider": "groq", "model": statuses.groq.text_model or "groq-default", "configured": True})
    if statuses.gemini and statuses.gemini.ok:
        providers.append({"role": "fallback", "provider": "gemini", "model": statuses.gemini.text_model or "gemini-default", "configured": True})
    if not providers:
        providers.append({"role": "rank", "provider": "mock", "model": "deterministic", "configured": True})
    return {
        "providers": providers,
        "fallback_enabled": True,
        "deterministic_fallback_enabled": True,
    }


@app.get("/api/v1/events")
async def get_events():
    events_path = DATA_DIR / "curated" / "events_odisha.json"
    if events_path.exists():
        try:
            data = json.loads(events_path.read_text(encoding="utf-8"))
            return data.get("events", [])
        except Exception:
            pass
    return []


@app.get("/api/v1/pois")
async def get_pois(limit: int = 10, offset: int = 0, category: str | None = None, q: str | None = None):
    store, _ = await load_demo_area(SETTINGS)
    pois = list(store.pois.values())
    if category:
        pois = [p for p in pois if p.get("category") == category or category in (p.get("categories") or [])]
    if q:
        ql = q.lower()
        pois = [p for p in pois if ql in p.get("name", "").lower()]

    items = []
    for p in pois[offset : offset + limit]:
        items.append({
            "poi_id": p.get("poi_id") or p.get("external_id") or p.get("name"),
            "name": p.get("name"),
            "category": p.get("category", "historical"),
            "categories": p.get("categories") or [p.get("category", "historical")],
            "latitude": p.get("latitude", 0.0),
            "longitude": p.get("longitude", 0.0),
            "address": p.get("address"),
            "data_origin": p.get("data_origin", "live"),
            "distance_km": None,
        })
    return {"items": items, "total": len(pois), "limit": limit, "offset": offset}


@app.get("/api/v1/pois/{poi_id}")
async def get_poi_detail(poi_id: str):
    store, _ = await load_demo_area(SETTINGS)
    poi = None
    for pid, p in store.pois.items():
        if pid == poi_id or p.get("name") == poi_id:
            poi = p
            break
    if not poi:
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "POI not found"}})

    rating_info = poi.get("ratings") or []
    rating = rating_info[0].get("rating") if rating_info else 4.5
    rating_count = rating_info[0].get("count") if rating_info else 120

    return {
        "poi_id": poi_id,
        "name": poi.get("name"),
        "category": poi.get("category", "historical"),
        "categories": poi.get("categories") or [poi.get("category", "historical")],
        "latitude": poi.get("latitude", 0.0),
        "longitude": poi.get("longitude", 0.0),
        "address": poi.get("address"),
        "data_origin": poi.get("data_origin", "live"),
        "distance_km": None,
        "description": poi.get("description") or poi.get("wiki_summary"),
        "website": poi.get("website"),
        "opening_hours": poi.get("opening_hours"),
        "primary_source": poi.get("primary_source", "osm"),
        "rating": rating,
        "rating_count": rating_count,
        "ratings": [{"source": "community", "rating": rating, "rating_count": rating_count, "fetched_at": datetime.now(timezone.utc).isoformat()}],
        "sources": [],
        "sentiment": {"status": "available"},
        "review_count": rating_count,
        "photo_count": poi.get("photo_count", 5),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "alt_names": poi.get("alt_names", []),
    }


@app.post("/api/v1/recommendations")
async def post_recommendations(profile: PreferenceProfile):
    store = await get_store_for(profile.latitude, profile.longitude, profile.max_distance_km)
    statuses = await get_statuses()
    result, _ = await recommend(store, profile, KEYS, statuses, SETTINGS)
    return result.model_dump()


@app.post("/api/v1/recommendations/refine")
async def post_refine(payload: dict):
    profile_data = payload.get("profile")
    message = payload.get("message", "")
    profile = PreferenceProfile.model_validate(profile_data)
    store = await get_store_for(profile.latitude, profile.longitude, profile.max_distance_km)
    statuses = await get_statuses()
    new_profile, changes, result = await refine(store, profile, message, KEYS, statuses, SETTINGS)
    return {
        "profile": new_profile.model_dump(),
        "changes": changes,
        "result": result.model_dump(),
    }
