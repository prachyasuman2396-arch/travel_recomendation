"""Application error hierarchy. API handlers map these to HTTP responses."""

from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class InvalidRequestError(AppError):
    status_code = 422
    code = "invalid_request"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"


class ServiceNotConfiguredError(AppError):
    """A live integration was requested but its credentials are not configured."""

    status_code = 503
    code = "service_not_configured"


class ExternalServiceError(AppError):
    """An upstream data provider failed after retries."""

    status_code = 502
    code = "external_service_error"

    def __init__(self, message: str, *, provider: str, retryable: bool = False,
                 status: int | None = None, details: dict | None = None) -> None:
        super().__init__(message, details=details)
        self.provider = provider
        self.retryable = retryable
        self.status = status


class RateLimitedError(ExternalServiceError):
    def __init__(self, message: str, *, provider: str, retry_after: float | None = None,
                 details: dict | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, status=429, details=details)
        self.retry_after = retry_after


class RecommendationUnavailableError(AppError):
    """Every configured LLM provider failed; we refuse to fabricate a result."""

    status_code = 503
    code = "recommendation_unavailable"
