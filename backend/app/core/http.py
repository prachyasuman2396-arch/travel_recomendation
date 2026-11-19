"""Shared helpers for provider HTTP clients."""

from __future__ import annotations

import email.utils
import time

import httpx

from app.core.errors import ExternalServiceError, RateLimitedError


def http2_available() -> bool:
    try:
        import h2  # noqa: F401  (installed by httpx[http2])
    except ImportError:
        return False
    return True


def build_async_client(*, user_agent: str, timeout: float, connect_timeout: float = 5.0,
                       max_connections: int = 100, max_keepalive_connections: int = 20,
                       keepalive_expiry: float = 30.0, http2: bool = True,
                       follow_redirects: bool = True) -> httpx.AsyncClient:
    """The one outbound HTTP client: pooled keep-alive connections, HTTP/2 when ``h2`` is installed.

    Create it ONCE per process (FastAPI lifespan) or once per event loop (Streamlit background loop) and
    share it — a client per request repeats DNS + TCP + TLS for every call, which costs ~100-300 ms per
    host on a cold connection.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(connect_timeout, timeout)),
        limits=httpx.Limits(max_connections=max_connections,
                            max_keepalive_connections=max_keepalive_connections,
                            keepalive_expiry=keepalive_expiry),
        http2=http2 and http2_available(),
        headers={"User-Agent": user_agent},
        follow_redirects=follow_redirects,
    )


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
