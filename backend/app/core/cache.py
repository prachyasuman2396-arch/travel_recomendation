"""Tiny in-process TTL cache.

Deliberately simple (no Redis): the app runs as one process initially, and the
cached items (weather, provider lookups) are cheap to recompute on a miss.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(self, ttl_seconds: float, max_items: int = 1024) -> None:
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, V]] = OrderedDict()

    def get(self, key: str) -> V | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() >= expires_at:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: V) -> None:
        if self.ttl <= 0:
            return
        self._items[key] = (time.monotonic() + self.ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()
