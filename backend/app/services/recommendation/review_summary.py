"""What visitors like and dislike about a place, in one line ("People love the calm vibe — but the food is poor").

Two ways, best first:
* **AI** — one batched call for all recommended places, given up to 5 real Google reviews each. The model may
  only paraphrase those reviews (short phrases, no numbers); anything malformed is dropped.
* **Keywords** — from the clause-level aspect sentiment we already compute (views, food, crowds, cleanliness…).

Nothing is ever produced for a place without reviews.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from app.services.llm.base import LLMInvalidResponseError, LLMRequest, LLMResponse
from app.services.llm.json_repair import JSONExtractionError, extract_json
from app.services.llm.llm_service import AllProvidersFailedError, LLMService
from app.services.sentiment.aspects import analyze_aspects
from app.services.sentiment.lexicon import LexiconSentimentAnalyzer

logger = logging.getLogger(__name__)

LIKE_PHRASES = {
    "environment": "the views and atmosphere", "cleanliness": "how clean it is", "facilities": "the facilities",
    "food": "the food", "safety": "that it feels safe", "accessibility": "easy access",
    "visitor_experience": "the overall experience", "crowding": "the calm, uncrowded feel",
}
DISLIKE_PHRASES = {
    "environment": "the surroundings", "cleanliness": "cleanliness", "facilities": "toilets and parking",
    "food": "the food", "safety": "touts, monkeys or safety", "accessibility": "steps and access",
    "visitor_experience": "staff and service", "crowding": "the crowds",
}
_DIGITS = re.compile(r"\d")
_LEXICON = LexiconSentimentAnalyzer()


@dataclass
class ReviewSummary:
    likes: list[str]
    dislikes: list[str]
    based_on: int
    method: str  # "ai" | "keywords"
    text: str = field(init=False)

    def __post_init__(self) -> None:
        self.text = summary_sentence(self.likes, self.dislikes)


@dataclass(frozen=True)
class PlaceReviews:
    poi_id: str
    name: str
    reviews: list[str]
    positive_aspects: list[str]
    negative_aspects: list[str]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary_sentence(likes: list[str], dislikes: list[str]) -> str:
    if likes and dislikes:
        return f"People like {_join(likes)} — but some mention {_join(dislikes)}."
    if likes:
        return f"People like {_join(likes)}."
    if dislikes:
        return f"Visitors often mention {_join(dislikes)}."
    return ""


def aspects_from_reviews(reviews: list[str]) -> tuple[list[str], list[str]]:
    """Clause-level aspect sentiment over the review texts themselves (no minimum review count)."""
    score: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    for text in reviews:
        for aspect, label in analyze_aspects(text, _LEXICON).items():
            mentions[aspect] += 1
            score[aspect] += {"positive": 1, "negative": -1}.get(label, 0)
    ordered = [a for a, _ in mentions.most_common()]
    return [a for a in ordered if score[a] > 0], [a for a in ordered if score[a] < 0]


def keyword_summary(place: PlaceReviews) -> ReviewSummary | None:
    if not place.reviews:
        return None
    positive, negative = aspects_from_reviews(place.reviews)
    if not (positive or negative):
        positive, negative = place.positive_aspects, place.negative_aspects
    likes = [LIKE_PHRASES[a] for a in positive if a in LIKE_PHRASES][:3]
    dislikes = [DISLIKE_PHRASES[a] for a in negative if a in DISLIKE_PHRASES][:2]
    if not (likes or dislikes):
        return None
    return ReviewSummary(likes, dislikes, len(place.reviews), "keywords")


# ------------------------------------------------------------------------------------------------ AI
SYSTEM = """You summarise tourist reviews of places in India for a travel app.
For each place you get its real visitor reviews. Write what visitors LIKE and what they DISLIKE as short
phrases (2-6 words each, lowercase, e.g. "peaceful vibe", "beautiful sunset views", "food is overpriced",
"long queues on weekends").
Rules: use ONLY what the reviews say; no numbers, prices or times; at most 3 likes and 3 dislikes; an empty
list when reviews mention nothing of that kind; never invent. Respond ONLY with JSON:
{"places": [{"id": "<id>", "likes": ["..."], "dislikes": ["..."]}]}"""

SCHEMA = {
    "type": "object",
    "properties": {"places": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "likes": {"type": "array", "items": {"type": "string"}},
                       "dislikes": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "likes", "dislikes"], "additionalProperties": False}}},
    "required": ["places"], "additionalProperties": False,
}


class _Place(BaseModel):
    id: str
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)


class _Out(BaseModel):
    places: list[_Place] = Field(default_factory=list)


def _clean(phrases: list[str], limit: int) -> list[str]:
    out = []
    for p in phrases:
        p = " ".join(str(p).split()).strip(" .,;").lower()
        if 2 <= len(p) <= 60 and not _DIGITS.search(p) and p not in out:
            out.append(p)
    return out[:limit]


class ReviewSummarizer:
    def __init__(self, llm: LLMService | None = None, max_reviews: int = 5, max_chars: int = 400) -> None:
        self.llm = llm
        self.max_reviews = max_reviews
        self.max_chars = max_chars

    async def summarize(self, places: list[PlaceReviews]) -> dict[str, ReviewSummary]:
        with_reviews = [p for p in places if p.reviews]
        out: dict[str, ReviewSummary] = {}
        if self.llm is not None and with_reviews:
            out = await self._ai(with_reviews)
        for p in with_reviews:  # keyword fallback for anything the model skipped
            if p.poi_id not in out and (s := keyword_summary(p)) is not None:
                out[p.poi_id] = s
        return out

    async def _ai(self, places: list[PlaceReviews]) -> dict[str, ReviewSummary]:
        payload = {"places": [{"id": p.poi_id, "name": p.name,
                               "reviews": [r[: self.max_chars] for r in p.reviews[: self.max_reviews]]}
                              for p in places]}
        known = {p.poi_id: p for p in places}

        def parse(response: LLMResponse) -> _Out:
            try:
                return _Out.model_validate(extract_json(response.text)[0])
            except (JSONExtractionError, ValidationError) as exc:
                raise LLMInvalidResponseError(f"review summary schema mismatch: {exc}",
                                              provider=response.provider) from exc

        request = LLMRequest(system=SYSTEM, user=json.dumps(payload, ensure_ascii=False), json_schema=SCHEMA,
                             schema_name="review_summary", temperature=0.1, max_output_tokens=1500)
        try:
            result = await self.llm.generate(request, parse)
        except AllProvidersFailedError:
            logger.info("review_summary_llm_unavailable")
            return {}
        out = {}
        for item in result.value.places:
            place = known.get(item.id)
            if place is None:
                continue
            likes, dislikes = _clean(item.likes, 3), _clean(item.dislikes, 3)
            if likes or dislikes:
                out[item.id] = ReviewSummary(likes, dislikes, min(len(place.reviews), self.max_reviews), "ai")
        return out
