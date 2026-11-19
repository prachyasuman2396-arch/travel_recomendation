"""Shared helpers for provider HTTP clients."""

from __future__ import annotations

import email.utils
import time

import httpx

from app.core.errors import ExternalServiceError, RateLimitedError


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(value)
        return max(0.0, parsed.timestamp() - time.time()) if parsed else None


def raise_for_provider_status(response: httpx.Response, provider: str) -> None:
    """Translate HTTP status codes into retryable / permanent application errors."""
    status = response.status_code
    if status < 400:
        return
    if status == 429:
        raise RateLimitedError(
            f"{provider} rate limit exceeded",
            provider=provider,
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
            details={"body": response.text[:300]},
        )
    if status in (401, 403):
        raise ExternalServiceError(
            f"{provider} rejected the credentials (HTTP {status})", provider=provider, status=status
        )
    retryable = status >= 500 or status == 408
    raise ExternalServiceError(
        f"{provider} returned HTTP {status}", provider=provider, retryable=retryable, status=status,
        details={"body": response.text[:300]},
    )


async def send(client: httpx.AsyncClient, request: httpx.Request, provider: str) -> httpx.Response:
    """Send a request, mapping transport failures to retryable provider errors."""
    try:
        response = await client.send(request)
    except httpx.TimeoutException as exc:
        raise ExternalServiceError(f"{provider} request timed out", provider=provider, retryable=True) from exc
    except httpx.TransportError as exc:
        raise ExternalServiceError(f"{provider} network error: {type(exc).__name__}",
                                   provider=provider, retryable=True) from exc
    raise_for_provider_status(response, provider)
    return response
