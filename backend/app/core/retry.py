"""One retry policy for every outbound call (data providers, weather, LLMs).

Only errors that declare themselves retryable are retried — e.g. 429/5xx/timeouts.
Authentication failures and bad requests fail immediately.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Module-level so tests can set them to 0 (no real sleeping in unit tests).
DEFAULT_INITIAL_WAIT = 0.5
DEFAULT_MAX_WAIT = 8.0
DEFAULT_JITTER = 0.5


def is_retryable(exc: BaseException) -> bool:
    return bool(getattr(exc, "retryable", False))


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    operation: str = "external_call",
) -> T:
    """Run ``fn`` with exponential backoff + jitter; re-raise the last error."""

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_exponential_jitter(initial=DEFAULT_INITIAL_WAIT, max=DEFAULT_MAX_WAIT, jitter=DEFAULT_JITTER),
        retry=retry_if_exception(is_retryable),
        reraise=True,
    ):
        with attempt:
            if attempt.retry_state.attempt_number > 1:
                logger.warning(
                    "retrying", extra={"operation": operation, "attempt": attempt.retry_state.attempt_number}
                )
            return await fn()
    raise RuntimeError("unreachable")  # pragma: no cover - AsyncRetrying always returns or raises
