"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import AppServices, build_services, get_services
from app.api.v1 import admin, events, pois, recommendations, users, weather
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, request_id_var
from app.core.rate_limit import FixedWindowLimiter
from app.database.session import check_database, get_db

logger = logging.getLogger("app")


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": details or {}, "request_id": request_id_var.get()}})


def create_app(settings: Settings | None = None, services: AppServices | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services or build_services(settings)
        logger.info("startup", extra={"env": settings.app_env, "llm_provider": settings.llm_provider,
                                      "integrations": settings.provider_status()})
        yield
        await app.state.services.http.aclose()

    app = FastAPI(title="Tourist Place Recommender API", version="0.1.0", lifespan=lifespan,
                  description="Context-aware tourist recommendations: PostGIS retrieval + Gemini/Groq ranking.")
    app.state.rate_limiter = FixedWindowLimiter(window_seconds=60.0)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type",
                                                                            "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid[:64])
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid[:64]
        logger.info("request", extra={"method": request.method, "path": request.url.path,
                                      "status": response.status_code, "request_id": rid[:64],
                                      "ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.warning("app_error", extra={"code": exc.code, "error": exc.message})
        response = _error(exc.status_code, exc.code, exc.message, exc.details)
        if (retry := exc.details.get("retry_after_seconds")) is not None:
            response.headers["Retry-After"] = str(retry)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _error(422, "validation_error", "Request validation failed", {"errors": errors})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled_error")
        return _error(500, "internal_error", "An unexpected error occurred")

    def _database_status(db: Session) -> dict:
        try:
            return check_database(db)
        except SQLAlchemyError as exc:
            return {"connected": False, "error": type(exc).__name__}
        except Exception as exc:  # deliberate boundary: a probe must answer, never 500
            logger.warning("health_db_check_failed", extra={"error_type": type(exc).__name__}, exc_info=True)
            return {"connected": False, "error": type(exc).__name__}

    @app.get("/health", tags=["health"])
    def health(db: Session = Depends(get_db), svc: AppServices = Depends(get_services)):
        """Human-readable status page (always 200; use /readyz for orchestration)."""
        database = _database_status(db)
        return {"status": "ok" if database.get("connected") else "degraded", "database": database,
                "llm_chain": svc.llm.provider_names if svc.llm else [],
                "live_integrations": settings.provider_status()}

    @app.get("/healthz", tags=["health"])
    def healthz():
        """Liveness: the process is up and serving. No dependencies, so a DB outage never restarts the pod."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz(db: Session = Depends(get_db), svc: AppServices = Depends(get_services)):
        """Readiness: 503 until the database answers, so the load balancer only routes to working instances.
        LLM providers are NOT required: the deterministic fallback still serves results without them."""
        database = _database_status(db)
        ready = bool(database.get("connected"))
        body = {"status": "ready" if ready else "not_ready", "database": database,
                "llm_configured": svc.llm is not None,
                "deterministic_fallback": settings.recommendation_deterministic_fallback}
        return JSONResponse(status_code=200 if ready else 503, content=body)

    api = APIRouter(prefix="/api/v1")
    for module in (pois, recommendations, events, users, weather, admin):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
