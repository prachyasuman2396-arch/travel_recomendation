"""One long-lived asyncio event loop (daemon thread) for the Streamlit process.

Why: Streamlit runs each user's script in its own thread with no event loop. The old ``asyncio.run`` per
call created a new loop *and* a new ``httpx.AsyncClient`` every time, so no connection was ever reused
(DNS + TCP + TLS again for Wikipedia, Open-Meteo, Gemini, Groq on every click) and nothing could be shared
between calls. ``nest_asyncio`` is not the fix: it patches the loop globally, is not thread-safe, and breaks
anyio/httpx cancellation semantics.

Here every coroutine is submitted to ONE loop with ``run_coroutine_threadsafe``. That loop owns ONE pooled
client, so keep-alive connections survive across reruns and across user sessions. Script threads only block
on a ``concurrent.futures.Future`` (with a timeout), never on a loop of their own.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx

T = TypeVar("T")
logger = logging.getLogger(__name__)


class BackgroundLoop:
    def __init__(self, client_factory: Callable[[], httpx.AsyncClient], *, name: str = "radius-async") -> None:
        self._factory = client_factory
        self._name = name
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._http: httpx.AsyncClient | None = None

    # ----------------------------------------------------------------- lifecycle
    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            threading.Thread(target=_run, name=self._name, daemon=True).start()
            ready.wait(timeout=5)
            self._loop = loop
            self._http = None
            atexit.register(self.close)
            return loop

    def close(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        if self._http is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._http.aclose(), loop).result(timeout=5)
            except (TimeoutError, RuntimeError) as exc:
                logger.warning("background_loop_client_close_failed", extra={"error": type(exc).__name__})
        loop.call_soon_threadsafe(loop.stop)

    # ----------------------------------------------------------------- public
    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = 90.0) -> T:
        """Run ``coro`` on the shared loop and block the calling (script) thread for the result."""
        loop = self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()  # don't leave work running for a user who has moved on
            raise

    def is_running_here(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def shared_client(self) -> httpx.AsyncClient | None:
        """The pooled client, but only for code running ON this loop (a client is bound to its loop)."""
        if not self.is_running_here():
            return None
        if self._http is None or self._http.is_closed:
            self._http = self._factory()
        return self._http


_ACTIVE: BackgroundLoop | None = None


def install(loop: BackgroundLoop) -> BackgroundLoop:
    """Register the process-wide loop so ``shared_client()`` can find it."""
    global _ACTIVE
    _ACTIVE = loop
    return loop


def shared_client() -> httpx.AsyncClient | None:
    return _ACTIVE.shared_client() if _ACTIVE is not None else None
