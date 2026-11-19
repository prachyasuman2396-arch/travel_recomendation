"""Per-client request budget for the expensive endpoints (each recommendation can cost LLM tokens).

In-process fixed-window counter: no extra dependency, correct for ONE uvicorn worker. With several workers
or instances the budget is per process — put the limit in Redis (or at the proxy / API gateway) then.
"""

from __future__ import annotations

import math
import threading
import time

from fastapi import Depends, Request, Response

from app.core.config import Settings, get_settings
from app.core.errors import AppError


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limited"


class FixedWindowLimiter:
    def __init__(self, window_seconds: float = 60.0, max_keys: int = 50_000) -> None:
        self.window = window_seconds
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, int]] = {}  # key -> (window_start, count)

    def hit(self, key: str, limit: int) -> tuple[bool, int, float]:
        """Count one request. Returns ``(allowed, remaining, seconds_until_reset)``."""
        now = time.monotonic()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            if len(self._hits) > self.max_keys:  # bound memory under a flood of distinct IPs
                cutoff = now - self.window
                self._hits = {k: v for k, v in self._hits.items() if v[0] >= cutoff}
            reset = max(0.0, self.window - (now - start))
            return count <= limit, max(0, limit - count), reset

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_key(request: Request, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        # Use the RIGHTMOST entry: it is the one your proxy appended. Earlier entries are client-supplied
        # and trivially spoofed to dodge the limit.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if last := forwarded.split(",")[-1].strip():
            return last
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> None:
    """FastAPI dependency: ``dependencies=[Depends(rate_limit)]``. The limiter lives on ``app.state`` (one per app)."""
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    limiter: FixedWindowLimiter = request.app.state.rate_limiter
    allowed, remaining, reset = limiter.hit(f"{request.url.path}:{client_key(request, settings.trust_forwarded_for)}",
                                             limit)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise TooManyRequestsError(
            f"Too many requests: limit is {limit} per minute. Try again in {math.ceil(reset)} s.",
            details={"retry_after_seconds": math.ceil(reset)})
