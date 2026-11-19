"""Deterministic lexicon sentiment baseline (default backend).

Why a lexicon by default: review volumes per POI are small (Yelp gives ≤3
excerpts), it needs no model download or GPU, and it is fully explainable.
Switch to the Transformer backend with SENTIMENT_BACKEND=transformers.
Handles negation ("not clean") and simple intensifiers ("very crowded").
"""

from __future__ import annotations

import math
import re

from app.services.sentiment.base import SentimentResult, label_for

POSITIVE = {
    "amazing": 2.5, "awesome": 2.5, "beautiful": 2.5, "breathtaking": 3, "best": 2.5, "calm": 2, "clean": 2,
    "delicious": 2.5, "enjoy": 2, "enjoyed": 2, "excellent": 3, "fantastic": 3, "fascinating": 2.5,
    "fresh": 1.5, "friendly": 2, "fun": 2, "good": 1.5, "great": 2.5, "helpful": 2, "informative": 2,
    "interesting": 1.5, "lovely": 2.5, "must": 1, "nice": 1.5, "peaceful": 2.5, "pleasant": 2, "quiet": 1.5,
    "recommend": 2, "safe": 1.5, "scenic": 2.5, "serene": 2.5, "spacious": 1.5, "stunning": 3, "tasty": 2,
    "value": 1, "well": 1, "wonderful": 3, "worth": 2, "authentic": 2, "decent": 1, "maintained": 1.5,
    "accessible": 1.5, "comfortable": 1.5, "relaxing": 2,
}
NEGATIVE = {
    "awful": -3, "bad": -2, "boring": -2, "crowded": -2, "dirty": -2.5, "disappointing": -2.5, "dusty": -1.5,
    "expensive": -1.5, "filthy": -3, "horrible": -3, "litter": -2, "long": -0.5, "noisy": -1.5,
    "overcrowded": -2.5, "overwhelming": -2, "poor": -2, "poorly": -2, "pushed": -1.5, "rude": -2.5,
    "scam": -3, "slow": -1.5, "smelly": -2.5, "terrible": -3, "unsafe": -3, "worst": -3, "average": -0.5,
    "limited": -1, "steep": -0.5, "queue": -0.5, "difficult": -1.5, "broken": -2, "unclean": -2.5,
    "inaccessible": -2, "rush": -1,
}
NEGATIONS = {"not", "no", "never", "hardly", "isnt", "wasnt", "dont", "didnt", "cant", "nothing"}
INTENSIFIERS = {"very": 1.5, "really": 1.4, "extremely": 1.8, "so": 1.3, "too": 1.3, "super": 1.5}
_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?")
_SENTENCE = re.compile(r"[.!?;,]+")


def score_text(text: str) -> float:
    total, hits = 0.0, 0
    # Negation/intensifier scope never crosses sentence or clause punctuation.
    for segment in _SENTENCE.split(text.lower()):
        seg_total, seg_hits = _score_segment([t.replace("'", "") for t in _TOKEN.findall(segment)])
        total += seg_total
        hits += seg_hits
    if hits == 0:
        return 0.0
    # Normalise to [-1, 1]; sqrt dampens long reviews piling up small words.
    return max(-1.0, min(1.0, total / (3.0 * math.sqrt(hits))))


def _score_segment(tokens: list[str]) -> tuple[float, int]:
    total, hits = 0.0, 0
    for i, tok in enumerate(tokens):
        value = POSITIVE.get(tok) or NEGATIVE.get(tok)
        if value is None:
            continue
        window = tokens[max(0, i - 3):i]
        if any(w in NEGATIONS for w in window):
            value *= -0.75
        if i > 0 and tokens[i - 1] in INTENSIFIERS:
            value *= INTENSIFIERS[tokens[i - 1]]
        total += value
        hits += 1
    return total, hits


class LexiconSentimentAnalyzer:
    name = "lexicon"
    version = "v1"

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        results = []
        for text in texts:
            s = score_text(text or "")
            results.append(SentimentResult(label_for(s), round(s, 3)))
        return results
