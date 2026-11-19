"""Background agents that keep the knowledge base fresh.

* ``EmbeddingIndexer`` — (re)embeds places whose descriptive document changed (needs Gemini key)
* ``FreshnessAgent``   — reloads the curated festival calendar/facts, re-checks Google business
                         status for stale places (live only), and purges expired provider content
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.services.agents.fact_extraction import AgentReport
from app.services.semantic.search import GeminiEmbedder, document_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingTarget:
    poi_id: str
    document: str
    current_hash: str | None


class EmbeddingStore(Protocol):
    def embedding_targets(self, model: str) -> list[EmbeddingTarget]: ...
    def save_embedding(self, poi_id: str, model: str, vector: list[float], doc_hash: str) -> None: ...


class EmbeddingIndexer:
    def __init__(self, store: EmbeddingStore, embedder: GeminiEmbedder | None) -> None:
        self.store = store
        self.embedder = embedder

    async def run(self) -> AgentReport:
        report = AgentReport("embedding_indexer", notes=[])
        if self.embedder is None:
            report.notes.append("No embedding provider configured (SEMANTIC_BACKEND=gemini + GEMINI_API_KEY); "
                                "requests use offline TF-IDF matching instead.")
            return report
        model = f"{self.embedder.model}@{self.embedder.dimensions}"
        todo = [t for t in self.store.embedding_targets(model) if document_hash(t.document) != t.current_hash]
        report.processed = len(todo)
        if not todo:
            return report
        vectors = await self.embedder.embed([t.document for t in todo], "RETRIEVAL_DOCUMENT")
        for target, vector in zip(todo, vectors, strict=True):
            self.store.save_embedding(target.poi_id, model, vector, document_hash(target.document))
        report.accepted = len(todo)
        return report


class StatusChecker(Protocol):
    async def get_status(self, place_id: str) -> dict: ...


class FreshnessStore(Protocol):
    def stale_google_places(self, older_than_days: int, limit: int) -> list[tuple[str, str]]: ...
    def update_google_status(self, poi_id: str, place_id: str, status: str | None,
                             opening_hours: dict | None) -> None: ...
    def purge_expired(self) -> dict: ...


class FreshnessAgent:
    def __init__(self, store: FreshnessStore, checker: StatusChecker | None, reload_curated=None) -> None:
        self.store = store
        self.checker = checker
        self.reload_curated = reload_curated  # async callable that reloads curated facts + events

    async def run(self, older_than_days: int = 14, limit: int = 100) -> AgentReport:
        report = AgentReport("freshness", notes=[])
        if self.reload_curated is not None:
            _, stats = await self.reload_curated()
            report.notes.append(f"curated reloaded: {stats.inserted} new, {stats.updated} updated")
        if self.checker is None:
            report.notes.append("Google status checks skipped (no PLACES_API_KEY)")
        else:
            for poi_id, place_id in self.store.stale_google_places(older_than_days, limit):
                report.processed += 1
                try:
                    data = await self.checker.get_status(place_id)
                except Exception as exc:  # one failure must not stop the sweep
                    report.errors += 1
                    report.notes.append(f"{poi_id}: {type(exc).__name__}")
                    continue
                self.store.update_google_status(poi_id, place_id, data.get("businessStatus"),
                                                data.get("regularOpeningHours"))
                if data.get("businessStatus") not in (None, "OPERATIONAL"):
                    report.accepted += 1
                    report.notes.append(f"{poi_id}: {data.get('businessStatus')}")
        purged = self.store.purge_expired()
        report.notes.append(f"purged: {purged}")
        logger.info("agent_finished", extra=report.as_dict())
        return report
