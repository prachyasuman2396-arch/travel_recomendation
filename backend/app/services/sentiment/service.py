"""Review analysis + POI-level aggregation.

* Per-review results are cached by (review_id, model) and text hash, so
  unchanged reviews are never re-analysed (see repositories/sentiment_repository.py).
* POIs with fewer than ``min_reviews`` analysed reviews get
  ``status="insufficient_data"`` instead of a manufactured sentiment.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.services.sentiment.aspects import analyze_aspects
from app.services.sentiment.base import Label, SentimentAnalyzer


@dataclass(frozen=True)
class ReviewAnalysisResult:
    review_id: int
    text_hash: str
    sentiment: Label
    score: float
    aspects: dict[str, Label]


@dataclass
class PoiSentimentSummary:
    status: str  # available | insufficient_data
    reviews_analyzed: int
    overall: Label | None = None
    positive_share: float | None = None
    neutral_share: float | None = None
    negative_share: float | None = None
    aspects: dict[str, dict] = field(default_factory=dict)


class SentimentService:
    def __init__(self, analyzer: SentimentAnalyzer, min_reviews: int = 3) -> None:
        self.analyzer = analyzer
        self.min_reviews = min_reviews

    @property
    def model_name(self) -> str:
        return f"{self.analyzer.name}:{self.analyzer.version}"

    def analyze(self, reviews: list[tuple[int, str, str]]) -> list[ReviewAnalysisResult]:
        """``reviews`` = [(review_id, text, text_hash)] — only texts that need (re)analysis."""
        reviews = [r for r in reviews if r[1] and r[1].strip()]
        overall = self.analyzer.analyze_batch([text for _, text, _ in reviews])
        return [
            ReviewAnalysisResult(rid, h, res.label, res.score, analyze_aspects(text, self.analyzer))
            for (rid, text, h), res in zip(reviews, overall, strict=True)
        ]

    def aggregate(self, results: list[tuple[Label, float, dict[str, Label]]]) -> PoiSentimentSummary:
        n = len(results)
        if n < self.min_reviews:
            return PoiSentimentSummary(status="insufficient_data", reviews_analyzed=n)
        counts = Counter(label for label, _, _ in results)
        mean = sum(score for _, score, _ in results) / n
        overall: Label = "positive" if mean >= 0.15 else "negative" if mean <= -0.15 else "neutral"
        aspect_counts: dict[str, Counter] = defaultdict(Counter)
        for _, _, aspects in results:
            for name, label in aspects.items():
                aspect_counts[name][label] += 1
        aspects = {}
        for name, c in aspect_counts.items():
            mentions = sum(c.values())
            top, top_n = c.most_common(1)[0]
            # Report a direction only when it is the clear majority of mentions.
            label = top if top_n / mentions > 0.5 else "mixed"
            aspects[name] = {"label": label, "mentions": mentions, **dict(c)}
        return PoiSentimentSummary(
            status="available", reviews_analyzed=n, overall=overall,
            positive_share=round(counts["positive"] / n, 3), neutral_share=round(counts["neutral"] / n, 3),
            negative_share=round(counts["negative"] / n, 3), aspects=aspects,
        )
