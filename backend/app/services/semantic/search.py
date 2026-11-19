"""Semantic matching of the traveller's free text against place descriptions.

Why no vector database: retrieval is geo-filtered first, so we only ever compare against a few
hundred candidates. Cosine similarity in NumPy over vectors stored as float[] in Postgres is enough.

Backends:
  * ``gemini`` — gemini-embedding-001 (RETRIEVAL_DOCUMENT for places, RETRIEVAL_QUERY for the request)
  * ``tfidf``  — offline fallback: TF-IDF over the candidate documents with an Indian travel
                 vocabulary expansion ("peaceful" ~ "serene", "shant", "sukoon"...). Lexical, so
                 weaker than embeddings, and reported as such in the response.
"""

from __future__ import annotations

import hashlib
import re

import httpx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from app.core.http import send
from app.core.retry import with_retries

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Query-side expansion: travellers' words (English, Hinglish) -> words that appear in place documents.
TRAVEL_SYNONYMS: dict[str, str] = {
    "peaceful": "peaceful serene quiet calm tranquil", "quiet": "quiet peaceful calm serene",
    "shant": "peaceful quiet calm", "sukoon": "peaceful calm relaxing", "shanti": "peaceful calm",
    "nature": "nature green forest garden lake trees scenic", "green": "green garden forest trees",
    "prakriti": "nature green forest", "hariyali": "green garden trees",
    "crowd": "crowded crowds busy", "bheed": "crowded crowds busy",
    "history": "history historical ancient heritage century caves", "itihas": "history historical ancient",
    "old": "ancient historical century old", "purana": "ancient old historical",
    "temple": "temple shrine mandir worship", "mandir": "temple shrine worship",
    "spiritual": "temple shrine worship pilgrimage meditation", "photography": "photography scenic views sunset",
    "sunset": "sunset evening views scenic", "view": "views viewpoint scenic hill",
    "kids": "children kids family zoo science", "bachche": "children kids family",
    "family": "family children kids", "adventure": "adventure trek hike climbing caves",
    "water": "lake river dam waterfall beach", "paani": "lake river water",
    "food": "food cuisine thali restaurant", "khana": "food cuisine restaurant",
    "shopping": "shopping market handicraft mall", "craft": "handicraft craft applique market",
    "wildlife": "wildlife animals zoo sanctuary elephants tigers", "animals": "animals zoo wildlife",
    "sagar": "lake tank water", "talab": "lake pond tank", "jheel": "lake", "nadi": "river",
    "kinare": "lake river beach shore", "pahad": "hill hills viewpoint", "jharna": "waterfall falls",
    "samundar": "beach sea", "samudra": "beach sea", "kila": "fort historical", "qila": "fort historical",
    "gufa": "caves cave", "gumpha": "caves cave", "bagicha": "garden park", "bagh": "garden park",
}
_TOKEN = re.compile(r"[a-z]+")


def expand_query(text: str) -> str:
    words = _TOKEN.findall(text.lower())
    return " ".join([*words, *(TRAVEL_SYNONYMS[w] for w in words if w in TRAVEL_SYNONYMS)])


def document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tfidf_scores(query: str, docs: dict[str, str]) -> dict[str, float]:
    if not query.strip() or not docs:
        return {}
    ids = list(docs)
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1)
    try:
        matrix = vectorizer.fit_transform([docs[i] for i in ids] + [expand_query(query)])
    except ValueError:  # empty vocabulary
        return {}
    q = matrix[-1]
    sims = (matrix[:-1] @ q.T).toarray().ravel()
    top = float(sims.max()) if sims.size and sims.max() > 0 else 1.0
    return {i: round(float(s) / top, 4) for i, s in zip(ids, sims, strict=True)}


def cosine_scores(query_vec: list[float], vectors: dict[str, list[float]]) -> dict[str, float]:
    if not vectors:
        return {}
    ids = list(vectors)
    m = np.asarray([vectors[i] for i in ids], dtype=np.float32)
    q = np.asarray(query_vec, dtype=np.float32)
    denom = np.linalg.norm(m, axis=1) * (np.linalg.norm(q) or 1.0)
    sims = (m @ q) / np.where(denom == 0, 1.0, denom)
    # Map cosine (-1..1, in practice ~0.3..0.9 for this model) to 0..1 relative within the candidate set.
    lo, hi = float(sims.min()), float(sims.max())
    span = (hi - lo) or 1.0
    return {i: round((float(s) - lo) / span, 4) for i, s in zip(ids, sims, strict=True)}


class GeminiEmbedder:
    """gemini-embedding-001 over REST (same HTTP stack and retry policy as everything else)."""

    def __init__(self, api_key: str, http: httpx.AsyncClient, model: str = "gemini-embedding-001",
                 dimensions: int = 768) -> None:
        self._key, self._http, self.model, self.dimensions = api_key, http, model, dimensions

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), 100):  # batchEmbedContents accepts batches of requests
            batch = texts[start:start + 100]
            body = {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t[:8000]}]},
                                  "taskType": task_type, "outputDimensionality": self.dimensions} for t in batch]}

            async def call(body: dict = body) -> dict:
                request = self._http.build_request(
                    "POST", f"{GEMINI_BASE}/models/{self.model}:batchEmbedContents", json=body,
                    headers={"x-goog-api-key": self._key})
                return (await send(self._http, request, "gemini_embeddings")).json()

            payload = await with_retries(call, attempts=3, operation="gemini.embed")
            out.extend(e["values"] for e in payload.get("embeddings", []))
        if len(out) != len(texts):
            raise ValueError("embedding count mismatch")
        return out


def build_poi_document(name: str, categories: list[str], description: str | None, facts: list[str],
                       wiki_summary: str | None, aspects: dict | None, review_snippets: list[str],
                       alt_names: list[str] | None = None) -> str:
    """The text that represents a place for semantic matching (facts + descriptions + review signals)."""
    parts = [name, " ".join(alt_names or []), " ".join(c.replace("_", " ") for c in categories),
             description or "", wiki_summary or "", " ".join(facts)]
    if aspects:
        parts.append(" ".join(f"{k} {v.get('label', '')}" for k, v in aspects.items()))
    parts.extend(review_snippets[:3])
    return " ".join(p for p in parts if p).strip()
