"""Hugging Face Transformer sentiment backend (optional; see requirements-ml.txt).

Default model ``nlptown/bert-base-multilingual-uncased-sentiment`` is a
BERT-based 1–5 star classifier — a good fit for review text and multilingual
input. Models with positive/neutral/negative labels are also supported.
The model is loaded lazily on first use.
"""

from __future__ import annotations

from app.services.sentiment.base import SentimentResult, label_for

_THREE_WAY = {"positive": 1.0, "neutral": 0.0, "negative": -1.0,
              "label_2": 1.0, "label_1": 0.0, "label_0": -1.0}


def _to_score(label: str, confidence: float) -> float:
    lab = label.lower()
    if "star" in lab:  # "1 star" .. "5 stars"
        stars = int(lab.split()[0])
        return (stars - 3) / 2.0
    if lab in _THREE_WAY:
        return _THREE_WAY[lab] * confidence
    raise ValueError(f"Unsupported sentiment label from model: {label}")


class TransformerSentimentAnalyzer:
    def __init__(self, model_name: str, batch_size: int = 16) -> None:
        self.name = f"transformers:{model_name}"
        self.version = model_name
        self._model_name = model_name
        self._batch_size = batch_size
        self._pipe = None

    def _pipeline(self):
        if self._pipe is None:
            try:
                from transformers import pipeline
            except ImportError as exc:  # pragma: no cover - depends on optional install
                raise RuntimeError(
                    "SENTIMENT_BACKEND=transformers requires `pip install -r requirements-ml.txt`") from exc
            self._pipe = pipeline("sentiment-analysis", model=self._model_name, truncation=True)
        return self._pipe

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        if not texts:
            return []
        outputs = self._pipeline()(texts, batch_size=self._batch_size)
        results = []
        for out in outputs:
            score = _to_score(out["label"], float(out["score"]))
            results.append(SentimentResult(label_for(score), round(score, 3)))
        return results
