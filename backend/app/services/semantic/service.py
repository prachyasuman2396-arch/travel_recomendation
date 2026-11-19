"""Score candidates by how well they match the traveller's free text."""

from __future__ import annotations

import logging

from app.core.cache import TTLCache
from app.services.recommendation.types import CandidateRow
from app.services.semantic.search import (
    GeminiEmbedder,
    build_poi_document,
    cosine_scores,
    expand_query,
    tfidf_scores,
)

logger = logging.getLogger(__name__)


def row_document(row: CandidateRow) -> str:
    aspects = (row.sentiment or {}).get("aspects") if row.sentiment else None
    return build_poi_document(row.name, row.categories, row.description, [f.display_text for f in row.facts],
                              row.wiki_summary, aspects, row.review_snippets, row.alt_names)


class SemanticService:
    def __init__(self, embedder: GeminiEmbedder | None = None) -> None:
        self.embedder = embedder
        self._query_cache: TTLCache[list[float]] = TTLCache(3600, max_items=2000)

    async def scores(self, query: str | None, rows: list[CandidateRow]) -> tuple[dict[str, float], str]:
        """Returns ({poi_id: 0..1}, method). Empty dict when there is no free text to match."""
        if not query or not query.strip() or not rows:
            return {}, "none"
        with_vectors = {r.poi_id: r.embedding for r in rows if r.embedding}
        if self.embedder is not None and with_vectors:
            try:
                key = f"{self.embedder.model}:{query.strip().lower()}"
                qvec = self._query_cache.get(key)
                if qvec is None:
                    qvec = (await self.embedder.embed([expand_query(query)], "RETRIEVAL_QUERY"))[0]
                    self._query_cache.set(key, qvec)
                return cosine_scores(qvec, with_vectors), f"embeddings:{self.embedder.model}"
            except Exception as exc:  # semantic matching is a soft signal; degrade to lexical
                logger.warning("semantic_embeddings_failed", extra={"error": str(exc)})
        return tfidf_scores(query, {r.poi_id: row_document(r) for r in rows}), "tfidf"
