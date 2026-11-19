from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Label = Literal["positive", "neutral", "negative"]


@dataclass(frozen=True)
class SentimentResult:
    label: Label
    score: float  # -1 (very negative) .. 1 (very positive)


class SentimentAnalyzer(Protocol):
    name: str
    version: str

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]: ...


def label_for(score: float, threshold: float = 0.15) -> Label:
    if score >= threshold:
        return "positive"
    if score <= -threshold:
        return "negative"
    return "neutral"
