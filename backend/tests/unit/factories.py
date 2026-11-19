"""Builders for recommendation-layer test data (clearly synthetic)."""

from __future__ import annotations

from app.services.recommendation.types import CandidateRow, RatingInfo

HOURS_ALL_DAYS = {"weekdayDescriptions": [f"{d}: 6:00 AM – 6:00 PM" for d in
                                          ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
                                           "Sunday")]}
CLOSED_MONDAY = {"weekdayDescriptions": ["Monday: Closed"] + [f"{d}: 10:00 AM – 5:00 PM" for d in
                                                              ("Tuesday", "Wednesday", "Thursday", "Friday",
                                                               "Saturday", "Sunday")]}


def candidate(poi_id: str, name: str, category: str, distance_m: float, *, categories=None, rating=4.4,
              count=1000, photos=0, hours=HOURS_ALL_DAYS, sentiment=None, origin="mock") -> CandidateRow:
    return CandidateRow(
        poi_id=poi_id, name=name, category=category, categories=categories or [category], latitude=20.3,
        longitude=85.82, distance_m=distance_m, data_origin=origin, description=f"Synthetic {name}",
        opening_hours=hours, ratings=[RatingInfo("google_places", rating, count)] if rating is not None else [],
        photo_count=photos, review_count=3, sentiment=sentiment,
    )


GOOD_SENTIMENT = {"status": "available", "reviews_analyzed": 3, "overall": "positive", "positive_share": 0.67,
                  "neutral_share": 0.0, "negative_share": 0.33,
                  "aspects": {"environment": {"label": "positive", "mentions": 2},
                              "food": {"label": "negative", "mentions": 1}}}


def bhubaneswar_candidates() -> list[CandidateRow]:
    return [
        candidate("POI000001", "Ekamra Kanan Botanical Gardens", "parks_gardens", 1500,
                  categories=["parks_gardens", "nature"], rating=4.3, count=15322, photos=6,
                  sentiment=GOOD_SENTIMENT),
        candidate("POI000002", "Nandankanan Zoological Park", "wildlife", 11000, categories=["wildlife", "nature"],
                  rating=4.4, count=61234, photos=9),
        candidate("POI000003", "Lingaraj Temple", "religious", 6500, categories=["religious", "historical"],
                  rating=4.7, count=48211, photos=8),
        candidate("POI000004", "Esplanade One Mall", "shopping", 3500, categories=["shopping", "entertainment"],
                  rating=4.3, count=40876),
        candidate("POI000005", "Odisha State Museum", "cultural", 4400, categories=["cultural", "historical"],
                  rating=4.3, count=7012, hours=CLOSED_MONDAY),
        candidate("POI000006", "Deras Dam", "nature", 17500, categories=["nature"], rating=None, count=0,
                  hours=None, photos=5),
    ]
