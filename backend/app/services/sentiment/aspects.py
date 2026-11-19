"""Aspect-level sentiment: keyword-gated, clause-level.

An aspect is only reported when the review text actually mentions it (keyword
hit in a clause). Each mentioning clause is scored with the configured
analyzer, so 'Beautiful garden, but the food facilities are poor' yields
environment=positive and food=negative — and nothing for aspects not mentioned.
"""

from __future__ import annotations

import re

from app.services.sentiment.base import Label, SentimentAnalyzer

ASPECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "environment": ("garden", "green", "scenic", "view", "views", "nature", "beautiful", "serene", "sunset",
                    "air", "lake", "river", "trees", "flowers", "atmosphere", "campus", "environment",
                    "surroundings", "scenery", "greenery"),
    "cleanliness": ("clean", "dirty", "dusty", "litter", "garbage", "hygiene", "hygienic", "filthy", "unclean"),
    "facilities": ("toilet", "toilets", "parking", "facilities", "facility", "seating", "lit", "maintained",
                   "benches", "restroom", "restrooms"),
    "food": ("food", "snacks", "thali", "restaurant", "restaurants", "stalls", "coffee", "cuisine", "dish",
             "dishes", "dalma", "court"),
    "safety": ("safe", "unsafe", "theft", "security", "monkeys", "scam"),
    "accessibility": ("wheelchair", "accessible", "steps", "elderly", "stairs", "climb"),
    "visitor_experience": ("staff", "guide", "experience", "fun", "service", "pandas", "explained", "families"),
    "crowding": ("crowd", "crowded", "crowds", "busy", "rush", "queue", "overcrowded", "quiet", "peaceful",
                 "calm"),
}
_CLAUSE_SPLIT = re.compile(r"[.!?;]+|\bbut\b|\bhowever\b|\bthough\b|\balthough\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+")


def split_clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT.split(text or "") if c and c.strip()]


def detect_aspects(clause: str) -> list[str]:
    words = set(_WORD.findall(clause.lower()))
    return [aspect for aspect, kws in ASPECT_KEYWORDS.items() if words & set(kws)]


def analyze_aspects(text: str, analyzer: SentimentAnalyzer) -> dict[str, Label]:
    clauses = split_clauses(text)
    tagged = [(c, detect_aspects(c)) for c in clauses]
    tagged = [(c, a) for c, a in tagged if a]
    if not tagged:
        return {}
    results = analyzer.analyze_batch([c for c, _ in tagged])
    aspects: dict[str, Label] = {}
    for (_, names), res in zip(tagged, results, strict=True):
        for name in names:
            # No inversion needed for crowding: the lexicon scores "quiet"/"peaceful" positive
            # and "crowded" negative, so the label reads as "good/bad news about crowds".
            if name in aspects and aspects[name] != res.label:
                aspects[name] = "neutral"  # mixed evidence within one review
            else:
                aspects.setdefault(name, res.label)
    return aspects
